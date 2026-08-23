/** Weighted multimodal scoring, ported from `analysis/fusion.py`.
 *
 *  ## Why this is here and not left to the prompt
 *
 *  The obvious way to feed audio into the scoring is the `{events}` block in
 *  `score_clips.txt`. Measured against the desktop pipeline on a real
 *  38-minute VOD, that block contained **one event** — the `_build_events`
 *  threshold of 0.92 over three averaged percentile channels almost never
 *  fires. So the prompt hint is close to decorative, and the audio channel
 *  earns its keep somewhere else entirely: here, as a weighted term worth
 *  0.20 of the final score.
 *
 *  Porting this is what makes the audio analysis change any picks at all.
 *
 *  ## Missing channels are neutral, not zero
 *
 *  `_fuse` reads its inputs with `s.get("visual", 50)` — a channel that was
 *  never measured contributes a neutral 50, not a penalty. A browser has no
 *  visual channel (needs video decode) and no reaction channel (needs
 *  active-speaker detection), so both fall back to exactly that default. The
 *  arithmetic is unchanged from the desktop app; two of its five inputs are
 *  simply constant.
 *
 *  A constant term cannot reorder anything, so in practice ranking here is
 *  driven by text (0.30), audio (0.20) and engagement (0.10) — 0.60 of the
 *  real signal rather than the 0.30 a text-only pass would use.
 */

import type { Segment } from "./openrouter";
import type { Clip } from "./score";

/** From `config/settings.yaml` scoring.weights. Must sum to 1.0. */
export const WEIGHTS = {
	text: 0.3,
	visual: 0.2,
	reaction: 0.2,
	audio: 0.2,
	engagement: 0.1,
} as const;

/** Score bonus for trending/drama moments, from `analysis/fusion.py`. */
const TRENDING_BONUS = 10;

/** What a channel scores when it was never measured. Matches the defaults
 *  baked into `_fuse`'s `.get()` calls. */
const NEUTRAL_SUBSCORE = 50;
const NEUTRAL_REACTION = 0.5;

/** Mean of a per-second signal across a clip's window. Port of
 *  `_window_mean`, including its 0.5 fallback when the clip falls outside the
 *  analysed range. */
export function windowMean(
	signal: Float32Array,
	start: number,
	end: number,
): number {
	const lo = Math.floor(start);
	const hi = Math.min(Math.floor(end) + 1, signal.length);
	if (lo >= signal.length || hi <= lo) return 0.5;

	let sum = 0;
	for (let i = lo; i < hi; i++) sum += signal[i];
	return sum / (hi - lo);
}

/** 0 = silent, 1 = steady talking (~2 words/sec). Port of `_speech_ratio`. */
export function speechRatio(clip: Clip, segments: Segment[]): number {
	let words = 0;
	for (const s of segments) {
		if (s.end > clip.start && s.start < clip.end) {
			words += s.text.split(/\s+/).filter(Boolean).length;
		}
	}
	const duration = Math.max(clip.end - clip.start, 1);
	return Math.min(1, words / duration / 2);
}

/** Weighted multimodal score, 0..1. Port of `_fuse`.
 *
 *  The adaptive part is worth keeping even with two channels constant: for a
 *  low-speech clip the text and engagement weights are shifted onto visual
 *  and reaction, so a moment with little dialogue is not punished for the
 *  absence of words. Deliberately NOT shifted onto audio — for a quiet clip,
 *  low audio excitement is just silence, and weighting it up would bury
 *  exactly the content the shift exists to surface. */
export function fuse(
	clip: Clip,
	audioExcitementMean: number,
	speech: number,
): number {
	const talky = Math.max(0, Math.min(1, speech));

	const wText = WEIGHTS.text * talky;
	const wEngagement = WEIGHTS.engagement * talky;
	const freed = WEIGHTS.text - wText + (WEIGHTS.engagement - wEngagement);
	const carriers = WEIGHTS.visual + WEIGHTS.reaction;
	const boost = 1 + (carriers > 0 ? freed / carriers : 0);

	return (
		(wText * clip.score) / 100 +
		(WEIGHTS.visual * boost * NEUTRAL_SUBSCORE) / 100 +
		WEIGHTS.reaction * boost * NEUTRAL_REACTION +
		(WEIGHTS.audio * (audioExcitementMean * 100)) / 100 +
		(wEngagement * (clip.engagement ?? NEUTRAL_SUBSCORE)) / 100
	);
}

/** Rescore every candidate against the audio signal.
 *
 *  Returns new objects rather than mutating, so the LLM's own text score
 *  stays available for display — showing someone a "score" that is really a
 *  fused number while calling it the model's judgement would be a small lie
 *  in the UI. */
export function rescore(
	clips: Clip[],
	segments: Segment[],
	excitement: Float32Array,
): Clip[] {
	if (!excitement.length) return clips;

	return clips.map((clip) => {
		const audio = windowMean(excitement, clip.start, clip.end);
		let fused = Math.round(
			100 * fuse(clip, audio, speechRatio(clip, segments)),
		);

		// Trending/drama moments ride attention that already exists.
		if (clip.trending) fused = Math.min(100, fused + TRENDING_BONUS);

		return { ...clip, score: Math.max(0, Math.min(100, fused)) };
	});
}
