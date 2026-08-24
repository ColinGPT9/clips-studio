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
 *  Instead ffmpeg cuts the audio into 10-minute WAVs up front and each is
 *  handled on its own, so peak memory is one segment (~57 MB) whatever the
 *  recording's length. A six-hour VOD costs the same as a ten-minute one.
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
 *  ## Why WAV
 *
 *  Encoding it is a header and a cast — no encoder, no worker, no second wasm.
 *  ffmpeg emits it directly, and `readWav` below reads it back without an
 *  AudioContext.
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
	/** 16 kHz mono WAV, ready to post to the STT endpoint. */
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
	await ffmpeg.exec([
		"-i",
		inputPath,
		"-vn",
		"-ac",
		"1",
		"-ar",
		String(TARGET_RATE),
		"-f",
		"segment",
		"-segment_time",
		String(SEGMENT_SECONDS),
		`${prefix}%04d.wav`,
	]);

	const produced = (await ffmpeg.listDir("/"))
		.filter(
			(f) => !f.isDir && f.name.startsWith(prefix) && f.name.endsWith(".wav"),
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
		const pcm = readWav(owned);

		yield {
			blob: new Blob([owned.buffer], { type: "audio/wav" }),
			offsetSeconds: samplesSoFar / TARGET_RATE,
			pcm,
		};

		samplesSoFar += pcm.length;
		onProgress?.(++index);
	}
}

/** Samples out of a 16-bit PCM WAV.
 *
 *  Walks the RIFF chunks rather than assuming a 44-byte header: ffmpeg emits a
 *  LIST/INFO chunk before `data` often enough that a fixed offset would read
 *  metadata as audio, which sounds like static and measures like a permanent
 *  audio spike.
 *
 *  Done by hand rather than with `decodeAudioData` because this is a format we
 *  chose and control — no resampling, no AudioContext, no async. */
function readWav(bytes: Uint8Array): Float32Array {
	const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

	let offset = 12; // past "RIFF" + size + "WAVE"
	let dataStart = -1;
	let dataLength = 0;

	while (offset + 8 <= view.byteLength) {
		const id = String.fromCharCode(
			view.getUint8(offset),
			view.getUint8(offset + 1),
			view.getUint8(offset + 2),
			view.getUint8(offset + 3),
		);
		const size = view.getUint32(offset + 4, true);

		if (id === "data") {
			dataStart = offset + 8;
			dataLength = Math.min(size, view.byteLength - dataStart);
			break;
		}
		// Chunks are word-aligned; an odd size is followed by a pad byte.
		offset += 8 + size + (size % 2);
	}

	if (dataStart < 0) return new Float32Array(0);

	const samples = new Float32Array(Math.floor(dataLength / 2));
	for (let i = 0; i < samples.length; i++) {
		samples[i] = view.getInt16(dataStart + i * 2, true) / 32768;
	}
	return samples;
}
