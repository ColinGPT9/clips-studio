/** Vertical Live: a livestream already composed as 9:16 during the broadcast.
 *
 *  The same definition as the desktop app's core/modes.py, and the numbers
 *  here must match it (tests/test_web_vertical_sync.py checks). A YouTube
 *  vertical live, the vertical feed of a Twitch Dual Format or Streamlabs Dual
 *  Output stream, or a downloaded Instagram/TikTok/YouTube live MP4: the
 *  streamer already placed everything on a 9:16 canvas, so it is kept whole.
 *
 *  This page never crops or reframes anything anyway: clips are cut straight
 *  out of the source with no re-encode. So here Vertical Live means taking the
 *  VOD's vertical version, and refusing a source that isn't 9:16 rather than
 *  clipping the wrong thing. A 1080x1920 source gives 1080x1920 clips; a
 *  smaller 9:16 one keeps its own size (the desktop app scales to 1080x1920,
 *  which needs a re-encode this page doesn't do). */

/** The standard vertical output. */
export const TARGET = { width: 1080, height: 1920 };

/** Width / height counted as 9:16 (0.5625), with room for a slightly cropped
 *  canvas. */
export const VERTICAL_MIN = 0.5;
export const VERTICAL_MAX = 0.6;

export const MISMATCH =
	"This video is not a vertical 9:16 source. Vertical Live mode expects a vertically composed video.";

export const NO_VERTICAL_VERSION =
	"This VOD has no vertical version. Twitch keeps the vertical version of a Dual Format stream for 7 days after it ends. If you have the vertical recording as a file, choose A file instead.";

export type Orientation = "vertical" | "horizontal" | "other";

export function orientation(width: number, height: number): Orientation {
	if (width <= 0 || height <= 0) return "other";
	const ratio = width / height;
	if (ratio >= VERTICAL_MIN && ratio <= VERTICAL_MAX) return "vertical";
	const inverse = 1 / ratio;
	if (inverse >= VERTICAL_MIN && inverse <= VERTICAL_MAX) return "horizontal";
	return "other";
}

/** A file's displayed size, from its header only (instant on any length).
 *  0x0 when the browser can't read it, which orientation() calls "other". */
export function probeSize(
	file: Blob,
): Promise<{ width: number; height: number }> {
	return new Promise((resolve) => {
		const el = document.createElement("video");
		const url = URL.createObjectURL(file);
		const done = (size: { width: number; height: number }) => {
			URL.revokeObjectURL(url);
			el.removeAttribute("src");
			resolve(size);
		};
		el.preload = "metadata";
		el.onloadedmetadata = () =>
			done({ width: el.videoWidth, height: el.videoHeight });
		el.onerror = () => done({ width: 0, height: 0 });
		el.src = url;
	});
}
