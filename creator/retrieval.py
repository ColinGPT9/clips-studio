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
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from core.state import StateDB, _now

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

    rows = db.conn.execute(
        "SELECT knowledge_id, knowledge_type, information FROM creator_knowledge"
        " WHERE creator_id = ? ORDER BY CASE confidence WHEN 'high' THEN 0 ELSE 1 END,"
        " created_at DESC",
        (creator_id,),
    ).fetchall()
    used_ids, themes = [], {"topic": [], "game": [], "series": [], "format": []}
    for r in rows:
        info = r["information"].strip()
        if r["knowledge_type"] in ("catchphrase", "joke"):
            # Only short literal phrases are matchable in a transcript.
            phrase = info.split(" - ")[0].strip().strip("'\"")
            if 1 <= len(phrase.split()) <= 6 and len(ctx.phrases) < 8:
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
