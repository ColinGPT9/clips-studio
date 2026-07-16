/**
 * Clips Studio feedback relay — a tiny Cloudflare Worker that lets app
 * users file bug reports / feature requests WITHOUT a GitHub account.
 *
 * The app POSTs a report here; this worker validates it, applies anti-spam
 * checks, and creates a GitHub Issue on the repo using a token that only
 * lives in this worker's secrets (never in the open-source app).
 *
 * Anti-spam layers:
 *  1. Proof-of-work challenge: the client must fetch GET /challenge and
 *     burn ~1s of CPU finding a nonce whose sha256(salt+nonce) starts with
 *     DIFFICULTY zero bits. Invisible to real users, expensive at spam
 *     scale. Challenges are HMAC-signed, expire in 10 minutes, and are
 *     single-use (KV).
 *  2. Per-IP rate limit: MAX_PER_DAY submissions per IP per UTC day (KV).
 *  3. Size caps + strict field validation server-side.
 *  4. Honeypot: a "website" field that humans never see; bots that fill it
 *     get a fake success.
 *
 * Secrets (wrangler secret put ...):
 *   GITHUB_TOKEN  — fine-grained PAT, Issues: Read+Write on the repo only
 *   HMAC_KEY      — any long random string (signs challenges)
 * Vars (wrangler.toml):
 *   REPO          — "owner/name", e.g. "ColinGPT9/clips-studio"
 * KV binding: FEEDBACK_KV
 */

const DIFFICULTY_BITS = 20; // ~1s of hashing on a normal PC
const CHALLENGE_TTL_S = 600;
const MAX_PER_DAY = 5;
const MAX_BODY_BYTES = 6 * 1024 * 1024; // report + up to 3 screenshots
const MAX_TEXT = 20_000; // per free-text field
const MAX_IMAGES = 3;
const MAX_IMAGE_BYTES = 2 * 1024 * 1024;

const TYPES = {
  bug: { labels: ["bug", "from-app"], prefix: "[Bug]" },
  feature: { labels: ["feature-request", "from-app"], prefix: "[Feature]" },
  improvement: { labels: ["enhancement", "from-app"], prefix: "[Improvement]" },
};

const AREA_LABELS = new Set([
  "ui", "ai", "performance", "accessibility", "video-editor", "clips-studio",
  "windows", "youtube", "twitch", "kick", "ffmpeg", "whisper", "gemma", "ollama",
]);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (request.method === "GET" && url.pathname === "/challenge") {
        return json(await makeChallenge(env));
      }
      if (request.method === "POST" && url.pathname === "/submit") {
        return await handleSubmit(request, env);
      }
      return json({ error: "not found" }, 404);
    } catch (e) {
      return json({ error: `relay error: ${e.message}` }, 500);
    }
  },
};

// ---- proof-of-work challenge ------------------------------------------------

async function makeChallenge(env) {
  const salt = crypto.randomUUID();
  const expires = Date.now() + CHALLENGE_TTL_S * 1000;
  const sig = await hmac(env.HMAC_KEY, `${salt}.${expires}`);
  return { salt, expires, sig, difficulty: DIFFICULTY_BITS };
}

async function verifyPow(env, pow) {
  if (!pow || typeof pow !== "object") return "missing proof-of-work";
  const { salt, expires, sig, nonce } = pow;
  if (typeof salt !== "string" || typeof nonce !== "string") return "bad pow fields";
  if (!Number.isFinite(expires) || Date.now() > expires) return "challenge expired";
  if ((await hmac(env.HMAC_KEY, `${salt}.${expires}`)) !== sig) return "bad challenge signature";
  // single use
  if (await env.FEEDBACK_KV.get(`pow:${salt}`)) return "challenge already used";
  await env.FEEDBACK_KV.put(`pow:${salt}`, "1", { expirationTtl: CHALLENGE_TTL_S });
  // hash must start with DIFFICULTY_BITS zero bits
  const digest = await sha256Bytes(`${salt}.${nonce}`);
  let bits = 0;
  for (const byte of digest) {
    if (byte === 0) { bits += 8; continue; }
    bits += Math.clz32(byte) - 24;
    break;
  }
  return bits >= DIFFICULTY_BITS ? null : "proof-of-work too weak";
}

