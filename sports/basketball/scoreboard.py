"""Basketball's score bug, read: every basket, its points, and the game clock.

A basketball bug shows both teams' scores, the quarter and the game clock
counting down ("LAL 98  BOS 101  4TH  0:32"), often the shot clock and
timeouts too. A score that goes up by 1, 2 or 3 on one side, and stays up for
two readings, is a made free throw, two or three: ground truth, as a soccer
goal is. The quarter and the clock say how much it mattered (the last seconds
of a close fourth quarter against the second quarter of a blowout).

The box is found and read with the plumbing every sport shares
(sports/core/scorebug.py); this file is what basketball's text means. Real
NBA bugs, measured on three games, are not soccer's one tight line: one
stacks the teams in two rows with their letters on their side, one shows
logos and two bare numbers, and read as one line by the recogniser alone
each came back as run-together digits. So the box is found around the game
clock (the one thing every bug has), each keyframe's box is read piece by
piece with the full OCR, and the two scores are told by where they sit: the
two biggest numbers that keep their place and never go down (the shot
clock runs down, the fouls and timeouts are smaller).

Nothing is guessed: a jump of more than 3 at once (two baskets between
readings) is followed without being called a basket, and a video with no
readable bug (a gym camera, a phone in the stands) is scored without one.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from sports.core import scorebug

# The period: "1ST", "2ND QTR", "Q3", "4TH", "OT", "2OT", "OT2", "1ST HALF", "H2".
PERIOD = re.compile(r"(?<![A-Z0-9])(?:([1-4])\s?(?:ST|ND|RD|TH)(?:\s*(QTR|QUARTER|HALF|HLF))?|Q([1-4])|([1-4])Q"
                    r"|H([12])|([2-9])?OT([2-9])?)(?![A-Z0-9])")
# The game clock: "11:42", "0:32", "2:05.4"; under a minute "24.3" or ":32".
CLOCK = re.compile(r"(?<![\d:.])(\d{1,2}):(\d{2})(?:\.\d)?(?![\d:])|(?<![\d:.])(\d{1,2})\.(\d)(?![\d.:])"
                   r"|(?<![\d])[:](\d{2})(?![\d:])")
# A team and its score, either way round: "LAL 98", "98 LAL", or a school's
# whole name, as high-school and college bugs write it ("ATTLEBORO 43").
CODE_SCORE = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0]{1,13})\s*[|:·.\-]?\s*(\d{1,3})(?![\d:.])")
SCORE_CODE = re.compile(r"(?<![\d:.])(\d{1,3})\s*[|:·.\-]?\s*([A-Z][A-Z0]{1,3})(?![A-Z0-9])")
# Without team codes, two scores with a dash between: "98 - 101".
DASH_SCORE = re.compile(r"(?<![\d:.])(\d{1,3})\s*[-–]\s*(\d{1,3})(?![\d:.])")
# ...and not a team's record in brackets beside its name: "GSW(25-20)".
BARE_DASH = re.compile(r"(?<![\d:.(（])(\d{1,3})\s*[-–]\s*(\d{1,3})(?![\d:.)）])")
# Only separators between a team's score and the other's: "LAL 98 - 101 BOS".
BETWEEN = re.compile(r"[\s|·\-–]*")
# The OCR can read a bug's boxes as one word ("TAUNTON37ATTLEBORO364TH", a
# 2022 high-school broadcast): then a period glued to the score before it,
# and letters glued to digits, are split apart, and the period's own
# spellings ("4TH", "Q3", "2OT") put back together.
GLUED_PERIOD = re.compile(r"(?<=\d)(?=[1-4](?:ST|ND|RD|TH)(?![A-Z]))")
GLUED_WORD = re.compile(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])")
REJOIN = re.compile(r"(?<![A-Z0-9])(?:(\d) (ST|ND|RD|TH|Q|OT)|(Q|H|OT) (\d))(?![A-Z0-9])")
# A team's code or school name on its own ("LAL", "GSW(25-20)": its record
# beside it, "ATTLEBORO").
CODE = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0]{1,13})(?![A-Z0-9])")
# A number on its own: a score, the shot clock, a foul or timeout count. Not
# a "+3" drawn over a score after a three (the graphic stays up for seconds).
NUMBER = re.compile(r"(?<![\d:.+])(?<!\+ )(\d{1,3})(?![\d:.])")
# Words a bug writes beside the score that look like a team code.
NOT_TEAMS = {"QTR", "OT", "ST", "ND", "RD", "TH", "BONUS", "FOUL", "FOULS", "TO", "TOL", "TOS", "HALF", "HLF",
             "FINAL", "PTS", "REB", "AST", "FG", "FT", "PF", "SHOT", "Q", "H"}

EVERY = 5.0              # seconds between readings when frames have to be sought one by one
LOOKBACK = 25.0          # a basket can be this long before its new score shows (the bug updates in seconds)
MAX_POINTS = 3           # one basket's worth; more at once is two baskets between readings
MAX_SCORE = 199          # a number above this is no score
BUG_NUMBERS = 2          # the fewest numbers beside the clock that make a block of text a bug
SLOT_READINGS = 6        # the fewest readings with numbers to tell the scores' places from
SLOT_X = 0.06            # a score keeps its place in the box: within this share of its width...
SLOT_SHARE = 0.3         # ...in at least this share of the readings
SLOT_DOWN = 0.2          # a score never goes down: at most this share of its changes may (misreads)
SLOT_HEIGHT = 0.7        # the two scores are about the same size, the box's biggest numbers
TEAM_SHARE = 0.5         # a team's code is read at its place in at least this share of the readings...
TEAM_SAME = 0.6          # ...as the same code at least this often (a logo reads differently each time)
TEAM_NEAR = 1.2          # ...on the scores' row, or within this many scores' heights of it
SAME_BOX = 0.001         # a box with fewer of its pixels changed than this since the last one read reads the same


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
    # Every number read apart from the period and the clock, as (x, y,
    # height, value) in the box's fractions: the scores among them are told
    # by their places over the whole game (from_readings).
    numbers: list = field(default_factory=list)
    codes: list = field(default_factory=list)        # (x, y, height, code) for each team-like code


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

    def seen_twice(self) -> list:
        """The readings whose score the reading before or after it (of those
        with a score) shows too. A score mid-roll or a misread is seen once:
        changes() counts only scores seen twice already, and score_before()
        and final() take only these (a "12-33" read mid-roll in a 40-33 game
        is no 21-point game)."""
        scored = [r for r in self.readings if r.score is not None]
        return [r for i, r in enumerate(scored)
                if (i > 0 and scored[i - 1].score == r.score)
                or (i + 1 < len(scored) and scored[i + 1].score == r.score)]

    def final(self) -> tuple | None:
        """The game's last score: the last one read, unless either side of
        it is below the last score seen twice (a score mid-roll or a "+3"
        over it reads lower; the last basket before the video ends may be
        read once)."""
        sure = self.seen_twice()
        if not sure:
            return None
        last = next(r.score for r in reversed(self.readings) if r.score is not None)
        settled = sure[-1].score
        return last if last[0] >= settled[0] and last[1] >= settled[1] else settled

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
        prior = [r for r in self.seen_twice() if r.t < t]
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
    if " " not in upper.strip():
        upper = unglue(upper)
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


def unglue(text: str) -> str:
    """A bug read as one word, split where letters meet digits and before a
    period glued to a score ("TAUNTON37ATTLEBORO364TH" ->
    "TAUNTON 37 ATTLEBORO 36 4TH")."""
    text = GLUED_WORD.sub(" ", GLUED_PERIOD.sub(" ", text.upper()))
    return REJOIN.sub(lambda m: "".join(g for g in m.groups() if g), text)


def _blank(text: str, pattern: re.Pattern) -> str:
    """`text` with every match of `pattern` turned to spaces, keeping each
    character's place."""
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def rows(pieces: list[tuple[tuple, str]]) -> list[list[tuple[tuple, str]]]:
    """Pieces of text in rows from the top, each row left to right."""
    found: list[dict] = []
    for box, text in sorted(pieces, key=lambda x: (x[0][1] + x[0][3]) / 2):
        mid = (box[1] + box[3]) / 2
        row = next((r for r in found if r["top"] <= mid <= r["bottom"]), None)
        if row is None:
            found.append({"top": box[1], "bottom": box[3], "pieces": [(box, text)]})
        else:
            row["pieces"].append((box, text))
    return [sorted(r["pieces"], key=lambda x: x[0][0]) for r in found]


