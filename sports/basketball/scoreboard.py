"""Basketball's score bug, read: every basket, its points, and the game clock.

A basketball bug shows both teams' scores, the quarter and the game clock
counting down ("LAL 98  BOS 101  4TH  0:32"), often the shot clock and
timeouts too. A score that goes up by 1, 2 or 3 on one side, and stays up for
two readings, is a made free throw, two or three: ground truth, as a soccer
goal is. The quarter and the clock say how much it mattered (the last seconds
of a close fourth quarter against the second quarter of a blowout).

The box is found and read with the plumbing every sport shares
(sports/core/scorebug.py); this file is what basketball's text means. Nothing
is guessed: a jump of more than 3 at once (two baskets between readings) is
followed without being called a basket, and a video with no readable bug (a
gym camera, a phone in the stands) is scored without one.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from sports.core import scorebug

# The period: "1ST", "2ND QTR", "Q3", "4TH", "OT", "2OT", "OT2", "1ST HALF", "H2".
PERIOD = re.compile(r"(?<![A-Z0-9])(?:([1-4])(?:ST|ND|RD|TH)(?:\s*(QTR|QUARTER|HALF|HLF))?|Q([1-4])|([1-4])Q"
                    r"|H([12])|([2-9])?OT([2-9])?)(?![A-Z0-9])")
# The game clock: "11:42", "0:32", "2:05.4"; under a minute "24.3" or ":32".
CLOCK = re.compile(r"(?<![\d:.])(\d{1,2}):(\d{2})(?:\.\d)?(?![\d:])|(?<![\d:.])(\d{1,2})\.(\d)(?![\d.:])"
                   r"|(?<![\d])[:](\d{2})(?![\d:])")
# A team code and its score, either way round: "LAL 98", "98 LAL".
CODE_SCORE = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0]{1,3})\s*[|:·.\-]?\s*(\d{1,3})(?![\d:.])")
SCORE_CODE = re.compile(r"(?<![\d:.])(\d{1,3})\s*[|:·.\-]?\s*([A-Z][A-Z0]{1,3})(?![A-Z0-9])")
# Without team codes, two scores with a dash between: "98 - 101".
DASH_SCORE = re.compile(r"(?<![\d:.])(\d{1,3})\s*[-–]\s*(\d{1,3})(?![\d:.])")
# Words a bug writes beside the score that look like a team code.
NOT_TEAMS = {"QTR", "OT", "ST", "ND", "RD", "TH", "BONUS", "FOUL", "FOULS", "TO", "TOL", "TOS", "HALF", "HLF",
             "FINAL", "PTS", "REB", "AST", "FG", "FT", "PF", "SHOT", "Q", "H"}

EVERY = 5.0              # seconds between readings when frames have to be sought one by one
LOOKBACK = 25.0          # a basket can be this long before its new score shows (the bug updates in seconds)
MAX_POINTS = 3           # one basket's worth; more at once is two baskets between readings


@dataclass
class Reading:
    t: float
    score: tuple | None = None       # (first team, second team), as the bug lists them
    teams: tuple | None = None       # ("LAL", "BOS"), when the bug names them
    period: int | None = None        # 1-4, 5 = overtime, 6 = double overtime...
    clock: float | None = None       # seconds left in the period
    halves: bool = False             # the bug counts halves (college), not quarters
    visible: bool = False
    text: str = ""
    minute: int | None = None        # (a match minute: none in basketball, for the shared code)


@dataclass
class ScoreChange:
    lo: float                        # the basket went in between lo and hi
    hi: float
    before: tuple
    after: tuple
    team: str = ""                   # the side that scored, when the bug names it
    points: int = 0
    side: int = 0                    # 0 or 1: which number went up

    def label(self) -> str:
        who = f" ({self.team})" if self.team else ""
        return f"score {self.after[0]}-{self.after[1]}{who}, +{self.points}"


@dataclass
class Scoreboard:
    box: tuple | None = None
    readings: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    halftime: float | None = None    # (soccer's; unused)

    def final(self) -> tuple | None:
        for r in reversed(self.readings):
            if r.score is not None:
                return r.score
        return None

    def teams(self) -> tuple | None:
        pairs = Counter(r.teams for r in self.readings if r.teams)
        return pairs.most_common(1)[0][0] if pairs else None

    def halves(self) -> bool:
        return sum(r.halves for r in self.readings) > len(self.readings) / 4 if self.readings else False

    def last_period(self) -> int:
        """The regulation's last period: the 4th quarter, or the 2nd half."""
        return 2 if self.halves() else 4

    def _near(self, t: float, within: float, has) -> "Reading | None":
        near = [r for r in self.readings if abs(r.t - t) <= within and has(r)]
        return min(near, key=lambda r: abs(r.t - t)) if near else None

    def period_number(self, t: float) -> int | None:
        r = self._near(t, 300, lambda r: r.period is not None)
        return r.period if r is not None else None

    def period_at(self, t: float) -> str:
        """q1-q4 or ot (the Quarter choices), or "" when the bug's period
        wasn't read near t, or the game is played in halves (college): a half
        is no quarter, so a Quarter choice keeps it and says so."""
        n = self.period_number(t)
        if n is None:
            return ""
        last = self.last_period()
        if n > last:
            return "ot"
        return "" if last == 2 else f"q{n}"

    def minute_at(self, t: float) -> int | None:
        return None                            # basketball has no match minutes

    def video_time_at(self, clock: float, second_half: bool | None = None) -> float | None:
        return None

    def clock_at(self, t: float) -> float | None:
        """Seconds left in the period at video time t. The game clock stops
        for every whistle, so the reading just before t (at most 20 s before)
        is run down by the time since, and never below a reading just after."""
        before = [r for r in self.readings if r.clock is not None and 0 <= t - r.t <= 20]
        after = [r for r in self.readings if r.clock is not None and 0 < r.t - t <= 20]
        if not before and not after:
            return None
        if before:
            r = max(before, key=lambda r: r.t)
            left = max(0.0, r.clock - (t - r.t))
            if after:
                left = max(left, min(after, key=lambda r: r.t).clock)
            return left
        return min(after, key=lambda r: r.t).clock

    def score_before(self, t: float) -> tuple | None:
        prior = [r for r in self.readings if r.score is not None and r.t < t]
        return max(prior, key=lambda r: r.t).score if prior else None

    def when(self, t: float) -> str:
        """"Q4 0:32", "OT 1:05" or "" for a moment at video time t."""
        period, left = self.period_at(t), self.clock_at(t)
        if not period:
            return ""
        name = "OT" if period == "ot" else (f"H{period[1]}" if self.last_period() == 2 else period.upper())
        if left is None:
            return name
        return f"{name} {int(left) // 60}:{int(left) % 60:02d}"

    def hidden(self, lo: float, hi: float) -> bool:
        inside = [r for r in self.readings if lo <= r.t <= hi]
        return bool(inside) and sum(not r.visible for r in inside) > len(inside) / 2


