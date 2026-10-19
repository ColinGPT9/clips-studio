"""Sports (sports/, docs/SPORTS.md): the registry, the soccer data, and the
deterministic logic every sport shares: a moment's window, grouping a moment
with its replays, typing a moment only when signals agree, and choosing what
a job keeps. No model runs here."""

import importlib

import pytest

pytest.importorskip("yaml")

import sports
from sports.core.events import SportEvent, best_per_moment, group_moments, valid
from sports.core.select import post_extra, select
from sports.core.windows import window


@pytest.fixture
def soccer():
    return sports.profile_for({"clips": {"sport": {"name": "soccer"}}})


# ---- registry and data ---------------------------------------------------------


def test_soccer_is_offered_with_its_choices():
    offered = {s["id"]: s for s in sports.available()}
    assert "soccer" in offered
    assert {h["id"] for h in offered["soccer"]["highlights"]} >= {"best", "goals", "saves", "cards"}
    assert next(p["id"] for p in offered["soccer"]["periods"]) == "full"


def test_every_highlight_choice_names_real_events():
    for name in sports.SPORTS:
        spec = sports.spec(name)
        events = spec["events"]
        for choice in spec["highlights_choices"].values():
            if choice["events"] != "all":
                assert set(choice["events"]) <= set(events), choice
        for kind in spec.get("callouts", {}):
            assert kind in events, kind
        for e in events.values():
            assert 0 <= e["importance"] <= 100 and e["pre"] >= 0 and e["post"] >= 0


def test_the_option_is_cleaned_and_unknown_values_refused():
    assert sports.clean("soccer") == {"name": "soccer", "highlights": "best", "period": "full"}
    got = sports.clean({"name": "Soccer", "highlights": "goals", "period": "second_half",
                        "teams": "  Team   A  "})
    assert got == {"name": "soccer", "highlights": "goals", "period": "second_half", "teams": "Team A"}
    assert sports.clean({"name": "soccer", "footage": "sideline"})["footage"] == "sideline"
    assert "footage" not in sports.clean({"name": "soccer", "footage": "auto"})
    # Custom's own words go with Custom only.
    custom = sports.clean({"name": "soccer", "highlights": "custom", "request": " the  saves "})
    assert custom["request"] == "the saves"
    assert "request" not in sports.clean({"name": "soccer", "highlights": "goals", "request": "the saves"})
    for bad in ({"name": "curling"}, {"name": "soccer", "highlights": "dunks"},
                {"name": "soccer", "period": "third_half"}, {"name": "soccer", "period": "q4"},
                {"name": "soccer", "footage": "drone"}, 42):
        with pytest.raises(ValueError):
            sports.clean(bad)


def test_the_words_become_a_clip_direction():
    assert sports.direction({"name": "soccer"}) == ""
    assert sports.direction({"teams": "Team A"}) == "More clips involving Team A."
    both = sports.direction({"request": "the saves.", "teams": "Team B"})
    assert both == "the saves. More clips involving Team B."


def test_the_framing_hook_reaches_the_ball_follower(monkeypatch):
    # The pipeline reaches a sport's framing through sports.framing(), the
    # package's framing() hook. A module named framing.py beside it would
    # shadow the hook (or be shadowed by it), and every clip would quietly
    # fall back to face tracking: checked both before and after the module
    # itself has been imported.
    from sports.soccer import ball

    monkeypatch.setattr(ball, "compute", lambda path, model_name, imgsz: {
        "mode": "track", "path": [(0.0, 0.4)], "led": {"ball": 1}, "imgsz": imgsz})
    got = sports.framing("soccer", "clip.mp4", {})
    assert got == {"mode": "track", "path": [(0.0, 0.4)], "imgsz": 1280}
    importlib.import_module("sports.soccer.ball")        # imported by name: still the hook
    assert callable(importlib.import_module("sports.soccer").framing)


def test_no_sport_means_no_profile():
    assert sports.profile_for({"clips": {}}) is None
    assert sports.option({"clips": {"sport": {"name": "curling"}}}) is None


def test_the_profile_speaks_the_gaming_interface(soccer):
    # analysis/fusion.py asks a sport what it asks the gaming profile.
    assert soccer.game_at(10, 20) == ("", "soccer")
    assert soccer.genre_track(3) == ["soccer"] * 3
    assert soccer.games == [] and soccer.game == ""
    assert "SOCCER MATCH" in soccer.guidance("clips")
    assert "prefer the bigger moment" in soccer.guidance("rerank")
    assert "GOAL" in soccer.screen_lexicon["events"]["soccer"]
    assert soccer.sound_weights()["soccer"]["crowd"] == 1.0


# ---- reading the commentary -------------------------------------------------------


@pytest.mark.parametrize("said, expected", [
    ("GOOOAL! What a goal from the edge of the box!", "goal"),
    ("¡Golazo! Increíble", "goal"),
    ("and it's a penalty... he scores from the spot", "penalty_goal"),
    ("The keeper saves the penalty!", "penalty_miss"),
    ("own goal! into the back of the net off the defender", "own_goal"),
    ("Red card! He's been sent off", "red_card"),
    ("the VAR is checking it", "var"),
    ("what a save from the goalkeeper", "save"),
    ("off the post! so close", "chance"),
])
def test_the_commentary_names_the_moment(soccer, said, expected):
    kind, confidence = soccer.classify(soccer.callouts_in(said), ["crowd"])
    assert kind == expected and confidence > 0.5


