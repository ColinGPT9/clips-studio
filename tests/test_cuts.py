"""The cut decision behind shot-aware reframing (tracking.shot_aware).

video/cuts.py decides from lists of numbers (find_cut), so all of this runs
in CI's light install: no numpy, no OpenCV, no footage. Each window below is
what ShotCuts would measure around a candidate: entry i compares frame i with
frame i-1 (gray: the small_gray difference, 0..255; col: the hue/saturation
Bhattacharyya distance, 0..1) and luma is each frame's brightness.

How the setting itself is read (shot_aware_on: on unless the settings turn
it off) is tested here too. The last test reads video/tracker.py's source
rather than importing it, for the same reason.
"""

import ast
from pathlib import Path

import pytest

from video import cuts

ROOT = Path(__file__).resolve().parent.parent


def window(n=16, motion=1.0, colour=0.02, luma=100.0):
    """n frames of ordinary motion: every frame a little different from the last."""
    return [0.0] + [motion] * (n - 1), [0.0] + [colour] * (n - 1), [luma] * n


def cut(gray, col, luma, k, g=60.0, c=0.6, to=180.0):
    """A cut on frame k: the picture, its colours and its brightness change and stay changed."""
    gray[k], col[k] = g, c
    luma[k:] = [to] * (len(luma) - k)


# ---- the rules ----------------------------------------------------------------------------


def test_a_hard_cut_is_found_on_its_own_frame():
    gray, col, luma = window()
    cut(gray, col, luma, 10)
    assert cuts.find_cut(gray, col, luma, 9, 12) == 10


def test_every_frame_of_the_gap_can_be_the_cut():
    for k in (9, 10, 11, 12):
        gray, col, luma = window()
        cut(gray, col, luma, k)
        assert cuts.find_cut(gray, col, luma, 9, 12) == k


def test_a_gray_change_needs_a_colour_change_too():
    # A brightness change with the same colours (a light switched on, an
    # exposure step) is not a new shot.
    gray, col, luma = window()
    cut(gray, col, luma, 10, g=60.0, c=0.1)
    assert cuts.find_cut(gray, col, luma, 9, 12) is None


def test_the_strong_rule_holds_whatever_the_baseline():
    # Fast motion all around (baseline 20), so only the strong rule can
    # accept it: gray >= 40 with colour >= 0.30.
    gray, col, luma = window(motion=20.0)
    cut(gray, col, luma, 10, g=45.0, c=0.35)
    assert cuts.find_cut(gray, col, luma, 9, 12) == 10
    gray, col, luma = window(motion=20.0)
    cut(gray, col, luma, 10, g=45.0, c=0.25)
    assert cuts.find_cut(gray, col, luma, 9, 12) is None


def test_a_weaker_cut_has_to_stand_out_from_the_motion_around_it():
    # gray >= 18 and colour >= 0.20 is enough against a still picture...
    gray, col, luma = window(motion=1.0)
    cut(gray, col, luma, 10, g=25.0, c=0.22)
    assert cuts.find_cut(gray, col, luma, 9, 12) == 10
    # ...but not against motion of 10 a frame: 25 is under 3x that.
    gray, col, luma = window(motion=10.0)
    cut(gray, col, luma, 10, g=25.0, c=0.22)
    assert cuts.find_cut(gray, col, luma, 9, 12) is None


def test_below_the_floors_nothing_is_a_cut():
    gray, col, luma = window()
    cut(gray, col, luma, 10, g=17.0, c=0.5)       # under CUT_GRAY
    assert cuts.find_cut(gray, col, luma, 9, 12) is None
    gray, col, luma = window()
    cut(gray, col, luma, 10, g=30.0, c=0.18)      # under CUT_COL
    assert cuts.find_cut(gray, col, luma, 9, 12) is None


def test_a_whip_pan_is_no_cut():
    # Every frame changes a lot and the colours barely: a fast handheld pan.
    gray, col, luma = window(motion=30.0, colour=0.1)
    assert cuts.find_cut(gray, col, luma, 9, 12) is None


def test_only_the_gap_counts():
    # A cut before the previous sample was the previous pair's to find; one
    # after the current sample is the next pair's.
    for k in (5, 13):
        gray, col, luma = window()
        cut(gray, col, luma, k)
        assert cuts.find_cut(gray, col, luma, 9, 12) is None


def test_the_window_s_first_frame_is_never_a_cut():
    gray, col, luma = window()
    gray[0], col[0] = 200.0, 1.0
    assert cuts.find_cut(gray, col, luma, 0, 3) is None


def test_of_two_cuts_in_one_gap_the_first_wins():
    gray, col, luma = window()
    cut(gray, col, luma, 10)
    cut(gray, col, luma, 12, to=40.0)
    assert cuts.find_cut(gray, col, luma, 9, 12) == 10


# ---- flashes ------------------------------------------------------------------------------


def flash(gray, col, luma, k, frames=2, peak=240.0):
    """A flash: bright for `frames` frames from k, then the same picture as before."""
    for j in range(k, k + frames):
        luma[j] = peak
    gray[k], col[k] = 70.0, 0.4
    gray[k + frames], col[k + frames] = 70.0, 0.4


def test_a_flash_is_no_cut():
    gray, col, luma = window()
    flash(gray, col, luma, 10)
    assert cuts.find_cut(gray, col, luma, 9, 12) is None


