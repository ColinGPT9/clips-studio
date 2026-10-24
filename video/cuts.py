"""Frame-exact camera cuts for the tracker's shot-aware mode (tracking.shot_aware, on by default).

The tracker decodes one frame in every frame_step (four at 29.97 fps and the
default 8 samples a second) and, with this mode off, calls a cut only when a
sample pair's thumbnails differ by more than 55. On edited road-trip footage,
real cuts scored mostly 39-58 across a sample pair and some as low as about
17-30, so that missed 104 of 223 of them, placed 89 of the 119 it caught 1-3
frames late, on the sample after the cut, and called 76 cuts where there were none.
Differencing every frame instead fires on 4-13% of ordinary frames and costs
58% more time, so that is not the fix either.

So it is done in two steps. A sample pair that changes by CANDIDATE_DIFF or
more is only a candidate. The frames around it, and only those, are then
decoded with a capture of their own: from LOOK_BACK frames before the
previous sample (a local baseline of ordinary motion) to LOOK_AHEAD frames
after the current one (enough to see a flash end). A frame in between is a
cut when it passes the hard-cut rules sports/basketball/crop/shots.py uses
on broadcast footage:

  * a gray change of at least CUT_GRAY, a colour change of at least CUT_COL,
    and at least CUT_RATIO times the local baseline (a whip pan raises the
    baseline along with the frame), or
  * a gray change of at least CUT_GRAY_STRONG with a colour change of at least
    CUT_COL_STRONG, whatever the baseline;

and it is not a camera flash: a jump in brightness that comes back within
FLASH_MAX frames. The answer is the cut's exact frame, the first frame of
the new shot, so the crop can hold the old framing to the frame before it
and snap on it.

The rules live in find_cut, which is pure Python: lists of numbers in, a
frame index out. CI's light install has no numpy or OpenCV, and the
decision is what most needs testing. ShotCuts, which decodes and measures,
imports both when it runs.

Soccer's framing (sports/soccer/ball.py) uses ShotCuts too, with options the
tracker leaves off: a colour gate on the candidates, a colour-led hard-cut
rule, and dissolves (see "Soccer's additions" below). At their defaults
find_cut and ShotCuts answer exactly as they did before the options existed.

Nothing here changes video/framing.py's is_cut, small_gray, refine_cuts or
CUT_DIFF; the podcast path runs ShotCuts too. Soccer uses small_gray and
CUT_DIFF as they are: its samples' thumbnails, and its fallback (ShotCuts'
`fallback`).
"""

from __future__ import annotations

import statistics

# ---- candidates ---------------------------------------------------------------------------

# A sample pair whose small_gray thumbnails differ by this much (mean absolute
# difference, 0..255) is checked frame by frame. It is CUT_GRAY on purpose:
# a pair that changed less than the rule's own minimum cannot hold a frame
# that clears it, short of motion cancelling part of the change. Real cuts on
# road-trip footage scored mostly 39-58 across a sample pair, and some as low
# as about 17-30. The cost is in decoding: the lower this is, the more often a
# handheld stream pays for a look at the frames around a sample.
CANDIDATE_DIFF = 18.0

LOOK_BACK = 8      # frames decoded before the previous sample: the local baseline of ordinary motion
LOOK_AHEAD = 3     # frames decoded after the current sample: FLASH_MAX, so a flash on it can be seen ending
SKIP_MAX = 120     # the next window starts within this many frames of the reader: read on rather than
#                    seek (a seek decodes from the keyframe before it, and libx264's default keyframe
#                    interval is 250 frames)
ANCHOR_TOL = 1.0   # the window's copies of the two sample frames must match the tracker's own thumbnails
#                    this closely (mean gray difference), or the window is not where it was asked to be

# ---- the rules (adapted from sports/basketball/crop/shots.py) -------------------------------

