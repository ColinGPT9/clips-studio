"""Clip selection 2 (scoring.selection: 2, the default): scoring a standard
run's windows.

Selection 1 (scoring.selection: 1, the choice before selection 2 existed)
asks the model about every signal-peak window of a standard run in one
request. A local model's answer stops at its output
budget (Ollama's num_predict is 1024 tokens, about 4096 characters), so with
36 windows the JSON never closes, the retry stops the same way, and every
window silently falls back to a neutral 50. A partial answer is matched by
position, so a hook can land on another window's clip. Selection 2 asks about
8 windows at a time and matches each answer to its window by time, as a
gaming stream's windows already were, and says so when a window is left at
the neutral 50.

Synthetic windows and a fake model only. CI-light: nothing heavier than
analysis.highlights and core.models is imported.
"""

import json
import re

from analysis import highlights
from core.models import ClipCandidate, Segment

SHOWN = re.compile(r"WINDOW \d+ \[([\d.]+)s - ([\d.]+)s\]")
BUDGET = 4096  # characters: about what num_predict 1024 lets a local model write
REASON = "a synthetic reason for this window, written long enough to read like the one a real model gives"
NEUTRAL = "High-energy moment"


def windows(count=36, first=30.0, every=50.0, length=25.0):
    return [(first + every * i, first + every * i + length) for i in range(count)]


def segments_for(wins):
    return [Segment(start=a, end=a + 5.0, text=f"synthetic line number {i} said in this window")
            for i, (a, _b) in enumerate(wins)]


def score_for(start):
    """The fake model's score for the window starting at `start`: 61-97, never 50."""
    return 61 + int(start) % 37


def hook_for(start):
    return f"what happens at {start:.0f} seconds"


class Model:
    """Answers the windows a prompt shows, cut off at `limit` characters the
    way a local model's output budget cuts it. `pick` chooses which of them it
    answers and in what order: all of them, in the order shown, by default."""

    def __init__(self, limit=BUDGET, pick=None):
        self.limit = limit
        self.pick = pick or (lambda shown: shown)
        self.prompts = []  # the windows each prompt showed

    def generate(self, prompt, *, json_mode=False):
        shown = [(float(a), float(b)) for a, b in SHOWN.findall(prompt)]
        self.prompts.append(shown)
        clips = [{"start": a, "end": b, "score": score_for(a), "engagement": 60,
                  "hook": hook_for(a), "reason": REASON} for a, b in self.pick(shown)]
        return json.dumps({"clips": clips})[: self.limit]


def outcome(got):
    return [(c.start, c.end, c.score, c.hook) for c in got]


def skip_the_third_and_reverse(shown):
    return [w for i, w in enumerate(shown) if i != 2][::-1]


# Selection 1's answers, recorded from the unchanged code (5c3fb52) before
# selection 2 existed, faults included: what scoring.selection: 1 gives.
V1_T9 = [
    (30.0, 55.0, 82, "what happens at 280 seconds"), (80.0, 105.0, 69, "what happens at 230 seconds"),
    (130.0, 155.0, 93, "what happens at 180 seconds"), (180.0, 205.0, 67, "what happens at 80 seconds"),
    (230.0, 255.0, 91, "what happens at 30 seconds"), (280.0, 305.0, 50, "High-energy moment")
]


# ---- the setting -------------------------------------------------------------


def test_selection_is_2_when_the_settings_do_not_set_it():
    # settings.yaml as it ships has a scoring block without the key.
    assert highlights.selection_version({"scoring": {"rerank_pool": 8}}) == 2
    assert highlights.selection_version({"scoring": {}}) == 2
    assert highlights.selection_version({"scoring": None}) == 2   # `scoring:` with nothing under it
    assert highlights.selection_version({}) == 2
    assert highlights.selection_version(None) == 2


def test_set_it_is_2_only_when_it_says_2_and_1_turns_it_off():
    for two in (2, 2.0, "2", " 2 "):
        assert highlights.selection_version({"scoring": {"selection": two}}) == 2, two
    for one in (1, 1.0, "1"):
        assert highlights.selection_version({"scoring": {"selection": one}}) == 1, one
    # Any value it doesn't know is read as it always was: 1, clips chosen the old way. A blank
    # (`selection:` with nothing after it) is one of them.
    for odd in (None, 0, 3, "two", "", [], {}, True, False):
        assert highlights.selection_version({"scoring": {"selection": odd}}) == 1, odd


