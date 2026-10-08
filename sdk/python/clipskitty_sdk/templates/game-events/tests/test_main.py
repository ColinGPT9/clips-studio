"""This plugin's tests. They run on your PC, never inside Clips Kitty:

    python -m pytest -q

They need the SDK's test extra (pip install "clipskitty-sdk[yaml,test]").
clipskitty_sdk.testing runs the plugin the way Clips Kitty would, on the
SDK's 40-second sample video, whose red banner shows from 22 to 27 s with a
loud sound. Without FFmpeg on PATH they are skipped. Change these as you
change the plugin.
"""

import re
from pathlib import Path

from clipskitty_sdk import testing

PLUGIN = Path(__file__).resolve().parent.parent  # the folder holding clipskitty.yaml


def _run(tmp_path, **settings):
    video = testing.sample_video(tmp_path)  # skips the test without FFmpeg
    return testing.run_plugin(PLUGIN, video=video, settings=settings, tmp_path=tmp_path)


def test_it_finds_the_banner_and_the_loud_sound(tmp_path):
    run = _run(tmp_path)
    assert run.ok, run.error
    # The banner shows from 22 to 26.75 s: the moment runs from 6 s before to 3 s after.
    assert [(m.start, m.end, m.label) for m in run.moments] == [(16.0, 29.75, "quark_burst")]
    assert re.fullmatch(r"the banner shows from 22 to 26.75 s, and the sound gets \d+ dB louder", run.moments[0].reason)


def test_a_banner_without_a_loud_enough_sound_is_not_a_moment(tmp_path):
    run = _run(tmp_path, louder_by_db=30)
    assert run.ok, run.error
    assert run.moments == []
    assert run.notes == "The banner shows 1 time(s), 0 of them as the sound gets louder."


def test_another_colour_is_not_the_banner(tmp_path):
    run = _run(tmp_path, banner_colour="30e03a")
    assert run.ok, run.error
    assert run.moments == []
