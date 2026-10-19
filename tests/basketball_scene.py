"""A synthetic basketball broadcast for the framing tests.

Seeded, so every run is the same; built from a few knobs; and returned with
what really happened, so a test checks the planner against the truth rather
than against itself.

    built = (scene(duration=15, seed=3)
             .players(10)
             .closeup(0, 3.0)                      # a TV close-up, then the court
             .basket("right", backboard=True)
             .ball_dribble(3.0, 6.0, player=2)
             .ball_pass(6.0, 6.6, frm=2, to=5)
             .ball_shot(7.4, 8.6, shooter=5, basket="right")
             .false_ball("logo")
             .build())
    built.obs       # model.Observation, as the observation stage hands it over
    built.truth     # Truth: cuts, shots, the ball's real path, who had it, the rims

**World and image.** Things on the court are placed in world coordinates:
where they would be in the picture with the camera where it starts (x a
share of the frame's width, y of its height). The camera's travel (see
`camera`, and `cut_at(jump=...)`) moves the picture: image x = world x -
camera(t). A sample's `pan` is the travel since its shot began, as the
observation stage measures it, so the planning core's world coordinates
(model.py: X = (x + pan) * aspect) are this world, shifted per shot.

**What a sample sees** depends on its shot. Court shots (wide, and replays)
show the floor players, a few spectators high in the stands, the ball, the
false balls and the rims in frame. A close-up shows one big person, a crowd
shot eight fans, a graphic nobody.

**Randomness** comes from separate seeded streams (people, ball, false balls,
rims, frame signals), so adding a false ball doesn't change where the real
ball was detected.

Real team and player names never appear here: players are numbers.
"""

from __future__ import annotations

import math
import random
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field

from sports.basketball.crop.model import (
    BallCand,
    BallPoint,
    FrameSignals,
    Geometry,
    Observation,
    Person,
    RimCand,
    Sample,
)

FPS = 59.94
WIDTH, HEIGHT = 1920, 1080
ASPECT = WIDTH / HEIGHT
PLAYER_H = 0.22                 # a floor player's height (share of the frame's height) at middle depth
PERSON_W = 0.42                 # a person's box is this share of its height wide, in pixels
BALL_D = 0.12 * PLAYER_H        # the ball's diameter in frame heights (the gate expects 0.12 x a player)
DRIBBLE_PERIOD = 0.55           # seconds from the hand to the floor and back
NET = 0.06                      # the net hangs this far below the rim (frame heights)
FLOOR_UNDER_RIM = 0.3           # the floor under a basket is this far below its rim (frame heights)
RIM_H = 0.3                     # a rim's box is this share of its width tall, in pixels (seen from the side)
PAN_GRAY = 300.0                # d_gray added per frame-width of camera travel in one frame
SHUTTER = 1 / 120               # seconds: a moving ball's box grows by its travel in this time (motion blur)
LUMA = {"wide": 125.0, "closeup": 100.0, "crowd": 70.0, "graphic": 170.0}


def scene(duration: float = 15.0, seed: int = 0, fps: float = FPS, hz: float = 10.0) -> Scene:
    """A new scene `duration` seconds long at `fps`, sampled at `hz` (see
    Scene.sampling). With no other call it is one wide shot of ten players
    standing about, a static camera and no ball."""
    return Scene(duration, seed, fps, hz)


# ---- the truth --------------------------------------------------------------------------------


@dataclass
class TrueShot:
    f0: int
    f1: int                     # one past the last frame
    t0: float
    t1: float
    kind: str = "wide"          # wide | closeup | crowd | graphic
    replay: bool = False


@dataclass
class Truth:
    """What really happened. Per-sample lists line up with obs.samples."""

    fps: float
    frames: int
    cuts: list[tuple[int, str]]                  # (first frame of the new shot, hard | transition)
    spans: dict[int, tuple[int, int]]            # a transition's frames, by its cut frame
    flashes: list[int]                           # each flash's first frame (a flash is not a cut)
    shots: list[TrueShot]
    events: list[dict]                           # the scripted play, in order (see the Scene.ball_* calls)
    baskets: dict[str, dict]                     # name -> side, rim, backboard, net (world corners), found
    who: list[list[str]]                         # per sample, per Person: "player:3", "fan:0", "closeup", "crowd"
    labels: list[list[str]]                      # per sample, per BallCand: ball | logo | head | shoe | noise
    ball: list[tuple[float, float] | None]       # per sample: the real ball in the image (None off the court)
    ball_v: list[tuple[float, float]]            # per sample: its world velocity, frame heights per second
    handler: list[int | None]                    # per sample: the player who has it
    rims: list[dict[str, tuple]]                 # per sample: each basket's rim in the image (corners), if in frame
    backboards: list[dict[str, tuple]]           # per sample: each basket's real backboard in the image (unclipped)
    times: list[float] = field(default_factory=list)   # per sample: its time

    def shot_at(self, t: float) -> TrueShot:
        f = round(t * self.fps)
        return next(s for s in self.shots if s.f0 <= f < s.f1) if f < self.frames else self.shots[-1]

    def event(self, kind: str, n: int = 0) -> dict:
        """The n-th scripted event of a kind (dribble, pass, shot, dunk, transition)."""
        return [e for e in self.events if e["kind"] == kind][n]

    def in_frame(self, i: int) -> bool:
        b = self.ball[i]
        return b is not None and 0.0 <= b[0] <= 1.0 and 0.0 <= b[1] <= 1.0

    def ball_point(self, i: int, status: str = "detected", conf: float = 0.9) -> BallPoint:
        """The real ball at sample i as a BallPoint (missing off the court)."""
        b = self.ball[i]
        if b is None:
            return BallPoint(t=self.times[i])
        return BallPoint(t=self.times[i], x=b[0], y=b[1], status=status, conf=conf,
                         vx=self.ball_v[i][0], vy=self.ball_v[i][1])


