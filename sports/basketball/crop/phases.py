"""What is happening in one camera shot, as phases the planner frames by.

The planner keeps different things in the crop at different moments (the
owner's rules, planner.py): the ball and whoever has it during the play, the
receiver of a pass before the catch, the shooter at the release, the ball in
flight, and the whole basket (rim, net and backboard) from just before the
ball reaches the rim until the result. This module says when each of those
moments is.

From the ball's validated path (ball.py), who holds it (players.py) and the
relevant basket (basket.py):

- **possession**: someone has the ball;
- **pass**: it leaves one player and reaches another (actors: passer, receiver);
- **release**, **flight**, **rim**, **result**: a shot, from the release to
  the ball reaching the rim zone and 1.5 s after; "rim" starts RIM_LEAD
  before the ball gets there, so the crop arrives first;
- **attack**: a dunk or a layup, the ball carried toward the rim (then "rim"
  and "result" as for a shot);
- **rebound**: a block or a rebound at the rim (from the moment's kind);
- **transition**: the camera racing down the court;
- **loose**: no usable ball and nobody holding it.

When the ball can't be seen around the moment the clip was chosen for, the
moment's time and kind (from the score bug, Hints) place the shot's phases
instead: its dating error is about +-0.65 s, so those windows are generous.

Pure Python.
"""

from __future__ import annotations

from sports.basketball.crop import ball as ball_mod
from sports.basketball.crop import basket as basket_mod
from sports.basketball.crop import players as players_mod
from sports.basketball.crop.model import BallPoint, Basket, Hints, Phase, Sample, Shot

ASPECT = 16 / 9

# A shot, seen.
SHOT_REACH = 2.2         # seconds: the ball reaches the rim zone at most this long after it leaves the shooter
RISE = 0.6               # frame heights per second upward: a ball leaving the hands this fast is going up
RIM_LEAD = 0.35          # seconds before the ball reaches the rim zone that the basket must be in the crop
                         # (0.5 made a long three's ball and the basket need a wider crop, on job 210's clips)
RESULT = 1.5             # seconds the result lasts after the ball reaches the rim...
RESULT_HOLD = 0.6        # ...of which this much holds the basket whatever the camera does; after it, the
PAN_AWAY = 0.05          # camera panned this far (frame widths) away from the basket since the result began
                         # has left it to follow the players, and the result ends there. On a 2019 game-winner
                         # the broadcast panned after the scorer as the ball dropped through, and the crop,
                         # still holding the rim, slid the other way off the celebration.
RELEASE_BEFORE = 0.3     # the release phase: this long before the ball leaves the hands...
RELEASE_AFTER = 0.2      # ...to this long after
ATTACK_X = 0.08          # frame widths: a handler this close to the rim's centre is attacking it...
ATTACK_Y = 2.0           # ...with the ball within this many rim widths (as frame heights) of the rim's height
ATTACK_BEFORE = 0.8      # an attack phase starts this long before the ball reaches the rim

# A pass.
PASS_GAP = 1.5           # seconds: two different holders this close either side of a gap is a pass...
PASS_MIN = 0.2           # ...when the ball was away from both at least this long...
PASS_DIST = 0.06         # ...and they stood at least this far apart (frame widths)

# The camera racing down the court.
PAN_FAST = 0.35          # frame widths per second
PAN_SPAN = 0.6           # seconds, at least

# The moment's kind, when only the moment is known (Hints).
THREES = {"made_3", "corner_three", "deep_three", "attempt_3"}
JUMPERS = {"step_back", "fadeaway", "pull_up", "contested_shot", "clutch_shot", "buzzer_beater", "game_winner",
           "game_tying", "go_ahead", "isolation_score", "made_2", "attempt_2", "free_throw", "miss"}
AT_RIM = {"dunk", "poster_dunk", "alley_oop", "layup", "putback", "tip_in", "euro_step", "difficult_finish",
          "and_one", "fast_break_score", "alley_oop_assist"}
