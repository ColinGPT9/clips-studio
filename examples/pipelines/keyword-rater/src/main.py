"""Keyword rater: an example Clips Kitty plugin that rates and understands moments.

It runs after the moments are found (by Clips Kitty, Sports, Gaming scoring
or a pipeline), when it is chosen under Rate & understand. What it does, and
nothing more:

1. Reads the `words` setting: words or phrases, separated by commas.
2. For each moment it is handed, reads what is said during it.
3. When one of the words is said and the run is asked to rate, it adds the
   `bonus` setting to the moment's score (never above 100).
4. When the run is asked to understand, it adds a note for each word said,
   up to 5 for one moment: The commentary says "{word}" here.

A moment where none of the words is said gets no answer, so it keeps its
score. A word matches as a whole word in any case, so "win" doesn't match
"window".

It only knows what is said. It doesn't know what happens on screen, or
whether a word was said about this moment at all. That is the honest limit of
matching words, and it is why this is an example.

It imports only the Python standard library and the Clips Kitty SDK.
"""

from __future__ import annotations

import re

from clipskitty_sdk import run
from clipskitty_sdk.contract import MAX_CONTEXT_ITEMS  # notes for one moment in one run


def words_of(setting) -> list[str]:
    """The words from the setting: split on commas, trimmed, each kept once."""
    words: list[str] = []
    for word in str(setting or "").split(","):
        word = " ".join(word.split())
        if word and word.lower() not in (w.lower() for w in words):
            words.append(word)
    return words


def pattern_for(word: str) -> re.Pattern:
    """`word` as a whole word or phrase, in any case and with any spacing."""
    return re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, word.split())) + r"(?!\w)", re.IGNORECASE)


def words_said(text: str, patterns: list[tuple[str, re.Pattern]]) -> list[str]:
    """The words said in `text`, in the setting's order."""
    return [word for word, pattern in patterns if pattern.search(text)]


def main(job):
    words = words_of(job.settings.get("words"))
    if not words:
        job.fail("No words are set to listen for: add some to the words setting.")
    bonus = float(job.settings.get("bonus", 15))
    patterns = [(word, pattern_for(word)) for word in words]
    said_in = 0
    for m in job.moments:
        said = words_said(job.text(m), patterns)
        if not said:
            continue
        said_in += 1
        if job.wants("rate"):
            job.rate(m, min(100.0, m.score + bonus),
                     reason="the commentary says " + ", ".join(f'"{word}"' for word in said))
        if job.wants("understand"):
            for word in said[:MAX_CONTEXT_ITEMS]:
                job.understand(m, f'The commentary says "{word}" here')
    job.finish(notes=f"One of the words is said in {said_in} of {len(job.moments)} moment(s).")


if __name__ == "__main__":
    run(main)
