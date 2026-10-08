"""This plugin's tests. They run on your PC, never inside Clips Kitty:

    python -m pytest -q

They need the SDK's test extra (pip install "clipskitty-sdk[yaml,test]").
clipskitty_sdk.testing runs the plugin the way Clips Kitty would, here on the
transcript of the SDK's sample video, so they need no FFmpeg. Change these as
you change the plugin.
"""

from pathlib import Path

from clipskitty_sdk import testing

PLUGIN = Path(__file__).resolve().parent.parent  # the folder holding clipskitty.yaml


def _run(tmp_path, **settings):
    return testing.run_plugin(PLUGIN, transcript=testing.sample_transcript(), duration=testing.SAMPLE_VIDEO_SECONDS,
                              settings=settings, tmp_path=tmp_path)


def test_it_finds_where_the_words_are_said(tmp_path):
    run = _run(tmp_path)
    assert run.ok, run.error
    # "quark burst" is said from 23.5 to 26 s in the sample.
    assert [(m.start, m.end, m.label, m.reason) for m in run.moments] == [
        (13.5, 32.0, "words_said", 'the commentary says "quark burst"')]


def test_it_finds_nothing_when_the_words_are_not_said(tmp_path):
    run = _run(tmp_path, words="triple bloom")
    assert run.ok, run.error
    assert run.moments == []


def test_without_words_it_says_so(tmp_path):
    run = _run(tmp_path, words="")
    assert not run.ok
    assert run.error == "No words are set to look for: add some to the words setting."
