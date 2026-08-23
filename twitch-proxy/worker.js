/**
 * PROBE — does Twitch serve requests coming from Cloudflare's network?
 *
 * This is a throwaway diagnostic, not the proxy. It exists to answer one
 * question before any real work goes into building the proxy: Twitch is
 * entitled to refuse datacenter IPs, and Cloudflare's egress is about as
 * datacenter as it gets. If Twitch blocks it, the whole approach is dead and
 * we should find out in ten minutes rather than after a day of integration.
 *
 * It walks the exact chain the real proxy would:
 *
 *   1. POST gql.twitch.tv          -> playback access token
 *   2. GET  usher.ttvnw.net        -> master playlist
 *   3. GET  the CDN media playlist -> segment list
 *   4. GET  one .ts segment        -> actual bytes
 *
 * and reports the status of each, so a failure says WHICH step broke rather
 * than just "it didn't work".
 *
 *   npx wrangler deploy
 *   curl "https://<your-worker>.workers.dev/?vod=2822582470"
 *
 * A note on why this cannot be tested from a laptop: from a home connection
 * every step already works — that is how the chain was mapped in the first
 * place. The only thing in question is the source IP, so the test has to run
 * where the proxy would run.
 *
 * DELETE THIS FILE (or replace it with the real proxy) once the question is
 * answered. It has no auth and no rate limiting, and is not meant to stay up.
 */

const CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko";
const PLAYBACK_QUERY_HASH =
	"0828119ded1c13477966434e15800ff57ddacf13ba1911c129dc2200705b0712";

/** A public VOD to test against. Twitch VODs expire, so if every step fails
 *  with 404 the id is stale rather than Cloudflare being blocked — pass
 *  ?vod=<id> with something current before concluding anything. */
const DEFAULT_VOD = "2822582470";

export default {
	async fetch(request) {
		const vod = new URL(request.url).searchParams.get("vod") || DEFAULT_VOD;
		const steps = [];

		const record = (name, res, extra = {}) =>
			steps.push({
				step: name,
				status: res?.status ?? null,
				ok: Boolean(res?.ok),
				...extra,
			});

		try {
			// 1. Playback token.
			const gql = await fetch("https://gql.twitch.tv/gql", {
				method: "POST",
				headers: {
					"Client-ID": CLIENT_ID,
					"Content-Type": "application/json",
				},
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

			const gqlBody = await gql.json().catch(() => null);
			const token = gqlBody?.data?.videoPlaybackAccessToken;
			record("1-gql-token", gql, { gotToken: Boolean(token?.value) });

			if (!token?.value) {
				return report(steps, "Twitch would not issue a token from this IP.");
			}

			// 2. Master playlist.
			const usherUrl = new URL(`https://usher.ttvnw.net/vod/${vod}.m3u8`);
			usherUrl.searchParams.set("allow_source", "true");
			usherUrl.searchParams.set("allow_audio_only", "true");
			usherUrl.searchParams.set("player", "twitchweb");
			usherUrl.searchParams.set("sig", token.signature);
			usherUrl.searchParams.set("token", token.value);

			const usher = await fetch(usherUrl.toString());
			const master = usher.ok ? await usher.text() : "";
			record("2-usher-master", usher, { bytes: master.length });

			if (!usher.ok) {
				return report(steps, "usher refused this IP — the approach is dead.");
			}

			// 3. Media playlist. Prefer audio_only, since that is what the real
			//    proxy would pull in full.
			const lines = master.split("\n").map((l) => l.trim());
			let mediaUrl = "";
			for (let i = 0; i < lines.length; i++) {
				if (lines[i].startsWith("#EXT-X-STREAM-INF") && lines[i + 1]) {
					mediaUrl = lines[i + 1];
					if (/audio.?only/i.test(lines[i])) break;
				}
			}

			const media = await fetch(mediaUrl);
			const playlist = media.ok ? await media.text() : "";
			record("3-cdn-playlist", media, {
				bytes: playlist.length,
				audioOnly: /audio_only/.test(mediaUrl),
			});

			if (!media.ok) return report(steps, "The CDN refused the playlist.");

			// 4. One real segment — the bytes that would actually cost money.
			const first = playlist
				.split("\n")
				.map((l) => l.trim())
				.find((l) => l && !l.startsWith("#"));
			const segUrl = new URL(first, mediaUrl).toString();

			const segment = await fetch(segUrl);
			const bytes = segment.ok
				? (await segment.arrayBuffer()).byteLength
				: 0;
			record("4-cdn-segment", segment, { bytes });

			return report(
				steps,
				segment.ok
					? "WORKS — Twitch serves Cloudflare. The proxy approach is viable."
					: "The CDN refused the segment.",
			);
		} catch (e) {
			steps.push({ step: "threw", error: String(e) });
			return report(steps, "Something threw — see the last step.");
		}
	},
};

function report(steps, verdict) {
	return new Response(JSON.stringify({ verdict, steps }, null, 2), {
		headers: {
			"Content-Type": "application/json",
			"Access-Control-Allow-Origin": "*",
		},
	});
}
