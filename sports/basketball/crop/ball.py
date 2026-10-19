"""One ball path per camera shot, and how sure each point of it is.

The detector judges one frame at a time and finds the real ball in only some
frames, among floor logos, heads in the crowd and orange shoes. So, per shot:

1. gate(): every candidate gets a score, or a reason it can't be the ball;
2. tracklets(): short tracks in world coordinates (the camera's travel taken
   out, see model.py), predicted with constant velocity, or a parabola once
   the ball is plainly in flight;
3. choose_chain(): one plausible chain of tracklets, by dynamic programming.
   A jump no ball could make starts the chain again (a "restart") instead of
   being bridged;
4. statuses(): every sample says where the ball is and how that is known:
   detected, interpolated (inside a tracklet, across a short flight, or
   carried in a player's hands), predicted (a little past either end of what
   was seen; look-ahead only) or missing.

track_shot() runs all four. Run it per shot: nothing is bridged across a cut.
A candidate's `score` and `rejected` stay on it for the debug output. Reject
letters: s size, a shape, b in the score bug, f bottom edge of a wide shot,
t a fixed mark (static), h a spectator's head, l scored below ENTER.

Pure Python (see model.py).
"""

from __future__ import annotations

import bisect
import contextvars
import math
import statistics
from collections import Counter
from dataclasses import dataclass, field

from sports.basketball.crop import players
from sports.basketball.crop.model import BallCand, BallPoint, Person, Sample
from sports.basketball.crop.shots import TALL

ASPECT = 16 / 9          # the frame's width over its height when the caller doesn't say

# ---- the gate ---------------------------------------------------------------------------------

BALL_REL = 0.12          # a ball is about this share of a floor player's height across
SIZE_FREE = (0.004, 0.05)   # with no players to measure by, any diameter (frame heights) in here
SIZE_RANGE = (0.45, 2.2)    # a candidate's diameter within these multiples of the expected one
SHAPE = (0.6, 1.67)      # a round ball's box is this wide over tall (pixels)...
SHAPE_BLUR = 2.2         # ...or up to this (and its inverse) when the detector is sure: motion blur
SHAPE_BLUR_CONF = 0.3
EDGE_Y = 0.8             # in a wide shot, nothing lower in the frame than this is the ball
GRID = (128, 72)         # the static test's cells across and down (small, see static_cells)...
GRIDS = ((0.0, 0.0), (0.5, 0.0), (0.0, 0.5), (0.5, 0.5))   # ...in four grids, offset by half a cell
STATIC_MIN = 4           # a cell hit in at least this many samples, and this share of those it could be
STATIC_SHARE = 0.3       # hit in (in view, or not behind a player), holds something fixed (a logo, a light)
FIXED_SLACK = 0.5        # seconds: a fixed cell holds this long before its first hit and after its last
COURT_EXPLAINS = 0.9     # a fixed picture cell this share of whose hits a fixed court cell explains is a
#                          floor mark seen through a still camera, left to the court cell
STILLISH = 2             # a cell hit this many times is static-ish: the motion term halves
HANDS_PART = 0.6         # a floor player's hands region: the top part of his box, this share of it
HANDS_NEAR = 0.06        # the context term: within this (frame widths) of a hands region...
CONTEXT_OFF = 0.3        # ...or inside the court's rows, else this
FEET_PART = 0.2          # the at_feet penalty: the bottom part of a person's box...
FEET_BELOW = 0.1         # ...and this share of his height below it (a shoe sticks out of a loose box)
HEAD_PART = 0.12         # the on_head penalty: the top part of a person's box
FAN_HEAD = (0.1, 0.25)   # a spectator's head: from this share of his height above his box's top to this below
FAN_MEMORY = 2.0         # seconds: a spectator seen this close in time still sits there (the detector
#                          misses people in the stands in a good share of frames, sometimes for a second)
FAN_ROUND = 0.004        # frame fractions: remembered spectators' boxes are rounded to this, and kept once
W_CONF, W_SIZE, W_ORANGE, W_MOTION, W_CONTEXT = 0.40, 0.15, 0.15, 0.15, 0.15
W_FEET, W_HEAD = 0.15, 0.10
ENTER = 0.25             # a candidate scoring at least this enters tracking

# How tall this game's camera shows the players (Sample.scale, reactions.people_scale). The
# sizes, distances and speeds in frame heights here were set on national broadcasts: a camera
# that shows the players 1.8 times as tall shows the ball 1.8 times as big, moving 1.8 times as
# far across the frame each second, so each is multiplied by it, and EDGE_Y's band at the
# bottom of a wide shot narrows by it (a close camera shows dribbles low in the frame). Set
# per shot by track_shot; 1 everywhere else.
_SCALE: contextvars.ContextVar[float] = contextvars.ContextVar("ball_scale", default=1.0)

# ---- tracklets --------------------------------------------------------------------------------

