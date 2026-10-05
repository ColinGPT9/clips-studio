"""What the commentary says about a basket: who scored it.

Only the commentary's own words name a scorer, read where the basket went
in: "Fox the three", "Champagnie knocks down the three", "Wallace attacking
to the basket, laid it in". On an NBA game the titles, left to read the
commentary themselves, credited the passer ("Parker's Rookie Spin!" for
Champagnie's three), the screener and a shot-blocker. The scorer is the
player named last before the words that say the ball went in ("hit for
Johnson": the one named after them). No one is named when a pass nobody
is named for comes between ("kicks it out, bang"), when two players are,
or when the name is one Whisper heard only once and the video's own
description doesn't spell ("Fussell", for the last dunk): no name rather
than a wrong one, and never from who is on screen."""

import re
from collections import Counter
from dataclasses import dataclass

from sports.basketball.names import COMMON

# Capitalised words that are no player's name: a commentator's exclamations
# and the game's own words.
NOT_NAMES = COMMON | set("""
oh wow yes yeah no not and but what how look again first second third fourth last next half court free throw
throws shot shots clock time timeout bang boom big huge man good great nice all-star rookie
""".split())
_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*|\d+(?:[.:]\d+)?")
_POSSESSIVE = re.compile(r"['’][sS]?$")

# Words that say the ball went in, by the points they fit (None: any basket).
SCORED = (
    # ("a three on two" is a fast break, three players on two)
    (3, re.compile(r"\b(?:(?:the|a|that|another|his|her|for|from|with|corner|deep|step[- ]?back|pull[- ]?up) "
                   r"three\b(?![- ](?:point|seconds?|minutes?|fouls?|straight|times|of)\b)"
                   r"(?![- ]on[- ](?:one|two|three|1|2|3)\b)"
                   r"|three[- ]?pointers?\b|triples?\b|treys?\b|downtown\b"
                   r"|from (?:deep|way downtown|the logo|beyond the arc)\b)")),
    (2, re.compile(r"\b(?:dunks?|dunked|slams?|slammed|throws? it down|threw it down|jams?|jammed|flush(?:es|ed)?"
                   r"|lays? it (?:in|up)|laid it (?:in|up)|lay[- ]?ups?|finger roll|floater|puts? it back"
                   r"|tips? it in|tipped in|finish(?:es|ed)?|alley[- ]oop)\b(?!['’-])")),
    # Not "score" (the score is tied) or "can" (he can shoot), and never the
    # start of a longer word ("can't", "score's"): on an NBA game "Holmgren
    # can't get the board" gave a basket to the player who missed the rebound.
    (None, re.compile(r"\b(?:scores|scored|knocks? (?:it )?down|knocked (?:it )?down|drills?|drilled|buries|buried"
                      r"|nails?|nailed|drains?|drained|sinks?|sank|cans|canned|splash(?:es)?|banks? it in"
                      r"|hits? (?:the|a|that|it|another|his|her)\b|gets? (?:the )?(?:bucket|basket)\b|bang\b"
                      r"|bingo\b|got it\b|count it\b|there it is\b"
                      r"|(?:hit|bucket|basket|hoop)(?= (?:for|by) ))(?![\w'’-])")),     # "a deep hit for Ruiz"
)
# A pass that hands the ball on to someone the commentary doesn't name:
# whoever was named before it didn't score.
PASSED = re.compile(r"\b(?:kicks? (?:it )?out|kicked (?:it )?out|dish(?:es|ed)?|swings? it|swung it"
                    r"|pitch(?:es|ed)? (?:it )?(?:out|ahead|back)|lobs?|lobbed|feeds?|drops? it off"
                    r"|hands? it off|outlet|finds|found)\b")
NEAR = 16           # the scorer is named at most this many words before the words that say it went in
BEFORE = 9.0        # a basket's words: from this long before it...
AFTER = 6.0         # ...to this long after (the call "hit for Johnson" comes after the ball)


@dataclass
class Word:
    text: str       # as written, without punctuation ("Wembanyama's", "laid")
    t: float        # when it was said (its middle)
    start: bool     # it starts a sentence, or a segment: a capital there says nothing
    pause: bool = False     # a comma or a stop after it: the next word starts another name


def words(segments, lo: float = float("-inf"), hi: float = float("inf")) -> list[Word]:
    """The words said between lo and hi, in order. A segment without word
    timings has its words spread evenly over it."""
    out: list[Word] = []
    for s in segments or []:
        if s.end < lo or s.start > hi:
            continue
        items = s.words or _spread(s)
        first = True
        for w in items:
            raw = str(w.get("word", ""))
            t = (float(w.get("start", s.start)) + float(w.get("end", s.end))) / 2
            tokens = _WORD.findall(raw)
            for k, token in enumerate(tokens):
                if lo <= t <= hi:
                    out.append(Word(token, t, first, k == len(tokens) - 1 and bool(re.search(r"[,.;:!?]\W*$", raw))))
                first = False
            if re.search(r"[.!?]\W*$", raw):
                first = True
    return out


