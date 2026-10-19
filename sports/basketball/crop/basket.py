"""The baskets in each camera shot: the rim, the backboard and the span a crop must hold.

A hoop source (the classic rim finder, or a basketball-trained model) reports
rims sample by sample and misses many: the ball and players pass in front, the
camera pans, a far rim is small. A basket is a rim seen again and again at one
place on the court, so candidates are clustered per shot in world coordinates
(the camera's travel taken out), and the basket is then placed on every sample
of the shot, seen or not, where the camera's travel says it is now.

The owner's rule: from the ball's approach to the rim until the result, the
rim, the net and the backboard are all in the crop, so the crop's edge on the
basket's baseline side sits past the backboard. Each basket therefore carries
its backboard (found on this sample; carried over as an offset from the rim
when found on others; or else estimated from the rim's width) and its "unit",
the horizontal span with margins a crop must hold. When the broadcast itself
cuts the board off at the frame's edge, the unit stops at the edge and a note
says so.

relevant() says which basket a play is about. Pure Python (see model.py).
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass

from sports.basketball.crop.model import BallPoint, Basket, Hints, Person, Phase, RimCand, Sample, Shot
from sports.basketball.crop.players import floor_players
from sports.basketball.crop.shots import samples_in

ASPECT = 16 / 9          # the source's width over height, when the caller doesn't say

# ---- one basket per place on the court --------------------------------------------------------

CLUSTER_DX = 0.03        # a rim within this share of the frame's width of a basket's recent position...
CLUSTER_DY = 0.03        # ...and this share of its height (world coordinates) is the same basket...
CLUSTER_DW = 0.4         # ...when its width is within this share of the basket's
CLUSTER_RECENT = 2.0     # seconds: a basket's position is the median of its detections this recent (zoom drift)
SUPPORT_MIN = 0.3        # a basket is detected in at least this share of the samples where it should be in view...
SUPPORT_N = 3            # ...and in at least this many: a lone detection is never a basket
MAX_BASKETS = 2          # a shot shows at most both ends of the court
BASKET_SEP = 0.2         # frame widths (world): two baskets this close are one, split by a jump; the weaker goes
ANCHOR_HALF = 1.0        # seconds: between detections, the position is re-anchored on those within this of t
PROP_DECAY = 3.0         # seconds from the nearest detection over which a propagated basket's conf fades

# ---- the backboard ----------------------------------------------------------------------------

BB_MIN = 2               # boards found on at least this many samples make the shot's board offset
EDGE = 0.002             # a box this close to the frame's edge is cut off by it
BB_NEAR = 0.5            # a found board reaches within this many rim widths of the rim horizontally...
BB_WIDE = 5.0            # ...is at most this many rim widths wide...
#                          ...and its middle is above the rim's; anything else is not this rim's board
SIDE_BOARD = 0.25        # rim widths: a board centred this far off the rim says which side the baseline is

# The allowance when no board is found, in rim widths: a board seen from the side reaches about
# this far past the rim's outer edge. Measured on 32 clips of an NBA broadcast: past the ring
# itself 0.85 rim widths in the median, 1.26 at the 95th percentile, 1.47 at most (past the
# detector's hoop box, ring and net, 0.70-0.91 box widths); with the margin past it (m_out, at
# least 0.35 rim widths) the crop clears the farthest of them...
ALLOW_OUT = 1.3
ALLOW_IN = 0.3           # ...starts this far past the rim's inner edge (toward the baseline)...
ALLOW_TOP = 2.4          # ...has its top this far above the rim's top (rim widths, in frame heights)...
BB_BELOW = 0.3           # ...and its bottom this far below the rim's bottom
ALLOW_NET = 1.2          # the net hangs this far below the rim's bottom (rim widths, in frame heights)

# ---- the baseline side ------------------------------------------------------------------------

SIDE_PLAYERS = 0.05      # frame widths: the rim this far from the floor players' median x says the side
SIDE_PLAYERS_N = 3       # a sample needs this many floor players to vote
SIDE_FRAME = 0.2         # else a rim this far off the frame's centre is on its baseline's side

# ---- the unit ---------------------------------------------------------------------------------

M_OUT_MIN = 0.012        # frame widths past the board's outer edge, at least...
M_OUT_RW = 0.35          # ...or this many rim widths, whichever is more
M_IN = 0.01              # frame widths inside the rim on the court side
CLIP_CONF = 0.6          # a basket whose board the frame cuts is this much less sure
ALLOW_CONF = 0.9         # an estimated board makes the basket this much less sure
NOTE = "the broadcast cuts the backboard"

# ---- which basket the play is about -----------------------------------------------------------

USABLE_CONF = 0.35       # a ball point counts when it isn't missing and is at least this sure
RIM_ZONE_X = 2.5         # rim widths either side of the rim's centre...
RIM_ZONE_UP = 4.0        # ...from this many rim widths (in frame heights) above it...
RIM_ZONE_DOWN = 2.0      # ...to this many below (through the net): the ball there is at this rim
RIM_ZONE_WITHIN = 3.0    # seconds after the release the ball is looked for in a rim zone
HEAD_AFTER = 1.0         # seconds after the release whose ball velocity says where it is heading
HEAD_VMIN = 0.15         # frame heights per second: slower than this the ball heads nowhere
HEAD_REACH = 2.5         # seconds: a basket the ball would take longer than this to reach is not its target
HEAD_CLEAR = 1.5         # the winner's votes are at least this many times the runner-up's
HINT_RELEASE_BACK = 2.5  # with only the moment known, the release is looked for from this long before it
ATTACK_BACK = 2.0        # seconds before the release (or the moment) whose motion is the attack direction
ATTACK_VMIN = 0.1        # frame heights per second: the ball's (or handler's) median speed that has a direction
PAN_VMIN = 0.05          # frame widths per second: a camera pan this fast has a direction


# ---- detections, clustered ------------------------------------------------------------------


@dataclass
class _Hit:
    k: int                                   # the sample's index within the shot
    t: float
    x: float                                 # world position: image + the shot's pan (frame fractions)
    y: float
    rim: RimCand
    board: tuple[float, float, float, float] | None   # this rim's board, when found and plausible


@dataclass
class _Board:
    """A backboard as offsets from the rim's centre: x in rim widths, y in rim
    widths measured in frame heights (a rim width x the aspect)."""

    x0: float
    y0: float
    x1: float
    y1: float


def _plausible(r: RimCand) -> tuple[float, float, float, float] | None:
    """The rim's board when it could be this rim's: beside or over it, not
    too wide, its middle above the rim's."""
    b = r.backboard
    if b is None or r.w <= 0:
        return None
    x0, y0, x1, y1 = b
    near = x0 <= r.x + r.w / 2 + BB_NEAR * r.w and x1 >= r.x - r.w / 2 - BB_NEAR * r.w
    if not near or x1 - x0 > BB_WIDE * r.w or x1 <= x0 or (y0 + y1) / 2 >= r.y:
        return None
    return b


