"""The crop for every frame of a clip, planned for the whole clip before a
frame is rendered.

What must be in the picture depends on the moment (phases.py). These are the
owner's rules:

- During the play (dribbles, passes, drives) the crop follows the ball and
  whoever has it; the basket may be out of view.
- A pass keeps the ball and moves toward the receiver before the catch.
- When the shot goes up, the crop shows the shooter at the release, then
  follows the ball's flight to the basket.
- From just before the ball reaches the rim (phases.RIM_LEAD) until the
  result, the whole basket (rim, net and backboard, with a margin past the
  backboard: basket.unit) is in the picture: the crop's edge sits past the
  backboard, never through the rim. The same for a dunk or a layup and a
  block or a rebound at the rim.
- The crop is 9:16 from the first frame to the last, as on his House of
  Highlights reference: it pans, it never zooms (the TV scoreboard's rows,
  when left out, are the clip's own band, set once). When what is
  required can't all fit (the shooter in the corner and the basket), it
  keeps the side the play is on (the ball, weighted most) and pans with the
  ball to the rim. It used to widen there (4:5, 1:1, the whole frame over a
  blurred copy), and to zoom out whenever the ball was lost: "I don't like
  the zoom in and out".
- Each camera shot is planned on its own: the frame before a cut keeps the
  old crop and the first one after it snaps (no frame blended across).
- Close-ups and crowd shots: the person the camera is on (the biggest, the
  nearer the middle the better: the biggest alone was often a blurred
  shoulder in front, the crop on an ear or the back of a head), held at
  9:16 like the rest; so is a graphic, in the middle (shown whole it was a
  zoom out: the back of a head nobody was found in read as one).

The crop looks ahead (its focus leans toward where things will be a fraction
of a second later) and is smoothed with a centred average, so it moves before
the ball arrives instead of chasing it, then held to a speed and acceleration
a viewer can follow.

When the ball is found in too few of a shot's samples, the crop follows
the players around the play and where the broadcast's camera points (it
follows the ball): assisted when the ball is seen some of the time, players
when hardly ever, the camera alone (wide) with no floor players at all. It
used to widen to 4:5, 1:1 and the whole frame there and hold half the floor
players: on a team's own network, where the detector found the ball in 3 of
12 frames, nearly every clip zoomed out with the ball and its handler
already in the picture, and again after the basket as the players spread
out. validate.py checks every frame; what still lags is repaired here with
a quicker crop, never a wider one.

Pure Python.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from sports.basketball.crop import ball as ball_mod
from sports.basketball.crop import basket as basket_mod
from sports.basketball.crop import phases as phases_mod
from sports.basketball.crop import players as players_mod
from sports.basketball.crop import validate
from sports.basketball.crop.model import BallPoint, Basket, Cut, Geometry, Phase, Plan, Sample, Shot

ASPECT = 16 / 9

# What must be in the picture.
BALL_MARGIN = 0.03       # frame widths around the ball (about four of its diameters on a broadcast)
ITEM_MARGIN = 0.025      # frame widths around a player's box
RECEIVER_BEFORE = 0.2    # seconds before the catch the receiver must be in (the look-ahead leans there earlier)
SHOOTER_AFTER = 0.3      # seconds after the release the shooter stays required
CORE_SHARE = 0.5         # with no ball: the floor players nearest their median, this share of them
LOOK = {"possession": 0.3, "pass": 0.4, "release": 0.2, "flight": 0.6, "transition": 0.8, "loose": 0.5,
        "rim": 0.0, "result": 0.0, "attack": 0.2, "rebound": 0.2}
LOOK_SHARE = 0.6         # the focus's share taken from where things will be after the look-ahead
LEAD = 0.12              # crop widths ahead of a moving ball, at LEAD_SPEED and faster
LEAD_SPEED = 1.0         # frame widths per second
TRANSITION_LEAD = 0.15   # crop widths ahead of the ball in a transition, in the camera's direction
# A short or mid-range shot shows the shooter and the rim together from the release (2 of 2 on
# broadcasters' published vertical highlights); a jump shot from distance never had the rim in at the release (0/6), it comes
# in during the flight (the "rim" phase, phases.RIM_LEAD). Short: the shooter's box and the basket's
# unit, at the release, fit in this share of the crop's width (0.85 of the picture under the top band
# is about a full-height 9:16 crop, the broadcasters' own).
SHORT_FIT = 0.85
# The right rail: the apps' buttons run down the right of the screen, and broadcasters keep the rim
# left of it with the ball at the rim (past x 0.81 in 2 of 37 shots on their published vertical
# highlights). While the basket is required, so is this much of the crop's width right of the
# rim's centre, when it fits with everything else required.
RAIL = 0.19

# Zoom levels.
ZOOM_EARLY = 0.5         # seconds: a wider level is reached by the time it is needed (the ease starts this early)
ZOOM_IN_AFTER = 0.6      # seconds the need must stay below the narrower level before narrowing...
ZOOM_IN_SLACK = 0.03     # ...by at least this (frame widths)
LEVEL_HOLD = 0.8         # seconds between two level changes (narrowing; widening when needed is never held)
EASE_OUT = 0.5           # seconds to widen
EASE_IN = 0.8            # seconds to narrow

# Smoothing (per shot, per frame).
MEDIAN_S = 0.3
DEAD_ZONE = 0.18         # crop widths the target may move before the crop follows (unless something leaves it)
AVERAGE_S = 0.5
AVERAGE_PASSES = 2
V_MAX = 1.5              # frame widths per second
ROUND_S = 0.2            # a last centred average this long rounds off the speed limit's corners, which
A_MAX = V_MAX / ROUND_S  # bounds the acceleration (frame widths per second per second) without the
                         # momentum an acceleration limit has: that made the crop overshoot the play and
                         # come back on a fast drive (job 210, a clip with the camera racing)
LIMIT_PASSES = 2
FAST = 2.0               # a repair lets the crop move this many times faster before it widens (a quick pan
                         # beats a zoom-out when the crop only lagged)

# Fallback, by the share of a shot's samples with a usable ball.
TRACKED_SHARE = 0.40
ASSISTED_SHARE = 0.15
MODE_LEVEL = {"tracked": 0, "assisted": 0, "players": 0, "wide": 0}
CAMERA_W = 1.0           # with no ball, the camera's aim (the frame's middle) steers as much as the players...
HOLDER_HOLD = 2.5        # ...and the player who last had it, seen this recently, steers twice as much (a
HOLDER_W = 2.0           #    bring-up with the ball lost: the crop sat on the floor between him and a coach)...
MOVER_SPEED = 0.25       # ...else a floor player crossing the court this fast (frame widths/s, the camera's pan
MOVER_BACK = 0.4         #    taken out, over this many seconds) steers as the holder would: the play moves
SUBJECT_MIDDLE = 1.2     # a close-up's subject: the biggest box times (this - its distance from the middle)
NEXT_MODE = {"tracked": "assisted", "assisted": "players", "players": "wide", "wide": "wide"}
REPAIR_ROUNDS = 3
REPAIR_PAD = 0.25        # seconds either side of a failing span that gets the wider level
REPAIR_MIN = 4           # frames: a shorter failing run is reported, not widened for
BALL_NEAR = 1.0          # seconds: with no usable ball now, the crop aims at the nearest usable one this close
# A close-up keeps one subject (SUBJECT_KEEP, frame widths: the nearest to the last one, this close),
# and holds still on him while he stays within HOLD_SPREAD (frame widths, the 10th to the 90th
# percentile of his head's x): picking the biggest person again on every frame swapped between him
# and whoever leaned in, and the crop kept correcting.
SUBJECT_KEEP = 0.2
HOLD_SPREAD = 0.12

SHOT_PHASES = {"release", "flight", "rim", "result", "attack", "rebound"}
NO_NARROWING = {"release", "flight", "rim"}   # the crop never narrows while a shot is in the air
BASKET_PHASES = {"rim", "result", "rebound"}


@dataclass
class ShotData:
    """What plan_clip worked out for one camera shot."""

    shot: Shot
    samples: list[Sample]
    points: list[BallPoint] = field(default_factory=list)
    rows: list[list[Basket]] = field(default_factory=list)
    relevant: int | None = None
    phases: list[Phase] = field(default_factory=list)
    short: dict = field(default_factory=dict)    # a release phase's (t0, t1) -> a short shot (_short_shot)


@dataclass
class _Target:
    """One sample's wish: where the crop should centre, the span it must hold."""

    t: float
    focus: float
    req: tuple[float, float] | None
    need: int                # the narrowest level that holds `req`
    mode: str
    conf: float
    phase: str
    basket: tuple[float, float] | None = None


