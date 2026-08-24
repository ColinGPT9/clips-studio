/** Turning whatever the visitor pointed at into transcribable segments.
 *
 *  All of this runs in the tab. Their file is never uploaded to us — only the
 *  audio segments go anywhere, and they go straight to OpenRouter on their own
 *  key.
 *
 *  ## Why segments, and not "decode it and slice the array"
 *
 *  The obvious approach — `decodeAudioData` on the whole recording, then cut
 *  the samples up — allocates the entire thing as float32 at **230 MB per
 *  hour**. That put a hard ceiling on length dressed up as a "limit": one hour
 *  meant ~440 MB, three hours ~1.3 GB, and four hours simply killed the tab.
 *  Nobody streams for an hour, so the ceiling was in the wrong place.
 *
 *  Instead ffmpeg cuts the audio into 10-minute segments up front and each is
 *  handled on its own, so peak memory is one segment whatever the recording's
 *  length. A six-hour VOD costs the same as a ten-minute one.
 *
 *  ## Why 10 minutes
 *
 *  The old chunk was 60 seconds, from misreading the STT constraint: providers
 *  time out after ~60 s of **processing**, which is not 60 s of **audio** —
 *  Whisper runs many times faster than realtime. The binding limit is the
 *  25 MB upload cap, which at 16 kHz mono 16-bit is 13 minutes. Ten leaves
 *  room and cuts the request count by 10x: a two-hour VOD went from 120
 *  transcription requests to 12, which matters against OpenRouter's 50/day
 *  free allowance.
 *
 *  ## Why ffmpeg only COPIES the audio
 *
 *  It would be natural to have ffmpeg decode and resample here, and it used
 *  to. But `-c:a copy` hands the decoding to the browser instead, which does
 *  it on the platform's own decoder rather than in a single wasm thread.
 *
 *  Measured with native ffmpeg on a 38-minute source: 0.58s to copy against
 *  1.27s to decode and resample, and 35 MB of output rather than 70 MB. The
 *  ratio is what carries over into wasm, where the cycles cost several times
 *  more.
 *
 *  Measured cost of copying rather than decoding continuously: each segment
 *  loses its predecessor's MDCT overlap, so roughly the FIRST 0.1 SECONDS of
 *  each segment decodes slightly differently (rms 2390 of 32768 against a
 *  continuous decode). Past that it settles to rms ~60-125, which is decoder
 *  rounding. That is a tenth of a second every ten minutes, at boundaries
 *  where transcription already splits — so it adds no new class of error.
 *  Verified aligned: best correlation lag 0, and the segments total exactly
 *  the same sample count as a continuous decode.
 *
 *  Honest about the size of this win: audio decoding was never the slow part.
 *  A run spends minutes in sequential transcription and scoring requests and
 *  seconds here. This is a cheap improvement to a small stage, not a fix for
 *  how long a run takes.
 */

import type { FFmpeg } from "@ffmpeg/ffmpeg";

const TARGET_RATE = 16000;

/** Seconds of audio per segment. See the note above on why not 60. */
export const SEGMENT_SECONDS = 600;

/** Thrown when a file cannot be read at all. There is deliberately no
 *  "too long" error any more: segmenting removed the length ceiling, so
 *  refusing a recording for its duration would be inventing a limit. */
export class AudioDecodeError extends Error {}

export type AudioSegment = {
	/** The still-encoded audio segment, ready to post to the STT endpoint.
	 *  Roughly half the size of the decoded WAV this used to be, so a run
	 *  uploads half as much. */
	blob: Blob;
	/** Seconds into the original recording. Every timestamp the model returns
	 *  is shifted by this — get it wrong and every clip after the first
	 *  segment is cut in the wrong place. */
	offsetSeconds: number;
	/** The same audio as samples, for the excitement measurement. Released as
	 *  soon as the caller is done with it. */
	pcm: Float32Array;
};

/** How long the recording is, without decoding it.
 *
 *  A media element reads only enough of the container to answer, so this is
 *  cheap even for a multi-gigabyte file — which matters because the answer is
 *  used to warn about cost BEFORE any work begins. */
export function probeDuration(file: Blob): Promise<number> {
	return new Promise((resolve, reject) => {
		const el = document.createElement("video");
		const url = URL.createObjectURL(file);

		const done = (fn: () => void) => {
			URL.revokeObjectURL(url);
			el.removeAttribute("src");
			fn();
		};

		el.preload = "metadata";
		el.onloadedmetadata = () =>
			done(() =>
				Number.isFinite(el.duration) && el.duration > 0
					? resolve(el.duration)
					: // Some containers report Infinity until fully read. Not fatal:
						// the caller only loses the up-front cost estimate.
						resolve(0),
			);
		el.onerror = () =>
			done(() =>
				reject(
					new AudioDecodeError(
						"This browser could not read that file. MP4, MOV, WebM, MP3 and WAV work; MKV usually does not.",
					),
				),
			);
		el.src = url;
	});
}

/** Cut the audio into segments and hand them back one at a time.
 *
 *  A generator rather than an array on purpose: the caller transcribes and
 *  measures each segment then drops it, so only one is ever in memory. Return
 *  an array and the whole recording is resident again, which is the thing this
 *  exists to avoid.
 *
 *  `input` must already be present in ffmpeg's filesystem — either written
 *  there, or mounted with `mountSource` for a local file, which reads lazily
 *  and so never copies a large file into memory. */
