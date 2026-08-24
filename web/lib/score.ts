/** Finding the moments worth clipping — the same way the desktop app does.
 *
 *  This is a port of `analysis/highlights.py`, not a fresh take on the same
 *  idea. That matters: this page exists to show what Clips Kitty's judgement
 *  is like, so if it scored differently it would be advertising a product
 *  nobody can download. The prompt below is copied byte-for-byte from
 *  `config/prompts/score_clips.txt`, and the parser mirrors
 *  `_parse_clips_json` including which malformed cases it forgives.
 *
 *  KEEP IN SYNC. If the prompt changes in the main repo, change it here too.
 *  It is duplicated rather than fetched because this page is a static bundle
 *  with no server to read the repo from.
 *
 *  ## This is ONE STAGE of the desktop pipeline, not all of it
 *
 *  Worth being precise about, because it sets what this page can honestly
 *  claim. The desktop app's entry point is `analysis/fusion.py`, which calls
 *  `find_highlights()` — the part ported here — and then does considerably
 *  more with the result:
 *
 *  * a second LLM pass over signal-peak windows (`score_windows.txt`)
 *  * fused scoring across five weighted signals, of which this text pass is
 *    weighted **0.30**; the others are visual, reaction, audio and engagement
 *  * trending, action and creator-context bonuses
 *  * a rerank pass over the finalists (`rerank.txt`)
 *
 *  And outside fusion: creator intelligence (catchphrases, storylines,
 *  learned preferences) and Twitch chat-replay hype curves from
 *  `analysis/hype.py`.
 *
 *  What IS here: the text pass, the **audio channel** (`audioEvents.ts` — a
 *  port of `analysis/audio_features.py`, free because the PCM is already
 *  decoded for transcription), and the **weighted fusion** that makes it
 *  count (`fuse.ts`), including the trending bonus.
 *
 *  What is NOT here: the visual and reaction channels, which need video
 *  decode and an active-speaker model; the signal-peak window pass; the
 *  rerank pass; and any memory of the creator. So picks still differ from the
 *  desktop app's, most visibly on gameplay, where on-screen action carries
 *  weight that no amount of listening recovers.
 *
 *  One further difference, this one about cost rather than capability: there
 *  is **no separate metadata pass**. The desktop app makes extra calls for
 *  titles and descriptions; here the `hook` scoring already returns serves as
 *  the title, which costs nothing extra — worth it when a free OpenRouter
 *  account gets 50 requests a day.
 */

import { type AudioEvent, buildEvents, eventsBlock } from "./audioEvents";
import { rescore } from "./fuse";
import type { Segment } from "./openrouter";
import { generate } from "./openrouter";

/** Copied verbatim from config/prompts/score_clips.txt. */
const SCORE_PROMPT = `You are an expert short-form video editor. You find the moments in long videos that perform best as viral vertical Shorts/Reels/TikToks.

Below is a transcript with timestamps in seconds, formatted as:
[start - end] spoken text

You may also see an AUDIO/VISUAL EVENTS list from automated signal analysis (loud reactions, laughter, scene cuts, high motion). A mild-sounding transcript line that coincides with a big audio spike is often a GREAT clip — weigh the events seriously.

Find the best self-contained clip moments. Judge each candidate against this virality framework:
- Hook moments and strong opening lines
- Emotional peaks (laughter, anger, excitement, shock)
- Opinion-driven statements ("opinion bombs")
- Revelations or disclosures
- Conflict or tension
- Quotable lines
- Story-structure peaks (setup that pays off)
- Practical or actionable value
- TRENDING & DRAMA (weight this heavily): the speaker names or reacts to another creator, streamer, celebrity, influencer, or public figure; discusses beef, callouts, controversy, or drama; reacts to viral/trending content, current events, or something everyone is talking about. These moments ride existing attention and perform extremely well — score them high and set "trending": true.

Rules:
- Each clip must be {min_duration} to {max_duration} seconds long. Let the moment decide its own length within that range — a punchy one-liner might be 12 seconds, a story that builds to a payoff might be 45. Do NOT default to the shortest length; capture the whole self-contained moment including its setup and payoff.
- Each clip must make sense on its own with no extra context.
- start and end must be numbers in seconds taken from the transcript timestamps.
- score is 0-100 predicted viral potential. Be critical: most moments score below 50. Only truly strong moments score above 80.
- engagement is 0-100 for hook strength + payoff + quotability specifically: would someone stop scrolling in the first 2 seconds, and is there a satisfying payoff?
- trending is true if this moment names/reacts to another creator, celebrity, or public figure, or discusses drama/beef/controversy/trending topics; otherwise false. When true, boost the score.
- Return at most 5 clips. If nothing is good, return an empty list.

Respond with ONLY valid JSON in exactly this shape, no other text:
{"clips": [{"start": 124.5, "end": 162.0, "score": 87, "engagement": 80, "trending": true, "hook": "...", "reason": "..."}]}

Field meanings:
- "hook": an attention-grabbing one-liner about THIS moment. It MUST be built from words actually spoken in the clip — quote or closely paraphrase the transcript lines between your start and end timestamps. Do not invent events that are not in the transcript. Do not write a generic description.
- "reason": one sentence on why this specific moment will perform, referencing what is actually said in it.

{events}

TRANSCRIPT:
{transcript}
`;

