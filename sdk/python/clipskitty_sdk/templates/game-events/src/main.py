"""A Clips Kitty pipeline that finds the moments when a coloured banner shows
on screen and the sound gets louder.

Made from the game-events template of the Clips Kitty SDK, set up for
Quarkbloom Arena (a made-up game), whose red "quark burst" banner shows at
the top of the screen. Change the settings in clipskitty.yaml and the marked
block below for your game.

1. It looks at the banner's part of the screen (banner_region) 4 times a
   second, shrunk to a few pixels, and keeps the times when most of it is the
   banner's colour (banner_colour).
2. Those times join into the stretches the banner shows.
3. With needs_loud, a stretch counts only when the sound is at least
   louder_by_db louder than the half minute around it at the same time
   (from the second before the banner shows to the second after it goes).
4. Each becomes a moment from 6 seconds before the banner shows to 3 seconds
   after it goes, fitted to the clip lengths the creator chose.

It sees only that part of the screen and how loud the video is: it can't
read the banner's words, anything else of that colour there fools it, and a
moment without the banner is missed.

Clips Kitty runs it on its own Python, which has the Python standard library
and clipskitty_sdk and nothing else, so import only those, at the top of the
file. To try it, run `python -m clipskitty_sdk run . --sample` in the
plugin's folder (`py -m clipskitty_sdk` in PowerShell); it needs FFmpeg.
"""

from __future__ import annotations

import re
import statistics

from clipskitty_sdk import media, run, signals

# ---- your game: change these ------------------------------------------------------------
LABEL = "quark_burst"     # the kind of moment; list it under events in clipskitty.yaml
SHARE = 0.5               # how much of the banner's part of the screen must be its colour (0 to 1)
FRAMES_PER_SECOND = 4     # how often to look
LEAD_SECONDS = 6.0        # seconds kept before the banner shows
TAIL_SECONDS = 3.0        # seconds kept after it goes
# ------------------------------------------------------------------------------------------


def banner_shows(job, region, colour) -> list[tuple[float, float]]:
    """(first, last) second of each stretch the banner shows."""
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
    louder_by = float(job.settings.get("louder_by_db", 6))
    needs_loud = bool(job.settings.get("needs_loud", True))

    job.progress(0.1, "Looking for the banner")
    shows = banner_shows(job, region, colour)
    job.progress(0.7, "Listening for the sound getting louder")
    loudness = media.loudness(job) if shows else []  # each second's loudness
    loud = set(signals.spikes(loudness, louder_by=louder_by))  # the seconds that are louder than around them
    usual = statistics.median(loudness) if loudness else 0.0

    found = 0
    for start, end in shows:
        # The loud seconds from the second before the banner shows to the second after it goes.
        heard = [second for second in range(int(start) - 1, int(end) + 2) if second in loud]
        if needs_loud and not heard:
            continue
        reason = f"the banner shows from {start:g} to {end:g} s"
        if heard:
            reason += f", and the sound gets {max(loudness[s] for s in heard) - usual:.0f} dB louder"
        first, last = signals.around(job, start, end, lead=LEAD_SECONDS, tail=TAIL_SECONDS)
        job.add_range(first, last, label=LABEL, reason=reason)
        found += 1

    if needs_loud:
        notes = f"The banner shows {len(shows)} time(s), {found} of them as the sound gets louder."
    else:
        notes = f"The banner shows {len(shows)} time(s)."
    job.finish(notes=notes)


if __name__ == "__main__":
    run(main)
