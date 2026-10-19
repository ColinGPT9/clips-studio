"""The detectors basketball framing draws its ball, rims and people from.

Each of the three is a slot (config/sports.yaml, basketball `framing.sources`):

    ball:   coco | ebard
    hoop:   classic | ebard
    people: coco | ebard

(`none` leaves a slot empty: for tests, and for timing the decode alone.)
Optional keys: `ebard_weights` (a path or a file name), `ebard_imgsz` (960),
`coco_model` (yolov8n.pt), `coco_imgsz` (1280).

- **coco** is the YOLOv8n the app already ships (COCO "person" and "sports
  ball"). It finds the real ball in a minority of broadcast wide frames and
  reports dozens of people in the stands, which the planning core sorts out.
- **ebard** is a YOLOv8n fine-tuned on NBA broadcast frames (classes
  basketball, hoop, player, referee; CC BY 4.0). Its classes are looked up by
  NAME from the weights, never by number, so another export of it with the
  classes in another order still maps right. Its hoop box covers the ring and
  the net, never the backboard. Missing weights fall back to coco / classic
  with one log line, so a job never fails over a detector.
- **classic** is the hand-written rim finder (rim_finder.find_rims: the
  orange ring's shape, the backboard's edges, the white net below).

When the people come from E-BARD, COCO's people are still looked at about
twice a second: E-BARD was trained on players and referees, and a close-up
of a coach or a crowd shot are exactly the people it may not report, while
the shot classification needs them. Those go in Sample.people_extra, never
in Sample.people, so the player tracker sees one detector's boxes only.

Not pure: numpy, cv2 and ultralytics are imported inside the functions, so
importing the package stays cheap. Every model comes from
sports.soccer.ball._model (one cached copy per weights file, resolved by
core.binaries.yolo_weights) and every inference runs under the shared
video.tracker._infer_lock on core.gpu.torch_device().
"""

from __future__ import annotations

import time
from pathlib import Path

from sports.basketball.crop.model import BallCand, Person, RimCand, Sample
from sports.basketball.crop.shots import TALL

ROOT = Path(__file__).resolve().parents[3]

COCO_MODEL = "yolov8n.pt"
COCO_PERSON, COCO_BALL = 0, 32
COCO_IMGSZ = 1280        # a broadcast ball is a few pixels wide: input size matters more than the model
EBARD_FILE = "BODD_yolov8n_0001.pt"
# During development the weights sit with the bench data (data/ is gitignored); a shipped copy
# would be found by core.binaries.yolo_weights in models/ first.
EBARD_DEV = ROOT / "data" / "framing_bench" / "ebard" / EBARD_FILE
EBARD_IMGSZ = 960        # on the owner's frames its ball was found best at 960 (11/11, vs 10/11 at 704 and 1280)
# ...on a camera that shows the players no taller than the national broadcasts it was set on. A
# closer camera (reactions.people_scale) shows the ball bigger than E-BARD learned it: on a team's
# own network (scale 1.8, the ball 0.07 of the height against 0.037) it found 54% of the balls at
# 960 and 74-80% anywhere from 448 to 704, so the input shrinks by the scale, aimed at the
# middle of that range (EBARD_CLOSE x EBARD_IMGSZ / scale: 640 there), to no less than this...
EBARD_CLOSE = 1.2
EBARD_IMGSZ_MIN = 512
# ...and a video this wide or narrower is never shrunk at all: on a 720p game it found 46% at 960
# and 85% at its own 1280 (a soft picture loses the ball's edges when it is made smaller).
EBARD_NATIVE_MAX = 1280

BALL_CONF = 0.10         # the ball is small and blurred: take weak detections, the ball gate vets them
PEOPLE_CONF = 0.35
HOOP_CONF = 0.25         # E-BARD's real hoops scored 0.47-0.89 on the owner's frames; its one extra box 0.12-0.42
EXTRA_PEOPLE_EVERY = 0.45   # seconds between COCO people looks when E-BARD gives the people (about 2 Hz)
# A close-up is framed on its subject's head, not his body's box (a player turning, or leaning
# into the frame, had his face at its edge): the pose model's nose, eyes and ears, looked for
# only on frames with someone this tall (shots.TALL, times the camera's scale), so the play
# itself costs nothing more.
POSE_MODEL = "yolov8n-pose.pt"
POSE_IMGSZ = 640
POSE_HEAD = (0, 1, 2, 3, 4)   # COCO keypoints: nose, eyes, ears
POSE_KPT_CONF = 0.5
POSE_MATCH = 0.3              # a pose box gives its head to the person box it overlaps this much (IoU)

