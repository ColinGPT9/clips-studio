/** Turning whatever the visitor dropped in into chunks an STT model accepts.
 *
 *  All of this runs in the tab. The file is never uploaded to us — only the
 *  small WAV chunks go anywhere, and they go straight to OpenRouter on the
 *  visitor's own key.
 *
 *  ## Why 16 kHz mono
 *
 *  Speech models resample to 16 kHz mono internally anyway, so doing it here
 *  costs nothing in accuracy and buys a ~12x reduction in memory over 48 kHz
 *  stereo. `decodeAudioData` resamples to its context's rate as it decodes,
 *  so an OfflineAudioContext at 16 kHz means the expensive intermediate never
 *  exists in the first place — the difference between a 45-minute recording
 *  costing ~170 MB and costing ~2 GB.
 *
 *  ## Why the size cap is real and not timidity
 *
 *  Decoding needs the whole file as one ArrayBuffer, because no browser API
 *  demuxes just the audio track without pulling in a full MP4 parser. That
 *  read is the hard ceiling on this page, and exceeding it does not fail
 *  politely — the tab dies with no error anyone can act on. So the check
 *  happens up front, with a message that names the desktop app, which has no
 *  such limit.
 */

/** Refuse before reading rather than crash while reading.
 *
 *  Deliberately below where browsers actually fall over: a tab that dies mid
 *  decode looks like the site is broken, whereas a stated limit looks like a
 *  free tool having a free tool's boundary. */
export const MAX_FILE_BYTES = 1_200_000_000;

/** Long enough for a highlights-worth of stream, short enough that the
 *  decoded buffer stays comfortable. ~1 hour at 16 kHz mono is ~230 MB. */
export const MAX_DURATION_SECONDS = 3600;

/** Upstream STT providers time out after ~60 seconds of PROCESSING, which
 *  binds long before the 25 MB upload cap — so this is a processing budget,
 *  not a size one. Raising it does not save requests, it just starts failing. */
export const CHUNK_SECONDS = 60;

const TARGET_RATE = 16000;

export type AudioChunk = {
	blob: Blob;
	/** Seconds into the original recording. Every timestamp the STT model
	 *  returns for this chunk gets shifted by this, which is the only thing
	 *  keeping clip boundaries aligned with the source. */
	offsetSeconds: number;
};

export class AudioTooLongError extends Error {}
export class AudioDecodeError extends Error {}

/** Decode any file the browser can read into 16 kHz mono PCM.
 *
 *  Works on video containers as well as audio ones: `decodeAudioData` pulls
 *  the audio track out of an MP4 or WebM on its own. It does NOT work on
 *  everything a media player would open — MKV in particular is widely
 *  unsupported — hence the specific error rather than a generic failure. */
export async function decodeToMono16k(file: Blob): Promise<Float32Array> {
	if (file.size > MAX_FILE_BYTES) {
		throw new AudioTooLongError(
			`That file is ${(file.size / 1e9).toFixed(1)} GB. This page can handle about ${(
				MAX_FILE_BYTES / 1e9
			).toFixed(1)} GB — the desktop app has no limit.`,
		);
	}

	const bytes = await file.arrayBuffer();

	// Length is required by the constructor but irrelevant to decoding; the
	// decoded buffer sets its own. The rate is the part that matters.
	const ctx = new OfflineAudioContext(1, 1, TARGET_RATE);

	let decoded: AudioBuffer;
	try {
		decoded = await ctx.decodeAudioData(bytes);
	} catch {
		throw new AudioDecodeError(
			"This browser could not read the audio in that file. MP4, MOV, WebM, MP3 and WAV work; MKV usually does not.",
		);
	}

	if (decoded.duration > MAX_DURATION_SECONDS) {
		throw new AudioTooLongError(
			`That recording is ${Math.round(decoded.duration / 60)} minutes. This page handles up to ${
				MAX_DURATION_SECONDS / 60
			} — the desktop app sits on a three-hour VOD happily.`,
		);
	}

	return downmix(decoded);
}

/** Average all channels into one.
 *
 *  Averaging rather than taking channel 0: a stream captured with the mic on
 *  one side and game audio on the other would lose the voice entirely if we
 *  just took the first channel. */
function downmix(buffer: AudioBuffer): Float32Array {
	if (buffer.numberOfChannels === 1) return buffer.getChannelData(0);

	const out = new Float32Array(buffer.length);
	for (let c = 0; c < buffer.numberOfChannels; c++) {
		const channel = buffer.getChannelData(c);
		for (let i = 0; i < channel.length; i++) out[i] += channel[i];
	}
	for (let i = 0; i < out.length; i++) out[i] /= buffer.numberOfChannels;
	return out;
}

/** Split PCM into WAV blobs, each tagged with where it starts.
 *
 *  WAV rather than a compressed format because encoding it is a header and a
 *  cast — no encoder library, no worker, no wasm. At 16 kHz mono a 60-second
 *  chunk is under 2 MB, so the 25 MB upload cap is never in sight and the
 *  bytes saved by compressing would not pay for the dependency. */
export function toChunks(pcm: Float32Array): AudioChunk[] {
	const perChunk = CHUNK_SECONDS * TARGET_RATE;
	const chunks: AudioChunk[] = [];

	for (let start = 0; start < pcm.length; start += perChunk) {
		const slice = pcm.subarray(start, Math.min(start + perChunk, pcm.length));
		// A trailing sliver is silence or a syllable; sending it costs a whole
		// request and a whole provider round-trip to transcribe nothing.
		if (slice.length < TARGET_RATE) break;

		chunks.push({
			blob: encodeWav(slice),
			offsetSeconds: start / TARGET_RATE,
		});
	}

	return chunks;
}

/** 16-bit PCM WAV. */
function encodeWav(samples: Float32Array): Blob {
	const buffer = new ArrayBuffer(44 + samples.length * 2);
	const view = new DataView(buffer);

	const ascii = (offset: number, text: string) => {
		for (let i = 0; i < text.length; i++)
			view.setUint8(offset + i, text.charCodeAt(i));
	};

	ascii(0, "RIFF");
	view.setUint32(4, 36 + samples.length * 2, true);
	ascii(8, "WAVE");
	ascii(12, "fmt ");
	view.setUint32(16, 16, true); // PCM header size
	view.setUint16(20, 1, true); // format: PCM
	view.setUint16(22, 1, true); // channels
	view.setUint32(24, TARGET_RATE, true);
	view.setUint32(28, TARGET_RATE * 2, true); // byte rate
	view.setUint16(32, 2, true); // block align
	view.setUint16(34, 16, true); // bits per sample
	ascii(36, "data");
	view.setUint32(40, samples.length * 2, true);

	// Clamp before scaling: decoded audio can sit slightly outside [-1, 1],
	// and letting that wrap turns a loud moment into white noise — which is
	// exactly the moment most likely to be worth clipping.
	for (let i = 0; i < samples.length; i++) {
		const s = Math.max(-1, Math.min(1, samples[i]));
		view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
	}

	return new Blob([buffer], { type: "audio/wav" });
}
