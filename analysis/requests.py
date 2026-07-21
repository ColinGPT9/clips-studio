"""Natural-language clip requests — find the moments a creator asked for.

An OPTIONAL, additive input to clip discovery. The user describes moments in
plain language ("the part where they announce the update", "the funniest
reaction"); this proposes matching time windows that join the SAME candidate
pool the automatic system builds, get scored by the SAME multimodal fusion,
and receive a small capped relevance bonus — exactly like creator context or
audience hype already do.

It can only ADD candidates or promote them. There is no path here that
removes, downranks, or alters an automatically-found clip.

Modular by design: fusion calls match() and bonus() the way it already calls
creator.retrieval.context_bonus(). Nothing in the scoring math imports this.
When no request is given, none of it runs and the pipeline is byte-for-byte
unchanged. So future features can add their own instruction sources the same
way, without touching the scorer.

Matching is SEMANTIC, not string search: the LLM already in the pipeline is
shown the transcript (with timestamps) and the multimodal event timeline
(audio bursts, scene cuts, motion) and asked which ranges match the MEANING
of the request — so "when they get surprised" resolves via context and an
audio spike even when nobody says the word "surprised".
"""

import json
import re
from dataclasses import dataclass

from core.models import Segment
from llm.base import LLMBackend

# Capped additive bonus for a candidate that matches a request, scaled by the
# model's confidence. Same order as action/audience/context bonuses, so a
# request can promote a real match without letting a weak clip jump the queue.
REQUEST_BONUS_MAX = 12

_PROMPT = """You are helping a video editor find SPECIFIC moments they asked for.

Here is a transcript with timestamps (seconds):
{transcript}
{events}
The editor asked for these moments:
{requests}

For EACH request, find every time range that matches its MEANING — not just
the exact words. A request like "when they get surprised" matches a shocked
reaction, a sudden change in tone, or an audio spike, even if the word
"surprised" is never said. Use the surrounding context. It is fine for a
request to have several matches, or none.

Respond with ONLY this JSON, no prose:
{
  "matches": [
    {"request": <0-based request number>, "start": <seconds>, "end": <seconds>,
     "confidence": <0.0-1.0>, "why": "<short reason>"}
  ]
}
Give confidence honestly: 0.9 for an unmistakable match, 0.4 for a plausible
one, and simply omit anything you are not really seeing."""


@dataclass
class RequestMatch:
    request_index: int
    request: str
    start: float
    end: float
    confidence: float   # 0..1, the model's own honesty about the match
    why: str = ""


def normalize(requests: list[str] | None) -> list[str]:
    """Clean the raw text box into distinct request strings.

    Splits on newlines, drops blanks, and strips leading list markers
    ("1.", "-", "•") so a numbered list pasted in one box becomes separate
    requests."""
    out: list[str] = []
    for raw in requests or []:
        for line in str(raw).splitlines():
            line = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", line).strip()
            if len(line) >= 3:
                out.append(line)
    return out


def bonus(confidence: float) -> int:
    """The additive score bump for a matched candidate, 0..REQUEST_BONUS_MAX."""
    return round(REQUEST_BONUS_MAX * max(0.0, min(1.0, confidence)))


def match(
    segments: list[Segment],
    llm: LLMBackend,
    requests: list[str],
    events: list[tuple[float, str]] | None,
    min_duration: float,
    max_duration: float,
) -> tuple[list[RequestMatch], set[int]]:
    """Time windows matching each request, plus the set of request indices
    that found NOTHING (so the caller can tell the user).

    Never raises: a model or parse failure yields no matches, and the normal
    automatic pipeline carries on unaffected."""
    reqs = normalize(requests)
    if not segments or not reqs:
        return [], set()

    # Reuse the analyser's own chunking so long videos are handled the same
    # way the automatic pass handles them.
    from analysis.highlights import _chunk_segments

    chunks = _chunk_segments(segments, 1200.0, 60.0, 1800.0)
    req_block = "\n".join(f"{i}. {r}" for i, r in enumerate(reqs))
    video_end = segments[-1].end

    matches: list[RequestMatch] = []
    for chunk in chunks:
        transcript = "\n".join(f"[{s.start:.1f} - {s.end:.1f}] {s.text}" for s in chunk)
        prompt = (
            _PROMPT.replace("{transcript}", transcript)
            .replace("{events}", _events_block(events, chunk[0].start, chunk[-1].end))
            .replace("{requests}", req_block)
        )
        try:
            raw = llm.generate(prompt, json_mode=True)
            found = _parse(raw)
        except Exception as e:
            print(f"      (request matching failed on a chunk: {e})")
            found = []
        lo, hi = chunk[0].start, chunk[-1].end
        for m in found:
            if not (0 <= m["request"] < len(reqs)):
                continue
            start = max(lo, min(float(m["start"]), video_end))
            end = max(start, min(float(m["end"]), video_end))
            # Guard the window into a sane clip length; fusion re-fits it to
            # sentence boundaries afterwards, same as a signal peak.
            if end - start < min_duration:
                end = min(video_end, start + min_duration)
            if end - start > max_duration:
                end = start + max_duration
            if end <= start:
                continue
            matches.append(RequestMatch(
                request_index=m["request"], request=reqs[m["request"]],
                start=round(start, 2), end=round(end, 2),
                confidence=max(0.0, min(1.0, float(m.get("confidence", 0.5)))),
                why=str(m.get("why", ""))[:200],
            ))

    matched_idx = {m.request_index for m in matches}
    unmatched = {i for i in range(len(reqs)) if i not in matched_idx}
    if matches:
        print(f"      {len(matches)} window(s) matched {len(matched_idx)} request(s)")
    for i in sorted(unmatched):
        print(f"      No strong match for request: {reqs[i]!r}")
    return matches, unmatched


def _events_block(events, start: float, end: float) -> str:
    if not events:
        return ""
    lines = [f"[{sec:.0f}s] {desc}" for sec, desc in events if start <= sec <= end]
    if not lines:
        return "\n"
    return "\nAUDIO/VISUAL EVENTS (from signal analysis):\n" + "\n".join(lines) + "\n"


def _parse(raw: str) -> list[dict]:
    """Pull the matches array out of the model's reply, tolerating fences and
    surrounding prose."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    out = []
    for m in data.get("matches", []) if isinstance(data, dict) else []:
        if isinstance(m, dict) and "request" in m and "start" in m and "end" in m:
            out.append(m)
    return out