# ---- what one sample needs ------------------------------------------------------------------


def _span_box(p) -> tuple[float, float]:
    return p.x - p.w / 2 - ITEM_MARGIN, p.x + p.w / 2 + ITEM_MARGIN


def _span_ball(bp: BallPoint) -> tuple[float, float]:
    return bp.x - BALL_MARGIN, bp.x + BALL_MARGIN


def _person(sample: Sample, track: int | None):
    if track is None:
        return None
    return next((p for p in sample.people if p.track == track), None)


def _person_near(data: ShotData, k: int, track: int | None, t_max: float):
    """The tracked person at sample k, else at the nearest later sample up to t_max."""
    for j in range(k, len(data.samples)):
        if data.samples[j].t > t_max:
            break
        p = _person(data.samples[j], track)
        if p is not None:
            return p
    return None


def _core(sample: Sample) -> tuple[tuple[float, float], float] | None:
    """The floor players nearest their median: their span and that median."""
    floor = players_mod.floor_players(sample)
    if not floor:
        return None
    med = statistics.median(p.x for p in floor)
    near = sorted(floor, key=lambda p: abs(p.x - med))[:max(1, math.ceil(CORE_SHARE * len(floor)))]
    return (min(p.x - p.w / 2 for p in near) - ITEM_MARGIN, max(p.x + p.w / 2 for p in near) + ITEM_MARGIN), med