V_MAX = 3.0              # frame heights per second: faster than any pass
GATE_R = 0.03            # a tracklet takes a candidate within this (frame heights) of its prediction,
#                          plus V_MAX x dt (half that once it has two points and so a speed)...
GATE_SLOW = 1.0          # ...and, once it has a speed, no more than this or twice its own speed x dt: a
#                          tracklet that has stood still doesn't take a ball that turns up far off. On a 2019
#                          game-winner a decoy at the frame's edge, still for two samples, took the ball
#                          back at the rim 0.63 frame heights away (the ball's own tracklet had ended
#                          while it hid in the net), and the crop swung to the edge and back. A held
#                          ball that is passed starts a new tracklet, and the chain links the two.
END_AFTER = 0.35         # a tracklet ends once unmatched for longer than this...
END_SKIPS = 2            # ...and at least this many samples
LINE_N = 3               # constant velocity from the last this many points
FLIGHT_SPAN = 0.7        # a parabola is fitted to the points within this many seconds of the end
GRAVITY = (0.2, 3.0)     # frame heights per s^2: a fitted fall outside this is not a ball in flight
FLIGHT_JITTER = 0.01     # frame heights: detection noise allowed against a steady rise or fall
FLIGHT_FIT = 0.015       # frame heights: the farthest a flight's point may lie off its parabola
ASSIGN_SCORE = 0.15      # frame heights: assignment cost = distance + this x (1 - score), so the
#                          nearest candidate doesn't win over a much surer one just next to it
STRONG = 0.45            # a score that looks like the ball. A tracklet keeps its strongest run (the
#                          detections in a row whose scores above this add up most), so the weak ends
#                          it picked up from a shoe or a stray mark while the ball went unseen don't
#                          ride on the ball's history; counts only when its detections average above
#                          it; and weighs, in the chain, what its detections score above it
VALID_N = 3              # a tracklet counts with this many detections...
VALID_PAIR = 0.55        # ...or two, both scoring at least this, at a speed a ball can have

# ---- the chain --------------------------------------------------------------------------------

LINK_SLACK = 0.05        # two tracklets are compatible when the gap's distance is within this + V_MAX x gap
GAP_TIME = 0.3           # a link costs this per second of gap...
GAP_SPEED = 0.5          # ...plus this x the speed it implies, as a share of V_MAX
RESTART = 1.5            # an incompatible link costs this, and nothing is bridged across it
OVERLAP_N = 3            # a tracklet may run on past the next one's start by this many detections, dropped

# ---- statuses ---------------------------------------------------------------------------------

FLIGHT_BRIDGE = 0.6      # seconds: a free-flight gap between chained tracklets is bridged up to this...
CARRY_BRIDGE = 1.5       # ...and a gap with the same player holding the ball at both ends up to this
HOLD_VOTE = 3            # who holds the ball at a tracklet's end: the most named over its last this many
TIME_SLACK = 0.02        # sample times sit on frames, up to half a frame off their grid
QUALITY_N = 5            # a tracklet with this many detections is fully trusted
INNER_FACTOR = 0.9       # a point between two detections of one tracklet: their lower conf times this
BRIDGE_CONF = 0.5        # a bridged point's conf...
CARRY_FACTOR = 0.8       # ...times this when it is placed at the handler's hands
PREDICT_AHEAD = 0.4      # predictions run this far past a chain's end...
PREDICT_BACK = 0.2       # ...and this far before its start,
PREDICT_CONF = 0.5       # their conf falling linearly from this to 0
USABLE = 0.35            # a point is usable (a target for the crop) at this conf, when not missing


@dataclass
class Chain:
    """The tracklets chosen as the ball, in time order, and what joins each
    to the next: carried (bridged at the handler's hands), bridge (a short
    flight, bridged), link (compatible but too long a gap to bridge) or
    restart (a jump no ball makes; never bridged). `keep[n]` is how many of
    tracklet ids[n]'s detections are used: fewer when the next one began
    before it ended (see choose_chain)."""

    ids: list[int] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    keep: list[int] = field(default_factory=list)
    value: float = 0.0

    def used(self, tracks: list[list[tuple[int, BallCand]]]) -> list[list[tuple[int, BallCand]]]:
        """The chosen tracklets' detections, as used."""
        return [tracks[k][:n] for k, n in zip(self.ids, self.keep)]


def usable(point: BallPoint) -> bool:
    """A ball point the crop may follow."""
    return point.status != "missing" and point.conf >= USABLE


# ---- the gate ---------------------------------------------------------------------------------


def player_height(samples: list[Sample]) -> float | None:
    """The median floor player's height over the shot, what a ball's size is judged by."""
    hs = [p.h for s in samples for p in players.floor_players(s)]
    return statistics.median(hs) if hs else None


def context(samples: list[Sample], aspect: float = ASPECT, wide: bool = True,
            bug_box: tuple[float, float, float, float] | None = None) -> dict:
    """What gate() needs to know about the shot."""
    return {"player_h": player_height(samples), "wide": wide, "bug_box": bug_box,
            "static": static_cells(samples), "fans": fans(samples), "aspect": aspect}


def _stands(sample: Sample) -> list[Person]:
    """The people in the stands: everyone but the floor players, told only
    when there is a floor to tell them from, and but those nearer the camera
    than the floor (taller than TALL): they walk past, so fans() would
    remember them where they no longer are, and on a close camera a player
    passing the lens left a "head" over the play that rejected the ball."""
    floor = players.floor_players(sample)
    if len(floor) < players.STANDS_MIN:
        return []
    on_floor = {id(p) for p in floor}
    return [p for p in sample.people if id(p) not in on_floor and p.h <= TALL * sample.scale]


