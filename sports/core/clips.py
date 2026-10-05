"""From a match's moments to its clips, inside the normal scoring.

The moments (sports/core/detect.py) join the candidates every other video
gets; the scorer still decides how good each is. This module only:
- attaches each candidate to the moment it shows;
- gives it a capped bonus for that moment (a goal is worth more than a
  corner), as the game bonus does for a gaming stream;
- keeps one clip per moment (a goal, not the goal plus its two replays);
- applies the Highlights choice and the period;
- and, for a chosen kind of moment (All goals, Cards...), keeps every one
  that was confirmed, lifted to the quality bar, with its build-up and
  reaction: asked for all the goals, a person expects all the goals.
"""

from sports.core import select
from sports.core.events import SportEvent

BONUS_MAX = 20          # a confirmed goal; everything else in proportion to its worth
WINDOWS_MAX = 40        # moments given a window of their own, most important first
TYPED = 0.66            # confidence at which a moment is called by its type (two signals: 2/3)


def windows_to_add(moments: list[SportEvent], candidates, shown_only: bool = False) -> list[SportEvent]:
    """The moments whose own window no candidate already matches, most
    important first. A candidate that already covers the moment isn't
    enough: its window may start as the ball goes in.

    `shown_only`: a candidate matches only the moment it shows (attach). On
    an NBA game one sentence's candidate covered the last layup and the
    final dunk, showed the layup, and the dunk had no clip."""
    shown = attach(moments, candidates) if shown_only else {}
    out = []
    for e in sorted(moments, key=lambda e: -(e.importance * max(e.confidence, 0.34))):
        if e.is_replay or len(out) >= WINDOWS_MAX:
            continue
        if any(_overlap(c.start, c.end, e.start, e.end) >= 0.8 and (not shown_only or shown.get(id(c)) is e)
               for c in candidates):
            continue
        out.append(e)
    return out


def _overlap(a0, a1, b0, b1) -> float:
    inter = min(a1, b1) - max(a0, b0)
    shorter = min(a1 - a0, b1 - b0)
    return inter / shorter if inter > 0 and shorter > 0 else 0.0


def attach(moments: list[SportEvent], candidates) -> dict:
    """id(candidate) -> the moment it shows: one whose time falls inside it
    (or overlapping most), the most important first."""
    out = {}
    for c in candidates:
        best = None
        for e in moments:
            inside = c.start - 2 <= e.t <= c.end
            if not inside and _overlap(c.start, c.end, e.start, e.end) < 0.5:
                continue
            key = (not e.is_replay, e.importance * max(e.confidence, 0.34), inside)
            if best is None or key > best[0]:
                best = (key, e)
        if best is not None:
            out[id(c)] = best[1]
    return out


def bonus(e: SportEvent | None, profile=None) -> int:
    """Points for the moment a clip shows: its worth, times the game's
    situation when the sport weighs it (profile.context_weight), within
    BONUS_MAX."""
    if e is None or e.is_replay:
        return 0
    weight = profile.context_weight(e) if profile is not None else 1.0
    worth = min(100.0, e.importance * weight)
    return round(BONUS_MAX * worth / 100 * max(e.confidence, 0.34))


def mark(c, e: SportEvent, label: str, points: int) -> None:
    """The moment on the clip's breakdown (subscores reach the clip card)."""
    s = c.subscores if c.subscores is not None else {}
    c.subscores = s
    s["sport_event"] = e.type
    s["sport_label"] = label
    s["sport_t"] = round(e.t, 1)
    s["sport_group"] = e.group
    s["sport_why"] = e.why()
    if e.team:
        s["sport_team"] = e.team
    if e.player:
        s["sport_player"] = e.player
    if e.period:
        s["sport_period"] = e.period
    if e.minute is not None:
        s["sport_minute"] = e.minute
    if e.when:
        s["sport_when"] = e.when
    if e.context:
        s["sport_context"] = e.context
    if e.person:
        s["sport_person"] = e.person
    if e.is_replay:
        s["sport_replay"] = True
    if points:
        s["sport_bonus"] = points


