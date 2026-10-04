"""Framing basketball for 9:16: the play, not a face.

A 16:9 court cropped to 9:16 keeps about a third of it. Following the
biggest face frames a player on the bench; following the ball alone loses
the rim on a drive. So the crop follows, in order:

- **A close-up or a reaction shot** (someone a third of the frame's height
  or more: a player, a fan, the coach; on three NBA games the court's
  players from the stands were 0.18-0.33 of it): the biggest of them,
  rather than staying where the court was.
- **The ball** (the app's YOLOv8n, COCO "sports ball", vetted the way
  soccer's is, sports/soccer/ball.py) with the players around it: the ball
  handler and the defenders near them, so a drive keeps both.
- **Toward the rim** as the ball heads for it: in a broadcast wide shot the
  baskets sit near the left and right edges, so when the ball is high or
  moving fast toward an edge, the crop leans that way to keep the rim in.
- **When the ball is lost**, the players on the floor, not the stands (people
  under half the tallest one's height are spectators).

A "ball" in the bottom fifth of the frame or at a player's feet is
dropped: on three NBA games those were the front rows, the score bug and
bright shoes far more often than the ball, and the detector's confidence
didn't tell them apart.

Moved by the shared HoldMove controller and snapped at camera cuts, as
soccer's framing is. A cut is told by the picture's colours changing as
well as its pixels: the shared test (video/framing.py's gray difference)
fired on a third to three quarters of a broadcast's samples, the camera
whipping across the court, and snapping on each made the crop jump. The
detector and its input size come from config/sports.yaml (`framing`). The
output is the crop path video/cropper.py renders for everything else.
"""

# Measured on three NBA games (docs/SPORTS.md):
BALL_MEMORY = 1.5        # seconds a ball position stays usable after it's lost (bridges 76-97% of gaps)
MAX_JUMP = 0.25          # share of the frame width the ball can move between samples (not at a cut)
BALL_NEW_CONF = 0.3
NEAR_BALL = 0.15         # players this close to the ball are the play around it (half the crop is 0.16)
BALL_SHARE = 0.65        # the ball's share of the target; the players near it the rest
CLOSE_UP = 0.36          # a person this tall makes a close-up (court players are 0.18-0.33 of the height)
RIM_EDGE = 0.4           # a ball within this of an edge, heading to it, is going to that rim
RIM_LEAN = 0.25          # ...and the crop leans this share of its width toward it
FLOOR_BAND = 0.8         # a "ball" below this share of the height is the front rows, the bug or a shoe
FEET = 0.15              # ...as is one in the bottom this share of a player's box
ON_FLOOR = 0.5           # people under this share of the tallest one's height are in the stands
CUT_COLOURS = 0.31       # a cut changes the picture's colours this much too (Bhattacharyya distance)


def real_balls(balls: list, people: list) -> list:
    """The detections that can be the ball: none in the bottom FLOOR_BAND
    of the frame, none at a player's feet."""
    keep = []
    for b in balls:
        if b[1] > FLOOR_BAND:
            continue
        if any(abs(b[0] - p[0]) <= p[2] / 2 and p[1] + p[3] * (0.5 - FEET) <= b[1] <= p[1] + p[3] * 0.55
               for p in people):
            continue
        keep.append(b)
    return keep


def colours(frame):
    """The picture's hue and saturation histogram, for telling a cut from a pan."""
    import cv2

    hsv = cv2.cvtColor(cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
    return cv2.normalize(hist, hist)


def is_cut(prev_small, small, prev_colours, now_colours) -> bool:
    """A camera cut: the shared gray-difference test, and the colours
    changing too. A pan across the court changes the pixels but keeps the
    floor, the crowd and the kits; a cut to another camera changes them."""
    import cv2

    from video.framing import is_cut as pixels_changed

    if not pixels_changed(prev_small, small) or prev_colours is None:
        return False
    return float(cv2.compareHist(prev_colours, now_colours, cv2.HISTCMP_BHATTACHARYYA)) > CUT_COLOURS


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
    led = {"close-up": 0, "ball": 0, "rim": 0, "players": 0, "held": 0}
    prev_t = None
    for s in samples:
        t = float(s["t"])
        if s.get("cut"):
            ball = None
            trail.clear()
            recent.clear()
        found = None
        candidates = sorted(real_balls(s.get("balls") or [], s.get("people") or []), key=lambda b: -b[2])
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
        # The players, not the stands: at 1280 px the detector finds a dozen
        # spectators too (16-20 people a frame on high-school footage), far
        # smaller than the players on the floor.
        tallest = max((p[3] for p in people), default=0.0)
        people = [p for p in people if p[3] >= ON_FLOOR * tallest]
        have_ball = ball is not None and t - ball[2] <= BALL_MEMORY
        if close:
            target, source = max(close, key=lambda p: p[2] * p[3])[0], "close-up"
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


def compute(clip_path, model_name: str = "yolov8n.pt", imgsz: int = 1280, sample_fps: float = 5.0) -> dict:
    """The crop path for one clip, in the form video/cropper.render_vertical takes."""
    import cv2

    from sports.soccer.ball import _model, detect
    from video.capture import video_capture
    from video.framing import small_gray

    model = _model(model_name)
    samples = []
    with video_capture(clip_path) as cap:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 16
        height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 9
        step = max(1, round(fps / sample_fps))
        prev_small = prev_colours = None
        index = 0
        while True:
            ok = cap.grab()
            if not ok:
                break
            if index % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                small, now_colours = small_gray(frame), colours(frame)
                balls, people = detect(model, frame, imgsz)
                samples.append({"t": index / fps, "cut": is_cut(prev_small, small, prev_colours, now_colours),
                                "balls": balls, "people": people})
                prev_small, prev_colours = small, now_colours
            index += 1
    crop_frac = min(1.0, (height * 9 / 16) / max(width, 1))
    path, led = plan(samples, crop_frac)
    return {"mode": "track", "path": path or [(0.0, 0.5)], "led": led}
