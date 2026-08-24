/**
 * Clips Kitty Twitch proxy — the one piece of server the web version needs.
 *
 * ## Why it exists
 *
 * Twitch VODs cannot be read from a browser. `gql.twitch.tv` is fine, but
 * `usher.ttvnw.net` sends NO CORS headers on a successful response, and
 * neither does the CloudFront CDN behind it — not for manifests, not for the
 * `.ts` segments. The browser therefore refuses to hand any of it to the page.
 *
 * > The trap, recorded because it already caught us once: Twitch DOES send
 * > `Access-Control-Allow-Origin: *` on ERROR responses. A made-up VOD id
 * > 403s with permissive headers and looks like proof the path is open. Test
 * > CORS against a SUCCESSFUL response, or the errors will lie to you.
 *
 * So the bytes have to come through something that adds the header. This is
 * that something, and nothing more: it does not transcode, cache, or store.
 *
 * ## Why Cloudflare and not Vercel
 *
 * Cloudflare does not bill egress for Workers. A two-hour VOD is ~190 MB of
 * audio, which would consume Vercel's entire monthly allowance in ~500 runs;
 * here the bytes are free and only the REQUEST COUNT is metered. The free
 * plan's 100,000 requests/day is roughly 130 VODs — see README.md.
 *
 * ## Shape, and why it is not a generic `?url=` proxy
 *
 * A generic proxy is an open relay: anyone who finds the URL gets free
 * bandwidth on someone else's account. Instead this exposes three narrow
 * endpoints and REWRITES the playlists so every URL a client follows points
 * back here rather than at Twitch:
 *
 *   GET /master?vod=<id>  token + usher, rendition URLs rewritten to /media
 *   GET /media?u=<url>    a rendition playlist, segment names rewritten to /seg
 *   GET /seg?u=<url>      one segment, streamed straight through
 *   GET /health[?vod=]    walks all four steps and reports each
 *
 * Requests are further limited to `ALLOWED_ORIGINS` and to Twitch-owned
 * hostnames. See the README for the HMAC upgrade if that stops being enough.
 *
 * ## Free-plan constraints this is built around
 *
 * 50 subrequests per invocation, so the worker never fetches-and-stitches a
 * whole VOD — the browser pulls each segment through separately, one upstream
 * fetch per request. Streaming means CPU stays near zero regardless of size:
 * the response body is handed straight back without being buffered.
 */

const CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko";
const PLAYBACK_QUERY_HASH =
	"0828119ded1c13477966434e15800ff57ddacf13ba1911c129dc2200705b0712";

/** Hostnames this will fetch from. Everything else is refused, so the worker
 *  cannot be pointed at arbitrary targets even by a forged request.
 *
 *  `.cloudfront.net` is broader than ideal — it is anyone's CloudFront, not
 *  just Twitch's — but Twitch serves VODs from rotating CloudFront
 *  distributions with no stable pattern to match on. The origin check below
 *  is what carries the weight; this is the second lock, not the first. */
const ALLOWED_HOSTS = [".ttvnw.net", ".cloudfront.net", ".twitch.tv"];

const MAX_UPSTREAM_BYTES = 60 * 1024 * 1024; // one segment is ~300 KB

function allowedHost(url) {
	try {
		const { hostname, protocol } = new URL(url);
		if (protocol !== "https:") return false;
		return ALLOWED_HOSTS.some(
			(suffix) => hostname === suffix.slice(1) || hostname.endsWith(suffix),
		);
	} catch {
		return false;
	}
}

/** Which pages may use this.
 *
 *  Set ALLOWED_ORIGINS in wrangler.toml. A browser cannot forge `Origin`, so
 *  this stops the obvious abuse — somebody else's web page quietly spending
 *  this worker's daily request budget. It does NOT stop a determined person
 *  with curl, which is a deliberate trade: the alternative is signing every
 *  URL, which costs a secret and a setup step for a tool whose whole point is
 *  being free and easy to run. Upgrade if it is ever actually abused. */
