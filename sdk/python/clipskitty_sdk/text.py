# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Words in what is said: a words setting, matching whole words, and when in
the video a word is said.

    from clipskitty_sdk import text

    def main(job):
        words = text.words_of(job.settings.get("words"))       # "quark burst, triple bloom"
        for start, end, word in text.said(job, words):
            job.add_range(max(0, start - 4), end + 6, label="words_said", reason=f'the commentary says "{word}"')

A word or phrase matches as a whole word, in any case and with any spacing,
so "win" doesn't match "window" and "Quark  Burst" matches "quark burst".
said() and hits() read the transcript, so the plugin needs `transcript` in
its inputs and `transcript.read` in its permissions; the rest needs nothing.
Standard library only.
"""

from __future__ import annotations

import math
import re
import unicodedata
import weakref

from .contract import ContractError

# The transcript's segments, read once for each job.
_SEGMENTS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_EDGES = re.compile(r"^\W+|\W+$")


def normalise(text) -> str:
    """`text` for comparing: Unicode's compatibility form (NFKC), lower case
    (casefold, so "STRASSE" and "straße" agree), one space between words and
    none at either end."""
    return " ".join(unicodedata.normalize("NFKC", "" if text is None else str(text)).casefold().split())


def words_of(setting) -> list[str]:
    """The words or phrases in a setting such as "quark burst, triple bloom":
    split on commas, spaces tidied, each kept once (in any case), in order."""
    words: list[str] = []
    seen: set[str] = set()
    for part in str(setting or "").split(","):
        word = " ".join(part.split())
        if word and normalise(word) not in seen:
            seen.add(normalise(word))
            words.append(word)
    return words


def _words(words) -> list[str]:
    return words_of(words) if isinstance(words, str) else [w for w in (" ".join(str(w).split()) for w in words) if w]


def _pattern(word: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, normalise(word).split())) + r"(?!\w)")


def find_words(text, words) -> list[str]:
    """Which of `words` (a list, or a setting such as "quark burst, triple
    bloom") are said in `text`, as whole words in any case or spacing, in
    the order of `words`."""
    said = normalise(text)
    return [word for word in _words(words) if _pattern(word).search(said)]


def _segments(job) -> list[tuple[float, float, str, list]]:
    """(start, end, text, words) for each segment of the job's transcript that has a time and text."""
    if job.transcript is None:
        raise ContractError("text", [("this job has no transcript: add transcript to inputs and "
                                      "transcript.read to permissions")])
    try:
        return _SEGMENTS[job]
    except (KeyError, TypeError):
        pass
    out = []
    for s in job.transcript.segments():
        if not isinstance(s, dict):
            continue
        start, end, said = _seconds(s.get("start")), _seconds(s.get("end")), str(s.get("text") or "").strip()
        if start is None or end is None or not said:
            continue
        timed = []
        for w in s.get("words") or []:
            if isinstance(w, dict):
                ws, we = _seconds(w.get("start")), _seconds(w.get("end"))
                token = _EDGES.sub("", normalise(w.get("word")))
                if ws is not None and we is not None and token:
                    timed.append((ws, we, token))
        out.append((start, end, said, timed))
    try:
        _SEGMENTS[job] = out
    except TypeError:
        pass  # a job that can't be weakly referenced isn't cached; it is read again next time
    return out


def _seconds(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        return None
    return float(value)


def said(job, words) -> list[tuple[float, float, str]]:
    """Each time one of `words` is said in the job's transcript: (start,
    end, word), in time order. The times are the word's own when the
    transcript gives each word a time (Clips Kitty's usually do),
    else the segment's that says it."""
    wanted = [(word, _pattern(word), [_EDGES.sub("", t) for t in normalise(word).split()]) for word in _words(words)]
    out = []
    for start, end, text, timed in _segments(job):
        spoken = normalise(text)
        tokens = [t for _, _, t in timed]
        for word, pattern, parts in wanted:
            if not pattern.search(spoken):
                continue
            n = len(parts)
            exact = [(timed[i][0], timed[i + n - 1][1], word) for i in range(len(tokens) - n + 1)
                     if tokens[i:i + n] == parts] if parts and all(parts) else []
            out.extend(exact or [(start, end, word)])
    return sorted(out, key=lambda hit: (hit[0], hit[1]))


def hits(job, moment, words) -> list[str]:
    """Which of `words` are said during `moment` (a Moment, or (start,
    end)), in the order of `words`: a time from said() that overlaps it."""
    start, end = (moment.start, moment.end) if hasattr(moment, "start") else (float(moment[0]), float(moment[1]))
    during = {word for s, e, word in said(job, words) if s < end and e > start}
    return [word for word in _words(words) if word in during]


__all__ = ["find_words", "hits", "normalise", "said", "words_of"]
