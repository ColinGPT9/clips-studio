/** Audio excitement signals, ported from `analysis/audio_features.py`.
 *
 *  ## Why this exists
 *
 *  `config/prompts/score_clips.txt` tells the model, in its own words:
 *
 *  > "A mild-sounding transcript line that coincides with a big audio spike is
 *  > often a GREAT clip — weigh the events seriously."
 *
 *  Without an events list, that instruction is dead text and the model judges
 *  from words alone. In the desktop app the audio channel is weighted 0.20 of
 *  the final score, so leaving it out is not a rounding error — it is a
 *  meaningfully worse pick list, most obviously on gameplay and IRL streams
 *  where the funniest moment is often a reaction rather than a sentence.
 *
 *  ## Why it is free
 *
 *  This is signal processing, not ML: RMS energy, onset counting and
 *  zero-crossing rate. No model, no download, no network. And the input is
 *  the same 16 kHz mono PCM `audio.ts` has already decoded for
 *  transcription — the numbers are sitting in memory either way.
 *
 *  Kept a faithful port rather than a fresh take, for the same reason the
 *  prompt is copied verbatim: this page should demonstrate the judgement the
 *  product actually ships. Constants and thresholds below match
 *  `analysis/audio_features.py` and `analysis/fusion.py` exactly.
 *
 *  Deliberately NOT ported: the visual channel (motion, scene cuts, flashes)
 *  and the reaction channel (active-speaker detection). Both need video
 *  decode and, in the reaction case, a model. They stay desktop-only.
 */

const SAMPLE_RATE = 16000;
/** 50 ms analysis frames. */
const FRAME = SAMPLE_RATE / 20;
const FRAMES_PER_SEC = SAMPLE_RATE / FRAME;

/** Percentile rank above which a second is called notable. From
 *  `_build_events` in `analysis/fusion.py`. */
const EVENT_THRESHOLD = 0.92;

/** Cap on events handed to the model, so a rowdy stream cannot crowd out the
 *  transcript itself. Also from `_build_events`. */
const MAX_EVENTS = 120;

export type AudioEvent = { second: number; description: string };

/** Raw per-second measurements for one slice of audio.
 *
 *  Deliberately raw — no ranking, no gating, no rolling median. Those all
 *  compare a second against the WHOLE recording, so they cannot be computed
 *  from a slice without changing the answer. Extract here, judge in
 *  `combineFeatures` once every slice has been seen.
 *
 *  This split is what lets a three-hour VOD be processed ten minutes at a
 *  time. The samples for a slice are released as soon as it is measured; all
 *  that survives is three numbers per second, which for six hours is about
 *  260 KB. */
export type SecondFeatures = {
	loudness: Float32Array;
	burst: Float32Array;
	noisiness: Float32Array;
	/** RMS of this slice's final 50 ms frame, to be handed to the next slice.
	 *
	 *  Onsets are defined against the PREVIOUS frame, and the first frame of a
	 *  slice has none — so without carrying this, every slice boundary silently
	 *  loses one possible onset. Measured: that alone moved the excitement
	 *  curve by up to 0.01 against processing the file whole, because `burst`
	 *  is full of ties and losing one shifts a rank. */
	lastFrameRms: number;
};

