"""The classic rim finder: the orange ring, the white net under it and the backboard beside it.

No model: colour, shape and edges, in a few milliseconds a frame, so it runs on any machine and
stands in whenever the basketball-trained detector is not used. It is not pure Python (cv2 and
numpy), so the planning modules never import it; the observation stage does.

What a broadcast's game camera shows, measured on the owner's footage (233 labelled rings):
- The ring is a small, flat, red-orange ellipse, 0.020-0.055 of the frame's width and 3-5 times
  as wide as it is tall, between 0.17 and 0.31 of the height. Its colour is redder than the
  ball's orange: OpenCV hue median 179 (5-95%: 172 to 7, through 0), saturation 89-239, value
  75-159. The ball, the gold crowd shirts and the wood floor are yellower (hue 10-25), so the
  ring's colour alone already leaves most of them out; red signs, red jerseys and the orange
  mounting bracket at the ring's board end are what is left.
- The ring touches that bracket, and often a red sign or pad beside the board, so looking for
  it as one coloured blob of the right shape fails a third of the time: the blob is the ring
  and its neighbours. It is found instead as a band of its colour with nothing of that colour
  above or below it and at least one clear end (a matched filter on the colour mask).
- The net hangs under it: grey-white at this size (value >= 120, saturation <= 70 covers a
  median 28% of the area under the ring, 2% beside it).
- The backboard is glass with a white border, seen from the side: its outer (baseline-side)
  edge is a long near-vertical line 0.85 ring widths past the ring's outer edge (median;
  5-95%: 0.55-1.25). The glass shows the crowd through it, so a bright board is rare; the
  border's edges are the evidence.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from sports.basketball.crop.model import Person, RimCand

# ---- the working copy ---------------------------------------------------------------------------

WORK_W = 960                 # the ring is looked for on a copy this wide (540 high for 16:9)
ROWS = (0.08, 0.70)          # the band of the height a rim can be in (score bug above, floor below)

# ---- the ring's colour (OpenCV HSV: hue 0-179) ----------------------------------------------------

HUE_WRAP = 172               # red-orange: hue from here up to 179...
HUE_HI = 9                   # ...and from 0 to here (measured: the ring's hue is 172-7 for 90%)
S_MIN = 100                  # the ring is saturated (5%: 89)
V_MIN = 70                   # and not dark (5%: 75)

# ---- the matched filter --------------------------------------------------------------------------

SCALES = (20, 25, 31, 38, 46, 55)   # ring widths tried, pixels of the working copy (0.021-0.057 W)
BAND = 0.30                  # the band the ring fills, as a share of its width
FILTER_DOWN = 2              # the filter runs on a copy this many times smaller (colour shares)
W_CLEAR = 0.7                # the ring's colour above or below the band counts against it this much...
W_END = 0.5                  # ...and past both ends this much (one end may touch the bracket)
PEAK_MIN = 0.12              # a filter response this strong is a candidate
PEAK_KEEP = 30               # at most this many candidates a frame are looked at closely

# ---- the ring's box and shape, in frame widths and pixels of the working copy -------------------

W_MIN, W_MAX = 0.010, 0.060  # its width (measured 0.020-0.055)
ASPECT_MIN, ASPECT_MAX = 2.0, 8.0   # width over height
GAP = 3                      # pixels: gaps this wide along the ring are bridged
REACH = 0.24                 # the ring's colour is looked for this many filter widths above and below the peak...
OUTSIDE = 4                  # ...and a column of the ring keeps this many pixels past that clear
RING_H = 0.42                # the ring is at most this tall for its width (measured 0.27-0.40)
RESP_FULL = 0.45             # a filter response this strong is a perfect shape

# ---- the net, under the ring -------------------------------------------------------------------

NET_TOPHAT = 18              # a net strand: this much brighter than its 5x5 surroundings...
NET_V, NET_S = 60, 70        # ...grey-white, not dark
NET_DOWN = 0.7               # the net is looked for this many ring widths under the ring
NET_FULL = 0.35              # the share of strands there that counts as a full net (measured median 0.41)

# ---- the backboard -----------------------------------------------------------------------------

BRIGHT_V, BRIGHT_S = 150, 50  # a painted (not glass) board: bright and low in saturation
BRIGHT_MIN = 0.5             # the share of bright pixels above and beside the ring that is a painted board...
BRIGHT_EV = 0.5              # ...and the board evidence that gives (half: a bright floor or wall looks the same;
#                              the owner's footage has glass boards only, so this is untested)
RW_NORM = 40.0               # the board is searched on a copy scaled so the ring is this many pixels wide
BB_RIDGE_W = 9               # pixels of that copy: a white line narrower than this is a border...
BB_RIDGE_MIN = 12            # ...this many grey levels brighter than beside it
BB_GAP = 3                   # pixels: breaks this long along the border are bridged
RING_HW = 0.36               # a ring is about this tall for its width (measured 0.25-0.40)
BB_OUT = (0.7, 2.6)          # ring widths out from the ring's centre where the board's outer edge is looked for
#                              (measured 1.05-1.75 past the labelled ring's centre, 90%; wider for a found ring)
BB_IN_C = -0.6               # the inner edge is no further in than this from the ring's centre
BB_UP = 2.6                  # the search reaches this many ring widths above the ring's top
BB_DOWN = -0.5               # the border comes down to at least this far (ring widths) from the ring's top
BB_LINE_MIN = 0.6            # ring widths: the shortest border that counts
BB_WIDTH = (0.6, 2.0)        # ring widths from the outer edge in to the inner one (measured 0.87-1.42, median 1.12)
BB_LEN_MIN = 0.8             # ring widths: an edge this short is no evidence (a crowd's clutter makes these)...
BB_FULL = 1.6                # ...and this long is full evidence (measured: 90% of labelled boards' edges reach 1.8)
BB_EDGE_OUT = 0.1            # ring widths from the line's middle to the board's outermost edge (measured)
BB_KEEP_LEN = 1.0            # ring widths: a board whose outer edge is shorter than this is not reported
SIDE_MID = 0.1               # a ring this far off the frame's middle has its board on that side

# ---- the score ---------------------------------------------------------------------------------

W_SHAPE, W_BOARD, W_NET, W_POS = 0.35, 0.35, 0.2, 0.1
POS_IDEAL = (0.10, 0.40)     # rows where a rim earns the full position prior (measured 0.17-0.31)
THRESHOLD = 0.8              # a candidate scoring this much is a rim (tuned on the bench: see the docstring)
MAX_RIMS = 2                 # a frame shows at most both baskets
SECOND_DX = 0.5              # frame widths: a second rim is the other basket, this far across...
SECOND_DY = 0.08             # ...within this much of the first's height...
SECOND_W = 1.6               # ...and of a width within this ratio of it

# The ring-coloured pixels' means, with full marks inside the measured spread of the labelled
# rings (5-95% of the candidates on them: hue -5.1 to 0.7, saturation 137-177, value 109-142)
# and none past the outer limits. LED ad boards are redder and brighter, shirts and shoes
# yellower: these leave most of them out.
HUE_FIT = (-9.0, 6.0, (-5.0, 1.0))
SAT_FIT = (100.0, 230.0, (130.0, 180.0))
VAL_FIT = (70.0, 190.0, (100.0, 140.0))

# People: a ring-coloured shape on a person is a jersey, shorts or a shoe, not a rim.
TORSO = (0.35, 0.65)         # the band of a person's box (from its top) where a jersey is...
TORSO_W = 0.35               # ...and its half-width, in box widths (any person)
BODY_TOP = 0.35              # a floor player's body: below this share of the box from its top (head, raised arms)
BODY_H = 0.12                # ...of a person at least this tall
FEET_X = 0.02                # frame widths either side of a floor player's box...
FEET_Y = 0.12                # ...and box heights above and below its bottom, where the shoes are


def _ramp(v: float, lo: float, hi: float, ideal: tuple[float, float]) -> float:
    """1 inside the ideal range, falling linearly to 0.5 at the hard limits, 0 past them."""
    if v < lo or v > hi:
        return 0.0
    a, b = ideal
    if a <= v <= b:
        return 1.0
    if v < a:
        return 0.5 + 0.5 * (v - lo) / max(1e-9, a - lo)
    return 0.5 + 0.5 * (hi - v) / max(1e-9, hi - b)


def _working_copy(frame_bgr: np.ndarray) -> np.ndarray:
    h, w = frame_bgr.shape[:2]
    if w == WORK_W:
        return frame_bgr
    return cv2.resize(frame_bgr, (WORK_W, max(1, round(h * WORK_W / w))), interpolation=cv2.INTER_AREA)


def _share(mask: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> float:
    h, w = mask.shape[:2]
    x0, x1 = max(0, x0), min(w, x1)
    y0, y1 = max(0, y0), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float(np.count_nonzero(mask[y0:y1, x0:x1])) / ((x1 - x0) * (y1 - y0))


def _in_torso(cx: float, cy: float, people: list[Person] | None) -> bool:
    """A jersey's number or trim, not a rim: the centre inside a person's torso band."""
    for p in people or ():
        top = p.y - p.h / 2
        if abs(cx - p.x) <= TORSO_W * p.w and top + TORSO[0] * p.h <= cy <= top + TORSO[1] * p.h:
            return True
    return False


def _default_sides(cx: float) -> tuple[str, ...]:
    """Where the board can be: the side of the frame the ring is on (a game camera at centre
    court sees each board past its ring's outer edge), or both near the middle."""
    if cx < 0.5 - SIDE_MID:
        return ("left",)
    if cx > 0.5 + SIDE_MID:
        return ("right",)
    return ("left", "right")


# ---- the ring ------------------------------------------------------------------------------------


def _ring_response(colour: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The matched filter on the half-size shares of the ring's colour: for every pixel, the
    best response over SCALES and the ring width (working-copy pixels) it came from.

    At each scale the ring is a band BAND times its width tall, split in thirds. The response
    is the emptiest third's colour share (the ring spans the band), less W_CLEAR times the
    share in the same band above or below it (a sign or a shirt fills those too), less W_END
    times the clearer of the two ends (the ring stops; one end may touch its bracket).
    """
    h, w = colour.shape
    best = np.full((h, w), -1.0, np.float32)
    best_w = np.zeros((h, w), np.float32)
    for ring_w in SCALES:
        s = ring_w / FILTER_DOWN
        t = max(1, round(s / 3))
        b = max(2, round(BAND * s))
        my, mx = b, 2 * t
        if h <= 2 * my or w <= 2 * mx:
            continue
        third = cv2.boxFilter(colour, -1, (t, b), normalize=True, borderType=cv2.BORDER_CONSTANT)

        def v(dy: int, dx: int, a: np.ndarray = third, my: int = my, mx: int = mx) -> np.ndarray:
            return a[my + dy:h - my + dy, mx + dx:w - mx + dx]

        left, mid, right = v(0, -t), v(0, 0), v(0, t)
        span = np.minimum(np.minimum(left, mid), right)
        above = (v(-b, -t) + v(-b, 0) + v(-b, t)) / 3
        below = (v(b, -t) + v(b, 0) + v(b, t)) / 3
        ends = np.minimum(v(0, -2 * t), v(0, 2 * t))
        resp = span - W_CLEAR * np.maximum(above, below) - W_END * ends
        region = best[my:h - my, mx:w - mx]
        better = resp > region
        region[better] = resp[better]
        best_w[my:h - my, mx:w - mx][better] = ring_w
    return best, best_w


def _peaks(best: np.ndarray, best_w: np.ndarray) -> list[tuple[float, int, int, float]]:
    """The strongest local maxima of the response: (response, x, y, ring width), with x and y
    in working-copy pixels of the band, at most PEAK_KEEP."""
    local = (best >= PEAK_MIN) & (best >= cv2.dilate(best, np.ones((3, 3), np.uint8)))
    ys, xs = np.nonzero(local)
    if not len(ys):
        return []
    vals = best[ys, xs]
    order = np.argsort(-vals, kind="stable")
    out: list[tuple[float, int, int, float]] = []
    for k in order:
        x, y, rw = int(xs[k]) * FILTER_DOWN, int(ys[k]) * FILTER_DOWN, float(best_w[ys[k], xs[k]])
        if any(abs(x - o[1]) < 0.6 * max(rw, o[3]) and abs(y - o[2]) < 0.6 * max(rw, o[3]) for o in out):
            continue
        out.append((float(vals[k]), x, y, rw))
        if len(out) == PEAK_KEEP:
            break
    return out


def _run(on: np.ndarray, c: int, gap_max: int) -> tuple[int, int] | None:
    """The run of True through index c (or the nearest True), bridging gaps up to gap_max."""
    if not on[c]:
        near = np.nonzero(on)[0]
        if not len(near):
            return None
        c = int(near[np.argmin(np.abs(near - c))])
    lo = hi = c
    gap = 0
    while lo - 1 >= 0 and gap <= gap_max:
        lo -= 1
        gap = 0 if on[lo] else gap + 1
    lo += gap
    gap = 0
    while hi + 1 < len(on) and gap <= gap_max:
        hi += 1
        gap = 0 if on[hi] else gap + 1
    hi -= gap
    return lo, hi


def _refine(mask: np.ndarray, x: int, y: int, ring_w: float) -> tuple[int, int, int, int] | None:
    """The ring's box (x0, y0, x1, y1, working-copy pixels of the band) around a filter peak.

    Its columns are the run through the peak of columns coloured within REACH of the peak's
    row and clear for OUTSIDE pixels past that: the bracket, a sign or a pad that touches the
    ring is taller than the ring, so it ends the run. Its rows are the RING_H-tall window
    (a share of the width found) holding the most colour, so a ball in the net or the
    bracket under it does not make it taller.
    """
    h, w = mask.shape
    reach = max(4, round(REACH * ring_w))
    y0, y1 = max(0, y - reach), min(h, y + reach + 1)
    xa, xb = max(0, int(x - W_MAX * WORK_W)), min(w, int(x + W_MAX * WORK_W) + 1)
    band = mask[y0:y1, xa:xb] > 0
    out = (mask[max(0, y0 - OUTSIDE):y0, xa:xb] > 0).sum(axis=0) + (mask[y1:y1 + OUTSIDE, xa:xb] > 0).sum(axis=0)
    cols = _run(band.any(axis=0) & (out <= 1), min(max(0, x - xa), xb - xa - 1), GAP)
    if cols is None:
        return None
    lo, hi = cols
    prof = band[:, lo:hi + 1].sum(axis=1).astype(np.float32)
    tall = max(3, round(RING_H * (hi - lo + 1)))
    if len(prof) > tall:
        sums = np.convolve(prof, np.ones(tall, np.float32), mode="valid")
        top = int(np.argmax(sums))
        prof[:top] = 0
        prof[top + tall:] = 0
    filled = np.nonzero(prof)[0]
    if not len(filled):
        return None
    return xa + lo, y0 + int(filled[0]), xa + hi + 1, y0 + int(filled[-1]) + 1


# ---- the backboard -------------------------------------------------------------------------------


def _column_runs(on: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Every vertical run of True in a 2-D mask: (column, first row, one past the last row)."""
    padded = np.zeros((on.shape[0] + 2, on.shape[1]), np.int8)
    padded[1:-1] = on
    d = np.diff(padded, axis=0).T                    # columns first, so runs come out column by column
    sc, sr = np.nonzero(d == 1)
    _ec, er = np.nonzero(d == -1)
    return sc, sr, er


def _board_search(frame_bgr: np.ndarray, box: tuple[float, float, float, float],
                  side: str) -> tuple[tuple[float, float, float, float] | None, float, float]:
    """The board on one side of a ring, from its border: two long near-vertical white lines,
    its outer edge past the ring's outer end and its inner edge a board's width further in,
    both coming down to about the ring's height.

    `box` is the ring's corners in frame fractions. A ring found in a frame is often a piece
    of the ring, or the ring with its bracket, so the search is measured from the box's
    centre in units of the ring's width as its height also gives it (RING_HW), never less.
    Returns the board's corners (frame fractions) and the two lines' lengths in those units
    (0 for a line not found). A crowd makes short white lines everywhere; long ones a board's
    width apart are a board. The search runs on a copy scaled so the ring is RW_NORM pixels
    wide, mirrored for a right-side board so the outer side is always on its left. A white
    line is a horizontal top-hat (bright, narrower than BB_RIDGE_W) of at least BB_RIDGE_MIN
    grey levels; a column one either side counts, for a border that leans slightly.
    """
    fh, fw = frame_bgr.shape[:2]
    x0, y0, x1, y1 = box[0] * fw, box[1] * fh, box[2] * fw, box[3] * fh
    rw = max(x1 - x0, (y1 - y0) / RING_HW)
    if rw < 4:
        return None, 0.0, 0.0
    c = (x0 + x1) / 2
    if side == "left":
        ax0, ax1 = c - (BB_OUT[1] + 0.2) * rw, c - (BB_IN_C - 0.2) * rw
    else:
        ax0, ax1 = c + (BB_IN_C - 0.2) * rw, c + (BB_OUT[1] + 0.2) * rw
    ay0, ay1 = y0 - BB_UP * rw, y1 + 0.3 * rw
    cx0, cx1 = max(0, int(ax0)), min(fw, int(math.ceil(ax1)))
    cy0, cy1 = max(0, int(ay0)), min(fh, int(math.ceil(ay1)))
    if cx1 - cx0 < 8 or cy1 - cy0 < 8:
        return None, 0.0, 0.0
    f = RW_NORM / rw
    size = (max(8, round((cx1 - cx0) * f)), max(8, round((cy1 - cy0) * f)))
    roi = cv2.resize(frame_bgr[cy0:cy1, cx0:cx1], size, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
    if side == "right":
        roi = roi[:, ::-1]
    sx, sy = size[0] / (cx1 - cx0), size[1] / (cy1 - cy0)

    gray = cv2.cvtColor(np.ascontiguousarray(roi), cv2.COLOR_BGR2GRAY)
    ridge = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (BB_RIDGE_W, 1)))
    on = cv2.dilate((ridge >= BB_RIDGE_MIN).astype(np.uint8), np.ones((1, 3), np.uint8))
    on = cv2.morphologyEx(on, cv2.MORPH_CLOSE, np.ones((BB_GAP + 1, 1), np.uint8))
    uc = ((c - cx0) if side == "left" else (cx1 - c)) * sx
    ring_top = (y0 - cy0) * sy
    cols, starts, ends = _column_runs(on)
    d = (uc - cols) / RW_NORM                        # how far out from the ring's centre, ring widths
    length = (ends - starts) / RW_NORM
    low = ((ends - ring_top) / RW_NORM >= BB_DOWN) & (length >= BB_LINE_MIN)
    outer = low & (d >= BB_OUT[0]) & (d <= BB_OUT[1])
    if not outer.any():
        return None, 0.0, 0.0
    k = int(np.argmax(np.where(outer, length, -1.0)))
    span = (cols - cols[k]) / RW_NORM                # how far in from the outer line
    inner = low & (span >= BB_WIDTH[0]) & (span <= BB_WIDTH[1]) & (d >= BB_IN_C)
    j = int(np.argmax(np.where(inner, length, -1.0))) if inner.any() else None

    u = float(cols[k]) - BB_EDGE_OUT * RW_NORM          # the board's outermost edge, past the line's middle
    outer_x = cx0 + u / sx if side == "left" else cx1 - u / sx
    top = starts[k] if j is None else min(starts[k], starts[j])
    bottom = max(ends[k] / sy + cy0, y1)
    if j is None:
        inner_x = c                  # no inner line: a board seen from the side ends about the ring's centre
    else:
        inner_x = cx0 + float(cols[j]) / sx if side == "left" else cx1 - float(cols[j]) / sx
    bx0, bx1 = (outer_x, inner_x) if side == "left" else (inner_x, outer_x)
    corners = (max(0.0, bx0 / fw), max(0.0, (top / sy + cy0) / fh), min(1.0, bx1 / fw), min(1.0, bottom / fh))
    return corners, float(length[k]), 0.0 if j is None else float(length[j])


def _corners(rim: RimCand | tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    if isinstance(rim, RimCand):
        return (rim.x - rim.w / 2, rim.y - rim.h / 2, rim.x + rim.w / 2, rim.y + rim.h / 2)
    x0, y0, x1, y1 = (float(v) for v in rim)
    return x0, y0, x1, y1


def _board_evidence(outer_len: float, inner_len: float) -> float:
    """How sure the board is, 0-1: each edge counts half, nothing below BB_LEN_MIN ring widths
    and in full from BB_FULL."""
    def one(length: float) -> float:
        return min(1.0, max(0.0, (length - BB_LEN_MIN) / (BB_FULL - BB_LEN_MIN)))

    return 0.5 * one(outer_len) + 0.5 * one(inner_len)


def _best_board(frame_bgr: np.ndarray, box: tuple[float, float, float, float],
                side: str | None) -> tuple[tuple[float, float, float, float] | None, float, float]:
    """The board on the given side, else on the side(s) _default_sides allows, the surer one:
    its corners and its two edges' lengths in ring widths."""
    sides = (side,) if side in ("left", "right") else _default_sides((box[0] + box[2]) / 2)
    best: tuple[tuple[float, float, float, float] | None, float, float] = (None, 0.0, 0.0)
    for s in sides:
        found = _board_search(frame_bgr, box, s)
        if found[0] is not None and _board_evidence(*found[1:]) >= _board_evidence(*best[1:]):
            best = found
    return best


def find_backboard(frame_bgr: np.ndarray, rim: RimCand | tuple[float, float, float, float],
                   side: str | None = None) -> tuple[float, float, float, float] | None:
    """The backboard behind a rim, as corners (x0, y0, x1, y1) in frame fractions, or None.

    The outer (baseline-side) edge is the border's white line found past the ring's outer
    edge; the inner edge is the second line a board's width in from it (else the ring's
    centre, where a board seen from the side ends); the top is where the lines end. `rim` is
    a RimCand or the ring's corners; `side` is the baseline side when known, else it is the
    frame's side the ring is on (both near the middle, the surer one kept). Given a rim, the
    outer line alone is enough: it is what a crop must clear.
    """
    corners, outer_len, _inner = _best_board(frame_bgr, _corners(rim), side)
    return corners if outer_len >= BB_KEEP_LEN else None


# ---- the rims ------------------------------------------------------------------------------------


def _colour(hsv: np.ndarray, mask: np.ndarray, box: tuple[int, int, int, int]) -> tuple[float, float, float]:
    """The ring-coloured pixels' mean hue (through 0: 179 is -1), saturation and value."""
    x0, y0, x1, y1 = box
    sel = mask[y0:y1, x0:x1] > 0
    if not sel.any():
        return 0.0, 0.0, 0.0
    px = hsv[y0:y1, x0:x1][sel].astype(np.float32)
    hue = np.where(px[:, 0] >= 90, px[:, 0] - 180, px[:, 0])
    return float(hue.mean()), float(px[:, 1].mean()), float(px[:, 2].mean())


def _on_body(cx: float, cy: float, people: list[Person] | None) -> float:
    """The tallest person (box height) whose body below the head, or whose feet, hold this
    point: a jersey, the trim of a pair of shorts or a shoe is ring-coloured too. A rim is
    3 m up, so a player near it in the picture has it above the head."""
    tallest = 0.0
    for p in people or ():
        top, bottom = p.y - p.h / 2, p.y + p.h / 2
        body = abs(cx - p.x) <= p.w / 2 and top + BODY_TOP * p.h <= cy <= bottom
        feet = abs(cx - p.x) <= p.w / 2 + FEET_X and abs(cy - bottom) <= FEET_Y * p.h
        if body or feet:
            tallest = max(tallest, p.h)
    return tallest


def _score(c: dict) -> float:
    """The plan's weighting: shape (the filter's response and the ring's colour), the board
    (its border, or a bright painted board above), the net under it and the position."""
    colour = _ramp(c["hue"], *HUE_FIT) * _ramp(c["sat"], *SAT_FIT) * _ramp(c["val"], *VAL_FIT)
    shape = min(1.0, c["resp"] / RESP_FULL) * colour
    board = max(_board_evidence(c["board_len"], c["board_in"]), BRIGHT_EV if c["bright"] >= BRIGHT_MIN else 0.0)
    net = min(1.0, c["net"] / NET_FULL)
    return W_SHAPE * shape + W_BOARD * board + W_NET * net + W_POS * c["pos"]


def candidates(frame_bgr: np.ndarray, people: list[Person] | None = None) -> list[dict]:
    """The strongest ring-like shapes with their evidence and score: the rims and what was
    turned down (with why), for find_rims and the debug output."""
    small = _working_copy(frame_bgr)
    sh, sw = small.shape[:2]
    r0, r1 = int(ROWS[0] * sh), int(ROWS[1] * sh)
    hsv = cv2.cvtColor(small[r0:r1], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, S_MIN, V_MIN), (HUE_HI, 255, 255))
    mask |= cv2.inRange(hsv, (HUE_WRAP, S_MIN, V_MIN), (179, 255, 255))
    gray = cv2.cvtColor(small[r0:r1], cv2.COLOR_BGR2GRAY)
    strands = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    white = (strands >= NET_TOPHAT) & (cv2.inRange(hsv, (0, 0, NET_V), (179, NET_S, 255)) > 0)
    bright = cv2.inRange(hsv, (0, 0, BRIGHT_V), (179, BRIGHT_S, 255))
    half = (max(1, sw // FILTER_DOWN), max(1, (r1 - r0) // FILTER_DOWN))
    colour = cv2.resize(mask.astype(np.float32) / 255.0, half, interpolation=cv2.INTER_AREA)
    best, best_w = _ring_response(colour)
    out = []
    for resp, px, py, ring_w in _peaks(best, best_w):
        box_px = _refine(mask, px, py, ring_w)
        if box_px is None:
            continue
        x, y, x1, y1 = box_px
        w, h = x1 - x, y1 - y
        box = (x / sw, (y + r0) / sh, x1 / sw, (y1 + r0) / sh)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        cand = {"box": box, "resp": round(resp, 3), "w": round(w / sw, 4), "aspect": round(w / max(1, h), 2),
                "board": None, "board_len": 0.0, "board_in": 0.0, "score": 0.0, "rejected": ""}
        out.append(cand)
        if not W_MIN <= cand["w"] <= W_MAX:
            cand["rejected"] = "size"
            continue
        if not ASPECT_MIN <= cand["aspect"] <= ASPECT_MAX:
            cand["rejected"] = "aspect"
            continue
        hue, sat, val = _colour(hsv, mask, box_px)
        cand.update(fill=round(_share(mask, x, y, x1, y1), 3),
                    core=round(_share(mask, x + 3 * w // 10, y + 3 * h // 10, x1 - 3 * w // 10, y1 - 3 * h // 10), 3),
                    hue=round(hue, 2), sat=round(sat, 1), val=round(val, 1),
                    body=round(_on_body(cx, cy, people), 3), torso=_in_torso(cx, cy, people),
                    net=round(_share(white, x + w // 7, y1, x1 - w // 7, y1 + round(NET_DOWN * w)), 3),
                    bright=round(_share(bright, x - w // 3, y - round(1.5 * w), x1 + w // 3, y - 1), 3),
                    pos=round(_ramp(cy, ROWS[0], ROWS[1], POS_IDEAL), 3))
        if cand["torso"] or cand["body"] >= BODY_H:
            cand["rejected"] = "person"
            continue
        if _score(cand) + W_BOARD >= THRESHOLD:     # the board search only where it can decide
            board, outer_len, inner_len = _best_board(frame_bgr, box, None)
            cand.update(board=board if outer_len >= BB_KEEP_LEN else None, board_len=round(outer_len, 3),
                        board_in=round(inner_len, 3))
        cand["score"] = round(_score(cand), 4)
        if cand["score"] < THRESHOLD:
            cand["rejected"] = "score"
    return out


def find_rims(frame_bgr: np.ndarray, people: list[Person] | None = None) -> list[RimCand]:
    """The rims in one frame (at most MAX_RIMS), best first, each with its backboard when the
    board's border was found. `people` are the frame's person boxes: a ring-coloured shape on
    a torso is a jersey."""
    kept: list[dict] = []
    for c in sorted((c for c in candidates(frame_bgr, people) if not c["rejected"]),
                    key=lambda c: -c["score"]):
        if not kept:
            kept.append(c)
        elif _other_basket(c, kept[0]):
            kept.append(c)
            break
    rims = []
    for c in kept:
        x0, y0, x1, y1 = c["box"]
        rims.append(RimCand(x=(x0 + x1) / 2, y=(y0 + y1) / 2, w=x1 - x0, h=y1 - y0, score=c["score"],
                            src="classic", backboard=c["board"]))
    return rims


def _other_basket(c: dict, first: dict) -> bool:
    """A second rim is the court's other basket: across the frame from the first, at about
    its height and size (a game camera at centre court sees both from the same distance).
    Anything else beside a rim is its bracket, a piece of it or something red nearby."""
    a, b = c["box"], first["box"]
    wa, wb = a[2] - a[0], b[2] - b[0]
    return (abs((a[0] + a[2]) - (b[0] + b[2])) / 2 >= SECOND_DX
            and abs((a[1] + a[3]) - (b[1] + b[3])) / 2 <= SECOND_DY
            and max(wa, wb) <= SECOND_W * min(wa, wb))
