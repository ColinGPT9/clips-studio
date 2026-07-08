"""Knowledge extraction: transcript -> structured creator facts.

Runs AFTER analysis (overlapping the render stage, when Ollama is idle) and
writes to creator_knowledge / creator_events. This is deliberately paranoid
about the LLM's output: local models produce malformed JSON and confident
nonsense, so anything that fails validation is silently dropped — a smaller,
cleaner knowledge base beats a big noisy one. Extraction NEVER affects the
current video's clips; it only informs future videos.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from core.models import Segment
from core.state import StateDB, _now
from creator.models import (
    EVENT_STATUSES,
    KNOWLEDGE_TYPES,
    MAX_KNOWLEDGE_PER_CREATOR,
    STALE_EVENT_DAYS,
)
from llm.base import LLMBackend

_PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "extract_knowledge.txt"

# Bound the LLM work per video: sample chunks evenly across the stream.
CHUNK_SECONDS = 300
MAX_CHUNKS = 10


def extract_and_store(
    db: StateDB,
    creator_id: int,
    video_id: str,
    segments: list[Segment],
    llm: LLMBackend,
) -> int:
    """Extract facts/events from this video's transcript and store them.
    Returns how many NEW items were stored. Any error is the caller's to
    swallow — this must never break a pipeline run."""
    enabled = db.conn.execute(
        "SELECT learning_enabled FROM creators WHERE creator_id = ?", (creator_id,)
    ).fetchone()
    if not enabled or not enabled["learning_enabled"]:
        return 0

    prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")
    stored = 0
    for chunk_text in _sample_chunks(segments):
        raw = llm.generate(prompt_template.replace("{transcript}", chunk_text), json_mode=True)
        data = _parse(raw)
        if data is None:
            continue
        stored += _store_facts(db, creator_id, video_id, data.get("facts") or [])
        stored += _store_events(db, creator_id, video_id, data.get("events") or [])

    _mark_stale_events(db, creator_id)
    _prune(db, creator_id)
    return stored


def _sample_chunks(segments: list[Segment]) -> list[str]:
    """~5-minute transcript chunks, sampled evenly across the whole video so
    a 3-hour stream contributes its middle and end, not just its intro."""
    if not segments:
        return []
    chunks: list[str] = []
    current: list[str] = []
    chunk_start = segments[0].start
    for seg in segments:
        if seg.end - chunk_start > CHUNK_SECONDS and current:
            chunks.append(" ".join(current))
            current, chunk_start = [], seg.start
        current.append(seg.text)
    if current:
        chunks.append(" ".join(current))
    if len(chunks) <= MAX_CHUNKS:
        return chunks
    step = len(chunks) / MAX_CHUNKS
    return [chunks[int(i * step)] for i in range(MAX_CHUNKS)]


def _parse(raw: str) -> dict | None:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _store_facts(db: StateDB, creator_id: int, video_id: str, facts: list) -> int:
    existing = {
        _norm(r["information"])
        for r in db.conn.execute(
            "SELECT information FROM creator_knowledge WHERE creator_id = ?", (creator_id,)
        )
    }
    stored = 0
    for f in facts:
        if not isinstance(f, dict):
            continue
        ktype = _norm(str(f.get("type", "")))
        info = str(f.get("information", "")).strip()
        conf = _norm(str(f.get("confidence", "")))
        # Hard validation: whitelisted type, meaningful length, confident.
        if ktype not in KNOWLEDGE_TYPES or conf not in ("high", "medium"):
            continue
        if not (3 <= len(info) <= 200):
            continue
        key = _norm(info)
        # Dedupe: exact or containment against what we already know.
        if key in existing or any(key in e or e in key for e in existing):
            continue
        db.conn.execute(
            "INSERT INTO creator_knowledge (creator_id, knowledge_type, information,"
            " confidence, source_video, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (creator_id, ktype, info, conf, video_id, _now()),
        )
        existing.add(key)
        stored += 1
    db.conn.commit()
    return stored


def _store_events(db: StateDB, creator_id: int, video_id: str, events: list) -> int:
    rows = db.conn.execute(
        "SELECT event_id, event_name, status FROM creator_events WHERE creator_id = ?",
        (creator_id,),
    ).fetchall()
    stored = 0
    for e in events:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name", "")).strip()
        desc = str(e.get("description", "")).strip()[:300]
        status = _norm(str(e.get("status", "announced")))
        if status not in EVENT_STATUSES or not (3 <= len(name) <= 120):
            continue
        # Continuation: same event mentioned again (word overlap) updates the
        # existing row instead of duplicating — "speedrun attempt announced"
        # in week 1, "did the speedrun" in week 3 is ONE event completing.
        match = _match_event(name, rows)
        if match is not None:
            if status != match["status"]:
                db.conn.execute(
                    "UPDATE creator_events SET status = ?, completed_date = ? WHERE event_id = ?",
                    (status, _now() if status == "completed" else None, match["event_id"]),
                )
            continue
        db.conn.execute(
            "INSERT INTO creator_events (creator_id, event_name, description, status,"
            " detected_date, completed_date, source_video) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (creator_id, name, desc, status,
             _now(), _now() if status == "completed" else None, video_id),
        )
        stored += 1
    db.conn.commit()
    return stored


def _match_event(name: str, rows) -> dict | None:
    words = set(_norm(name).split())
    best, best_overlap = None, 0.0
    for r in rows:
        rw = set(_norm(r["event_name"]).split())
        if not rw or not words:
            continue
        overlap = len(words & rw) / min(len(words), len(rw))
        if overlap > best_overlap:
            best, best_overlap = r, overlap
    return dict(best) if best is not None and best_overlap >= 0.6 else None


def _mark_stale_events(db: StateDB, creator_id: int) -> None:
    """Open events not touched in ~2 months stop being 'upcoming' forever."""
    cutoff = (datetime.now() - timedelta(days=STALE_EVENT_DAYS)).isoformat(timespec="seconds")
    db.conn.execute(
        "UPDATE creator_events SET status = 'stale' WHERE creator_id = ?"
        " AND status IN ('announced', 'in_progress') AND detected_date < ?",
        (creator_id, cutoff),
    )
    db.conn.commit()


def _prune(db: StateDB, creator_id: int) -> None:
    """Keep the knowledge base bounded: drop lowest-confidence, least
    recently used items beyond the cap."""
    db.conn.execute(
        "DELETE FROM creator_knowledge WHERE knowledge_id IN ("
        "  SELECT knowledge_id FROM creator_knowledge WHERE creator_id = ?"
        "  ORDER BY CASE confidence WHEN 'high' THEN 0 ELSE 1 END,"
        "           COALESCE(last_used, created_at) DESC"
        "  LIMIT -1 OFFSET ?"
        ")",
        (creator_id, MAX_KNOWLEDGE_PER_CREATOR),
    )
    db.conn.commit()
