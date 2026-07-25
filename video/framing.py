"""Shared framing components — the parts of the Podcast rebuild that are not
podcast-specific, factored out so every pipeline can use them.

The Podcast pipeline was rebuilt around three ideas that turned out to be
general truths about framing footage, not facts about podcasts:

  1. CUTS ARE BOUNDARIES. When the source hard-cuts to another camera, the
     old framing is meaningless. Panning across a cut is always wrong: the
     crop should snap, and per-subject state built up before the cut (mouth
     motion in particular) must be thrown away, not carried over.

  2. COMMIT TO ONE SUBJECT. Averaging two people's positions frames neither
     of them — it just parks the crop between two faces. Pick the person who
     is talking, else the most prominent, and put them in frame.

  3. HOLD, THEN MOVE DELIBERATELY. A camera that corrects every small error
     never settles; the constant micro-adjustment reads as drift and jitter.
     A held frame that moves only on real, sustained drift — and then moves
     decisively and stops — reads as intentional.

Point 1 and 2 are shared as plain functions; point 3 is the HoldMove
controller. Each pipeline still decides its own policy — the podcast path
freezes one crop per shot, the stream path pans continuously — but they now
share the primitives instead of each growing their own copy.
"""

from __future__ import annotations

import cv2
import numpy as np

# ---- cut detection ----------------------------------------------------------
# A camera cut changes nearly every pixel at once, so a mean absolute
# difference over a tiny grayscale thumbnail separates cuts from motion
# cleanly and costs almost nothing per frame.

CUT_DIFF = 25.0        # mean abs gray difference (0..255) that counts as a cut
_THUMB = (48, 27)      # enough structure to spot a scene change, cheap to build


def small_gray(frame) -> np.ndarray:
    """The tiny grayscale thumbnail cut detection compares."""
    return cv2.cvtColor(cv2.resize(frame, _THUMB), cv2.COLOR_BGR2GRAY).astype(np.float32)


def is_cut(prev_small, cur_small, threshold: float = CUT_DIFF) -> bool:
    """True when these two consecutive thumbnails straddle a camera cut.

    For frame-exact cut placement, pair this with refine_cuts rather than
    running it on every frame: detect the cut coarsely on the sample grid,
    then refine only the handful of frames around it. Differencing every
    frame decodes the whole video just to place cuts, which is the bulk of
    the cost when detection itself only samples.
    """
    if prev_small is None or cur_small is None:
        return False
    return float(np.abs(cur_small - prev_small).mean()) > threshold


def refine_cuts(clip_path, regions, threshold: float = CUT_DIFF) -> dict:
    """Pin each coarse cut to its exact frame.

    A coarse cut is only known to fall between two sample frames (a, b]. This
    decodes that short span and returns the frame with the largest change from
    its predecessor — the first frame of the new shot. Returns {b: exact_idx}
    keyed by the coarse sample index so callers can map it back.

    Decoding a few frames per cut is far cheaper than decoding every frame of
    the clip, which is what full-rate cut detection costs.
    """
    if not regions:
        return {}
    cap = cv2.VideoCapture(str(clip_path))
    out: dict[int, int] = {}
    try:
        for a, b in regions:
            # Seek a little before the span so the decoder is already producing
            # exact frames by the time we reach it (POS_FRAMES lands on the
            # preceding keyframe and reads forward).
            start = max(0, a - 1)
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            prev = None
            best_idx, best_diff = b, -1.0
            for idx in range(start, b + 1):
                ok, frame = cap.read()
                if not ok:
                    break
                cur = small_gray(frame)
                if prev is not None and idx > a - 1:
                    d = float(np.abs(cur - prev).mean())
                    if d > best_diff:
                        best_diff, best_idx = d, idx
                prev = cur
            out[b] = best_idx
    finally:
        cap.release()
    return out


# ---- committing to a subject -------------------------------------------------


def pick_focus(
    candidates: list,
    talk_rate,
    prominence,
    talk_floor: float = 0.004,
    talk_margin: float = 1.4,
) -> object:
    """Choose the ONE subject to frame, never a blend of several.

    The talker wins when one clearly leads — "clearly" meaning both above the
    floor (somebody is actually talking) and ahead of the runner-up by
    talk_margin, so two people mid-conversation don't hand the crop back and
    forth on noise. Otherwise the most prominent subject wins, which is what
    the footage itself is usually emphasising.

    candidates: any items; talk_rate/prominence: item -> float.
    Returns the chosen item (callers keep their own item shape).
    """
    if not candidates:
        raise ValueError("pick_focus needs at least one candidate")
    ordered = sorted(candidates, key=lambda c: -talk_rate(c))
    lead = talk_rate(ordered[0])
    if lead > talk_floor and (
        len(ordered) == 1 or lead > talk_margin * talk_rate(ordered[1])
    ):
        return ordered[0]
    return max(candidates, key=prominence)


# ---- holding vs moving -------------------------------------------------------


class HoldMove:
    """Hysteretic pan controller: hold still, move deliberately, then settle.

    A plain dead-zone + smoothing chain re-decides every sample, so the crop
    spends its life making corrections it immediately partly undoes — the
    camera oscillates inside a narrow band and never looks settled. This
    controller has two states instead:

      HOLD  - the crop does not move at all. It leaves this state only when
              the subject has drifted past move_trigger, i.e. a real change
              rather than detector noise.
      MOVE  - ease toward the target (same smoothing + pan-speed clamp as
              before, so a move still looks like a camera move) until the
              error falls under settle, then hold again.

    The result is long steady takes broken by a few purposeful moves, instead
    of continuous micro-correction.
    """

    def __init__(
        self,
        move_trigger: float = 0.055,
        settle: float = 0.012,
        smoothing: float = 0.45,
        max_pan_speed: float = 0.30,
    ) -> None:
        self.move_trigger = move_trigger
        self.settle = settle
        self.smoothing = smoothing
        self.max_pan_speed = max_pan_speed
        self.x: float | None = None
        self.moving = False

    def update(self, target: float, dt: float) -> float:
        """Advance one sample toward target; returns the crop center to use."""
        if self.x is None:
            self.x = float(target)
            return self.x
        if not self.moving and abs(target - self.x) > self.move_trigger:
            self.moving = True
        if self.moving:
            step = self.smoothing * (target - self.x)
            max_step = self.max_pan_speed * dt
            self.x += float(np.clip(step, -max_step, max_step))
            if abs(target - self.x) <= self.settle:
                self.moving = False
        return self.x

    def snap(self, x: float) -> float:
        """Jump straight to x and settle — for cuts, where easing is wrong."""
        self.x = float(x)
        self.moving = False
        return self.x


def stable_target(values: list[float], window: int = 5) -> float:
    """Median of the most recent samples.

    The per-sample subject center carries detector noise, and feeding that
    straight into the pan controller is what makes framing twitch. A short
    median rejects the odd bad box outright instead of averaging it in.
    """
    if not values:
        raise ValueError("stable_target needs at least one value")
    return float(np.median(values[-window:]))
