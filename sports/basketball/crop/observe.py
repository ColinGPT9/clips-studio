"""What the picture and the detectors say about one clip: its Observation.

Two FFmpeg decodes of the 16:9 clip run side by side (NVDEC when there is
one, the CPU when that fails):

- **Pass A1, every frame, small.** Per frame, how much the picture changed
  (`d_gray`, the mean gray difference of 48x27 thumbnails, the measure
  video/framing.is_cut uses; `d_col`, the Bhattacharyya distance between
  hue/saturation histograms at 192x108), how far the camera
  moved (`pan_dx`, `pan_dy`, by phase correlation at 384x216 over the rows
  the score bug and the courtside boards leave alone) and the mean
  brightness (`luma`, for the flash test). Cuts are then frame-exact, which
  a 5 Hz grid never was, and the camera's travel lets the planning core
  follow the ball in court coordinates rather than in the picture.
- **Pass A2, the detection frames at full size.** About 6 a second, and 15
  a second from 4 s before the basket to 2 s after it (10 a second when the
  clip has no basket time). Each goes through the configured sources
  (sources.py): ball candidates with their size, colour and contrast; people;
  rims. Each is also measured for the floor's colour in its lower middle and
  for the score bug being up.

Then, with both done: the cuts (shots.find_cuts) reset the camera's travel
at each shot's first frame, the floor's colour is the clip's most common one
in the lower middle of its wide-looking samples, and the score bug, found
once by the score reader's OCR, is "up" on a sample whose bug region looks
like the clip's usual bug.

Not pure: numpy, cv2 and FFmpeg, imported inside the functions.
"""

from __future__ import annotations

import math
import subprocess
import threading
import time

from sports.basketball.crop.model import FrameSignals, Geometry, Observation, Sample

# ---- pass A1: every frame, small ------------------------------------------------------------

# Pass A1 decodes at PAN_SIZE (width, height). The camera's motion is read there: at 192x108 a
# broadcast's slow pans were lost in the sub-pixel error and bogus peaks (a shot's travel off by
# 0.12 of the width at the median over 15 shots of six bench clips, 0.38 at the 90th percentile,
# against a 960x540 reference), at 384x216 they held (0.016 and 0.031) for 0.6 ms a frame more.
# The change measures use SMALL, as the spec has them.
PAN_SIZE = (384, 216)
SMALL = (192, 108)          # d_col's picture
THUMB = (48, 27)            # video/framing.small_gray's thumbnail, for d_gray
HIST_BINS = [30, 32]        # hue x saturation bins, for d_col
PAN_ROWS = (0.08, 0.78)     # camera motion is measured between these rows: the score bug and the boards left out
PAN_MIN_RESPONSE = 0.05     # a phase-correlation peak weaker than this is no motion estimate (a cut, a flash)
PAN_HOP = 0.05              # a new anchor frame once the picture moved this share of the width (19 px)...
PAN_AGE = 30                # ...or after this many frames
PAN_JUMP = 18.0             # a gray change this big (shots.CUT_GRAY) is a cut or a flash: no motion read across it
PAN_MAX_SPEED = 3.0         # shares of the width (or height) a second: a faster "move" is a wrong peak

# ---- pass A2: the detection frames --------------------------------------------------------

BASE_HZ = 6.0               # detection frames a second over the whole clip...
DENSE_HZ = 15.0             # ...and around the play, from DENSE_BEFORE before the basket to DENSE_AFTER after
DENSE_BEFORE = 4.0
DENSE_AFTER = 2.0
FREE_HZ = 10.0              # everywhere, when the clip has no basket time
LOOK = (320, 180)           # each detection frame is also kept this small, for the floor's colour and the bug

COURT_ROWS = (0.55, 0.85)   # the lower middle of the picture: the floor, in a wide shot
COURT_COLS = (0.25, 0.75)
COURT_BINS = (18, 8)        # hue x saturation bins the floor's colour is one of
COURT_NEAR = 1              # ...counting the bins this many steps around it (the light varies across a court)
FLOOR_TONE = 0.15           # ...and each other bin holding this share of the wide-looking samples' (a second tone)
WIDE_TALL = 0.36            # a sample looks wide when nobody is this tall (share of the height)...
WIDE_PEOPLE = 3             # ...and at least this many people are
WIDE_MIN = 5                # the floor's colour comes from the wide-looking samples when there are this many

