"""What the creator did with an edit a plugin suggested: used, hidden or taken
back (plugins/edit_marks.py).

Pure functions, no app: after_render() records a render the creator asked
for, keeping only what that render really changed, and carry() keeps the
creator's decisions when a forced re-run makes the same suggestions again.
The cases live in tests/fixtures/edit_marks/, which the editor's own tests
read too, so both sides give the same answer. Quarkbloom Arena is a made-up
game.
"""

import json
from pathlib import Path

import pytest

from plugins import edit_marks

CASES = Path(__file__).resolve().parent / "fixtures" / "edit_marks"


def _cases(name: str) -> dict:
    return {c["name"]: c for c in json.loads((CASES / f"{name}.json").read_text(encoding="utf-8"))["cases"]}


AFTER = _cases("after_render")
CARRY = _cases("carry")


def _after(name: str) -> tuple[list, list]:
    c = AFTER[name]
    return edit_marks.after_render(c["entries"], c["used"], c["before"], c["after"], c["window"],
                                   c.get("new_window"))


def _carry(name: str) -> list:
    c = CARRY[name]
    return edit_marks.carry(c["old"], c["new"], c["kept"], c.get("rendered"))


@pytest.mark.parametrize("name", sorted(AFTER))
def test_every_shared_render_case_gives_its_answer(name):
    entries, lines = _after(name)
    assert {"entries": entries, "lines": lines} == AFTER[name]["expect"], AFTER[name]["about"]


@pytest.mark.parametrize("name", sorted(CARRY))
def test_every_shared_rerun_case_gives_its_answer(name):
    assert _carry(name) == CARRY[name]["expect"], CARRY[name]["about"]


def test_used_keeps_only_what_the_render_changed():
    (entry,), lines = _after("used_keeps_only_what_the_render_changed")
    assert entry["state"] == "used" and lines == []
    applied = entry["applied"]
    # The suggestion's cut, not the creator's own (120-122 s) nor a span nothing removed.
    assert applied["removed"] == [[102.0, 106.5]]
    # Its mute and the word inside it; not a mute the suggestion never made.
    assert applied["mutes"] == [[108.25, 108.75]]
    assert applied["muted_words"] == [{"start": 108.3, "end": 108.6, "word": "Quark"}]
    # Its fade, hook title and layout, as the suggestion set them; not a speed it never suggested.
    assert sorted(applied["values"]) == ["crop", "fade_out", "hook"]
    assert applied["values"]["fade_out"] == {"before": 0.3, "after": 0.5}
    # What the client sent is never changed in place.
    assert AFTER["used_keeps_only_what_the_render_changed"]["entries"][0]["state"] == "new"


def test_a_used_item_with_nothing_in_the_render_is_not_marked():
    (entry,), lines = _after("nothing_in_the_render_is_not_marked")
    assert entry["state"] == "new" and "applied" not in entry
    assert lines == [
        "Not marked as used: none of Quarkbloom Trimmer's suggested edit a1b2c3d4e5f6 is in this render",
        "Not marked as used: this clip has no suggested edit 0123456789ab"]
    # A value the saved options already held wasn't changed by this render either.
    c = AFTER["used_keeps_only_what_the_render_changed"]
    entries, _ = edit_marks.after_render(c["entries"], c["used"], c["after"], c["after"], c["window"])
    assert entries[0]["state"] == "new"


@pytest.mark.parametrize("name", ["take_it_back", "reset"])
def test_a_later_render_trims_applied_and_hides_when_nothing_is_left(name):
    (entry,), lines = _after(name)
    assert entry["state"] == "hidden" and "applied" not in entry
    hidden = "Marked as hidden: none of Quarkbloom Trimmer's suggested edit a1b2c3d4e5f6 is left in the clip's edit"
    assert lines == [hidden]
    # A hand change keeps what is still the suggestion's: the cut not put back, the word still
    # hidden, the hook title and layout; not the mute span or the fade the creator changed.
    (entry,), lines = _after("hand_change")
    assert entry["state"] == "used" and lines == []
    assert entry["applied"]["removed"] == [[102.0, 104.0]]
    assert "mutes" not in entry["applied"] and sorted(entry["applied"]["values"]) == ["crop", "hook"]
    # A clip whose start moved keeps only what is still where the suggestion put it.
    (entry,), _ = _after("moved_start")
    assert entry["applied"]["removed"] == [[103.0, 106.5]] and "muted_words" not in entry["applied"]


