"""Who is on the floor, who has the ball, and who is near it.

A small box tracker that keeps each player's id through camera pans (the pan
is taken out before boxes are compared), the floor players told from the
crowd, and the ball handler. Pure Python (see model.py).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from sports.basketball.crop.model import BallPoint, Person, Sample
from sports.basketball.crop.shots import TALL

MATCH_IOU = 0.3          # boxes this overlapped (world coordinates) are the same person...
MATCH_DIST = 0.5         # ...or centres within this share of the box height
TRACK_TTL = 0.6          # seconds a track waits unseen before it is dropped
TRACK_VMAX = 2.0         # frame heights per second: faster than a sprint, so a bad match can't fling a track
FLOOR_REL = 0.5          # a floor player is at least this share of the tallest floor-height person
STANDS_DROP = 0.25       # the stands line: this far (frame heights) above the floor players' median feet,
#                          times the camera's Sample.scale (a camera that shows the players 1.8 times as
#                          tall spreads the floor over more of the frame: at 0.25 the far side's players
#                          were taken for spectators, and the ball in their hands for a fan's head)
STANDS_MIN = 3           # with fewer floor players than this there is no stands line
HOLD_WIDEN = 1.2         # a player's box, widened this much, holds the ball he has
HANDS_UP = 0.15          # his upper body's centre: this share of his height above the box centre...
HANDS_REACH = 0.35       # ...and the ball within this share of his height of it
ACTIVE_RANGE = 0.15      # active players: within this share of the frame's width of the ball


@dataclass
class _Track:
    id: int
    box: tuple[float, float, float, float]   # world x, y, w, h (frame heights)
    t: float
    vx: float = 0.0
    vy: float = 0.0

    def guess(self, t: float) -> tuple[float, float]:
        dt = t - self.t
        return self.box[0] + self.vx * dt, self.box[1] + self.vy * dt

    def update(self, box: tuple[float, float, float, float], t: float) -> None:
        dt = t - self.t
        if 0 < dt <= TRACK_TTL:
            self.vx = _clamp((box[0] - self.box[0]) / dt, TRACK_VMAX)
            self.vy = _clamp((box[1] - self.box[1]) / dt, TRACK_VMAX)
        self.box, self.t = box, t


def _clamp(v: float, limit: float) -> float:
    return max(-limit, min(limit, v))


def _world(p: Person, pan: tuple[float, float], aspect: float) -> tuple[float, float, float, float]:
    """A box with the camera's travel taken out, in frame heights on both axes."""
    return (p.x + pan[0]) * aspect, p.y + pan[1], p.w * aspect, p.h


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix = min(a[0] + a[2] / 2, b[0] + b[2] / 2) - max(a[0] - a[2] / 2, b[0] - b[2] / 2)
    iy = min(a[1] + a[3] / 2, b[1] + b[3] / 2) - max(a[1] - a[3] / 2, b[1] - b[3] / 2)
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = ix * iy
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)


def _cost(track: _Track, box: tuple[float, float, float, float], t: float) -> float | None:
    """How far the box is from the track, in units of MATCH_DIST x height
    (from where the track was or where its speed says it is now, whichever is
    closer), or None when neither the distance nor the overlap says it's him."""
    gx, gy = track.guess(t)
    dist = min(math.hypot(box[0] - track.box[0], box[1] - track.box[1]), math.hypot(box[0] - gx, box[1] - gy))
    cost = dist / (MATCH_DIST * (track.box[3] + box[3]) / 2)
    moved = (gx, gy, track.box[2], track.box[3])
    if cost <= 1.0 or max(_iou(track.box, box), _iou(moved, box)) >= MATCH_IOU:
        return cost
    return None