def reading_order(pieces: list[tuple[tuple, str]]) -> list[tuple[tuple, str]]:
    """Pieces of text in reading order: row by row from the top, each row
    left to right."""
    return [p for row in rows(pieces) for p in row]


def _tall(starts: list[tuple[int, float]], i: int) -> float:
    """The height of the piece that the character at i of a row's text is in,
    from (where each piece starts in the text, its height)."""
    return [h for start, h in starts if start <= i][-1]


def row_pairs(rows: list[list[tuple[tuple, str]]]) -> tuple[tuple | None, tuple | None]:
    """(teams, score) when the bug writes each team's code beside its score
    on one row ("LAL 4  GS 5", "LAL 98 - 101 BOS", or a row each), or else
    two bare scores with a dash between ("98 - 101", "ESPN 98 - 101" after a
    network's logo; no teams): exactly one such pair of scores, the two numbers about the same size (a team's timeouts
    beside its code are smaller). (None, None) otherwise. A code and a number
    on different rows are never paired: a header's "RIVALS WEEK" over the
    shot clock is no team and its score. `rows`: the pieces of each row."""
    forward, flipped, mixed, dashed = [], [], [], []
    for row in rows:
        text, starts = "", []
        for box, piece in row:
            starts.append((len(text), box[3] - box[1]))
            text += piece + " "
        rest = _blank(_blank(text, PERIOD), CLOCK)
        ahead = [m for m in CODE_SCORE.finditer(rest) if m.group(1).replace("0", "O") not in NOT_TEAMS]
        behind = [m for m in SCORE_CODE.finditer(rest) if m.group(2).replace("0", "O") not in NOT_TEAMS]
        forward += [(m.group(1).replace("0", "O"), int(m.group(2)), _tall(starts, m.start(2))) for m in ahead]
        flipped += [(m.group(2).replace("0", "O"), int(m.group(1)), _tall(starts, m.start(1))) for m in behind]
        if (len(ahead) == 1 and len(behind) == 1 and ahead[0].end() <= behind[0].start()
                and BETWEEN.fullmatch(rest[ahead[0].end():behind[0].start()])):
            mixed.append([forward[-1], flipped[-1]])
        dashed += [((None, int(m.group(1)), _tall(starts, m.start(1))),
                    (None, int(m.group(2)), _tall(starts, m.start(2)))) for m in BARE_DASH.finditer(rest)]
    if len(forward) == 2:
        pairs = forward
    elif len(flipped) == 2:
        pairs = flipped
    elif len(forward) == 1 and len(flipped) == 1 and len(mixed) == 1:
        pairs = mixed[0]
    elif len(dashed) == 1:
        pairs = list(dashed[0])
    else:
        return None, None
    (code_a, a, tall_a), (code_b, b, tall_b) = pairs
    if (code_a is not None and code_a == code_b) or max(a, b) > MAX_SCORE:
        return None, None
    if min(tall_a, tall_b) < SLOT_HEIGHT * max(tall_a, tall_b):
        return None, None
    return ((code_a, code_b) if code_a is not None else None), (a, b)