CUT_GRAY = 18.0          # a cut changes the gray thumbnail at least this much...
CUT_RATIO = 3.0          # ...and at least this many times the local baseline...
CUT_COL = 0.20           # ...and the colours at least this much (Bhattacharyya distance, 0..1)
CUT_GRAY_STRONG = 40.0   # or a gray change this large...
CUT_COL_STRONG = 0.30    # ...with this much colour change, whatever the baseline
BASE_FLOOR = 2.0         # the baseline never drops below this, so a still picture's noise is no cut
FLASH_MAX = 3            # a camera flash lasts at most this many frames
FLASH_JUMP = 12.0        # brightness (0..255) must jump this much on the first frame for the flash test
FLASH_MATCH = 0.3        # ...and a frame after it be back within this share of the jump
FLASH_BACK = 0.6         # without brightness: a second change this share of the first, that soon, is a flash

# ---- Soccer's additions (keyword arguments, all off by default) ------------------------------
#
# Broadcast football pans fast: at 50 fps the gray thumbnail can move 14-27 a frame for
# seconds at a time, while its colours barely change (0.09 a frame: the pitch stays green).
#
# So half of a match's sample pairs change gray by CANDIDATE_DIFF or more, and decoding the
# frames around every one of them made Soccer's framing 2-4 times slower. With a colour gate
# (ShotCuts' colour_gate) a pair is a candidate only when its two samples' colours differ by
# the gate as well: on a goals compilation every cut's pair changed them by 0.32 or more, and
# 9% of all pairs passed both bars. A higher gray bar alone would not do: at 25 it still let
# through a quarter of the pairs, and lost three cuts whose pairs changed gray by 18.9-22.3.
#
# A cut out of such a pan changed gray by 31-39 with colour 0.50-0.84: under CUT_GRAY_STRONG,
# and under CUT_RATIO times a baseline the pan had raised, so 5 of 60 cuts went unseen.

COLOUR_GRAY = 25.0       # colour_led: a gray change this large...
COLOUR_COL = 0.45        # ...with this much colour change is a cut, whatever the baseline

# Replays fade in over 8-10 frames: no frame changes much, so no hard-cut rule sees them.
DISSOLVE_GRAY = 25.0     # dissolves: a sample pair that changes more than this in gray...
DISSOLVE_COL = 0.40      # ...and at least this much in colour (its two samples' histograms, and the
#                          frames LOOK_BACK before and LOOK_AHEAD after them, which a flash on a
#                          sample does not reach), with no hard cut between, is a dissolve...
DISSOLVE_STEP = 0.13     # ...and a frame whose colour changes this much from the one before is still
#                          part of it (ordinary frames of a match: 0.09, 99% under 0.14)

# The colour measure's picture and histogram, as the basketball observer has them.
_COL_SIZE = (192, 108)
_HIST_BINS = [30, 32]    # hue x saturation


def find_cut(gray: list[float], col: list[float], luma: list[float] | None,
             first: int, last: int, *, colour_led: bool = False) -> int | None:
    """The first frame of the new shot when a hard cut starts in first..last, else None.

    The lists describe a short decoded window, one entry per frame. Entry i
    compares frame i with frame i-1: `gray` is the mean difference of their
    small_gray thumbnails and `col` the Bhattacharyya distance between their
    hue/saturation histograms. Entry 0 has nothing before it and is never a
    cut. `luma` is each frame's mean brightness (0..255), or None, in which
    case a flash is told by the picture changing back. `first`..`last` are
    the window indices a new shot may start on: the frames after the
    previous sample, up to and including the current one.

    The first hard change that is not a flash wins. Two cuts inside one
    sample gap leave a shot of a frame or three between them, which no
    framing decision is about.

    The scan starts at the window's beginning, not at `first`, as the
    basketball scan runs through the whole clip: a flash that began on the
    previous sample is still seen as one, so the frame the picture comes
    back on, inside this gap, is not taken for a cut.

    `colour_led` adds a third hard-cut rule (Soccer turns it on): a gray
    change of at least COLOUR_GRAY with a colour change of at least
    COLOUR_COL, whatever the baseline. Off, the result is exactly as before.
    """
    k = 1
    end = min(last, len(gray) - 1)
    while k <= end:
        if _is_hard(gray, col, k, colour_led):
            back = _flash_end(gray, luma, k)
            if back is None:
                if k >= first:
                    return k
            else:
                k = back + 1    # the flash and the frame the picture came back on
                continue
        k += 1
    return None