def _period(m: re.Match) -> tuple[int, bool]:
    quarter, kind, q_a, q_b, half, ot_before, ot_after = m.groups()
    if quarter:
        return int(quarter), bool(kind) and kind.startswith("H")
    if q_a or q_b:
        return int(q_a or q_b), False
    if half:
        return int(half), True
    extra = int(ot_before or ot_after or 1)
    return 4 + extra, False                    # OT is the 5th period (in halves, read as past the 2nd)


def parse(texts: list[str]) -> Reading:
    """What a bug's text says: the scores, the teams, the period and the clock."""
    line = " ".join(" ".join(str(t).split()) for t in texts)
    out = Reading(t=0.0, visible=bool(line.strip()), text=line)
    upper = line.upper()
    rest = upper
    p = PERIOD.search(rest)
    if p:
        out.period, out.halves = _period(p)
        rest = rest[:p.start()] + " " + rest[p.end():]
    c = CLOCK.search(rest)
    if c:
        if c.group(1) is not None and int(c.group(2)) < 60 and int(c.group(1)) <= 20:
            out.clock = int(c.group(1)) * 60 + int(c.group(2))
        elif c.group(3) is not None:
            out.clock = int(c.group(3)) + int(c.group(4)) / 10
        elif c.group(5) is not None:
            out.clock = float(int(c.group(5)))
        if out.clock is not None:
            rest = rest[:c.start()] + " " + rest[c.end():]
    pairs = [(m.group(1).replace("0", "O"), int(m.group(2)), m.start()) for m in CODE_SCORE.finditer(rest)]
    pairs = [x for x in pairs if x[0] not in NOT_TEAMS]
    if len(pairs) < 2:
        flipped = [(m.group(2).replace("0", "O"), int(m.group(1)), m.start()) for m in SCORE_CODE.finditer(rest)]
        flipped = [x for x in flipped if x[0] not in NOT_TEAMS]
        if len(flipped) >= 2:
            pairs = flipped
        elif len(pairs) == 1 and len(flipped) == 1 and pairs[0][0] != flipped[0][0]:
            pairs = sorted([pairs[0], flipped[0]], key=lambda x: x[2])
    if len(pairs) >= 2 and pairs[0][0] != pairs[1][0]:
        out.teams = (pairs[0][0], pairs[1][0])
        out.score = (pairs[0][1], pairs[1][1])
    else:
        d = DASH_SCORE.search(rest)
        if d:
            out.score = (int(d.group(1)), int(d.group(2)))
    return out


