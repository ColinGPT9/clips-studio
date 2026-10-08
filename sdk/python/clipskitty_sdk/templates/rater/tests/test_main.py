"""This plugin's tests. They run on your PC, never inside Clips Kitty:

    python -m pytest -q

They need the SDK's test extra (pip install "clipskitty-sdk[yaml,test]").
clipskitty_sdk.testing runs the plugin the way Clips Kitty would, here on 5
moments spread through the SDK's 40-second sample, each scored 60, and on
what is said in it, so they need no FFmpeg. Change these as you change the
plugin.
"""

from pathlib import Path

from clipskitty_sdk import testing

PLUGIN = Path(__file__).resolve().parent.parent  # the folder holding clipskitty.yaml


def _run(tmp_path, **settings):
    return testing.run_plugin(PLUGIN, transcript=testing.sample_transcript(), duration=testing.SAMPLE_VIDEO_SECONDS,
                              settings=settings, tmp_path=tmp_path)


def test_it_rates_moments_by_the_words_said(tmp_path):
    run = _run(tmp_path)
    assert run.ok, run.error
    # "quark burst" is said in m3 (20 to 26.7 s) and "waiting" in m5 (33.3 to 40 s).
    assert {m.id: m.score for m in run.moments} == {"m1": 60, "m2": 60, "m3": 75, "m4": 60, "m5": 10}
    assert {key: answer["reason"] for key, answer in run.answers.items()} == {
        "m3": 'the commentary says "quark burst"',
        "m5": 'the commentary says "waiting", which this rater marks as dull',
    }


def test_without_words_it_says_so(tmp_path):
    run = _run(tmp_path, good_words="", dull_words="")
    assert not run.ok
    assert run.error == "No words are set: add some to good_words or dull_words."
