"""Framing a match for 9:16: follow the ball, not a face.

Face tracking frames whoever is biggest, which on a pitch is the nearest
player or the crowd, while the goal happens off the side of the crop. The
camera systems (Veo, Pixellot, Hudl, Trace) all do the same thing instead:
follow the ball, and when it's lost, where the players are. Here:

- the ball, from the detector the app already ships (models/yolov8n.pt, COCO
  "sports ball"), followed from sample to sample with jumps it can't make
  thrown out;
- when the ball is lost for more than BALL_MEMORY, the players: those near
  where the ball was last seen, else all of them;
- in a close-up (a player filling the frame: a celebration, a reaction), that
  player;
- moved by the shared HoldMove controller (video/framing.py), faster than for
  talk because play moves faster, and snapped at cuts, never panned across.

Cuts are found to the frame, the way the standard tracker's shot-aware mode
finds them (video/cuts.py): a sample pair that changes enough is only a
candidate, and the frames between the two samples are decoded with a second
capture to find the new shot's first frame, a camera flash not counted. A
match needs three of ShotCuts' options on top (measured on a goals
compilation at 50 fps):

- a colour gate: a match pans fast, so half its sample pairs changed by
  enough to be decoded frame by frame, and framing took 2-4 times as long. A
  pair is now a candidate only when its two samples' colours differ by
  COLOUR_GATE as well, which every cut's pair did (by 0.32 or more) and 9%
  of all pairs do along with the gray change;
- the colour-led rule: a cut straight out of a fast pan (gray 31-39, colour
  0.50-0.84) stood under both of the tracker's rules, and 5 of 60 were missed;
- dissolves: a replay fades in over 8-10 frames, none of which is a hard cut.
  The new shot's framing starts on the frame the fade completes.

The framing before the cut is held to the frame before that one, and the new
shot's starts on it. With one key per sample and the cut called on the
sample after it, the renderer, which interpolates between keys, slid the
crop across every cut for about 0.2 s. And the clip and every shot open on
the median of where the framing aims over their first OPENING seconds, so a
shot whose ball is confirmed a sample late no longer opens on the players
and then slides to the ball.

The result is the same crop path video/cropper.py renders for everything
else, so nothing downstream changes.
"""

import statistics
import threading

BALL, PERSON = 32, 0
SAMPLE_FPS = 5.0
BALL_CONF = 0.15        # the ball is small and blurred: take weak detections, then vet them
BALL_NEW_CONF = 0.3     # ...but a ball not already being followed needs a surer one (a
                        # steward's vest or a boot scores 0.15-0.3 at 1280 px)
PERSON_CONF = 0.35
BALL_MEMORY = 1.0       # seconds a ball position stays usable after it's lost
MAX_JUMP = 0.3          # share of the frame width the ball can move between samples (not at a cut)
NEAR_BALL = 0.25        # players this close to the last ball are the play
CLOSE_UP = 0.45         # a person this tall (share of the frame height) makes a close-up
OPENING = 0.5           # seconds: the clip and each shot open on the median target over this long
COLOUR_GATE = 0.2       # a sample pair is checked frame by frame for a cut only when its two
                        # samples' colours differ this much too (Bhattacharyya distance): video/cuts.py's
                        # CUT_COL, the least colour change any of its rules takes on the cut's frame

_models: dict = {}
_models_lock = threading.Lock()


def plan(samples: list[dict], crop_frac: float, fps: float) -> tuple[list[tuple[float, float]], dict]:
    """The crop path from what each sample saw. Each sample:
    {"t", "cut", "balls": [(x, y, conf)], "people": [(x, y, w, h)]} in frame
    fractions, and at a cut "cut_frame", the new shot's first frame (the
    sample's own frame when it is missing). `fps` is the clip's frame rate:
    the keys at a cut are frame numbers over it, as the renderer times its
    frames. Returns ([(t, crop centre x)], how often each source led).

    At a cut, the framing before it is held to the frame before the cut's
    frame (a freeze key) and the new shot's starts on that frame (a snap
    key), so nothing is interpolated across the cut. The clip and every
    shot open on the median of their targets over their first OPENING
    seconds (_opening), then follow as before: a trailing median of the
    shot's targets, the opening first, moved by HoldMove. A sample that
    sees nothing holds the last of them, the opening included."""
    from video.framing import HoldMove, stable_target

    lo, hi = crop_frac / 2, 1 - crop_frac / 2
    targets, led = _targets(samples, lo, hi)
    hold = HoldMove(move_trigger=0.05, settle=0.012, smoothing=0.35, max_pan_speed=0.8)
    path: list[tuple[float, float]] = []
    recent: list[float] = []     # this shot's targets, for the trailing median
    prev_t = None
    for i, s in enumerate(samples):
        t = float(s["t"])
        snap_t = None
        if prev_t is None:
            x = hold.snap(_opening(samples, targets, i))
            recent = [x]
        elif s.get("cut"):
            frame = s.get("cut_frame")
            cut_t = frame / fps if frame is not None else t
            freeze_t = (frame - 1) / fps if frame is not None else t - 1.0 / fps
            if path[-1][0] < freeze_t < cut_t:
                path.append((freeze_t, path[-1][1]))
            # The trailing median starts again from the opening, so nothing
            # from the shot before the cut pulls the new one back.
            x = hold.snap(_opening(samples, targets, i))
            recent = [x]
            if path[-1][0] < cut_t < t:
                snap_t = cut_t
        else:
            recent.append(targets[i] if targets[i] is not None else recent[-1])
            x = hold.update(stable_target(recent, window=3), t - prev_t)
        x = round(min(max(x, lo), hi), 4)
        if snap_t is not None:
            path.append((snap_t, x))
        path.append((t, x))
        prev_t = t
    return path, led