/** Measure one slice. See `SecondFeatures` for why nothing is normalised. */
export function extractFeatures(
	pcm: Float32Array,
	previousFrameRms = 0,
): SecondFeatures {
	const empty = {
		loudness: new Float32Array(0),
		burst: new Float32Array(0),
		noisiness: new Float32Array(0),
		lastFrameRms: previousFrameRms,
	};
	if (pcm.length < SAMPLE_RATE) return empty;

	const nFrames = Math.floor(pcm.length / FRAME);
	const nSecs = Math.floor(nFrames / FRAMES_PER_SEC);
	if (nSecs === 0) return empty;

	const frameRms = new Float32Array(nFrames);
	const frameZcr = new Float32Array(nFrames);

	for (let f = 0; f < nFrames; f++) {
		const base = f * FRAME;
		let sumSq = 0;
		let crossings = 0;
		let previousNegative = pcm[base] < 0;

		for (let i = 0; i < FRAME; i++) {
			const sample = pcm[base + i];
			sumSq += sample * sample;
			const negative = sample < 0;
			if (i > 0 && negative !== previousNegative) crossings++;
			previousNegative = negative;
		}

		frameRms[f] = Math.sqrt(sumSq / FRAME);
		frameZcr[f] = crossings / (FRAME - 1);
	}

	const loudness = new Float32Array(nSecs);
	const burst = new Float32Array(nSecs);
	const noisiness = new Float32Array(nSecs);

	for (let s = 0; s < nSecs; s++) {
		const base = s * FRAMES_PER_SEC;
		let rmsSum = 0;
		let zcrSum = 0;
		let onsets = 0;

		for (let i = 0; i < FRAMES_PER_SEC; i++) {
			const f = base + i;
			rmsSum += frameRms[f];
			zcrSum += frameZcr[f];
			// Onset: energy jumping >2x over the previous frame. Laughter and
			// applause are onset-dense, which is what separates them from
			// someone simply talking loudly.
			// f === 0 uses the carried frame from the previous slice, so a
			// boundary behaves exactly as mid-file does. 0 for the first slice,
			// where there genuinely is no earlier frame.
			const previous = f > 0 ? frameRms[f - 1] : previousFrameRms;
			if (previous > 0 && frameRms[f] > 2 * Math.max(previous, 1e-6)) onsets++;
		}

		loudness[s] = rmsSum / FRAMES_PER_SEC;
		burst[s] = onsets;
		noisiness[s] = zcrSum / FRAMES_PER_SEC;
	}

	return { loudness, burst, noisiness, lastFrameRms: frameRms[nFrames - 1] };
}

/** Turn every slice's measurements into one excitement curve.
 *
 *  Everything comparative happens here, over the concatenated recording, so
 *  the result is identical to measuring the whole thing at once: the rolling
 *  median spans slice boundaries, the audible floor is the whole recording's
 *  mean, and percentile ranks are global. Doing any of it per slice would
 *  quietly rank a quiet ten minutes against itself and call its loudest
 *  moments notable. */
export function combineFeatures(parts: SecondFeatures[]): Float32Array {
	const total = parts.reduce((n, p) => n + p.loudness.length, 0);
	if (total === 0) return new Float32Array(0);

	const loudness = new Float32Array(total);
	const burst = new Float32Array(total);
	const noisiness = new Float32Array(total);

	let at = 0;
	for (const part of parts) {
		loudness.set(part.loudness, at);
		burst.set(part.burst, at);
		noisiness.set(part.noisiness, at);
		at += part.loudness.length;
	}

	// Spike: this second against the rolling 30 s median, so a quiet streamer
	// and a loud one are measured against themselves rather than each other.
	const median = rollingMedian(loudness, 30);
	const spike = new Float32Array(total);
	for (let s = 0; s < total; s++) {
		spike[s] = Math.min(
			5,
			Math.max(0, loudness[s] / Math.max(median[s], 1e-6)),
		);
	}

	// Only count noisiness where something is actually audible, or silence
	// full of low-level hiss reads as a room full of laughter.
	let loudnessSum = 0;
	for (let s = 0; s < total; s++) loudnessSum += loudness[s];
	const audibleFloor = Math.max((loudnessSum / total) * 0.3, 1e-6);
	for (let s = 0; s < total; s++) {
		if (loudness[s] <= audibleFloor) noisiness[s] = 0;
	}

	return combine([percentile(spike), percentile(burst), percentile(noisiness)]);
}

/** Whole-recording excitement in one call. Kept for short inputs and tests;
 *  the segmented path uses `extractFeatures` + `combineFeatures`. */
export function audioExcitement(pcm: Float32Array): Float32Array {
	return combineFeatures([extractFeatures(pcm)]);
}