# What E-BARD's class names mean here.
EBARD_NAMES = {
    "basketball": "ball", "ball": "ball", "sports ball": "ball",
    "hoop": "rim", "rim": "rim", "basket": "rim",
    "player": "player", "person": "player", "referee": "referee",
}

DEFAULT_SOURCES = {"ball": "coco", "hoop": "classic", "people": "coco"}

# ---- what a ball candidate looks like -------------------------------------------------------

# A basketball's orange to brown, in OpenCV's HSV (hue 0-180, a reddish ball wrapping past 180 to
# ORANGE_WRAP). The gold of a crowd's shirts sits above this hue, a hand or a face mostly below the
# saturation floor. On the 256 labelled bench frames it told COCO's real balls from its false ones
# almost perfectly (AUC 0.98, few real ones: 5), but NOT E-BARD's: its false balls are orange
# things (the rim's near side, the floor, gold shirts), and they scored higher than its real,
# blurred balls (AUC 0.32; medians 0.97 against 0.86).
ORANGE_HUE = (0, 16)
ORANGE_WRAP = 170
ORANGE_SAT = 70
ORANGE_VAL = 50
BALL_INNER = 0.7         # the share of the box's width and height measured: a round ball leaves its corners
RING = 0.5               # the ring around the box reaches this many box sizes out on each side


def ball_looks(frame, cand: BallCand) -> None:
    """Fill a candidate's `orange` (the share of its middle in a basketball's
    colours) and `contrast` (how far its mean colour is from a ring around it,
    0-255): what lets the ball gate tell the ball from a gold shirt, a head or
    a patch of floor the detector liked."""
    import cv2
    import numpy as np

    fh, fw = frame.shape[:2]
    bw, bh = max(2.0, cand.w * fw), max(2.0, cand.h * fh)
    cx, cy = cand.x * fw, cand.y * fh

    def box(half_w: float, half_h: float) -> tuple[int, int, int, int]:
        return (max(0, int(cx - half_w)), max(0, int(cy - half_h)),
                min(fw, int(np.ceil(cx + half_w))), min(fh, int(np.ceil(cy + half_h))))

    x0, y0, x1, y1 = box(bw * BALL_INNER / 2, bh * BALL_INNER / 2)
    inner = frame[y0:y1, x0:x1]
    if inner.size == 0:
        return
    hsv = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    orange = ((((hue >= ORANGE_HUE[0]) & (hue <= ORANGE_HUE[1])) | (hue >= ORANGE_WRAP))
              & (sat >= ORANGE_SAT) & (val >= ORANGE_VAL))
    cand.orange = round(float(orange.mean()), 3)

    bx0, by0, bx1, by1 = box(bw / 2, bh / 2)
    ox0, oy0, ox1, oy1 = box(bw * (0.5 + RING), bh * (0.5 + RING))
    outer = frame[oy0:oy1, ox0:ox1].astype(np.float32)
    mask = np.ones(outer.shape[:2], bool)
    mask[by0 - oy0:by1 - oy0, bx0 - ox0:bx1 - ox0] = False
    own = frame[by0:by1, bx0:bx1].reshape(-1, 3).astype(np.float32)
    ring = outer[mask]
    if len(ring) == 0 or len(own) == 0:
        return
    cand.contrast = round(float(np.abs(own.mean(axis=0) - ring.mean(axis=0)).mean()), 2)


# ---- the detectors ----------------------------------------------------------------------------


def _predict(model, frame, imgsz: int, conf: float, classes: list[int] | None, augment: bool = False):
    """One inference, under the lock every detector in the app shares, on the
    device the app chose (`augment`: the model's own test-time augmentation,
    flipped and rescaled copies, about three times as long). Returns
    [(class, conf, x0, y0, x1, y1)] in pixels."""
    from core.gpu import torch_device
    from video.tracker import _infer_lock

    with _infer_lock:
        results = model.predict(frame, classes=classes, conf=conf, imgsz=imgsz,
                                device=torch_device(), verbose=False, augment=augment)
    out = []
    for r in results:
        boxes = r.boxes
        if boxes is None or len(boxes) == 0:
            continue
        xyxy = boxes.xyxy.cpu().numpy()
        cls = boxes.cls.cpu().numpy()
        cf = boxes.conf.cpu().numpy()
        for (x0, y0, x1, y1), c, p in zip(xyxy, cls, cf):
            out.append((int(c), float(p), float(x0), float(y0), float(x1), float(y1)))
    return out


