"""A match's events as the person has them: pasted from a match report, a
club app (Veo tags its goals), or the description of the upload itself.

    18:16 Goal! Player Name        a time in the video (m:ss or h:mm:ss)
    1:06:51 Goal                   ...even "1:14.29" or "1:14.29:", as typed
    18' own goal Team A            a match minute, from a report
    45+2' yellow card              ...with added time
    09:22 Kick off                 where the match starts in the video
    1:00:40 Second half            ...and where the second half does

Read in code, one line at a time: a line with no time or no kind of moment
is listed back, never guessed. A match minute is placed on the video by the
score box's clock, or by the kick-off lines when there's no clock; within
that minute, the crowd dates the moment when there is one (sports/core/
detect.py).
"""

import re
from dataclasses import dataclass

# h:mm:ss, with the typos people make ("1:14.29", a trailing colon).
HMS = re.compile(r"(?<![\d'])(\d{1,2})[:.](\d{2})[:.](\d{2})(?![\d'])")
# m:ss or mm:ss (a time in the video, however long).
MS = re.compile(r"(?<![\d:.'])(\d{1,3})[:.](\d{2})(?![\d:.'])")
# A match minute: 18' or 45+2' or 45'+2 (' or ’ or a word).
MINUTE = re.compile(r"(?<![\d:.])(\d{1,3})\s*(?:['’′]|min\b|mins\b|minutes?\b)(?:\s*\+\s*(\d{1,2})\s*['’′]?)?"
                    r"|(?<![\d:.])(\d{1,3})\s*\+\s*(\d{1,2})\s*['’′]", re.I)
# A bare minute at the start of a line ("67 goal"), as match reports list them.
LEADING = re.compile(r"^\s*(\d{1,3})(?![\d:.'’])\s+")

# The kinds of moment a line can name, longest first so "own goal" isn't a
# plain goal and "second yellow" isn't a yellow card. The sport's own labels
# and callouts are added to these (event_words).
WORDS = [
    ("own goal", "own_goal"), ("o.g.", "own_goal"), ("og", "own_goal"),
    ("penalty goal", "penalty_goal"), ("penalty scored", "penalty_goal"), ("pen scored", "penalty_goal"),
    ("penalty missed", "penalty_miss"), ("penalty saved", "penalty_miss"), ("missed penalty", "penalty_miss"),
    ("second yellow", "red_card"), ("red card", "red_card"), ("sent off", "red_card"), ("red", "red_card"),
    ("yellow card", "yellow_card"), ("booked", "yellow_card"), ("yellow", "yellow_card"),
    ("penalty", "penalty"), ("pen", "penalty"),
    ("second half", "kickoff_second"), ("2nd half", "kickoff_second"),
    ("kick off", "kickoff"), ("kick-off", "kickoff"), ("kickoff", "kickoff"), ("ko", "kickoff"),
    ("half time", "halftime"), ("half-time", "halftime"), ("halftime", "halftime"), ("ht", "halftime"),
    ("full time", "fulltime"), ("full-time", "fulltime"), ("fulltime", "fulltime"), ("ft", "fulltime"),
    ("free kick", "free_kick"), ("free-kick", "free_kick"),
    ("substitution", "substitution"), ("sub", "substitution"),
    ("goal", "goal"), ("scores", "goal"), ("scored", "goal"),
    ("save", "save"), ("chance", "chance"), ("shot", "shot"), ("corner", "corner"),
    ("offside", "offside"), ("injury", "injury"), ("var", "var"),
]
MAX_TEXT = 4000


@dataclass
class Listed:
    kind: str                     # an event type, or kickoff / kickoff_second / halftime / fulltime
    who: str = ""                 # the rest of the line: a player or a team, as written
    video_t: float | None = None  # a time in the video
    minute: int | None = None     # ...or a match minute
    added: int = 0                # its added time ("45+2'")
    line: str = ""


def event_words(spec: dict) -> list[tuple[str, str]]:
    """What a line can call each kind of moment: the words above (or the
    sport's own `listed_words`, for a sport whose words differ: "ft" is a free
    throw in basketball, not full time), then the sport's own labels and
    commentary callouts (config/sports.yaml)."""
    own = spec.get("listed_words")
    words = [(str(w).lower(), str(k)) for w, k in own] if own else list(WORDS)
    known = {w for w, _ in words}
    for kind, event in (spec.get("events") or {}).items():
        label = str((event or {}).get("label") or "").lower()
        if label and label not in known:
            words.append((label, kind))
            known.add(label)
    for kind, callouts in (spec.get("callouts") or {}).items():
        for word in callouts or []:
            w = str(word).lower()
            if w not in known and len(w) >= 3:
                words.append((w, kind))
                known.add(w)
    return sorted(words, key=lambda x: -len(x[0]))


