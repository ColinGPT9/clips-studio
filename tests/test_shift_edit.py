"""A clip's saved cuts and mutes stay on the same moment when its start or end
moves (editor suite step 2, video_editor/timeline.py shift_edit).

render_opts["edit"] counts from the clip's start. A clip widened or trimmed in
its details panel, or by the AI chat, used to be rendered with the same
numbers, so every cut and mute landed on a different moment of the video:
widen the front by five seconds and a cut of "um" became a cut of the five
seconds before it. Standard library only, so CI's light install runs these.
"""

import copy
import random

import pytest

from plugins import edit_marks
from video_editor.timeline import MIN_SEGMENT, EditList, shift_edit

# A 30-second clip of the video's 100-130 s, with two cuts and a muted word.
OLD = (100.0, 130.0)
EDIT = {
    "keep": [[0.0, 10.0], [12.0, 25.0]],          # cuts 110-112 and 125-130 of the video
    "mutes": [[3.1, 3.4], [20.0, 20.5]],
    "muted_words": [{"start": 3.14, "end": 3.36, "word": "darn"},
                    {"start": 20.04, "end": 20.46, "word": "heck"}],
    "volume": 1.3,
    "mute_all": False,
    "fade_in": 0.5,
    "fade_out": 1.0,
    "speed": 1.25,
    "hook": {"text": "Wait for it", "seconds": 3.0},
    "music": {"path": "music.mp3", "volume": 0.2, "duck": True},
    "future_key": {"kept": "as it is"},
}
OTHERS = ("volume", "mute_all", "fade_in", "fade_out", "speed", "hook", "music", "future_key")


def _video(ranges, start):
    """Clip-relative ranges as seconds of the video."""
    return [[round(start + a, 3), round(start + b, 3)] for a, b in ranges]


def _kept_at(edit, window, t):
    """Whether second `t` of the video plays in the clip at `window` with `edit`,
    as the render reads it (EditList)."""
    start, end = window
    parsed = EditList.from_dict(edit, duration=end - start)
    if parsed is None or parsed.keep is None:
        return start <= t <= end
    return any(a <= t - start <= b for a, b in parsed.keep)


def _muted_at(edit, window, t):
    start, end = window
    parsed = EditList.from_dict(edit, duration=end - start)
    return parsed is not None and any(a <= t - start <= b for a, b in parsed.mutes)


# ---- widening ---------------------------------------------------------------


def test_widened_at_the_front_the_cuts_stay_and_the_new_head_plays():
    out = shift_edit(EDIT, *OLD, 95.0, 130.0)

    assert _video(out["keep"], 95.0) == [[95.0, 110.0], [112.0, 125.0]]   # 95-100 gained, merged with 100-110
    assert _video(out["mutes"], 95.0) == [[103.1, 103.4], [120.0, 120.5]]
    assert [w["start"] for w in out["muted_words"]] == [8.14, 25.04]


def test_widened_at_the_back_the_new_tail_plays_after_the_cut_that_stays():
    out = shift_edit(EDIT, *OLD, 100.0, 140.0)

    # 125-130 was cut by hand and stays cut; 130-140 is new and plays.
    assert _video(out["keep"], 100.0) == [[100.0, 110.0], [112.0, 125.0], [130.0, 140.0]]
    assert out["mutes"] == EDIT["mutes"]
    assert out["muted_words"] == EDIT["muted_words"]


def test_widened_at_the_back_a_tail_that_touches_the_kept_end_is_merged():
    edit = {"keep": [[0.0, 10.0], [12.0, 30.0]]}
    out = shift_edit(edit, *OLD, 100.0, 136.0)
    assert out["keep"] == [[0.0, 10.0], [12.0, 36.0]]


def test_widened_at_both_ends():
    out = shift_edit(EDIT, *OLD, 90.0, 135.0)
    assert _video(out["keep"], 90.0) == [[90.0, 110.0], [112.0, 125.0], [130.0, 135.0]]
    assert _video(out["mutes"], 90.0) == [[103.1, 103.4], [120.0, 120.5]]


def test_a_head_gained_before_a_cut_at_the_old_start_stays_apart_from_it():
    """The first two seconds were cut by hand: widening the front shows the
    new seconds, then that cut, then the rest."""
    edit = {"keep": [[2.0, 30.0]]}
    out = shift_edit(edit, *OLD, 95.0, 130.0)
    assert _video(out["keep"], 95.0) == [[95.0, 100.0], [102.0, 130.0]]


# ---- narrowing --------------------------------------------------------------


