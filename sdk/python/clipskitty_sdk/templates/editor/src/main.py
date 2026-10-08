"""A Clips Kitty plugin that suggests edits for the clips Clips Kitty makes, by
the words said in them.

Made from the editor template of the Clips Kitty SDK, set up for Quarkbloom
Arena (a made-up game). It runs after the clips are chosen, when it is
chosen under Suggest edits, and is handed those clips. For each one it
suggests:

- a mute over each of the `mute_words` said in it, a little wider than the
  word, so the whole word is silent and its caption is hidden too;
- a cut of the wait after WAIT_WORDS, from when they are said to
  WAIT_SECONDS after, when all of that is inside the clip and the cuts
  still leave the job's shortest clip;
- HOOK_TITLE as the clip's hook title, when HOOK_WORDS are said in it;

with a reason the creator sees. A clip with none of these gets no
suggestion.

A suggestion waits for the creator in the timeline editor: Clips Kitty
doesn't put it into a clip until the creator uses it there and applies their
edits. Times are seconds of the video, as text.said() gives them. Clips Kitty
fits each suggestion to its clip as it reads the answer: for example, cuts
that would leave the clip shorter than the job's shortest clip are left out,
and the job's log says so. This plugin measures that first, with
kept_length(), so its reason never names a cut Clips Kitty leaves out.

Clips Kitty runs it on its own Python, which has the Python standard library
and clipskitty_sdk and nothing else, so import only those, at the top of the
file. To try it, run `python -m clipskitty_sdk run . --sample` in the
plugin's folder (`py -m clipskitty_sdk` in PowerShell); it needs no FFmpeg.
"""

from __future__ import annotations

from clipskitty_sdk import run
from clipskitty_sdk.contract import MAX_EDIT_SPANS, kept_length  # MAX_EDIT_SPANS: cuts, and mutes, per clip
from clipskitty_sdk.text import said, words_of

# ---- your game: change these ------------------------------------------------------------
WAIT_WORDS = ["respawn timer"]  # said as a wait starts
WAIT_SECONDS = 4                # how long the wait goes on after they are said
HOOK_WORDS = ["quark burst"]    # said at a big play
HOOK_TITLE = "Quark burst!"     # the hook title for a clip where they are said
HOOK_SECONDS = 3                # how long it shows: Clips Kitty uses the nearest of 2, 3, 5 or 8
MUTE_MARGIN = 0.1               # seconds muted before and after each word
# ------------------------------------------------------------------------------------------


def quoted(words) -> str:
    return ", ".join(f'"{word}"' for word in words)


def sentence(parts) -> str:
    """The parts as one sentence: "Mutes a, cuts b and adds c"."""
    text = ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]
    return text[:1].upper() + text[1:]


def main(job):
    muted = said(job, words_of(job.settings.get("mute_words")))  # (start, end, word), in seconds of the video
    waits = said(job, WAIT_WORDS)
    hooks = said(job, HOOK_WORDS)
    # Clips Kitty leaves out cuts that would make a clip shorter than this.
    shortest = max(1.0, job.limits.min_duration or 0)

    suggested = 0
    for m in job.moments:  # the clips Clips Kitty will make
        s = job.suggest_edit(m)
        why = []
        for start, end, _ in muted:
            if m.start <= start < m.end and len(s.mutes) < MAX_EDIT_SPANS:
                s.mute(max(0.0, start - MUTE_MARGIN), end + MUTE_MARGIN)
        if s.mutes:
            why.append("mutes the words you listed")
        for start, end, _ in waits:
            cut = (start, end + WAIT_SECONDS)
            if (m.start < start and cut[1] < m.end and len(s.cuts) < MAX_EDIT_SPANS
                    and kept_length(m.start, m.end, [*s.cuts, cut]) >= shortest):
                s.cut(*cut)
        if s.cuts:
            why.append(f"cuts the wait after {quoted(WAIT_WORDS)}")
        if any(m.start <= start < m.end for start, _, _ in hooks):
            s.title_overlay(HOOK_TITLE, seconds=HOOK_SECONDS)
            why.append(f"adds a hook title where the commentary says {quoted(HOOK_WORDS)}")
        if why:
            s.reason(sentence(why))
            suggested += 1
    job.finish(notes=f"Suggested edits for {suggested} of {len(job.moments)} clip(s).")


if __name__ == "__main__":
    run(main)