@dataclass
class Built:
    obs: Observation
    truth: Truth

    @property
    def samples(self) -> list[Sample]:
        return self.obs.samples

    def between(self, t0: float, t1: float) -> list[Sample]:
        """The samples with t0 <= t < t1."""
        return [s for s in self.obs.samples if t0 <= s.t < t1]


# ---- the actors -------------------------------------------------------------------------------


def _interp(keys: list[tuple], t: float) -> tuple:
    """Linear between keyframes (t, value...), held before the first and after the last."""
    times = [k[0] for k in keys]
    i = bisect_right(times, t)
    if i == 0:
        return keys[0][1:]
    if i == len(keys):
        return keys[-1][1:]
    (ta, *a), (tb, *b) = keys[i - 1], keys[i]
    u = (t - ta) / (tb - ta) if tb > ta else 1.0
    return tuple(va + (vb - va) * u for va, vb in zip(a, b))


def _put(keys: list[tuple], key: tuple) -> None:
    """Add a keyframe, replacing one at the same time."""
    keys[:] = [k for k in keys if abs(k[0] - key[0]) > 1e-9] + [key]
    keys.sort(key=lambda k: k[0])


@dataclass
class _Player:
    keys: list[tuple[float, float, float]]           # (t, world x, feet y)
    wander: tuple[float, float, float]               # x amplitude, angular speed, phase
    h: float                                         # height at middle depth
    jumps: list[tuple[float, float, float]] = field(default_factory=list)   # (t0, t1, height)

    def ground(self, t: float) -> tuple[float, float]:
        """Scripted world x and feet y (no wander, no jump)."""
        x, feet = _interp(self.keys, t)
        return x, feet

    def box(self, t: float) -> tuple[float, float, float, float]:
        """World box (centre x, centre y, w, h): farther from the camera (feet
        higher in the frame) means smaller."""
        x, feet = self.ground(t)
        a, w, ph = self.wander
        x += a * math.sin(w * t + ph)
        lift = sum(hgt * math.sin(math.pi * (t - j0) / (j1 - j0)) for j0, j1, hgt in self.jumps if j0 <= t <= j1)
        h = self.h * (0.85 + 0.3 * (feet - 0.58) / 0.28)
        return x, feet - lift - h / 2, h * PERSON_W / ASPECT, h

    def hands(self, t: float) -> tuple[float, float]:
        x, y, w, h = self.box(t)
        return x + 0.25 * w, y - 0.1 * h


@dataclass
class _Segment:
    """A stretch of the ball's path. `pos(t)` and `holder(t)` hold for every
    t >= t0: after t1 the segment says where the ball rests (in the receiver's
    hands, on the floor under the basket) until the next segment begins."""

    kind: str
    t0: float
    t1: float
    pos: Callable[[float], tuple[float, float]]     # world (x, y) at t
    holder: Callable[[float], int | None]           # the player who has it at t
    rate: float | None                              # its detection chance, when not the scene's


# ---- the scene --------------------------------------------------------------------------------


