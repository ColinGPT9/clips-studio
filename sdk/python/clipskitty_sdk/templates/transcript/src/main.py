"""A Clips Kitty pipeline that finds the moments where your words are said.

Made from the transcript template of the Clips Kitty SDK. Each time the
commentary says one of the `words` (a setting, separated by commas), it adds
a moment from `lead_in_seconds` before the words to a few seconds after.
Words said close together share one moment. A word matches as a whole word,
in any case, so "win" doesn't match "window".

It reads only what Clips Kitty wrote down of what is said: it doesn't see the
screen, and it can't tell whether the words are about what is happening.

Clips Kitty runs it on its own Python, which has the Python standard library
and clipskitty_sdk and nothing else, so import only those, at the top of the
file. To try it, run `python -m clipskitty_sdk run . --sample` in the
plugin's folder (`py -m clipskitty_sdk` in PowerShell); it needs no FFmpeg.
"""

from __future__ import annotations

from clipskitty_sdk import run, signals, text

# ---- your game: change these ------------------------------------------------------------
LABEL = "words_said"   # the kind of moment; list it under events in clipskitty.yaml
AFTER_SECONDS = 6.0    # seconds kept after the words are said
# ------------------------------------------------------------------------------------------


def main(job):
    words = text.words_of(job.settings.get("words"))
    if not words:
        job.fail("No words are set to look for: add some to the words setting.")
    lead_in = float(job.settings.get("lead_in_seconds", 10))

    # Each time a word is said, (start, end, word), joined when the moments around them would overlap.
    groups: list[list] = []
    for start, end, word in text.said(job, words):
        if groups and start - groups[-1][1] <= lead_in + AFTER_SECONDS:
            groups[-1][1] = max(groups[-1][1], end)
            if word not in groups[-1][2]:
                groups[-1][2].append(word)
        else:
            groups.append([start, end, [word]])

    for start, end, said in groups:
        first, last = signals.around(job, start, end, lead=lead_in, tail=AFTER_SECONDS)
        job.add_range(first, last, label=LABEL,
                      reason="the commentary says " + ", ".join(f'"{word}"' for word in said))
    job.finish(notes=f"The words are said in {len(groups)} place(s).")


if __name__ == "__main__":
    run(main)