def fans(samples: list[Sample]) -> dict[int, list[tuple[float, float, float, float]]]:
    """For each sample (by Sample.i), the spectators seen within FAN_MEMORY s
    of it, as boxes (x, y, w, h) with the camera's travel taken out (frame
    fractions), so a head is known for one even in frames he was missed in.
    Boxes are rounded to FAN_ROUND and kept once each: people in the stands sit still."""
    times = [s.t for s in samples]
    seen = [{tuple(round(v / FAN_ROUND) * FAN_ROUND for v in (p.x + s.pan[0], p.y + s.pan[1], p.w, p.h))
             for p in _stands(s)} for s in samples]
    out = {}
    for s in samples:
        lo, hi = bisect.bisect_left(times, s.t - FAN_MEMORY), bisect.bisect_right(times, s.t + FAN_MEMORY)
        out[s.i] = sorted(set().union(*seen[lo:hi]))
    return out


def _inside(c: BallCand, p: Person, widen: float = 1.0) -> bool:
    return abs(c.x - p.x) <= widen * p.w / 2 and abs(c.y - p.y) <= widen * p.h / 2


def static_cells(samples: list[Sample]) -> dict:
    """Where candidates keep turning up in one place over the shot: on the
    court (a floor logo, which moves with the camera) or in the picture (a
    screen overlay, a light).

    On the court every candidate counts, against the samples the spot was in
    view in: a player standing in front of a logo doesn't hide it, and a logo
    the camera pans away from still shows. The cells are small, so a ball
    dribbled in place, sweeping from the hand to the floor and back, spreads
    over many; only one held perfectly still for much of the shot fills one.

    In the picture, candidates in a floor player's box don't count (a ball he
    carries while the camera follows him sits still there too), against the
    samples the spot was free of players in. A floor logo sits still in the
    picture too while the camera does: a picture cell whose hits a fixed court
    cell explains (COURT_EXPLAINS of them) is left to the court cell, so it
    can't reject the ball that passed that spot before the camera moved.

    A cell is fixed over the time it was hit, first to last (in the picture,
    counting the hits behind a player too).

    Returns {"n": samples, "wld"/"img": {cell: samples with a counted hit in
    it}, "wld_view"/"img_free": {cell: samples it could be hit in, for cells
    hit STATIC_MIN times}, "fixed": {("wld" | "img", cell): (first, last) time
    hit}}. Cells are (grid, column, row) in GRIDS half-offset grids, so a mark
    on a cell border is whole in one of them."""
    floors = [players.floor_players(s) for s in samples]
    hits: dict[str, Counter] = {"wld": Counter(), "img": Counter()}
    span: dict[tuple, tuple[float, float]] = {}
    court_of: dict[tuple, list] = {}            # picture cell -> [(t, its court cells)] of its counted hits
    for s, floor in zip(samples, floors):
        seen: dict[str, set] = {"wld": set(), "img": set()}
        any_img: set = set()
        for c in s.balls:
            here, pic = _cells(c.x + s.pan[0], c.y + s.pan[1]), _cells(c.x, c.y)
            seen["wld"].update(here)
            any_img.update(pic)
            if not any(_inside(c, p, players.HOLD_WIDEN) for p in floor):
                seen["img"].update(pic)
                for cell in pic:
                    court_of.setdefault(cell, []).append((s.t, here))
        hits["wld"].update(seen["wld"])
        hits["img"].update(seen["img"])
        for space, cells in (("wld", seen["wld"]), ("img", any_img)):
            for cell in cells:
                span[(space, cell)] = (span.get((space, cell), (s.t, s.t))[0], s.t)
    view = {cell: sum(1 for s in samples if _spot(cell, s.pan, ()))
            for cell, n in hits["wld"].items() if n >= STATIC_MIN}
    free_of = {cell: sum(1 for floor in floors if _spot(cell, (0.0, 0.0), floor))
               for cell, n in hits["img"].items() if n >= STATIC_MIN}
    fixed = {("wld", cell): span[("wld", cell)] for cell, n in view.items()
             if hits["wld"][cell] >= max(STATIC_MIN, STATIC_SHARE * n)}
    for cell, n in free_of.items():
        if hits["img"][cell] >= max(STATIC_MIN, STATIC_SHARE * n):
            got = court_of[cell]
            if sum(1 for t, here in got if _near(fixed, "wld", here, t)) < COURT_EXPLAINS * len(got):
                fixed[("img", cell)] = span[("img", cell)]
    return {"n": len(samples), "wld": hits["wld"], "img": hits["img"], "wld_view": view, "img_free": free_of,
            "fixed": fixed}


def _cells(x: float, y: float) -> list[tuple[int, int, int]]:
    gx, gy = x * GRID[0], y * GRID[1]
    return [(k, math.floor(gx + dx), math.floor(gy + dy)) for k, (dx, dy) in enumerate(GRIDS)]


def _spot(cell: tuple[int, int, int], pan: tuple[float, float], floor) -> bool:
    """The cell's centre is in the picture and in none of these floor players' boxes."""
    dx, dy = GRIDS[cell[0]]
    x, y = (cell[1] + 0.5 - dx) / GRID[0] - pan[0], (cell[2] + 0.5 - dy) / GRID[1] - pan[1]
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return False
    half = players.HOLD_WIDEN / 2
    return not any(abs(x - p.x) <= half * p.w and abs(y - p.y) <= half * p.h for p in floor)