def _opening(samples: list[dict], targets: list[float | None], i: int) -> float:
    """Where a shot opens: the median of its targets over its first OPENING
    seconds, from sample i (its first) to before the next cut. Opened on its
    first sample's target, a shot whose ball was confirmed a sample late
    opened on the players and then slid to the ball.

    The time is counted from the shot's first sample, not its first frame,
    which can be up to a sample gap earlier: at SAMPLE_FPS that keeps three
    samples in it, so one off target is outvoted. Counted from the cut's
    frame it held only two on a third of cuts, and the median of two is
    their mean, halfway to the outlier. A sample that saw nothing doesn't
    count; a shot that sees nothing in that time opens on the centre, as
    before."""
    t0 = float(samples[i]["t"])
    seen = []
    for j in range(i, len(samples)):
        if j > i and (samples[j].get("cut") or float(samples[j]["t"]) > t0 + OPENING):
            break
        if targets[j] is not None:
            seen.append(targets[j])
    return float(statistics.median(seen)) if seen else 0.5


def _targets(samples: list[dict], lo: float, hi: float) -> tuple[list[float | None], dict]:
    """Where each sample's framing aims, inside lo..hi, None where it saw
    nothing (the framing holds: plan); and how often the ball, the players,
    a close-up, or nothing (held) led."""
    targets: list[float | None] = []
    ball: tuple[float, float] | None = None       # (x, when seen)
    led = {"ball": 0, "players": 0, "close-up": 0, "held": 0}
    for s in samples:
        t = float(s["t"])
        if s.get("cut"):
            ball = None
        # The ball: the detection nearest where it was, if it could have got there.
        found = None
        candidates = sorted(s.get("balls") or [], key=lambda b: -b[2])
        if ball is not None and t - ball[1] <= BALL_MEMORY:
            near = [b for b in candidates if abs(b[0] - ball[0]) <= MAX_JUMP * max(1.0, (t - ball[1]) * SAMPLE_FPS)]
            found = min(near, key=lambda b: abs(b[0] - ball[0])) if near else None
        elif candidates and candidates[0][2] >= BALL_NEW_CONF:
            found = candidates[0]
        if found is not None:
            ball = (found[0], t)
        people = s.get("people") or []
        close = [p for p in people if p[3] >= CLOSE_UP]
        if close:
            target, source = max(close, key=lambda p: p[2] * p[3])[0], "close-up"
        elif ball is not None and t - ball[1] <= BALL_MEMORY:
            target, source = ball[0], "ball"
        elif people:
            anchor = ball[0] if ball is not None else None
            near = [p[0] for p in people if anchor is not None and abs(p[0] - anchor) <= NEAR_BALL]
            xs = sorted(near or [p[0] for p in people])
            target, source = xs[len(xs) // 2], "players"
        else:
            target, source = None, "held"
        led[source] += 1
        targets.append(None if target is None else min(max(target, lo), hi))
    return targets, led


def _model(name: str):
    """The detector for the ball and the players: its own cache, since the
    face tracker's holds whichever model it was first asked for."""
    with _models_lock:
        if name not in _models:
            from ultralytics import YOLO

            from core.binaries import yolo_weights
            from core.gpu import cuda_usable

            model = YOLO(yolo_weights(name))
            usable, _reason = cuda_usable()
            if usable:
                model.to("cuda")
            _models[name] = model
        return _models[name]


def detect(model, frame, imgsz: int) -> tuple[list, list]:
    """(balls [(x, y, conf)], people [(x, y, w, h)]) in frame fractions."""
    from core.gpu import torch_device
    from video.tracker import _infer_lock

    h, w = frame.shape[:2]
    with _infer_lock:
        results = model.predict(frame, classes=[PERSON, BALL], conf=BALL_CONF, imgsz=imgsz,
                                device=torch_device(), verbose=False)
    balls, people = [], []
    for r in results:
        for b in r.boxes:
            x0, y0, x1, y1 = (float(v) for v in b.xyxy[0])
            cls, conf = int(b.cls[0]), float(b.conf[0])
            if cls == BALL:
                balls.append(((x0 + x1) / 2 / w, (y0 + y1) / 2 / h, conf))
            elif cls == PERSON and conf >= PERSON_CONF:
                people.append(((x0 + x1) / 2 / w, (y0 + y1) / 2 / h, (x1 - x0) / w, (y1 - y0) / h))
    return balls, people


def compute(clip_path, model_name: str = "yolov8n.pt", imgsz: int = 1280,
            sample_fps: float = SAMPLE_FPS) -> dict:
    """The crop path for one clip, in the form video/cropper.render_vertical takes."""
    import cv2

    from video.capture import video_capture
    from video.cuts import ShotCuts, colours
    from video.framing import CUT_DIFF, small_gray

    model = _model(model_name)
    samples = []
    # The cut reader decodes the frames around a candidate cut with a capture
    # of its own, on a thread of its own (_CutReader). When that capture will
    # not open, cuts are called on the sample, as before, when the pair changes
    # by more than CUT_DIFF (and, with the colour gate, by COLOUR_GATE in colour).
    with video_capture(clip_path) as cap, video_capture(clip_path, required=False) as cut_cap, \
            _CutReader(ShotCuts(cut_cap, fallback=CUT_DIFF, colour_gate=COLOUR_GATE, colour_led=True,
                                dissolves=True)) as cuts:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 16
        height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 9
        step = max(1, round(fps / sample_fps))
        prev_small, prev_index = None, 0
        index = 0
        while True:
            ok = cap.grab()
            if not ok:
                break
            if index % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                small = small_gray(frame)
                cuts.check(prev_small, small, prev_index, index, colours(frame))
                balls, people = detect(model, frame, imgsz)
                samples.append({"t": index / fps, "balls": balls, "people": people})
                prev_small, prev_index = small, index
            index += 1
    for s, cut in zip(samples, cuts.found):
        s["cut"], s["cut_frame"] = cut is not None, cut
    # The share of the source's width a 9:16 crop of its full height takes.
    crop_frac = min(1.0, (height * 9 / 16) / max(width, 1))
    path, led = plan(samples, crop_frac, fps)
    return {"mode": "track", "path": path or [(0.0, 0.5)], "led": led}


class _CutReader:
    """ShotCuts.check for every sample, in order, on a thread of its own.

    Finding a cut's frame decodes the clip a second time around each
    candidate, which the samples' own decoding and the detector (mostly on
    the GPU) leave cores free for. On a goals compilation (15 windows of
    25 s at 1080p50), made in turn with them the checks made Soccer's
    framing take 1.8 times as long as it took with cuts called on the
    sample grid and no second capture; beside them, 1.26 times (1.47 on the
    slowest window), and 1.27 times with three clips framed at once. The
    processor time is 1.6 times either way. The answers are the same: one
    thread makes every check, in the samples' order, with the same
    thumbnails and colours.

    The samples never wait for it: each hands over a few kilobytes (its
    thumbnail and its colours), not its picture. Leaving the `with` block
    waits for the last check and raises what went wrong on the thread; when
    the block itself fails, the checks still waiting are dropped. `found`
    is then each sample's cut frame, None for no cut."""

    def __init__(self, cuts) -> None:
        import queue

        self._cuts = cuts
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        self.found: list = []
        self._error: Exception | None = None
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name="soccer-cuts", daemon=True)

    def __enter__(self) -> "_CutReader":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stopped = exc_type is not None
        self._jobs.put(None)
        self._thread.join()
        if exc_type is None and self._error is not None:
            raise self._error

    def check(self, prev_small, small, a: int, b: int, hist) -> None:
        self._jobs.put((prev_small, small, a, b, hist))

    def _run(self) -> None:
        while (job := self._jobs.get()) is not None:
            if self._stopped or self._error is not None:
                continue            # the rest is dropped
            prev_small, small, a, b, hist = job
            try:
                self.found.append(self._cuts.check(prev_small, small, a, b, colours=hist))
            except Exception as e:  # raised by __exit__
                self._error = e
