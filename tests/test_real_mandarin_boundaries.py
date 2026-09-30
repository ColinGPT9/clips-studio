"""An offline regression fixture derived from natural public-domain speech.

The adjacent JSON records provenance, actual ASR output and known limitations.
No model or media download is performed when this test runs.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.highlights import _fit_to_segments  # noqa: E402
from core.models import ClipCandidate, Segment  # noqa: E402


def test_real_mandarin_article7_drops_the_unfinished_article8_clause():
    fixture = json.loads(Path(__file__).with_name("real_mandarin_article7.json").read_text(encoding="utf8"))
    segments = [Segment(**s) for s in fixture["segments"]]
    params = fixture["candidate"]
    clip = _fit_to_segments(
        ClipCandidate(params["start"], params["end"], params["score"]),
        segments,
        params["min_duration"],
        params["max_duration"],
    )
    assert (clip.start, clip.end) == (fixture["expected_after"]["start"], fixture["expected_after"]["end"])