// ---- submit -----------------------------------------------------------------

async function handleSubmit(request, env) {
  const len = Number(request.headers.get("content-length") || 0);
  if (len > MAX_BODY_BYTES) return json({ error: "report too large" }, 413);

  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const day = new Date().toISOString().slice(0, 10);
  const rlKey = `rl:${ip}:${day}`;
  const used = Number((await env.FEEDBACK_KV.get(rlKey)) || 0);
  if (used >= MAX_PER_DAY) {
    return json({ error: "daily feedback limit reached — try again tomorrow" }, 429);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON" }, 400);
  }

  // Honeypot: invisible field a human never fills. Pretend success.
  if (body.website) return json({ ok: true, url: "" });

  const powError = await verifyPow(env, body.pow);
  if (powError) return json({ error: powError }, 403);

  // ---- validation (server-side, strict) ----
  const type = TYPES[body.type];
  if (!type) return json({ error: "type must be bug|feature|improvement" }, 400);
  const title = clean(body.title, 140);
  const markdown = clean(body.markdown, MAX_TEXT);
  if (!title || title.length < 8) return json({ error: "title too short" }, 400);
  if (!markdown || markdown.length < 40) return json({ error: "report too short" }, 400);
  const labels = [...type.labels];
  for (const l of Array.isArray(body.areas) ? body.areas.slice(0, 4) : []) {
    if (AREA_LABELS.has(l)) labels.push(l);
  }
  if (body.severity === "critical" || body.severity === "high") {
    labels.push(body.severity === "critical" ? "critical" : "high-priority");
  }

  // ---- optional screenshots -> committed to the feedback-assets branch ----
  let imagesMd = "";
  const images = Array.isArray(body.images) ? body.images.slice(0, MAX_IMAGES) : [];
  for (let i = 0; i < images.length; i++) {
    const img = images[i];
    if (typeof img?.b64 !== "string" || img.b64.length > MAX_IMAGE_BYTES * 1.4) continue;
    const ext = img.ext === "jpg" ? "jpg" : "png"; // whitelist
    const path = `assets/${Date.now()}-${i}.${ext}`;
    const put = await gh(env, `contents/${path}`, "PUT", {
      message: `feedback screenshot`,
      content: img.b64,
      branch: "feedback-assets",
    });
    if (put.ok) {
      const raw = `https://raw.githubusercontent.com/${env.REPO}/feedback-assets/${path}`;
      imagesMd += `\n![screenshot ${i + 1}](${raw})`;
    }
  }

  // ---- create the issue ----
  const res = await gh(env, "issues", "POST", {
    title: `${type.prefix} ${title}`,
    body: markdown + (imagesMd ? `\n\n### Screenshots\n${imagesMd}` : ""),
    labels,
  });
  if (!res.ok) {
    const detail = await res.text();
    return json({ error: `GitHub rejected the report (${res.status}): ${detail.slice(0, 200)}` }, 502);
  }
  const issue = await res.json();
  await env.FEEDBACK_KV.put(rlKey, String(used + 1), { expirationTtl: 172800 });
  return json({ ok: true, url: issue.html_url, number: issue.number });
}

// ---- helpers ------------------------------------------------------------------

function clean(v, max) {
  if (typeof v !== "string") return "";
  // strip control characters (keep newline=10 and tab=9), cap length
  const stripped = [...v]
    .filter((ch) => { const c = ch.charCodeAt(0); return c === 10 || c === 9 || c >= 32; })
    .join("");
  return stripped.trim().slice(0, max);
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function gh(env, path, method, payload) {
  return fetch(`https://api.github.com/repos/${env.REPO}/${path}`, {
    method,
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "user-agent": "clips-studio-feedback-relay",
      "x-github-api-version": "2022-11-28",
    },
    body: JSON.stringify(payload),
  });
}

async function hmac(key, msg) {
  const k = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(msg));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function sha256Bytes(msg) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(msg));
  return new Uint8Array(d);
}
