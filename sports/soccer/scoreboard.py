"""The score bug, read: the scoreboard is ground truth.

A broadcast keeps the score and the match clock in a small box, usually along
the top. When the score changes, a goal went in shortly before; the clock says
which half a moment is in. HypeCut's broadcast profile puts it plainly: "the
scoreboard is ground truth, not a proxy."

Cost, measured on a 1 h 42 min 1080p final:
- Finding the box needs OCR's text search on whole bands of a few frames:
  about 1.5 s a band, so it stops as soon as five frames agree.
- Reading it then needs no search at all. One ffmpeg pass decodes only the
  keyframes (every few seconds) cropped to the box: 1,510 crops in 19 s.
  Each is read as a single line by the recogniser alone: about 16 ms, against
  a second for a full OCR.
It uses the OCR the app already ships for games (RapidOCR, analysis/game_text.py).

Nothing is guessed. A score counts only when two readings in a row agree, a
change of more than one goal at once is left to the other signals, and a video
with no readable score bug (sideline footage, a stream) is scored without it.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from sports.core import scorebug
from sports.core.scorebug import FIND_FRAMES, rec_line
from sports.core.scorebug import crop as _crop
from sports.core.scorebug import keyframe_crops as _keyframe_crops

# Team code, score, team code, as the recogniser reads a bug's line:
#   "HOM 1-0 AWO", "(> HOM  1: 0 AW0  26:47", "(HOM1:1AW037:37", "(HOM4·2|AW069:17".
# A 0 inside a team code is an O ("AW0" is AWO); the codes may touch the
# score and the clock may follow straight on.
TEAMS_SCORE = re.compile(
    r"(?<![A-Z0-9])([A-Z][A-Z0]{1,3})[\s|:·.,>(]*?(\d{1,2})\s*[-–:·.|]?\s*(\d{1,2})[\s|:·.,)]*([A-Z0][A-Z0]{1,3})(?![A-Z])")
# Without team codes, only a dash counts: "1 - 0". A clock's colon never does.
DASH_SCORE = re.compile(r"(?<![\d:])(\d{1,2})\s*[-–]\s*(\d{1,2})(?![\d:])")
O_AFTER = re.compile(r"(?<=\d)(\s*[:\-–·.|]\s*)[Oo](?![A-Za-z])")
O_BEFORE = re.compile(r"(?<![A-Za-z])[Oo](\s*[:\-–·.|]\s*)(?=\d)")
CLOCK = re.compile(r"(?<!\d)(\d{1,3})[:.'](\d{2})(?!\d)")

EVERY = 10.0             # seconds between readings when frames have to be sought one by one
GOAL_LOOKBACK = 180.0    # a bug updates after the celebration and replays: the
                         # goal itself can be this long before the new score shows


@dataclass
class Reading:
    t: float
    score: tuple | None = None       # (home, away)
    teams: tuple | None = None       # ("HOM", "AWO"), when the bug names them
    minute: int | None = None        # the match clock's minutes
    clock: int | None = None         # ...and the whole clock, in seconds
    visible: bool = False            # anything was read in the box at all
    text: str = ""                   # what was read, for a second look once the teams are known


@dataclass
class ScoreChange:
    lo: float                        # the goal went in between lo and hi
    hi: float
    before: tuple
    after: tuple
    team: str = ""                   # the side whose number went up, when named

    def label(self) -> str:
        who = f" ({self.team})" if self.team else ""
        return f"score {self.after[0]}-{self.after[1]}{who}"


@dataclass
class Scoreboard:
    box: tuple | None = None                      # (left, top, right, bottom), frame fractions
    readings: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    halftime: float | None = None                 # when the second half starts (video time)

    def final(self) -> tuple | None:
        for r in reversed(self.readings):
            if r.score is not None:
                return r.score
        return None

    def teams(self) -> tuple | None:
        for r in self.readings:
            if r.teams:
                return r.teams
        return None

    def period_at(self, t: float) -> str:
        """first_half, second_half, extra_time, or "" when the clock wasn't read."""
        minutes = [(r.t, r.minute) for r in self.readings if r.minute is not None]
        if not minutes:
            return ""
        near = min(minutes, key=lambda x: abs(x[0] - t))
        if abs(near[0] - t) > 300:
            return ""
        if near[1] >= 95 and any(m >= 105 for _, m in minutes):
            return "extra_time"
        if self.halftime is not None:
            return "first_half" if t < self.halftime else "second_half"
        return "first_half" if near[1] < 45 else "second_half"

    def minute_at(self, t: float) -> int | None:
        """The match minute at video time t the way a match report gives it
        (17:23 on the clock is the 18th minute). The clock runs with the
        video, so each reading within two minutes (in the same half) says how
        far ahead of the video it is, and the median of those says it best: a
        single reading can be a misread digit, seconds out. None when the
        clock wasn't read there."""
        half = None if self.halftime is None else t >= self.halftime
        ahead = sorted(r.clock - r.t for r in self.readings
                       if r.clock is not None and abs(r.t - t) <= 120
                       and (half is None or (r.t >= self.halftime) == half))
        if not ahead:
            return None
        return int(max(0.0, t + ahead[len(ahead) // 2]) // 60) + 1

    def video_time_at(self, clock: float, second_half: bool | None = None) -> float | None:
        """Where in the video the match clock read `clock` seconds: the
        median of how far ahead of the video the clock ran in the readings
        near that time (in the right half when the half is known, since
        the first half's added time and the second half share clock
        times). None when the clock wasn't read near there."""
        near = [r for r in self.readings if r.clock is not None and abs(r.clock - clock) <= 180]
        if self.halftime is not None and second_half is not None:
            near = [r for r in near if (r.t >= self.halftime) == second_half]
        if not near:
            return None
        ahead = sorted(r.clock - r.t for r in near)
        return max(0.0, clock - ahead[len(ahead) // 2])

    def hidden(self, lo: float, hi: float) -> bool:
        """Whether the bug was off screen for most readings in [lo, hi]: the
        broadcast hides it for replays and celebrations."""
        inside = [r for r in self.readings if lo <= r.t <= hi]
        return bool(inside) and sum(not r.visible for r in inside) > len(inside) / 2


def parse(texts: list[str]) -> Reading:
    """What a bug's text says: the score, the teams and the clock."""
    line = " ".join(" ".join(str(t).split()) for t in texts)
    # A 0 read as the letter O beside a score's separator ("1:O", "O-2"): soft
    # text (a phone filming a screen, an upscaled frame) reads it that way.
    line = O_AFTER.sub(r"\g<1>0", line)
    line = O_BEFORE.sub(r"0\g<1>", line)
    out = Reading(t=0.0, visible=bool(line.strip()), text=line)
    upper = line.upper()
    rest = line
    m = TEAMS_SCORE.search(upper)
    if m:
        out.teams = (m.group(1).replace("0", "O"), m.group(4).replace("0", "O"))
        out.score = (int(m.group(2)), int(m.group(3)))
        rest = line[m.end():]
    else:
        d = DASH_SCORE.search(line)
        if d:
            out.score = (int(d.group(1)), int(d.group(2)))
    c = CLOCK.search(rest)
    if c and int(c.group(2)) < 60 and int(c.group(1)) < 130:
        out.minute = int(c.group(1))
        out.clock = out.minute * 60 + int(c.group(2))
    return out


# ---- finding the box (sports/core/scorebug.py, with soccer's own parse) --------


def _score_lines(lines: list[tuple[tuple, str]], aspect: float) -> list[tuple[tuple, str]]:
    """The bug's own lines among everything read in a band (scorebug.score_lines)."""
    return scorebug.score_lines(lines, aspect, parse, CLOCK)


def find_box(grab, duration: float, ocr, frames: int = FIND_FRAMES) -> tuple | None:
    """Where the score bug is (scorebug.find_box), read as a soccer score."""
    return scorebug.find_box(grab, duration, ocr, parse, CLOCK, frames)


# ---- reading it ---------------------------------------------------------------------


def from_readings(readings: list[Reading], box: tuple | None = None) -> Scoreboard:
    readings = sorted(readings, key=lambda r: r.t)
    teams = known_teams(readings)
    if teams is not None:
        for r in readings:
            with_known_teams(r, teams)
    board = Scoreboard(box=box, readings=readings)
    board.changes = changes(board.readings)
    board.halftime = second_half_start(board.readings)
    return board


def known_teams(readings: list[Reading]) -> tuple | None:
    """The two team codes the box shows: the pair most readings agree on, a
    code with a letter too many in front (the flag beside it, read as "D"
    or ">") taken as the code it ends in when that is read too."""
    pairs = Counter(r.teams for r in readings if r.teams)
    if not pairs:
        return None
    seen = Counter()
    for pair, n in pairs.items():
        for code in pair:
            seen[code] += n

    def plain(code: str) -> str:
        return next((other for other in sorted(seen, key=len)
                     if other != code and len(other) >= 3 and code.endswith(other) and seen[other] >= 2), code)

    merged = Counter()
    for (a, b), n in pairs.items():
        merged[(plain(a), plain(b))] += n
    a, b = merged.most_common(1)[0][0]
    return (a, b) if a != b else None


# The recogniser's letters for digits, where a score is known to be.
AS_DIGITS = str.maketrans({"O": "0", "o": "0", "D": "0", "I": "1", "l": "1", "i": "1"})


def with_known_teams(r: Reading, teams: tuple) -> None:
    """A reading put right once the teams are known: their codes as the box
    writes them, and a score read from between the two codes when the parse
    missed it. Soft text loses the separator and reads 0 as a letter: in
    "HOMOOAW0" the score is the "OO"."""
    if r.teams:
        r.teams = teams
    if r.score is not None or not r.text:
        return
    upper = r.text.upper().replace("0", "O")
    first, second = (code.replace("0", "O") for code in teams)
    i = upper.find(first)
    j = upper.find(second, i + len(first)) if i >= 0 else -1
    if i < 0 or j < 0:
        return
    groups = re.findall(r"\d+", upper[i + len(first):j].translate(AS_DIGITS))
    if len(groups) == 1 and len(groups[0]) == 2:
        groups = [groups[0][0], groups[0][1]]         # "OO": two scores, their separator lost
    if len(groups) == 2 and all(len(g) <= 2 for g in groups):
        r.score = (int(groups[0]), int(groups[1]))
        r.teams = teams


def changes(readings: list[Reading]) -> list[ScoreChange]:
    """Confirmed goals: the score going up by one, seen on two readings in a
    row. Dated from GOAL_LOOKBACK before the new score (or the last reading of
    the old one, if earlier) to the first reading of the new one."""
    scored = [r for r in readings if r.score is not None]
    out: list[ScoreChange] = []
    current: tuple | None = None
    last_old_t = 0.0
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
        one_goal = sum(r.score) == sum(current) + 1 and all(n >= o for n, o in zip(r.score, current))
        if one_goal:
            side = 0 if r.score[0] > current[0] else 1
            teams = r.teams or next((x.teams for x in scored if x.teams), None)
            out.append(ScoreChange(lo=max(0.0, min(last_old_t, r.t - GOAL_LOOKBACK)), hi=r.t,
                                   before=current, after=r.score, team=teams[side] if teams else ""))
        # Anything else (two goals at once, a goal taken back) is followed
        # without being called a goal.
        current, last_old_t = r.score, r.t
    return out


def second_half_start(readings: list[Reading]) -> float | None:
    """When the second half starts: the first reading of minute 46-59, after a
    reading of 45 or earlier."""
    seen_first = False
    for r in readings:
        if r.minute is None:
            continue
        if r.minute <= 45:
            seen_first = True
        elif seen_first and 45 < r.minute < 60:
            return r.t
    return None


def read(grab, duration: float, find_ocr=None, rec=None, every: float = EVERY, cancel=None) -> Scoreboard:
    """The match's scoreboard, seeking a frame every `every` seconds. `grab`
    (seconds -> frame), `find_ocr` and `rec` stand in for the video and the
    OCR in tests; read_video() is the fast path for a file."""
    if find_ocr is None:
        from analysis.game_text import _ocr as find_ocr
    rec = rec or rec_line
    box = find_box(grab, duration, find_ocr)
    if box is None:
        return Scoreboard()
    readings = []
    t = 0.0
    while t < duration:
        if cancel is not None:
            cancel()
        img = grab(t)
        if img is not None:
            r = parse([rec(_crop(img, box))])
            r.t = t
            readings.append(r)
        t += every
    return from_readings(readings, box)


def read_video(path, duration: float, cancel=None) -> Scoreboard:
    """read() on a video file: the box found on a few sought frames, then every
    keyframe's crop read in one pass. Falls back to seeking when the file has
    too few keyframes to date a goal by."""
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

        box = find_box(grab, duration, _ocr)
        if box is None:
            return Scoreboard()
        texts: dict[int, str] = {}
        try:
            times = _keyframe_crops(path, box, probe_size(path),
                                    lambda i, img: texts.__setitem__(i, rec_line(img)), cancel)
        except OSError:
            times = []
        if len(times) >= max(10, duration / (EVERY * 3)):
            readings = []
            for i, t in enumerate(times):
                if i in texts:
                    r = parse([texts[i]])
                    r.t = t
                    readings.append(r)
            return from_readings(readings, box)
        board = read(grab, duration, find_ocr=_ocr, cancel=cancel)
        return board