def test_selection_1_adds_no_keywords_to_any_call():
    """Every call selection 2 changes only gains these keywords, and for
    selection 1 there are none: its calls are exactly as before."""
    hooked = ClipCandidate(start=10.0, end=40.0, score=70, hook="a hook from the transcript")
    assert highlights.window_options(1) == {}
    assert highlights.start_rules(1, hooked) == {}
    assert highlights.start_rules(1, hooked, lead_in=highlights.LEAD_IN_SECONDS) == {}
    assert highlights.window_options(2) == {"batch": 8, "warn": True}
    assert highlights.start_rules(2, hooked) == {"keep_own_start": True, "hook": "a hook from the transcript",
                                                "lead_in": 0.0, "sentence_reach": 8.0}
    # The placeholder a window gets when the model says nothing isn't a hook.
    neutral = ClipCandidate(start=10.0, end=40.0, score=50, hook=NEUTRAL, source="signal")
    assert highlights.start_rules(2, neutral, lead_in=6.0) == {"keep_own_start": True, "hook": "",
                                                               "lead_in": 6.0, "sentence_reach": 8.0}


# ---- T8: 36 windows and an output budget ----------------------------------------


def test_t8_36_windows_are_asked_8_at_a_time_and_none_falls_back_to_50(capsys):
    wins = windows()
    model = Model()
    got = highlights.score_windows(segments_for(wins), model, wins, **highlights.window_options(2))
    assert len(model.prompts) == 5 and max(len(p) for p in model.prompts) <= 8
    assert sorted(w for p in model.prompts for w in p) == wins  # each window asked once
    assert outcome(got) == [(a, b, score_for(a), hook_for(a)) for a, b in wins]
    assert "Warning" not in capsys.readouterr().out


def test_t8_selection_1_is_unchanged_one_request_cut_off_and_all_50(capsys):
    """Selection 1's behaviour, recorded: one request shows all 36 windows (and its
    retry the same), the answer is cut off, and every window is a silent 50."""
    wins = windows()
    model = Model()
    got = highlights.score_windows(segments_for(wins), model, wins, **highlights.window_options(1))
    assert [len(p) for p in model.prompts] == [36, 36]
    assert outcome(got) == [(a, b, 50, NEUTRAL) for a, b in wins]
    assert "Warning" not in capsys.readouterr().out


def test_t8_a_batch_the_model_cut_off_is_a_warning_not_a_silent_50(capsys):
    wins = windows(count=20)
    got = highlights.score_windows(segments_for(wins), Model(limit=600), wins, **highlights.window_options(2))
    assert outcome(got) == [(a, b, 50, NEUTRAL) for a, b in wins]  # the windows are still kept
    said = capsys.readouterr().out
    assert said.count("Warning") == 3 and "couldn't be read" in said


def test_t8_the_warning_spans_the_windows_whatever_their_order(capsys):
    """Fusion hands the windows over signal by signal, not in time order, so
    the first window's start and the last one's end could make a span that
    runs backwards (216-167s). The warning gives the earliest start and the
    latest end instead, and the windows keep the order they came in."""
    wins = [(216.0, 241.0), (30.0, 55.0), (142.0, 167.0)]
    got = highlights.score_windows(segments_for(wins), Model(limit=10), wins, **highlights.window_options(2))
    assert outcome(got) == [(a, b, 50, NEUTRAL) for a, b in wins]
    said = capsys.readouterr().out
    assert "3 window(s) (30-241s) couldn't be read" in said and "216-167s" not in said


# ---- T9: an answer that skips and reverses ---------------------------------------


def test_t9_each_hook_is_matched_to_its_own_window_by_time(capsys):
    wins = windows(count=6)
    got = highlights.score_windows(segments_for(wins), Model(pick=skip_the_third_and_reverse), wins,
                                   **highlights.window_options(2))
    for (a, b), c in zip(wins, got):
        assert (c.start, c.end) == (a, b)
        if a == wins[2][0]:
            assert (c.score, c.hook) == (50, NEUTRAL)  # the one it skipped
        else:
            assert (c.score, c.hook) == (score_for(a), hook_for(a))
    said = capsys.readouterr().out
    assert "Warning" in said and "1 of 6" in said and f"{wins[2][0]:.0f}s" in said


def test_t9_selection_1_is_unchanged_matched_by_position():
    """Selection 1's behaviour, recorded: answers matched by their place in the
    list, so after the skip and the reversal the hooks land on other windows."""
    wins = windows(count=6)
    got = highlights.score_windows(segments_for(wins), Model(pick=skip_the_third_and_reverse), wins)
    assert outcome(got) == V1_T9
    assert not any(c.hook == hook_for(c.start) for c in got)  # every hook on another window