@pytest.mark.parametrize("said", ["a shot on goal, and it's a goal kick", "var is a variable name",
                                  "but he's going nowhere"])
def test_everyday_words_are_not_moments(soccer, said):
    assert soccer.callouts_in(said) == []


def test_a_type_needs_a_second_signal(soccer):
    said = soccer.callouts_in("what a goal")
    # The commentary alone recalls goals in lulls and names substitutions in
    # passing: never a moment on its own.
    assert soccer.classify(said, []) == ("", 0.0)
    assert soccer.classify(said, ["crowd"])[0] == "goal"
    assert soccer.classify([], ["crowd"]) == ("big_moment", pytest.approx(1 / 3))
    assert soccer.classify([], []) == ("", 0.0)


def test_replay_words(soccer):
    assert soccer.replay_said("Let's see it again from another angle")
    assert not soccer.replay_said("he plays it long again? no, short")


# ---- windows -------------------------------------------------------------------


def test_a_goal_keeps_its_build_up_and_celebration(soccer):
    pre, post = soccer.window_of("goal")
    start, end = window(pre, post, 3921, min_len=10, max_len=60, video_end=6000)
    assert (start, end) == (3907, 3931)


def test_a_moment_found_by_the_roar_is_dated_earlier():
    start, end = window(14, 10, 3923.5, min_len=10, max_len=60, video_end=6000, lag=1.5)
    assert (start, end) == (3908, 3932)


def test_too_long_loses_build_up_never_the_moment():
    start, end = window(14, 10, 100, min_len=10, max_len=20, video_end=6000)
    assert end == 110 and start == 90 and start < 100 < end


def test_the_window_stays_inside_the_video():
    assert window(14, 10, 5, min_len=10, max_len=60, video_end=6000)[0] == 0
    start, end = window(14, 10, 5995, min_len=10, max_len=60, video_end=6000)
    assert end == 6000 and start == 5981


def test_goals_and_celebrations_keep_more_after(soccer):
    spec = sports.spec("soccer")
    assert post_extra(spec, "goals_celebrations") == 12
    assert post_extra(spec, "goals") == 0


# ---- one moment, one clip --------------------------------------------------------


def _ev(kind, t, soccer, replay=False, conf=0.7):
    pre, post = soccer.window_of(kind)
    start, end = window(pre, post, t, min_len=10, max_len=60, video_end=7000)
    return SportEvent(kind, t, conf, soccer.importance(kind), start, end, is_replay=replay)


def test_a_goal_and_its_replays_are_one_moment(soccer):
    events = [_ev("goal", 3921, soccer), _ev("big_moment", 3952, soccer, replay=True),
              _ev("big_moment", 3968, soccer, replay=True), _ev("save", 4300, soccer)]
    grouped = group_moments(events, soccer.replay_within)
    assert [e.group for e in grouped] == [1, 1, 1, 2]
    kept = best_per_moment(grouped, lambda e: e.importance)
    assert [(e.type, e.t) for e in kept] == [("goal", 3921), ("save", 4300)]


def test_overlapping_finds_of_one_moment_keep_the_best_type(soccer):
    events = [_ev("big_moment", 3923, soccer), _ev("goal", 3925, soccer)]
    kept = best_per_moment(group_moments(events, 90), lambda e: e.importance)
    assert [e.type for e in kept] == ["goal"]


def test_a_second_goal_soon_after_is_its_own_moment(soccer):
    events = [_ev("goal", 1000, soccer), _ev("goal", 1080, soccer)]
    assert [e.group for e in group_moments(events, 90)] == [1, 2]


def test_an_event_outside_its_window_or_the_video_is_invalid():
    assert valid(SportEvent("goal", 50, start=40, end=60), duration=100)
    assert not valid(SportEvent("goal", 150, start=140, end=160), duration=100)
    assert not valid(SportEvent("goal", 50, start=55, end=60), duration=100)


# ---- what a job keeps -----------------------------------------------------------


def test_all_goals_keeps_only_goals(soccer):
    events = [_ev("goal", 100, soccer), _ev("save", 200, soccer), _ev("own_goal", 300, soccer),
              _ev("yellow_card", 400, soccer)]
    kept, notes = select(events, sports.spec("soccer"), "goals")
    assert [e.type for e in kept] == ["goal", "own_goal"] and notes == []


def test_a_half_filter_never_loses_a_moment_whose_half_is_unknown(soccer):
    first = _ev("goal", 100, soccer)
    first.period = "first_half"
    second = _ev("goal", 4000, soccer)
    second.period = "second_half"
    unknown = _ev("goal", 5000, soccer)
    kept, notes = select([first, second, unknown], sports.spec("soccer"), "goals", "second_half")
    assert [e.t for e in kept] == [4000, 5000]
    assert notes and "without knowing their half" in notes[0]