def test_narrowed_at_the_front_what_it_loses_is_cut_away():
    out = shift_edit(EDIT, *OLD, 105.0, 130.0)

    assert _video(out["keep"], 105.0) == [[105.0, 110.0], [112.0, 125.0]]
    assert out["mutes"] == [[15.0, 15.5]]                      # 3.1-3.4 was before the new start
    assert out["muted_words"] == [{"start": 15.04, "end": 15.46, "word": "heck"}]


def test_narrowed_at_the_back():
    out = shift_edit(EDIT, *OLD, 100.0, 120.2)

    assert out["keep"] == [[0.0, 10.0], [12.0, 20.2]]
    assert out["mutes"] == [[3.1, 3.4], [20.0, 20.2]]         # cut at the new end
    assert out["muted_words"] == EDIT["muted_words"]           # both words still begin inside


def test_narrowed_at_both_ends():
    out = shift_edit(EDIT, *OLD, 104.0, 121.0)
    assert _video(out["keep"], 104.0) == [[104.0, 110.0], [112.0, 121.0]]
    assert _video(out["mutes"], 104.0) == [[120.0, 120.5]]


def test_a_sliver_too_short_to_play_is_not_kept():
    edit = {"keep": [[0.0, 10.0], [12.0, 25.0]]}
    out = shift_edit(edit, *OLD, 109.9, 130.0)   # 0.1 s of the first piece is left
    assert out["keep"] == [[2.1, 15.1]]
    assert all(b - a >= MIN_SEGMENT for a, b in out["keep"])


# ---- keep set or not ----------------------------------------------------------


def test_no_keep_stays_no_keep_whatever_the_window():
    edit = {k: v for k, v in EDIT.items() if k != "keep"}
    for window in ((95.0, 135.0), (105.0, 120.0)):
        out = shift_edit(edit, *OLD, *window)
        assert "keep" not in out
        assert EditList.from_dict(out, duration=window[1] - window[0]).keep is None


def test_a_keep_of_the_whole_clip_is_still_the_whole_clip():
    edit = {"keep": [[0.0, 30.0]], "volume": 0.8}
    out = shift_edit(edit, *OLD, 92.0, 141.0)
    assert out["keep"] == [[0.0, 49.0]]
    assert EditList.from_dict(out, duration=49.0).keep is None   # keeping it all is not a cut


def test_with_nothing_kept_inside_the_new_clip_keep_goes_and_the_rest_stays():
    edit = {"keep": [[0.0, 5.0]], "mutes": [[20.0, 21.0]], "volume": 1.5}
    out = shift_edit(edit, *OLD, 110.0, 130.0)

    assert "keep" not in out                                   # the whole new clip plays
    assert out["mutes"] == [[10.0, 11.0]]
    assert out["volume"] == 1.5
    parsed = EditList.from_dict(out, duration=20.0)
    assert parsed is not None and parsed.keep is None and parsed.mutes == [(10.0, 11.0)]


# ---- outside the new window ----------------------------------------------------


def test_moved_clear_of_everything_saved():
    out = shift_edit(EDIT, *OLD, 200.0, 230.0)

    assert out["keep"] == [[0.0, 30.0]]                        # all of it is new, so all of it plays
    assert out["mutes"] == []
    assert out["muted_words"] == []


def test_moved_clear_before_the_old_clip():
    out = shift_edit(EDIT, *OLD, 40.0, 70.0)
    assert out["keep"] == [[0.0, 30.0]]
    assert out["mutes"] == [] and out["muted_words"] == []


def test_a_word_half_in_the_new_clip_keeps_its_own_times():
    """The editor finds a muted word by its start, so it isn't cut short."""
    edit = {"mutes": [[3.1, 3.4]], "muted_words": [{"start": 3.14, "end": 3.36, "word": "darn"}]}
    out = shift_edit(edit, *OLD, 103.2, 130.0)
    assert out["mutes"] == [[0.0, 0.2]]
    assert out["muted_words"] == [{"start": -0.06, "end": 0.16, "word": "darn"}]


# ---- what stays the same ---------------------------------------------------------


def test_the_same_window_gives_an_equal_copy():
    out = shift_edit(EDIT, *OLD, *OLD)
    assert out == EDIT and out is not EDIT
    assert out["keep"] is not EDIT["keep"]


def test_the_input_is_never_changed():
    before = copy.deepcopy(EDIT)
    for window in ((95.0, 140.0), (105.0, 120.0), (200.0, 230.0), (110.0, 130.0)):
        shift_edit(EDIT, *OLD, *window)
    assert EDIT == before