def _near(fixed: dict, space: str, cells: list[tuple[int, int, int]], t: float) -> bool:
    """One of the cells, or a cell next to one (a mark's odd detection lands a
    little off it), is fixed, and was over a span of time that holds t."""
    for k, a, b in cells:
        for da in (-1, 0, 1):
            for db in (-1, 0, 1):
                when = fixed.get((space, (k, a + da, b + db)))
                if when is not None and when[0] - FIXED_SLACK <= t <= when[1] + FIXED_SLACK:
                    return True
    return False


def _static(c: BallCand, s: Sample, static: dict) -> tuple[bool, bool]:
    """(a fixed mark, static-ish): in or next to a fixed cell, and its court cell's hits."""
    if not static:
        return False, False
    court = _cells(c.x + s.pan[0], c.y + s.pan[1])
    fixed = static.get("fixed") or {}
    near = bool(fixed) and (_near(fixed, "wld", court, s.t) or _near(fixed, "img", _cells(c.x, c.y), s.t))
    # Static-ish by the court: a ball the camera follows sits still in the picture, not on the court.
    return near, static["wld"].get(court[0], 0) >= STILLISH


def _near_hands(c: BallCand, p: Person, aspect: float) -> bool:
    top = p.y - p.h / 2
    dx = max(0.0, abs(c.x - p.x) - p.w / 2)
    dy = max(0.0, top - c.y, c.y - (top + HANDS_PART * p.h))
    return math.hypot(dx, dy / aspect) <= HANDS_NEAR


def _at_feet(c: BallCand, p: Person) -> bool:
    bottom = p.y + p.h / 2
    return (abs(c.x - p.x) <= players.HOLD_WIDEN * p.w / 2
            and bottom - FEET_PART * p.h <= c.y <= bottom + FEET_BELOW * p.h)


def _fan_head(x: float, y: float, box: tuple[float, float, float, float]) -> bool:
    bx, by, bw, bh = box
    top = by - bh / 2
    return abs(x - bx) <= players.HOLD_WIDEN * bw / 2 and top - FAN_HEAD[0] * bh <= y <= top + FAN_HEAD[1] * bh


def gate(sample: Sample, ctx: dict) -> list[BallCand]:
    """Score every candidate in the sample and mark those that can't be the
    ball (see the module's reject letters). Sets `score` and `rejected` on
    each and returns them all; those with `rejected` empty enter tracking.

    `ctx` (see context()): player_h, wide, bug_box, static, fans, aspect.

    score = 0.40 conf + 0.15 size_fit + 0.15 orange + 0.15 motion + 0.15 context
            - 0.15 at_feet - 0.10 on_head, clipped to 0..1."""
    aspect = ctx.get("aspect") or ASPECT
    k = _SCALE.get()
    player_h = ctx.get("player_h")
    expected = BALL_REL * player_h if player_h else None
    bug = ctx.get("bug_box")
    floor = players.floor_players(sample)
    rows = (min(p.y - p.h / 2 for p in floor), max(p.y + p.h / 2 for p in floor)) if floor else None
    if "fans" in ctx:
        stands = ctx["fans"].get(sample.i, [])
    else:
        stands = [(p.x + sample.pan[0], p.y + sample.pan[1], p.w, p.h) for p in _stands(sample)]
    for c in sample.balls:
        d = max(c.w * aspect, c.h)
        why = ""
        if expected:
            ratio = d / expected
            if not SIZE_RANGE[0] <= ratio <= SIZE_RANGE[1]:
                why += "s"
            size_fit = min(1.0, max(0.0, 1 - abs(math.log(max(ratio, 1e-6))) / math.log(SIZE_RANGE[1])))
        else:
            if not SIZE_FREE[0] * k <= d <= SIZE_FREE[1] * k:
                why += "s"
            size_fit = 0.5
        shape = c.w * aspect / c.h if c.h > 0 else math.inf
        lo, hi = (1 / SHAPE_BLUR, SHAPE_BLUR) if c.conf >= SHAPE_BLUR_CONF else SHAPE
        if not lo <= shape <= hi:
            why += "a"
        if bug and bug[0] <= c.x <= bug[2] and bug[1] <= c.y <= bug[3]:
            why += "b"
        if ctx.get("wide", True) and c.y > 1 - (1 - EDGE_Y) / k:
            why += "f"
        fixed, stillish = _static(c, sample, ctx.get("static") or {})
        if fixed:
            why += "t"
        if any(_fan_head(c.x + sample.pan[0], c.y + sample.pan[1], box) for box in stands):
            why += "h"
        near = any(_near_hands(c, p, aspect) for p in floor) or (rows is not None and rows[0] <= c.y <= rows[1])
        at_feet = any(_at_feet(c, p) for p in sample.people)
        on_head = any(_inside(c, p) and c.y <= p.y - p.h / 2 + HEAD_PART * p.h for p in sample.people)
        score = (W_CONF * c.conf + W_SIZE * size_fit + W_ORANGE * (0.5 if c.orange is None else c.orange)
                 + W_MOTION * (0.5 if stillish else 1.0) + W_CONTEXT * (1.0 if near else CONTEXT_OFF)
                 - W_FEET * at_feet - W_HEAD * on_head)
        c.score = round(min(1.0, max(0.0, score)), 6)
        c.rejected = why or ("l" if c.score < ENTER else "")
    return sample.balls


# ---- tracklets --------------------------------------------------------------------------------


def _world(c: BallCand, s: Sample, aspect: float) -> tuple[float, float]:
    """A candidate's place with the camera's travel taken out, in frame heights."""
    return (c.x + s.pan[0]) * aspect, c.y + s.pan[1]