def _union(spans: list[tuple[float, float]]) -> tuple[float, float] | None:
    spans = [s for s in spans if s is not None]
    if not spans:
        return None
    return min(s[0] for s in spans), max(s[1] for s in spans)


def _fits(spans: list[tuple[float, float]], width: float) -> bool:
    """All of the spans fit in one crop `width` wide."""
    span = _union(spans)
    return span is None or span[1] - span[0] <= width + 1e-9


def _short_shot(data: ShotData, ph: Phase | None, width: float) -> bool:
    """The shot whose release or flight `ph` is was taken from close enough
    for the shooter and the basket to be in the picture together (SHORT_FIT,
    as broadcasters frame them): the median over its release's samples where both are seen. One
    answer per shot, so what the crop must hold doesn't flicker as the
    shooter's box or the camera moves."""
    if ph is None or ph.kind not in ("release", "flight"):
        return False
    rel = ph if ph.kind == "release" else next(
        (p for p in reversed(data.phases) if p.kind == "release" and p.t1 <= ph.t0 + 1e-6
         and ph.t0 - p.t1 <= phases_mod.GRID + 1e-6), None)
    if rel is None or not rel.actors:
        return False
    key = (rel.t0, rel.t1)
    if key not in data.short:
        widths = []
        for k, s in enumerate(data.samples):
            if not rel.t0 <= s.t < rel.t1 or k >= len(data.rows):
                continue
            p, b = _person(s, rel.actors[0]), basket_mod.find(data.rows[k], data.relevant)
            if p is not None and b is not None and b.in_frame:
                span = _union([_span_box(p), b.unit])
                widths.append(span[1] - span[0])
        data.short[key] = bool(widths) and statistics.median(widths) <= SHORT_FIT * width
    return data.short[key]


def _need(levels: list[float], span: tuple[float, float] | None, floor: int) -> int:
    if span is None:
        return floor
    width = span[1] - span[0]
    for i, lv in enumerate(levels):
        if i >= floor and lv >= width - 1e-9:
            return i
    return len(levels) - 1


def _last_holder(data: ShotData, k: int, aspect: float):
    """With no usable ball at sample k: the player who held it at the last
    sample that had one, at most HOLDER_HOLD before, where he is now; None
    when there is none or he isn't seen."""
    t = data.samples[k].t
    for j in range(k - 1, -1, -1):
        if t - data.samples[j].t > HOLDER_HOLD:
            return None
        if j < len(data.points) and ball_mod.usable(data.points[j]):
            return _person(data.samples[k], players_mod.handler(data.samples[j], data.points[j], aspect))
    return None


def _mover(data: ShotData, k: int):
    """The floor player crossing the court fastest at sample k, MOVER_SPEED
    or faster over the last MOVER_BACK (the camera's pan taken out); None."""
    s = data.samples[k]
    j = k
    while j > 0 and s.t - data.samples[j - 1].t <= MOVER_BACK:
        j -= 1
    if j == k:
        return None
    then = data.samples[j]
    dt = max(1e-3, s.t - then.t)
    best, speed = None, MOVER_SPEED
    for p in players_mod.floor_players(s):
        q = _person(then, p.track)
        if q is None:
            continue
        v = abs((p.x + s.pan[0]) - (q.x + then.pan[0])) / dt
        if v >= speed:
            best, speed = p, v
    return best