def _centre(x0: float, y0: float, x1: float, y1: float, fw: int, fh: int) -> tuple[float, float, float, float]:
    return ((x0 + x1) / 2 / fw, (y0 + y1) / 2 / fh, (x1 - x0) / fw, (y1 - y0) / fh)


def _r(v: float) -> float:
    return round(v, 5)


class CocoSource:
    """The app's YOLOv8n: COCO person and sports ball, one inference for
    whichever of the two this run asks of it."""

    def __init__(self, balls: bool, people: bool, imgsz: int = COCO_IMGSZ, model: str = COCO_MODEL):
        from sports.soccer.ball import _model

        self.balls, self.people, self.imgsz = balls, people, int(imgsz)
        self.model = _model(model)
        self.classes = ([COCO_BALL] if balls else []) + ([COCO_PERSON] if people else [])

    def detect(self, frame) -> tuple[list[BallCand], list[Person]]:
        fh, fw = frame.shape[:2]
        conf = BALL_CONF if self.balls else PEOPLE_CONF
        balls, people = [], []
        for c, p, x0, y0, x1, y1 in _predict(self.model, frame, self.imgsz, conf, self.classes):
            x, y, w, h = _centre(x0, y0, x1, y1, fw, fh)
            if c == COCO_BALL and self.balls:
                balls.append(BallCand(_r(x), _r(y), _r(w), _r(h), round(p, 4), src="coco"))
            elif c == COCO_PERSON and self.people and p >= PEOPLE_CONF:
                people.append(Person(_r(x), _r(y), _r(w), _r(h), round(p, 4), src="coco"))
        return balls, people


def ebard_imgsz_for(scale: float = 1.0, width: int = 0) -> int:
    """E-BARD's input size for a video `width` pixels wide whose game camera
    shows the players `scale` times as tall as the national broadcasts
    (reactions.people_scale): its own width when that is EBARD_NATIVE_MAX or
    less, else EBARD_CLOSE x EBARD_IMGSZ shrunk by the scale, in steps of 32,
    between EBARD_IMGSZ_MIN and EBARD_IMGSZ. EBARD_IMGSZ up to scale 1.2."""
    if 0 < width <= EBARD_NATIVE_MAX:
        return max(32, round(width / 32) * 32)
    size = round(EBARD_CLOSE * EBARD_IMGSZ / max(1.0, float(scale or 1.0)) / 32) * 32
    return int(min(EBARD_IMGSZ, max(EBARD_IMGSZ_MIN, size)))


class PoseHeads:
    """Where the heads are: yolov8n-pose's nose, eyes and ears, each set as
    the head_x of the person box its own box overlaps most (POSE_MATCH)."""

    def __init__(self, model: str = POSE_MODEL, imgsz: int = POSE_IMGSZ):
        from sports.soccer.ball import _model

        self.model, self.imgsz = _model(model), int(imgsz)

    def fill(self, frame, people: list[Person]) -> None:
        from core.gpu import torch_device
        from video.tracker import _infer_lock

        fh, fw = frame.shape[:2]
        with _infer_lock:
            results = self.model.predict(frame, conf=PEOPLE_CONF, imgsz=self.imgsz, device=torch_device(),
                                         verbose=False)
        heads = []
        for r in results:
            if r.keypoints is None or r.boxes is None or len(r.boxes) == 0:
                continue
            xy, kc = r.keypoints.xy.cpu().numpy(), r.keypoints.conf
            kc = kc.cpu().numpy() if kc is not None else None
            for box, pts, conf in zip(r.boxes.xyxy.cpu().numpy(), xy, kc if kc is not None else [None] * len(xy)):
                seen = [pts[k][0] for k in POSE_HEAD if conf is None or conf[k] >= POSE_KPT_CONF]
                if seen:
                    x0, y0, x1, y1 = box
                    heads.append(((x0 / fw, y0 / fh, x1 / fw, y1 / fh), float(sum(seen) / len(seen) / fw)))
        for p in people:
            box = (p.x - p.w / 2, p.y - p.h / 2, p.x + p.w / 2, p.y + p.h / 2)
            best = max(heads, key=lambda h: _iou(box, h[0]), default=None)
            if best is not None and _iou(box, best[0]) >= POSE_MATCH:
                p.head_x = round(best[1], 5)


