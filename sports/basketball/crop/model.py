"""What each stage of basketball framing hands the next.

Pure Python: no numpy, no cv2, no ultralytics, at any level. The planning
modules beside this one (shots, ball, players, basket, phases, planner,
validate) import only this, so CI, which installs neither, tests them on
synthetic scenes.

Conventions, everywhere in this package:
- Positions and sizes are fractions of the full source frame: x of its
  width, y of its height. A box is its centre and size (x, y, w, h), or
  corners (x0, y0, x1, y1) where the name says so.
- Times are seconds into the 16:9 clip being framed; `frame` is the frame
  index in it.
- A crop is its centre x and its width, both fractions of the frame's
  width. It always takes the full height of the band it keeps (`rows`);
  a width wider than 9:16 of that band is shown over a blurred copy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

# ---- what was seen --------------------------------------------------------------------------


@dataclass
class BallCand:
    """One thing a detector called a ball. `score` and `rejected` are filled
    in by the gate (ball.gate): a reason letter when it can't be the ball."""

    x: float
    y: float
    w: float
    h: float
    conf: float
    src: str = "coco"              # coco | ebard | roi
    orange: float | None = None    # share of the box in a basketball's colours
    contrast: float | None = None  # how much it stands out from its surroundings
    score: float = 0.0
    rejected: str = ""


@dataclass
class Person:
    x: float
    y: float
    w: float
    h: float
    conf: float = 1.0
    track: int | None = None       # players.track's id, once tracked
    src: str = ""                  # coco | ebard ("" when not recorded)
    role: str = ""                 # player | referee when the detector tells them apart, else ""
    head_x: float | None = None    # his head's centre (frame fraction), from the pose model, on close-ups


@dataclass
class RimCand:
    """A rim (with its net) a hoop source found. `backboard` is the board's
    corners when the source saw it too."""

    x: float
    y: float
    w: float
    h: float
    score: float
    src: str = "classic"           # classic | ebard
    backboard: tuple[float, float, float, float] | None = None


@dataclass
class Sample:
    """What the detectors saw on one frame of the clip."""

    i: int                         # its index among the samples
    frame: int
    t: float
    dense: bool = False            # one of the frames looked at closely, around the play
    imgsz: int = 0
    pan: tuple[float, float] = (0.0, 0.0)   # the camera's travel since its shot began (frame fractions)
    court: float | None = None     # share of the lower middle that is the floor's colour
    bug: bool | None = None        # the TV scoreboard on screen
    balls: list[BallCand] = field(default_factory=list)
    people: list[Person] = field(default_factory=list)
    rims: list[RimCand] = field(default_factory=list)
    # A second detector's people, for telling shots apart only (COCO about twice a second when
    # the people come from E-BARD, which may not report a coach or the crowd); empty otherwise.
    # Never mixed into `people`, so the player tracker sees one detector's boxes.
    people_extra: list[Person] = field(default_factory=list)
    # How tall the broadcast's game camera shows the players against the broadcasts the
    # height tests were set on (reactions.people_scale): every test of a person's height
    # (a close-up, a floor player, a wide-looking frame) is scaled by it. 1 on those.
    scale: float = 1.0


@dataclass
class FrameSignals:
    """One value per frame of the clip, from a small copy of every frame:
    how much the picture changed (for cuts) and how far the camera moved."""

    fps: float
    width: int
    height: int
    d_gray: list[float] = field(default_factory=list)   # mean gray difference of 48x27 thumbnails
    d_col: list[float] = field(default_factory=list)    # hue/saturation histogram distance (Bhattacharyya)
    pan_dx: list[float] = field(default_factory=list)   # camera translation since the previous frame
    pan_dy: list[float] = field(default_factory=list)
    luma: list[float] = field(default_factory=list)

    @property
    def frames(self) -> int:
        return len(self.d_gray)


@dataclass
class Observation:
    """Everything the framing decides from, for one clip."""

    geometry: Geometry
    signals: FrameSignals
    samples: list[Sample] = field(default_factory=list)
    bug_box: tuple[float, float, float, float] | None = None   # the scoreboard's corners, when found