AT_RIM_DEFENCE = {"block", "offensive_rebound", "defensive_rebound", "shot_contest", "charge"}
HINT_THREE = 2.5         # seconds: a three's release is at most this long before its moment
HINT_JUMPER = 2.0        # ...another jump shot's
HINT_ATTACK = 1.5        # ...a drive's last steps
HINT_RIM = 1.0           # the basket is needed from this long before the moment (the dating error and the lead)
HINT_CONF = 0.3

GRID = 0.05              # seconds: the timeline phases are painted on

# Which phase wins where two overlap.
PRIORITY = ("result", "rim", "attack", "rebound", "release", "flight", "pass", "transition", "possession", "loose")


def _kind_of(hints: Hints | None) -> str | None:
    """"three", "jumper", "attack" or "defence" from the moment's kind."""
    kind = (hints.kind if hints else None) or ""
    if kind in THREES:
        return "three"
    if kind in AT_RIM:
        return "attack"
    if kind in AT_RIM_DEFENCE:
        return "defence"
    if kind in JUMPERS:
        return "jumper"
    return None


def _person(sample: Sample, track: int | None):
    if track is None:
        return None
    return next((p for p in sample.people if p.track == track), None)


def _rim_centre(b: Basket) -> tuple[float, float, float]:
    return (b.rim[0] + b.rim[2]) / 2, (b.rim[1] + b.rim[3]) / 2, b.rim[2] - b.rim[0]


HOLD_FAST = 1.0          # frame heights per second: a ball this fast past a player for one sample isn't his