class Scene:
    """The builder. Every method returns the scene, so calls chain; build()
    makes the Observation and the Truth."""

    def __init__(self, duration: float, seed: int, fps: float, hz: float):
        self.duration, self.fps, self.seed = duration, fps, seed
        self.frames = round(duration * fps)
        self._hz = hz
        self._dense: list[tuple[float, float, float]] = []
        self._cam: list[tuple[float, float]] = [(0.0, 0.0)]
        self._tilt: list[tuple[float, float]] = [(0.0, 0.0)]
        self._jumps: list[tuple[int, float]] = []
        self._cuts: dict[int, dict] = {}
        self._flashes: list[tuple[int, int]] = []
        self._windows: list[tuple[float, float, str, dict]] = []
        self._players: list[_Player] = []
        self._fans: list[tuple[float, float, float]] = []      # world x, feet y, h
        self._detect = (0.95, 0.7)                             # players', spectators' detection chance
        self._segments: list[_Segment] = []
        self._events: list[dict] = []
        self._hidden: list[tuple[float, float]] = []
        self._false: list[dict] = []
        self._baskets: dict[str, dict] = {}
        self._ball = {"rate": 0.6, "noise": 0.003, "conf": (0.25, 0.7)}
        self._bug_box = (0.05, 0.9, 0.33, 0.97)
        self._scripted = False

    def _rng(self, stream: str) -> random.Random:
        return random.Random(f"{self.seed}:{stream}")

    def _frame(self, t: float) -> int:
        return max(0, min(self.frames - 1, round(t * self.fps)))

    # ---- camera and cuts ----

    def camera(self, *keys: tuple[float, float]) -> Scene:
        """Camera keyframes (t, x): its travel to the right in frame widths
        since the clip began, linear in between. (1.0, 0.0), (1.6, 0.5) is a
        fast pan: half the frame in 0.6 s."""
        for key in keys:
            _put(self._cam, key)
        return self

    def tilt(self, *keys: tuple[float, float]) -> Scene:
        """Vertical camera keyframes (t, y), in frame heights (down is +)."""
        for key in keys:
            _put(self._tilt, key)
        return self

    def cam(self, f: int) -> tuple[float, float]:
        """The camera's travel at frame f: (x, y)."""
        t = f / self.fps
        return _interp(self._cam, t)[0] + sum(dx for fj, dx in self._jumps if f >= fj), _interp(self._tilt, t)[0]

    def cut_at(self, t: float, jump: float = 0.0, d_gray: float | None = None, d_col: float | None = None) -> Scene:
        """A hard cut: the frame nearest t is the new shot's first. `jump`
        moves the camera that far (frame widths) at the cut, another camera's
        view. d_gray and d_col override the cut frame's signals (defaults:
        28-55 and 0.25-0.45, a clear cut)."""
        f = self._frame(t)
        self._cuts[f] = {"kind": "hard", "d_gray": d_gray, "d_col": d_col}
        if jump:
            self._jumps.append((f, jump))
        return self

    def wipe(self, t: float, frames: int = 12) -> Scene:
        """A transition (a replay wipe) of `frames` frames ending just before
        t: no frame is a hard cut, but together they change the picture."""
        f = self._frame(t)
        self._cuts[f] = {"kind": "transition", "span": (f - frames, f - 1)}
        return self

    def flash(self, t: float, frames: int = 1) -> Scene:
        """A camera flash: `frames` bright frames, then the same picture. Not a cut."""
        self._flashes.append((self._frame(t), frames))
        return self

    def _window(self, kind: str, t0: float, t1: float, cuts: bool, **opts) -> Scene:
        self._windows.append((t0, t1, kind, opts))
        for t in (t0, t1) if cuts else ():
            if 0 < t < self.duration and self._frame(t) not in self._cuts:
                self.cut_at(t)
        return self

    def closeup(self, t0: float, t1: float, bug: bool = True, cuts: bool = True) -> Scene:
        """A TV close-up from t0 to t1: one person 0.6 of the frame tall (and
        sometimes a second, cut off at the side), little floor, no ball. Hard
        cuts at both ends unless `cuts` is False. `bug`: the score bug stays up."""
        return self._window("closeup", t0, t1, cuts, bug=bug)

    def crowd(self, t0: float, t1: float, cuts: bool = True) -> Scene:
        """A crowd shot: eight fans 0.2-0.42 tall, almost no floor."""
        return self._window("crowd", t0, t1, cuts)

    def graphic(self, t0: float, t1: float, cuts: bool = True) -> Scene:
        """A full-screen graphic: nobody, no floor, no score bug."""
        return self._window("graphic", t0, t1, cuts)

    def replay(self, t0: float, t1: float, wipes: bool = True) -> Scene:
        """A replay: the court as usual, but the score bug is gone; wiped in
        and out (transitions) unless `wipes` is False (hard cuts)."""
        self._windows.append((t0, t1, "replay", {}))
        for t in (t0, t1):
            if 0 < t < self.duration and wipes:
                self.wipe(t)
            elif 0 < t < self.duration:
                self.cut_at(t)
        return self

    def sampling(self, hz: float = 10.0, dense: tuple[float, float, float] | None = None) -> Scene:
        """Samples every 1/hz s, plus every 1/dense_hz s over dense = (t0, t1,
        dense_hz), the frames looked at closely around the play."""
        self._hz = hz
        if dense:
            self._dense.append(dense)
        return self

    # ---- people ----

    def players(self, n: int = 10, h: float = PLAYER_H, homes: list[tuple[float, float]] | None = None,
                x_range: tuple[float, float] = (0.08, 0.92), feet_range: tuple[float, float] = (0.58, 0.86),
                spectators: int = 4, detect: float = 0.95, wander: float = 0.02) -> Scene:
        """n floor players (ids 0..n-1), about `h` tall, standing at `homes`
        (world x, feet y) or spread at random over x_range and feet_range at
        least 0.16 frame heights apart; each drifts sideways up to `wander`
        (frame widths) about home. `spectators` fans, about 0.08 tall, sit high
        in the stands. Each player is detected with chance `detect`, each fan
        with 0.7. Call it before move() and the ball_* calls, which script
        these players (without it, they get the defaults)."""
        if self._scripted:
            raise ValueError("players() must come before move() and the ball_* calls")
        rng = self._rng("players")
        spots = list(homes or [])
        sep, tries = 0.16, 0
        while len(spots) < n:
            x, feet = rng.uniform(*x_range), rng.uniform(*feet_range)
            if all(math.hypot((x - a) * ASPECT, feet - b) >= sep for a, b in spots):
                spots.append((x, feet))
            tries += 1
            if tries % 300 == 0:
                sep *= 0.9
        self._players = [_Player(keys=[(0.0, x, feet)], h=h,
                                 wander=(wander * rng.uniform(0.5, 1.0), rng.uniform(0.6, 1.4),
                                         rng.uniform(0, 2 * math.pi)))
                         for x, feet in spots[:n]]
        self._fans = [(rng.uniform(0.05, 0.95), rng.uniform(0.17, 0.27), rng.uniform(0.07, 0.09))
                      for _ in range(spectators)]
        self._detect = (detect, 0.7)
        return self

    def move(self, player: int, t0: float, t1: float, x: float, feet: float | None = None) -> Scene:
        """Player runs from where he is at t0 to world (x, feet) at t1."""
        p = self._player(player)
        self._scripted = True
        x0, f0 = p.ground(t0)
        p.keys = [k for k in p.keys if not t0 < k[0] < t1]
        _put(p.keys, (t0, x0, f0))
        _put(p.keys, (t1, x, f0 if feet is None else feet))
        return self

    def _player(self, pid: int) -> _Player:
        if not self._players:
            self.players()
        return self._players[pid]

    # ---- the ball ----

    def _segment(self, kind: str, t0: float, t1: float, pos, holder, rate: float | None, **event) -> Scene:
        self._scripted = True
        self._segments.append(_Segment(kind, t0, t1, pos, holder, rate))
        self._segments.sort(key=lambda s: s.t0)
        self._events.append({"kind": kind, "t0": t0, "t1": t1, **event})
        self._events.sort(key=lambda e: e["t0"])
        return self

    def _dribble_pos(self, pid: int, t0: float):
        p = self._player(pid)

        def pos(t: float) -> tuple[float, float]:
            x, y, w, h = p.box(t)
            floor = y + h / 2 - BALL_D / 2
            bounce = abs(math.cos(math.pi * (t - t0) / DRIBBLE_PERIOD))
            return x + 0.45 * w, floor - (floor - y) * bounce
        return pos

    def ball_dribble(self, t0: float, t1: float, player: int, rate: float | None = None) -> Scene:
        """The player dribbles from t0 to t1 (hand to floor every
        DRIBBLE_PERIOD), then holds the ball until the next segment. `rate`:
        the chance the ball is detected in a sample, instead of the scene's."""
        p, bounce = self._player(player), self._dribble_pos(player, t0)

        def pos(t):
            return bounce(t) if t <= t1 else p.hands(t)
        return self._segment("dribble", t0, t1, pos, lambda t: player, rate, player=player)

    def ball_pass(self, t0: float, t1: float, frm: int, to: int, rate: float | None = None) -> Scene:
        """A pass leaving frm's hands at t0, caught by `to` at t1 (a slight
        arc), then held by him. Truth: passer, receiver, catch."""
        a, b = self._player(frm), self._player(to)

        def pos(t):
            if t >= t1:
                return b.hands(t)
            (x0, y0), (x1, y1) = a.hands(t0), b.hands(t1)
            u = (t - t0) / (t1 - t0)
            return x0 + (x1 - x0) * u, y0 + (y1 - y0) * u - 0.02 * math.sin(math.pi * u)

        def holder(t):
            return frm if t <= t0 else (to if t >= t1 else None)
        return self._segment("pass", t0, t1, pos, holder, rate, passer=frm, receiver=to, release=t0, catch=t1)

    def _rim(self, name: str) -> dict:
        if name not in self._baskets:
            self.basket(name)
        return self._baskets[name]

    def ball_shot(self, t0: float, t1: float, shooter: int, basket: str = "right", made: bool = True,
                  arc: float = 0.18, rate: float | None = None) -> Scene:
        """A jump shot released above the shooter's head at t0, on an arc
        `arc` frame heights above the straight line, reaching the rim at t1.
        Made: through the net (NET below the rim) by t1 + 0.3, then to the
        floor under the basket by t1 + 0.8. Missed: off the rim back toward
        the court. Truth: shooter, basket, release, rim, made, result_end
        (t1 + 1.5)."""
        p, rim = self._player(shooter), self._rim(basket)
        rx, ry, out = rim["x"], rim["y"], 1.0 if rim["side"] == "right" else -1.0
        x0, y0, _, h0 = p.box(t0)
        start = (x0, y0 - 0.62 * h0)

        def pos(t):
            if t <= t1:
                u = (t - t0) / (t1 - t0)
                return (start[0] + (rx - start[0]) * u,
                        start[1] + (ry - start[1]) * u - 4 * arc * u * (1 - u))
            if made:
                if t <= t1 + 0.3:
                    return rx, ry + NET * (t - t1) / 0.3
                u = min(1.0, (t - t1 - 0.3) / 0.5)
                return rx - out * 0.02 * u, ry + NET + (FLOOR_UNDER_RIM - NET) * u
            u = min(1.0, (t - t1) / 0.9)
            return rx - out * 0.1 * u, ry - 0.08 * math.sin(math.pi * min(1.0, u * 1.5)) + FLOOR_UNDER_RIM * u * u

        return self._segment("shot", t0, t1, pos, lambda t: shooter if t < t0 else None, rate,
                             shooter=shooter, basket=basket, made=made, release=t0, rim=t1,
                             result_end=t1 + 1.5, distance=abs(rx - start[0]))

    def ball_dunk(self, t0: float, t1: float, player: int, basket: str = "right", rate: float | None = None) -> Scene:
        """The player drives from where he is at t0 to under the rim, jumps
        and dunks: the ball goes through at t1, then drops to the floor."""
        p, rim = self._player(player), self._rim(basket)
        rx, ry, out = rim["x"], rim["y"], 1.0 if rim["side"] == "right" else -1.0
        self.move(player, t0, t1 - 0.25, rx - out * 0.035, ry + FLOOR_UNDER_RIM)
        p.jumps.append((t1 - 0.5, t1 + 0.15, 0.07))
        above, through = (rx, ry - 0.05), (rx, ry + NET)

        def pos(t):
            if t <= t1 - 0.35:
                return p.hands(t)
            if t <= t1 - 0.05:
                (hx, hy), u = p.hands(t1 - 0.35), (t - t1 + 0.35) / 0.3
                return hx + (above[0] - hx) * u, hy + (above[1] - hy) * u
            if t <= t1 + 0.2:
                u = (t - t1 + 0.05) / 0.25
                return rx, above[1] + (through[1] - above[1]) * u
            u = min(1.0, (t - t1 - 0.2) / 0.4)
            return rx - out * 0.02 * u, through[1] + (FLOOR_UNDER_RIM - NET) * u

        return self._segment("dunk", t0, t1, pos, lambda t: player if t < t1 - 0.05 else None, rate,
                             player=player, basket=basket, rim=t1, result_end=t1 + 1.5)

    def ball_transition(self, t0: float, t1: float, player: int, to_x: float, camera: bool = True,
                        rate: float | None = None) -> Scene:
        """A cross-court break: the player dribbles from where he is at t0 to
        world x `to_x` at t1. With `camera`, the camera follows him (keyframes
        at t0 and t1), so he keeps his place in the picture."""
        p = self._player(player)
        x0, _ = p.ground(t0)
        if camera:
            c0 = _interp(self._cam, t0)[0]
            self.camera((t0, c0), (t1, c0 + to_x - x0))
        self.move(player, t0, t1, to_x)
        bounce = self._dribble_pos(player, t0)

        def pos(t):
            return bounce(t) if t <= t1 else p.hands(t)
        return self._segment("transition", t0, t1, pos, lambda t: player, rate,
                             player=player, from_x=x0, to_x=to_x)

    def hide_ball(self, t0: float, t1: float) -> Scene:
        """No detection of the real ball from t0 to t1 (behind a player)."""
        self._hidden.append((t0, t1))
        return self

    def ball_detection(self, rate: float = 0.6, noise: float = 0.003,
                       conf: tuple[float, float] = (0.25, 0.7)) -> Scene:
        """The real ball is detected in a sample with chance `rate` when it is
        in frame, off by gaussian `noise` (frame fractions), with a detector
        confidence uniform in `conf`."""
        self._ball = {"rate": rate, "noise": noise, "conf": conf}
        return self

    def false_ball(self, kind: str, x: float | None = None, y: float | None = None, rate: float | None = None,
                   player: int = 0, fan: int = 0, world: bool = True) -> Scene:
        """Things a detector takes for the ball, each seen with chance `rate`:
        - "logo": a ball-sized orange mark at (x, y), default (0.5, 0.74);
          painted on the floor (`world`), so it moves with the camera, or a
          screen overlay (world=False) that doesn't. Default rate 0.5.
        - "head": a spectator's head (`fan`), default rate 0.3.
        - "shoe": an orange shoe at a player's feet (`player`), default rate 0.25.
        - "noise": anywhere at random, default rate 0.2."""
        default = {"logo": 0.5, "head": 0.3, "shoe": 0.25, "noise": 0.2}[kind]
        self._false.append({"kind": kind, "x": 0.5 if x is None else x, "y": 0.74 if y is None else y,
                            "rate": default if rate is None else rate, "player": player, "fan": fan,
                            "world": world})
        return self

    # ---- baskets ----

    def basket(self, side: str = "right", x: float | None = None, y: float = 0.36, w: float = 0.022,
               backboard: bool = True, rate: float = 0.85, bb_rate: float = 0.6, bb_out: float = 1.4,
               name: str | None = None) -> Scene:
        """A basket with its baseline on `side`: the rim's centre at world (x,
        y) (x defaults to 0.86 on the right, 0.14 on the left), `w` wide (frame
        widths). The real backboard runs from just inside the rim's outer edge
        to `bb_out` rim widths past it, and from 1.6 rim widths (in frame
        heights) above the rim to just below it. The rim is detected in a
        sample with chance `rate` when in frame; when `backboard`, the board is
        found with it with chance `bb_rate`, clipped to the frame. Named by its
        side unless `name`."""
        x = (0.86 if side == "right" else 0.14) if x is None else x
        rh, rw_h, out = w * RIM_H * ASPECT, w * ASPECT, 1.0 if side == "right" else -1.0
        rim = (x - w / 2, y - rh / 2, x + w / 2, y + rh / 2)
        edge = x + out * w / 2
        bx0, bx1 = sorted((edge - out * 0.15 * w, edge + out * bb_out * w))
        board = (bx0, rim[1] - 1.6 * rw_h, bx1, rim[3] + 0.25 * rw_h)
        net = (rim[0] + 0.1 * w, rim[3], rim[2] - 0.1 * w, rim[3] + NET)
        self._baskets[name or side] = {"side": side, "x": x, "y": y, "w": w, "rim": rim, "backboard": board,
                                       "net": net, "found": backboard, "rate": rate, "bb_rate": bb_rate}
        return self

    # ---- building ----

    def _ball_state(self, t: float):
        """(world position, holder, segment) of the ball at t, or None with no segments."""
        if not self._segments:
            return None
        i = bisect_right([s.t0 for s in self._segments], t) - 1
        seg = self._segments[max(0, i)]
        tt = max(t, seg.t0)
        return seg.pos(tt), seg.holder(tt), seg

    def _true_shots(self) -> list[TrueShot]:
        edges = [0, *sorted(f for f in self._cuts if 0 < f < self.frames), self.frames]
        shots = []
        for f0, f1 in zip(edges, edges[1:]):
            mid = (f0 + f1) / 2 / self.fps
            kind, replay = "wide", False
            for t0, t1, wkind, _ in self._windows:
                if t0 <= mid < t1:
                    if wkind == "replay":
                        replay = True
                    else:
                        kind = wkind
            shots.append(TrueShot(f0, f1, f0 / self.fps, f1 / self.fps, kind, replay))
        return shots

    def _window_opts(self, t: float) -> dict:
        return next((o for t0, t1, k, o in self._windows if t0 <= t < t1 and k != "replay"), {})

    def _signals(self, shots: list[TrueShot]) -> FrameSignals:
        rng, n = self._rng("signals"), self.frames
        starts = [s.f0 for s in shots]
        level = [LUMA[s.kind] + rng.uniform(-8, 8) for s in shots]
        gray, col, luma, pdx, pdy = [], [], [], [], []
        for f in range(n):
            k = bisect_right(starts, f) - 1
            if f == 0 or f == shots[k].f0:
                dx = dy = 0.0
            else:
                (x1, y1), (x0, y0) = self.cam(f), self.cam(f - 1)
                dx, dy = x1 - x0, y1 - y0
            pdx.append(dx)
            pdy.append(dy)
            gray.append(0.0 if f == 0 else 1.0 + abs(rng.gauss(0, 0.4)) + PAN_GRAY * (abs(dx) + abs(dy)))
            col.append(0.0 if f == 0 else 0.008 + abs(rng.gauss(0, 0.004)))
            luma.append(level[k] + rng.gauss(0, 0.3))
        for f, cut in sorted(self._cuts.items()):
            if not 0 < f < n:
                continue
            if cut["kind"] == "hard":
                gray[f] = cut["d_gray"] if cut["d_gray"] is not None else rng.uniform(28, 55)
                col[f] = cut["d_col"] if cut["d_col"] is not None else rng.uniform(0.25, 0.45)
                continue
            a, b = cut["span"]
            k = bisect_right(starts, f) - 1
            old, new = level[k - 1], level[k]
            for g in range(max(1, a), b + 1):
                gray[g], col[g] = rng.uniform(12, 16), rng.uniform(0.04, 0.06)
                luma[g] = old + (new - old) * (g - a + 1) / (b - a + 2)
        for k, m in self._flashes:
            for g in range(k, min(n, k + m)):
                luma[g] += 90.0
            for g in (k, k + m):
                if 0 < g < n:
                    gray[g], col[g] = rng.uniform(45, 60), rng.uniform(0.22, 0.3)
        return FrameSignals(fps=self.fps, width=192, height=108, d_gray=gray, d_col=col,
                            pan_dx=pdx, pan_dy=pdy, luma=luma)

    def _sample_frames(self) -> list[tuple[int, bool]]:
        frames = {self._frame(k / self._hz): False for k in range(int(self.duration * self._hz + 1e-9) + 1)
                  if k / self._hz < self.duration}
        for t0, t1, dhz in self._dense:
            k = 0
            while t0 + k / dhz < min(t1, self.duration):
                frames[self._frame(t0 + k / dhz)] = True
                k += 1
        return sorted(frames.items())

    def build(self) -> Built:
        if not self._players:
            self.players()
        shots = self._true_shots()
        signals = self._signals(shots)
        starts = [s.f0 for s in shots]
        rngs = {k: self._rng(k) for k in ("people", "ball", "false", "rims", "looks")}
        samples: list[Sample] = []
        tr: dict[str, list] = {k: [] for k in ("who", "labels", "ball", "ball_v", "handler", "rims",
                                               "backboards", "times")}
        for i, (f, dense) in enumerate(self._sample_frames()):
            shot = shots[bisect_right(starts, f) - 1]
            t = f / self.fps
            (cx, cy), (sx, sy) = self.cam(f), self.cam(shot.f0)
            s = Sample(i=i, frame=f, t=t, dense=dense, imgsz=1920 if dense else 1280, pan=(cx - sx, cy - sy))
            who: list[str] = []
            labels: list[str] = []
            rims: dict[str, tuple] = {}
            boards: dict[str, tuple] = {}
            state = self._ball_state(t)
            vel = (0.0, 0.0)
            if state is not None:
                (ax, ay), _, _ = self._ball_state(t - 0.02)
                (bx, by), _, _ = self._ball_state(t + 0.02)
                vel = ((bx - ax) * ASPECT / 0.04, (by - ay) / 0.04)
            ball = None
            if shot.kind == "wide":
                ball = self._court(s, (cx, cy), shot.replay, rngs, who, labels, rims, boards, vel)
            else:
                self._cutaway(s, shot.kind, rngs["looks"], who)
            samples.append(s)
            tr["who"].append(who)
            tr["labels"].append(labels)
            tr["ball"].append(ball)
            tr["ball_v"].append(vel)
            tr["handler"].append(state[1] if state is not None and shot.kind == "wide" else None)
            tr["rims"].append(rims)
            tr["backboards"].append(boards)
            tr["times"].append(t)
        geometry = Geometry(width=WIDTH, height=HEIGHT, fps=self.fps, frames=self.frames)
        obs = Observation(geometry=geometry, signals=signals, samples=samples, bug_box=self._bug_box)
        truth = Truth(fps=self.fps, frames=self.frames,
                      cuts=[(f, c["kind"]) for f, c in sorted(self._cuts.items()) if 0 < f < self.frames],
                      spans={f: c["span"] for f, c in self._cuts.items() if c["kind"] == "transition"},
                      flashes=sorted(k for k, _ in self._flashes), shots=shots, events=list(self._events),
                      baskets={k: {key: b[key] for key in ("side", "rim", "backboard", "net", "found")}
                               for k, b in self._baskets.items()},
                      **tr)
        return Built(obs=obs, truth=truth)

    # ---- what one sample sees ----

    def _court(self, s: Sample, cam: tuple[float, float], replay: bool, rngs: dict, who: list, labels: list,
               rims: dict, boards: dict, vel: tuple[float, float]) -> tuple[float, float] | None:
        """Fill a court sample; returns the real ball's image position. A
        moving ball is blurred: its box grows by its travel during SHUTTER
        (a pass at 1.2 frame heights a second, by about a third of its size)."""
        cx, cy = cam
        rng = rngs["people"]
        s.court = 0.5 + rng.uniform(-0.04, 0.04)
        s.bug = False if replay else rng.random() < 0.97
        people: list[tuple[Person, str]] = []
        for pid, p in enumerate(self._players):
            x, y, w, h = p.box(s.t)
            seen = rng.random() < self._detect[0]
            if seen and 0.0 <= x - cx <= 1.0:
                people.append((Person(x - cx + rng.gauss(0, 0.003), y - cy + rng.gauss(0, 0.003),
                                      w * (1 + rng.gauss(0, 0.03)), h * (1 + rng.gauss(0, 0.02)),
                                      conf=rng.uniform(0.5, 0.95)), f"player:{pid}"))
        for k, (x, feet, h) in enumerate(self._fans):
            seen = rng.random() < self._detect[1]
            if seen and 0.0 <= x - cx <= 1.0:
                people.append((Person(x - cx + rng.gauss(0, 0.002), feet - h / 2 - cy, h * PERSON_W / ASPECT, h,
                                      conf=rng.uniform(0.3, 0.6)), f"fan:{k}"))
        rng.shuffle(people)
        s.people = [p for p, _ in people]
        who.extend(w for _, w in people)

        cands: list[tuple[BallCand, str]] = []
        state = self._ball_state(s.t)
        ball = None
        if state is not None:
            (bx, by), _, seg = state
            ball = (bx - cx, by - cy)
            rate = self._ball["rate"] if seg.rate is None or s.t < seg.t0 else seg.rate
            hidden = any(t0 <= s.t < t1 for t0, t1 in self._hidden)
            rng = rngs["ball"]
            seen = rng.random() < rate
            if seen and not hidden and 0.0 <= ball[0] <= 1.0 and 0.0 <= ball[1] <= 1.0:
                real = self._cand(rng, ball, self._ball["noise"], BALL_D, self._ball["conf"], (0.55, 0.9))
                real.w += abs(vel[0]) * SHUTTER / ASPECT
                real.h += abs(vel[1]) * SHUTTER
                cands.append((real, "ball"))
        for fb in self._false:
            spot = self._false_spot(fb, s.t, cam, rngs["false"])
            if spot is not None:
                cands.append(spot)
        rngs["false"].shuffle(cands)
        s.balls = [c for c, _ in cands]
        labels.extend(lb for _, lb in cands)

        for name, b in self._baskets.items():
            rim = (b["rim"][0] - cx, b["rim"][1] - cy, b["rim"][2] - cx, b["rim"][3] - cy)
            board = (b["backboard"][0] - cx, b["backboard"][1] - cy, b["backboard"][2] - cx, b["backboard"][3] - cy)
            if not 0.0 <= (rim[0] + rim[2]) / 2 <= 1.0:
                continue
            rims[name], boards[name] = rim, board
            rng = rngs["rims"]
            if rng.random() >= b["rate"]:
                continue
            found = b["found"] and rng.random() < b["bb_rate"]
            bb = tuple(min(1.0, max(0.0, v + rng.gauss(0, 0.002))) for v in board) if found else None
            s.rims.append(RimCand(x=(rim[0] + rim[2]) / 2 + rng.gauss(0, 0.0015),
                                  y=(rim[1] + rim[3]) / 2 + rng.gauss(0, 0.0015),
                                  w=(rim[2] - rim[0]) * (1 + rng.gauss(0, 0.05)),
                                  h=(rim[3] - rim[1]) * (1 + rng.gauss(0, 0.05)),
                                  score=rng.uniform(0.5, 0.9), backboard=bb))
        return ball

    @staticmethod
    def _cand(rng: random.Random, at: tuple[float, float], noise: float, d: float, conf: tuple[float, float],
              orange: tuple[float, float]) -> BallCand:
        size = d * (1 + rng.gauss(0, 0.1))
        return BallCand(x=at[0] + rng.gauss(0, noise), y=at[1] + rng.gauss(0, noise), w=size / ASPECT, h=size,
                        conf=rng.uniform(*conf), orange=rng.uniform(*orange))

    def _false_spot(self, fb: dict, t: float, cam: tuple[float, float],
                    rng: random.Random) -> tuple[BallCand, str] | None:
        cx, cy = cam
        kind = fb["kind"]
        seen = rng.random() < fb["rate"]
        if kind == "logo":
            at = (fb["x"] - cx, fb["y"] - cy) if fb["world"] else (fb["x"], fb["y"])
            d, conf, orange = 0.028, (0.2, 0.45), (0.55, 0.65)
        elif kind == "head":
            if not self._fans:
                return None
            x, feet, h = self._fans[fb["fan"] % len(self._fans)]
            at, d, conf, orange = (x - cx, feet - 0.9 * h - cy), 0.022, (0.15, 0.35), (0.25, 0.4)
        elif kind == "shoe":
            x, y, w, h = self._players[fb["player"]].box(t)
            at, d, conf, orange = (x + 0.15 * w - cx, y + h / 2 - 0.008 - cy), 0.016, (0.15, 0.35), (0.75, 0.9)
        else:
            at, d, conf, orange = (rng.uniform(0.05, 0.95), rng.uniform(0.1, 0.9)), rng.uniform(0.01, 0.04), \
                (0.1, 0.3), (0.1, 0.6)
        if not seen or not (0.0 <= at[0] <= 1.0 and 0.0 <= at[1] <= 1.0):
            return None
        return self._cand(rng, at, 0.0015, d, conf, orange), kind

    def _cutaway(self, s: Sample, kind: str, rng: random.Random, who: list) -> None:
        """Fill a close-up, crowd or graphic sample: no ball, no rims."""
        if kind == "closeup":
            s.court, s.bug = 0.1 + rng.uniform(-0.02, 0.02), bool(self._window_opts(s.t).get("bug", True))
            s.people = [Person(0.5 + 0.02 * math.sin(s.t), 0.62, 0.6 * PERSON_W / ASPECT, 0.6, conf=0.9)]
            who.append("closeup")
            if rng.random() < 0.3:
                s.people.append(Person(0.1, 0.7, 0.3 * PERSON_W / ASPECT, 0.3, conf=0.5))
                who.append("closeup")
        elif kind == "crowd":
            s.court, s.bug = 0.05, True
            for k, h in enumerate((0.42, 0.38, 0.3, 0.28, 0.25, 0.24, 0.22, 0.2)):
                h += rng.uniform(-0.01, 0.01)
                s.people.append(Person(0.08 + 0.11 * k + rng.uniform(-0.01, 0.01), 0.55 + rng.uniform(-0.05, 0.05),
                                       h * PERSON_W / ASPECT, h, conf=0.6))
                who.append("crowd")
        else:
            s.court, s.bug = 0.0, False