def _spread(s) -> list[dict]:
    parts = str(s.text or "").split()
    step = (s.end - s.start) / max(1, len(parts))
    return [{"word": p, "start": s.start + i * step, "end": s.start + (i + 1) * step} for i, p in enumerate(parts)]


def bare(word: str) -> str:
    """A word without its possessive ("Wembanyama's" -> "Wembanyama")."""
    return _POSSESSIVE.sub("", word) if len(word) > 2 else word


class Names:
    """The players the commentary names, as Whisper wrote them, and the ones
    the video's own title and description spell (names.hint). Whisper writes
    a name with a capital wherever it says it, and other words only where a
    sentence starts: a word written with a capital in mid-sentence more often
    than without one is a name ("Castle", "Fox"), the teams aside."""

    def __init__(self, segments, known: str = "", teams=()):
        self.upper: Counter = Counter()
        self.lower: Counter = Counter()
        for w in words(segments):
            word = bare(w.text)
            if not word[:1].isalpha():
                continue
            if word[0].isupper():
                if not w.start:
                    self.upper[word.lower()] += 1
            else:
                self.lower[word.lower()] += 1
        self.teams = {p.lower() for team in teams for p in _WORD.findall(str(team or ""))}
        self.known: dict[str, str] = {}           # lower case -> as the description spells it
        for phrase in str(known or "").split(","):
            parts = _WORD.findall(phrase)
            if parts and not any(p.lower() in self.teams for p in parts):
                for p in parts:
                    self.known[p.lower()] = p

    def is_name(self, word: str) -> bool:
        """Whether `word` names a player (in any case: a title's "PARKER'S" too)."""
        word = bare(word)
        low = word.lower()
        if not word[:1].isalpha() or low in NOT_NAMES or low in self.teams:
            return False
        if low in self.known:
            return True
        if word.isupper() and len(word) <= 4:          # a team's letters ("OKC"), not a player
            return False
        return self.upper[low] > self.lower[low]

    def sure(self, word: str) -> bool:
        """A name to print: spelled by the video's own description, or said
        at least twice. "Fussell" (Vassell, heard once) is not."""
        low = bare(word).lower()
        return low in self.known or self.upper[low] >= 2

    def spelled(self, word: str) -> str:
        """`word` as the description spells it, else as Whisper wrote it."""
        word = bare(word)
        return self.known.get(word.lower(), word)


def scorer(segments, t: float, points: int, names: Names, lo: float | None = None,
           hi: float | None = None) -> str:
    """Who scored the basket at `t` (worth `points`), as the commentary says
    it ("Champagnie", "Keldon Johnson"), or "" when it doesn't say, or says
    two. Its words are read from BEFORE before it to AFTER after it, within
    lo and hi (the baskets either side)."""
    a = t - BEFORE if lo is None else max(t - BEFORE, lo)
    b = t + AFTER if hi is None else min(t + AFTER, hi)
    said = words(segments, a, b)
    if not said:
        return ""
    text, at = "", []                              # lower case, and each character's word
    for i, w in enumerate(said):
        if text:
            text += " "
            at.append(i)
        text += w.text.lower()
        at += [i] * len(w.text)
    named = [i for i, w in enumerate(said) if w.text[:1].isupper() and names.is_name(w.text)]
    found: list[tuple[int, bool, str]] = []        # (how well its words fit, a name to print, the name)
    for fits, pattern in SCORED:
        if fits is not None and fits != points:
            continue
        for m in pattern.finditer(text):
            first, last = at[m.start()], at[m.end() - 1]
            who = _named_after(said, named, last) or _named_before(said, named, first, text, at)
            if who is not None:
                run = _run(said, named, who)
                sure = [i for i in run if names.sure(said[i].text)]
                name = " ".join(names.spelled(said[i].text) for i in run[:run.index(sure[-1]) + 1]) if sure else ""
                found.append((2 if fits else 1, bool(sure), name))
    if not found:
        return ""
    best = max(level for level, _sure, _name in found)
    sure = {name for level, ok, name in found if level == best and ok}
    return sure.pop() if len(sure) == 1 else ""


def _named_after(said: list[Word], named: list[int], last: int) -> int | None:
    """ "hit for Johnson", "the three by Fox": the player named just after."""
    if last + 2 < len(said) and said[last + 1].text.lower() in ("for", "by") and last + 2 in named:
        return last + 2
    return None


def _named_before(said: list[Word], named: list[int], first: int, text: str, at: list[int]) -> int | None:
    """The player named last before word `first`, at most NEAR words before,
    unless a pass to someone unnamed comes between."""
    before = [i for i in named if i < first and first - i <= NEAR]
    if not before:
        return None
    who = before[-1]
    between = " ".join(w.text.lower() for w in said[who + 1:first])
    return None if PASSED.search(between) else who


def _run(said: list[Word], named: list[int], i: int) -> list[int]:
    """The name word `i` ends, with the first names before it ("Keldon Johnson"),
    never across a comma ("to Vance, Vance lets it fly")."""
    run = [i]
    while (run[0] - 1 in named and not said[run[0] - 1].pause
           and said[run[0] - 1].text.lower() != said[run[0]].text.lower()):
        run.insert(0, run[0] - 1)
    return run
