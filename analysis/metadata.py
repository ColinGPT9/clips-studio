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


BATCH_PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "metadata_batch.txt"


def generate_metadata_batch(
    candidates: list[ClipCandidate],
    segments: list[Segment],
    video_title: str,
    llm: LLMBackend,
    batch_size: int = 8,
    creator_context: str = "",
) -> list[ClipMetadata]:
    """Metadata for ALL clips in a few LLM calls instead of one per clip —
    on a long stream this cuts dozens of model calls from the analysis time.
    Any clip the model skips or mangles falls back to hook-based metadata.
    creator_context (optional): learned facts about the creator — series
    names, running jokes, collaborators — for more accurate titles/hashtags."""
    results: list[ClipMetadata] = [_fallback(c, video_title) for c in candidates]
    template = BATCH_PROMPT_PATH.read_text(encoding="utf-8")
    if creator_context:
        template = template.replace(
            "{clips}",
            "CREATOR CONTEXT (background knowledge — use for accuracy when relevant,"
            " never invent beyond it):\n" + creator_context + "\n\n{clips}",
        )

    for base in range(0, len(candidates), batch_size):
        batch = candidates[base : base + batch_size]
        blocks = []
        for i, c in enumerate(batch):
            text = " ".join(
                s.text for s in segments if s.end > c.start and s.start < c.end
            )[:900]
            blocks.append(f"CLIP {i}:\n{text or '(no speech)'}")
        prompt = (
            template.replace("{video_title}", video_title)
            .replace("{count}", str(len(batch)))
            .replace("{clips}", "\n\n".join(blocks))
        )
        try:
            data = _parse(llm.generate(prompt, json_mode=True))
        except Exception:
            data = None
        if not data or not isinstance(data.get("items"), list):
            continue  # whole batch falls back
        for item in data["items"]:
            try:
                idx = int(item.get("index", -1))
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(batch):
                continue
            fallback = results[base + idx]
            results[base + idx] = ClipMetadata(
                title=_clean_title(item.get("title", "")) or fallback.title,
                description=str(item.get("description", "")).strip() or fallback.description,
                hashtags=_clean_hashtags(item.get("hashtags", [])) or fallback.hashtags,
            )
    return results


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