def _hits(own: list[Sample]) -> list[list[_Hit]]:
    return [[_Hit(k, s.t, r.x + s.pan[0], r.y + s.pan[1], r, _plausible(r)) for r in s.rims if r.w > 0]
            for k, s in enumerate(own)]


def _ref(hits: list[_Hit], t: float) -> tuple[float, float, float]:
    """A basket's recent world position and width: medians over its detections
    in the last CLUSTER_RECENT s (its last three when it hasn't been seen since)."""
    recent = [h for h in hits if t - h.t <= CLUSTER_RECENT] or hits[-3:]
    return (statistics.median(h.x for h in recent), statistics.median(h.y for h in recent),
            statistics.median(h.rim.w for h in recent))


def _match(ref: tuple[float, float, float], h: _Hit) -> float | None:
    dx, dy = abs(h.x - ref[0]), abs(h.y - ref[1])
    if dx > CLUSTER_DX or dy > CLUSTER_DY or abs(h.rim.w / ref[2] - 1) > CLUSTER_DW:
        return None
    return max(dx / CLUSTER_DX, dy / CLUSTER_DY)


def _cluster(rows: list[list[_Hit]]) -> list[list[_Hit]]:
    """Each sample's rims joined to the baskets seen so far, greedily by
    distance, at most one per basket per sample; the rest start new ones."""
    clusters: list[list[_Hit]] = []
    for row in rows:
        if not row:
            continue
        refs = [_ref(c, row[0].t) for c in clusters]
        pairs = sorted((cost, ci, hi) for ci, ref in enumerate(refs) for hi, h in enumerate(row)
                       if (cost := _match(ref, h)) is not None)
        taken_c: set[int] = set()
        taken_h: set[int] = set()
        for _cost, ci, hi in pairs:
            if ci in taken_c or hi in taken_h:
                continue
            taken_c.add(ci)
            taken_h.add(hi)
            clusters[ci].append(row[hi])
        clusters += [[h] for hi, h in enumerate(row) if hi not in taken_h]
    return clusters