def _wish(data: ShotData, k: int, mode: str, levels: list[float], aspect: float) -> tuple:
    """One sample's required spans and weighted focus points, by its phase."""
    s, bp = data.samples[k], data.points[k] if k < len(data.points) else BallPoint(t=data.samples[k].t)
    ph = phases_mod.at(data.phases, s.t)
    kind = ph.kind if ph is not None else "loose"
    usable = ball_mod.usable(bp)
    req: list[tuple[float, float]] = []
    focus: list[tuple[float, float]] = []           # (x, weight)
    unit = None
    if usable:
        # After the ball reaches the rim the basket is what must stay; the ball
        # falling away from the net steers the crop but doesn't widen it.
        if kind != "result":
            req.append(_span_ball(bp))
        focus.append((bp.x, 2.0))
    holder = players_mod.handler(s, bp, aspect) if usable else None
    if kind in ("possession", "transition", "attack") and holder is not None:
        p = _person(s, holder)
        if p is not None:
            req.append(_span_box(p))
            focus.append((p.x, 1.0))
    if kind == "pass" and ph is not None and len(ph.actors) == 2:
        passer, receiver = ph.actors
        if s.t <= ph.t0 + 0.05:
            p = _person(s, passer)
            if p is not None:
                req.append(_span_box(p))
                focus.append((p.x, 1.0))
        if s.t >= ph.t1 - RECEIVER_BEFORE:
            p = _person_near(data, k, receiver, ph.t1 + 0.5)
            if p is not None:
                req.append(_span_box(p))
                focus.append((p.x, 1.5))
    if kind == "release" and ph is not None and ph.actors:
        p = _person(s, ph.actors[0])
        if p is not None and s.t <= ph.t1 + SHOOTER_AFTER:
            req.append(_span_box(p))
            focus.append((p.x, 1.0))
    if kind in ("release", "flight") and _short_shot(data, ph, levels[0]):
        # A short shot: the basket with the shooter at the release. It
        # steers from the release phase's start, as it does at the rim, so the crop
        # is there by the release; the ball, weighted twice, keeps the crop on the
        # play.
        b = basket_mod.find(data.rows[k], data.relevant) if k < len(data.rows) else None
        if b is not None and b.in_frame and _fits([*req, b.unit], levels[0]):
            focus.append(((b.unit[0] + b.unit[1]) / 2, 1.0))
            if kind == "flight" or s.t >= ph.t0 + phases_mod.RELEASE_BEFORE:
                req.append(b.unit)
    if kind in BASKET_PHASES:
        b = basket_mod.find(data.rows[k], data.relevant) if k < len(data.rows) else None
        if b is not None and b.in_frame:
            unit = b.unit
            req.append(unit)
            focus.append(((unit[0] + unit[1]) / 2, 1.0))
            # The rim kept out of the right rail where the crop can.
            rim_x = (b.rim[0] + b.rim[2]) / 2
            rail = (rim_x, min(1.0, rim_x + RAIL * levels[0]))
            if _fits([*req, rail], levels[0]):
                req.append(rail)
    if kind == "transition" and usable:
        sign = 1.0 if (s.pan[0] - data.samples[max(0, k - 1)].pan[0]) >= 0 else -1.0
        focus.append((bp.x + sign * TRANSITION_LEAD * levels[MODE_LEVEL.get(mode, 0)], 1.0))
    if mode in ("assisted", "players") or not focus:
        # The players around the play only steer the crop, never widen it
        # (requiring half the floor zoomed out for every missed detection, and
        # after a basket as the players spread out).
        core = _core(s)
        if core is not None:
            focus.append((core[1], 1.0))
        if mode in ("assisted", "players") and not usable:
            # The broadcast's camera follows the ball: where it points is the
            # best guess at the play when the detector doesn't see the ball,
            # after the player who last had it.
            focus.append((0.5, CAMERA_W))
            held = _last_holder(data, k, aspect) or _mover(data, k)
            if held is not None:
                focus.append((held.x, HOLDER_W))
    if mode == "wide":
        req, focus = [], [(0.5, 1.0)]        # no floor players: where the camera points
    lead = 0.0
    if usable and kind not in BASKET_PHASES:
        vx = bp.vx / aspect
        lead = LEAD * math.copysign(min(1.0, abs(vx) / LEAD_SPEED), vx) if vx else 0.0
    point = (sum(x * wgt for x, wgt in focus) / sum(wgt for _, wgt in focus)) if focus else None
    conf = bp.conf if usable else (phases_mod.HINT_CONF if kind in SHOT_PHASES else 0.5 if req else 0.2)
    led = "tracked" if (usable and mode == "tracked") else ("players" if mode == "tracked" else mode)
    return kind, _union(req), point, lead, unit, conf, led