export async function* segmentAudio(
	ffmpeg: FFmpeg,
	inputPath: string,
	onProgress?: (done: number) => void,
): AsyncGenerator<AudioSegment> {
	const prefix = "seg";

	// `-vn` matters: Kick has no audio-only rendition, so what arrives can be a
	// low-quality video stream whose video track must be dropped rather than
	// transcoded. `-f segment` writes seg0000.wav, seg0001.wav, …
	// `-c:a copy` — ffmpeg COPIES the audio rather than decoding it, and the
	// browser decodes each segment instead. Measured on a 38-minute source
	// with native ffmpeg: 0.58s to copy versus 1.27s to decode and resample,
	// and the segments come out 35 MB rather than 70 MB. In wasm, where every
	// one of those cycles is several times more expensive, that ratio is worth
	// having — and the decode it replaces is done by the platform.
	//
	// Halving the bytes matters twice over: these same segments are what gets
	// uploaded to the transcription endpoint, so a run moves half as much.
	//
	// `exec` RESOLVES WITH ffmpeg's exit code — it does not throw on a non-zero
	// one (worker.js:42-48). Ignoring it means a failed conversion is only
	// noticed further down as "no segments produced", which describes the
	// symptom and hides the cause.
	const code = await ffmpeg.exec([
		"-i",
		inputPath,
		"-vn",
		"-c:a",
		"copy",
		"-f",
		"segment",
		"-segment_time",
		String(SEGMENT_SECONDS),
		`${prefix}%04d.m4a`,
	]);

	if (code !== 0) {
		throw new AudioDecodeError(
			`The video engine could not read the audio out of that (ffmpeg exited ${code}).`,
		);
	}

	const produced = (await ffmpeg.listDir("/"))
		.filter(
			(f) => !f.isDir && f.name.startsWith(prefix) && f.name.endsWith(".m4a"),
		)
		.map((f) => f.name)
		.sort();

	if (!produced.length) {
		throw new AudioDecodeError(
			"No audio could be read out of that. If the recording has no sound there is nothing to transcribe.",
		);
	}

	// Offsets are accumulated from the samples actually produced, NOT from
	// `index * SEGMENT_SECONDS`. The segment muxer cuts on packet boundaries,
	// so a "600 second" segment is 600 seconds *ish* — measured against a real
	// VOD, the first came back 9,600,042 samples instead of 9,600,000. Nominal
	// offsets therefore drift, and since these offsets are what every clip
	// timestamp is built on, the drift shows up as clips cut in the wrong place
	// further into the recording. Counting real samples cannot drift.
	let index = 0;
	let samplesSoFar = 0;

	for (const name of produced) {
		const bytes = (await ffmpeg.readFile(name)) as Uint8Array;
		// Drop it from the virtual filesystem immediately — leaving every
		// segment there would rebuild the whole recording in memory, one file
		// at a time, which is exactly what this design avoids.
		await ffmpeg.deleteFile(name);

		const owned = new Uint8Array(bytes.byteLength);
		owned.set(bytes);
		// Blob first, and decode from a COPY: `decodeAudioData` DETACHES the
		// buffer it is given. Decoding `owned.buffer` directly would leave
		// anything else holding it looking at zero bytes — a silent failure
		// that would surface as an empty upload rather than an error.
		const blob = new Blob([owned.buffer], { type: "audio/mp4" });
		const pcm = await decodeToMono16k(owned.slice().buffer);

		yield {
			blob,
			offsetSeconds: samplesSoFar / TARGET_RATE,
			pcm,
		};

		samplesSoFar += pcm.length;
		onProgress?.(++index);
	}
}

/** Decode one segment to 16 kHz mono, using the browser's own decoder.
 *
 *  This is the half of the work that used to happen inside ffmpeg.wasm. The
 *  segments now arrive still encoded (ffmpeg only copied them), and
 *  `decodeAudioData` hands them to the platform decoder — hardware-backed
 *  where the machine has one, and never a wasm cycle either way.
 *
 *  The rate conversion is free here, and that is the point of the
 *  `OfflineAudioContext`: `decodeAudioData` resamples to ITS context's rate,
 *  so asking for a 16 kHz context does the downsampling with the browser's own
 *  resampler. Sources are not always 48 kHz — 44.1 kHz gives a ratio of
 *  2.75625 — so "take every third sample" would be wrong in general and would
 *  alias speech when it was.
 *
 *  Only one segment is ever resident, which is the memory property the whole
 *  segmented design exists for. */
async function decodeToMono16k(bytes: ArrayBuffer): Promise<Float32Array> {
	// Length is required by the constructor but irrelevant to decoding; the
	// decoded buffer sets its own. The RATE is the part that matters.
	const ctx = new OfflineAudioContext(1, 1, TARGET_RATE);

	let decoded: AudioBuffer;
	try {
		decoded = await ctx.decodeAudioData(bytes);
	} catch {
		throw new AudioDecodeError(
			"This browser could not decode the audio in that recording.",
		);
	}

	if (decoded.numberOfChannels === 1) return decoded.getChannelData(0);

	// Average the channels rather than taking the first. A stream captured
	// with the mic on one side and game audio on the other would lose the
	// voice entirely if we just took channel 0.
	const mono = new Float32Array(decoded.length);
	for (let c = 0; c < decoded.numberOfChannels; c++) {
		const channel = decoded.getChannelData(c);
		for (let i = 0; i < channel.length; i++) mono[i] += channel[i];
	}
	for (let i = 0; i < mono.length; i++) mono[i] /= decoded.numberOfChannels;
	return mono;
}
