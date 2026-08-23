/** What can be opened from a link, and what has to be said when it cannot.
 *
 *  Mirrors `sources/dispatch.py` in the main repo, minus the platforms a
 *  browser cannot reach. The honest answer differs per platform and each one
 *  deserves its own sentence — "unsupported URL" tells someone nothing about
 *  what to do next, and the thing to do next is usually "download the app",
 *  which is the entire point of this page.
 *
 *  ## Why YouTube is not here
 *
 *  Not an oversight and not laziness. YouTube's video servers send no
 *  cross-origin headers, so a browser is physically unable to fetch from
 *  them — this is enforced by the browser, not by anything we could work
 *  around. The third-party "converter" sites are not an escape hatch either:
 *  they are websites rather than APIs, they are Cloudflare-fronted with no
 *  CORS headers of their own, break constantly, and would put somebody else's
 *  legal and advertising baggage on this domain.
 *
 *  What actually solves the real case: a creator clipping their OWN video can
 *  export it from YouTube Studio in two clicks and drop the file in here. So
 *  that is what we tell them.
 */

import * as kick from "./kick";
import * as twitch from "./twitch";

export type Source =
	| { kind: "kick"; id: string }
	| { kind: "unsupported"; message: string };

const YOUTUBE = /(^|\.)(youtube\.com|youtu\.be)$/i;

/** Why Twitch is recognised but refused.
 *
 *  It was built and then removed, so this is worth stating precisely enough
 *  that nobody rebuilds it. The GraphQL token step works fine from a browser —
 *  `gql.twitch.tv` allows the origin and the `Client-ID` header. The failure
 *  is one step later: `usher.ttvnw.net` returns **no CORS headers at all on a
 *  successful response**, and neither does the CloudFront CDN behind it. The
 *  browser therefore refuses to hand the playlist to the page.
 *
 *  The trap is that Twitch DOES send `Access-Control-Allow-Origin: *` on
 *  error responses — a 403 for a nonexistent VOD looks permissive, which is
 *  what made this appear to work when it was checked against a made-up ID.
 *  Verify against a real VOD, or the errors lie to you.
 *
 *  Fixing it needs a proxy, which means a server, its bandwidth bill, and
 *  someone's name on the traffic. The desktop app already does this properly
 *  with yt-dlp. */
const TWITCH_BLOCKED =
	"Twitch VODs cannot be opened from a browser — Twitch's video servers refuse cross-origin requests, and no web page can get around that. The desktop app handles Twitch links directly. Kick VOD links work here, and so does any video file from your computer.";

export function identify(input: string): Source {
	const text = input.trim();
	if (!text) {
		return { kind: "unsupported", message: "Paste a Kick VOD link." };
	}

	if (twitch.parseVodId(text)) {
		return { kind: "unsupported", message: TWITCH_BLOCKED };
	}

	const kickId = kick.parseVideoId(text);
	if (kickId) return { kind: "kick", id: kickId };

	let host = "";
	try {
		host = new URL(text).hostname;
	} catch {
		return {
			kind: "unsupported",
			message:
				"That does not look like a link. Paste a Twitch or Kick VOD URL, or choose a file from your computer.",
		};
	}

	if (YOUTUBE.test(host)) {
		return {
			kind: "unsupported",
			message:
				"YouTube links cannot be opened from a browser — Google's video servers refuse cross-origin requests, and no web page can get around that. If it is your own video, download it from YouTube Studio and drop the file in above. The desktop app handles YouTube links directly.",
		};
	}

	if (/kick\.com$/i.test(host)) {
		return {
			kind: "unsupported",
			message:
				"That is a Kick link, but not to a VOD. Use a link to a saved video — live channels and clips cannot be processed.",
		};
	}

	if (/twitch\.tv$/i.test(host)) {
		return { kind: "unsupported", message: TWITCH_BLOCKED };
	}

	return {
		kind: "unsupported",
		message:
			"Only Kick VOD links work here. For anything else, download the video and choose it as a file above.",
	};
}

/** The master playlist URL for a source, however that platform hands it over. */
export function masterPlaylistUrl(source: Source): Promise<string> {
	return source.kind === "kick"
		? kick.masterPlaylistUrl(source.id)
		: Promise.reject(new Error(source.message));
}
