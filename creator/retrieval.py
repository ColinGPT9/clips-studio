"""Retrieval: stored creator knowledge -> context for scoring and metadata.

Two consumers:
  * scoring (analysis/fusion.py): `context_bonus` — a DETERMINISTIC, hard-
    capped, additive-only nudge for clips that contain verifiable callbacks
    (an open event, a catchphrase, a collaborator's name). It can never
    lower a score, never exceed its cap, and is zero for creators with no
    knowledge — so scoring quality cannot degrade as knowledge accumulates.
  * metadata (titles/descriptions/hashtags): `CreatorContext.summary` — a
    short text block the LLM may use for accuracy (series names, running
    jokes, collaborators). No scores involved.

Both consumers see only ACTIVE knowledge. Two filters stand in the way:
a catchphrase must have been repeated (creator/models.MIN_PHRASE_REPEATS),
and anything the creator has stopped saying goes dormant (DORMANT_DAYS /
DORMANT_VIDEOS). Dormant facts are not deleted — they simply stop having an
opinion until they're heard again.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from core.state import StateDB, _now
from creator.models import (
    DORMANT_DAYS,
    DORMANT_VIDEOS,
    MIN_PHRASE_REPEATS,
    PHRASE_TYPES,
)

RECENT_COMPLETED_DAYS = 14   # a just-finished goal is still a callback
MAX_SUMMARY_ITEMS = 4        # per category, keeps the prompt block tiny

_STOP = set(
    "the a an is are was were be been being i we you they he she it this that "
    "to of in on at for with and or but my your our their his her its do did "
    "done have has had not no so just like really gonna going get got".split()
)


@dataclass
class CreatorContext:
    creator_name: str
    events: list[dict] = field(default_factory=list)   # {name, description, words}
    phrases: list[str] = field(default_factory=list)   # catchphrases + running jokes
    collaborators: list[str] = field(default_factory=list)
    summary: str = ""


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", s.lower()) if w not in _STOP and len(w) > 2}


def _flatten(s: str) -> str:
    """Lowercased, punctuation stripped to single spaces — so a phrase matches
    however Whisper happened to punctuate it that time."""
    return re.sub(r"[^a-z0-9']+", " ", s.lower()).strip()


def phrase_of(information: str) -> str:
    """The literal spoken part of a catchphrase/joke fact. The extractor
    stores them as "phrase - what it means"; only the phrase can be matched
    against a transcript."""
    return information.split(" - ")[0].strip().strip("'\"‘’“”")


def count_phrase(phrase: str, text: str) -> int:
    """How many times a phrase is actually spoken in a transcript. This is the
    number that decides whether something is a catchphrase or a thing the
    creator said once."""
    p = _flatten(phrase)
    if not p:
        return 0
    return len(re.findall(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", _flatten(text)))


def is_phrase_like(phrase: str) -> bool:
    """A matchable signature phrase: short, and carrying at least one word
    that isn't filler (so "you know what I mean" can't become a catchphrase)."""
    words = phrase.split()
    return 1 <= len(words) <= 6 and bool(_words(phrase))


def dormant_before(db: StateDB, creator_id: int, days: int, videos: int) -> str | None:
    """The cutoff timestamp for "this stopped being said": knowledge last
    heard before it has sat through both `days` of calendar time AND `videos`
    of this creator's videos without coming up again. None when they haven't
    posted enough videos yet to conclude anything — silence isn't evidence
    when nobody was listening."""
    row = db.conn.execute(
        "SELECT updated_at FROM videos WHERE creator_id = ? AND status = 'done'"
        " ORDER BY updated_at DESC LIMIT 1 OFFSET ?",
        (creator_id, videos - 1),
    ).fetchone()
    if row is None:
        return None
    by_time = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    return min(by_time, row["updated_at"])


def context_for(db: StateDB, creator_id: int) -> CreatorContext | None:
    """Build the context for one creator, or None when nothing is known.
    Marks the included knowledge as used (last_used drives pruning)."""
    c = db.conn.execute(
        "SELECT display_name FROM creators WHERE creator_id = ?", (creator_id,)
    ).fetchone()
    if c is None:
        return None
    ctx = CreatorContext(creator_name=c["display_name"])

    recent = (datetime.now() - timedelta(days=RECENT_COMPLETED_DAYS)).isoformat(
        timespec="seconds"
    )
    for e in db.conn.execute(
        "SELECT event_name, description, status FROM creator_events"
        " WHERE creator_id = ? AND (status IN ('announced', 'in_progress')"
        "       OR (status = 'completed' AND completed_date >= ?))"
        " ORDER BY detected_date DESC LIMIT 8",
        (creator_id, recent),
    ):
        ctx.events.append(
            {
                "name": e["event_name"],
                "description": e["description"],
                "status": e["status"],
                "words": _words(f"{e['event_name']} {e['description']}"),
            }
        )

    # Dropout: anything the creator hasn't said again in a long time is left
    # out of scoring and out of the prompt. It stays in the database (and on
    # the Creators page) and comes straight back the next time it's heard.
    cutoff = dormant_before(db, creator_id, DORMANT_DAYS, DORMANT_VIDEOS)
    rows = db.conn.execute(
        "SELECT knowledge_id, knowledge_type, information, times_seen"
        " FROM creator_knowledge WHERE creator_id = ?"
        "   AND (? IS NULL OR COALESCE(last_seen, created_at) >= ?)"
        " ORDER BY CASE confidence WHEN 'high' THEN 0 ELSE 1 END,"
        " times_seen DESC, created_at DESC",
        (creator_id, cutoff, cutoff),
    ).fetchall()
    used_ids, themes = [], {"topic": [], "game": [], "series": [], "format": []}
    for r in rows:
        info = r["information"].strip()
        if r["knowledge_type"] in PHRASE_TYPES:
            # A catchphrase has to have been REPEATED. Below that it's just a
            # line the creator said once, and it neither scores nor reaches
            # the metadata prompt.
            if r["times_seen"] < MIN_PHRASE_REPEATS:
                continue
            phrase = phrase_of(info)
            if is_phrase_like(phrase) and len(ctx.phrases) < 8:
                ctx.phrases.append(phrase)
                used_ids.append(r["knowledge_id"])
        elif r["knowledge_type"] == "collaborator":
            name = info.split(" - ")[0].strip()
            if 1 <= len(name.split()) <= 4 and len(ctx.collaborators) < 8:
                ctx.collaborators.append(name)
                used_ids.append(r["knowledge_id"])
        elif r["knowledge_type"] in themes and len(themes[r["knowledge_type"]]) < MAX_SUMMARY_ITEMS:
            themes[r["knowledge_type"]].append(info)
            used_ids.append(r["knowledge_id"])

    if not (ctx.events or ctx.phrases or ctx.collaborators or any(themes.values())):
        return None

    lines = [f"Known about this creator ({ctx.creator_name}), use only if relevant:"]
    for e in ctx.events[:MAX_SUMMARY_ITEMS]:
        state = {"announced": "upcoming", "in_progress": "ongoing", "completed": "recently completed"}[e["status"]]
        lines.append(f"- {state}: {e['name']}" + (f" — {e['description']}" if e["description"] else ""))
    if themes["series"]:
        lines.append(f"- Recurring series: {', '.join(themes['series'])}")
    if themes["topic"] or themes["game"]:
        lines.append(f"- Usual content: {', '.join(themes['topic'] + themes['game'])}")
    if ctx.phrases:
        lines.append(f"- Catchphrases/running jokes: {', '.join(repr(p) for p in ctx.phrases[:MAX_SUMMARY_ITEMS])}")
    if ctx.collaborators:
        lines.append(f"- Frequent collaborators: {', '.join(ctx.collaborators[:MAX_SUMMARY_ITEMS])}")
    ctx.summary = "\n".join(lines)

    if used_ids:
        db.conn.execute(
            f"UPDATE creator_knowledge SET last_used = ? WHERE knowledge_id IN"
            f" ({','.join('?' * len(used_ids))})",
            (_now(), *used_ids),
        )
        db.conn.commit()
    return ctx


def context_bonus(clip_text: str, ctx: CreatorContext | None, cap: int = 6) -> tuple[int, list[str]]:
    """Additive-only, capped score nudge for verifiable callbacks in this
    clip's transcript. Deterministic string matching — no LLM judgment can
    move scores here. Returns (bonus, human-readable reasons)."""
    if ctx is None or not clip_text:
        return 0, []
    text_words = _words(clip_text)
    text_lower = clip_text.lower()
    bonus, reasons = 0, []

    # An open/recent event referenced in the clip — the strongest callback
    # (viewers with context get the payoff). One event max.
    for e in ctx.events:
        if e["words"] and len(e["words"] & text_words) / len(e["words"]) >= 0.6:
            bonus += 4
            reasons.append(f"event callback: {e['name']}")
            break

    for p in ctx.phrases:
        if p.lower() in text_lower:
            bonus += 1
            reasons.append(f"catchphrase: {p}")
            break

    for name in ctx.collaborators:
        if re.search(rf"\b{re.escape(name.lower())}\b", text_lower):
            bonus += 1
            reasons.append(f"collaborator: {name}")
            break

    return min(bonus, cap), reasons