def _fit(ts: list[float], vs: list[float], degree: int) -> list[float]:
    """Least squares polynomial coefficients [a, b(, c)] of v = a + b t (+ c t^2)."""
    n = degree + 1
    sums = [sum(t ** k for t in ts) for k in range(2 * n - 1)]
    m = [[sums[r + k] for k in range(n)] + [sum(v * t ** r for t, v in zip(ts, vs))] for r in range(n)]
    for col in range(n):                                    # Gauss-Jordan, partial pivoting
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return [statistics.fmean(vs)] + [0.0] * degree
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [a - f * b for a, b in zip(m[r], m[col])]
    return [m[r][n] / m[r][r] for r in range(n)]


def _airborne(pts: list[tuple[float, float, float]]) -> list[float] | None:
    """The parabola [a, b, c] (Y = a + b t + c t^2, t as given) of a ball in
    flight: the points rise steadily, then fall steadily (y grows downward;
    the highest is neither the first nor the last), lie on the parabola and
    fall as a ball could. A dribble, up and down several times or bending
    harder than a ball falls, is no flight. Else None."""
    if len(pts) < 3:
        return None
    ys = [p[2] for p in pts]
    top = ys.index(min(ys))
    if top in (0, len(ys) - 1):
        return None
    k = _SCALE.get()
    if any(b > a + FLIGHT_JITTER * k for a, b in zip(ys[:top], ys[1:top + 1])) or \
            any(b < a - FLIGHT_JITTER * k for a, b in zip(ys[top:], ys[top + 1:])):
        return None
    ts = [p[0] for p in pts]
    coef = _fit(ts, ys, 2)
    misfit = max(abs(coef[0] + coef[1] * t + coef[2] * t * t - y) for t, y in zip(ts, ys))
    return coef if GRAVITY[0] * k <= 2 * coef[2] <= GRAVITY[1] * k and misfit <= FLIGHT_FIT * k else None


def predict(path: list[tuple[float, float, float]], t: float) -> tuple[float, float, float, float]:
    """World (X, Y, vx, vy) at t, from a tracklet's path [(t, X, Y)] in time
    order, extrapolated from its end nearer t: a parabola in Y once the ball
    is in flight (see _airborne), constant velocity otherwise."""
    if len(path) == 1:
        return path[0][1], path[0][2], 0.0, 0.0
    if t < path[0][0] or (t <= path[-1][0] and t - path[0][0] < path[-1][0] - t):
        # Backward: time mirrored about the path's start, so the same forward fits run on its first points.
        ref, sign = path[0][0], -1.0
        pts = [(ref - p[0], p[1], p[2]) for p in reversed(path)]
        tau = ref - t
    else:
        ref, sign = path[-1][0], 1.0
        pts = [(p[0] - ref, p[1], p[2]) for p in path]
        tau = t - ref
    span = [p for p in pts if p[0] >= -FLIGHT_SPAN]
    flight = _airborne(span)
    if flight is not None:
        bx = _fit([p[0] for p in span], [p[1] for p in span], 1)
        a, b, c = flight
        return bx[0] + bx[1] * tau, a + b * tau + c * tau * tau, sign * bx[1], sign * (b + 2 * c * tau)
    recent = pts[-LINE_N:]
    bx = _fit([p[0] for p in recent], [p[1] for p in recent], 1)
    by = _fit([p[0] for p in recent], [p[2] for p in recent], 1)
    return bx[0] + bx[1] * tau, by[0] + by[1] * tau, sign * bx[1], sign * by[1]


@dataclass
class _Live:
    pts: list[tuple[int, BallCand]]
    path: list[tuple[float, float, float]]
    last: int                    # the position of its last sample in the shot
    order: int                   # when it began, for a stable order


def tracklets(samples: list[Sample], gated: list[list[BallCand]], aspect: float = ASPECT
              ) -> list[list[tuple[int, BallCand]]]:
    """Short tracks of candidates, [(Sample.i, BallCand), ...] each, in world
    coordinates. `gated[k]` are the candidates of samples[k] that passed the
    gate. Each tracklet takes a candidate within GATE_R + V_MAX x dt of its
    prediction (once it has a speed: half V_MAX, or less for a slow one, see
    GATE_SLOW), greedily by
    distance plus ASSIGN_SCORE x (1 - score); one candidate per tracklet. A
    candidate nobody takes starts a tracklet. A tracklet ends unmatched for
    END_AFTER s and END_SKIPS samples. Returned in order of their first sample,
    each cut to its strongest run (see STRONG)."""
    k_scale = _SCALE.get()
    live: list[_Live] = []
    done: list[_Live] = []
    made = 0
    for k, s in enumerate(samples):
        keep = []
        for tr in live:
            ended = s.t - tr.path[-1][0] > END_AFTER and k - tr.last - 1 >= END_SKIPS
            (done if ended else keep).append(tr)
        live = keep
        cands = gated[k] if k < len(gated) else []
        spots = [_world(c, s, aspect) for c in cands]
        pairs = []
        for a, tr in enumerate(live):
            dt = s.t - tr.path[-1][0]
            px, py, vx, vy = predict(tr.path, s.t)
            if len(tr.path) < 2:
                reach = V_MAX * k_scale
            else:
                reach = min(0.5 * V_MAX * k_scale, max(GATE_SLOW * k_scale, 2 * math.hypot(vx, vy)))
            r = GATE_R * k_scale + reach * dt
            for b, (x, y) in enumerate(spots):
                dist = math.hypot(x - px, y - py)
                if dist <= r:
                    # The distance itself, not over the radius: a lone detection's gate is
                    # twice as wide, and mustn't win the ball from the tracklet following it.
                    pairs.append((dist + ASSIGN_SCORE * k_scale * (1 - cands[b].score), a, b))
        pairs.sort()
        used_t: set[int] = set()
        used_c: set[int] = set()
        for _, a, b in pairs:
            if a in used_t or b in used_c:
                continue
            used_t.add(a)
            used_c.add(b)
            tr = live[a]
            tr.pts.append((s.i, cands[b]))
            tr.path.append((s.t, *spots[b]))
            tr.last = k
        for b, c in enumerate(cands):
            if b not in used_c:
                live.append(_Live([(s.i, c)], [(s.t, *spots[b])], k, made))
                made += 1
    done.extend(live)
    done.sort(key=lambda tr: (tr.path[0][0], tr.order))
    return [_strongest(tr.pts) for tr in done]


