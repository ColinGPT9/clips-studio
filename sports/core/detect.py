"""Finding a match's moments in signals the pipeline already has.

No single signal is trusted on its own (WSC Sports documents the same rule:
clip when the signals agree):
- the crowd, from the sound tagger (analysis/game_audio.py): a roar has to
  last CROWD_SUSTAIN seconds, since one that dies in a second was a near miss;
- the commentator's voice jumping (analysis/fusion.py _voice_jump);
- the whistle;
- what the commentary says (the sport's callouts, config/sports.yaml);
- on-screen text (analysis/game_text.py);
- the scoreboard, which alone confirms a goal (sports/soccer/scoreboard.py),
  dated by the crowd's loudest roar before the new score showed.

Signals close together are one moment; the profile types it (a type needs two
signals to agree), dates it (a crowd-found moment earlier, for the crowd's
lag), and gives it a window. A second "goal" soon after a goal, with no new
score, is the goal shown again and joins its moment.
"""

from sports.core.events import SportEvent, group_moments
from sports.core.windows import window

# A roar: crowd strength at CROWD_AT for CROWD_SUSTAIN seconds (HypeCut uses
# 2.5 s). Measured on a broadcast final, where the commentary sits over the
# crowd: at 0.5 for 3 s only the goals counted, and three of the six not even
# those; at 0.4 for 2 s it also found a yellow card, attacks and duels, and
# every goal's replays and celebrations (grouped with it). A one-second
# "ooh" still doesn't count.
CROWD_AT = 0.4
CROWD_SUSTAIN = 2
VOICE_AT = 0.6          # the commentator's voice at twice their usual (fusion's own bar)
WHISTLE_AT = 0.6
CLUSTER = 15.0          # signals this close are one moment
NEAR_CALLOUT = 12.0     # the commentary about a moment lands within this of it
NEAR_WHISTLE = 10.0
GOAL_ROAR = 0.1         # the crowd before a new score has to reach this to date the goal...
ROAR_SPAN = 5           # ...judged over this many seconds, so a blip doesn't outweigh a roar...
ROAR_START = 0.3        # ...and the roar starts where it first reaches this share of its peak
# Soccer's, for reference: each sport's own are its profile's scoring_types
# and celebration (config/sports.yaml).
CELEBRATION = 45.0      # a roar this soon after a goal is its celebration: no kick-off comes sooner
GOALS = ("goal", "penalty_goal", "own_goal")


def _at(curve, t: float) -> float:
    if curve is None or len(curve) == 0:
        return 0.0
    i = int(max(0, min(len(curve) - 1, t)))
    return float(curve[i])


def _max(curve, lo: float, hi: float) -> float:
    if curve is None or len(curve) == 0:
        return 0.0
    a, b = int(max(0, lo)), int(min(len(curve), hi + 1))
    return float(max(curve[a:b])) if b > a else 0.0


def runs(curve, at: float, sustain: int) -> list[tuple[int, int, float]]:
    """(first second, last second, peak) for each stretch at or above `at`
    lasting `sustain` seconds or more (a one-second dip bridged)."""
    out = []
    n = 0 if curve is None else len(curve)
    sec = 0
    while sec < n:
        if curve[sec] < at:
            sec += 1
            continue
        end = sec
        while end + 1 < n and (curve[end + 1] >= at or (end + 2 < n and curve[end + 2] >= at)):
            end += 1
        if end - sec + 1 >= sustain:
            out.append((sec, end, float(max(curve[sec:end + 1]))))
        sec = end + 1
    return out


def peaks(curve, at: float, spacing: int = 5) -> list[tuple[int, float]]:
    """Seconds where `curve` reaches `at`, at most one per `spacing` seconds."""
    out: list[tuple[int, float]] = []
    n = 0 if curve is None else len(curve)
    for sec in range(n):
        v = float(curve[sec])
        if v < at:
            continue
        if out and sec - out[-1][0] < spacing:
            if v > out[-1][1]:
                out[-1] = (sec, v)
            continue
        out.append((sec, v))
    return out