def _targets(data: ShotData, mode: str, levels: list[float], aspect: float) -> list[_Target]:
    floor = MODE_LEVEL[mode]
    raw = [_wish(data, k, mode, levels, aspect) for k in range(len(data.samples))]
    out: list[_Target] = []
    times = [s.t for s in data.samples]
    # Where a usable ball is, for the samples between (the first samples of a
    # shot, before the ball's path is confirmed, and short gaps).
    usable = [k for k in range(len(data.points)) if ball_mod.usable(data.points[k])]
    points: list[float | None] = []
    for k, (kind, req, point, lead, unit, conf, led) in enumerate(raw):
        if req is None and usable and mode == "tracked":
            near = min(usable, key=lambda j: abs(times[j] - times[k]))
            if abs(times[near] - times[k]) <= BALL_NEAR:
                point = data.points[near].x
        points.append(point)
    last_point = 0.5
    for k, (kind, req, _point, lead, unit, conf, led) in enumerate(raw):
        point = points[k] if points[k] is not None else last_point
        last_point = point
        # Look ahead: lean toward where the focus will be LOOK[kind] later (the same
        # points, so a ball remembered now isn't pulled off by the players a moment on).
        ahead = times[k] + LOOK.get(kind, 0.3)
        j = k
        while j + 1 < len(times) and times[j + 1] <= ahead:
            j += 1
        future = points[j] if points[j] is not None else point
        need = _need(levels, req, floor)
        width = levels[need]
        focus = (1 - LOOK_SHARE) * point + LOOK_SHARE * future + lead * width
        out.append(_Target(times[k], focus, req, need, led, conf, kind, unit))
    return out


# ---- levels ---------------------------------------------------------------------------------


def _levels_per_sample(targets: list[_Target], floor: int) -> list[int]:
    """Each sample's level: widened ZOOM_EARLY before it is needed, narrowed
    only after the need has stayed lower for ZOOM_IN_AFTER, at most once per
    LEVEL_HOLD, and never during a shot (release to result)."""
    n = len(targets)
    if not n:
        return []
    early = []
    for k in range(n):
        horizon = targets[k].t + ZOOM_EARLY
        want = targets[k].need
        j = k + 1
        while j < n and targets[j].t <= horizon:
            want = max(want, targets[j].need)
            j += 1
        early.append(max(floor, want))
    cur, last_change = early[0], -math.inf
    out = []
    for k in range(n):
        t, want = targets[k].t, early[k]
        if want > cur:
            cur, last_change = want, t
        elif want < cur and targets[k].phase not in NO_NARROWING and t - last_change >= LEVEL_HOLD:
            since = [targets[j] for j in range(k + 1) if targets[j].t >= t - ZOOM_IN_AFTER]
            settled = since and since[0].t <= t - ZOOM_IN_AFTER + 0.11
            if settled and all(x.need < cur for x in since):
                cur, last_change = max(want, max(x.need for x in since)), t
        out.append(cur)
    return out


def _ease(u: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, u)))


def _widths(frames: range, fps: float, times: list[float], lvl: list[int], levels: list[float]) -> list[float]:
    """Per-frame width: each level change eased (EASE_OUT widening, EASE_IN narrowing)."""
    changes = [(times[k], levels[lvl[k - 1]], levels[lvl[k]]) for k in range(1, len(lvl)) if lvl[k] != lvl[k - 1]]
    start = levels[lvl[0]] if lvl else levels[0]
    out = []
    for f in frames:
        t = f / fps
        w = start
        for tc, a, b in changes:
            if t < tc:
                break
            span = EASE_OUT if b > a else EASE_IN
            w = a + (b - a) * _ease((t - tc) / span)
        out.append(w)
    return out


# ---- smoothing ------------------------------------------------------------------------------


def _interp(times: list[float], values: list, t: float):
    if t <= times[0]:
        return values[0]
    if t >= times[-1]:
        return values[-1]
    lo, hi = 0, len(times) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if times[mid] <= t:
            lo = mid
        else:
            hi = mid
    a, b = values[lo], values[hi]
    if a is None or b is None:
        return a if a is not None else b
    u = (t - times[lo]) / max(1e-9, times[hi] - times[lo])
    if isinstance(a, tuple):
        return tuple(x + (y - x) * u for x, y in zip(a, b))
    return a + (b - a) * u


