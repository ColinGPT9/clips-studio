"""A Clips Kitty plugin that rates the moments others found, by the words said in them.

Made from the rater template of the Clips Kitty SDK. It runs after the
moments are found (by Clips Kitty, or a pipeline), when it is chosen under
Rate & understand, and is handed those moments:

- a moment where one of the `good_words` is said gains `bonus` points (never
  above 100);
- otherwise, a moment where one of the `dull_words` is said drops to
  `dull_score` (never rising);
- any other moment gets no answer, so it keeps its score.

Scores decide which clips are made and their order. It reads only what is
said: it doesn't see the screen, and it can't tell whether the words are
about the moment.

Clips Kitty runs it on its own Python, which has the Python standard library
and clipskitty_sdk and nothing else, so import only those, at the top of the
file. To try it, run `python -m clipskitty_sdk run . --sample` in the
plugin's folder (`py -m clipskitty_sdk` in PowerShell); it needs no FFmpeg.
"""

from __future__ import annotations

from clipskitty_sdk import run, text

# ---- your game: the reasons it gives -----------------------------------------------------
GOOD = "the commentary says {words}"
DULL = "the commentary says {words}, which this rater marks as dull"
# ------------------------------------------------------------------------------------------


def quoted(words) -> str:
    return ", ".join(f'"{word}"' for word in words)


def main(job):
    good_words = text.words_of(job.settings.get("good_words"))
    dull_words = text.words_of(job.settings.get("dull_words"))
    if not good_words and not dull_words:
        job.fail("No words are set: add some to good_words or dull_words.")
    bonus = float(job.settings.get("bonus", 15))
    dull_score = float(job.settings.get("dull_score", 10))

    rated = 0
    for m in job.moments:
        score = m.score or 0.0  # its score now, after any plugin that rated it before this one
        good, dull = text.hits(job, m, good_words), text.hits(job, m, dull_words)
        if good:
            job.rate(m, min(100.0, score + bonus), reason=GOOD.format(words=quoted(good)))
        elif dull:
            job.rate(m, min(score, dull_score), reason=DULL.format(words=quoted(dull)))
        else:
            continue
        rated += 1
    job.finish(notes=f"Rated {rated} of {len(job.moments)} moment(s) by the words said in them.")


if __name__ == "__main__":
    run(main)