def moments(profile, segments, *, curves: dict, voice=None, screen=(), board=None,
            video_end: float, min_len: float, max_len: float,
            extra_after: float = 0.0, extra_types=()) -> list[SportEvent]:
    """The match's moments, typed, dated, windowed and grouped.

    `curves`: the sport's sound strengths per second ({"crowd": ..., "whistle":
    ...}, 0..1), and "crowd_heard", the sound model's own crowd probability
    before scaling, which dates the goals. `screen`: (second, text) the
    broadcast wrote on screen.
    `board`: the read scoreboard, or None. `extra_after` seconds are added
    after moments of `extra_types` (Goals + celebrations)."""
    crowd, whistle = curves.get("crowd"), curves.get("whistle")
    goals = profile.scoring_types
    said_at: list[tuple[float, float, list]] = []          # (start, end, callouts) per segment
    for sg in segments:
        said = profile.callouts_in(sg.text)
        if said:
            said_at.append((sg.start, sg.end, said))

    anchors: list[tuple[float, str]] = []
    roars = runs(crowd, CROWD_AT, CROWD_SUSTAIN)
    anchors += [(float(s), "crowd") for s, _e, _p in roars]
    anchors += [(float(s), "voice") for s, _v in peaks(voice, VOICE_AT)]
    anchors += [(s, "callout") for s, _e, _said in said_at]
    anchors += [(float(s), "screen") for s, _text in screen]
    anchors.sort()

    clusters: list[list[tuple[float, str]]] = []
    for a in anchors:
        if clusters and a[0] - clusters[-1][-1][0] <= CLUSTER:
            clusters[-1].append(a)
        else:
            clusters.append([a])

    events: list[SportEvent] = []
    for cl in clusters:
        lo, hi = cl[0][0], cl[-1][0]
        signals: list[str] = []
        roar = next(((s, e, p) for s, e, p in roars if s <= hi + 2 and e >= lo - 2), None)
        if roar is not None:
            signals.append(f"crowd roar {roar[1] - roar[0] + 1}s")
        if _max(voice, lo - 3, hi + 5) >= VOICE_AT:
            # No commentator at a club match: whoever is near the camera.
            signals.append("someone shouts" if getattr(profile, "footage", "broadcast") == "sideline"
                           else "commentator's voice jumps")
        if _max(whistle, lo - NEAR_WHISTLE, hi + NEAR_WHISTLE) >= WHISTLE_AT:
            signals.append("whistle")
        shown = [text for s, text in screen if lo - 5 <= s <= hi + 5]
        if shown:
            signals.append(f"on screen: {shown[0]}")
        said = [x for s, e, found in said_at if s <= hi + NEAR_CALLOUT and e >= lo - NEAR_CALLOUT
                for x in found]
        kind, confidence = profile.classify(said, signals)
        if not kind:
            continue
        # When it happened: the commentator calls it as it happens; the crowd
        # roars a second or two after.
        called = [s for s, e, found in said_at if s <= hi + NEAR_CALLOUT and e >= lo - NEAR_CALLOUT
                  and any(k == kind for k, _ in found)]
        if called:
            t, lag = min(called), 0.0
        elif roar is not None:
            t, lag = float(roar[0]), profile.crowd_lag
        else:
            t, lag = lo, 0.0
        text = " ".join(sg.text for sg in segments if sg.end >= lo - 5 and sg.start <= hi + 15)
        e = SportEvent(kind, t, confidence, profile.importance(kind),
                       signals=signals + [f'said "{w}"' for _k, w in said[:2]],
                       is_replay=profile.replay_said(text))
        e.start, e.end = _window(profile, e, lag, video_end, min_len, max_len, extra_after, extra_types)
        events.append(e)

    # The scoreboard: every confirmed goal is a goal, dated by the crowd. The
    # new score shows anywhere from seconds to minutes after the ball goes in,
    # so it can't date the goal itself; it does say the goal came after the
    # score before it.
    heard = curves.get("crowd_heard")
    previous = 0.0
    for change in (board.changes if board is not None else []):
        lo = max(change.lo, previous)
        previous = change.hi
        roar = goal_roar(heard if heard is not None else crowd, lo, change.hi)
        moment = None if roar is None else max(0.0, roar - profile.crowd_lag)
        inside = [e for e in events if lo <= e.t <= change.hi
                  and (moment is None or abs(e.t - moment) <= CLUSTER)]
        best = max(inside, key=lambda e: (e.type in goals, e.confidence, e.importance), default=None)
        if best is None:
            kind = profile.confirmed_type(change, None)
            best = SportEvent(kind, moment if moment is not None else max(0.0, change.hi - 30.0), 0.67,
                              profile.importance(kind), signals=[])
            events.append(best)
        if moment is not None:
            best.t = moment
            if not any(s.startswith("crowd roar") for s in best.signals):
                best.signals.append("crowd roar")
        best.type = profile.confirmed_type(change, best)
        best.importance = profile.importance(best.type)
        best.confidence = 1.0
        best.confirmed = True
        best.team = change.team
        best.is_replay = False
        best.signals = [change.label()] + [s for s in best.signals if not s.startswith("score ")]
        best.start, best.end = _window(profile, best, 0.0, video_end, min_len, max_len, extra_after,
                                       extra_types)

    # The match's events as the person listed them (sports/core/events_import.py):
    # certain, placed on the video by the clock or the list's kick-offs.
    starts: dict = {}
    if (profile.option or {}).get("events"):
        events, starts = _with_listed(profile, events, str(profile.option["events"]), board,
                                      heard if heard is not None else crowd,
                                      video_end, min_len, max_len, extra_after, extra_types)

    # A second goal-like moment soon after a goal, with no new score of its
    # own, is that goal again: the replay, or the celebration's second roar.
    confirmed = {id(e) for e in events if e.confidence >= 1.0 and e.type in goals}
    goals_at = sorted(e.t for e in events if id(e) in confirmed)
    for e in events:
        if id(e) in confirmed or e.type not in (*goals, "big_moment"):
            continue
        since = [e.t - g for g in goals_at if 0 < e.t - g <= profile.replay_within]
        if not since:
            continue
        if (board is None or not board.changes or e.type in goals or min(since) <= profile.celebration
                or board.hidden(e.t - 5, e.t + 10)):
            e.is_replay = True

    # With the score read, it alone says what a goal is: a "goal" the score
    # never confirmed, where it was read, was talk about one or one ruled
    # out. Where it wasn't read, the commentary and the crowd still decide.
    for e in events:
        if (e.type in goals and e.confidence < 1.0 and not e.is_replay and board is not None
                and _score_read(board, e.t)):
            e.type = "big_moment"
            e.importance = profile.importance("big_moment")
            # ...on the signals alone: what was said was what made it a goal.
            e.confidence = min(1.0, sum(not s.startswith("said") for s in e.signals) / 3)
            e.start, e.end = _window(profile, e, 0.0, video_end, min_len, max_len, extra_after, extra_types)
    # Whatever the sport finds on its own (basketball: the reactions to a play).
    events = profile.extra_moments(events, segments, curves=curves, video_end=video_end,
                                   min_len=min_len, max_len=max_len)
    for e in events:
        if board is not None:
            e.period = board.period_at(e.t)
            e.minute = board.minute_at(e.t)
        if starts and not e.period and e.minute is None:
            e.period, e.minute = _from_kickoffs(e.t, starts)
    return group_moments(events, profile.replay_within, getattr(profile, "one_play_per_clip", False))