function corsHeaders(request, env) {
	const origin = request.headers.get("Origin") || "";
	const allowed = (env.ALLOWED_ORIGINS || "")
		.split(",")
		.map((o) => o.trim())
		.filter(Boolean);

	// No configured list means "any", which is right for local development
	// and wrong for production — the README says so.
	//
	// A request with NO Origin is allowed regardless. Browsers always send it
	// on a cross-origin fetch, so its absence means this is not a web page
	// being drafted into spending the request budget — it is curl, or the
	// /health check. Refusing those would 403 the first thing anyone runs
	// after deploying, while protecting nothing.
	// Any localhost origin passes, whatever the port. A browser sends the exact
	// origin it is on, so "localhost:3000" in a list does not match a build
	// being served on 127.0.0.1:4321 — and chasing that mismatch port by port
	// wastes time on a value that can only ever come from the developer's own
	// machine. Not a hole: nothing on the public internet can present a
	// localhost origin to this worker.
	const isLocal = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin);

	const ok =
		allowed.length === 0 || !origin || isLocal || allowed.includes(origin);

	return {
		// ALWAYS permissive, even when the origin is refused. This looks
		// backwards and is not: the protection is that a refused origin gets no
		// video, not that it is denied the ability to READ the refusal. An
		// earlier version returned "null" here, which meant the browser blocked
		// the 403 and the page saw a bare "Failed to fetch" — the carefully
		// worded error explaining exactly what to fix was itself unreadable.
		"Access-Control-Allow-Origin": origin || "*",
		"Access-Control-Allow-Methods": "GET, OPTIONS",
		"Access-Control-Max-Age": "86400",
		Vary: "Origin",
		_ok: ok,
	};
}

function json(body, request, env, status = 200) {
	const { _ok, ...cors } = corsHeaders(request, env);
	return new Response(JSON.stringify(body, null, 2), {
		status,
		headers: { "Content-Type": "application/json", ...cors },
	});
}

// ---- Twitch ---------------------------------------------------------------

async function playbackToken(vod) {
	const res = await fetch("https://gql.twitch.tv/gql", {
		method: "POST",
		headers: { "Client-ID": CLIENT_ID, "Content-Type": "application/json" },
		body: JSON.stringify({
			operationName: "PlaybackAccessToken",
			variables: {
				isLive: false,
				login: "",
				isVod: true,
				vodID: vod,
				playerType: "embed",
			},
			extensions: {
				persistedQuery: { version: 1, sha256Hash: PLAYBACK_QUERY_HASH },
			},
		}),
	});

	if (!res.ok) throw new Error(`Twitch refused the token request (${res.status})`);

	const token = (await res.json())?.data?.videoPlaybackAccessToken;
	if (!token?.value) {
		throw new Error(
			"Twitch would not issue a playback token — the VOD may be deleted, private or subscriber-only.",
		);
	}
	return token;
}

function usherUrl(vod, token) {
	const url = new URL(`https://usher.ttvnw.net/vod/${vod}.m3u8`);
	url.searchParams.set("allow_source", "true");
	url.searchParams.set("allow_audio_only", "true");
	url.searchParams.set("player", "twitchweb");
	url.searchParams.set("sig", token.signature);
	url.searchParams.set("token", token.value);
	return url.toString();
}

/** Point every URL in a playlist back at this worker.
 *
 *  Without this the browser would read a rewritten manifest and then fetch
 *  the segments from Twitch directly — straight back into the CORS wall this
 *  exists to get around. `base` resolves the relative segment names that
 *  media playlists use (`0.ts`, `1.ts`, …). */
function rewritePlaylist(text, base, self, endpoint) {
	return text
		.split("\n")
		.map((line) => {
			const trimmed = line.trim();
			if (!trimmed || trimmed.startsWith("#")) return line;

			const absolute = new URL(trimmed, base).toString();
			return `${self}/${endpoint}?u=${encodeURIComponent(absolute)}`;
		})
		.join("\n");
}

// ---- handlers -------------------------------------------------------------

async function handleMaster(vod, request, env, self) {
	const token = await playbackToken(vod);
	const upstream = await fetch(usherUrl(vod, token));

	if (!upstream.ok) {
		return json(
			{ error: `Twitch's playlist server returned ${upstream.status}.` },
			request,
			env,
			502,
		);
	}

	const master = await upstream.text();
	const { _ok, ...cors } = corsHeaders(request, env);

	return new Response(
		rewritePlaylist(master, "https://usher.ttvnw.net/", self, "media"),
		{
			headers: {
				"Content-Type": "application/vnd.apple.mpegurl",
				"Cache-Control": "no-store",
				...cors,
			},
		},
	);
}

async function handleMedia(target, request, env, self) {
	const upstream = await fetch(target);
	if (!upstream.ok) {
		return json(
			{ error: `The CDN returned ${upstream.status}.` },
			request,
			env,
			502,
		);
	}

	const playlist = await upstream.text();
	const { _ok, ...cors } = corsHeaders(request, env);

	return new Response(rewritePlaylist(playlist, target, self, "seg"), {
		headers: {
			"Content-Type": "application/vnd.apple.mpegurl",
			// Manifests are small and cheap to refetch; segments below are the
			// ones worth caching.
			"Cache-Control": "public, max-age=300",
			...cors,
		},
	});
}

/** Stream one segment through, without buffering it.
 *
 *  Handing `upstream.body` straight to the Response is what keeps CPU near
 *  zero no matter how large the segment is — the worker never holds the bytes,
 *  it just connects two pipes. Buffering here would be the difference between
 *  free and billed. */
