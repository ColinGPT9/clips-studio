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

Nothing here changes video/framing.py's is_cut, small_gray, refine_cuts or
CUT_DIFF, which the podcast and soccer paths use as they are.
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

# The colour measure's picture and histogram, as the basketball observer has them.
_COL_SIZE = (192, 108)
_HIST_BINS = [30, 32]    # hue x saturation


def find_cut(gray: list[float], col: list[float], luma: list[float] | None,
             first: int, last: int) -> int | None:
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
    """
    k = 1
    end = min(last, len(gray) - 1)
    while k <= end:
        if _is_hard(gray, col, k):
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


def _is_hard(gray: list[float], col: list[float], k: int) -> bool:
    g = gray[k]
    c = col[k] if k < len(col) else 0.0
    if g >= CUT_GRAY_STRONG and c >= CUT_COL_STRONG:
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


# ---- decoding the frames around a candidate ------------------------------------------------


class ShotCuts:
    """Candidate test and frame-by-frame check for one clip.

    `cap` is a capture of the clip the tracker opened for this alone (through
    video.capture.video_capture, which releases it), or None when it would
    not open. `fallback` is the tracker's own sample-grid threshold, used
    whenever the frames around a candidate cannot be read where they should
    be: the cut is then called the old way, on the sample, when the pair
    clears it.

    Candidates come in frame order, so the capture reads forward through the
    clip, keeping the measures of the last few frames for the next window,
    and seeks only across a long stretch with none.
    """

    def __init__(self, cap, fallback: float) -> None:
        self._cap = cap
        self._fallback = fallback
        self._pos = 0                       # the frame the capture returns next
        self._seen: dict[int, tuple] = {}   # frame -> (thumbnail, histogram, brightness)
        self._broken = cap is None

    def check(self, prev_small, cur_small, a: int, b: int) -> int | None:
        """The first frame of a new shot that starts after frame `a` and at or
        before frame `b`, the previous and the current sample, whose small_gray
        thumbnails are `prev_small` and `cur_small`; None when no cut starts
        there."""
        if prev_small is None or cur_small is None:
            return None
        import numpy as np

        pair = float(np.abs(cur_small - prev_small).mean())
        if pair < CANDIDATE_DIFF:
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
        k = find_cut(gray, col, [w[2] for w in window], a + 1 - lo, b - lo)
        # What the next candidate can still need: its window starts no earlier
        # than LOOK_BACK frames before this sample.
        self._seen = {i: m for i, m in self._seen.items() if i >= b - LOOK_BACK}
        return None if k is None else lo + k

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
