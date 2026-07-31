"""The decision logic around TalkNet, tested without TalkNet.

The model itself needs a GPU, a 63 MB checkpoint and real footage, none of
which belong in a unit test. What IS worth pinning down is everything around
it, because every one of these was got wrong first:

  * scoring only where two people are actually on screen — the optimisation
    that keeps this affordable
  * refusing to answer during silence, which is what replaced a broken
    absolute threshold on the model's logit
  * treating "this person was not on screen" as no answer rather than as a
    quiet answer

pytest.importorskip because CI installs four packages and none of them is
OpenCV; this runs locally and on any machine with the full requirements.
"""

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from video.tracker import _asd_verdict, _contested_spans, _interp_boxes  # noqa: E402

FPS = 25


def spans(mask_frames, n):
    mask = np.zeros(n, dtype=bool)
    for a, b in mask_frames:
        mask[a:b] = True
    return _contested_spans(mask, FPS, n)


def test_nothing_contested_means_nothing_to_score():
    assert spans([], 500) == []


def test_a_contested_stretch_is_scored_whole():
    assert spans([(100, 200)], 500) == [(100, 200)]


def test_a_flicker_is_grown_to_a_window_the_model_can_take():
    # Six frames of two faces is a quarter of a second. The model's shortest
    # window is a second, so scoring it as-is would score mostly padding.
    (a, b), = spans([(100, 106)], 500)
    assert b - a >= FPS
    assert a <= 100 and b >= 106  # and it still covers what was contested


def test_stretches_close_together_become_one():
    # Two people who briefly turn away are still the same conversation;
    # splitting it would restart the model's context every time.
    assert len(spans([(100, 150), (160, 200)], 500)) == 1


def test_stretches_far_apart_stay_separate():
    assert len(spans([(50, 100), (300, 350)], 500)) == 2


def test_a_grown_span_never_runs_off_the_end():
    for a, b in spans([(495, 498)], 500):
        assert 0 <= a < b <= 500


def test_silence_produces_no_verdict():
    """The one that matters. There is deliberately no floor on the model's
    logit — its absolute level moves with the crop — so silence is what stops
    two quiet faces being ranked against each other."""
    clear = {1: 3.0, 2: -2.0}
    assert _asd_verdict(clear, [1, 2], loud=True) == 1
    assert _asd_verdict(clear, [1, 2], loud=False) is None


def test_a_close_call_is_not_a_verdict():
    assert _asd_verdict({1: 0.4, 2: 0.2}, [1, 2], loud=True) is None


def test_a_negative_winner_still_wins():
    """Both faces score below zero on real footage all the time; what makes
    one of them the speaker is beating the other, not clearing a number."""
    assert _asd_verdict({1: -0.5, 2: -2.0}, [1, 2], loud=True) == 1


def test_only_people_on_screen_can_win():
    assert _asd_verdict({1: 5.0, 2: -1.0}, [2], loud=True) == 2
    assert _asd_verdict({1: 5.0}, [2], loud=True) is None
    assert _asd_verdict({}, [1, 2], loud=True) is None


def test_boxes_are_not_invented_where_the_face_was_not_seen():
    """Interpolating past the ends, or across a long gap, would hand the model
    a stale box — which on a moving camera is somebody else's face."""
    seen = [(1.0, (10, 10, 50, 50)), (1.5, (20, 10, 60, 50)),
            (5.0, (30, 10, 70, 50))]
    times = np.array([0.5, 1.0, 1.25, 1.5, 3.25, 5.0, 6.0])
    out = _interp_boxes(seen, times)

    assert np.isnan(out[0][0]), "before the track started"
    assert np.isnan(out[6][0]), "after it ended"
    assert np.isnan(out[4][0]), "middle of a 3.5s gap"
    assert not np.isnan(out[1][0]) and not np.isnan(out[5][0])
    # And where it does interpolate, it interpolates.
    assert out[2][0] == pytest.approx(15.0)


def test_changing_subject_is_a_cut_not_a_pan():
    """Watched back, the panning was the thing that looked wrong: the crop
    swept across the room to reach whoever had started talking and arrived
    after they had. A cut is on them from the first frame, which is how the
    footage itself is edited.

    Pinned as a constant rather than a behaviour test because reproducing it
    needs a real two-person video; the measurement that justifies it is in the
    commit — 0.61 frame-widths of panning before, 0.00 after, same two
    subject changes."""
    from video import tracker

    assert tracker._SWITCH_CUT is True
    assert tracker._SWITCH_HOLD >= 1.0, (
        "a conversation trades the verdict about once a second; a shorter "
        "hold lets the camera flick back and forth"
    )