// Matches config/settings.yaml clips.min_duration / max_duration.
const MIN_DURATION = 10;
const MAX_DURATION = 60;

// Matches config/settings.yaml analysis.chunk_seconds / long_video_threshold_seconds.
const CHUNK_SECONDS = 300;
const CHUNK_OVERLAP_SECONDS = 60;
const LONG_VIDEO_THRESHOLD = 420;

/** Rejection threshold from `analysis/highlights.py`: a candidate overlapping
 *  a kept clip by more than this fraction of the shorter clip is the same
 *  moment found twice, which is exactly what chunk overlap produces. */
const MAX_OVERLAP = 0.4;

/** How many scoring calls a recording of this length will cost.
 *
 *  Mirrors `chunkSegments` exactly, including that short recordings go in
 *  whole. Used to warn about OpenRouter's daily allowance BEFORE a run starts:
 *  a free account gets 50 requests a day, and a three-hour VOD needs more than
 *  that, which is much better learned up front than as a 429 halfway through a
 *  run the visitor has already partly paid for. */
export function estimateScoringRequests(durationSeconds: number): number {
	if (durationSeconds <= LONG_VIDEO_THRESHOLD) return 1;
	return Math.ceil(durationSeconds / (CHUNK_SECONDS - CHUNK_OVERLAP_SECONDS));
}

export type Clip = {
	start: number;
	end: number;
	score: number;
	engagement: number | null;
	trending: boolean;
	hook: string;
	reason: string;
};

/** Split a long transcript so each call fits comfortably in context.
 *
 *  Same shape as `_chunk_segments`: short videos go in whole, long ones are
 *  cut with an overlap so a moment straddling a boundary is still seen intact
 *  by one of the two chunks. The duplicates that overlap creates are removed
 *  afterwards by `dedupe`. */
export function chunkSegments(segments: Segment[]): Segment[][] {
	if (!segments.length) return [];

	const total = segments[segments.length - 1].end;
	if (total <= LONG_VIDEO_THRESHOLD) return [segments];

	const chunks: Segment[][] = [];
	for (
		let start = 0;
		start < total;
		start += CHUNK_SECONDS - CHUNK_OVERLAP_SECONDS
	) {
		const end = start + CHUNK_SECONDS;
		const chunk = segments.filter((s) => s.end > start && s.start < end);
		if (chunk.length) chunks.push(chunk);
	}
	return chunks;
}

function buildPrompt(chunk: Segment[], events: AudioEvent[]): string {
	const transcript = chunk
		.map((s) => `[${s.start.toFixed(1)} - ${s.end.toFixed(1)}] ${s.text}`)
		.join("\n");

	return (
		SCORE_PROMPT.replace("{min_duration}", String(MIN_DURATION))
			.replace("{max_duration}", String(MAX_DURATION))
			// Only the events inside this chunk's window, exactly as
			// `find_highlights` does it — an event list spanning the whole
			// recording would point the model at moments it cannot see.
			.replace(
				"{events}",
				eventsBlock(events, chunk[0].start, chunk[chunk.length - 1].end),
			)
			.replace("{transcript}", transcript)
	);
}

/** Tolerant parse. Port of `_parse_clips_json`.
 *
 *  Returns null when nothing usable came back — which is the signal to retry —
 *  and an empty array when the model validly found nothing worth clipping.
 *  Collapsing those two into one value is what makes a retry loop either
 *  never fire or never stop. */