def parse_pieces(pieces: list[tuple[tuple, str]]) -> Reading:
    """What a bug says, from the pieces the full OCR found in its box: the
    period and the clock parse() reads in their text, the teams and the
    score when each code sits beside its score on a row (row_pairs), and
    every number with its place, for from_readings to tell the scores by."""
    lines = [[(box, unglue(str(text))) for box, text in row] for row in rows(pieces)]
    ordered = [p for row in lines for p in row]
    out = parse([t for _, t in ordered])
    out.teams, out.score = row_pairs(lines)
    for box, text in ordered:
        rest = _blank(_blank(text, PERIOD), CLOCK)
        for m in CODE.finditer(rest):
            code = m.group(1).replace("0", "O")
            if code not in NOT_TEAMS:
                along = (m.start() + m.end()) / 2 / max(len(rest), 1)
                out.codes.append((box[0] + (box[2] - box[0]) * along, (box[1] + box[3]) / 2,
                                  box[3] - box[1], code))
        for m in NUMBER.finditer(rest):
            value = int(m.group(1))
            if value > MAX_SCORE:
                continue
            # A piece can hold more than one number ("LAL 98"): each sits at
            # its share along the piece.
            along = (m.start() + m.end()) / 2 / max(len(rest), 1)
            out.numbers.append((box[0] + (box[2] - box[0]) * along, (box[1] + box[3]) / 2,
                                box[3] - box[1], value))
    return out