BUG_STEADY = 6.0            # a bug pixel within this many gray levels of its usual value in most samples is steady
BUG_STEADY_SHARE = 0.25     # with less than this share of the box steady, there is no bug to tell about
BUG_CORR = 0.25             # the bug is up when its steady pixels correlate with their usual look at least this much


class _Stop(Exception):
    """Pass A1 stopping because pass A2 did (an error or a cancel)."""


def _check(cancel) -> None:
    """Stop when asked. `cancel` may raise itself (core.cancel.check_active)
    or return True."""
    if cancel is not None and cancel():
        from core.cancel import CancelledError

        raise CancelledError("basketball framing")


# ---- decoding --------------------------------------------------------------------------------


def probe(clip_path) -> dict:
    """Width, height, frame rate and duration of the clip's video."""
    import json

    from core.binaries import ffprobe

    r = subprocess.run(
        [ffprobe(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,duration:format=duration",
         "-of", "json", str(clip_path)],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe could not read {clip_path}: {r.stderr.strip()[-300:]}")
    doc = json.loads(r.stdout or "{}")
    stream = (doc.get("streams") or [{}])[0]

    def rate(text) -> float:
        try:
            num, den = (float(x) for x in str(text).split("/"))
            return num / den if den else 0.0
        except ValueError:
            return 0.0

    fps = rate(stream.get("r_frame_rate")) or rate(stream.get("avg_frame_rate")) or 30.0
    duration = 0.0
    for text in (stream.get("duration"), (doc.get("format") or {}).get("duration")):
        try:
            duration = float(text)
            break
        except (TypeError, ValueError):
            continue
    if not duration and stream.get("nb_frames"):
        duration = int(stream["nb_frames"]) / fps
    return {"width": int(stream.get("width") or 0), "height": int(stream.get("height") or 0),
            "fps": fps, "duration": duration}


def _drain(pipe, tail: list) -> threading.Thread:
    """Read FFmpeg's stderr as it comes, so a chatty decoder never fills the
    pipe and stalls; the last of it is kept for the error message."""
    def run():
        for line in iter(pipe.readline, b""):
            tail.append(line.decode("utf-8", "replace"))
            del tail[:-20]
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def frames(clip_path, filters: list[str], width: int, height: int):
    """Yield the clip's frames as (height, width, 3) BGR arrays from FFmpeg,
    after `filters` (-vf arguments). Hardware decode first; if that fails
    before the first frame, the CPU. The frames are views of their own
    buffers, safe to keep."""
    import numpy as np

    from core.binaries import ffmpeg
    from video.encoding import hwaccel_input_args

    size = width * height * 3
    for hw in (True, False):
        cmd = [ffmpeg(), "-v", "error", "-nostdin", *(hwaccel_input_args() if hw else []),
               "-i", str(clip_path), "-an", "-sn", "-dn", *filters,
               "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                bufsize=size)
        tail: list[str] = []
        drain = _drain(proc.stderr, tail)
        got = 0
        try:
            while True:
                buf = proc.stdout.read(size)
                if len(buf) < size:
                    break
                got += 1
                yield np.frombuffer(buf, np.uint8).reshape(height, width, 3)
            proc.wait(timeout=30)
        finally:
            # A consumer that stops early (a cancel, an error) must not leave FFmpeg
            # writing into a pipe nobody reads.
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
            proc.wait()
            drain.join(timeout=5)
            proc.stderr.close()
        if proc.returncode == 0 or got:
            if proc.returncode != 0:
                print(f"      Basketball framing: FFmpeg stopped after {got} frames: {''.join(tail).strip()[-200:]}")
            return
        if not hw:
            raise RuntimeError(f"FFmpeg could not decode {clip_path}: {''.join(tail).strip()[-300:]}")
        print("      Basketball framing: hardware decode failed, decoding on the CPU")


# ---- pass A1 ---------------------------------------------------------------------------------


def frame_signals(clip_path, fps: float, width: int, height: int, stop: threading.Event | None = None,
                  cancel=None) -> FrameSignals:
    """Pass A1: one value per frame of how much the picture changed and how
    far the camera moved since the previous frame (see the module's notes).

    pan_dx > 0 means the camera moved right (the picture moved left), in
    shares of the width; pan_dy > 0 that it moved down, in shares of the
    height. Both are 0 on the first frame and where the correlation has no
    clear peak."""
    import cv2
    import numpy as np

    pw, ph = PAN_SIZE
    sig = FrameSignals(fps=fps, width=width, height=height)
    camera = _Camera(fps)
    prev_thumb = prev_hist = None
    stream = frames(clip_path, ["-vf", f"scale={pw}:{ph}:flags=area"], pw, ph)
    try:
        for k, frame in enumerate(stream):
            if stop is not None and stop.is_set():
                raise _Stop
            if k % 30 == 0:
                _check(cancel)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            thumb = cv2.resize(gray, THUMB, interpolation=cv2.INTER_AREA).astype(np.float32)
            small = cv2.resize(frame, SMALL, interpolation=cv2.INTER_AREA)
            hist = cv2.calcHist([cv2.cvtColor(small, cv2.COLOR_BGR2HSV)], [0, 1], None, HIST_BINS,
                                [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            d_gray = d_col = 0.0
            if prev_thumb is not None:
                d_gray = float(np.abs(thumb - prev_thumb).mean())
                d_col = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
            dx, dy = camera.step(gray, d_gray >= PAN_JUMP)
            sig.d_gray.append(round(d_gray, 3))
            sig.d_col.append(round(d_col, 4))
            sig.pan_dx.append(dx)
            sig.pan_dy.append(dy)
            sig.luma.append(round(float(gray.mean()), 2))
            prev_thumb, prev_hist = thumb, hist
    finally:
        stream.close()          # stops FFmpeg now, not whenever the generator is collected
    return sig


class _Camera:
    """The camera's travel, frame by frame, by phase correlation of the
    PAN_SIZE gray frames' middle rows (PAN_ROWS: the bug and the boards out).

    Each frame is measured against an anchor frame, not just the one before:
    the sub-pixel peak of a one-pixel shift is pulled toward the whole pixel
    (a synthetic 1.2 px a frame pan at 192 px read 1.02, 15% short, frame to
    frame), and summed over a shot that error grows with every frame. Against
    an anchor the shift is several pixels, so the same fraction-of-a-pixel
    error is a few percent, paid once per anchor. A new anchor is taken once
    the picture has moved PAN_HOP, after PAN_AGE frames (players move, the
    camera zooms), when the anchor no longer correlates or reads a move
    faster than PAN_MAX_SPEED (a wrong peak), and on a big change (a cut, a
    flash). The frame-to-frame correlation stands in for those frames."""

    def __init__(self, fps: float):
        import cv2

        pw, ph = PAN_SIZE
        self.r0, self.r1 = round(PAN_ROWS[0] * ph), round(PAN_ROWS[1] * ph)
        self.window = cv2.createHanningWindow((pw, self.r1 - self.r0), cv2.CV_32F)
        self.max_step = PAN_MAX_SPEED / max(1.0, fps)
        self.prev = self.anchor = None
        self.age = 0
        self.at = (0.0, 0.0)          # the travel at the anchor
        self.now = (0.0, 0.0)         # the travel at the previous frame

    def _shift(self, a, b) -> tuple[float, float] | None:
        """The camera's move from picture a to picture b (shares of the
        width and height), or None without a clear peak."""
        import cv2

        (sx, sy), response = cv2.phaseCorrelate(a, b, self.window)
        if response < PAN_MIN_RESPONSE:
            return None
        pw, ph = PAN_SIZE
        return -sx / pw, -sy / ph      # the picture moved (sx, sy); the camera the other way

    def _plausible(self, now: tuple[float, float]) -> bool:
        return abs(now[0] - self.now[0]) <= self.max_step and abs(now[1] - self.now[1]) <= self.max_step

    def step(self, gray, jump: bool) -> tuple[float, float]:
        """This frame's pan_dx, pan_dy."""
        import numpy as np

        band = gray[self.r0:self.r1].astype(np.float32)
        if self.prev is None:
            self.prev = self.anchor = band
            return 0.0, 0.0
        moved = None if jump else self._shift(self.anchor, band)
        now = (self.at[0] + moved[0], self.at[1] + moved[1]) if moved is not None else None
        if now is not None and self._plausible(now):
            self.age += 1
            if (abs(moved[0]) >= PAN_HOP or abs(moved[1]) >= PAN_HOP or self.age >= PAN_AGE):
                self.anchor, self.at, self.age = band, now, 0
        else:
            step = None if jump else self._shift(self.prev, band)
            now = (self.now[0] + step[0], self.now[1] + step[1]) if step else self.now
            if not self._plausible(now):
                now = self.now
            self.anchor, self.at, self.age = band, now, 0
        before = self.now
        self.now = (round(now[0], 6), round(now[1], 6))
        self.prev = band
        return round(self.now[0] - before[0], 6), round(self.now[1] - before[1], 6)


# ---- pass A2 ---------------------------------------------------------------------------------


def schedule(fps: float, moment_t: float | None) -> tuple[int, int, tuple[int, int] | None]:
    """(base step, dense step, dense frames [a, b] or None): every base-th
    frame is a detection frame, and every dense-th inside [a, b]."""
    if moment_t is None:
        return max(1, round(fps / FREE_HZ)), 0, None
    base, dense = max(1, round(fps / BASE_HZ)), max(1, round(fps / DENSE_HZ))
    a = max(0, math.ceil((moment_t - DENSE_BEFORE) * fps))
    b = math.floor((moment_t + DENSE_AFTER) * fps)
    return base, dense, ((a, b) if b >= a else None)


def selected(n: int, base: int, dense: int, span: tuple[int, int] | None) -> bool:
    """Whether frame n is a detection frame: the same rule as select_filter."""
    return n % base == 0 or (span is not None and span[0] <= n <= span[1] and n % dense == 0)


def select_filter(base: int, dense: int, span: tuple[int, int] | None) -> str:
    """FFmpeg's select expression for the detection frames (commas escaped:
    they separate filter arguments otherwise)."""
    expr = f"not(mod(n\\,{base}))"
    if span is not None:
        expr += f"+between(n\\,{span[0]}\\,{span[1]})*not(mod(n\\,{dense}))"
    return f"select='{expr}'"


def _indexes(base: int, dense: int, span: tuple[int, int] | None):
    n = 0
    while True:
        if selected(n, base, dense, span):
            yield n
        n += 1


def _court_hist(look):
    """The hue x saturation histogram of a small frame's lower middle."""
    import cv2

    h, w = look.shape[:2]
    region = look[round(COURT_ROWS[0] * h):round(COURT_ROWS[1] * h), round(COURT_COLS[0] * w):round(COURT_COLS[1] * w)]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    return cv2.calcHist([hsv], [0, 1], None, list(COURT_BINS), [0, 180, 0, 256]).reshape(COURT_BINS)


def court_shares(hists: list, samples: list[Sample]) -> list[float]:
    """Each sample's share of its lower middle in the floor's colour. The
    floor's colour is the most common hue x saturation bin (with the bins
    around it) over the clip's wide-looking samples, or over all of them when
    too few look wide: a clip opening on a close-up still learns the floor
    from the court shots after it. Learned from wide-looking samples, every
    other bin holding FLOOR_TONE of them is the floor's too: a floor of two
    tones (a light wood and a darker painted key, each 15-45% of a home
    network's close shots of it) read 12% floor on its lighter half, and
    the court as the crowd."""
    import numpy as np

    if not hists:
        return []
    wide = [h for h, s in zip(hists, samples)
            if len(s.people) >= WIDE_PEOPLE and max(p.h for p in s.people) < WIDE_TALL * s.scale]
    total = np.sum(wide if len(wide) >= WIDE_MIN else hists, axis=0)
    tones = {tuple(int(v) for v in np.unravel_index(int(np.argmax(total)), total.shape))}
    if len(wide) >= WIDE_MIN:
        tones |= {(int(a), int(b)) for a, b in np.argwhere(total >= FLOOR_TONE * max(1e-9, float(total.sum())))}
    mask = np.zeros(COURT_BINS, bool)
    for hue, sat in tones:
        for dh in range(-COURT_NEAR, COURT_NEAR + 1):      # hue wraps around: red is both ends
            mask[(hue + dh) % COURT_BINS[0], max(0, sat - COURT_NEAR):sat + COURT_NEAR + 1] = True
    return [round(float(h[mask].sum() / max(1.0, h.sum())), 4) for h in hists]


def find_bug(looks: dict, duration: float) -> tuple[float, float, float, float] | None:
    """The TV scoreboard's box (corners, frame fractions), found the way the
    current framing finds it (scoreboard.find_text over a few frames, under
    the OCR lock); None without the OCR or without a bug."""
    if not looks:
        return None
    from analysis import game_text

    if not game_text.available():
        return None
    from analysis.game_text import _ocr
    from sports.basketball import scoreboard

    times = sorted(looks)

    def grab(t: float):
        return looks[min(times, key=lambda x: abs(x - t))]

    with scoreboard.OCR:
        found = scoreboard.find_text(grab, duration, _ocr)
    if found is None:
        return None
    return tuple(round(float(v), 4) for v in found[0])


def bug_flags(grays: list, box: tuple[float, float, float, float]) -> list[bool | None]:
    """Whether the score bug is up on each sample: the pattern of the bug's
    steady pixels (those that hardly change over most samples: its graphic)
    correlating with their usual look, the per-pixel median over the
    samples. A replay's wipe or a graphic in its place replaces the pattern.

    Correlation, not a difference: the bug is see-through, so over a bright
    floor it is brighter all over, and it animates for a second after a
    basket; a caption also joins it for a few seconds. On the 32 bench clips
    a mean-difference test flagged 191 of 5593 samples, every one still
    showing the bug when checked by eye. The correlation was 0.39 or more
    on every sample with the bug up and -0.18 to -0.03 on the two kinds of
    replacement there (a replay wipe's banner, a player's lower third).
    None for every sample when too little of the box is steady to tell (no
    bug in this clip, or a box from another video)."""
    import numpy as np

    if not grays:
        return []
    h, w = grays[0].shape[:2]
    x0, y0 = int(box[0] * w), int(box[1] * h)
    x1, y1 = max(x0 + 1, math.ceil(box[2] * w)), max(y0 + 1, math.ceil(box[3] * h))
    stack = np.stack([g[y0:y1, x0:x1].astype(np.float32) for g in grays])
    usual = np.median(stack, axis=0)
    steady = np.median(np.abs(stack - usual), axis=0) <= BUG_STEADY
    if steady.mean() < BUG_STEADY_SHARE:
        return [None] * len(grays)
    seen = stack[:, steady]
    seen = seen - seen.mean(axis=1, keepdims=True)
    ref = usual[steady] - usual[steady].mean()
    corr = (seen * ref).sum(axis=1) / (np.sqrt((seen * seen).sum(axis=1) * (ref * ref).sum()) + 1e-6)
    return [bool(c >= BUG_CORR) for c in corr]


# ---- after both passes -----------------------------------------------------------------------


def _hard_cuts(sig: FrameSignals) -> list:
    """The spec's hard-cut rule alone, for when shots.find_cuts is not there."""
    from statistics import median

    from sports.basketball.crop.model import Cut

    gray, col, out = sig.d_gray, sig.d_col, []
    for k in range(1, sig.frames):
        near = gray[max(0, k - 15):k] + gray[k + 1:k + 16]
        b = max(2.0, median(near)) if near else 2.0
        if (gray[k] >= 18 and gray[k] >= 3.0 * b and col[k] >= 0.20) or (gray[k] >= 40 and col[k] >= 0.30):
            out.append(Cut(frame=k, t=k / sig.fps))
    return out


def cuts_of(sig: FrameSignals) -> list:
    try:
        from sports.basketball.crop.shots import find_cuts
    except ImportError:
        return _hard_cuts(sig)
    return find_cuts(sig)


def travel(sig: FrameSignals, cuts: list) -> tuple[list[float], list[float]]:
    """The camera's travel since its shot began, per frame: pan summed from
    each shot's first frame (where it is 0)."""
    starts = {c.frame for c in cuts}
    px, py = [0.0] * sig.frames, [0.0] * sig.frames
    for f in range(1, sig.frames):
        if f not in starts:
            px[f] = px[f - 1] + sig.pan_dx[f]
            py[f] = py[f - 1] + sig.pan_dy[f]
    return px, py


# ---- the whole observation -------------------------------------------------------------------


def observe(clip_path, sources: dict | None = None, moment_t: float | None = None, cancel=None,
            timings: dict | None = None, bug_box: tuple[float, float, float, float] | None = None,
            find_bug_box: bool = True, people_scale: float = 1.0) -> Observation:
    """Everything the framing decides from, for one clip: see the module's
    notes. `sources` is the framing.sources config (sources.Detectors);
    `moment_t` the basket's time in the clip, if known; `cancel` a callable
    that raises or returns True to stop. `timings`, when given, is filled
    with seconds per stage and the sources really used.

    `bug_box`: the score bug's corners when the caller already knows them
    (the bug stays put through a broadcast, and the score reader found it
    for the whole video). Finding it here costs the OCR about 2 s a frame
    on the CPU, 10-13 s a clip on the bench, so a caller framing many clips
    of one video should find it once and pass it. find_bug_box=False skips
    the bug altogether (Sample.bug stays None). `people_scale`: how tall the
    game camera shows the players (reactions.people_scale), Sample.scale."""
    import cv2

    from sports.basketball.crop.sources import Detectors
    from sports.core import scorebug

    started = time.perf_counter()
    stats = timings if timings is not None else {}
    info = probe(clip_path)
    width, height, fps = info["width"], info["height"], info["fps"]
    stats["probe"] = time.perf_counter() - started

    t0 = time.perf_counter()
    detectors = Detectors(sources, scale=people_scale, width=width)
    stats["load"] = time.perf_counter() - t0
    stats["sources"] = dict(detectors.used)

    # Pass A1 runs beside pass A2: one is the CPU's, the other mostly the GPU's.
    stop = threading.Event()
    a1: dict = {}

    def run_a1():
        t = time.perf_counter()
        cpu = time.thread_time()
        try:
            a1["sig"] = frame_signals(clip_path, fps, width, height, stop, cancel)
        except Exception as e:  # re-raised in the caller's thread
            a1["error"] = e
        a1["wall"], a1["cpu"] = time.perf_counter() - t, time.thread_time() - cpu

    thread = threading.Thread(target=run_a1, name="framing-a1", daemon=True)
    thread.start()

    base, dense, span = schedule(fps, moment_t)
    duration = info["duration"]
    # The frames the scoreboard's finder looks at, kept as they go by (at most 14 full frames).
    wanted = ([duration * (i + 1) / (scorebug.FIND_FRAMES + 1) for i in range(scorebug.FIND_FRAMES)]
              if duration > 0 and bug_box is None and find_bug_box else [])
    samples: list[Sample] = []
    hists, grays, looks = [], [], {}
    waited = measured = 0.0
    t_a2 = time.perf_counter()
    stream = frames(clip_path, ["-vf", select_filter(base, dense, span), "-fps_mode", "passthrough"],
                    width, height)
    try:
        indexes = _indexes(base, dense, span)
        while True:
            t = time.perf_counter()
            frame = next(stream, None)
            waited += time.perf_counter() - t
            if frame is None:
                break
            n = next(indexes)
            _check(cancel)
            if "error" in a1:
                break
            s = Sample(i=len(samples), frame=n, t=round(n / fps, 4),
                       dense=span is not None and span[0] <= n <= span[1], scale=people_scale)
            detectors.run(frame, s)
            t = time.perf_counter()
            look = cv2.resize(frame, LOOK, interpolation=cv2.INTER_AREA)
            hists.append(_court_hist(look))
            grays.append(cv2.cvtColor(look, cv2.COLOR_BGR2GRAY))
            while wanted and s.t >= wanted[0]:
                looks[wanted.pop(0)] = frame
            measured += time.perf_counter() - t
            samples.append(s)
    except BaseException:
        stop.set()
        thread.join(timeout=30)
        raise
    finally:
        stream.close()          # stops FFmpeg now, not whenever the generator is collected
    stats["a2"] = time.perf_counter() - t_a2
    t = time.perf_counter()
    thread.join()
    stats["a1_wait"] = time.perf_counter() - t
    stats["a2_decode_wait"] = waited
    stats["a2_measure"] = measured
    for key, sec in detectors.seconds.items():
        stats[f"detect_{key}"] = sec
    if "error" in a1:
        raise a1["error"]
    sig: FrameSignals = a1["sig"]
    stats["a1"], stats["a1_cpu"] = a1["wall"], a1["cpu"]

    t = time.perf_counter()
    if bug_box is None and find_bug_box:
        bug_box = find_bug(looks, duration or sig.frames / fps)
    looks.clear()
    stats["bug_find"] = time.perf_counter() - t

    t = time.perf_counter()
    cuts = cuts_of(sig)
    px, py = travel(sig, cuts)
    for s in samples:
        f = min(s.frame, sig.frames - 1)
        if f >= 0:
            s.pan = (round(px[f], 5), round(py[f], 5))
    for s, share in zip(samples, court_shares(hists, samples)):
        s.court = share
    if bug_box is not None:
        for s, up in zip(samples, bug_flags(grays, bug_box)):
            s.bug = up
    stats["post"] = time.perf_counter() - t
    stats["frames"], stats["samples"], stats["cuts"] = sig.frames, len(samples), len(cuts)
    stats["total"] = time.perf_counter() - started

    geometry = Geometry(width=width, height=height, fps=fps, frames=sig.frames)
    return Observation(geometry=geometry, signals=sig, samples=samples, bug_box=bug_box)