def _strongest(pts: list[tuple[int, BallCand]]) -> list[tuple[int, BallCand]]:
    """The run of detections whose scores above STRONG add up most (the first
    such run on a tie); a tracklet of weak detections keeps them all, and
    never counts (is_valid)."""
    best, start, total, at = None, 0, 0.0, 0
    for n, (_, c) in enumerate(pts):
        if total <= 0:
            total, at = 0.0, n
        total += c.score - STRONG
        if best is None or total > best + 1e-12:
            best, start, end = total, at, n
    return pts[start:end + 1] if best is not None and best > 0 else pts


def path_of(track: list[tuple[int, BallCand]], samples: list[Sample], aspect: float = ASPECT
            ) -> list[tuple[float, float, float]]:
    """A tracklet's world path [(t, X, Y)]."""
    by_i = {s.i: s for s in samples}
    return [(by_i[i].t, *_world(c, by_i[i], aspect)) for i, c in track]


def is_valid(track: list[tuple[int, BallCand]], path: list[tuple[float, float, float]]) -> bool:
    """VALID_N detections averaging above STRONG (a trimmed tracklet always
    does; one of weak candidates only, a shoe followed about, never), or two
    scoring VALID_PAIR at a speed a ball can have. A lone detection never counts."""
    if len(track) >= VALID_N:
        return statistics.fmean(c.score for _, c in track) > STRONG
    if len(track) == 2 and all(c.score >= VALID_PAIR for _, c in track):
        (t0, x0, y0), (t1, x1, y1) = path
        return t1 > t0 and math.hypot(x1 - x0, y1 - y0) / (t1 - t0) <= V_MAX * _SCALE.get()
    return False


# ---- the chain --------------------------------------------------------------------------------


def choose_chain(tracks: list[list[tuple[int, BallCand]]], samples: list[Sample], aspect: float = ASPECT
                 ) -> Chain:
    """The valid tracklets that make the most plausible ball path, by dynamic
    programming over them in order of start:

        best[k] = w_k + max(0, max_j best[j] - cost(j, k))   (j ends before k starts)

    w_k sums each detection's score above STRONG, so only detections that
    look like a ball pay for the links to them (a valid tracklet's w_k is
    always positive, see is_valid). j may run on past k's start by up to
    OVERLAP_N detections, which are then dropped (their weight with them),
    so a tracklet that forked when the ball was briefly missed still joins.

    j and k are compatible when the gap's distance is within LINK_SLACK +
    V_MAX x gap, or the same player holds the ball at both ends ("carried",
    see _holder); then cost = GAP_TIME x gap + GAP_SPEED x speed / V_MAX.
    Otherwise the link is a restart costing RESTART. The players' track ids
    must be set (players.track) for "carried" to be found."""
    by_i = {s.i: s for s in samples}
    paths = [path_of(tr, samples, aspect) for tr in tracks]
    ids = sorted((k for k, tr in enumerate(tracks) if is_valid(tr, paths[k])), key=lambda k: (paths[k][0][0], k))
    holders: dict[tuple[int, int, int], int | None] = {}

    def holder(k: int, keep: int, end: int) -> int | None:
        if (k, keep, end) not in holders:
            holders[(k, keep, end)] = _holder(tracks[k][:keep], by_i, aspect, end)
        return holders[(k, keep, end)]

    best: dict[int, float] = {}
    back: dict[int, tuple[int, str, int] | None] = {}
    for n, k in enumerate(ids):
        w = sum(c.score - STRONG for _, c in tracks[k])
        top, via = 0.0, None
        for j in ids[:n]:
            # A tracklet that ran on a little past the next one's start (it followed a stray
            # candidate while the ball began a new one) joins it without that tail.
            keep = sum(1 for p in paths[j] if p[0] < paths[k][0][0])
            if keep < len(tracks[j]) and (len(tracks[j]) - keep > OVERLAP_N
                                          or not is_valid(tracks[j][:keep], paths[j][:keep])):
                continue
            tail = sum(c.score - STRONG for _, c in tracks[j][keep:])
            kind, cost = _link(paths[j][:keep], paths[k], holder(j, keep, -1), holder(k, len(tracks[k]), 0))
            if best[j] - tail - cost > top + 1e-12:
                top, via = best[j] - tail - cost, (j, kind, keep)
        best[k], back[k] = w + top, via
    if not ids:
        return Chain()
    k = max(ids, key=lambda k: (round(best[k], 9), -paths[k][0][0], -k))
    chain = Chain(value=best[k])
    keep = len(tracks[k])
    while True:
        chain.ids.append(k)
        chain.keep.append(keep)
        if back[k] is None:
            break
        k, kind, keep = back[k]
        chain.links.append(kind)
    for part in (chain.ids, chain.links, chain.keep):
        part.reverse()
    return chain


