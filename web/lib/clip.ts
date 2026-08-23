/** Cutting clips out of the visitor's own file, in their own tab.
 *
 *  ## Stream copy only
 *
 *  Every cut here is `-c copy`: the existing video and audio packets are
 *  remuxed into a new container without being decoded or re-encoded. That is
 *  the difference between a clip appearing in a second and a browser tab
 *  grinding for ten minutes, and it is why this page can exist on the kind of
 *  laptop it is aimed at.
 *
 *  The cost is frame accuracy. A copy cut can only start on a keyframe, so a
 *  clip may begin up to a second or two before the requested timestamp. That
 *  is the right trade for drafts someone is about to drop into an editor.
 *  **Do not "fix" this by re-encoding** — that is where browser video dies,
 *  and precise cuts, vertical reframing and burned captions are deliberately
 *  what the desktop app is for.
 *
 *  ## Why WORKERFS
 *
 *  ffmpeg.wasm normally wants its input written into an in-memory virtual
 *  filesystem, which for a 1 GB recording means a second full copy in RAM on
 *  top of the one the browser already holds — the fastest way to kill the
 *  tab. WORKERFS maps the `File` object itself and reads it lazily through
 *  the File API, so a long VOD costs almost nothing to mount.
 */

import { FFmpeg } from "@ffmpeg/ffmpeg";

/** Served from `public/ffmpeg/`, not a CDN.
 *
 *  Copied there by `scripts/copy-ffmpeg-core.mjs` at install time. Local
 *  rather than unpkg so the tool has no third-party runtime dependency that
 *  can rot, rate-limit, or be blocked on a corporate network. */
const CORE_URL = "/ffmpeg/ffmpeg-core.js";
const WASM_URL = "/ffmpeg/ffmpeg-core.wasm";

const MOUNT = "/mount";

let loading: Promise<FFmpeg> | null = null;

/** Load ffmpeg once and reuse it.
 *
 *  The wasm binary is ~30 MB, so this is deliberately lazy — someone who
 *  signs in, scores a VOD and reads the results without cutting anything
 *  never pays for it. */
export function loadFFmpeg(onLog?: (line: string) => void): Promise<FFmpeg> {
	if (loading) return loading;

	loading = (async () => {
		const ffmpeg = new FFmpeg();
		if (onLog) ffmpeg.on("log", ({ message }) => onLog(message));
		await ffmpeg.load({ coreURL: CORE_URL, wasmURL: WASM_URL });
		return ffmpeg;
	})();

	return loading;
}

export type CutClip = {
	blob: Blob;
	filename: string;
};

/** Cut one clip. `start` and `end` are seconds into the source.
 *
 *  `-ss` is placed BEFORE `-i` deliberately: that makes ffmpeg seek to the
 *  keyframe rather than decoding forward from zero to find the timestamp,
 *  which on a two-hour source is the difference between instant and minutes.
 *
 *  `-avoid_negative_ts make_zero` rebases the timestamps of the copied
 *  packets. Without it a stream-copied cut keeps its original presentation
 *  times, and players show a clip that begins at 01:23:45 with a long black
 *  lead-in, or refuse to play it at all. */
export async function cutClip(
	ffmpeg: FFmpeg,
	sourceName: string,
	start: number,
	end: number,
	outputName: string,
): Promise<CutClip> {
	const duration = Math.max(0.1, end - start);

	await ffmpeg.exec([
		"-ss",
		start.toFixed(3),
		"-i",
		`${MOUNT}/${sourceName}`,
		"-t",
		duration.toFixed(3),
		"-c",
		"copy",
		"-avoid_negative_ts",
		"make_zero",
		outputName,
	]);

	const data = await ffmpeg.readFile(outputName);
	// Clean up immediately: the output lives in MEMFS, and a run producing
	// five clips off a long VOD would otherwise hold all five in memory at
	// once for no reason.
	await ffmpeg.deleteFile(outputName);

	const bytes = data as Uint8Array;
	if (!bytes.length) {
		throw new Error(
			"FFmpeg produced an empty clip. The source may use a codec that cannot be copied into an MP4.",
		);
	}

	// Copied into a plain ArrayBuffer rather than handed to Blob directly.
	// ffmpeg.wasm types its output as `Uint8Array<ArrayBufferLike>`, which may
	// be backed by a SharedArrayBuffer and so is not a valid BlobPart. The
	// copy is one clip's worth of bytes and happens once.
	const owned = new Uint8Array(bytes.byteLength);
	owned.set(bytes);

	return {
		blob: new Blob([owned.buffer], { type: "video/mp4" }),
		filename: outputName,
	};
}