# ---- the clip's structure -------------------------------------------------------------------


@dataclass
class Cut:
    frame: int                     # the first frame of the new shot
    t: float
    kind: str = "hard"             # hard | transition
    span: tuple[int, int] | None = None   # a transition's frames (a replay wipe, a dissolve)


@dataclass
class Shot:
    """One camera shot: the frames between two cuts."""

    id: int
    f0: int                        # first frame
    f1: int                        # one past the last
    t0: float
    t1: float
    kind: str = "wide"             # wide | medium | closeup | crowd | graphic
    replay: bool = False
    evidence: dict = field(default_factory=dict)

    @property
    def gameplay(self) -> bool:
        return self.kind in ("wide", "medium") and not self.replay


@dataclass
class BallPoint:
    """Where the ball is at one sample, and how sure that is."""

    t: float
    x: float | None = None
    y: float | None = None
    status: str = "missing"        # detected | interpolated | predicted | missing
    conf: float = 0.0
    vx: float = 0.0                # frame heights per second, camera motion removed
    vy: float = 0.0
    via: str = ""                  # "" | carried (in a player's hands) | bridge
    tracklet: int | None = None


@dataclass
class Basket:
    """One basket at one sample: its rim, its backboard and the span a crop
    must hold to show both with the net (the "unit")."""

    id: int
    shot: int
    t: float
    rim: tuple[float, float, float, float]          # corners
    backboard: tuple[float, float, float, float] | None
    bb_src: str = "allowance"      # found | model | allowance
    side: str = "unknown"          # the baseline side: left | right | unknown
    unit: tuple[float, float] = (0.0, 1.0)          # x0, x1 the crop must hold
    status: str = "detected"       # detected | propagated
    in_frame: bool = True
    conf: float = 0.0
    unit_y: tuple[float, float] = (0.0, 1.0)        # y0, y1: the board's top to the net's bottom
    clipped: str = ""              # "" | left | right | both: the frame's edge cuts the backboard there


@dataclass
class Phase:
    kind: str                      # possession | pass | release | flight | rim | result | attack | rebound |
    #                                transition | reaction
    t0: float
    t1: float
    conf: float = 0.0
    actors: list[int] = field(default_factory=list)   # player track ids (passer, receiver, shooter...)
    basket: int | None = None


@dataclass
class Hints:
    """What the clip's selection already knows: when the basket went in (or
    the moment happened), seconds into the clip, and what kind of play it was."""

    moment_t: float | None = None
    kind: str | None = None        # the moment's type: made_3, dunk, block, fast_break...
    footage: str | None = None     # broadcast | sideline | ...
    scale: float = 1.0             # how tall the game camera shows the players (Sample.scale)

    @classmethod
    def from_opts(cls, opts: dict | None) -> Hints:
        m = (opts or {}).get("sport_moment") or {}
        t, scale = m.get("t"), m.get("scale")
        return cls(moment_t=float(t) if isinstance(t, (int, float)) else None,
                   kind=str(m["kind"]) if m.get("kind") else None,
                   footage=str(m["footage"]) if m.get("footage") else None,
                   scale=float(scale) if isinstance(scale, (int, float)) and scale > 0 else 1.0)


# ---- the frame and the crop -----------------------------------------------------------------