LISTED_NEAR = 60.0      # a found moment this close to a listed one is that moment
LIST_BEFORE = 5.0       # a bare listed time's clip: from just before the tag...
LIST_AFTER = 40.0       # ...to the celebration, whether the tag is the goal or the attack
NOT_MOMENTS = ("kickoff", "kickoff_second", "halftime", "fulltime")


def _with_listed(profile, events: list, text: str, board, level, video_end: float, min_len: float,
                 max_len: float, extra_after: float, extra_types) -> tuple[list, dict]:
    """The events plus the ones the person listed, each placed, typed and
    certain; and where each half starts, when the list says. A found moment
    near a listed one is that moment: the list names it, and a goal the
    score box dated to the second keeps its time. What couldn't be read or
    placed is reported on the profile (listed_report), never guessed."""
    from sports.core import events_import

    listed, unread = events_import.parse(text, profile.spec)
    starts = events_import.kickoffs(listed)
    kinds = profile.events_spec()
    read = board if board is not None and board.readings else None
    teams = read.teams() if read is not None else None
    placed, unplaced = 0, []
    for item in listed:
        if item.kind in NOT_MOMENTS or item.kind not in kinds:
            continue
        span = events_import.place(item, read, starts)
        if span is None:
            unplaced.append(item.line)
            continue
        lo, hi = span
        # When in the span: the crowd, as for a new score. A listed time is a
        # tag, often at the start of the attack: a club app's goal tags sat
        # 21-27 s before the ball went in on an auto-camera match.
        roar = goal_roar(level, max(0.0, lo - 5), hi + 10 if hi > lo else lo + LIST_AFTER)
        dated = roar is not None
        if dated:
            t = max(0.0, roar - profile.crowd_lag)
        else:
            t = (lo + hi) / 2 if hi > lo else lo
        near = [e for e in events if not e.is_replay and abs(e.t - t) <= LISTED_NEAR
                and (e.type == item.kind or e.type == "big_moment"
                     or (item.kind in profile.scoring_types and e.type in profile.scoring_types))]
        e = min(near, key=lambda e: abs(e.t - t), default=None)
        if e is None:
            e = SportEvent(item.kind, t, 1.0, profile.importance(item.kind), signals=[])
            events.append(e)
        elif not (e.confirmed and hi > lo):
            e.t = t                              # the person's time, unless the score box's is finer
        e.type = item.kind
        e.importance = profile.importance(item.kind)
        e.confidence = 1.0
        e.confirmed = True
        e.is_replay = False
        if item.who:
            if teams and item.who.upper() in teams:
                e.team = item.who.upper()
            else:
                e.player = item.who
        e.signals = ["from your match events"] + [s for s in e.signals if s != "from your match events"]
        if dated or hi > lo:
            e.start, e.end = _window(profile, e, 0.0, video_end, min_len, max_len, extra_after, extra_types)
        else:
            # Nothing but the tag: from just before it to well after, so the
            # goal is in whether the tag marks it or the attack before it.
            e.start, e.end = window(LIST_BEFORE, LIST_AFTER, lo, min_len=min_len, max_len=max_len,
                                    video_end=video_end)
        placed += 1
    profile.listed_report = {"placed": placed, "unplaced": unplaced, "unread": unread}
    return events, starts


