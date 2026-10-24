"""Clip selection 2 (scoring.selection: 2, the default) in the scorer, analysis/fusion.py.

Selection 1 (scoring.selection: 1, the choice before selection 2 existed)
builds a standard run's signal-peak windows in time order and keeps the
first max(12, length/300) of each pass, so on a long video the late peaks
never become candidates however strong they are. Every transcript pick
counts as covering its moment, even one the model scored near zero, so a hot
moment it overlaps is never proposed. Selection 2 keeps the strongest peaks
(and hands them on in time order), lets only picks at or above min_score hide
a peak, scores the windows 8 at a time matched by time, and gives a signal
window a few seconds of complete sentences before it. A gaming stream and a
match keep their own path, whatever the setting.

Synthetic signals, transcripts and a fake model. numpy and OpenCV, which
analysis.fusion imports, so these run locally like the other scorer tests.
"""

import hashlib
import json
import re

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from analysis import fusion, highlights  # noqa: E402
from core.models import ClipCandidate, Segment  # noqa: E402

LONG = 1863  # seconds
PEAKS = [60 + 90 * k for k in range(20)]  # 60 s ... 1770 s, each stronger than the one before
STRONGEST = PEAKS[-5:]
SHOWN = re.compile(r"WINDOW \d+ \[([\d.]+)s - ([\d.]+)s\]")
NEUTRAL = "High-energy moment"
REASON = "a synthetic reason for this window, written long enough to read like the one a real model gives"


def noise(n, seed):
    """Noise in [0, 1) from integer arithmetic: the same on every machine and
    every numpy, which a seeded generator does not promise."""
    out, x = [], seed
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        out.append(x / 2147483648)
    return np.array(out)


def late_peaks():
    """1863 s of noise; on the audio side, 20 peaks that grow stronger over time."""
    spike = noise(LONG, 7)
    for k, t in enumerate(PEAKS):
        spike[t:t + 3] = 2.0 + 0.1 * k  # above any noise
    return {"spike": spike}, {"motion": noise(LONG, 11)}


def hot_moment():
    """600 s, one hot moment at 300-310 s on the audio side."""
    spike = np.arange(600) * 1e-6
    spike[300:310] = 1.0
    return {"spike": spike}, {"motion": np.arange(600) * 1e-3}


def chatter(length, text="just chatting about nothing much"):
    return [Segment(start=float(s), end=float(s + 5), text=text) for s in range(0, length, 5)]


def config(selection=None, min_score=40):
    scoring = {"rerank_pool": 0, "read_screen": False, "look_at_game": False}
    if selection is not None:
        scoring["selection"] = selection
    return {
        "clips": {"min_duration": 10, "max_duration": 60, "min_score": min_score, "max_clips_per_video": 0},
        "analysis": {"chunk_seconds": 600, "chunk_overlap_seconds": 30, "long_video_threshold_seconds": 3600,
                     "max_overlap": 0.3, "max_text_similarity": 0.8, "max_segment_reuse": 0.5},
        "scoring": scoring,
        "tracking": {"detector": "yolov8n-pose.pt"},
    }


class Says:
    def __init__(self, answer="{}"):
        self.answer = answer

    def generate(self, *_a, **_k):
        return self.answer


def scorer(monkeypatch, picks):
    """find_clips with the chunk pass, the window scorer and the reaction pass
    stubbed. run(config, segments, signals, **find_clips keywords) returns
    (kept, rejected, asked): asked is each score_windows call's windows and
    its batch/warn keywords."""
    asked = []

    def score_windows(_segments, _llm, wins, **k):
        asked.append((list(wins), {key: k[key] for key in ("batch", "warn") if key in k}))
        return [ClipCandidate(start=a, end=b, score=60, hook="w", source="signal") for a, b in wins]

    monkeypatch.setattr(highlights, "find_highlights", lambda *_a, **_k: (
        [ClipCandidate(start=a, end=b, score=s, hook="h", reason="r") for a, b, s in picks], []))
    monkeypatch.setattr(highlights, "score_windows", score_windows)
    monkeypatch.setattr(fusion, "reaction_for_window", lambda *_a, **_k: 0.5)

    def run(cfg, segs, signals, **kw):
        asked.clear()
        kept, rejected = fusion.find_clips("vod.mp4", segs, Says(), cfg, signals=signals,
                                           measure_reaction=False, **kw)
        return kept, rejected, list(asked)

    return run


def key(clips):
    return sorted((round(float(c.start), 2), round(float(c.end), 2), c.score) for c in clips)