def _iou(a: tuple, b: tuple) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


class EbardSource:
    """The basketball-trained YOLOv8n. Classes are read by name from the
    weights (EBARD_NAMES); a class this run doesn't take from it is not
    asked for, so the model spends nothing on it."""

    def __init__(self, path: str, balls: bool, hoops: bool, people: bool, imgsz: int = EBARD_IMGSZ,
                 augment: bool = False):
        from sports.soccer.ball import _model

        self.imgsz = int(imgsz)
        self.augment = bool(augment)
        self.model = _model(path)
        names = self.model.names
        names = names if isinstance(names, dict) else dict(enumerate(names))
        self.kind = {int(k): EBARD_NAMES.get(str(v).strip().lower()) for k, v in names.items()}
        wanted = {"ball"} if balls else set()
        wanted |= {"rim"} if hoops else set()
        wanted |= {"player", "referee"} if people else set()
        self.classes = sorted(k for k, kind in self.kind.items() if kind in wanted)
        missing = {"ball": balls, "rim": hoops, "player": people}
        lost = [k for k, need in missing.items() if need and k not in self.kind.values()]
        if lost:
            print(f"      Basketball framing: the E-BARD weights have no {', '.join(lost)} class; "
                  f"classes are {sorted(set(map(str, names.values())))}")

    def detect(self, frame) -> tuple[list[BallCand], list[Person], list[RimCand]]:
        fh, fw = frame.shape[:2]
        balls, people, rims = [], [], []
        if not self.classes:
            return balls, people, rims
        for c, p, x0, y0, x1, y1 in _predict(self.model, frame, self.imgsz, BALL_CONF, self.classes, self.augment):
            kind = self.kind.get(c)
            x, y, w, h = _centre(x0, y0, x1, y1, fw, fh)
            if kind == "ball":
                balls.append(BallCand(_r(x), _r(y), _r(w), _r(h), round(p, 4), src="ebard"))
            elif kind == "rim" and p >= HOOP_CONF:
                rims.append(RimCand(_r(x), _r(y), _r(w), _r(h), round(p, 4), src="ebard"))
            elif kind in ("player", "referee") and p >= PEOPLE_CONF:
                people.append(Person(_r(x), _r(y), _r(w), _r(h), round(p, 4), src="ebard", role=kind))
        return balls, people, rims


class ClassicHoop:
    """The hand-written rim finder, when it is there. Written alongside this
    module (rim_finder.py); until it is, or if it fails on a clip, the rims
    are left empty and the basket module falls back to its estimates."""

    def __init__(self):
        self.find = None
        try:
            from sports.basketball.crop.rim_finder import find_rims
        except ImportError as e:
            print(f"      Basketball framing: no rim finder ({e}); rims from the detector only")
            return
        self.find = find_rims

    def detect(self, frame, people: list[Person]) -> list[RimCand]:
        if self.find is None:
            return []
        try:
            return list(self.find(frame, people) or [])
        except Exception as e:  # a finder bug costs the rims, never the clip
            print(f"      Basketball framing: the rim finder failed ({type(e).__name__}: {e}); "
                  "rims off for this clip")
            self.find = None
            return []


def ebard_weights(cfg: dict | None = None) -> str | None:
    """The E-BARD weights file, or None. In order: the configured path (as
    given, or by name through core.binaries.yolo_weights), the shipped name
    through yolo_weights (models/ in an installed copy), the bench copy in a
    checkout."""
    from core.binaries import yolo_weights

    tried = []
    named = str((cfg or {}).get("ebard_weights") or "").strip()
    if named:
        tried += [named, yolo_weights(named)]
    tried.append(yolo_weights(EBARD_FILE))
    tried.append(str(EBARD_DEV))
    for path in tried:
        if path and Path(path).is_file():
            return str(Path(path).resolve())
    return None