/** Standout seconds, as `(second, description)` pairs. */
export function buildEvents(excitement: Float32Array): AudioEvent[] {
	const events: AudioEvent[] = [];
	for (let s = 0; s < excitement.length; s++) {
		if (excitement[s] > EVENT_THRESHOLD) {
			events.push({
				second: s,
				// Wording matches `_build_events` in analysis/fusion.py, so the
				// model sees the phrasing it sees in the desktop app.
				description: "AUDIO spike (shouting/laughter/cheering likely)",
			});
		}
	}

	if (events.length <= MAX_EVENTS) return events;

	// Keep the most spread-out subset rather than the first 120, which on a
	// long stream would all come from the opening minutes.
	const step = events.length / MAX_EVENTS;
	return Array.from(
		{ length: MAX_EVENTS },
		(_, i) => events[Math.floor(i * step)],
	);
}

/** The block that fills `{events}` in the prompt.
 *
 *  Port of `_events_block` in `analysis/highlights.py`, including returning
 *  an empty string when nothing falls inside the window — the prompt reads
 *  better with the section absent than with an empty heading. */
export function eventsBlock(
	events: AudioEvent[],
	start: number,
	end: number,
): string {
	const lines = events
		.filter((e) => e.second >= start && e.second <= end)
		.map((e) => `[${e.second.toFixed(0)}s] ${e.description}`);

	return lines.length
		? `AUDIO/VISUAL EVENTS (from signal analysis):\n${lines.join("\n")}`
		: "";
}

/** Percentile-rank normalisation to 0..1 within this recording.
 *
 *  Port of `_pct`: the double argsort is a rank, not a sort. Ranking rather
 *  than scaling is what makes the threshold meaningful across recordings with
 *  wildly different absolute levels.
 *
 *  ## Ties are averaged, and that is a deliberate divergence
 *
 *  `_pct` uses `argsort().argsort()`, and numpy's default sort is an
 *  UNSTABLE quicksort — so tied values get consecutive ranks in an arbitrary
 *  order. That matters here because `burst` is an integer count where ties
 *  are the norm, not the exception: a whole recording's quiet seconds all
 *  score 0 and then get handed ranks 0, 1, 2, … in whatever order the sort
 *  happened to leave them.
 *
 *  JavaScript's sort is stable, so copying the shape of the Python literally
 *  still would not reproduce its numbers. Giving every tied value the mean of
 *  the ranks they collectively occupy is the standard definition of a
 *  percentile rank, is deterministic, and sits at the centre of the range
 *  numpy's arbitrary ordering picks from — so it is both more defensible and
 *  closer on average than imitating an unstable sort could be. */
function percentile(x: Float32Array): Float32Array {
	if (x.length === 0) return x;

	const order = Array.from({ length: x.length }, (_, i) => i).sort(
		(a, b) => x[a] - x[b],
	);
	const ranks = new Float32Array(x.length);
	const denominator = Math.max(x.length - 1, 1);

	let i = 0;
	while (i < order.length) {
		let j = i;
		while (j + 1 < order.length && x[order[j + 1]] === x[order[i]]) j++;

		const shared = (i + j) / 2 / denominator;
		for (let k = i; k <= j; k++) ranks[order[k]] = shared;
		i = j + 1;
	}
	return ranks;
}

/** Mean across channels, truncated to the shortest. Port of `_combine`. */
function combine(channels: Float32Array[]): Float32Array {
	const present = channels.filter((c) => c.length);
	if (!present.length) return new Float32Array(0);

	const n = Math.min(...present.map((c) => c.length));
	const out = new Float32Array(n);
	for (let i = 0; i < n; i++) {
		let sum = 0;
		for (const channel of present) sum += channel[i];
		out[i] = sum / present.length;
	}
	return out;
}

/** Median of a centred window at each position; edges shrink. Port of
 *  `_rolling_median`. */
function rollingMedian(x: Float32Array, window: number): Float32Array {
	const half = Math.floor(window / 2);
	const out = new Float32Array(x.length);
	const scratch: number[] = [];

	for (let i = 0; i < x.length; i++) {
		const lo = Math.max(0, i - half);
		const hi = Math.min(x.length, i + half + 1);

		scratch.length = 0;
		for (let j = lo; j < hi; j++) scratch.push(x[j]);
		scratch.sort((a, b) => a - b);

		const mid = scratch.length >> 1;
		out[i] =
			scratch.length % 2 === 0
				? (scratch[mid - 1] + scratch[mid]) / 2
				: scratch[mid];
	}
	return out;
}