/** Decode MPEG-TS audio into a WAV the browser can read.
 *
 *  Needed only on the VOD paths. `decodeAudioData` handles MP4, WebM, MP3 and
 *  WAV, but not a raw `.ts` container — so the HLS segments we join have to go
 *  through ffmpeg before the normal decode path can touch them. Audio-only
 *  transcoding is cheap even in wasm; this is nothing like asking it to
 *  re-encode video.
 *
 *  `-vn` matters more for Kick than Twitch: Kick has no audio-only rendition,
 *  so what arrives here is a low-quality *video* stream and the video track
 *  has to be thrown away rather than transcoded.
 *
 *  Output is already 16 kHz mono, matching what `audio.ts` would resample to
 *  anyway, which keeps the WAV small enough to hand straight back. */
export async function tsAudioToWav(ffmpeg: FFmpeg, audio: Blob): Promise<Blob> {
	const input = "vod-audio.ts";
	const output = "vod-audio.wav";

	await ffmpeg.writeFile(input, new Uint8Array(await audio.arrayBuffer()));
	await ffmpeg.exec([
		"-i",
		input,
		"-vn",
		"-ac",
		"1",
		"-ar",
		"16000",
		"-f",
		"wav",
		output,
	]);

	const data = (await ffmpeg.readFile(output)) as Uint8Array;

	// Both copies are large — a long VOD's audio is tens of megabytes each —
	// so drop them from the virtual filesystem before returning rather than
	// leaving them there for the rest of the session.
	await ffmpeg.deleteFile(input);
	await ffmpeg.deleteFile(output);

	if (!data.length) {
		throw new Error("Could not read the audio out of that VOD.");
	}

	const owned = new Uint8Array(data.byteLength);
	owned.set(data);
	return new Blob([owned.buffer], { type: "audio/wav" });
}

/** Cut from bytes already in memory, rather than a mounted file.
 *
 *  The VOD paths fetch just the segments covering one moment, so there is no
 *  file to mount — and `start` here is relative to that fetched range, not to
 *  the VOD. See `offsetWithinRange`. */
export async function cutFromBlob(
	ffmpeg: FFmpeg,
	source: Blob,
	start: number,
	duration: number,
	outputName: string,
): Promise<CutClip> {
	const input = "range.ts";
	await ffmpeg.writeFile(input, new Uint8Array(await source.arrayBuffer()));

	await ffmpeg.exec([
		"-ss",
		start.toFixed(3),
		"-i",
		input,
		"-t",
		Math.max(0.1, duration).toFixed(3),
		"-c",
		"copy",
		"-avoid_negative_ts",
		"make_zero",
		outputName,
	]);

	const data = (await ffmpeg.readFile(outputName)) as Uint8Array;
	await ffmpeg.deleteFile(input);
	await ffmpeg.deleteFile(outputName);

	if (!data.length) {
		throw new Error("FFmpeg produced an empty clip from that VOD range.");
	}

	const owned = new Uint8Array(data.byteLength);
	owned.set(data);
	return {
		blob: new Blob([owned.buffer], { type: "video/mp4" }),
		filename: outputName,
	};
}

/** Make the visitor's file visible to ffmpeg without copying it.
 *
 *  Mounting is idempotent per session but not per file, so switching source
 *  files means unmounting first — otherwise the second file is invisible and
 *  ffmpeg reports a missing input for a file the user can plainly see. */
export async function mountSource(ffmpeg: FFmpeg, file: File): Promise<string> {
	try {
		await ffmpeg.createDir(MOUNT);
	} catch {
		// Already there from a previous file.
	}
	try {
		await ffmpeg.unmount(MOUNT);
	} catch {
		// Nothing mounted yet, which is the common case.
	}

	// `WORKERFS` as a string rather than the FFFSType enum: the enum moved
	// between 0.12.x releases and the worker only ever compares the string.
	await ffmpeg.mount("WORKERFS" as never, { files: [file] }, MOUNT);
	return file.name;
}

/** A filename that is safe on every OS and still says what the clip is.
 *
 *  Clip hooks are model output and routinely contain quotes, slashes, emoji
 *  and newlines — all of which are legal in a hook and illegal, or merely
 *  disastrous, in a filename. */
export function clipFilename(index: number, hook: string): string {
	const words = hook
		.toLowerCase()
		.replace(/[^a-z0-9\s-]/g, "")
		.trim()
		.split(/\s+/)
		.slice(0, 6)
		.join("-");

	const stem = words || "clip";
	return `${String(index + 1).padStart(2, "0")}-${stem}.mp4`;
}