@dataclass
class Geometry:
    width: int                     # source frame, pixels
    height: int
    fps: float
    frames: int
    rows: tuple[float, float] | None = None   # the band of the height the crop keeps (the scoreboard left out)
    # The share of the vertical screen's height the game picture fills: the rest is blurred bands
    # above and below it, under the apps' own bar (TikTok's search, the Reels title) and their
    # caption at the bottom, so the basket and the floor are never behind them
    # (action.picture_share()). 1.0 fills the screen.
    picture: float = 1.0
    # The share of the screen's height above the picture (action.bands()); the rest of what the
    # picture doesn't fill is below it. 0 with a picture under 1.0: all of the band is on top.
    band_top: float = 0.0

    @property
    def band(self) -> float:
        """The share of the height the crop keeps."""
        return 1.0 if self.rows is None else self.rows[1] - self.rows[0]

    @property
    def aspect(self) -> float:
        """Width over height of the source frame."""
        return self.width / max(1, self.height)

    def width_for(self, ratio_w: float, ratio_h: float) -> float:
        """The crop width, as a share of the frame's width, of a ratio_w:ratio_h
        crop that takes the band's full height (never more than the frame)."""
        return min(1.0, self.band * self.height * ratio_w / ratio_h / max(1, self.width))

    @property
    def base(self) -> float:
        """The crop that fills the game picture: 9:16 when it fills the
        screen, wider when a band across the top takes some of its height."""
        return self.width_for(9, 16 * self.picture)

    def levels(self) -> list[float]:
        """The crop widths the planner steps between, narrowest first:
        9:16 (fills the screen), 4:5, 1:1, and the whole 16:9 frame."""
        out: list[float] = []
        for w in (self.base, self.width_for(4, 5), self.width_for(1, 1), 1.0):
            if not out or w > out[-1] + 1e-6:
                out.append(round(w, 6))
        return out


@dataclass
class Plan:
    """The crop for every frame, and what it rests on."""

    fps: float
    base: float                    # the picture's width (9:16, or wider under a top band)
    cx: list[float] = field(default_factory=list)
    w: list[float] = field(default_factory=list)
    conf: list[float] = field(default_factory=list)
    mode: list[str] = field(default_factory=list)   # tracked | assisted | players | wide | person
    cuts: list[Cut] = field(default_factory=list)
    shots: list[Shot] = field(default_factory=list)
    quality: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    band_top: float = 0.0          # the blurred band across the top of the screen (Geometry.picture)
    band_bottom: float = 0.0       # and across the bottom, under the apps' caption

    def to_tracking(self) -> dict:
        """The form video/cropper.render_vertical takes: one key per frame.
        "zoom" only when some frame is wider than 9:16, so a plan that never
        widens renders exactly as a path always has."""
        times = [round(f / self.fps, 4) for f in range(len(self.cx))]
        out: dict = {"mode": "track", "path": [(t, round(x, 5)) for t, x in zip(times, self.cx)] or [(0.0, 0.5)]}
        if any(w > self.base + 2e-3 for w in self.w):
            out["zoom"] = [(t, round(w, 5)) for t, w in zip(times, self.w)]
        if self.band_top:
            out["band_top"] = self.band_top
        if self.band_bottom:
            out["band_bottom"] = self.band_bottom
        if self.cuts:
            out["cuts"] = [c.t for c in self.cuts]
        return out


# ---- JSON, for the debug output, the bench and tests -----------------------------------------

_TUPLES = {"pan", "backboard", "span", "rim", "unit", "rows", "bug_box"}


def to_json(obj):
    """Plain data (dicts and lists) from any of the classes above, or lists of them."""
    if isinstance(obj, list):
        return [to_json(o) for o in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    return obj


def _build(cls, data: dict):
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        if f.name in _TUPLES and isinstance(value, list):
            value = tuple(value)
        kwargs[f.name] = value
    return cls(**kwargs)


def sample_from_json(data: dict) -> Sample:
    s = _build(Sample, {k: v for k, v in data.items() if k not in ("balls", "people", "rims", "people_extra")})
    s.balls = [_build(BallCand, b) for b in data.get("balls") or []]
    s.people = [_build(Person, p) for p in data.get("people") or []]
    s.rims = [_build(RimCand, r) for r in data.get("rims") or []]
    s.people_extra = [_build(Person, p) for p in data.get("people_extra") or []]
    return s


def observation_from_json(data: dict) -> Observation:
    return Observation(geometry=_build(Geometry, data["geometry"]),
                       signals=_build(FrameSignals, data["signals"]),
                       samples=[sample_from_json(s) for s in data.get("samples") or []],
                       bug_box=tuple(data["bug_box"]) if data.get("bug_box") else None)