def _baseline(gray: list[float], k: int) -> float:
    """The window's median change, frame k and the first entry (no predecessor) left out,
    never below BASE_FLOOR."""
    around = gray[1:k] + gray[k + 1:]
    return max(BASE_FLOOR, statistics.median(around)) if around else BASE_FLOOR


def _is_hard(gray: list[float], col: list[float], k: int, colour_led: bool = False) -> bool:
    g = gray[k]
    c = col[k] if k < len(col) else 0.0
    if g >= CUT_GRAY_STRONG and c >= CUT_COL_STRONG:
        return True
    if colour_led and g >= COLOUR_GRAY and c >= COLOUR_COL:
        return True
    return g >= CUT_GRAY and c >= CUT_COL and g >= CUT_RATIO * _baseline(gray, k)


def _flash_end(gray: list[float], luma: list[float] | None, k: int) -> int | None:
    """The frame the picture comes back on when the change at k is a flash, else None."""
    last = min(len(gray) - 1, k + FLASH_MAX)
    if luma is not None:
        jump = abs(luma[k] - luma[k - 1])
        if jump < FLASH_JUMP:
            return None
        for j in range(k + 1, last + 1):
            if abs(luma[j] - luma[k - 1]) <= FLASH_MATCH * jump:
                return j
        return None
    for j in range(k + 1, last + 1):
        if gray[j] >= FLASH_BACK * gray[k]:
            return j
    return None


def is_dissolve(pair_gray: float, pair_col: float, kept_col: float) -> bool:
    """Whether a sample pair with no hard cut between its samples is a dissolve.

    `pair_gray` is the small_gray difference of the two samples, `pair_col`
    the Bhattacharyya distance of their histograms, and `kept_col` the
    smaller of that distance from the first sample, and from the window's
    first frame (LOOK_BACK before it), to the window's last frame (LOOK_AHEAD
    after the second sample). A flash on either sample is over by then; a
    new shot is still there."""
    return pair_gray > DISSOLVE_GRAY and pair_col >= DISSOLVE_COL and kept_col >= DISSOLVE_COL


def dissolve_span(col: list[float], first: int, last: int) -> tuple[int, int] | None:
    """A dissolve's first and last frames: the run of frames whose colour
    changes by DISSOLVE_STEP or more from the one before that holds the
    steepest such frame in first..last. The dissolve completes on the last:
    on that frame the new picture is whole. None when no frame in
    first..last changes that much: a slow change, not one to snap at.

    `col` is the window's list as find_cut takes it. The run can start
    before `first`, and end past `last`: the change completes after the
    current sample, or is still going on the window's last frame."""
    first, end = max(1, first), min(last, len(col) - 1)
    if end < first:
        return None
    k = max(range(first, end + 1), key=col.__getitem__)
    if col[k] < DISSOLVE_STEP:
        return None
    start = k
    while start > 1 and col[start - 1] >= DISSOLVE_STEP:
        start -= 1
    while k + 1 < len(col) and col[k + 1] >= DISSOLVE_STEP:
        k += 1
    return start, k


# ---- decoding the frames around a candidate ------------------------------------------------


