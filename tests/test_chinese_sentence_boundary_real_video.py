"""A CC BY 3.0 natural-video regression; attribution is in the adjacent JSON.

The small fixture preserves raw ASR text and source-absolute timings. No media
or model download is required. It asserts unchanged behavior, not clip quality.
"""

import json
from pathlib import Path

from analysis.highlights import _ends_sentence, _fit_to_segments
from core.models import ClipCandidate, Segment


def test_unpunctuated_real_mandarin_keeps_existing_boundaries():
    fixture = json.loads(
        Path(__file__).with_name("chinese_sentence_boundary_real_video.json").read_text(encoding="utf8")
    )
    segments = [Segment(**s) for s in fixture["segments"]]
    assert not any(_ends_sentence(s) for s in segments)
    params = fixture["candidate"]
    clip = _fit_to_segments(
        ClipCandidate(params["start"], params["end"], params["score"]),
        segments,
        params["min_duration"],
        params["max_duration"],
    )
    assert [clip.start, clip.end] == fixture["expected_after"]
    assert fixture["expected_after"] == fixture["observed_before"]