def _anchor(hits: list[_Hit], t: float) -> tuple[float, float, float, float]:
    """The basket's world position and rim size at t, re-anchored on the
    detections within ANCHOR_HALF of t (a sliding window, so a slow camera
    zoom that the pan doesn't measure is followed); with none that close,
    the window around the nearest detection."""
    near = [h for h in hits if abs(h.t - t) <= ANCHOR_HALF]
    if not near:
        t_near = min(hits, key=lambda h: abs(h.t - t)).t
        near = [h for h in hits if abs(h.t - t_near) <= ANCHOR_HALF]
    return (statistics.median(h.x for h in near), statistics.median(h.y for h in near),
            statistics.median(h.rim.w for h in near), statistics.median(h.rim.h for h in near))


def _inside(x: float, y: float) -> bool:
    return 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0


def _support(hits: list[_Hit], own: list[Sample]) -> float:
    """The share of the samples where the basket should be in view (its
    anchored position, less the pan, inside the frame) that detected it."""
    seen = {h.k for h in hits}
    view = set(seen)
    for k, s in enumerate(own):
        ax, ay, _, _ = _anchor(hits, s.t)
        if _inside(ax - s.pan[0], ay - s.pan[1]):
            view.add(k)
    return len(seen) / max(1, len(view))


def _pick(clusters: list[list[_Hit]], own: list[Sample]) -> list[tuple[list[_Hit], float]]:
    """The clusters that are baskets: seen in SUPPORT_N samples and SUPPORT_MIN
    of those where they should be, at most MAX_BASKETS, at least BASKET_SEP
    apart (the most seen first), in world x order."""
    good = []
    for c in clusters:
        if len(c) >= SUPPORT_N and (sup := _support(c, own)) >= SUPPORT_MIN:
            good.append((c, sup))
    good.sort(key=lambda cs: (-len(cs[0]), -statistics.fmean(h.rim.score for h in cs[0]), cs[0][0].t))
    kept: list[tuple[list[_Hit], float]] = []
    for c, sup in good:
        x = statistics.median(h.x for h in c)
        if len(kept) < MAX_BASKETS and all(abs(x - statistics.median(h.x for h in k)) >= BASKET_SEP for k, _ in kept):
            kept.append((c, sup))
    return sorted(kept, key=lambda cs: statistics.median(h.x for h in cs[0]))


# ---- the backboard and the side -------------------------------------------------------------


def _board_model(hits: list[_Hit], aspect: float) -> _Board | None:
    """The board as an offset from the rim, from every sample that found it:
    robust medians, so samples that see only the rim still get a board. An
    edge the frame cuts on every sample is only known to reach at least that
    far, so it takes the farther of that and the allowance."""
    found = [h for h in hits if h.board is not None]
    if len(found) < BB_MIN:
        return None
    x0s, x0c, x1s, x1c, y0s, y1s = [], [], [], [], [], []
    for h in found:
        r, (bx0, by0, bx1, by1) = h.rim, h.board
        (x0c if bx0 <= EDGE else x0s).append((bx0 - r.x) / r.w)
        (x1c if bx1 >= 1 - EDGE else x1s).append((bx1 - r.x) / r.w)
        y0s.append((by0 - r.y) / (r.w * aspect))
        y1s.append((by1 - r.y) / (r.w * aspect))
    reach = 0.5 + ALLOW_OUT
    x0 = statistics.median(x0s) if x0s else min(min(x0c), -reach)
    x1 = statistics.median(x1s) if x1s else max(max(x1c), reach)
    return _Board(x0, statistics.median(y0s), x1, statistics.median(y1s))


def _side_of_board(board: _Board | None) -> str | None:
    if board is None:
        return None
    mid = (board.x0 + board.x1) / 2
    return "right" if mid >= SIDE_BOARD else "left" if mid <= -SIDE_BOARD else None