@pytest.mark.parametrize("window", [(95.0, 130.0), (100.0, 140.0), (105.0, 121.0), (300.0, 310.0)])
def test_everything_else_is_left_as_it_is(window):
    out = shift_edit(EDIT, *OLD, *window)
    assert {k: out[k] for k in OTHERS} == {k: EDIT[k] for k in OTHERS}


def test_only_a_dict_is_an_edit():
    assert shift_edit(None, *OLD, 95.0, 130.0) is None
    assert shift_edit([[0, 1]], *OLD, 95.0, 130.0) is None
    assert shift_edit({}, *OLD, 95.0, 130.0) == {}


def test_unreadable_entries_are_dropped_not_fatal():
    edit = {"keep": [["a", "b"], [0.0, 10.0]], "mutes": [None, [1.0, 2.0]],
            "muted_words": ["x", {"start": None, "end": 1.0}, {"start": 1.0, "end": 1.2, "word": "ok"}]}
    out = shift_edit(edit, *OLD, 98.0, 130.0)
    assert out["keep"] == [[0.0, 12.0]]
    assert out["mutes"] == [[3.0, 4.0]]
    assert out["muted_words"] == [{"start": 3.0, "end": 3.2, "word": "ok"}]


def test_a_keep_mutes_or_words_that_isnt_a_list_is_left_as_it_is():
    edit = {"keep": 5, "mutes": "x", "muted_words": None, "volume": 0.5}
    assert shift_edit(edit, *OLD, 95.0, 140.0) == edit


# ---- every moment stays where it was ----------------------------------------------


@pytest.mark.parametrize("seed", range(40))
def test_every_second_of_the_video_plays_and_is_muted_as_before(seed):
    """Any window: inside what both clips hold, a second of the video plays
    (and is muted) exactly when it did; what the new clip gained plays, unless
    it is a sliver too short to play on its own (the render drops those)."""
    rng = random.Random(seed)
    cuts = sorted(rng.uniform(0, 30) for _ in range(4))
    keep = [[cuts[0], cuts[1]], [cuts[2], cuts[3]]]
    mutes = [sorted([rng.uniform(0, 30), rng.uniform(0, 30)]) for _ in range(2)]
    edit = {"keep": keep, "mutes": mutes}
    new = (round(100 + rng.uniform(-20, 20), 2), round(130 + rng.uniform(-20, 20), 2))
    if new[1] - new[0] < 2:
        new = (new[0], new[0] + 2)
    if EditList.from_dict(edit, duration=30.0) is None:
        return
    out = shift_edit(edit, *OLD, *new)

    # Kept pieces the new window cuts shorter than the render plays.
    slivers = [(max(OLD[0] + a, new[0]), min(OLD[0] + b, new[1])) for a, b in keep]
    slivers = [(a, b) for a, b in slivers if 0 < b - a < MIN_SEGMENT + 0.01]
    for i in range(600):
        t = 80 + i * 0.1
        if not (new[0] + 0.02 <= t <= new[1] - 0.02):
            continue
        near = [abs(t - (OLD[0] + x)) for pair in keep + mutes for x in pair]
        if near and min(near) < 0.02:
            continue                        # at an edge; rounding may fall either side
        if any(a - 0.02 <= t <= b + 0.02 for a, b in slivers):
            continue                        # too short to play now, so the render drops it
        if OLD[0] + 0.02 < t < OLD[1] - 0.02:
            if out.get("keep") is not None:
                assert _kept_at(out, new, t) == _kept_at(edit, OLD, t), (t, edit, new, out)
            assert _muted_at(out, new, t) == _muted_at(edit, OLD, t), (t, edit, new, out)
        elif ((t < OLD[0] - 0.02 and OLD[0] - new[0] >= MIN_SEGMENT)
              or (t > OLD[1] + 0.02 and new[1] - OLD[1] >= MIN_SEGMENT)):
            assert _kept_at(out, new, t), (t, new, out)   # gained, so it plays


def test_a_used_suggestions_cuts_are_still_the_same_seconds_of_the_video():
    """plugins/edit_marks reads a clip's edit with the window it is rendered
    at: after the shift, the cuts are the same seconds of the video, so a used
    suggestion's cut is still found in the clip (it used to read as gone)."""
    edit = {"keep": [[0.0, 10.0], [12.0, 30.0]]}
    before = edit_marks.view({"edit": edit}, OLD)["removed"]
    after = edit_marks.view({"edit": shift_edit(edit, *OLD, 95.0, 140.0)}, (95.0, 140.0))["removed"]
    assert before == after == [(110.0, 112.0)]
