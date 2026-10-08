"""A Clips Kitty plugin that notes what happens in the moments others found,
from the screen and the words said.

Made from the understander template of the Clips Kitty SDK, set up for
Quarkbloom Arena (a made-up game), whose red "quark burst" banner shows at
the top of the screen. It runs after the moments are found, when it is
chosen under Rate & understand, and is handed those moments. For each one it
adds a note when:

- the banner (banner_region, banner_colour) starts showing during it;
- the commentary says one of the `cue_words` during it.

Clips Kitty gives the notes to the AI that writes each clip's title,
description and hashtags. It doesn't change any moment's score. It sees only
that part of the screen and what is said.

Clips Kitty runs it on its own Python, which has the Python standard library
and clipskitty_sdk and nothing else, so import only those, at the top of the
file. To try it, run `python -m clipskitty_sdk run . --sample` in the
plugin's folder (`py -m clipskitty_sdk` in PowerShell); it needs FFmpeg.
"""

from __future__ import annotations

import re

from clipskitty_sdk import media, run, signals, text
from clipskitty_sdk.contract import MAX_CONTEXT_ITEMS  # notes for one moment in one run

# ---- your game: change these ------------------------------------------------------------
BANNER_NOTE = "The quark burst banner shows from {start:.1f} s"
WORDS_NOTE = 'The commentary says "{word}" here'
SHARE = 0.5               # how much of the banner's part of the screen must be its colour (0 to 1)
FRAMES_PER_SECOND = 4     # how often to look
# ------------------------------------------------------------------------------------------


def banner_shows(job, region, colour) -> list[tuple[float, float]]:
    """(first, last) second of each stretch the banner shows, in the whole video."""
    seen = [frame.t for frame in media.frames(job, fps=FRAMES_PER_SECOND, region=region)
            if signals.colour_share(frame, colour) >= SHARE]
    return signals.stretches(seen)


def main(job):
    try:
        region = media.Region.parse(job.settings.get("banner_region", "0.30,0.10,0.40,0.10"))
    except ValueError as e:
        job.fail(f"The banner_region setting isn't a part of the screen: {e}")
    colour = str(job.settings.get("banner_colour", "e0303a")).strip()
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", colour):
        job.fail(f'The banner_colour setting isn\'t a colour: write 6 hex digits, such as "e0303a", not "{colour}"')
    cue_words = text.words_of(job.settings.get("cue_words"))

    job.progress(0.1, "Looking for the banner")
    shows = banner_shows(job, region, colour)
    job.progress(0.8, "Reading what is said")
    noted = 0
    for m in job.moments:
        notes = [WORDS_NOTE.format(word=word) for word in text.hits(job, m, cue_words)]
        notes += [BANNER_NOTE.format(start=start) for start, _ in shows if m.start <= start < m.end]
        for note in notes[:MAX_CONTEXT_ITEMS]:
            job.understand(m, note)
        noted += bool(notes)
    job.finish(notes=f"Noted what happens in {noted} of {len(job.moments)} moment(s).")


if __name__ == "__main__":
    run(main)