def holders(samples: list[Sample], points: list[BallPoint], aspect: float = ASPECT) -> list[int | None]:
    """Who has the ball at each sample (a track id), when it is usable. A
    pass flying over a player's box for one sample doesn't make him a holder:
    a holder seen for a single sample must have the ball moving slowly."""
    raw = [players_mod.handler(s, bp, aspect) if ball_mod.usable(bp) else None
           for s, bp in zip(samples, points)]
    out = list(raw)
    for k, h in enumerate(raw):
        if h is None:
            continue
        alone = (k == 0 or raw[k - 1] != h) and (k == len(raw) - 1 or raw[k + 1] != h)
        if alone and (points[k].vx ** 2 + points[k].vy ** 2) ** 0.5 >= HOLD_FAST:
            out[k] = None
    # A one-sample flicker to someone else between two samples of the same holder
    # is the same holder (players crowding the ball).
    for k in range(1, len(out) - 1):
        if out[k] is not None and out[k - 1] is not None and out[k - 1] == out[k + 1] != out[k]:
            out[k] = out[k - 1]
    return out


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    """(first, last) index of each run of True."""
    out, start = [], None
    for k, f in enumerate(flags):
        if f and start is None:
            start = k
        if not f and start is not None:
            out.append((start, k - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out


def _seen_shots(samples, points, hand, rel, aspect) -> list[tuple[str, float, float, list[int]]]:
    """Shots and drives seen in the ball's path: (kind, t0, t1, actors) intervals."""
    out = []
    zone = [bool(ball_mod.usable(bp) and b is not None and b.in_frame and basket_mod.in_rim_zone(bp, b, aspect))
            for bp, b in zip(points, rel)]
    for k0, _k1 in _runs(zone):
        t_rim = samples[k0].t
        b = rel[k0]
        cx, cy, rw = _rim_centre(b)
        # The last sample before the rim zone where someone held the ball.
        last = next((k for k in range(k0 - 1, -1, -1) if hand[k] is not None), None)
        if last is not None and samples[k0].t - samples[last].t <= SHOT_REACH:
            holder_track = hand[last]
            p = _person(samples[last], holder_track)
            bp = points[last]
            near = (p is not None and abs(p.x - cx) <= ATTACK_X
                    and bp.y is not None and abs(bp.y - cy) <= ATTACK_Y * rw * aspect)
            if near or samples[k0].t - samples[last].t < 0.25:
                # Carried to the rim: a dunk or a layup. The drive follows the
                # player and the ball; the basket joins as the ball nears the rim.
                out.append(("attack", t_rim - ATTACK_BEFORE, t_rim - RIM_LEAD, [holder_track]))
                out.append(("rim", t_rim - RIM_LEAD, t_rim, [holder_track]))
                out.append(("result", t_rim, t_rim + RESULT, []))
                continue
            # A shot: up out of the hands, then to the rim.
            air = [k for k in range(last + 1, k0) if ball_mod.usable(points[k])]
            rising = any(points[k].vy <= -RISE for k in air) or (
                len(air) >= 2 and points[air[0]].y is not None and points[air[-1]].y is not None
                and min(points[k].y for k in air) < points[air[0]].y - 0.02)
            release = (samples[last].t + samples[min(last + 1, k0)].t) / 2
            if rising or not air:
                out.append(("release", release - RELEASE_BEFORE, release + RELEASE_AFTER, [holder_track]))
                out.append(("flight", release + RELEASE_AFTER, t_rim - RIM_LEAD, [holder_track]))
        out.append(("rim", t_rim - RIM_LEAD, t_rim, []))
        out.append(("result", t_rim, t_rim + RESULT, []))
    return out


def _passes(samples, points, hand, taken: list[tuple[float, float]]) -> list[tuple[str, float, float, list[int]]]:
    """Passes: the ball leaves one holder and the next holder, within PASS_GAP, is someone else."""
    out = []
    held = [(k, h) for k, h in enumerate(hand) if h is not None]
    for (ka, a), (kb, b) in zip(held, held[1:]):
        if a == b:
            continue
        t0, t1 = samples[ka].t, samples[kb].t
        if t1 - t0 > PASS_GAP or t1 - t0 < PASS_MIN:
            continue
        pa, pb = _person(samples[ka], a), _person(samples[kb], b)
        if pa is not None and pb is not None and abs(pa.x - pb.x) < PASS_DIST:
            continue
        if any(s0 < t1 and t0 < s1 for s0, s1 in taken):
            continue
        out.append(("pass", t0, t1, [a, b]))
    return out


def _transitions(samples: list[Sample]) -> list[tuple[str, float, float, list[int]]]:
    """The camera racing: its pan faster than PAN_FAST for PAN_SPAN or more."""
    out = []
    fast = [False] * len(samples)
    for k in range(1, len(samples)):
        dt = samples[k].t - samples[k - 1].t
        if dt > 0:
            fast[k] = abs(samples[k].pan[0] - samples[k - 1].pan[0]) / dt >= PAN_FAST
    for k0, k1 in _runs(fast):
        t0, t1 = samples[max(0, k0 - 1)].t, samples[k1].t
        if t1 - t0 >= PAN_SPAN:
            out.append(("transition", t0, t1, []))
    return out


def _until_camera_leaves(iv, samples: list[Sample], rel: list[Basket | None]):
    """A result interval, ended where the broadcast's camera leaves its basket:
    after RESULT_HOLD, the first sample where the camera has panned PAN_AWAY
    or more since the result began, in the direction that takes the basket
    toward the frame's edge. A camera panning toward the basket keeps it."""
    kind, t0, t1, actors = iv
    k0 = next((k for k, s in enumerate(samples) if s.t >= t0), None)
    if k0 is None:
        return iv
    for k in range(k0, len(samples)):
        s = samples[k]
        if s.t >= t1:
            break
        b = rel[k] or rel[k0]
        if s.t < t0 + RESULT_HOLD or b is None:
            continue
        moved = s.pan[0] - samples[k0].pan[0]
        if abs(moved) >= PAN_AWAY and moved * (0.5 - (b.rim[0] + b.rim[2]) / 2) > 0:
            return kind, t0, s.t, actors
    return iv


def _hinted(shot: Shot, hints: Hints | None) -> list[tuple[str, float, float, list[int]]]:
    """The shot's phases from the moment alone, when the ball didn't show them."""
    if hints is None or hints.moment_t is None or not shot.t0 <= hints.moment_t < shot.t1 + HINT_RIM:
        return []
    m, kind = hints.moment_t, _kind_of(hints)
    if kind == "three" or kind == "jumper":
        back = HINT_THREE if kind == "three" else HINT_JUMPER
        return [("flight", m - back, m - HINT_RIM, []), ("rim", m - HINT_RIM, m, []), ("result", m, m + RESULT, [])]
    if kind == "attack":
        return [("attack", m - HINT_ATTACK, m - HINT_RIM, []), ("rim", m - HINT_RIM, m, []),
                ("result", m, m + RESULT, [])]
    if kind == "defence":
        return [("rebound", m - HINT_RIM, m + RESULT, [])]
    return []


def phases(shot: Shot, samples: list[Sample], points: list[BallPoint], rows: list[list[Basket]] | None,
           relevant: int | None, hints: Hints | None = None, aspect: float = ASPECT) -> list[Phase]:
    """The phases of one camera shot, in time order, covering it with no
    overlap. `samples` and `points` are the shot's (one point per sample),
    `rows` its baskets per sample (basket.shot_baskets) and `relevant` the
    basket the play is about. A shot that isn't gameplay (close-ups, crowd,
    graphics) is one "reaction" phase when it follows a result, else none."""
    if shot.kind not in ("wide", "medium"):
        return [Phase("reaction", shot.t0, shot.t1, 1.0)] if shot.kind in ("closeup", "crowd") else []
    if not samples:
        return []
    rows = rows or [[] for _ in samples]
    rel = [basket_mod.find(row, relevant) for row in rows]
    hand = holders(samples, points, aspect)
    intervals = _seen_shots(samples, points, hand, rel, aspect)
    seen_shot = any(k in ("rim", "attack") for k, *_ in intervals)
    conf = dict.fromkeys(PRIORITY, 1.0)
    if not seen_shot:
        hinted = _hinted(shot, hints)
        if hinted:
            intervals += hinted
            for k, *_ in hinted:
                conf[k] = HINT_CONF
    intervals = [_until_camera_leaves(iv, samples, rel) if iv[0] == "result" else iv for iv in intervals]
    intervals += _passes(samples, points, hand, [(t0, t1) for k, t0, t1, _ in intervals])
    intervals += _transitions(samples)
    # Possession and loose, sample by sample.
    for k, s in enumerate(samples):
        t0 = s.t if k == 0 else (samples[k - 1].t + s.t) / 2
        t1 = shot.t1 if k == len(samples) - 1 else (s.t + samples[k + 1].t) / 2
        if k == 0:
            t0 = shot.t0
        kind = "possession" if hand[k] is not None or ball_mod.usable(points[k]) else "loose"
        intervals.append((kind, t0, t1, [hand[k]] if hand[k] is not None else []))
    return _paint(shot, intervals, conf, relevant)


def _paint(shot: Shot, intervals, conf: dict, relevant: int | None) -> list[Phase]:
    """The intervals painted on a GRID timeline in PRIORITY order, then merged."""
    n = max(1, int(round((shot.t1 - shot.t0) / GRID)))
    cells: list[tuple | None] = [None] * n
    rank = {k: r for r, k in enumerate(PRIORITY)}
    for kind, t0, t1, actors in sorted(intervals, key=lambda iv: rank.get(iv[0], 99)):
        a = max(0, int((max(t0, shot.t0) - shot.t0) / GRID))
        b = min(n, int(round((min(t1, shot.t1) - shot.t0) / GRID)))
        for c in range(a, b):
            if cells[c] is None:
                cells[c] = (kind, tuple(actors))
    out: list[Phase] = []
    for c, cell in enumerate(cells):
        cell = cell or ("loose", ())
        t0 = shot.t0 + c * GRID
        t1 = shot.t1 if c == n - 1 else shot.t0 + (c + 1) * GRID
        if out and out[-1].kind == cell[0] and tuple(out[-1].actors) == cell[1]:
            out[-1].t1 = t1
            continue
        uses_basket = cell[0] in ("rim", "result", "attack", "rebound", "release", "flight")
        out.append(Phase(cell[0], t0, t1, conf.get(cell[0], 1.0), list(cell[1]),
                         relevant if uses_basket else None))
    return out


def at(phases_: list[Phase], t: float) -> Phase | None:
    """The phase at time t."""
    for p in phases_:
        if p.t0 <= t < p.t1:
            return p
    return phases_[-1] if phases_ and t >= phases_[-1].t1 else None