def _median(xs: list[float], half: int) -> list[float]:
    n = len(xs)
    return [statistics.median(xs[max(0, i - half):min(n, i + half + 1)]) for i in range(n)]


def _average(xs: list[float], half: int) -> list[float]:
    n = len(xs)
    if n == 0 or half <= 0:
        return list(xs)
    pre = [0.0]
    for x in xs:
        pre.append(pre[-1] + x)
    return [(pre[min(n, i + half + 1)] - pre[max(0, i - half)]) / (min(n, i + half + 1) - max(0, i - half))
            for i in range(n)]


def _limit(xs: list[float], fps: float, speed: float = 1.0) -> list[float]:
    """The speed limit, forward then backward: forward keeps the crop from
    racing ahead, backward lets it set off early enough to arrive (no lag
    either way, and no momentum, so it never overshoots)."""
    v_max = V_MAX * speed / fps
    out = list(xs)
    for _ in range(LIMIT_PASSES):
        for i in range(1, len(out)):
            out[i] = max(out[i - 1] - v_max, min(out[i - 1] + v_max, out[i]))
        for i in range(len(out) - 2, -1, -1):
            out[i] = max(out[i + 1] - v_max, min(out[i + 1] + v_max, out[i]))
    return out


def _contain(cx: float, w: float, req: tuple[float, float] | None) -> float:
    """The nearest centre to cx whose crop holds req; when req is wider than
    the crop, the nearest to cx inside req (the side the play is on: cx is
    weighted to the ball), not req's middle, which held neither the shooter
    nor the basket."""
    if req is None:
        return cx
    if req[1] - req[0] >= w:
        return min(max(cx, req[0] + w / 2), req[1] - w / 2)
    return min(max(cx, req[1] - w / 2), req[0] + w / 2)


def _smooth(targets: list[float], reqs: list, widths: list[float], fps: float, scale: float = 1.0,
            speed: float = 1.0) -> list[float]:
    """Median, dead zone, centred average, limits, then the frame. A repair
    shrinks the windows (`scale`) and raises the limits (`speed`)."""
    n = len(targets)
    if n == 0:
        return []
    xs = _median(targets, max(0, int(round(MEDIAN_S * fps * scale / 2))))
    held, out = xs[0], []
    for f in range(n):
        w = widths[f]
        if abs(xs[f] - held) > DEAD_ZONE * w or validate.inside(held, w, reqs[f]) is False:
            held = xs[f]
        out.append(held)
    for _ in range(AVERAGE_PASSES):
        out = _average(out, max(0, int(round(AVERAGE_S * fps * scale / 2))))
    out = _limit(out, fps, speed)
    # Hold what is required, then the limit again and a short average over its corners
    # (they may undo a little; validate tells).
    for _ in range(2):
        out = [_contain(x, widths[f], reqs[f]) for f, x in enumerate(out)]
        out = _limit(out, fps, speed)
        out = _average(out, max(0, int(round(ROUND_S * fps / 2))))
    return [min(max(x, widths[f] / 2), 1 - widths[f] / 2) for f, x in enumerate(out)]


# ---- one shot -------------------------------------------------------------------------------


def _usable_share(data: ShotData) -> float:
    if not data.points:
        return 0.0
    return sum(1 for bp in data.points if ball_mod.usable(bp)) / len(data.points)


def _mode_of(data: ShotData) -> str:
    share = _usable_share(data)
    if share >= TRACKED_SHARE:
        return "tracked"
    if share >= ASSISTED_SHARE:
        return "assisted"
    if any(players_mod.floor_players(s) for s in data.samples):
        return "players"
    return "wide"


