"""This plugin's tests. They run on your PC, never inside Clips Kitty:

    python -m pytest -q

They need the SDK's test extra (pip install "clipskitty-sdk[yaml,test]").
clipskitty_sdk.testing runs the plugin the way Clips Kitty would, in a run
that suggests edits, and reads each clip's suggestion as Clips Kitty keeps
it: fitted to the clip and to the editor's choices. They use what is said in
the SDK's 40-second sample, handed over as 5 clips spread through it, or in
a match of their own, so they need no FFmpeg. Change these as you change the
plugin.
"""

from pathlib import Path

from clipskitty_sdk import testing

PLUGIN = Path(__file__).resolve().parent.parent  # the folder holding clipskitty.yaml
HOOK = 'Adds a hook title where the commentary says "quark burst"'

# What is said in a match of Quarkbloom Arena (a made-up game), in seconds of the video.
MATCH = [
    {"start": 4.0, "end": 6.0, "text": "what a quark burst"},
    {"start": 12.0, "end": 14.0, "text": "now the respawn timer"},
    {"start": 24.0, "end": 26.0, "text": "nice one Quinn", "words": [
        {"start": 24.0, "end": 24.5, "word": "nice"}, {"start": 24.5, "end": 25.0, "word": "one"},
        {"start": 25.0, "end": 26.0, "word": "Quinn"}]},
]


def _on_the_sample(tmp_path, **settings):
    return testing.run_plugin(PLUGIN, transcript=testing.sample_transcript(), duration=testing.SAMPLE_VIDEO_SECONDS,
                              settings=settings, tmp_path=tmp_path)


def test_it_adds_a_hook_title_where_the_commentary_says_quark_burst(tmp_path):
    run = _on_the_sample(tmp_path)
    assert run.ok, run.error
    # "quark burst" is said in m3 (20 to 26.7 s); the other clips get no suggestion.
    assert run.edits == {"m3": {"edit": {"title_overlay": {"text": "Quark burst!", "seconds": 3}}, "reason": HOOK}}


def test_it_mutes_your_words(tmp_path):
    run = _on_the_sample(tmp_path, mute_words="round one")
    assert run.ok, run.error
    # "round one" is said from 8.0 to 9.6 s, in m1 (6.7 to 13.3 s): it is muted with a tenth to spare.
    assert run.edits["m1"] == {"edit": {"mutes": [[7.9, 9.7]]}, "reason": "Mutes the words you listed"}


def test_it_cuts_the_wait_after_the_respawn_timer(tmp_path):
    run = testing.run_plugin(PLUGIN, transcript=MATCH, duration=40, moments=[{"start": 0, "end": 40}],
                             settings={"mute_words": "Quinn"}, tmp_path=tmp_path)
    assert run.ok, run.error
    assert run.edits == {"m1": {
        "edit": {"cuts": [[12.0, 18.0]], "mutes": [[24.9, 26.1]],
                 "title_overlay": {"text": "Quark burst!", "seconds": 3}},
        "reason": ('Mutes the words you listed, cuts the wait after "respawn timer" and adds a hook title where '
                   'the commentary says "quark burst"'),
    }}


def test_a_cut_that_leaves_too_short_a_clip_is_left_out(tmp_path):
    # From 8 to 22 s, the cut would leave 8 s of the clip, under the 10 s shortest clip.
    run = testing.run_plugin(PLUGIN, transcript=MATCH, duration=40, moments=[{"start": 8, "end": 22}],
                             tmp_path=tmp_path)
    assert run.ok, run.error
    assert run.edits == {}
    assert "ignored: m1's cuts: they would leave 8.0 s, under this job's 10 s shortest clip" in run.log
