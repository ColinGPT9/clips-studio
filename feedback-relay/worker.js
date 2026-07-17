/**
 * Clips Studio feedback relay — a tiny Cloudflare Worker that lets app
 * users file bug reports / feature requests WITHOUT a GitHub account.
 *
 * The app POSTs a report here; this worker validates it, applies anti-spam
 * checks, and creates a GitHub Issue on the repo using a token that only
 * lives in this worker's secrets (never in the open-source app).
 *
 * Anti-spam layers:
 *  1. Custom header (x-clips-studio): its absence 403s everything, and its
 *     presence on cross-origin browser requests forces a CORS preflight we
 *     never answer — web pages can't draft visitors into spamming.
 *  2. Proof-of-work challenge: the client must fetch GET /challenge and
 *     burn ~1s of CPU finding a nonce whose sha256(salt+nonce) starts with
 *     DIFFICULTY zero bits. Invisible to real users, expensive at spam
 *     scale. Challenges are HMAC-signed, IP-bound, expire in 10 minutes,
 *     and are single-use (KV).
 *  3. Per-IP rate limit: MAX_PER_DAY submissions per IP per UTC day (KV).
 *  4. Size caps + strict field/structure validation + image magic bytes.
 *  5. Honeypot: a "website" field that humans never see; bots that fill it
 *     get a fake success.
 *
 * Duplicate handling: exact resubmissions are answered from a 24h KV cache;
 * reports whose title strongly matches an open from-app issue become a
 * comment on that issue (comment count = how many users hit it).
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
    const ip = request.headers.get("cf-connecting-ip") || "unknown";
    try {
      // The custom header is required on every route. Cross-origin browser
      // requests with a custom header trigger a CORS preflight, which this
      // worker never answers — so web pages can't draft their visitors'
      // browsers (and fresh IPs) into spamming the relay. The desktop app
      // sets it trivially; only browsers are locked out.
      if (request.headers.get("x-clips-studio") !== "1") {
        return json({ error: "bad client" }, 403);
      }
      if (request.method === "GET" && url.pathname === "/challenge") {
        return json(await makeChallenge(env, ip));
      }
      if (request.method === "POST" && url.pathname === "/submit") {
        return await handleSubmit(request, env, ip);
      }
      return json({ error: "not found" }, 404);
    } catch (e) {
      // Log for `wrangler tail` / Workers Logs; never echo internals.
      console.error("relay error:", e);
      return json({ error: "relay error" }, 500);
    }
  },
};

// ---- proof-of-work challenge ------------------------------------------------

// Challenges are bound to the requesting IP: a botnet can't solve once and
// share the solution — every node must burn its own CPU.
async function makeChallenge(env, ip) {
  const salt = crypto.randomUUID();
  const expires = Date.now() + CHALLENGE_TTL_S * 1000;
  const sig = await hmac(env.HMAC_KEY, `${salt}.${expires}.${ip}`);
  return { salt, expires, sig, difficulty: DIFFICULTY_BITS };
}

async function verifyPow(env, pow, ip) {
  if (!pow || typeof pow !== "object") return "missing proof-of-work";
  const { salt, expires, sig, nonce } = pow;
  if (typeof salt !== "string" || typeof nonce !== "string") return "bad pow fields";
  if (!Number.isFinite(expires) || Date.now() > expires) return "challenge expired";
  if ((await hmac(env.HMAC_KEY, `${salt}.${expires}.${ip}`)) !== sig) {
    return "bad challenge signature";
  }
  // hash must start with DIFFICULTY_BITS zero bits
  const digest = await sha256Bytes(`${salt}.${nonce}`);
  let bits = 0;
  for (const byte of digest) {
    if (byte === 0) { bits += 8; continue; }
    bits += Math.clz32(byte) - 24;
    break;
  }
  if (bits < DIFFICULTY_BITS) return "proof-of-work too weak";
  // Single use, marked only AFTER the hash checks out — garbage submissions
  // don't consume KV's 1k writes/day. (KV is eventually consistent, so a
  // cross-region burst could reuse a challenge briefly; the rate limit and
  // the PoW cost bound the damage — a deliberate free-tier trade-off.)
  if (await env.FEEDBACK_KV.get(`pow:${salt}`)) return "challenge already used";
  await env.FEEDBACK_KV.put(`pow:${salt}`, "1", { expirationTtl: CHALLENGE_TTL_S });
  return null;
}

// ---- submit -----------------------------------------------------------------

async function handleSubmit(request, env, ip) {
  // Fast-path reject on the declared size, then verify against the ACTUAL
  // bytes — Content-Length is client-controlled and can be absent or a lie.
  const len = Number(request.headers.get("content-length") || 0);
  if (len > MAX_BODY_BYTES) return json({ error: "report too large" }, 413);
  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return json({ error: "report too large" }, 413);

  // KV read-then-write is eventually consistent (not atomic) — good enough
  // for a feedback box; worst case a burst slightly exceeds the cap.
  const day = new Date().toISOString().slice(0, 10);
  const rlKey = `rl:${ip}:${day}`;
  const used = Number((await env.FEEDBACK_KV.get(rlKey)) || 0);
  if (used >= MAX_PER_DAY) {
    return json({ error: "daily feedback limit reached — try again tomorrow" }, 429);
  }

  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return json({ error: "invalid JSON" }, 400);
  }
  if (!body || typeof body !== "object") return json({ error: "invalid JSON" }, 400);

  // Honeypot: invisible field a human never fills. Pretend success.
  if (body.website) return json({ ok: true, url: "" });

  const powError = await verifyPow(env, body.pow, ip);
  if (powError) return json({ error: powError }, 403);

  // ---- validation (server-side, strict) ----
  const type = TYPES[body.type];
  if (!type) return json({ error: "type must be bug|feature|improvement" }, 400);
  // Neutralized @mentions: anonymous reports must not be able to ping
  // arbitrary GitHub users from this repo (a zero-width space breaks the
  // mention without changing how the text reads).
  const title = demention(clean(body.title, 140));
  const markdown = demention(clean(body.markdown, MAX_TEXT));
  if (!title || title.length < 8) return json({ error: "title too short" }, 400);
  // Structure check: the app builds one "### question" section per answered
  // field, and required questions are enforced client-side — a report with
  // fewer than two sections or under 60 chars is a skeleton from a modified
  // client, not a real submission.
  if (!markdown || markdown.length < 60) return json({ error: "report too short" }, 400);
  if ((markdown.match(/^### /gm) || []).length < 2) {
    return json({ error: "report is missing required sections" }, 400);
  }
  const labels = [...type.labels];
  for (const l of Array.isArray(body.areas) ? body.areas.slice(0, 4) : []) {
    if (AREA_LABELS.has(l)) labels.push(l);
  }
  if (body.severity === "critical" || body.severity === "high") {
    labels.push(body.severity === "critical" ? "critical" : "high-priority");
  }

  // ---- duplicate layer 1: exact resubmission (double-click, retry) ----
  const dupKey = "dup:" + (await sha256Hex(`${body.type}.${title}.${markdown}`));
  const already = await env.FEEDBACK_KV.get(dupKey);
  if (already) return json({ ok: true, url: already, duplicate: true });

  // ---- optional screenshots -> committed to the feedback-assets branch ----
  let imagesMd = "";
  const images = Array.isArray(body.images) ? body.images.slice(0, MAX_IMAGES) : [];
  for (let i = 0; i < images.length; i++) {
    const img = images[i];
    if (typeof img?.b64 !== "string" || img.b64.length > MAX_IMAGE_BYTES * 1.4) continue;
    const ext = img.ext === "jpg" ? "jpg" : "png"; // whitelist
    // Magic-byte check: without it this branch is anonymous file hosting
    // for arbitrary bytes served from raw.githubusercontent under the repo.
    if (!looksLikeImage(img.b64, ext)) continue;
    const path = `assets/${Date.now()}-${i}.${ext}`;
    const put = await gh(env, `contents/${path}`, "PUT", {
      message: `feedback screenshot`,
      content: img.b64,
      branch: "feedback-assets",
    });
    if (put.ok) {
      const rawUrl = `https://raw.githubusercontent.com/${env.REPO}/feedback-assets/${path}`;
      imagesMd += `\n![screenshot ${i + 1}](${rawUrl})`;
    }
  }
  const fullBody = markdown + (imagesMd ? `\n\n### Screenshots\n${imagesMd}` : "");

  // ---- duplicate layer 2: same problem already reported by someone else?
  // One search call; on a strong title match the report becomes a comment
  // on the existing issue (comment count = "how many users hit this").
  // Search failures fall through to creating a normal issue.
  const similar = await findSimilar(env, title, type.labels[0]);
  if (similar) {
    const c = await gh(env, `issues/${similar.number}/comments`, "POST", {
      body: `Another user reported the same problem from the app:\n\n${fullBody}`,
    });
    if (c.ok) {
      await env.FEEDBACK_KV.put(rlKey, String(used + 1), { expirationTtl: 172800 });
      await env.FEEDBACK_KV.put(dupKey, similar.html_url, { expirationTtl: 86400 });
      return json({ ok: true, url: similar.html_url, number: similar.number, duplicate: true });
    }
  }

  // ---- create the issue ----
  const res = await gh(env, "issues", "POST", {
    title: `${type.prefix} ${title}`,
    body: fullBody,
    labels,
  });
  if (!res.ok) {
    const detail = await res.text();
    // Full detail to Workers Logs for debugging; truncated to the client.
    console.error("issue creation failed:", res.status, JSON.stringify(detail.slice(0, 500)));
    return json({ error: `GitHub rejected the report (${res.status}): ${detail.slice(0, 200)}` }, 502);
  }
  const issue = await res.json();
  await env.FEEDBACK_KV.put(rlKey, String(used + 1), { expirationTtl: 172800 });
  await env.FEEDBACK_KV.put(dupKey, issue.html_url, { expirationTtl: 86400 });
  return json({ ok: true, url: issue.html_url, number: issue.number });
}

// ---- duplicate detection ------------------------------------------------------

function titleWords(title) {
  return [...new Set(
    title.toLowerCase().replace(/[^a-z0-9\s]/g, " ").split(/\s+/)
      .filter((w) => w.length >= 4)
  )];
}

/** The most similar OPEN from-app issue of the same kind, or null. Primitive
 *  on purpose: token overlap on titles — anything smarter belongs in triage,
 *  not in a relay that must stay simple. */