def _side(board: _Board | None, own: list[Sample], rim_x: list[float],
          floor_of: Callable[[Sample], list[Person]]) -> tuple[str, str]:
    """The basket's baseline side and why: the side its found board is on;
    else the side away from the floor players; else the frame's side the rim
    is on, when well off-centre; else unknown."""
    side = _side_of_board(board)
    if side:
        return side, "backboard"
    diffs = []
    for s, x in zip(own, rim_x):
        floor = floor_of(s)
        if len(floor) >= SIDE_PLAYERS_N:
            diffs.append(x - statistics.median(p.x for p in floor))
    if diffs:
        d = statistics.median(diffs)
        if abs(d) >= SIDE_PLAYERS:
            return ("right" if d > 0 else "left"), "players"
    xs = [x for x in rim_x if 0.0 <= x <= 1.0]
    if xs:
        x = statistics.median(xs)
        if abs(x - 0.5) > SIDE_FRAME:
            return ("right" if x > 0.5 else "left"), "frame"
    return "unknown", "unknown"


def allowance(rim: tuple[float, float, float, float], side: str,
              aspect: float = ASPECT) -> tuple[float, float, float, float]:
    """The board when none was found (corners): ALLOW_OUT rim widths past the
    rim's outer edge on the baseline side, from ALLOW_IN past its inner edge;
    ALLOW_OUT on both sides when the side is unknown; its top ALLOW_TOP rim
    widths above the rim's."""
    x0, y0, x1, y1 = rim
    rw = x1 - x0
    top, bottom = y0 - ALLOW_TOP * rw * aspect, y1 + BB_BELOW * rw * aspect
    if side == "right":
        return x0 + ALLOW_IN * rw, top, x1 + ALLOW_OUT * rw, bottom
    if side == "left":
        return x0 - ALLOW_OUT * rw, top, x1 - ALLOW_IN * rw, bottom
    return x0 - ALLOW_OUT * rw, top, x1 + ALLOW_OUT * rw, bottom


def m_out(rim_w: float) -> float:
    """The margin past the board's outer edge: M_OUT_MIN, or M_OUT_RW rim widths."""
    return max(M_OUT_MIN, M_OUT_RW * rim_w)


def unit(rim: tuple[float, float, float, float], board: tuple[float, float, float, float], side: str,
         aspect: float = ASPECT) -> tuple[tuple[float, float], tuple[float, float], str]:
    """What a crop must hold to show the rim, the net and the board: (x0, x1)
    with m_out past the board on the baseline side (both sides when it is
    unknown) and M_IN on the court side, clipped to the frame; (y0, y1) from
    the board's top to the net's bottom; and the side(s) where the board
    itself runs past the frame's edge ("" when it doesn't): the broadcast cuts
    it there, and no crop can do better than the edge."""
    rw = rim[2] - rim[0]
    out = m_out(rw)
    left_m = out if side in ("left", "unknown") else M_IN
    right_m = out if side in ("right", "unknown") else M_IN
    x0 = min(rim[0], board[0]) - left_m
    x1 = max(rim[2], board[2]) + right_m
    cut = [name for name, hit in (("left", side != "right" and board[0] <= EDGE),
                                  ("right", side != "left" and board[2] >= 1 - EDGE)) if hit]
    y0 = min(rim[1], board[1])
    y1 = max(board[3], rim[3] + ALLOW_NET * rw * aspect)
    clip = (min(1.0, max(0.0, x0)), min(1.0, max(0.0, x1)))
    return clip, (min(1.0, max(0.0, y0)), min(1.0, max(0.0, y1))), "both" if len(cut) == 2 else "".join(cut)


# ---- the baskets of a shot ------------------------------------------------------------------


def _floor_fn(players) -> Callable[[Sample], list[Person]]:
    """Who the floor players are at a sample: given as {Sample.i: [Person]},
    or else (None, or players.track's {id: [(i, Person)]}) worked out here."""
    if isinstance(players, dict):
        filled = [v for v in players.values() if v]
        if filled and all(isinstance(v[0], Person) for v in filled):
            return lambda s: players.get(s.i, [])
    return floor_players


def _place(hit: _Hit | None, hits: list[_Hit], s: Sample) -> tuple[float, float, float, float]:
    """The rim's image centre and size at a sample. The centre is as detected
    there, else its re-anchored world position less the sample's pan. The
    size is always the re-anchored one: it changes only with the camera's
    zoom, which the window follows, while one sample's width is noisy, and
    the board and the margins are measured in rim widths."""
    ax, ay, aw, ah = _anchor(hits, s.t)
    if hit is not None:
        return hit.rim.x, hit.rim.y, aw, ah
    return ax - s.pan[0], ay - s.pan[1], aw, ah


