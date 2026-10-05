"""The names Whisper listens for in a basketball game: the players and teams
the video's own title and description spell, and the teams the job names.

On an NBA game the captions spelled Wembanyama five wrong ways ("weapon
Yama", "Wimbanyama", "Bunyama"...), Champagnie "Champagne" and
Gilgeous-Alexander "Davis Alexander", and every one of them was spelled
right in the video's description. Whisper is told the names before it
listens, so a name it hears comes out spelled as the uploader spells it.
It still writes only what it hears: a name nobody says never appears, and
no one is named from who is on screen."""

import re

# A word as names are written: letters, with an apostrophe or a hyphen inside
# ("O'Neale", "Gilgeous-Alexander", "Dončić").
_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
# Capitalised words that are no one's name: sentence starts, a highlights
# video's own words and a channel's links and sign-offs.
COMMON = set("""
a an and are as at be but by for from has have he her his i if in into is it its of on or our she so that the
their them they this to vs versus was we were when who will with you your
game games highlights highlight full extended recap watch subscribe follow like share comment click visit get
join download stream streaming live official channel video videos app pass league season playoffs playoff
postseason finals final conference western eastern round series regular preseason quarter half overtime
points rebounds assists steals blocks pts reb ast stl blk record career night tonight today best top plays play
moments news stories more go see check out here there now new all every one two three four five six seven
win wins won loss lose lost beat beats defeat defeated victory home away team teams never miss moment don't
january february march april may june july august september october november december
monday tuesday wednesday thursday friday saturday sunday
""".split())
MOST = 40           # words at most: Whisper's prompt holds about 220 tokens, and names come first


def hint(*texts: str) -> str:
    """The names in `texts` (title first), as Whisper's hint: each run of
    capitalised words once ("Victor Wembanyama, Shai Gilgeous-Alexander"),
    a shouted word as a name ("SPURS" as "Spurs"), without the words every
    video and channel uses. "" when they name no one."""
    phrases: list[str] = []
    seen: set[str] = set()
    words = 0
    for text in texts:
        text, run, prev_end = text or "", [], 0
        for m in [*_WORD.finditer(text), None]:
            word = m.group(0) if m is not None else ""
            # A run is capitalised words with only spaces between them.
            if run and (m is None or text[prev_end:m.start()].strip() or not _named(word)):
                phrase = " ".join(run)
                if phrase.lower() not in seen and words < MOST:
                    seen.add(phrase.lower())
                    phrases.append(phrase)
                    words += len(run)
                run = []
            if m is not None and _named(word):
                run.append(word.title() if word.isupper() else word)
                prev_end = m.end()
    return ", ".join(phrases)


# An abbreviation run into a word: a link's or an app's name ("NBAApp-YTDes",
# from "...on the NBA App: https://app.link/NBAApp-YTDes"), never a person's.
_GLUED = re.compile(r"[A-Z]{3,}[a-z]")


def _named(word: str) -> bool:
    """A word that can be part of a name: capitalised, not a common word, not
    a short code in capitals ("NBA", "OKC"), which Whisper writes well, and
    not an abbreviation run into a word ("NBAApp")."""
    if not word or not word[0].isupper() or word.lower() in COMMON or _GLUED.search(word):
        return False
    return not (word.isupper() and len(word) <= 4)


# The result as the NBA's own descriptions write it: "...and the San Antonio
# Spurs defeated Shai Gilgeous-Alexander (31 PTS) and the Oklahoma City
# Thunder, 111-103, in Game 7". The winner, the loser and the score.
_TEAM = r"((?:[A-Z0-9][\w.'’&-]*\s+){0,3}[A-Z0-9][\w.'’&-]*)"
RESULT = re.compile(r"\b[Tt]he\s+" + _TEAM + r"\s+(?:defeated|beat|topped|edged|outlasted|downed|held\s+off)\b"
                    r"[^.]*?\b[Tt]he\s+" + _TEAM + r",?\s+(\d{2,3})\s*[-–]\s*(\d{2,3})\b")


def result(description: str) -> tuple[str, str, int, int] | None:
    """(winner, loser, the winner's points, the loser's) as the video's
    description says them, or None when it doesn't say."""
    m = RESULT.search(description or "")
    if m is None:
        return None
    won, lost = int(m.group(3)), int(m.group(4))
    if won <= lost:
        return None
    return " ".join(m.group(1).split()), " ".join(m.group(2).split()), won, lost


def sides(title: str, description: str, final: tuple | None) -> tuple[str, str] | None:
    """Which team is which on the score bug, in its order, when its own
    letters weren't read (a logo, letters on their side): the description
    says who won and by what score, and the bug's last score says which side
    has the winner's points. Each team as the title names it ("Spurs") when
    it does, else as the description does. None when either doesn't say, or
    they disagree: a side is never guessed."""
    said = result(description)
    if said is None or not final or len(final) != 2:
        return None
    winner, loser, won, lost = said
    if tuple(final) == (won, lost):
        pair = (winner, loser)
    elif tuple(final) == (lost, won):
        pair = (loser, winner)
    else:
        return None
    short = [p for p in hint(title).split(", ") if p]

    def named(team: str) -> str:
        return next((p for p in short if team.lower().endswith(p.lower())), team)

    return named(pair[0]), named(pair[1])


def for_video(title: str, description: str = "", teams: str = "") -> str | None:
    """The hint for one job: its title, its description, and the teams the
    job names (Highlights' team choice). None when they name no one."""
    return hint(title, description, teams) or None