def _person_shot(data: ShotData, fps: float, levels: list[float]) -> tuple[list[float], list[float]]:
    """A close-up or crowd shot: the biggest person, followed through the
    shot as smoothly as the play is (a player walking off after a basket
    stays in the picture; held, he was cut in half), the middle when no one
    is found. The person is the one the camera is on: the biggest, the
    nearer the middle the better (SUBJECT_MIDDLE), centred on his head
    (Person.head_x) when the pose model found it, else on his box."""
    n = data.shot.f1 - data.shot.f0

    def subject(people):
        return max(people, key=lambda p: p.w * p.h * (SUBJECT_MIDDLE - abs(p.x - 0.5)))

    def where(p):
        return p.head_x if p.head_x is not None else p.x      # the face, when the pose model found it

    # The subject is chosen once, as the one the camera is on most of the shot (the median of who
    # looks it sample by sample), and followed from there both ways: on each sample the person
    # nearest to where he last was (SUBJECT_KEEP), else he is taken to be still there. A sample
    # where someone else leans in bigger doesn't hand the close-up to him.
    seen = [(k, s) for k, s in enumerate(data.samples) if s.people]
    xs: dict[int, float] = {}
    if seen:
        picks = {k: where(subject(s.people)) for k, s in seen}
        usual = statistics.median(picks.values())
        k0 = min(picks, key=lambda k: abs(picks[k] - usual))
        xs[k0] = picks[k0]
        for order in (range(k0 + 1, len(data.samples)), range(k0 - 1, -1, -1)):
            last = xs[k0]
            for k in order:
                people = data.samples[k].people
                near = min(people, key=lambda p: abs(where(p) - last), default=None)
                if near is not None and abs(where(near) - last) <= SUBJECT_KEEP:
                    last = where(near)
                xs[k] = last
    keys = [(data.samples[k].frame - data.shot.f0, xs[k]) for k in sorted(xs)]
    base = levels[0]
    if not keys:
        return [min(max(0.5, base / 2), 1 - base / 2)] * n, [base] * n
    spread = sorted(x for _, x in keys)
    if spread[int(0.9 * (len(spread) - 1))] - spread[int(0.1 * (len(spread) - 1))] <= HOLD_SPREAD:
        # He hardly moves: hold still on him, as a camera operator would.
        held = min(max(statistics.median(spread), base / 2), 1 - base / 2)
        return [held] * n, [base] * n
    targets, j = [], 0
    for f in range(n):
        while j + 1 < len(keys) and keys[j + 1][0] <= f:
            j += 1
        (f0, x0), nxt = keys[j], keys[j + 1] if j + 1 < len(keys) else None
        targets.append(x0 if f <= f0 or nxt is None else x0 + (nxt[1] - x0) * (f - f0) / (nxt[0] - f0))
    return _smooth(targets, [None] * n, [base] * n, fps), [base] * n


def plan_shot(data: ShotData, geometry: Geometry, aspect: float = ASPECT) -> dict:
    """The crop for one camera shot: per-frame cx, w, conf, mode, the span
    each frame must hold and which frames need the basket."""
    # One level, 9:16: the crop pans, it never zooms (the owner's reference).
    shot, fps, levels = data.shot, geometry.fps, geometry.levels()[:1]
    frames = range(shot.f0, shot.f1)
    n = len(frames)
    if shot.kind in ("closeup", "crowd") or (shot.kind not in ("wide", "medium") and shot.kind != "graphic"):
        cx, w = _person_shot(data, fps, levels)
        return {"cx": cx, "w": w, "conf": [0.8] * n, "mode": ["person"] * n, "req": [None] * n,
                "basket": [False] * n, "gameplay": [False] * n, "reasons": [f"shot {shot.id}: {shot.kind}"]}
    if shot.kind == "graphic" or not data.samples:
        return {"cx": [0.5] * n, "w": [levels[0]] * n, "conf": [0.5] * n, "mode": ["wide"] * n, "req": [None] * n,
                "basket": [False] * n, "gameplay": [False] * n, "reasons": [f"shot {shot.id}: {shot.kind}"]}
    mode = _mode_of(data)
    reasons = [f"shot {shot.id}: {shot.kind}, ball usable in {_usable_share(data):.0%} of samples -> {mode}"]
    extra: dict[int, int] = {}            # sample index -> level floor raised by a repair
    result, best, best_bad = None, None, None
    for attempt in range(REPAIR_ROUNDS + 3):
        targets = _targets(data, mode, levels, aspect)
        for k, raise_to in extra.items():
            targets[k].need = max(targets[k].need, raise_to)
        lvl = _levels_per_sample(targets, MODE_LEVEL[mode])
        times = [x.t for x in targets]
        widths = _widths(frames, fps, times, lvl, levels)
        reqs = [_interp(times, [x.req for x in targets], f / fps) for f in frames]
        foci = [_interp(times, [x.focus for x in targets], f / fps) for f in frames]
        aims = [_contain(foci[i], widths[i], reqs[i]) for i in range(n)]
        scale, speed = (1.0, 1.0) if attempt == 0 else (0.5, FAST)
        cx = _smooth(aims, reqs, widths, fps, scale, speed)
        phase_kind = [_nearest(targets, f / fps).phase for f in frames]
        bad = validate.failures(cx, widths, reqs)
        result = {"cx": cx, "w": widths, "conf": [_nearest(targets, f / fps).conf for f in frames],
                  "mode": [_nearest(targets, f / fps).mode for f in frames], "req": reqs,
                  "basket": [k in BASKET_PHASES and reqs[i] is not None for i, k in enumerate(phase_kind)],
                  "gameplay": [True] * n, "reasons": reasons}
        if best is None or len(bad) < best_bad:
            best, best_bad = result, len(bad)
        runs = [(a, b) for a, b in validate.spans(bad, 0, n) if b - a >= REPAIR_MIN]
        if not runs:
            break
        if attempt == 0:
            # First, a quicker crop: it may simply have lagged.
            reasons.append(f"shot {shot.id}: {len(bad)} frame(s) failing, a quicker crop")
            continue
        # Wider only where what is required doesn't fit the crop's width; a crop
        # that only lagged is not zoomed out for it (the quality reports it).
        tight = [f for a0, b0 in runs for f in range(a0, b0)
                 if reqs[f] is not None and reqs[f][1] - reqs[f][0] > widths[f] - 2 * validate.SLACK]
        if tight and attempt <= REPAIR_ROUNDS:
            pad = int(round(REPAIR_PAD * fps))
            for a, b in validate.spans(tight, pad, n):
                t0, t1 = (shot.f0 + a) / fps, (shot.f0 + b) / fps
                for k, x in enumerate(targets):
                    if t0 - 1e-9 <= x.t <= t1 + 1e-9:
                        extra[k] = min(len(levels) - 1, max(extra.get(k, 0), lvl[k] + 1))
            reasons.append(f"shot {shot.id}: repair {attempt}, {len(bad)} frame(s) failing, wider there")
            continue
        if tight and mode != "wide" and len(levels) > 1 and len(tight) > validate.UNCERTAIN * n:
            mode, extra = NEXT_MODE[mode], {}
            reasons.append(f"shot {shot.id}: still {len(tight)} frame(s) too wide for the crop -> {mode}")
            continue
        if len(bad):
            reasons.append(f"shot {shot.id}: {best_bad} frame(s) still lag behind what is required")
        break
    return best if best is not None else result


