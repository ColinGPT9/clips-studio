/** Reading an HLS VOD from the browser, whoever is serving it.
 *
 *  Twitch and Kick differ only in how you find the master playlist — one
 *  wants a signed GraphQL token, the other hands the URL over in a plain JSON
 *  response. Everything after that is the same standard HLS, so it lives here
 *  and `twitch.ts` / `kick.ts` are reduced to "give me a master playlist URL".
 *
 *  ## The two-rendition trick, which is what makes this possible at all
 *
 *  A long VOD is gigabytes of video and no browser tab will hold that. But
 *  transcription is the only stage that needs the *whole* recording, and it
 *  only needs the sound. So this picks two renditions:
 *
 *  * the **cheapest audible one** for transcription — an `audio_only` track
 *    where the platform offers one (Twitch), otherwise the lowest-bandwidth
 *    video rendition (Kick's 160p is ~230 kbps, about 100 MB an hour)
 *  * the **best video one** for cutting, of which only the few segments
 *    covering a chosen moment are ever fetched
 *
 *  That is the difference between this being a feature and being impossible.
 */

/** How many segments to pull at once.
 *
 *  Six is a compromise: an hour of VOD is hundreds of segments, so serial
 *  fetching is painfully slow, but opening hundreds of connections gets a
 *  browser to queue them anyway and makes the CDN look at you funny. */
const CONCURRENCY = 6;

export class VodError extends Error {}

export type VodSegment = {
	url: string;
	/** Seconds into the VOD that this segment starts. */
	start: number;
	duration: number;
};

type Rendition = {
	url: string;
	bandwidth: number;
	audioOnly: boolean;
};

/** Pull the rendition list out of a master playlist. */
function parseMaster(master: string, baseUrl: string): Rendition[] {
	const lines = master.split("\n").map((l) => l.trim());
	const renditions: Rendition[] = [];

	for (let i = 0; i < lines.length; i++) {
		if (!lines[i].startsWith("#EXT-X-STREAM-INF")) continue;

		const url = lines[i + 1];
		if (!url || url.startsWith("#")) continue;

		renditions.push({
			url: new URL(url, baseUrl).toString(),
			bandwidth: Number(lines[i].match(/BANDWIDTH=(\d+)/)?.[1] ?? 0),
			audioOnly: /audio.?only/i.test(lines[i]),
		});
	}

	return renditions;
}

/** Parse a media playlist into segments with absolute start times.
 *
 *  The running total is the whole point: HLS gives each segment a duration
 *  but never says where it sits in the recording, and every timestamp this
 *  app produces later is relative to the start of the VOD. */
function parseSegments(playlist: string, baseUrl: string): VodSegment[] {
	const lines = playlist.split("\n").map((l) => l.trim());
	const segments: VodSegment[] = [];
	let start = 0;
	let duration = 0;

	for (const line of lines) {
		if (line.startsWith("#EXTINF:")) {
			duration = Number.parseFloat(line.slice(8).split(",")[0]) || 0;
			continue;
		}
		if (!line || line.startsWith("#")) continue;

		segments.push({
			url: new URL(line, baseUrl).toString(),
			start,
			duration,
		});
		start += duration;
	}

	return segments;
}

export type VodInfo = {
	/** Cheapest audible rendition — what gets fetched in full. */
	audioSegments: VodSegment[];
	/** Best rendition — only sampled, never fetched whole. */
	videoSegments: VodSegment[];
	durationSeconds: number;
	/** True when the "audio" track is really a low-quality video rendition,
	 *  which is the Kick case. Only affects how much gets downloaded. */
	audioIsVideo: boolean;
};

/** Fetch and parse everything needed to transcribe and later cut, without
 *  pulling any media. */
