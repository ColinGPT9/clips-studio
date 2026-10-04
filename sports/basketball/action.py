"""Framing basketball for 9:16: the play, not a face.

A 16:9 court cropped to 9:16 keeps about a third of it. Following the
biggest face frames a player on the bench; following the ball alone loses
the rim on a drive. So the crop follows, in order:

- **A reaction shot** (a cutaway: no court, people filling the frame): the
  biggest person in it, the crowd member or the coach reacting, rather than
  staying where the court was.
- **A close-up** (a player filling the frame): that player.
- **The ball** (the app's YOLOv8n, COCO "sports ball", vetted the way
  soccer's is, sports/soccer/ball.py) with the players around it: the ball
  handler and the defenders near them, so a drive keeps both.
- **Toward the rim** as the ball heads for it: in a broadcast wide shot the
  baskets sit near the left and right edges, so when the ball is high or
  moving fast toward an edge, the crop leans that way to keep the rim in.
- **When the ball is lost**, the players.

Moved by the shared HoldMove controller and snapped at camera cuts, as
soccer's framing is. The detector and its input size come from
config/sports.yaml (`framing`). The output is the crop path
video/cropper.py renders for everything else.
"""

BALL_MEMORY = 0.8        # seconds a ball position stays usable after it's lost
MAX_JUMP = 0.35          # share of the frame width the ball can move between samples (not at a cut)
BALL_NEW_CONF = 0.3
NEAR_BALL = 0.2          # players this close to the ball are the play around it
BALL_SHARE = 0.65        # the ball's share of the target; the players near it the rest
CLOSE_UP = 0.45          # a person this tall makes a close-up
CROWD_PEOPLE = 6         # this many people and no ball, with no one near the floor's middle: a reaction shot
RIM_EDGE = 0.3           # a ball within this of an edge, heading to it, is going to that rim
RIM_LEAN = 0.25          # ...and the crop leans this share of its width toward it


def plan(samples: list[dict], crop_frac: float) -> tuple[list[tuple[float, float]], dict]:
    """The crop path from what each sample saw, in the form soccer's plan
    takes: {"t", "cut", "balls": [(x, y, conf)], "people": [(x, y, w, h)]}.
    Returns ([(t, crop centre x)], how often each source led)."""
    from video.framing import HoldMove, stable_target

    lo, hi = crop_frac / 2, 1 - crop_frac / 2
    hold = HoldMove(move_trigger=0.05, settle=0.012, smoothing=0.35, max_pan_speed=0.9)
    path: list[tuple[float, float]] = []
    recent: list[float] = []
    ball: tuple[float, float, float] | None = None       # (x, y, when seen)
    trail: list[tuple[float, float]] = []                 # (t, x) of the ball, for its direction
    led = {"reaction": 0, "close-up": 0, "ball": 0, "rim": 0, "players": 0, "held": 0}
    prev_t = None
    for s in samples:
        t = float(s["t"])
        if s.get("cut"):
            ball = None
            trail.clear()
            recent.clear()
        found = None
        candidates = sorted(s.get("balls") or [], key=lambda b: -b[2])
        if ball is not None and t - ball[2] <= BALL_MEMORY:
            near = [b for b in candidates if abs(b[0] - ball[0]) <= MAX_JUMP]
            found = min(near, key=lambda b: abs(b[0] - ball[0])) if near else None
        elif candidates and candidates[0][2] >= BALL_NEW_CONF:
            found = candidates[0]
        if found is not None:
            ball = (found[0], found[1], t)
            trail.append((t, found[0]))
            trail[:] = [p for p in trail if t - p[0] <= 1.0]
        people = s.get("people") or []
        close = [p for p in people if p[3] >= CLOSE_UP]
        have_ball = ball is not None and t - ball[2] <= BALL_MEMORY
        if close:
            target, source = max(close, key=lambda p: p[2] * p[3])[0], "close-up"
        elif not have_ball and len(people) >= CROWD_PEOPLE and s.get("reaction", False):
            target, source = max(people, key=lambda p: p[2] * p[3])[0], "reaction"
        elif have_ball:
            around = [p[0] for p in people if abs(p[0] - ball[0]) <= NEAR_BALL]
            target = ball[0]
            if around:
                target = BALL_SHARE * ball[0] + (1 - BALL_SHARE) * sorted(around)[len(around) // 2]
            source = "ball"
            # Heading for a rim: near an edge and moving toward it.
            if len(trail) >= 2:
                moving = trail[-1][1] - trail[0][1]
                if ball[0] <= RIM_EDGE and moving < -0.02:
                    target, source = target - RIM_LEAN * crop_frac, "rim"
                elif ball[0] >= 1 - RIM_EDGE and moving > 0.02:
                    target, source = target + RIM_LEAN * crop_frac, "rim"
        elif people:
            xs = sorted(p[0] for p in people)
            target, source = xs[len(xs) // 2], "players"
        elif recent:
            target, source = recent[-1], "held"
        else:
            target, source = 0.5, "held"
        led[source] += 1
        recent.append(min(max(target, lo), hi))
        steady = stable_target(recent, window=3)
        dt = (t - prev_t) if prev_t is not None else 0.2
        x = hold.snap(steady) if s.get("cut") or prev_t is None else hold.update(steady, dt)
        path.append((round(t, 3), round(min(max(x, lo), hi), 4)))
        prev_t = t
    return path, led


def compute(clip_path, model_name: str = "yolov8n.pt", imgsz: int = 960, sample_fps: float = 5.0) -> dict:
    """The crop path for one clip, in the form video/cropper.render_vertical takes."""
    import cv2

    import sports
    from sports.basketball.reactions import looks
    from sports.soccer.ball import _model, detect
    from video.capture import video_capture
    from video.framing import is_cut, small_gray

    settings = sports.spec("basketball").get("reactions") or {}
    court_share = float(settings.get("court_share", 0.3))
    crowd_edges = float(settings.get("crowd_edges", 0.12))
    model = _model(model_name)
    samples = []
    with video_capture(clip_path) as cap:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 16
        height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 9
        step = max(1, round(fps / sample_fps))
        prev_small = None
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
                balls, people = detect(model, frame, imgsz)
                thumb = cv2.resize(frame, (192, max(2, round(frame.shape[0] * 192 / max(frame.shape[1], 1)))))
                samples.append({"t": index / fps, "cut": is_cut(prev_small, small), "balls": balls,
                                "people": people,
                                "reaction": looks(thumb, court_share, crowd_edges) != "court"})
                prev_small = small
            index += 1
    crop_frac = min(1.0, (height * 9 / 16) / max(width, 1))
    path, led = plan(samples, crop_frac)
    return {"mode": "track", "path": path or [(0.0, 0.5)], "led": led}