def _conf(hit: _Hit | None, hits: list[_Hit], t: float, support: float) -> float:
    sure = min(1.0, support / 0.6)
    if hit is not None:
        return sure * (0.5 + 0.5 * hit.rim.score)
    gap = min(abs(h.t - t) for h in hits)
    return sure * 0.75 * max(0.25, 1.0 - gap / PROP_DECAY)


def _one(hits: list[_Hit], support: float, own: list[Sample], shot: Shot, bid: int,
         floor_of: Callable[[Sample], list[Person]], aspect: float) -> list[Basket]:
    """One basket on every sample of its shot."""
    by_k = {h.k: h for h in hits}
    places = [_place(by_k.get(k), hits, s) for k, s in enumerate(own)]
    model = _board_model(hits, aspect)
    side, _why = _side(model, own, [p[0] for p in places], floor_of)
    out = []
    for k, s in enumerate(own):
        hit = by_k.get(k)
        cx, cy, w, h = places[k]
        rim = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        if hit is not None and hit.board is not None:
            board, src = hit.board, "found"
        elif model is not None:
            rwh = w * aspect
            board, src = (cx + model.x0 * w, cy + model.y0 * rwh, cx + model.x1 * w, cy + model.y1 * rwh), "model"
        else:
            board, src = allowance(rim, side, aspect), "allowance"
        span, span_y, cut = unit(rim, board, side, aspect)
        conf = _conf(hit, hits, s.t, support)
        conf *= (ALLOW_CONF if src == "allowance" else 1.0) * (CLIP_CONF if cut else 1.0)
        out.append(Basket(id=bid, shot=shot.id, t=s.t, rim=rim, backboard=board, bb_src=src, side=side,
                          unit=span, status="detected" if hit is not None else "propagated",
                          in_frame=_inside(cx, cy), conf=round(conf, 4), unit_y=span_y, clipped=cut))
    return out


def shot_baskets(own: list[Sample], shot: Shot, players=None, *, aspect: float = ASPECT, first_id: int = 0,
                 notes: list[str] | None = None) -> list[list[Basket]]:
    """The baskets of one shot, one list per sample of `own` (the shot's
    samples, in order), each holding every basket of the shot (in world x
    order, so the left one first), in frame or not (`in_frame` says). Ids
    count from `first_id`. When the frame cuts a board, a line is added to
    `notes`. `players`: see baskets()."""
    if not own:
        return []
    floor_of = _floor_fn(players)
    found = _pick(_cluster(_hits(own)), own)
    tracks = [_one(hits, sup, own, shot, first_id + n, floor_of, aspect) for n, (hits, sup) in enumerate(found)]
    for track in tracks:
        seen = [b for b in track if b.in_frame]
        cut = sum(1 for b in seen if b.clipped)
        if cut and notes is not None:
            notes.append(f"{NOTE} (shot {shot.id}, basket {track[0].id}, {track[0].side} side, "
                         f"{cut} of {len(seen)} samples)")
    return [[track[k] for track in tracks] for k in range(len(own))]


def cut_note(shot: Shot, rows: list[list[Basket]], relevant: int | None, need: list[bool]) -> str | None:
    """The quality note when the broadcast itself cuts off the backboard of
    the basket the play is about, on the samples that need it in the crop
    (`need`, one per row: at the rim and the result); None otherwise. A board
    cut off on another basket, or during the play before the shot, is no
    matter: the crop never has to hold it then."""
    if relevant is None:
        return None
    seen = [b for k, row in enumerate(rows) if k < len(need) and need[k] for b in row
            if b.id == relevant and b.in_frame]
    cut = sum(1 for b in seen if b.clipped)
    if not cut:
        return None
    return f"{NOTE} (shot {shot.id}, basket {relevant}, {seen[0].side} side, {cut} of {len(seen)} samples)"


def baskets(samples: list[Sample], shots: list[Shot], players=None, *, aspect: float = ASPECT,
            first_id: int = 0, notes: list[str] | None = None) -> dict[int, list[list[Basket]]]:
    """Every shot's baskets: {Shot.id: one list per sample in the shot (by
    frame, as shots.samples_in gives them) of its Baskets}. Ids are unique in
    the clip. `players` is {Sample.i: [floor players]} when the caller has
    them; otherwise (None, or players.track's output) players.floor_players
    decides per sample. Lines for the quality notes go to `notes`."""
    out: dict[int, list[list[Basket]]] = {}
    next_id = first_id
    for shot in shots:
        rows = shot_baskets(samples_in(shot, samples), shot, players, aspect=aspect, first_id=next_id, notes=notes)
        out[shot.id] = rows
        next_id += len(rows[0]) if rows else 0
    return out