def _bug_in(lines: list[tuple[tuple, str]], aspect: float) -> list[tuple[tuple, str]]:
    """The bug's own pieces among everything read in a band: the block of
    text around a game clock or a period with at least BUG_NUMBERS numbers
    beside it (two rows, logos between: scorebug.cluster); else, as soccer
    finds it, a run on one row that reads as a score. [] when neither."""
    anchors = [x for x in lines if CLOCK.search(x[1]) or PERIOD.search(str(x[1]).upper())]
    for anchor in anchors:
        group = scorebug.cluster(lines, anchor, aspect)
        if len(parse_pieces(group).numbers) >= BUG_NUMBERS:
            return group
    return scorebug.score_lines(lines, aspect, parse, CLOCK)


def find_box(grab, duration: float, ocr, frames: int = scorebug.FIND_FRAMES) -> tuple | None:
    """Where the score bug is: the block of text in the top or bottom band
    that _bug_in finds on the sampled frames, as the largest box most of them
    agree on (the bug grows for BONUS or a timeout count). None when fewer
    than FOUND_IN frames show one."""
    seen: list[tuple] = []
    for i in range(frames):
        img = grab(duration * (i + 1) / (frames + 1))
        if img is None:
            continue
        for band in scorebug.BANDS:
            strip = scorebug.crop(img, band)
            group = _bug_in(scorebug.texts(strip, ocr), strip.shape[0] / max(strip.shape[1], 1))
            if not group:
                continue
            bl, bt, br, bb = band
            boxes = [(bl + x0 * (br - bl), bt + y0 * (bb - bt), bl + x1 * (br - bl), bt + y1 * (bb - bt))
                     for (x0, y0, x1, y1), _ in group]
            seen.append((min(b[0] for b in boxes), min(b[1] for b in boxes),
                         max(b[2] for b in boxes), max(b[3] for b in boxes)))
            break
        if len(seen) >= scorebug.FOUND_AFTER:
            break
    if len(seen) < scorebug.FOUND_IN:
        return None
    # The box most frames agree on: sorted by position, the middle one (a
    # one-off graphic sorts to an end), grown to every box that overlaps it.
    seen.sort(key=lambda b: ((b[1] + b[3]) / 2, (b[0] + b[2]) / 2))
    middle = seen[len(seen) // 2]
    agree = [b for b in seen if _overlap(b, middle) >= 0.3]
    left, top = min(b[0] for b in agree), min(b[1] for b in agree)
    right, bottom = max(b[2] for b in agree), max(b[3] for b in agree)
    pad_x, pad_y = (right - left) * 0.05, (bottom - top) * 0.2
    return (max(0.0, left - pad_x), max(0.0, top - pad_y), min(1.0, right + pad_x), min(1.0, bottom + pad_y))


def _overlap(a: tuple, b: tuple) -> float:
    """Intersection over union of two boxes."""
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _steady(values: list[int]) -> list[int]:
    """The values seen on two readings running, each once in turn: a
    misread is seen once."""
    out: list[int] = []
    for a, b in zip(values, values[1:]):
        if a == b and (not out or out[-1] != a):
            out.append(a)
    return out


def score_places(readings: list[Reading]) -> tuple | None:
    """Where the two scores sit in the box, as two places (x, y, height) in
    reading order (the top or left team first), from every reading's
    numbers: the places a number keeps, that never go down (the shot clock
    runs down, the timeouts left too), the two biggest of them. None when
    fewer than SLOT_READINGS readings have numbers, or two such places
    aren't found."""
    read = [r for r in readings if r.numbers]
    if len(read) < SLOT_READINGS:
        return None
    places: list[dict] = []
    for r in read:
        taken: dict = {}                       # place -> (x, y, height) of this reading's number at it
        # The biggest first: a score claims its place before a smaller
        # number beside or over it can.
        for x, y, h, v in sorted(r.numbers, key=lambda n: -n[2]):
            near = [k for k, p in enumerate(places)
                    if abs(x - p["x"]) <= SLOT_X and abs(y - p["y"]) <= 0.5 * max(h, p["h"])]
            if any(abs(x - taken[k][0]) <= SLOT_X and abs(y - taken[k][1]) >= 0.25 * max(h, taken[k][2])
                   for k in near if k in taken):
                # A number above or below one this reading already placed:
                # a score mid-roll ("4" of the new score over "12" of the old).
                # Not a place of its own, or a team's score would split
                # across two. (A team's fouls beside its score, on its row,
                # keep a place of their own.)
                continue
            free = [k for k in near if k not in taken]
            if free:
                k = min(free, key=lambda k: abs(x - places[k]["x"]))
                p = places[k]
                p["seen"].append((r.t, v))
                n = len(p["seen"])
                p["x"] += (x - p["x"]) / n
                p["y"] += (y - p["y"]) / n
                p["h"] += (h - p["h"]) / n
            else:
                places.append({"x": x, "y": y, "h": h, "seen": [(r.t, v)]})
                k = len(places) - 1
            taken[k] = (x, y, h)
    scores = []
    for p in places:
        if len(p["seen"]) < SLOT_SHARE * len(read):
            continue
        steady = _steady([v for _, v in sorted(p["seen"])])
        if len(steady) < 2:
            continue
        downs = sum(b < a for a, b in zip(steady, steady[1:]))
        if downs > SLOT_DOWN * (len(steady) - 1):
            continue
        p["top"] = max(steady)
        scores.append(p)
    if len(scores) < 2:
        return None
    scores.sort(key=lambda p: -p["h"])
    first = scores[0]
    # The other score: as big as the first (within SLOT_HEIGHT), and of
    # those the one that counts highest (a team's fouls are smaller numbers).
    alike = [p for p in scores[1:] if p["h"] >= SLOT_HEIGHT * first["h"]]
    if not alike:
        return None
    second = max(alike, key=lambda p: (p["top"], p["h"]))
    pair = sorted([first, second], key=lambda p: (p["y"], p["x"])
                  if abs(first["y"] - second["y"]) > 0.5 * max(first["h"], second["h"]) else (p["x"], p["y"]))
    return tuple((p["x"], p["y"], p["h"]) for p in pair)


def team_codes(readings: list[Reading], places: tuple) -> tuple | None:
    """The two teams' codes, in the scores' order. First the pair written
    beside the scores (row_pairs) when most readings read it, its numbers
    the ones at the scores' places: a network's logo on the same row
    ("ESPN LAL 61 GS 54") is beside no score. Else from where codes are
    read in the box: a place where the same code is read most of the time
    (not a logo, read differently each time) on the scores' row or the one
    beside it. None unless exactly two such places are found: a code is
    never guessed (sideways letters, logos instead of codes)."""
    read = [r for r in readings if r.numbers]
    beside: Counter = Counter()
    for r in read:
        if r.teams and r.score:
            at = (_at(r, places[0]), _at(r, places[1]))
            if at == r.score:
                beside[r.teams] += 1
            elif at == r.score[::-1]:
                beside[r.teams[::-1]] += 1
    if beside:
        pair, n = beside.most_common(1)[0]
        if n >= TEAM_SHARE * len(read):
            return pair
    found: list[dict] = []
    for r in read:
        for x, y, h, code in r.codes:
            near = [p for p in found if abs(x - p["x"]) <= SLOT_X and abs(y - p["y"]) <= 0.5 * max(h, p["h"])]
            if near:
                near[0]["codes"].append(code)
            else:
                found.append({"x": x, "y": y, "h": h, "codes": [code]})
    teams = []
    for p in found:
        common = Counter(p["codes"]).most_common(1)[0]
        if (len(p["codes"]) >= TEAM_SHARE * len(read) and common[1] >= TEAM_SAME * len(p["codes"])
                and any(abs(p["y"] - y) <= TEAM_NEAR * h for _x, y, h in places)):
            teams.append((p["x"], p["y"], common[0]))
    if len(teams) != 2 or teams[0][2] == teams[1][2]:
        return None
    stacked = abs(places[0][1] - places[1][1]) > 0.5 * max(places[0][2], places[1][2])
    teams.sort(key=lambda p: (p[1], p[0]) if stacked else (p[0], p[1]))
    return teams[0][2], teams[1][2]


def _at(reading: Reading, place: tuple) -> int | None:
    """The number a reading has at a score's place: the nearest one the
    score's size. A team's fouls beside its score are smaller, and a
    one-digit score sits as far from the place as they do."""
    x, y, h = place
    near = [n for n in reading.numbers if abs(n[0] - x) <= SLOT_X and abs(n[1] - y) <= 0.5 * max(h, n[2])
            and n[2] >= SLOT_HEIGHT * h]
    return min(near, key=lambda n: abs(n[0] - x))[3] if near else None


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
    places = score_places(readings)
    if places is not None:
        # Each reading's score is the numbers at the scores' places: the same
        # two numbers every time, whatever else the bug shows beside them;
        # and the teams the codes read at their places, or none.
        teams = team_codes(readings, places)
        for r in readings:
            if r.numbers:
                a, b = _at(r, places[0]), _at(r, places[1])
                r.score = (a, b) if a is not None and b is not None else None
                r.teams = teams
    board = Scoreboard(box=box, readings=readings)
    teams = board.teams()
    if teams is not None:
        for r in readings:
            if r.teams and set(r.teams) != set(teams):
                r.teams = None                    # a misread code: the pair most readings agree on stands
    board.changes = changes(board.readings)
    return board


def read(grab, duration: float, find_ocr=None, every: float = EVERY, cancel=None) -> Scoreboard:
    """The game's scoreboard, seeking a frame every `every` seconds. `grab`
    (seconds -> frame) and `find_ocr` stand in for the video and the OCR in
    tests; read_video() is the fast path for a file."""
    if find_ocr is None:
        from analysis.game_text import _ocr as find_ocr
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
            r = parse_pieces(scorebug.pieces(scorebug.crop(img, box), find_ocr))
            r.t = t
            readings.append(r)
        t += every
    return from_readings(readings, box)


def read_video(path, duration: float, cancel=None) -> Scoreboard:
    """read() on a video file: the box found on a few sought frames, then every
    keyframe's crop read piece by piece in one pass; seeking when keyframes
    are too sparse."""
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
        found: dict[int, list] = {}
        last: dict = {}

        def on_frame(i: int, img) -> None:
            # A full OCR is most of a second a keyframe: a box that hasn't
            # changed since the last one read (the clock stopped for a foul,
            # a timeout, free throws) reads as that one did.
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            before = last.get("gray")
            if (before is not None and before.shape == gray.shape
                    and float((cv2.absdiff(before, gray) > 40).mean()) < SAME_BOX):
                found[i] = last["pieces"]
                return
            found[i] = last["pieces"] = scorebug.pieces(img, _ocr)
            last["gray"] = gray

        try:
            times = scorebug.keyframe_crops(path, box, probe_size(path), on_frame, cancel)
        except OSError:
            times = []
        # A basket is a few seconds of play: keyframes further apart than
        # EVERY * 2 on average would merge baskets, so those are sought instead.
        if len(times) >= max(10, duration / (EVERY * 2)):
            readings = []
            for i, t in enumerate(times):
                if i in found:
                    r = parse_pieces(found[i])
                    r.t = t
                    readings.append(r)
            return from_readings(readings, box)
        return read(grab, duration, find_ocr=_ocr, cancel=cancel)