def test_a_flash_that_began_on_the_last_sample_is_no_cut_where_it_ends():
    # The flash starts on the previous sample's frame (8) and ends inside
    # this gap (10). On its own, frame 10 looks like a cut to a new shot.
    gray, col, luma = window()
    flash(gray, col, luma, 8)
    assert cuts.find_cut(gray, col, luma, 9, 12) is None


def test_a_cut_after_a_flash_is_still_a_cut():
    gray, col, luma = window()
    flash(gray, col, luma, 5)
    cut(gray, col, luma, 10)
    assert cuts.find_cut(gray, col, luma, 9, 12) == 10


def test_a_flash_needs_a_jump_in_brightness():
    # The picture changes and comes back, but no brighter: luma cannot call
    # that a flash, so it is the cut the other rules say it is.
    gray, col, luma = window()
    gray[10], col[10] = 60.0, 0.6
    gray[12], col[12] = 60.0, 0.6
    assert cuts.find_cut(gray, col, luma, 9, 12) == 10


def test_without_brightness_a_quick_change_back_is_a_flash():
    gray, col, _ = window()
    gray[10], col[10] = 60.0, 0.6
    gray[11], col[11] = 50.0, 0.6       # at least FLASH_BACK of the first change, a frame later
    assert cuts.find_cut(gray, col, None, 9, 12) is None
    gray[11], col[11] = 1.0, 0.02
    assert cuts.find_cut(gray, col, None, 9, 12) == 10


# ---- the setting ----------------------------------------------------------------------------


def test_no_setting_turns_it_on():
    # settings.yaml as it ships has a tracking block without the key.
    assert cuts.shot_aware_on({"detector": "yolov8n-pose.pt", "sample_fps": 8}) is True
    assert cuts.shot_aware_on({}) is True
    assert cuts.shot_aware_on(None) is True                # no tracking block
    assert cuts.shot_aware_on({"shot_aware": None}) is True   # `shot_aware:` with nothing after it


@pytest.mark.parametrize("value", [True, 1, "true", "TRUE", "Yes", " yes ", "on", "ON\n", "1", " 1 "])
def test_the_setting_keeps_the_mode_on(value):
    assert cuts.shot_aware_on({"shot_aware": value}) is True


@pytest.mark.parametrize("value", [
    False, 0,
    "false", "no", "off", "0",          # quoted in settings.yaml: text, which a truth test reads as on
    "False", " NO ", "Off", " 0 ",
])
def test_the_setting_turns_the_mode_off(value):
    assert cuts.shot_aware_on({"shot_aware": value}) is False


@pytest.mark.parametrize("value", [2, -1, 1.0, 0.0, "", "enabled", "ture", [], ["true"], {}])
def test_any_other_value_is_off_too(value):
    # The safe reading of a value nobody can be sure about: the framing as before the mode.
    assert cuts.shot_aware_on({"shot_aware": value}) is False


# ---- the replay's snapshot -----------------------------------------------------------------


def _module(name):
    return ast.parse((ROOT / "video" / f"{name}.py").read_text(encoding="utf-8"))


def _is_tracks_item(node, dicts):
    return isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in dicts


def _track_reads(fn, funcs, dicts, singles, seen):
    """Attributes read off a track in `fn` and the module functions it hands
    one to: `dicts` names hold {id: _Track}, `singles` names hold one _Track."""
    singles = set(singles)
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and _is_tracks_item(node.value, dicts):
            singles |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    reads = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and (
                _is_tracks_item(node.value, dicts)
                or (isinstance(node.value, ast.Name) and node.value.id in singles)):
            reads.add(node.attr)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in funcs:
            callee = funcs[node.func.id]
            params = [a.arg for a in callee.args.args]
            d, s = set(), set()
            for i, arg in enumerate(node.args):
                if isinstance(arg, ast.Name) and i < len(params):
                    if arg.id in dicts:
                        d.add(params[i])
                    elif arg.id in singles:
                        s.add(params[i])
            if (d or s) and (callee.name, frozenset(d), frozenset(s)) not in seen:
                seen.add((callee.name, frozenset(d), frozenset(s)))
                reads |= _track_reads(callee, funcs, d, s, seen)
    return reads


def test_every_track_value_the_framing_pass_reads_is_replayed():
    """The framing pass replays each sample against a snapshot of the tracks
    (_Moment), because their values move as the clip goes on. A value read
    there but not in the snapshot is silently read at its END-of-clip value.

    last_t was one: the "subject gone" test read when each track was last
    seen in the whole clip, so a subject who came back later never counted as
    gone before they did. It is snapshotted now, and replayed in shot-aware
    mode (see _Moment)."""
    tree = _module("tracker")
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    moment = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Moment")
    fields = {s.target.id for s in moment.body if isinstance(s, ast.AnnAssign)}
    assert "last_t" in fields

    framing = next(
        n for n in ast.walk(funcs["compute_tracking"])
        if isinstance(n, ast.For) and isinstance(n.iter, ast.Call)
        and getattr(n.iter.func, "id", None) == "enumerate"
        and [getattr(a, "id", None) for a in n.iter.args] == ["samples"]
    )
    reads = _track_reads(framing, funcs, {"tracks"}, set(), set())
    assert {"speak", "dominance", "last_t", "box", "head_rate", "centers"} <= reads   # the scan works
    # `centers` is replayed as its length, n_centers.
    assert {"n_centers" if r == "centers" else r for r in reads} <= fields