async function handleSegment(target, request, env) {
	const upstream = await fetch(target);
	if (!upstream.ok) {
		return json(
			{ error: `The CDN returned ${upstream.status}.` },
			request,
			env,
			502,
		);
	}

	const length = Number(upstream.headers.get("Content-Length") || 0);
	if (length > MAX_UPSTREAM_BYTES) {
		return json({ error: "Segment unexpectedly large." }, request, env, 502);
	}

	const { _ok, ...cors } = corsHeaders(request, env);
	return new Response(upstream.body, {
		headers: {
			"Content-Type":
				upstream.headers.get("Content-Type") || "video/mp2t",
			// Segments are immutable once written, so a long cache costs nothing
			// and spares the request budget when someone re-runs a VOD.
			"Cache-Control": "public, max-age=86400",
			...cors,
		},
	});
}

/** Walks the whole chain and reports each step.
 *
 *  This is the question the whole design hangs on — Twitch is entitled to
 *  refuse datacenter IPs, and Cloudflare's egress is about as datacenter as it
 *  gets. It cannot be answered from a laptop, because from a home connection
 *  every step already works. Hit this after deploying. */
async function handleHealth(vod, request, env) {
	const steps = [];
	try {
		const token = await playbackToken(vod);
		steps.push({ step: "1-gql-token", ok: true });

		const usher = await fetch(usherUrl(vod, token));
		const master = usher.ok ? await usher.text() : "";
		steps.push({ step: "2-usher-master", ok: usher.ok, status: usher.status, bytes: master.length });
		if (!usher.ok) return json({ verdict: "usher refused this IP", steps }, request, env);

		const lines = master.split("\n").map((l) => l.trim());
		let media = "";
		for (let i = 0; i < lines.length; i++) {
			if (lines[i].startsWith("#EXT-X-STREAM-INF") && lines[i + 1]) {
				media = lines[i + 1];
				if (/audio.?only/i.test(lines[i])) break;
			}
		}

		const playlistRes = await fetch(media);
		const playlist = playlistRes.ok ? await playlistRes.text() : "";
		steps.push({ step: "3-cdn-playlist", ok: playlistRes.ok, status: playlistRes.status, bytes: playlist.length });
		if (!playlistRes.ok) return json({ verdict: "CDN refused the playlist", steps }, request, env);

		const first = playlist
			.split("\n")
			.map((l) => l.trim())
			.find((l) => l && !l.startsWith("#"));
		const seg = await fetch(new URL(first, media).toString());
		const bytes = seg.ok ? (await seg.arrayBuffer()).byteLength : 0;
		steps.push({ step: "4-cdn-segment", ok: seg.ok, status: seg.status, bytes });

		return json(
			{
				verdict: seg.ok
					? "WORKS — Twitch serves Cloudflare."
					: "CDN refused the segment",
				steps,
			},
			request,
			env,
		);
	} catch (e) {
		steps.push({ step: "threw", error: String(e) });
		return json({ verdict: "failed", steps }, request, env, 502);
	}
}

// ---- entry ----------------------------------------------------------------

export default {
	async fetch(request, env) {
		const url = new URL(request.url);
		const self = url.origin;

		if (request.method === "OPTIONS") {
			const { _ok, ...cors } = corsHeaders(request, env);
			return new Response(null, { status: 204, headers: cors });
		}
		if (request.method !== "GET") {
			return json({ error: "GET only." }, request, env, 405);
		}
		if (!corsHeaders(request, env)._ok) {
			return json(
				{
					error:
						"This proxy does not accept requests from that origin. Add it to ALLOWED_ORIGINS in twitch-proxy/wrangler.toml and redeploy.",
					origin: request.headers.get("Origin") || null,
				},
				request,
				env,
				403,
			);
		}

		const vod = url.searchParams.get("vod") || "";
		const target = url.searchParams.get("u") || "";

		try {
			switch (url.pathname) {
				case "/health":
					return await handleHealth(vod || "2822582470", request, env);

				case "/master":
					if (!/^\d+$/.test(vod)) {
						return json({ error: "vod must be a numeric VOD id." }, request, env, 400);
					}
					return await handleMaster(vod, request, env, self);

				case "/media":
				case "/seg":
					if (!allowedHost(target)) {
						return json({ error: "That target is not a Twitch URL." }, request, env, 403);
					}
					return url.pathname === "/media"
						? await handleMedia(target, request, env, self)
						: await handleSegment(target, request, env);

				default:
					return json(
						{ error: "Not found.", endpoints: ["/health", "/master?vod=", "/media?u=", "/seg?u="] },
						request,
						env,
						404,
					);
			}
		} catch (e) {
			return json({ error: String(e.message || e) }, request, env, 502);
		}
	},
};