def test_a_used_suggestion_the_saved_edit_still_holds_is_not_hidden():
    """PATCH /clips/{id} refuses to hide or show a used suggestion while the
    clip's saved edit holds some of it: it would lose its Take it back."""
    c = AFTER["take_it_back"]
    sid = c["entries"][0]["id"]
    # `before` is the saved edit with the suggestion in it; `after`, the creator's own once taken back.
    assert edit_marks.still_held(c["entries"], sid, c["before"], c["window"]) is True
    assert edit_marks.still_held(c["entries"], sid, c["after"], c["window"]) is False
    # A new or hidden one holds nothing to take back, and neither does an id the clip doesn't have.
    for state in ("new", "hidden"):
        assert edit_marks.still_held([{**c["entries"][0], "state": state}], sid, c["before"], c["window"]) is False
    assert edit_marks.still_held(c["entries"], "0123456789ab", c["before"], c["window"]) is False


def test_a_suggested_trim_of_the_clips_ends_stays_used_until_a_trim_passes_it():
    """Take it back puts back a suggestion's cut at the clip's start or end
    while that edge is where the cut left it, so a render keeps it used. A
    cut the creator has since trimmed past is out of its reach."""
    window = (100.0, 130.0)
    entry = {"id": "b2c3d4e5f6a1", "plugin": "example-dev/quarkbloom-trimmer", "name": "Quarkbloom Trimmer",
             "state": "used", "edit": {"cuts": [[100.0, 103.0], [110.0, 112.0], [127.0, 130.0]]},
             "applied": {"removed": [[100.0, 103.0], [110.0, 112.0], [127.0, 130.0]]}}
    saved = {"edit": {"keep": [[3, 10], [12, 27]]}}
    (kept,), lines = edit_marks.after_render([entry], [], saved, saved, window)
    assert kept["applied"] == entry["applied"] and lines == []
    assert edit_marks.still_held([entry], entry["id"], saved, window) is True
    # The creator trims the start past the suggestion's first two cuts: only its end cut is left.
    trimmed = {"edit": {"keep": [[15, 27]]}}
    (kept,), _ = edit_marks.after_render([entry], [], saved, trimmed, window)
    assert kept["state"] == "used" and kept["applied"] == {"removed": [[127.0, 130.0]]}
    # And past that too: none of it is left where Take it back could put it back.
    (kept,), lines = edit_marks.after_render([entry], [], saved, {"edit": {"keep": [[15, 25]]}}, window)
    assert kept["state"] == "hidden" and "applied" not in kept
    assert lines == [("Marked as hidden: none of Quarkbloom Trimmer's suggested edit b2c3d4e5f6a1 is left in the "
                      "clip's edit")]


def test_a_rerun_keeps_the_creators_decision_on_the_same_suggestion():
    used, hidden = _carry("rerun_keeps_decision")
    assert (used["state"], used["version"], used["applied"]) == (
        "used", "1.1.0", CARRY["rerun_keeps_decision"]["old"][0]["applied"])
    assert hidden["state"] == "hidden" and "applied" not in hidden


def test_a_changed_suggestion_is_new_again():
    entries = _carry("changed_is_new")
    assert [(e["id"], e["state"]) for e in entries] == [
        ("b2c3d4e5f6a1", "new"), ("e5d4c3b2a1f6", "new"), ("a1b2c3d4e5f6", "used")]
    assert "applied" not in entries[0] and "remade" not in entries[0]


def test_a_used_suggestion_stays_when_the_rerun_no_longer_makes_it():
    (entry,) = _carry("used_stays_when_not_made")
    assert entry["state"] == "used" and entry["applied"]["removed"] == [[102.0, 106.5]]
    # Nothing to carry and nothing kept.
    assert edit_marks.carry([{"id": "f6e5d4c3b2a1", "state": "hidden"}], None, {}, {}) == []


def test_a_rerun_over_a_saved_edit_marks_used_entries_remade():
    assert [e.get("remade") for e in _carry("rerun_keeps_decision")] == [True, None]
    assert _carry("remade_for_a_saved_layout")[0]["remade"] is True
    assert "remade" not in _carry("not_remade_when_the_file_has_the_edit")[0]
    assert "remade" not in _carry("not_remade_for_the_default_layout")[0]
    # Any render the creator asks for uses the saved options, so remade goes.
    (entry,), _ = _after("make_it_again_drops_remade")
    assert entry["state"] == "used" and "remade" not in entry


