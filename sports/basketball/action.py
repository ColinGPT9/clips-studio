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

**The TV scoreboard is left out** when the crop would cut through it: a
bug is wider than a 9:16 crop, so half of it showed along the bottom of
nearly every clip. The rows from the bug's top edge down (or from the top
down to its bottom edge) are left out of the crop, which zooms in that much.
The bug's text is found as the score reader finds the bug, taken where
most looks show it (scoreboard.find_text), and its graphic's edge as a
step in brightness at the same row in most looks, with the rows on the
graphic's side holding stiller than those just beyond. Only as far as
that edge: on an NBA game, leaving out everything from the top of the
reader's box (grown to a caption over the bug on one look) took 18-27% of
the height out where the bug was 15%, and cut players at the knees.

Moved by the shared HoldMove controller and snapped at camera cuts, as
soccer's framing is. A cut is told by the picture's colours changing as
well as its pixels: the shared test (video/framing.py's gray difference)
fired on a third to three quarters of a broadcast's samples, the camera
whipping across the court, and snapping on each made the crop jump. The
detector and its input size come from config/sports.yaml (`framing`). The
output is the crop path video/cropper.py renders for everything else.
"""

import threading

# Clips render a few at a time; the OCR reads one frame at a time.
_OCR = threading.Lock()

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

# The scoreboard left out. On copies of the three NBA games' bugs over a
# moving picture, the graphic reached 0.03-0.06 of the height past its text.
BUG_WIDTH = 480          # frames are looked at this wide, in gray, for the graphic's edge
BUG_REACH = 0.08         # the graphic reaches at most this share of the height past its text
BUG_INSIDE = 0.025       # ...and its edge can sit this far inside the text's box (on an NBA game the box's top,
                         # from looks that box the text differently, came out up to 0.014 above the edge)
BUG_EDGE = 4.0           # its edge: a step of this many gray levels between the same two rows...
BUG_SAME = 0.75          # ...the same way in this share of the looks (a line on the floor moves between them)
BUG_BAND = 0.03          # ...with this much of the height on the graphic's side moving less than
BUG_STILL = 0.8          # this share of what as much beyond does (a see-through graphic shows some
                         # of the picture), when the picture there moves enough to tell
BUG_MOVING = 6.0         # gray levels the picture must move by for any of that to tell anything...
BUG_TEXT = 0.5           # ...else the graphic is taken to reach this many of its text's heights past it
BUG_SLACK = 0.008        # ...plus this much more, so not a row of its border shows (2 rows at BUG_WIDTH)
BUG_MOST = 0.27          # a bug that would take more of the height than this out is left in
BUG_NEAR = 0.1           # a crop this close to the bug's text (share of the width) shows part of it


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


def bug_edge(grays: list, box: tuple) -> float:
    """Where the score bug's graphic ends past its text (box, in the frame's
    fractions): its top edge for a bug along the bottom, its bottom edge for
    one along the top, as a fraction of the height. The edge is the furthest
    row within BUG_REACH of the text where most looks step in brightness the
    same way, the rows on the text's side moving less than the picture beyond
    when that moves enough to tell; then past the rows beside it that move
    less than the picture too, which the graphic's border partly covers.
    Inside the text's box, only with a moving picture beyond. Without one, or
    when the picture hardly moves, BUG_TEXT of the text's height past it.
    grays: the looks, in gray, all the same size."""
    import numpy as np

    stack = np.stack([g.astype(np.float32) for g in grays])
    height, width = stack.shape[1:]
    part = stack[:, :, int(box[0] * width):max(int(box[0] * width) + 1, int(np.ceil(box[2] * width)))]
    # How much each row moves over the looks, and each look's step in
    # brightness from one row to the next (steps[:, r] is row r + 1 less row r).
    rows = np.median(np.median(np.abs(part - np.median(part, axis=0)), axis=0), axis=1)
    means = part.mean(axis=2)
    steps = means[:, 1:] - means[:, :-1]
    bottom = (box[1] + box[3]) / 2 > 0.5
    text = BUG_TEXT * (box[3] - box[1])
    fallback = max(0.0, box[1] - text) if bottom else min(1.0, box[3] + text)
    if float(np.median(rows[int(0.3 * height):int(0.6 * height)])) < BUG_MOVING:
        return fallback
    reach, band, within = round(BUG_REACH * height), max(2, round(BUG_BAND * height)), round(BUG_INSIDE * height)
    if bottom:
        # r: the graphic's first row, furthest from the text first.
        start = int(box[1] * height)
        tried = range(max(band, start - reach), min(height - band, start + within) + 1)
    else:
        # r: one past the graphic's last row.
        start = min(height, int(np.ceil(box[3] * height)))
        tried = range(min(height - band, start + reach), max(band, start - within) - 1, -1)
    for r in tried:
        step = steps[:, r - 1]
        way = np.sign(np.median(step))
        if not way or np.mean((np.abs(step) >= BUG_EDGE) & (np.sign(step) == way)) < BUG_SAME:
            continue
        inside, beyond = (rows[r:r + band], rows[r - band:r]) if bottom else (rows[r - band:r], rows[r:r + band])
        moving = float(np.median(beyond))
        if moving < BUG_MOVING:
            # A still picture beyond (the floor): the step alone tells, outside the text.
            if (r <= start) if bottom else (r >= start):
                return r / height
            continue
        if float(np.median(inside)) < BUG_STILL * moving:
            # On an NBA game the bar's top row was half picture, and in one window
            # only the step below its border agreed across the looks.
            for _ in range(band):
                past = r - 1 if bottom else r
                if not 0 <= past < height or rows[past] >= BUG_STILL * moving:
                    break
                r += -1 if bottom else 1
            return r / height
    return fallback


def hidden_rows(looks: dict, duration: float, path: list, crop_frac: float) -> tuple[float, float] | None:
    """The rows of the frame the crop keeps so the TV scoreboard is out of
    it, (top, bottom) as fractions of the height; None to keep them all: no
    bug found (gym or phone footage, or no OCR), the crop never near it, or
    a bug so tall that leaving it out would zoom in too far. looks: frames
    at the times scoreboard.find_text looks at, by time."""
    import cv2

    from analysis import game_text

    if not looks or not game_text.available():
        return None
    from analysis.game_text import _ocr
    from sports.basketball import scoreboard

    times = sorted(looks)

    def grab(t: float):
        return looks[min(times, key=lambda x: abs(x - t))]

    with _OCR:
        found = scoreboard.find_text(grab, duration, _ocr)
    if found is None:
        return None
    box, text = found
    half = crop_frac / 2
    if not any(x - half < box[2] + BUG_NEAR and x + half > box[0] - BUG_NEAR for _, x in path):
        return None
    grays = []
    for t in times:
        img = looks[t]
        h, w = img.shape[:2]
        grays.append(cv2.cvtColor(cv2.resize(img, (BUG_WIDTH, max(2, round(h * BUG_WIDTH / w))),
                                             interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY))
    edge = bug_edge(grays, text)
    rows = (0.0, max(0.0, edge - BUG_SLACK)) if (text[1] + text[3]) / 2 > 0.5 else (min(1.0, edge + BUG_SLACK), 1.0)
    if rows[1] - rows[0] < 1 - BUG_MOST:
        print(f"      Basketball framing: the scoreboard is {1 - (rows[1] - rows[0]):.0%} of the height, "
              "too tall to leave out")
        return None
    return round(rows[0], 4), round(rows[1], 4)


def compute(clip_path, model_name: str = "yolov8n.pt", imgsz: int = 1280, sample_fps: float = 5.0,
            hide_scoreboard: bool = True) -> dict:
    """The crop path for one clip, in the form video/cropper.render_vertical
    takes, with "rows" when the TV scoreboard is left out."""
    import cv2

    from sports.core import scorebug
    from sports.soccer.ball import _model, detect
    from video.capture import video_capture
    from video.framing import small_gray

    model = _model(model_name)
    samples = []
    looks: dict = {}
    with video_capture(clip_path) as cap:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 16
        height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 9
        duration = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps
        # The frames the scoreboard's finder looks at, kept as they go by.
        wanted = ([duration * (i + 1) / (scorebug.FIND_FRAMES + 1) for i in range(scorebug.FIND_FRAMES)]
                  if hide_scoreboard and duration > 0 else [])
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
                while wanted and index / fps >= wanted[0]:
                    # At most 1080p's width: 14 frames of a 4K clip would be 350 MB.
                    looks[wanted.pop(0)] = frame if frame.shape[1] <= 1920 else cv2.resize(
                        frame, (1920, round(frame.shape[0] * 1920 / frame.shape[1])), interpolation=cv2.INTER_AREA)
                prev_small, prev_colours = small, now_colours
            index += 1
    crop_frac = min(1.0, (height * 9 / 16) / max(width, 1))
    path, led = plan(samples, crop_frac)
    rows = hidden_rows(looks, duration, path, crop_frac) if looks else None
    if rows is not None:
        crop_frac = min(1.0, (height * (rows[1] - rows[0]) * 9 / 16) / max(width, 1))
        path, led = plan(samples, crop_frac)
    out = {"mode": "track", "path": path or [(0.0, 0.5)], "led": led}
    if rows is not None:
        out["rows"] = rows
    return out
