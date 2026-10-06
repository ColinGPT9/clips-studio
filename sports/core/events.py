"""A sporting moment, and grouping one with its replays.

A broadcast shows a goal, the celebration, then a replay or two, each with the
commentary and the crowd lifting again. Clipped naively that is three clips of
one goal. Here they are one moment: a replay found within the sport's
replay_within_seconds after a stronger moment joins that moment's group, and
the group keeps its best-scoring clip.
"""

from dataclasses import dataclass, field


@dataclass
class SportEvent:
    type: str                    # a key of the sport's `events` in config/sports.yaml
    t: float                     # when it happened, seconds into the video
    confidence: float = 0.0      # 0..1: how many independent signals agree
    importance: int = 0          # from the sport's event list
    start: float = 0.0           # the clip window around it
    end: float = 0.0
    team: str = ""               # only when the scoreboard or the commentary names it
    player: str = ""             # the same; never guessed
    period: str = ""             # first_half | second_half | extra_time | "" when unknown
    minute: int | None = None    # the match minute, when the clock was read
    signals: list = field(default_factory=list)   # what marked it: "crowd", "score 1-0", ...
    is_replay: bool = False
    group: int = 0               # the moment it belongs to (0: not grouped yet)
    confirmed: bool = False      # the scoreboard confirmed it: the score changed for it
    when: str = ""               # the game clock as the sport writes it ("Q4 0:32"), when read
    context: str = ""            # the game's situation that changed its worth ("tied, 0:02 left")
    person: str = ""             # someone on screen, only as the broadcast's own caption names them

    def overlaps(self, other: "SportEvent", min_ratio: float = 0.3) -> bool:
        """Whether the two windows share at least min_ratio of the shorter."""
        overlap = min(self.end, other.end) - max(self.start, other.start)
        if overlap <= 0:
            return False
        shorter = min(self.end - self.start, other.end - other.start)
        return shorter > 0 and overlap / shorter >= min_ratio

    def why(self) -> str:
        return "; ".join(str(s) for s in self.signals[:4])


def valid(event: SportEvent, duration: float) -> bool:
    """A usable event: inside the video, with a window around its moment."""
    return (0 <= event.t <= max(duration, 0)
            and event.start <= event.t <= event.end
            and event.end > event.start)


def group_moments(events: list[SportEvent], within: float, plays_apart: bool = False) -> list[SportEvent]:
    """Give every event a group: a replay within `within` seconds after a
    moment (and not itself a new, stronger moment) joins that moment's group;
    events whose windows overlap are one moment too. Returns the events, in
    time order, with `group` set.

    `plays_apart`: two confirmed moments are never one, however close
    (basketball: on an NBA game a Thunder three 6 s after a Spurs three was
    "the same moment", and had no clip)."""
    ordered = sorted(events, key=lambda e: e.t)
    group = 0
    anchors: list[SportEvent] = []
    for e in ordered:
        home = None
        for a in reversed(anchors):
            if e.t - a.t > within:
                break
            if plays_apart and e.confirmed and a.confirmed:
                continue
            if e.is_replay or e.overlaps(a):
                home = a
                break
        if home is None:
            group += 1
            e.group = group
            anchors.append(e)
        else:
            e.group = home.group
            if not e.is_replay and e.importance > home.importance:
                # The same moment, seen better: a goal the crowd marked first
                # as a big moment is still a goal.
                anchors[anchors.index(home)] = e
    return ordered


def best_per_moment(events: list[SportEvent], score) -> list[SportEvent]:
    """One event per group: the original over a replay, then the higher
    `score(event)`."""
    best: dict[int, SportEvent] = {}
    for e in events:
        cur = best.get(e.group)
        if cur is None or (cur.is_replay, -score(cur)) > (e.is_replay, -score(e)):
            best[e.group] = e
    return sorted(best.values(), key=lambda e: e.t)
