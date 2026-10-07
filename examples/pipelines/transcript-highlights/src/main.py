"""Clips Kitty's transcript scorer as a contract pipeline (first-party adapter).

It reads the job with the SDK like any plugin, then calls the scorer the app
already has. Because it imports Clips Kitty's own modules, it needs a source
checkout: CLIPSKITTY_SOURCE, or the checkout this file sits in.
"""

import os
import sys
from pathlib import Path

from clipskitty_sdk import run

SOURCE = Path(os.environ.get("CLIPSKITTY_SOURCE") or Path(__file__).resolve().parents[4])


def main(job):
    if not (SOURCE / "analysis" / "highlights.py").exists():
        job.fail(f"this adapter needs a Clips Kitty source checkout; none at {SOURCE}")
    sys.path.insert(0, str(SOURCE))
    from analysis.highlights import find_highlights
    from core.models import Segment

    if job.transcript is None:
        job.fail("no transcript was handed over (the manifest asks for transcript.read)")
    segments = [Segment(s["start"], s["end"], s["text"], s.get("words")) for s in job.transcript.segments()]
    job.progress(0.1, f"Scoring {len(segments)} transcript segments")

    if job.settings.get("fake_model"):
        from examples.fake_backend import FakeBackend

        llm = FakeBackend()
    else:
        from llm.registry import create_backend

        ollama = job.tools.ollama or {}
        if not ollama.get("model"):
            job.fail("no local model is chosen in Clips Kitty (Settings → AI), and this adapter only uses a local one")
        llm = create_backend({"backend": f"ollama/{ollama['model']}", "ollama_host": ollama["host"]})

    limits = job.limits
    clips, _rejected = find_highlights(
        segments, llm,
        min_score=int(job.settings.get("min_score", 60)),
        max_clips=limits.max_clips or 3,
        min_duration=limits.min_duration or 10.0,
        max_duration=limits.max_duration or 60.0,
    )
    for c in clips:
        job.add_range(c.start, c.end, score=c.score, label="highlight", title=c.hook, reason=c.reason)
    job.progress(1.0, f"{len(clips)} moment(s)")


if __name__ == "__main__":
    run(main)