def _holder(track: list[tuple[int, BallCand]], by_i: dict[int, Sample], aspect: float, end: int) -> int | None:
    """The player holding the ball at a tracklet's end (end -1) or start (0):
    the one players.handler names for most of its HOLD_VOTE detections there,
    the nearest the end on a tie, so a frame he went unseen in doesn't lose him."""
    near = track[-HOLD_VOTE:][::-1] if end == -1 else track[:HOLD_VOTE]
    votes = [players.handler(by_i[i], BallPoint(t=by_i[i].t, x=c.x, y=c.y), aspect) for i, c in near]
    seen = [v for v in votes if v is not None]
    return max(seen, key=lambda v: (seen.count(v), -seen.index(v))) if seen else None


def _link(a: list[tuple[float, float, float]], b: list[tuple[float, float, float]],
          held_a: int | None, held_b: int | None) -> tuple[str, float]:
    """How tracklet path a joins the later b, and what it costs."""
    gap = b[0][0] - a[-1][0]
    dist = math.hypot(b[0][1] - a[-1][1], b[0][2] - a[-1][2])
    k = _SCALE.get()
    reach = dist <= (LINK_SLACK + V_MAX * gap) * k
    carried = held_a is not None and held_a == held_b
    if not (reach or carried):
        return "restart", RESTART
    cost = GAP_TIME * gap + GAP_SPEED * dist / (V_MAX * k * max(gap, 1e-3))
    if carried and gap <= CARRY_BRIDGE + TIME_SLACK:
        return "carried", cost
    if reach and gap <= FLIGHT_BRIDGE + TIME_SLACK:
        return "bridge", cost
    return "link", cost


# ---- statuses ---------------------------------------------------------------------------------


def _image(world: tuple[float, float], s: Sample, aspect: float) -> tuple[float, float]:
    return world[0] / aspect - s.pan[0], world[1] - s.pan[1]


