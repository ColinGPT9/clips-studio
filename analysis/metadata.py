"""LLM-generated upload metadata: title, description, hashtags.

Never fails the pipeline: any LLM misbehavior falls back to metadata
derived from the clip's hook and the source video title.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.models import ClipCandidate, Segment
from llm.base import LLMBackend

PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "metadata.txt"

MAX_TITLE_LEN = 95  # leave headroom under YouTube's 100-char limit


@dataclass
class ClipMetadata:
    title: str
    description: str
    hashtags: list[str] = field(default_factory=list)


def generate_metadata(
    candidate: ClipCandidate,
    segments: list[Segment],
    video_title: str,
    llm: LLMBackend,
) -> ClipMetadata:
    clip_text = " ".join(
        s.text for s in segments if s.end > candidate.start and s.start < candidate.end
    )
    fallback = _fallback(candidate, video_title)
    if not clip_text:
        return fallback

    prompt = (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{video_title}", video_title)
        .replace("{clip_text}", clip_text)
    )
    try:
        raw = llm.generate(prompt, json_mode=True)
        parsed = _parse(raw)
    except Exception:
        parsed = None
    if parsed is None:
        return fallback

    title = _clean_title(parsed.get("title", "")) or fallback.title
    description = str(parsed.get("description", "")).strip() or fallback.description
    hashtags = _clean_hashtags(parsed.get("hashtags", [])) or fallback.hashtags
    return ClipMetadata(title=title, description=description, hashtags=hashtags)


def _fallback(candidate: ClipCandidate, video_title: str) -> ClipMetadata:
    title = _clean_title(candidate.hook) or _clean_title(video_title) or "Clip"
    return ClipMetadata(
        title=title,
        description=f"Clip from: {video_title}",
        hashtags=["#clips"],
    )


def _parse(raw: str) -> dict | None:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_title(title: str) -> str:
    title = re.sub(r"[<>]", "", str(title)).strip().strip('"')
    return title[:MAX_TITLE_LEN].strip()


def _clean_hashtags(tags) -> list[str]:
    if not isinstance(tags, list):
        return []
    cleaned = []
    for tag in tags[:5]:
        tag = re.sub(r"[^\w#]", "", str(tag).strip().lower())
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = f"#{tag}"
        if len(tag) > 1 and tag != "#shorts":
            cleaned.append(tag)
    return cleaned