def find(row: list[Basket], basket_id: int | None) -> Basket | None:
    """The basket with this id among one sample's, or None."""
    return next((b for b in row if b.id == basket_id), None)


# ---- which basket the play is about ---------------------------------------------------------


def _usable(bp: BallPoint | None) -> bool:
    return bp is not None and bp.status != "missing" and bp.conf >= USABLE_CONF and bp.x is not None \
        and bp.y is not None


def _rim_centre(b: Basket) -> tuple[float, float, float]:
    return (b.rim[0] + b.rim[2]) / 2, (b.rim[1] + b.rim[3]) / 2, b.rim[2] - b.rim[0]


def in_rim_zone(bp: BallPoint, b: Basket, aspect: float = ASPECT) -> bool:
    """The ball is at this basket's rim: within RIM_ZONE_X rim widths of its
    centre sideways, from RIM_ZONE_UP above to RIM_ZONE_DOWN below (through the net)."""
    cx, cy, rw = _rim_centre(b)
    dy = bp.y - cy
    return abs(bp.x - cx) <= RIM_ZONE_X * rw and -RIM_ZONE_UP * rw * aspect <= dy <= RIM_ZONE_DOWN * rw * aspect


def _hint_times(hint) -> tuple[float | None, float | None, float | None]:
    """(release, moment, handler's vx) from a hint: a dict ("release",
    "moment_t" or "moment", "handler_vx"), a Hints, or the phases found so far."""
    if hint is None:
        return None, None, None
    if isinstance(hint, Hints):
        return None, hint.moment_t, None
    if isinstance(hint, dict):
        moment = hint.get("moment_t", hint.get("moment"))
        return hint.get("release"), moment, hint.get("handler_vx")
    phases = [p for p in hint if isinstance(p, Phase)]
    release = next((p.t0 for p in phases if p.kind in ("release", "flight")), None)
    moment = next((p.t0 for p in phases if p.kind in ("rim", "result", "attack")), None)
    return release, moment, None


def _times(rows: list[list[Basket]], balls: list[BallPoint]) -> list[float]:
    if len(balls) == len(rows):
        return [bp.t for bp in balls]
    return [row[0].t if row else 0.0 for row in rows]


def _nearest(times: list[float], t: float) -> int:
    return min(range(len(times)), key=lambda k: abs(times[k] - t))


def _ball_at(balls: list[BallPoint], times: list[float], t: float) -> BallPoint | None:
    """The last usable ball at or before t."""
    best = None
    for k, bp in enumerate(balls):
        if k < len(times) and times[k] <= t + 1e-9 and _usable(bp):
            best = bp
    return best


def _rim_entry(rows, balls, times, t0: float, t1: float, aspect: float) -> int | None:
    for k, bp in enumerate(balls):
        if k < len(rows) and t0 - 1e-9 <= times[k] <= t1 and _usable(bp):
            hit = next((b.id for b in rows[k] if in_rim_zone(bp, b, aspect)), None)
            if hit is not None:
                return hit
    return None


def _heading(rows, balls, times, release: float, aspect: float) -> int | None:
    """The basket the ball's world velocity points at just after the release:
    each usable sample votes (by its conf) for the baskets it moves toward and
    would reach within HEAD_REACH s; the winner must clearly lead."""
    votes: dict[int, float] = {}
    for k, bp in enumerate(balls):
        if k >= len(rows) or not release - 1e-9 <= times[k] <= release + HEAD_AFTER or not _usable(bp):
            continue
        if abs(bp.vx) < HEAD_VMIN:
            continue
        for b in rows[k]:
            dx = (_rim_centre(b)[0] - bp.x) * aspect
            if dx * bp.vx > 0 and dx / bp.vx <= HEAD_REACH:
                votes[b.id] = votes.get(b.id, 0.0) + bp.conf
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    if ranked and (len(ranked) == 1 or ranked[0][1] >= HEAD_CLEAR * ranked[1][1]):
        return ranked[0][0]
    return None