def _lerp(a: tuple[float, float, float], b: tuple[float, float, float], t: float) -> tuple[float, float]:
    u = (t - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
    return a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u


def _speed(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float]:
    dt = b[0] - a[0]
    return ((b[1] - a[1]) / dt, (b[2] - a[2]) / dt) if dt > 0 else (0.0, 0.0)


def _hands(s: Sample, who: int | None, aspect: float) -> tuple[float, float] | None:
    """A player's hands (his upper body's centre), in world coordinates."""
    p = next((p for p in s.people if who is not None and p.track == who), None)
    if p is None:
        return None
    return (p.x + s.pan[0]) * aspect, p.y - players.HANDS_UP * p.h + s.pan[1]


def statuses(samples: list[Sample], tracks: list[list[tuple[int, BallCand]]], chain: Chain,
             aspect: float = ASPECT) -> list[BallPoint]:
    """One BallPoint per sample of the shot, from the chosen chain:
    - detected: conf (0.5 + 0.5 score) x min(1, n / QUALITY_N);
    - interpolated: between two detections of a tracklet; across a chained
      gap up to FLIGHT_BRIDGE s ("bridge"), or up to CARRY_BRIDGE s when the
      same player holds the ball at both ends ("carried", placed at his hands);
      linear in world coordinates, with a lower conf;
    - predicted: up to PREDICT_AHEAD s past a chain's (or a sub-chain's) end and
      PREDICT_BACK s before its start, conf falling from PREDICT_CONF to 0;
    - missing: the rest. Nothing is bridged across a restart.
    vx and vy are world speeds, frame heights per second."""
    out = [BallPoint(t=s.t) for s in samples]
    if not chain.ids:
        return out
    pos = {s.i: k for k, s in enumerate(samples)}
    by_i = {s.i: s for s in samples}
    used = chain.used(tracks)
    paths = [path_of(tr, samples, aspect) for tr in used]
    for k, tr, path in zip(chain.ids, used, paths):
        quality = min(1.0, len(tr) / QUALITY_N)
        _fill_tracklet(out, samples, pos, k, tr, path, [(0.5 + 0.5 * c.score) * quality for _, c in tr], aspect)
    for n, kind in enumerate(chain.links):
        lo, hi = pos[used[n][-1][0]], pos[used[n + 1][0][0]]
        if kind in ("carried", "bridge"):
            who = _holder(used[n], by_i, aspect, -1) if kind == "carried" else None
            conf = BRIDGE_CONF * (CARRY_FACTOR if kind == "carried" else 1.0)
            _bridge(out, samples, lo, hi, chain.ids[n], paths[n][-1], paths[n + 1][0], kind, who, conf, aspect)
        else:
            _predict_gap(out, samples, lo, hi, chain.ids[n], paths[n], chain.ids[n + 1], paths[n + 1], aspect)
    _predict_gap(out, samples, -1, pos[used[0][0][0]], None, None, chain.ids[0], paths[0], aspect)
    _predict_gap(out, samples, pos[used[-1][-1][0]], len(samples), chain.ids[-1], paths[-1], None, None, aspect)
    return out


def _fill_tracklet(out, samples, pos, k, track, path, confs, aspect) -> None:
    """A tracklet's detections, and the samples between them."""
    for n, (i, c) in enumerate(track):
        prev, nxt = path[max(0, n - 1)], path[min(len(path) - 1, n + 1)]
        vx, vy = _speed(prev, nxt)
        out[pos[i]] = BallPoint(t=path[n][0], x=c.x, y=c.y, status="detected", conf=round(confs[n], 6),
                                vx=vx, vy=vy, tracklet=k)
        if n == 0:
            continue
        a, b = path[n - 1], path[n]
        vx, vy = _speed(a, b)
        for m in range(pos[track[n - 1][0]] + 1, pos[i]):
            s = samples[m]
            x, y = _image(_lerp(a, b, s.t), s, aspect)
            out[m] = BallPoint(t=s.t, x=x, y=y, status="interpolated", conf=round(min(confs[n - 1], confs[n])
                               * INNER_FACTOR, 6), vx=vx, vy=vy, tracklet=k)


def _bridge(out, samples, lo, hi, k, a, b, kind, who, conf, aspect) -> None:
    """Interpolated points across a chained gap: in a straight line (world
    coordinates), or, carried by `who`, following his hands with the ball's
    offset from them blended from one end to the other (a straight line
    where he isn't seen at both ends)."""
    vx, vy = _speed(a, b)
    offset_a = offset_b = None
    if who is not None:
        ha, hb = _hands(samples[lo], who, aspect), _hands(samples[hi], who, aspect)
        if ha is not None and hb is not None:
            offset_a, offset_b = (a[1] - ha[0], a[2] - ha[1]), (b[1] - hb[0], b[2] - hb[1])
    for m in range(lo + 1, hi):
        s = samples[m]
        world = _lerp(a, b, s.t)
        hands = _hands(s, who, aspect) if offset_a is not None else None
        if hands is not None:
            u = (s.t - a[0]) / (b[0] - a[0])
            world = (hands[0] + offset_a[0] + (offset_b[0] - offset_a[0]) * u,
                     hands[1] + offset_a[1] + (offset_b[1] - offset_a[1]) * u)
        x, y = _image(world, s, aspect)
        out[m] = BallPoint(t=s.t, x=x, y=y, status="interpolated", conf=round(conf, 6), vx=vx, vy=vy,
                           via=kind, tracklet=k)


def _predict_gap(out, samples, lo, hi, a, path_a, b, path_b, aspect) -> None:
    """Predicted points in the samples strictly between lo and hi: forward from
    tracklet a's end, backward from b's start; the surer one where both reach."""
    for m in range(lo + 1, hi):
        s = samples[m]
        best = None
        for k, path, horizon, dt in ((a, path_a, PREDICT_AHEAD, s.t - path_a[-1][0] if path_a else None),
                                     (b, path_b, PREDICT_BACK, path_b[0][0] - s.t if path_b else None)):
            if path is None or dt is None or not 0 < dt < horizon:
                continue
            conf = PREDICT_CONF * (1 - dt / horizon)
            if best is None or conf > best[0]:
                best = (conf, k, path)
        if best is None:
            continue
        conf, k, path = best
        x_w, y_w, vx, vy = predict(path, s.t)
        x, y = _image((x_w, y_w), s, aspect)
        out[m] = BallPoint(t=s.t, x=x, y=y, status="predicted", conf=round(conf, 6), vx=vx, vy=vy, tracklet=k)


# ---- all of it --------------------------------------------------------------------------------


def track_shot(samples: list[Sample], ctx: dict | None = None, debug: dict | None = None) -> list[BallPoint]:
    """The ball through one camera shot: gate, tracklets, chain, statuses.
    `samples` are the shot's, in time order; `ctx` may give aspect, wide,
    bug_box (what the caller knows), player_h, static and fans (worked out
    here when missing, see context()). Runs players.track first when nobody
    has a track id yet (plan_clip runs it per shot with its own id ranges).
    Each candidate keeps its score and reject letters; pass a dict as `debug`
    to get the context, the tracklets, which are valid and the chain too."""
    token = _SCALE.set(max(1.0, float(samples[0].scale or 1.0)) if samples else 1.0)
    try:
        return _track_shot(samples, ctx, debug)
    finally:
        _SCALE.reset(token)


def _track_shot(samples: list[Sample], ctx: dict | None, debug: dict | None) -> list[BallPoint]:
    ctx = dict(ctx or {})
    aspect = ctx.get("aspect") or ASPECT
    ctx["aspect"] = aspect
    if any(s.people for s in samples) and all(p.track is None for s in samples for p in s.people):
        players.track(samples, aspect)
    if "player_h" not in ctx:
        ctx["player_h"] = player_height(samples)
    if "static" not in ctx:
        ctx["static"] = static_cells(samples)
    if "fans" not in ctx:
        ctx["fans"] = fans(samples)
    gated = [[c for c in gate(s, ctx) if not c.rejected] for s in samples]
    tracks = tracklets(samples, gated, aspect)
    chain = choose_chain(tracks, samples, aspect)
    points = statuses(samples, tracks, chain, aspect)
    if debug is not None:
        debug.update({"ctx": ctx, "tracklets": tracks,
                      "valid": [is_valid(tr, path_of(tr, samples, aspect)) for tr in tracks], "chain": chain})
    return points
