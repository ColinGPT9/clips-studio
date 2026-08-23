/** Finding the master playlist for a Kick VOD, from the browser.
 *
 *  Markedly simpler than Twitch: `kick.com/api/v1/video/{uuid}` returns the
 *  master playlist URL in plain JSON with no token, no signature and no
 *  persisted-query hash to go stale. Both the API and the CDN
 *  (`stream.kick.com`) send permissive CORS headers, so the visitor's browser
 *  can fetch all of it directly.
 *
 *  ## Kick has no audio-only rendition
 *
 *  Twitch offers an `audio_only` track, which is what makes transcribing a
 *  long VOD cheap there. Kick's manifest is video renditions only — but its
 *  lowest is 160p at roughly 230 kbps, about 100 MB an hour, which serves the
 *  same purpose. `hls.ts` picks the cheapest audible rendition rather than
 *  insisting on an audio-only one, so this needs no special handling; it is
 *  just worth knowing why a Kick run downloads a little more.
 *
 *  ## VODs expire
 *
 *  Kick's storage rules delete VODs after 30 days. A link that worked last
 *  month will 404, and that is Kick's behaviour rather than a bug here.
 */

import { VodError } from "./hls";

/** Pull the video UUID out of whatever the visitor pasted.
 *
 *  Kick VOD links come in two shapes — `/video/{uuid}` and the newer
 *  `/{channel}/videos/{uuid}` — and people paste both. A bare UUID is
 *  accepted too, since that is what the API itself takes. */
export function parseVideoId(input: string): string | null {
	const text = input.trim();
	const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

	if (UUID.test(text) && text.length === 36) return text.toLowerCase();

	try {
		const url = new URL(text);
		if (!/(^|\.)kick\.com$/i.test(url.hostname)) return null;

		// Both shapes end in the UUID, so one match over the path covers them.
		const found = url.pathname.match(UUID);
		return found ? found[0].toLowerCase() : null;
	} catch {
		return null;
	}
}

/** The master playlist URL for a Kick VOD. */
export async function masterPlaylistUrl(videoId: string): Promise<string> {
	const res = await fetch(`https://kick.com/api/v1/video/${videoId}`, {
		headers: { Accept: "application/json" },
	});

	if (!res.ok) {
		// A 404 here means only "Kick has nothing at that id". It could be
		// expired, deleted, private, or a link shape this does not recognise —
		// and Kick's retention varies by channel, so naming a number would be
		// inventing a cause. An earlier version confidently blamed a 30-day
		// expiry and told someone their three-day-old VOD had aged out.
		throw new VodError(
			res.status === 404
				? "Kick has no VOD at that link. Check the URL looks like kick.com/<channel>/videos/<id> — otherwise it may have been deleted, made private, or aged out of Kick's storage."
				: `Kick refused the request (HTTP ${res.status}). Kick sits behind bot protection that sometimes blocks requests it does not recognise — the desktop app handles Kick reliably.`,
		);
	}

	const source = (await res.json())?.source;
	if (typeof source !== "string" || !source) {
		throw new VodError(
			"Kick returned no playable stream for that VOD. It may be private or still processing.",
		);
	}
	return source;
}