def _direction(balls, times, samples, t: float, handler_vx: float | None) -> int:
    """The attack direction at t (+1 right, -1 left, 0 unknown): the ball's
    median world velocity over the last ATTACK_BACK s, else the handler's,
    else the camera's pan."""
    vx = [bp.vx for k, bp in enumerate(balls)
          if k < len(times) and t - ATTACK_BACK <= times[k] <= t and bp.status != "missing" and bp.x is not None]
    if vx and abs(med := statistics.median(vx)) >= ATTACK_VMIN:
        return 1 if med > 0 else -1
    if handler_vx is not None and abs(handler_vx) >= ATTACK_VMIN:
        return 1 if handler_vx > 0 else -1
    if samples and len(samples) == len(times):
        a, b = _nearest(times, t - ATTACK_BACK), _nearest(times, t)
        dt = times[b] - times[a]
        if dt > 0 and abs(v := (samples[b].pan[0] - samples[a].pan[0]) / dt) >= PAN_VMIN:
            return 1 if v > 0 else -1
    return 0


def choose(baskets_at: list[list[Basket]], ball_points: list[BallPoint], phases_hint=None, *,
           samples: list[Sample] | None = None, aspect: float = ASPECT) -> tuple[int | None, str]:
    """relevant(), with the reason for the log."""
    rows = list(baskets_at)
    ids = sorted({b.id for row in rows for b in row})
    if not ids or not rows:
        return None, "no basket"
    times = _times(rows, ball_points)
    release, moment, handler_vx = _hint_times(phases_hint)
    start = release if release is not None else (moment - HINT_RELEASE_BACK if moment is not None else None)
    # 1. Where the shot goes: the rim zone the ball reaches first after the release, or the
    #    basket its velocity points at just after it.
    if start is not None:
        hit = _rim_entry(rows, ball_points, times, start, start + RIM_ZONE_WITHIN, aspect)
        if hit is not None:
            return hit, "the ball reaches its rim"
        hit = _heading(rows, ball_points, times, start, aspect)
        if hit is not None:
            return hit, "the release heads for it"
    else:
        hit = _rim_entry(rows, ball_points, times, times[0], times[-1], aspect)
        if hit is not None:
            return hit, "the ball reaches its rim"
    # 2. The attack direction.
    t = release if release is not None else moment if moment is not None else times[-1]
    k = _nearest(times, t)
    sign = _direction(ball_points, times, samples, t, handler_vx)
    if sign and rows[k]:
        ref = _ball_at(ball_points, times, t)
        x = ref.x if ref is not None else 0.5
        ahead = [b for b in rows[k] if (_rim_centre(b)[0] - x) * sign > 0]
        if ahead:
            return min(ahead, key=lambda b: abs(_rim_centre(b)[0] - x)).id, "the attack direction"
    # 3. The only one in the picture.
    visible = [b for b in rows[k] if b.in_frame]
    if len(visible) == 1:
        return visible[0].id, "the only one in view"
    # 4. The one nearest the ball at the moment.
    m = _nearest(times, moment) if moment is not None else k
    pool = [b for b in rows[m] if b.in_frame] or rows[m] or rows[k]
    if not pool:
        return ids[0], "the only one known"
    ref = _ball_at(ball_points, times, times[m])
    x = ref.x if ref is not None else 0.5
    return min(pool, key=lambda b: (abs(_rim_centre(b)[0] - x), b.id)).id, "nearest the ball"


def relevant(baskets_at: list[list[Basket]], ball_points: list[BallPoint], phases_hint=None, *,
             samples: list[Sample] | None = None, aspect: float = ASPECT) -> int | None:
    """The id of the basket a play is about, from one shot's baskets per
    sample (`baskets_at`) and its ball points (one per sample, the same
    samples), in order:
    1. the basket the release heads for: the ball reaches its rim zone after
       the release, or its world velocity just after the release points at it;
    2. the attack direction: the ball's (or the handler's) median world
       velocity over the last 2 s, else the camera's pan;
    3. the only one in the picture;
    4. the one nearest the ball at the moment.
    `phases_hint`: a dict ("release", "moment_t", "handler_vx"; clip seconds),
    a model.Hints, or the phases found so far. `samples` (the same samples)
    give the camera's pan for rule 2. None when the shot has no basket."""
    return choose(baskets_at, ball_points, phases_hint, samples=samples, aspect=aspect)[0]