export async function loadFromMaster(masterUrl: string): Promise<VodInfo> {
	const res = await fetch(masterUrl);
	if (!res.ok) {
		throw new VodError(
			res.status === 403
				? "That VOD is not publicly readable — subscriber-only VODs cannot be opened here."
				: `The VOD's playlist could not be read (HTTP ${res.status}).`,
		);
	}

	const renditions = parseMaster(await res.text(), masterUrl);
	if (!renditions.length) {
		throw new VodError("That VOD's playlist has no playable video in it.");
	}

	const byBandwidth = [...renditions].sort((a, b) => a.bandwidth - b.bandwidth);
	const audioOnly = renditions.find((r) => r.audioOnly);

	// Cheapest audible track. Twitch offers a real audio_only rendition; Kick
	// does not, so its 160p video stands in — still only a tenth of what the
	// full-quality stream would cost to download.
	const audio = audioOnly ?? byBandwidth[0];
	const video =
		byBandwidth.filter((r) => !r.audioOnly).at(-1) ?? byBandwidth.at(-1);

	if (!video) {
		throw new VodError("That VOD's playlist has no video track.");
	}

	const [audioList, videoList] = await Promise.all([
		fetch(audio.url).then((r) => r.text()),
		fetch(video.url).then((r) => r.text()),
	]);

	const audioSegments = parseSegments(audioList, audio.url);
	const videoSegments = parseSegments(videoList, video.url);
	const last = audioSegments.at(-1);

	return {
		audioSegments,
		videoSegments,
		durationSeconds: last ? last.start + last.duration : 0,
		audioIsVideo: !audioOnly,
	};
}

/** Fetch segments and join them.
 *
 *  MPEG-TS is designed to be concatenated — each segment carries its own sync
 *  bytes and headers — so joining the bytes gives a stream a decoder will read
 *  straight through. That is what makes this cheap; no remuxing is needed just
 *  to put the pieces together. */
async function fetchSegments(
	segments: VodSegment[],
	onProgress?: (done: number, total: number) => void,
): Promise<Blob> {
	const parts = new Array<ArrayBuffer>(segments.length);
	let done = 0;

	for (let i = 0; i < segments.length; i += CONCURRENCY) {
		const batch = segments.slice(i, i + CONCURRENCY);
		await Promise.all(
			batch.map(async (segment, offset) => {
				const res = await fetch(segment.url);
				if (!res.ok) {
					throw new VodError(
						`A piece of the VOD failed to download (HTTP ${res.status}).`,
					);
				}
				parts[i + offset] = await res.arrayBuffer();
				onProgress?.(++done, segments.length);
			}),
		);
	}

	return new Blob(parts, { type: "video/mp2t" });
}

/** The whole cheap track, for transcription. */
export function fetchAudio(
	vod: VodInfo,
	onProgress?: (done: number, total: number) => void,
): Promise<Blob> {
	return fetchSegments(vod.audioSegments, onProgress);
}

/** Only the video covering `start`–`end`.
 *
 *  One segment of slack on the leading side because a stream copy can only cut
 *  on a keyframe: without the earlier segment, a clip whose first keyframe sits
 *  just before its start would lose its opening. */
export function fetchRange(
	vod: VodInfo,
	start: number,
	end: number,
	onProgress?: (done: number, total: number) => void,
): Promise<Blob> {
	const range = rangeSegments(vod, start, end);
	if (!range.length) throw new VodError("That moment is outside the VOD.");
	return fetchSegments(range, onProgress);
}

function rangeSegments(vod: VodInfo, start: number, end: number): VodSegment[] {
	const covering = vod.videoSegments.filter(
		(s) => s.start + s.duration > start && s.start < end,
	);
	if (!covering.length) return [];

	const first = vod.videoSegments.indexOf(covering[0]);
	return vod.videoSegments.slice(
		Math.max(0, first - 1),
		first + covering.length,
	);
}

/** Where `start` sits inside the blob `fetchRange` returned.
 *
 *  The fetched range begins at its own first segment, not at the VOD's zero,
 *  so cutting with the original timestamp would seek to entirely the wrong
 *  place — minutes off on a long VOD. */
export function offsetWithinRange(vod: VodInfo, start: number): number {
	const range = rangeSegments(vod, start, start + 1);
	return range.length ? Math.max(0, start - range[0].start) : 0;
}
