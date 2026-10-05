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
win wins won loss lose lost beat beats defeat defeated victory home away team teams
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


def _named(word: str) -> bool:
    """A word that can be part of a name: capitalised, not a common word, and
    not a short code in capitals ("NBA", "OKC"), which Whisper writes well."""
    if not word or not word[0].isupper() or word.lower() in COMMON:
        return False
    return not (word.isupper() and len(word) <= 4)


def for_video(title: str, description: str = "", teams: str = "") -> str | None:
    """The hint for one job: its title, its description, and the teams the
    job names (Highlights' team choice). None when they name no one."""
    return hint(title, description, teams) or None