class ShotCuts:
    """Candidate test and frame-by-frame check for one clip.

    `cap` is a capture of the clip the tracker opened for this alone (through
    video.capture.video_capture, which releases it), or None when it would
    not open. `fallback` is the tracker's own sample-grid threshold, used
    whenever the frames around a candidate cannot be read where they should
    be: the cut is then called the old way, on the sample, when the pair
    clears it. With `colour_gate` set, a pair that changes less than the
    gate in colour is no candidate, so a fallback cut needs the gate's
    colour change as well.

    Candidates come in frame order, so the capture reads forward through the
    clip, keeping the measures of the last few frames for the next window,
    and seeks only across a long stretch with none.

    The options, all off by default (Soccer turns them on):

      * `colour_gate`: a pair is a candidate only when its two samples'
        histograms are at least this far apart too (Bhattacharyya distance).
        It needs each sample's colours(frame), passed to check() as
        `colours`; a pair whose first sample came without them is not gated;
      * `colour_led`: find_cut's colour-led rule;
      * `dissolves`: a pair with no hard cut between its samples that
        is_dissolve calls a dissolve is a cut on the frame the dissolve
        completes (dissolve_span). When it completes after the current
        sample, the next pair places it, whatever that pair's own change,
        from the frames of its own gap like any pair (not the frames before
        the gap that its window also reads); when it is still changing a
        pair later, it is placed on that pair's sample. A change that runs
        on from a dissolve already placed is not a second one.
    """

    def __init__(self, cap, fallback: float, *, colour_gate: float | None = None,
                 colour_led: bool = False, dissolves: bool = False) -> None:
        self._cap = cap
        self._fallback = fallback
        self._colour_gate = colour_gate
        self._colour_led = colour_led
        self._dissolves = dissolves
        self._dissolving = False            # a dissolve completes after the last sample checked
        self._placed: int | None = None     # the frame the last dissolve was placed on
        self._sample: tuple[int, object] | None = None   # (frame, histogram) of the last sample
        self._pos = 0                       # the frame the capture returns next
        self._seen: dict[int, tuple] = {}   # frame -> (thumbnail, histogram, brightness)
        self._broken = cap is None

    def check(self, prev_small, cur_small, a: int, b: int, *, colours=None) -> int | None:
        """The first frame of a new shot that starts after frame `a` and at or
        before frame `b`, the previous and the current sample, whose small_gray
        thumbnails are `prev_small` and `cur_small`; None when no cut starts
        there.

        `colours` is the current sample's colours(frame), for the colour gate,
        which keeps them for the next pair."""
        hists = None
        if self._colour_gate is not None and colours is not None:
            if self._sample is not None and self._sample[0] == a:
                hists = (self._sample[1], colours)
            self._sample = (b, colours)
        if prev_small is None or cur_small is None:
            return None
        import numpy as np

        pair = float(np.abs(cur_small - prev_small).mean())
        waiting, self._dissolving = self._dissolving, False
        if not waiting and (pair < CANDIDATE_DIFF or (
                hists is not None and _distance(*hists) < self._colour_gate)):
            return None
        lo = max(0, a - LOOK_BACK)
        window = None if self._broken else self._window(lo, b + LOOK_AHEAD)
        if window is None or len(window) <= b - lo or not (
            _same(window[a - lo][0], prev_small) and _same(window[b - lo][0], cur_small)
        ):
            # No frames, or not the frames that were asked for: the tracker
            # has already decoded both samples, so they exist. A seek that
            # landed off its frame would put every later window off too, so
            # the reader is not trusted again, and cuts are called the old
            # way, on the sample, for the rest of the clip.
            self._broken = True
            return b if pair > self._fallback else None

        import cv2

        gray, col = [0.0], [0.0]
        for (t0, h0, _), (t1, h1, _) in zip(window, window[1:]):
            gray.append(float(np.abs(t1 - t0).mean()))
            col.append(float(cv2.compareHist(h0, h1, cv2.HISTCMP_BHATTACHARYYA)))
        k = find_cut(gray, col, [w[2] for w in window], a + 1 - lo, b - lo, colour_led=self._colour_led)
        # What the next candidate can still need: its window starts no earlier
        # than LOOK_BACK frames before this sample.
        self._seen = {i: m for i, m in self._seen.items() if i >= b - LOOK_BACK}
        if k is not None:
            return lo + k
        if not self._dissolves or not (waiting or is_dissolve(
                pair, _distance(window[a - lo][1], window[b - lo][1]),
                min(_distance(window[a - lo][1], window[-1][1]), _distance(window[0][1], window[-1][1])))):
            return None
        # The run around the steepest frame in this gap, for a dissolve
        # waiting from the last pair too. The window reaches back past the
        # last gap at sample steps under LOOK_BACK, to where a cut or a flash
        # just before the dissolve can change more than any of its frames:
        # taken for the dissolve's, that run, over before frame a, placed it
        # mid-change, and the next pair placed it again where it ends. Nor is
        # it the run that holds frame a + 1: a fade's last frame, the one the
        # new picture is whole on, can change the most, after a frame that
        # changes less than DISSOLVE_STEP.
        span = dissolve_span(col, a + 1 - lo, b - lo)
        if span is None:
            return None
        start, end = lo + span[0], lo + span[1]
        if not waiting and self._placed is not None and start <= self._placed:
            return None         # the same change as a dissolve already placed, still going
        if end <= b:
            self._placed = end
            return max(end, a + 1)
        if waiting:
            # Still changing a pair later: a long dissolve, whose new picture
            # has taken over by now. It is placed on the sample.
            self._placed = b
            return b
        # It completes after this sample: the next pair places it, whatever
        # that pair's own change.
        self._dissolving = True
        return None

    def _window(self, lo: int, hi: int) -> list | None:
        """Measures of frames lo..hi, as many as the clip has; None when they cannot be read."""
        import cv2

        start = next((i for i in range(lo, hi + 1) if i not in self._seen), None)
        if start is not None:
            if start < self._pos or start - self._pos > SKIP_MAX:
                if not self._cap.set(cv2.CAP_PROP_POS_FRAMES, start):
                    return None
                self._pos = start
            while self._pos < start:
                if not self._cap.grab():
                    return None
                self._pos += 1
            while self._pos <= hi:
                ok, frame = self._cap.read()
                if not ok:
                    break
                self._seen[self._pos] = _measure(frame)
                self._pos += 1
        out = []
        for i in range(lo, hi + 1):
            if i not in self._seen:
                break
            out.append(self._seen[i])
        return out or None