def plain(wins):
    return [(float(a), float(b)) for a, b in wins]


def covers(wins, t):
    return any(a <= t <= b for a, b in wins)


def signal_at(kept, rejected, t):
    """The signal candidates, kept or not, whose clip shows second `t`."""
    everything = kept + [r.candidate for r in rejected]
    return [c for c in everything if c.source == "signal" and c.start <= t <= c.end]


def quarters(wins, length=LONG):
    """How many windows start in each quarter of the video."""
    count = [0, 0, 0, 0]
    for a, _b in wins:
        count[min(3, int(a // (length / 4)))] += 1
    return count


LATE_PICKS = [(0, 30, 62), (400, 430, 64), (800, 830, 61)]
HOT_PICKS = [(0, 30, 62), (100, 130, 64), (290, 320, 5)]

# Selection 1's results, recorded from the unchanged code (5c3fb52) before
# selection 2 existed, faults included: what scoring.selection: 1 gives.
V1_T10_WINDOWS = [
    (32.0, 43.0), (52.0, 63.0), (65.0, 76.0), (87.0, 98.0), (94.0, 105.0), (102.0, 113.0), (110.0, 121.0),
    (116.0, 127.0), (129.0, 140.0), (147.5, 158.5), (158.5, 169.5), (176.5, 187.5), (193.0, 204.0),
    (218.5, 229.5), (236.0, 247.0), (283.0, 294.0), (301.0, 312.0), (326.0, 337.0), (347.0, 358.0),
    (356.0, 367.0), (376.0, 387.0), (382.0, 393.0), (438.0, 449.0), (484.0, 495.0), (74.0, 85.0),
    (203.5, 214.5), (392.0, 403.0), (456.0, 467.0), (469.5, 480.5), (492.0, 503.0), (498.0, 509.0),
    (507.0, 518.0), (527.0, 538.0), (540.0, 551.0), (547.0, 558.0), (559.0, 570.0)
]
V1_T10_KEPT = [(115.0, 140.0, 55)]
V1_T11_WINDOWS = [(540.0, 600.0)]
V1_T11_KEPT = [(290.0, 320.0, 45), (540.0, 600.0, 74)]
V1_PEAK_WINDOWS_SHA256 = "5b093816d11b90e1cdc55900b5790b5d309eacb9d290f58e84e389c9bc29ed11"


# ---- T10: the strongest peaks, wherever they are -------------------------------------


def test_t10_the_strongest_late_peaks_become_candidates(monkeypatch):
    run = scorer(monkeypatch, LATE_PICKS)
    _kept, _rejected, asked = run(config(selection=2), chatter(LONG), late_peaks())
    signal = asked[0][0]
    assert all(covers(signal, t + 1) for t in STRONGEST), signal
    assert min(quarters(signal)) >= 5  # spread over the whole video (selection 1: 27/9/0/0)
    assert asked[0][1] == {"batch": 8, "warn": True}


def test_t10_selection_1_is_unchanged_late_peaks_are_never_candidates(monkeypatch):
    """Selection 1's behaviour, recorded: every pass fills its budget of 12 from the
    start of the video, so none of the five strongest peaks is a candidate."""
    run = scorer(monkeypatch, LATE_PICKS)
    kept, _rejected, asked = run(config(selection=1), chatter(LONG), late_peaks())
    signal = asked[0][0]
    assert plain(signal) == V1_T10_WINDOWS and key(kept) == V1_T10_KEPT
    assert not any(covers(signal, t + 1) for t in STRONGEST)
    assert quarters(signal) == [27, 9, 0, 0]
    assert asked[0][1] == {}  # one request for them all, as always


def test_with_nothing_set_a_standard_run_uses_selection_2(monkeypatch):
    run = scorer(monkeypatch, LATE_PICKS)
    kept, _rejected, asked = run(config(), chatter(LONG), late_peaks())
    kept_2, _r, asked_2 = run(config(selection=2), chatter(LONG), late_peaks())
    assert asked == asked_2 and key(kept) == key(kept_2)
    assert all(covers(asked[0][0], t + 1) for t in STRONGEST)
    assert asked[0][1] == {"batch": 8, "warn": True}


# ---- T11: a pick scored near zero doesn't hide a hot moment ---------------------------


def test_t11_a_near_zero_pick_does_not_hide_the_hot_moment(monkeypatch):
    run = scorer(monkeypatch, HOT_PICKS)
    _kept, _rejected, asked = run(config(selection=2), chatter(600), hot_moment())
    assert covers(asked[0][0], 305)


def test_t11_a_pick_at_the_bar_still_covers_its_moment(monkeypatch):
    """Only picks under min_score stop counting: one at the bar still does."""
    run = scorer(monkeypatch, [*HOT_PICKS[:2], (290, 320, 40)])
    _kept, _rejected, asked = run(config(selection=2), chatter(600), hot_moment())
    assert not covers(asked[0][0], 305)


def test_t11_selection_1_is_unchanged_the_hot_moment_is_hidden(monkeypatch):
    run = scorer(monkeypatch, HOT_PICKS)
    kept, _rejected, asked = run(config(selection=1), chatter(600), hot_moment())
    assert plain(asked[0][0]) == V1_T11_WINDOWS and key(kept) == V1_T11_KEPT
    assert not covers(asked[0][0], 305)


# ---- R7 for a clip direction's windows ---------------------------------------------------


def test_clip_direction_windows_are_scored_in_batches_matched_by_time_too(monkeypatch):
    """The windows a clip direction adds (analysis/intent.py) went to the
    model in one request too: selection 2 scores them like the signal peaks."""
    from analysis import intent

    run = scorer(monkeypatch, HOT_PICKS[:2])
    no_topics = Says(json.dumps({"targets": [], "styles": [], "avoid": []}))

    def direction_call(selection):
        asked_for = intent.parse("more clips from 8:00-9:50", no_topics, 600)
        _kept, _rejected, asked = run(config(selection=selection), chatter(600), hot_moment(),
                                      intent=asked_for)
        return asked[-1]  # the direction's windows are scored after the signal peaks

    windows_2, options_2 = direction_call(2)
    windows_1, options_1 = direction_call(1)
    assert windows_2 and all(480 <= a and b <= 590 for a, b in windows_2)
    assert options_2 == {"batch": 8, "warn": True}
    assert windows_1 and options_1 == {}  # selection 1: one request, as always
    assert direction_call(None) == (windows_2, options_2)  # nothing set: selection 2


# ---- T13 through the scorer: a signal window's setup ---------------------------------


def test_a_signal_window_gets_its_setup_through_the_scorer(monkeypatch):
    """POLICY (see tests/test_selection_fit.py, T13): with selection 2 the hot
    moment's clip starts on the two short sentences before it."""
    segs = chatter(290) + [
        Segment(290.0, 294.0, "Alright here is the real test."),
        Segment(294.5, 297.0, "Watch this."),
        Segment(297.5, 299.5, "Three, two, one."),
        Segment(300.5, 304.0, "HAHAHA no way!"),
        Segment(305.0, 312.0, "It actually worked, I cannot believe it."),
        Segment(313.0, 320.0, "Okay let me try it again."),
        Segment(321.0, 327.0, "This time it is going to be even louder."),
    ] + [s for s in chatter(600) if s.start >= 330]
    run = scorer(monkeypatch, HOT_PICKS[:2])
    kept, rejected, _asked = run(config(selection=2), segs, hot_moment())
    moment = signal_at(kept, rejected, 305)
    assert moment and moment[0].start == 294.5
    kept, rejected, _asked = run(config(selection=1), segs, hot_moment())
    moment = signal_at(kept, rejected, 305)
    assert moment and moment[0].start == 300.5  # selection 1: from the laugh


# ---- T8 through the scorer: no silent neutral 50s -----------------------------------------


class WindowModel:
    """Answers every window a prompt shows, in order, cut off at 4096
    characters; anything else (the rerank) gets an answer it can't use."""

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, *, json_mode=False):
        shown = [(float(a), float(b)) for a, b in SHOWN.findall(prompt)]
        if not shown:
            return "{}"
        self.prompts.append(shown)
        clips = [{"start": a, "end": b, "score": 61 + int(a) % 37, "engagement": 60,
                  "hook": f"what happens at {a:.0f} seconds",
                  "reason": REASON}
                 for a, b in shown]
        return json.dumps({"clips": clips})[:4096]


def scored(monkeypatch, selection):
    monkeypatch.setattr(highlights, "find_highlights", lambda *_a, **_k: (
        [ClipCandidate(start=a, end=b, score=s, hook="h", reason="r") for a, b, s in LATE_PICKS], []))
    monkeypatch.setattr(fusion, "reaction_for_window", lambda *_a, **_k: 0.5)
    model = WindowModel()
    kept, rejected = fusion.find_clips("vod.mp4", chatter(LONG), model, config(selection=selection),
                                       signals=late_peaks(), measure_reaction=False)
    signal = [c for c in kept + [r.candidate for r in rejected] if c.source == "signal"]
    return signal, model.prompts


def test_t8_through_the_scorer_no_signal_window_is_a_silent_50(monkeypatch, capsys):
    signal, prompts = scored(monkeypatch, 2)
    assert len(signal) > 8 and max(len(p) for p in prompts) <= 8
    assert not any(c.subscores["text"] == 50 or c.hook == NEUTRAL for c in signal)
    assert "Warning" not in capsys.readouterr().out


def test_t8_through_the_scorer_selection_1_is_unchanged(monkeypatch):
    """Selection 1's behaviour: one request for all the windows (and its retry), cut
    off, so every signal window is a neutral 50."""
    signal, prompts = scored(monkeypatch, 1)
    assert len(prompts) == 2 and len(prompts[0]) == len(signal) > 20
    assert all(c.subscores["text"] == 50 and c.hook == NEUTRAL for c in signal)


# ---- the windows themselves: selection 1 exactly, selection 2's budget -------------------


def curves():
    """Synthetic curves of every shape for _signal_peak_windows: noise with
    and without peaks, long and short, a hot run at the very end."""
    out = []
    for seed, n in ((3, 600), (5, 1863), (9, 3600), (13, 7200)):
        x = noise(n, seed)
        for k in range(0, n, 97):
            x[k:k + 2 + k % 7] = 1.0 + (k % 13) / 13.0
        out.append(x)
    ramp = np.arange(900) * 1e-3
    ramp[880:] = 2.0
    out.append(ramp)
    return out


def peak_windows(**kw):
    got = []
    for x in curves():
        for pct in (85, 90):
            for lo, hi in ((10.0, 60.0), (61.0, 180.0)):
                segs = chatter(int(x.size) - 30)
                existing = [ClipCandidate(start=s, end=s + 30.0, score=50)
                            for s in range(100, int(x.size) - 200, 400)]
                got.append([list(w) for w in fusion._signal_peak_windows(
                    fusion._pct(x), segs, percentile=pct, min_duration=lo, max_duration=hi,
                    existing=existing, **kw)])
    return got


def test_selection_1_peak_windows_are_exactly_as_before():
    got = peak_windows()
    assert hashlib.sha256(json.dumps(got).encode("utf-8")).hexdigest() == V1_PEAK_WINDOWS_SHA256


def test_selection_2_spends_the_same_budget_on_the_strongest():
    # peak_windows() gives four results per curve: two percentiles, two duration ranges
    each = [y for x in curves() for y in [x] * 4]
    for before, after, x in zip(peak_windows(), peak_windows(strongest_first=True), each):
        assert len(after) == len(before) and after == sorted(after)
        budget = max(12, x.size // 300)
        assert len(after) <= budget


# ---- a gaming stream and a match keep their own path ---------------------------------------


def test_a_gaming_stream_is_scored_the_same_with_selection_2(monkeypatch):
    from analysis import gaming

    run = scorer(monkeypatch, LATE_PICKS)
    profile = gaming.profile_for({"clips": {"gaming_scoring": True}}, [{"name": "VALORANT"}], "")
    one = run(config(selection=1), chatter(LONG), late_peaks(), gaming=profile)
    two = run(config(selection=2), chatter(LONG), late_peaks(), gaming=profile)
    unset = run(config(), chatter(LONG), late_peaks(), gaming=profile)
    assert two[2] == one[2] == unset[2] and key(two[0]) == key(one[0]) == key(unset[0])
    assert all(k == {"batch": 8} for _w, k in one[2])  # the gaming path's own batches, no warning


def test_a_match_is_scored_the_same_with_selection_2(monkeypatch):
    pytest.importorskip("yaml")
    import sports

    run = scorer(monkeypatch, [(0, 30, 62), (100, 130, 64), (440, 470, 58)])

    def match(selection):
        cfg = config(selection=selection)
        cfg["clips"]["sport"] = {"name": "soccer", "highlights": "best"}
        sport = sports.profile_for(cfg)
        crowd = np.zeros(600, dtype=np.float32)
        crowd[452:460] = 0.9
        sport.curves = {"crowd": crowd, "whistle": np.zeros(600, dtype=np.float32)}
        segs = chatter(600, "passing it around the back")
        segs[90] = Segment(start=450.0, end=455.0, text="GOOOAL! what a goal, into the back of the net")
        return run(cfg, segs, hot_moment(), sport=sport)

    one, two, unset = match(1), match(2), match(None)
    assert two[2] == one[2] == unset[2] and key(two[0]) == key(one[0]) == key(unset[0])
