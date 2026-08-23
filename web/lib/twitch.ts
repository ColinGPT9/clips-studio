/** Finding the master playlist for a Twitch VOD, from the browser.
 *
 *  No server of ours is involved: `gql.twitch.tv` and `usher.ttvnw.net` both
 *  send permissive CORS headers, so the visitor's own browser can do exactly
 *  what the Twitch web player does — ask for a playback token, then ask usher
 *  for the manifest. Everything after that is ordinary HLS, handled in
 *  `hls.ts`.
 *
 *  ## This will break sometimes
 *
 *  The client-id and persisted-query hash below are the ones Twitch's own web
 *  player uses. They are not documented or promised to anyone, and Twitch
 *  changes them. When that happens this stops working and the desktop app —
 *  which uses yt-dlp and is maintained against exactly this churn — does not.
 *  Fail loudly and point there rather than trying to be clever.
 */

import { VodError } from "./hls";

/** The public web client-id Twitch's own player sends. Not a secret, not
 *  ours, and not tied to any account — it identifies the web player, which is
 *  what we are imitating. */
const CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko";

/** Persisted-query hash for PlaybackAccessToken. Twitch rotates these
 *  occasionally; a `null` token with no error is the symptom. */
const PLAYBACK_QUERY_HASH =
	"0828119ded1c13477966434e15800ff57ddacf13ba1911c129dc2200705b0712";

/** Pull the VOD id out of whatever the visitor pasted.
 *
 *  Accepts the forms people actually paste: a plain id, a /videos/ link, and
 *  a channel link with ?video=. Deliberately does NOT accept a clip URL —
 *  clipping a clip is not a thing, and silently treating it as a VOD id would
 *  produce a confusing "no such video". */
export function parseVodId(input: string): string | null {
	const text = input.trim();
	if (/^\d+$/.test(text)) return text;

	try {
		const url = new URL(text);
		if (!/(^|\.)twitch\.tv$/.test(url.hostname)) return null;

		const fromPath = url.pathname.match(/\/videos\/(\d+)/);
		if (fromPath) return fromPath[1];

		const fromQuery = url.searchParams.get("video");
		if (fromQuery && /^\d+$/.test(fromQuery)) return fromQuery;
	} catch {
		// Not a URL. Fall through.
	}
	return null;
}

type AccessToken = { value: string; signature: string };

async function playbackToken(vodId: string): Promise<AccessToken> {
	const res = await fetch("https://gql.twitch.tv/gql", {
		method: "POST",
		headers: { "Client-ID": CLIENT_ID, "Content-Type": "application/json" },
		body: JSON.stringify({
			operationName: "PlaybackAccessToken",
			variables: {
				isLive: false,
				login: "",
				isVod: true,
				vodID: vodId,
				playerType: "embed",
			},
			extensions: {
				persistedQuery: { version: 1, sha256Hash: PLAYBACK_QUERY_HASH },
			},
		}),
	});

	if (!res.ok) {
		throw new VodError(
			`Twitch refused the request (HTTP ${res.status}). This usually means Twitch has changed something — the desktop app handles Twitch reliably.`,
		);
	}

	const token = (await res.json())?.data?.videoPlaybackAccessToken;
	if (!token?.value || !token?.signature) {
		throw new VodError(
			"Twitch would not hand out a playback token for that VOD. It may be deleted, private, or subscriber-only.",
		);
	}
	return token;
}

/** The master playlist URL for a VOD, signed and ready to fetch. */
export async function masterPlaylistUrl(vodId: string): Promise<string> {
	const { value, signature } = await playbackToken(vodId);

	const url = new URL(`https://usher.ttvnw.net/vod/${vodId}.m3u8`);
	url.searchParams.set("allow_source", "true");
	url.searchParams.set("allow_audio_only", "true");
	url.searchParams.set("player", "twitchweb");
	url.searchParams.set("sig", signature);
	url.searchParams.set("token", value);

	return url.toString();
}
