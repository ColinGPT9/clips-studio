"""A missing Haar cascade must cost crop precision, not the whole clip.

Issue #86: a job downloaded, transcribed, analysed, scored and picked a clip,
then rendered zero clips. The log ended with

    Render failed for 21s-39s: OpenCV(4.13.0) cascadedetect.cpp:1689:
      error: (-215:Assertion failed) !empty() in function 'detectMultiScale'

`cv2.CascadeClassifier` given a path that does not exist does NOT raise. It
returns an empty classifier, and the failure only appears later inside
`detectMultiScale`. So a missing data file surfaced as a rendering crash.

It was missing because the frozen build ships cv2 as a hidden import, which
carries the module and not its data directory — meaning this reproduced only in
installed copies and never from a checkout, which is why it shipped.

Two defences, both tested here: the file is now resolved through core.binaries
(so the bundled copy is found), and an unusable cascade disables face
refinement instead of raising.
"""

import threading

import numpy as np
import pytest

import core.binaries as binaries
import video.tracker as tracker

FRAME = np.zeros((400, 400, 3), dtype=np.uint8)
BOX = (50, 50, 250, 350)


@pytest.fixture(autouse=True)
def _fresh_thread_state():
    """_get_cascades caches per thread, including the "missing" flag."""
    tracker._thread_local = threading.local()
    yield
    tracker._thread_local = threading.local()


def test_a_missing_cascade_does_not_crash_the_render(monkeypatch):
    """The regression. Before this, _face_box raised !empty() and took the
    clip — and the whole job — down with it."""
    monkeypatch.setattr(binaries, "haar_cascade", lambda name: None)

    assert tracker._face_box(FRAME, BOX) is None


def test_a_missing_cascade_disables_refinement_rather_than_retrying(monkeypatch):
    """Latched, so a 40-minute render does not print the notice per frame or
    re-probe the filesystem thousands of times."""
    calls = []
    monkeypatch.setattr(binaries, "haar_cascade", lambda name: calls.append(name))

    for _ in range(5):
        tracker._face_box(FRAME, BOX)

    assert tracker._get_cascades() is None
    assert len(calls) <= 2, f"probed the filesystem {len(calls)} times, expected one attempt"


def test_an_unreadable_cascade_is_caught_too(monkeypatch, tmp_path):
    """A file that exists but is not a cascade loads "successfully" and is
    empty — the same crash by a different route, so .empty() is checked."""
    junk = tmp_path / "haarcascade_frontalface_default.xml"
    junk.write_text("not a cascade", encoding="utf-8")
    monkeypatch.setattr(binaries, "haar_cascade", lambda name: str(junk))

    assert tracker._get_cascades() is None
    assert tracker._face_box(FRAME, BOX) is None


def test_the_normal_path_still_loads_both_cascades():
    """The fix must not quietly disable face refinement for everyone else."""
    cascades = tracker._get_cascades()

    assert cascades is not None, "cascades should load in a checkout"
    frontal, profile = cascades
    assert not frontal.empty() and not profile.empty()


def test_binaries_resolves_the_cascades_a_checkout_has():
    assert binaries.haar_cascade("haarcascade_frontalface_default.xml")
    assert binaries.haar_cascade("haarcascade_profileface.xml")
    assert binaries.haar_cascade("haarcascade_does_not_exist.xml") is None
