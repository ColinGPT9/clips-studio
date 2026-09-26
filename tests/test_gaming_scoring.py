"""Gaming / Split-Screen scoring: the reaction channel is left neutral.

"A person on screen, being emphasised" reads a game's characters as people
and a top-down game as nobody at all; that zero is how top-down games once
got no clips. With the toggle off, reactions are measured exactly as before.
"""

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from analysis import fusion, highlights  # noqa: E402
from core.models import ClipCandidate, Segment  # noqa: E402

CONFIG = {
    "clips": {"min_duration": 10, "max_duration": 60, "min_score": 0, "max_clips_per_video": 0},
    "analysis": {"chunk_seconds": 600, "chunk_overlap_seconds": 30, "long_video_threshold_seconds": 3600,
                 "max_overlap": 0.3, "max_text_similarity": 0.8, "max_segment_reuse": 0.5},
    "scoring": {"rerank_pool": 0},
    "tracking": {"detector": "yolov8n-pose.pt"},
}


class NoLLM:
    def generate(self, *_a, **_k):
        raise RuntimeError("no model in this test")


@pytest.fixture
def measured(monkeypatch):
    windows = []
    picks = [ClipCandidate(start=s, end=s + 30, score=70, hook="h", reason="r") for s in (0, 60, 120)]
    monkeypatch.setattr(highlights, "find_highlights", lambda *_a, **_k: (list(picks), []))
    monkeypatch.setattr(highlights, "score_windows", lambda *_a, **_k: [])
    monkeypatch.setattr(fusion, "reaction_for_window", lambda _p, s, e, **_k: windows.append(s) or 0.0)
    return windows


def _find(**kwargs):
    segments = [Segment(start=float(s), end=float(s + 5), text="and then it all went wrong") for s in range(0, 180, 5)]
    flat = {"spike": np.zeros(180), "motion": np.zeros(180)}
    return fusion.find_clips("vod.mp4", segments, NoLLM(), CONFIG, signals=(flat, flat), **kwargs)


def test_reactions_are_measured_as_before_without_the_toggle(measured):
    _find()
    assert sorted(measured) == [0, 60, 120]


def test_in_gaming_every_clip_keeps_a_neutral_reaction(measured):
    kept, _rejected = _find(measure_reaction=False)
    assert measured == []
    assert kept and all(c.subscores["reaction"] == 50 for c in kept)
    assert not any(c.subscores.get("reaction_measured") for c in kept)