def changes(readings: list[Reading]) -> list[ScoreChange]:
    """Confirmed baskets: one side's score up by 1, 2 or 3 and the other's
    unchanged, seen on two readings in a row."""
    scored = [r for r in readings if r.score is not None]
    out: list[ScoreChange] = []
    current: tuple | None = None
    last_old_t = 0.0
    teams = next((x.teams for x in scored if x.teams), None)
    for i, r in enumerate(scored):
        if current is None:
            if i + 1 < len(scored) and scored[i + 1].score == r.score:
                current, last_old_t = r.score, r.t
            continue
        if r.score == current:
            last_old_t = r.t
            continue
        confirmed = i + 1 < len(scored) and scored[i + 1].score == r.score
        if not confirmed:
            continue                                  # a misread: the next reading decides
        up = (r.score[0] - current[0], r.score[1] - current[1])
        side = 0 if up[0] else 1
        if up[1 - side] == 0 and 1 <= up[side] <= MAX_POINTS:
            out.append(ScoreChange(lo=max(0.0, min(last_old_t, r.t - LOOKBACK)), hi=r.t, before=current,
                                   after=r.score, team=teams[side] if teams else "", points=up[side],
                                   side=side))
        # Anything else (two baskets between readings, a correction) is
        # followed without being called a basket.
        current, last_old_t = r.score, r.t
    return out


def from_readings(readings: list[Reading], box: tuple | None = None) -> Scoreboard:
    readings = sorted(readings, key=lambda r: r.t)
    board = Scoreboard(box=box, readings=readings)
    teams = board.teams()
    if teams is not None:
        for r in readings:
            if r.teams and set(r.teams) != set(teams):
                r.teams = None                    # a misread code: the pair most readings agree on stands
    board.changes = changes(board.readings)
    return board


def read(grab, duration: float, find_ocr=None, rec=None, every: float = EVERY, cancel=None) -> Scoreboard:
    """The game's scoreboard, seeking a frame every `every` seconds. `grab`
    (seconds -> frame), `find_ocr` and `rec` stand in for the video and the
    OCR in tests; read_video() is the fast path for a file."""
    if find_ocr is None:
        from analysis.game_text import _ocr as find_ocr
    rec = rec or scorebug.rec_line
    box = scorebug.find_box(grab, duration, find_ocr, parse, CLOCK)
    if box is None:
        return Scoreboard()
    readings = []
    t = 0.0
    while t < duration:
        if cancel is not None:
            cancel()
        img = grab(t)
        if img is not None:
            r = parse([rec(scorebug.crop(img, box))])
            r.t = t
            readings.append(r)
        t += every
    return from_readings(readings, box)


def read_video(path, duration: float, cancel=None) -> Scoreboard:
    """read() on a video file: the box found on a few sought frames, then every
    keyframe's crop read in one pass; seeking when keyframes are too sparse."""
    import cv2

    from core.modes import probe_size
    from video.capture import video_capture

    with video_capture(path, required=False) as cap:
        if cap is None:
            return Scoreboard()

        def grab(t: float):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, img = cap.read()
            return img if ok else None

        from analysis.game_text import _ocr

        box = scorebug.find_box(grab, duration, _ocr, parse, CLOCK)
        if box is None:
            return Scoreboard()
        texts: dict[int, str] = {}
        try:
            times = scorebug.keyframe_crops(path, box, probe_size(path),
                                            lambda i, img: texts.__setitem__(i, scorebug.rec_line(img)), cancel)
        except OSError:
            times = []
        # A basket is a few seconds of play: keyframes further apart than
        # EVERY * 2 on average would merge baskets, so those are sought instead.
        if len(times) >= max(10, duration / (EVERY * 2)):
            readings = []
            for i, t in enumerate(times):
                if i in texts:
                    r = parse([texts[i]])
                    r.t = t
                    readings.append(r)
            return from_readings(readings, box)
        return read(grab, duration, find_ocr=_ocr, cancel=cancel)