def track(samples: list[Sample], aspect: float, first_id: int = 0) -> dict[int, list[tuple[int, Person]]]:
    """Each person seen, followed from sample to sample: greedy matching by
    IoU >= MATCH_IOU or centre distance <= MATCH_DIST x height, in world
    coordinates (camera travel removed). New ids for people who match no track;
    a track unseen for TRACK_TTL s is dropped. Writes each id onto its
    Person.track and returns {id: [(Sample.i, Person), ...]}.

    Run it per shot (pans are measured from each shot's start); `first_id`
    keeps ids from different shots apart."""
    out: dict[int, list[tuple[int, Person]]] = {}
    live: list[_Track] = []
    next_id = first_id
    for s in samples:
        live = [tr for tr in live if s.t - tr.t <= TRACK_TTL]
        boxes = [_world(p, s.pan, aspect) for p in s.people]
        pairs = sorted((cost, k, j) for k, tr in enumerate(live) for j, box in enumerate(boxes)
                       if (cost := _cost(tr, box, s.t)) is not None)
        taken_t: set[int] = set()
        taken_p: set[int] = set()
        for _cost_, k, j in pairs:
            if k in taken_t or j in taken_p:
                continue
            taken_t.add(k)
            taken_p.add(j)
            live[k].update(boxes[j], s.t)
            s.people[j].track = live[k].id
        for j, box in enumerate(boxes):
            if j in taken_p:
                continue
            live.append(_Track(id=next_id, box=box, t=s.t))
            s.people[j].track = next_id
            next_id += 1
        for p in s.people:
            out.setdefault(p.track, []).append((s.i, p))
    return out


def floor_players(sample: Sample) -> list[Person]:
    """The people on the floor: no taller than TALL (nearer the camera than
    the floor), at least FLOOR_REL of the tallest such person, and with their
    feet below the stands line, which sits STANDS_DROP above the floor
    players' median feet (no line with fewer than STANDS_MIN of them). The
    tallest is taken among people up to TALL, so one person walking past the
    camera doesn't hide the floor. TALL and STANDS_DROP are scaled by the
    camera's Sample.scale."""
    people = [p for p in sample.people if p.h <= TALL * sample.scale]
    if not people:
        return []
    tallest = max(p.h for p in people)
    floor = [p for p in people if p.h >= FLOOR_REL * tallest]
    if len(floor) < STANDS_MIN:
        return floor
    line = statistics.median(p.y + p.h / 2 for p in floor) - STANDS_DROP * sample.scale
    return [p for p in floor if p.y + p.h / 2 > line]


def holder(sample: Sample, ball: BallPoint | None, aspect: float = 16 / 9) -> Person | None:
    """The floor player who has the ball: his box widened HOLD_WIDEN holds it,
    or it's within HANDS_REACH x his height of his upper body's centre. When
    several do, the one whose upper body is closest. Anyone seen counts when
    there are no floor players (a close-up)."""
    if ball is None or ball.x is None or ball.y is None:
        return None
    best: tuple[float, Person] | None = None
    for p in floor_players(sample) or sample.people:
        inside = abs(ball.x - p.x) <= HOLD_WIDEN * p.w / 2 and abs(ball.y - p.y) <= HOLD_WIDEN * p.h / 2
        dist = math.hypot((ball.x - p.x) * aspect, ball.y - (p.y - HANDS_UP * p.h))
        if (inside or dist <= HANDS_REACH * p.h) and (best is None or dist < best[0]):
            best = (dist, p)
    return best[1] if best else None


def handler(sample: Sample, ball: BallPoint | None, aspect: float = 16 / 9) -> int | None:
    """The ball handler's track id (see holder), or None. Run track() first,
    or every Person's id is None."""
    p = holder(sample, ball, aspect)
    return p.track if p else None


def active(sample: Sample, ball: BallPoint | None, handler: int | Person | None = None) -> list[Person]:
    """The floor players within ACTIVE_RANGE of the frame's width of the ball,
    or of the handler (a track id or the Person) when the ball isn't known."""
    ref = ball.x if ball is not None and ball.x is not None else None
    if ref is None and isinstance(handler, Person):
        ref = handler.x
    elif ref is None and handler is not None:
        ref = next((p.x for p in sample.people if p.track == handler), None)
    if ref is None:
        return []
    return [p for p in floor_players(sample) if abs(p.x - ref) <= ACTIVE_RANGE]