def _time(line: str, minutes: bool = True) -> tuple[float | None, int | None, int, str]:
    """(a time in the video, or a match minute and its added time, and the
    line without the time). `minutes`: whether the sport has match minutes
    to read at all (basketball's "3 pointer" isn't the 3rd minute)."""
    m = HMS.search(line)
    if m:
        h, mm, ss = (int(x) for x in m.groups())
        if mm < 60 and ss < 60:
            return float(h * 3600 + mm * 60 + ss), None, 0, line[:m.start()] + line[m.end():]
    m = MINUTE.search(line) if minutes else None
    if m:
        minute = int(m.group(1) or m.group(3))
        added = int(m.group(2) or m.group(4) or 0)
        return None, minute, added, line[:m.start()] + line[m.end():]
    m = MS.search(line)
    if m:
        mins, ss = int(m.group(1)), int(m.group(2))
        if ss < 60:
            return float(mins * 60 + ss), None, 0, line[:m.start()] + line[m.end():]
    m = LEADING.match(line) if minutes else None
    if m:
        return None, int(m.group(1)), 0, line[m.end():]
    return None, None, 0, line


def _kind(rest: str, words: list[tuple[str, str]]) -> tuple[str, str]:
    """(the kind of moment the line names, the line without it)."""
    low = rest.lower()
    for word, kind in words:
        m = re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", low)
        if m:
            return kind, rest[:m.start()] + rest[m.end():]
    return "", rest


def parse(text: str, spec: dict) -> tuple[list[Listed], list[str]]:
    """(the events read, the lines that couldn't be read). CSV lines work as
    any other line: their fields are words and times too."""
    words = event_words(spec)
    minutes = bool(spec.get("match_minutes", True))
    events: list[Listed] = []
    unread: list[str] = []
    for raw in str(text or "")[:MAX_TEXT].splitlines():
        line = " ".join(raw.replace(",", " ").replace(";", " ").replace("\t", " ").split())
        if not line:
            continue
        video_t, minute, added, rest = _time(line, minutes)
        kind, rest = _kind(rest, words)
        if (video_t is None and minute is None) or not kind:
            # A header row ("minute, type, team") has no time: skipped quietly.
            if not re.search(r"\d", line) and kind == "" and "," in raw:
                continue
            unread.append(line)
            continue
        who = " ".join(re.sub(r"[!?.:;()\[\]\-–—|]+", " ", rest).split())
        events.append(Listed(kind=kind, who=who[:60], video_t=video_t, minute=minute, added=added, line=line))
    return events, unread


def kickoffs(events: list[Listed]) -> dict[int, float]:
    """Where each half starts in the video, from the list's own kick-off lines."""
    out: dict[int, float] = {}
    for e in events:
        if e.video_t is None:
            continue
        if e.kind == "kickoff" and 1 not in out:
            out[1] = e.video_t
        elif e.kind == "kickoff_second" or (e.kind == "kickoff" and 1 in out and e.video_t > out[1] + 1800):
            out.setdefault(2, e.video_t)
    return out


def place(e: Listed, board, starts: dict[int, float], half_minutes: int = 45) -> tuple[float, float] | None:
    """Where a listed event is in the video: (from, to), the span it can be
    in. A time in the video is exact; a match minute spans that minute
    (18' is the clock's 17:00-17:59). None when a minute can't be placed:
    no clock was read and no kick-off was listed."""
    if e.video_t is not None:
        return e.video_t, e.video_t
    if e.minute is None:
        return None
    second_half = e.minute > half_minutes and not (e.minute == half_minutes and e.added)
    clock_from = (e.minute - 1 + e.added) * 60
    clock_to = clock_from + 59
    if board is not None and getattr(board, "readings", None):
        at = board.video_time_at(clock_from, second_half=second_half)
        if at is not None:
            return at, at + 59
    half = 2 if second_half else 1
    if half in starts:
        offset = (half_minutes * 60) if half == 2 else 0
        return starts[half] + clock_from - offset, starts[half] + clock_to - offset
    return None