@pytest.mark.parametrize(("value", "message"), [
    ([], "suggestions: expected {used: [{id, applied}]}"),
    ({"used": [], "extra": 1}, "suggestions: expected {used: [{id, applied}]}"),
    ({"used": {}}, "suggestions.used: expected a list of {id, applied}"),
    ({"used": [{"id": f"s{i}"} for i in range(9)]}, "suggestions.used: at most 8 suggestions in one render"),
    ({"used": [{"applied": {}}]}, "suggestions.used[0]: expected {id, applied}"),
    ({"used": [{"id": "x" * 33}]}, "suggestions.used[0].id: expected a suggestion's id, at most 32 characters"),
    ({"used": [{"id": "a"}, {"id": "a"}]}, "suggestions.used[1].id: a is named twice"),
    ({"used": [{"id": "a", "applied": {"keep": []}}]},
     "suggestions.used[0].applied: expected {removed, mutes, muted_words, values}"),
    ({"used": [{"id": "a", "applied": {"removed": [[1, float("inf")]]}}]},
     "suggestions.used[0].applied.removed: expected at most 64 [start, end] pairs of seconds"),
    ({"used": [{"id": "a", "applied": {"removed": [[1, 2]] * 65}}]},
     "suggestions.used[0].applied.removed: expected at most 64 [start, end] pairs of seconds"),
    ({"used": [{"id": "a", "applied": {"mutes": [[1, 2]] * 21}}]},
     "suggestions.used[0].applied.mutes: expected at most 20 [start, end] pairs of seconds"),
    ({"used": [{"id": "a", "applied": {"mutes": [[True, 2]]}}]},
     "suggestions.used[0].applied.mutes: expected at most 20 [start, end] pairs of seconds"),
    ({"used": [{"id": "a", "applied": {"muted_words": [{"start": 1, "end": 2}]}}]},
     "suggestions.used[0].applied.muted_words: expected at most 500 {start, end, word}, in seconds"),
    ({"used": [{"id": "a", "applied": {"values": {"music": {"after": 1}}}}]},
     "suggestions.used[0].applied.values: 'music' isn't one of volume, fade_in, fade_out, speed, hook, crop"),
    ({"used": [{"id": "a", "applied": {"values": {"speed": {"after": "fast"}}}}]},
     "suggestions.used[0].applied.values.speed: expected {before, after}, each a number from 0.5 to 3"),
    ({"used": [{"id": "a", "applied": {"values": {"volume": {"before": 1e9, "after": 1.2}}}}]},
     "suggestions.used[0].applied.values.volume: expected {before, after}, each a number from 0 to 2"),
    ({"used": [{"id": "a", "applied": {"values": {"hook": {"after": {"text": ""}}}}}]},
     "suggestions.used[0].applied.values.hook: expected {before, after}, each a hook title {text, seconds} "
     + "of at most 120 characters and 1 to 10 seconds"),
])
def test_a_render_s_suggestions_are_checked(value, message):
    with pytest.raises(ValueError) as e:
        edit_marks.clean_used(value)
    assert str(e.value) == message


def test_a_well_formed_render_record_and_state_pass():
    applied = AFTER["used_keeps_only_what_the_render_changed"]["used"][0]["applied"]
    cleaned = edit_marks.clean_used({"used": [{"id": "a1b2c3d4e5f6", "applied": applied}]})
    assert cleaned["used"][0]["applied"]["removed"][0] == [102.0, 106.5]
    assert edit_marks.clean_used({}) == {"used": []}
    assert edit_marks.clean_state({"id": "a1b2c3d4e5f6", "state": "hidden"}) == ("a1b2c3d4e5f6", "hidden")
    for bad, message in (({"id": "a1b2c3d4e5f6", "state": "used"},
                          "suggestion: state must be hidden or new; a suggestion is used by applying it"),
                         ({"state": "new"},
                          "suggestion: expected {id, state}, with a suggestion's id of at most 32 characters")):
        with pytest.raises(ValueError) as e:
            edit_marks.clean_state(bad)
        assert str(e.value) == message