def choose(profile, candidates, attached: dict, *, min_score: int, max_len: float):
    """(candidates kept, [(candidate, reason)] dropped, notes)."""
    spec = profile.spec
    highlights = profile.option.get("highlights", "best")
    period = profile.option.get("period", "full")
    types = select.event_types(spec, highlights)
    board = getattr(profile, "board", None)
    kept, dropped, notes = [], [], []
    # Club or phone footage where nothing confirmed the chosen kind of moment
    # (no score box, no commentary): the best moments, and a note saying so,
    # rather than no clips at all. On two club matches, neither a sound nor
    # the camera's movement marked their goals reliably (docs/SPORTS.md).
    if (types is not None and getattr(profile, "footage", "broadcast") == "sideline"
            and not any(e.type in types and e.confidence >= TYPED and not e.is_replay
                        for e in attached.values())):
        choice = (spec.get("highlights_choices") or {}).get(highlights) or {}
        notes.append(f"Club or phone footage: nothing here could confirm "
                     f"{str(choice.get('label') or highlights).lower()} (no score box or commentary), "
                     "so these are the match's best moments instead. "
                     + str(spec.get("listed_hint") or "Add the goal times under Match events to clip every goal"))
        types = None

    # One clip per moment: the original over a replay, then the best scored.
    best_of: dict[int, object] = {}
    for c in candidates:
        e = attached.get(id(c))
        if e is None:
            continue
        cur = best_of.get(e.group)
        ce = attached.get(id(cur)) if cur is not None else None
        if cur is None or (ce.is_replay, -cur.score) > (e.is_replay, -c.score):
            best_of[e.group] = c
    for c in candidates:
        e = attached.get(id(c))
        if e is not None and best_of.get(e.group) is not c:
            dropped.append((c, "same_moment"))
            continue
        if types is not None and (e is None or e.type not in types):
            dropped.append((c, "not_in_highlights"))
            continue
        if period and period != "full":
            half = e.period if e is not None and e.period else (
                board.period_at((c.start + c.end) / 2) if board is not None else "")
            if half and half != period:
                dropped.append((c, "other_period"))
                continue
        kept.append(c)

    unknown = [c for c in kept if period and period != "full" and not (
        (attached.get(id(c)) and attached[id(c)].period)
        or (board is not None and board.period_at((c.start + c.end) / 2)))]
    if unknown:
        label = (spec.get("periods") or {}).get(period, period)
        notes.append(f"{len(unknown)} clip(s) kept for {label} without knowing their "
                     f"{spec.get('period_word') or 'half'}: the match clock wasn't read there")

    # A chosen kind of moment: every confirmed one is kept, with its build-up
    # and reaction, whatever the scorer made of its words.
    if types is not None:
        for c in kept:
            e = attached.get(id(c))
            if e is None or e.confidence < TYPED or e.type == "big_moment":
                continue
            c.start, c.end = e.start, min(e.end, e.start + max_len)
            s = c.subscores if c.subscores is not None else {}
            c.subscores = s
            s["required"] = profile.event_label(e.type)
            if c.score < min_score:
                s["sport_lift"] = min_score - c.score
                c.score = min_score
    else:
        # Best moments: a confirmed moment's clip keeps its build-up too.
        for c in kept:
            e = attached.get(id(c))
            if e is not None and e.confidence >= TYPED and e.type != "big_moment" and not e.is_replay:
                c.start, c.end = profile.clip_span(c, e)
                if c.end - c.start > max_len:
                    c.start, c.end = e.start, min(e.end, e.start + max_len)
            # A goal the scoreboard confirmed is one of the match's best
            # moments whatever the scorer made of its words: on the test final
            # the words alone rated two of its six goals 10 and 25.
            if e is not None and e.confirmed and not e.is_replay:
                s = c.subscores if c.subscores is not None else {}
                c.subscores = s
                s["required"] = profile.event_label(e.type)
                if c.score < min_score:
                    s["sport_lift"] = min_score - c.score
                    c.score = min_score
    return kept, dropped, notes


def report(profile, moments: list[SportEvent], kept, attached: dict, notes: list[str]) -> dict:
    """What the match gave, for the run's outcome and the clip page."""
    found: dict[str, int] = {}
    for e in moments:
        if not e.is_replay and e.confidence >= TYPED and e.type != "big_moment":
            found[profile.event_label(e.type)] = found.get(profile.event_label(e.type), 0) + 1
    board = getattr(profile, "board", None)
    final = board.final() if board is not None else None
    teams = board.teams() if board is not None else None
    notes = list(notes)
    listed = getattr(profile, "listed_report", None)
    if listed:
        if listed.get("placed"):
            notes.append(f"{listed['placed']} moment(s) from your match events")
        for line in listed.get("unplaced") or []:
            notes.append(f"Couldn't place \"{line}\": no match clock was read, and no kick-off time was listed")
        for line in listed.get("unread") or []:
            notes.append(f"Couldn't read \"{line}\": a line needs a time and a kind of moment")
    return {
        "sport": profile.label,
        "highlights": profile.option.get("highlights", "best"),
        "period": profile.option.get("period", "full"),
        "found": found,
        "big_moments": sum(1 for e in moments if e.type == "big_moment" and not e.is_replay),
        "replays_grouped": sum(1 for e in moments if e.is_replay),
        "score": (f"{teams[0]} {final[0]}-{final[1]} {teams[1]}" if final and teams
                  else f"{final[0]}-{final[1]}" if final else ""),
        "scoreboard": bool(board is not None and board.box),
        "footage": getattr(profile, "footage", "broadcast"),
        "clips": sum(1 for c in kept if id(c) in attached),
        "notes": list(notes),
    }
