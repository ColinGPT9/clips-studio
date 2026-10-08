"""This plugin's tests. They run on your PC, never inside Clips Kitty:

    python -m pytest -q

They need the SDK's test extra (pip install "clipskitty-sdk[yaml,test]").
clipskitty_sdk.testing runs the plugin the way Clips Kitty would, on 5
moments spread through the SDK's 40-second sample video, whose red banner
shows from 22 to 27 s and where "round one" is said at 8 s. Without FFmpeg
on PATH they are skipped. Change these as you change the plugin.
"""

from pathlib import Path

from clipskitty_sdk import testing

PLUGIN = Path(__file__).resolve().parent.parent  # the folder holding clipskitty.yaml


def _run(tmp_path, **settings):
    video = testing.sample_video(tmp_path)  # skips the test without FFmpeg
    return testing.run_plugin(PLUGIN, video=video, transcript=testing.sample_transcript(), settings=settings,
                              tmp_path=tmp_path)


def test_it_notes_the_banner_and_the_words(tmp_path):
    run = _run(tmp_path)
    assert run.ok, run.error
    assert {m.id: list(m.notes) for m in run.moments} == {
        "m1": ['The commentary says "round one" here'],
        "m2": [],
        "m3": ["The quark burst banner shows from 22.0 s"],
        "m4": [],
        "m5": [],
    }
    assert all(m.score == 60 for m in run.moments)  # it never changes a score


def test_another_colour_is_not_the_banner(tmp_path):
    run = _run(tmp_path, banner_colour="30e03a", cue_words="")
    assert run.ok, run.error
    assert all(m.notes == () for m in run.moments)