def _nearest(targets: list[_Target], t: float) -> _Target:
    lo, hi = 0, len(targets) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if targets[mid].t <= t:
            lo = mid
        else:
            hi = mid
    return targets[lo] if abs(targets[lo].t - t) <= abs(targets[hi].t - t) else targets[hi]


# ---- the clip -------------------------------------------------------------------------------


def plan(geometry: Geometry, shots: list[ShotData], cuts: list[Cut], notes: list[str] | None = None,
         aspect: float | None = None) -> Plan:
    """The crop for every frame of the clip, shot by shot, with its quality."""
    aspect = aspect or geometry.aspect
    fps, levels = geometry.fps, geometry.levels()
    total = geometry.frames or (shots[-1].shot.f1 if shots else 0)
    cx = [0.5] * total
    w = [levels[0]] * total
    conf = [0.0] * total
    mode = ["players"] * total
    req: list = [None] * total
    basket_need = [False] * total
    gameplay = [False] * total
    reasons: list[str] = []
    for data in shots:
        got = plan_shot(data, geometry, aspect)
        for i, f in enumerate(range(data.shot.f0, min(data.shot.f1, total))):
            cx[f], w[f], conf[f], mode[f] = got["cx"][i], got["w"][i], got["conf"][i], got["mode"][i]
            req[f], basket_need[f], gameplay[f] = got["req"][i], got["basket"][i], got["gameplay"][i]
        reasons += got["reasons"]
    failing = validate.failures(cx, w, req)
    basket_frames = [f for f in range(total) if basket_need[f]]
    failing_set = set(failing)
    basket_failing = [f for f in basket_frames if f in failing_set]
    quality = validate.quality(mode, gameplay, failing, basket_frames, basket_failing, levels[0], w,
                               notes or [], len(cuts), fps)
    # The blurred bands around the picture: the top one given, the rest below it; a geometry
    # with only a picture share (from before the bottom band) keeps all of its band on top.
    spare = round(1.0 - geometry.picture, 4) if geometry.picture < 1.0 else 0.0
    band_top = min(spare, round(geometry.band_top, 4)) if geometry.band_top else spare
    return Plan(fps=fps, base=levels[0], cx=cx, w=w, conf=conf, mode=mode, cuts=list(cuts),
                shots=[d.shot for d in shots], quality=quality, reasons=reasons,
                band_top=band_top, band_bottom=round(spare - band_top, 4))