async function findSimilar(env, title, kindLabel) {
  const words = titleWords(title).slice(0, 6);
  if (words.length < 2) return null;
  const q = encodeURIComponent(
    `repo:${env.REPO} is:issue is:open label:from-app label:${kindLabel} in:title ${words.join(" ")}`
  );
  const res = await ghApi(env, `/search/issues?q=${q}&per_page=3`, "GET");
  if (!res.ok) return null;
  const items = (await res.json()).items || [];
  const mine = new Set(titleWords(title));
  for (const it of items) {
    const theirs = titleWords(String(it.title || ""));
    if (theirs.length === 0) continue;
    const overlap = theirs.filter((w) => mine.has(w)).length /
      Math.min(mine.size, theirs.length);
    if (overlap >= 0.6) return it;
  }
  return null;
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

/** Break GitHub @mentions with a zero-width space — reads identically but
 *  no longer notifies the named user. */
function demention(text) {
  return text.replace(/@([A-Za-z0-9])/g, "@​$1");
}

/** Magic-byte check on the first decoded bytes: PNG or JPEG only. */
function looksLikeImage(b64, ext) {
  let head;
  try {
    head = atob(b64.slice(0, 16)); // 16 b64 chars -> first 12 bytes
  } catch {
    return false;
  }
  if (head.length < 4) return false;
  if (ext === "png") {
    return head.charCodeAt(0) === 0x89 && head.slice(1, 4) === "PNG";
  }
  return (
    head.charCodeAt(0) === 0xff && head.charCodeAt(1) === 0xd8 && head.charCodeAt(2) === 0xff
  );
}

async function ghApi(env, path, method, payload) {
  return fetch(`https://api.github.com${path}`, {
    method,
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "user-agent": "clips-studio-feedback-relay",
      "x-github-api-version": "2022-11-28",
    },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
}

function gh(env, path, method, payload) {
  return ghApi(env, `/repos/${env.REPO}/${path}`, method, payload);
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

async function sha256Hex(msg) {
  return [...(await sha256Bytes(msg))].map((b) => b.toString(16).padStart(2, "0")).join("");
}
