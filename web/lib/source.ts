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
	| { kind: "twitch"; id: string }
	| { kind: "kick"; id: string }
	| { kind: "unsupported"; message: string };

const YOUTUBE = /(^|\.)(youtube\.com|youtu\.be)$/i;

export function identify(input: string): Source {
	const text = input.trim();
	if (!text) {
		return { kind: "unsupported", message: "Paste a Twitch or Kick VOD link." };
	}

	const twitchId = twitch.parseVodId(text);
	if (twitchId) return { kind: "twitch", id: twitchId };

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
		return {
			kind: "unsupported",
			message:
				"That is a Twitch link, but not to a VOD. Use a /videos/ link — clips and live channels cannot be processed.",
		};
	}

	return {
		kind: "unsupported",
		message:
			"Only Twitch and Kick VOD links work here. For anything else, download the video and choose it as a file above.",
	};
}

/** The master playlist URL for a source, however that platform hands it over. */
export function masterPlaylistUrl(source: Source): Promise<string> {
	switch (source.kind) {
		case "twitch":
			return twitch.masterPlaylistUrl(source.id);
		case "kick":
			return kick.masterPlaylistUrl(source.id);
		default:
			return Promise.reject(new Error(source.message));
	}
}

export const PLATFORM_LABEL: Record<"twitch" | "kick", string> = {
	twitch: "Twitch",
	kick: "Kick",
};
