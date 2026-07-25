"""Knowledge extraction: transcript -> structured creator facts.

Runs AFTER analysis (overlapping the render stage, when Ollama is idle) and
writes to creator_knowledge / creator_events. This is deliberately paranoid
about the LLM's output: local models produce malformed JSON and confident
nonsense, so anything that fails validation is silently dropped — a smaller,
cleaner knowledge base beats a big noisy one. Extraction NEVER affects the
current video's clips; it only informs future videos.

Repetition is checked here, not taken on the model's word: a "catchphrase" is
counted in the actual transcript, and re-hearing a known fact reinforces it
instead of being discarded as a duplicate. That count is what promotes a
phrase out of candidacy and what keeps a fact from going dormant.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from core.models import Segment
from core.state import StateDB, _now
from creator.models import (
    EVENT_STATUSES,
    FORGET_DAYS,
    FORGET_VIDEOS,
    KNOWLEDGE_TYPES,
    MAX_KNOWLEDGE_PER_CREATOR,
    PHRASE_TYPES,
    STALE_EVENT_DAYS,
)
from creator.retrieval import count_phrase, dormant_before, is_phrase_like, phrase_of
from llm.base import LLMBackend

_PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "extract_knowledge.txt"
_NOTES_PROMPT_PATH = (
    Path(__file__).parent.parent / "config" / "prompts" / "extract_knowledge_notes.txt"
)

# Bound the LLM work per video: content-sized chunks, sampled evenly.
# Chunks are sized by TEXT, not time — gym/IRL streams speak so sparsely
# that 5-minute windows carried ~1KB of words and gemma found nothing.
CHUNK_CHARS = 3500
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
    notes_template = _NOTES_PROMPT_PATH.read_text(encoding="utf-8")
    # The WHOLE transcript, not just the sampled chunks: a phrase nominated
    # from one chunk is counted against everything the creator said.
    transcript = " ".join(s.text for s in segments)
    stored = 0
    for chunk_text in _sample_chunks(segments):
        # Two-stage extraction: gemma-class local models are good at open
        # summarization but have terrible recall for typed-JSON extraction
        # on messy stream banter (whole gym streams extracted ZERO facts
        # single-stage). Stage 1 takes free-form notes; stage 2 structures
        # the notes, where validation drops anything malformed.
        source = chunk_text
        try:
            notes = llm.generate(
                notes_template.replace("{transcript}", chunk_text), json_mode=False
            ).strip()
            if _norm(notes) in ("nothing", "nothing."):
                continue
            if len(notes) >= 40:
                source = notes
        except Exception:
            pass  # fall back to direct extraction on the raw chunk
        raw = llm.generate(prompt_template.replace("{transcript}", source), json_mode=True)
        data = _parse(raw)
        if data is None:
            continue
        stored += _store_facts(db, creator_id, video_id, data.get("facts") or [], transcript)
        stored += _store_events(db, creator_id, video_id, data.get("events") or [])

    _mark_stale_events(db, creator_id)
    _prune(db, creator_id)
    return stored


def _sample_chunks(segments: list[Segment]) -> list[str]:
    """Transcript chunks of ~CHUNK_CHARS of actual words, sampled evenly
    across the whole video so a 3-hour stream contributes its middle and
    end, not just its intro."""
    if not segments:
        return []
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for seg in segments:
        current.append(seg.text)
        size += len(seg.text) + 1
        if size >= CHUNK_CHARS:
            chunks.append(" ".join(current))
            current, size = [], 0
    if current and (size > 400 or not chunks):
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


def _store_facts(
    db: StateDB, creator_id: int, video_id: str, facts: list, transcript: str
) -> int:
    """Store new facts and reinforce ones we already knew.

    A fact we've heard before isn't a duplicate to throw away — it's the
    evidence that the fact is real. Hearing it again bumps times_seen and
    resets its dormancy clock, which is what promotes a phrase to a genuine
    catchphrase and what keeps live knowledge from decaying."""
    existing = {
        _norm(r["information"]): dict(r)
        for r in db.conn.execute(
            "SELECT knowledge_id, information, times_seen, last_video"
            " FROM creator_knowledge WHERE creator_id = ?",
            (creator_id,),
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

        # Catchphrases and running jokes are claims about REPETITION, and the
        # model makes them off a single quotable line all the time. Count what
        # the creator actually said: a phrase that isn't in the transcript at
        # all was paraphrased or invented, and one heard once is stored as a
        # candidate that scores nothing until it comes back.
        heard = 1
        if ktype in PHRASE_TYPES:
            phrase = phrase_of(info)
            if not is_phrase_like(phrase):
                continue
            heard = count_phrase(phrase, transcript)
            if heard == 0:
                continue

        key = _norm(info)
        # Dedupe: exact or containment against what we already know.
        match = existing.get(key) or next(
            (v for k, v in existing.items() if key in k or k in key), None
        )
        if match is not None:
            _reinforce(db, match, video_id, heard)
            continue
        cur = db.conn.execute(
            "INSERT INTO creator_knowledge (creator_id, knowledge_type, information,"
            " confidence, source_video, created_at, times_seen, last_seen, last_video)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (creator_id, ktype, info, conf, video_id, _now(), heard, _now(), video_id),
        )
        existing[key] = {"knowledge_id": cur.lastrowid, "times_seen": heard,
                         "last_video": video_id}
        stored += 1
    db.conn.commit()
    return stored


def _reinforce(db: StateDB, row: dict, video_id: str, heard: int) -> None:
    """Heard again — count it and reset the dormancy clock.

    Once per video: chunks are sampled from one transcript and the model
    repeats itself across them, so without this guard a fact could confirm
    itself as a catchphrase off a single video's chunking."""
    if row.get("last_video") == video_id:
        return
    db.conn.execute(
        "UPDATE creator_knowledge SET times_seen = times_seen + ?, last_seen = ?,"
        " last_video = ? WHERE knowledge_id = ?",
        (max(1, heard), _now(), video_id, row["knowledge_id"]),
    )
    row["last_video"] = video_id


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
    """Forget what didn't stick, and keep the knowledge base bounded.

    Dropout: a fact heard exactly once, that hasn't come up again across
    FORGET_DAYS and FORGET_VIDEOS of this creator's videos, was a one-off —
    it's deleted rather than left dormant forever. Repeated facts are never
    dropped this way, however quiet they've gone.

    Then the cap, which now prefers the facts the creator keeps saying over
    the ones we merely heard recently."""
    forget = dormant_before(db, creator_id, FORGET_DAYS, FORGET_VIDEOS)
    if forget is not None:
        db.conn.execute(
            "DELETE FROM creator_knowledge WHERE creator_id = ? AND times_seen <= 1"
            " AND COALESCE(last_seen, created_at) < ?",
            (creator_id, forget),
        )
    db.conn.execute(
        "DELETE FROM creator_knowledge WHERE knowledge_id IN ("
        "  SELECT knowledge_id FROM creator_knowledge WHERE creator_id = ?"
        "  ORDER BY CASE confidence WHEN 'high' THEN 0 ELSE 1 END,"
        "           times_seen DESC,"
        "           COALESCE(last_seen, last_used, created_at) DESC"
        "  LIMIT -1 OFFSET ?"
        ")",
        (creator_id, MAX_KNOWLEDGE_PER_CREATOR),
    )
    db.conn.commit()