export function parseClips(raw: string): Clip[] | null {
	const text = raw
		.trim()
		.replace(/^```(?:json)?\s*/, "")
		.replace(/\s*```$/, "");

	const start = text.indexOf("{");
	const end = text.lastIndexOf("}");
	if (start === -1 || end <= start) return null;

	let data: unknown;
	try {
		data = JSON.parse(text.slice(start, end + 1));
	} catch {
		return null;
	}

	const raw_clips = (data as { clips?: unknown })?.clips;
	if (!Array.isArray(raw_clips)) return null;

	const clips: Clip[] = [];
	for (const item of raw_clips) {
		const c = item as Record<string, unknown>;
		const startS = Number(c.start);
		const endS = Number(c.end);
		const score = Number(c.score);

		// Drop malformed entries and keep the rest, rather than losing a whole
		// chunk's worth of good clips to one bad object.
		if (!Number.isFinite(startS) || !Number.isFinite(endS)) continue;
		if (!Number.isFinite(score)) continue;
		if (startS < 0 || endS <= startS) continue;

		const engagement = Number(c.engagement);
		clips.push({
			start: startS,
			end: endS,
			score: Math.max(0, Math.min(100, Math.round(score))),
			engagement: Number.isFinite(engagement)
				? Math.max(0, Math.min(100, Math.round(engagement)))
				: null,
			trending: Boolean(c.trending),
			hook: String(c.hook ?? ""),
			reason: String(c.reason ?? ""),
		});
	}
	return clips;
}

/** One scoring call, with the single retry the desktop app also does.
 *
 *  Cloud models wrap JSON in prose far less often than local ones, but "far
 *  less often" over 24 chunks is still a lost chunk per run. */
async function scoreChunk(
	key: string,
	model: string,
	chunk: Segment[],
	events: AudioEvent[],
): Promise<Clip[]> {
	const prompt = buildPrompt(chunk, events);

	const first = await generate(key, model, prompt, { jsonMode: true });
	const parsed = parseClips(first);
	if (parsed !== null) return parsed;

	const retry = await generate(
		key,
		model,
		`${prompt}\n\nIMPORTANT: Respond with ONLY the JSON object. No markdown, no explanation.`,
		{ jsonMode: true },
	);
	return parseClips(retry) ?? [];
}

function overlapRatio(a: Clip, b: Clip): number {
	const overlap = Math.min(a.end, b.end) - Math.max(a.start, b.start);
	if (overlap <= 0) return 0;
	return overlap / Math.min(a.end - a.start, b.end - b.start);
}

/** Best-first, dropping anything that is mostly a clip we already kept. */
export function dedupe(clips: Clip[]): Clip[] {
	const kept: Clip[] = [];
	for (const c of [...clips].sort((x, y) => y.score - x.score)) {
		if (!kept.some((k) => overlapRatio(c, k) > MAX_OVERLAP)) kept.push(c);
	}
	return kept.sort((a, b) => a.start - b.start);
}

/** Score a whole transcript.
 *
 *  Sequential rather than parallel on purpose: a free OpenRouter account is
 *  capped at 20 requests/minute, and firing 24 chunks at once turns a working
 *  run into a wall of 429s. */
export async function findClips(
	key: string,
	model: string,
	segments: Segment[],
	excitement: Float32Array,
	onProgress?: (done: number, total: number) => void,
): Promise<Clip[]> {
	const chunks = chunkSegments(segments);
	const events = buildEvents(excitement);
	const found: Clip[] = [];

	for (let i = 0; i < chunks.length; i++) {
		found.push(...(await scoreChunk(key, model, chunks[i], events)));
		onProgress?.(i + 1, chunks.length);
	}

	const videoEnd = segments.length ? segments[segments.length - 1].end : 0;
	const sane = found.filter(
		(c) =>
			c.end <= videoEnd + 5 &&
			c.end - c.start >= MIN_DURATION - 2 &&
			c.end - c.start <= MAX_DURATION + 5,
	);

	// Order matches the desktop pipeline: `find_highlights` dedupes on the
	// model's own text score first, and only then does `fusion.py` rescore the
	// survivors against the other channels.
	return rescore(dedupe(sane), segments, excitement).sort(
		(a, b) => a.start - b.start,
	);
}