def _measure(frame) -> tuple:
    """What the rules compare, for one frame: the tracker's own thumbnail, a
    normalised hue/saturation histogram, and the mean brightness."""
    import cv2

    from video.framing import small_gray

    small = cv2.resize(frame, _COL_SIZE, interpolation=cv2.INTER_AREA)
    hist = cv2.calcHist([cv2.cvtColor(small, cv2.COLOR_BGR2HSV)], [0, 1], None, _HIST_BINS,
                        [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    luma = float(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).mean())
    return small_gray(frame), hist, luma


def colours(frame):
    """A sample's colours, for ShotCuts' colour gate: the histogram _measure
    takes of a frame, made the same way."""
    import cv2

    small = cv2.resize(frame, _COL_SIZE, interpolation=cv2.INTER_AREA)
    hist = cv2.calcHist([cv2.cvtColor(small, cv2.COLOR_BGR2HSV)], [0, 1], None, _HIST_BINS,
                        [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist


def _distance(h0, h1) -> float:
    """The Bhattacharyya distance of two of _measure's histograms (0..1)."""
    import cv2

    return float(cv2.compareHist(h0, h1, cv2.HISTCMP_BHATTACHARYYA))


def _same(a, b) -> bool:
    import numpy as np

    return a.shape == b.shape and float(np.abs(a - b).mean()) <= ANCHOR_TOL


# ---- the setting ---------------------------------------------------------------------------

_ON = ("true", "yes", "on", "1")


def shot_aware_on(tracking) -> bool:
    """tracking.shot_aware from a run's tracking settings (config["tracking"]).

    On by default: with no tracking block, no shot_aware in it, or the key
    left empty (YAML reads `shot_aware:` with nothing after it as null), the
    mode is on. Set, it is read strictly:

      * on for true, the integer 1, or the text "true", "yes", "on" or "1";
      * off for false, the integer 0, or the text "false", "no", "off" or
        "0", which a plain truth test would have read as on;

    text in any case, spaces around it ignored. Any other value is off as
    well: another number (2, and 1.0 or 0.0, which YAML reads as decimals),
    other text ("", "enabled", a typo) or a list. That is the safe reading
    of a value nobody can be sure about: the framing as it was before the
    mode existed."""
    value = tracking.get("shot_aware") if isinstance(tracking, dict) else None
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in _ON
    return False
