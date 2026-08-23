/** Twitch VODs, by way of the proxy.
 *
 *  ## Why there is almost nothing here any more
 *
 *  An earlier version did the whole dance in the browser: a GraphQL call for
 *  a playback token, then `usher.ttvnw.net` for the manifest. The token step
 *  works fine — `gql.twitch.tv` allows the origin and the `Client-ID` header.
 *  Everything after it does not. `usher` sends **no CORS headers on a
 *  successful response**, and neither does the CloudFront CDN behind it, for
 *  the manifests or the `.ts` segments. The browser refuses to hand any of it
 *  to the page.
 *
 *  > Recorded because it already caught us: Twitch DOES send
 *  > `Access-Control-Allow-Origin: *` on ERROR responses. A made-up VOD id
 *  > 403s with permissive headers and reads as proof the path is open. Verify
 *  > CORS against a SUCCESSFUL response.
 *
 *  So the token and manifest logic moved to `twitch-proxy/`, a Cloudflare
 *  Worker, and this file is reduced to recognising a link and pointing at it.
 *  The worker rewrites every URL in the playlists to point back at itself, so
 *  `hls.ts` needs no knowledge of any of this — it fetches a master playlist
 *  and follows the URLs it finds, exactly as it does for Kick.
 */

import { TWITCH_PROXY } from "./content";
import { VodError } from "./hls";

/** Whether Twitch links can be offered at all — false until the worker in
 *  `twitch-proxy/` is deployed and its URL set in `content.ts`. */
export const enabled = (): boolean => Boolean(TWITCH_PROXY);

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

/** The master playlist URL — served by the proxy, not by Twitch.
 *
 *  Async only to match the Kick source's shape, so `source.ts` can treat the
 *  two identically. There is nothing to await. */
export async function masterPlaylistUrl(vodId: string): Promise<string> {
	if (!TWITCH_PROXY) {
		throw new VodError(
			"Twitch links are not available here at the moment. The desktop app handles Twitch directly.",
		);
	}
	return `${TWITCH_PROXY.replace(/\/$/, "")}/master?vod=${encodeURIComponent(vodId)}`;
}