def _from_kickoffs(t: float, starts: dict) -> tuple[str, int | None]:
    """The half and the match minute at t from where the halves start."""
    if 2 in starts and t >= starts[2]:
        return "second_half", 45 + int((t - starts[2]) // 60) + 1
    if 1 in starts and t >= starts[1]:
        return "first_half", int((t - starts[1]) // 60) + 1
    return "", None


def _score_read(board, t: float) -> bool:
    """Whether the score was read around t: in the minutes before it and in
    the minutes after, when a goal at t would have shown. (The box is hidden
    for replays, so a minute or two without a reading is usual.)"""
    seen = [r.t for r in board.readings if r.score is not None and t - 180 <= r.t <= t + 180]
    return any(x <= t for x in seen) and sum(x > t for x in seen) >= 2


def goal_roar(level, lo: float, hi: float) -> float | None:
    """When a goal the scoreboard confirmed went in: where the crowd's
    loudest ROAR_SPAN seconds between `lo` and the new score start. None when
    the crowd was never heard there.

    Measured on a broadcast final, searching the three minutes before each of
    its six new scores (and never before the score before it): every goal
    dated within two seconds of the ball going in, including goals whose roar
    lasted two or three seconds, too short to be a moment of their own, and a
    score shown 40 s after its goal as well as one shown 8 s after. The
    scaled crowd strength tops out at 1.0 and ties across a match, so the
    sound model's own probability decides."""
    if level is None or len(level) == 0:
        return None
    a, b = int(max(0, lo)), int(min(len(level), hi + 1))
    if b <= a:
        return None

    def roar(i: int) -> float:
        return sum(float(v) for v in level[i:min(b, i + ROAR_SPAN)])

    best = max(range(a, b), key=roar)
    span = [float(v) for v in level[best:min(b, best + ROAR_SPAN)]]
    top = max(span)
    if top < GOAL_ROAR:
        return None
    return float(best + next(i for i, v in enumerate(span) if v >= ROAR_START * top))


def _window(profile, e: SportEvent, lag: float, video_end: float, min_len: float, max_len: float,
            extra_after: float, extra_types) -> tuple[float, float]:
    pre, post = profile.window_of(e.type)
    return window(pre, post, e.t, min_len=min_len, max_len=max_len, video_end=video_end, lag=lag,
                  post_extra=extra_after if e.type in extra_types else 0.0)
