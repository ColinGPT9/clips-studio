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

/** The last lines ffmpeg printed.
 *
 *  ffmpeg reports its real complaints here — "Unknown format", "No such file
 *  or directory", "Conversion failed" — and none of it reaches a caller
 *  otherwise: `exec` resolves with an exit code, and failures inside the
 *  worker arrive as a bare stringified error. Without this the page can say
 *  something failed but never what.
 *
 *  Bounded because a long conversion prints thousands of progress lines and
 *  none of the early ones matter. */
const LOG_LINES = 40;
const log: string[] = [];

/** The tail of ffmpeg's log, for attaching to an error message. Empty when
 *  ffmpeg has said nothing worth repeating. */
export function recentLog(): string {
	const lines = log
		.map((l) => l.trim())
		.filter(Boolean)
		// Progress spam drowns the one line that explains the failure.
		.filter((l) => !/^(frame|size)=/.test(l))
		.slice(-8);

	return lines.length ? `ffmpeg said:\n${lines.join("\n")}` : "";
}

/** Load ffmpeg once and reuse it.
 *
 *  The wasm binary is ~30 MB, so this is deliberately lazy — someone who
 *  signs in, scores a VOD and reads the results without cutting anything
 *  never pays for it. */
export function loadFFmpeg(onLog?: (line: string) => void): Promise<FFmpeg> {
	if (loading) return loading;

	loading = (async () => {
		const ffmpeg = new FFmpeg();

		ffmpeg.on("log", ({ message }) => {
			log.push(message);
			if (log.length > LOG_LINES) log.shift();
			onLog?.(message);
		});

		try {
			await ffmpeg.load({ coreURL: CORE_URL, wasmURL: WASM_URL });
		} catch (e) {
			// Let the next attempt retry rather than returning a permanently
			// rejected promise for the rest of the session.
			loading = null;
			throw new Error(
				`The video engine could not start: ${e instanceof Error ? e.message : String(e)}`,
			);
		}
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

/** Put a blob into ffmpeg's filesystem and return the path to it.
 *
 *  Used for the VOD paths, where the audio arrives as joined MPEG-TS segments
 *  with no file behind them. A local file does NOT come through here — it is
 *  mounted with `mountSource` instead, which reads lazily and so never copies
 *  a multi-gigabyte recording into memory.
 *
 *  This does hold the bytes: a three-hour Twitch audio-only track is ~290 MB.
 *  That is the one unavoidable copy on the VOD path, and it is still far
 *  cheaper than decoding the recording to samples, which would be 690 MB for
 *  the same three hours. */
export async function writeSource(
	ffmpeg: FFmpeg,
	source: Blob,
	name: string,
): Promise<string> {
	await ffmpeg.writeFile(name, new Uint8Array(await source.arrayBuffer()));
	return name;
}

/** Remove a file written by `writeSource`, once its segments have been cut. */
export async function discardSource(
	ffmpeg: FFmpeg,
	name: string,
): Promise<void> {
	try {
		await ffmpeg.deleteFile(name);
	} catch {
		// Already gone, or never written. Nothing to do.
	}
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
	//
	// The return value matters. `mount` RESOLVES WITH `false` when the
	// filesystem is not available (worker.js:90-97) rather than throwing, so
	// ignoring it lets a failed mount look like success — and the failure then
	// resurfaces much later as ffmpeg reporting a missing input file, which
	// points at entirely the wrong thing.
	const mounted = await ffmpeg.mount(
		"WORKERFS" as never,
		{ files: [file] },
		MOUNT,
	);
	if (mounted === false) {
		throw new Error(
			"This browser could not give the video engine access to that file (WORKERFS unavailable).",
		);
	}
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