class Detectors:
    """What runs on each detection frame, from the sources config.

    `used` says which source each slot really got after any fallback, for
    the log and the bench. `seconds` adds up the time spent in each detector."""

    def __init__(self, cfg: dict | None = None, scale: float = 1.0, width: int = 0):
        """`scale` and `width`: the game camera's people_scale and the video's
        width, which pick E-BARD's input size (ebard_imgsz_for) unless the
        config sets `ebard_imgsz`."""
        cfg = {**DEFAULT_SOURCES, **(cfg or {})}
        want = {k: str(cfg.get(k) or DEFAULT_SOURCES[k]).lower() for k in DEFAULT_SOURCES}
        weights = ebard_weights(cfg) if "ebard" in want.values() else None
        if "ebard" in want.values() and weights is None:
            print(f"      Basketball framing: E-BARD weights not found ({EBARD_FILE}); "
                  "using the COCO detector and the rim finder")
            want = {"ball": "coco" if want["ball"] == "ebard" else want["ball"],
                    "hoop": "classic" if want["hoop"] == "ebard" else want["hoop"],
                    "people": "coco" if want["people"] == "ebard" else want["people"]}
        self.used = dict(want)
        self.seconds: dict[str, float] = {}
        coco_imgsz = int(cfg.get("coco_imgsz") or COCO_IMGSZ)
        coco_model = str(cfg.get("coco_model") or COCO_MODEL)
        self.coco = None
        if want["ball"] == "coco" or want["people"] == "coco":
            self.coco = CocoSource(balls=want["ball"] == "coco", people=want["people"] == "coco",
                                   imgsz=coco_imgsz, model=coco_model)
        self.ebard = None
        if weights is not None:
            self.ebard = EbardSource(weights, balls=want["ball"] == "ebard", hoops=want["hoop"] == "ebard",
                                     people=want["people"] == "ebard",
                                     imgsz=int(cfg.get("ebard_imgsz") or ebard_imgsz_for(scale, width)),
                                     augment=bool(cfg.get("ebard_tta")))
            self.used["ebard_weights"] = Path(weights).name
        # COCO's people about twice a second, for the shot kinds, when E-BARD gives the people.
        self.extra = None
        if want["people"] == "ebard":
            self.extra = CocoSource(balls=False, people=True, imgsz=coco_imgsz, model=coco_model)
        self._extra_t: float | None = None
        self.classic = ClassicHoop() if want["hoop"] == "classic" else None
        self.pose: PoseHeads | None = None
        self._pose_ok = bool(cfg.get("close_up_heads", True))
        self.imgsz = (self.ebard.imgsz if want["ball"] == "ebard" and self.ebard else
                      self.coco.imgsz if self.coco else 0)

    def _time(self, key: str, t0: float) -> None:
        self.seconds[key] = self.seconds.get(key, 0.0) + time.perf_counter() - t0

    def run(self, frame, sample: Sample) -> None:
        """Fill one sample's balls, people, rims (and people_extra) from its
        full-size frame, then describe each ball candidate (ball_looks)."""
        sample.imgsz = self.imgsz
        if self.coco is not None:
            t0 = time.perf_counter()
            balls, people = self.coco.detect(frame)
            sample.balls += balls
            sample.people += people
            self._time("coco", t0)
        if self.ebard is not None:
            t0 = time.perf_counter()
            balls, people, rims = self.ebard.detect(frame)
            sample.balls += balls
            sample.people += people
            sample.rims += rims
            self._time("ebard", t0)
        if self.extra is not None and (self._extra_t is None or sample.t - self._extra_t >= EXTRA_PEOPLE_EVERY):
            t0 = time.perf_counter()
            _, extra = self.extra.detect(frame)
            sample.people_extra = extra
            self._extra_t = sample.t
            self._time("coco_extra", t0)
        if self.classic is not None:
            t0 = time.perf_counter()
            sample.rims += self.classic.detect(frame, sample.people)
            self._time("classic", t0)
        if self._pose_ok and any(p.h >= TALL * sample.scale for p in sample.people):
            t0 = time.perf_counter()
            try:
                if self.pose is None:
                    self.pose = PoseHeads()
                self.pose.fill(frame, sample.people)
            except Exception as e:   # no pose weights, no ultralytics: close-ups centre on the body's box
                print(f"      (close-ups: the pose model isn't available ({e}); framing them by the body)")
                self._pose_ok = False
            self._time("pose", t0)
        t0 = time.perf_counter()
        for cand in sample.balls:
            ball_looks(frame, cand)
        self._time("ball_looks", t0)
