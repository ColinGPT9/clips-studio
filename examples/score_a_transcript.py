"""Score a transcript and print what the app would pick — no video needed.

The fastest loop there is for working on clip selection. Transcribing a
stream takes minutes and rendering takes longer; this takes seconds, because
it skips both and starts from a transcript that already exists.

    python examples/score_a_transcript.py                 # needs Ollama
    python examples/score_a_transcript.py --fake          # needs nothing
    python examples/score_a_transcript.py --transcript data/transcripts/abc123.json

`--fake` swaps in a canned model reply (see fake_backend.py), which makes
the run deterministic. Use it when you are changing the selection logic
around the model rather than the prompt itself.

Point `--transcript` at any cached transcript under `data/transcripts/` to
re-score a real stream you have already processed, without touching it.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.highlights import find_highlights  # noqa: E402
from core.models import Segment  # noqa: E402

DEFAULT_TRANSCRIPT = ROOT / "tests" / "assets" / "sample_transcript.json"


def load(path: Path) -> list[Segment]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Segment(**seg) for seg in data["segments"]]


def timestamp(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, default=DEFAULT_TRANSCRIPT)
    parser.add_argument("--fake", action="store_true", help="canned reply instead of a real model")
    parser.add_argument("--max-clips", type=int, default=3)
    parser.add_argument("--min-score", type=int, default=60)
    args = parser.parse_args()

    if not args.transcript.exists():
        print(f"No such transcript: {args.transcript}")
        return 1

    segments = load(args.transcript)
    print(f"{args.transcript.name}: {len(segments)} segments, {timestamp(segments[-1].end)} long\n")

    if args.fake:
        from examples.fake_backend import FakeBackend

        llm = FakeBackend()
    else:
        import yaml

        from llm.registry import create_backend

        settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
        try:
            llm = create_backend(settings["llm"])
        except Exception as exc:
            # Almost always "Ollama is not running" — worth saying plainly,
            # because --fake genuinely is a fine answer for most work here.
            print(f"Could not reach the model: {exc}\nTry --fake for a run with no model.")
            return 1

    clips, rejected = find_highlights(
        segments, llm, max_clips=args.max_clips, min_score=args.min_score
    )

    print(f"\nSelected {len(clips)}:\n")
    for i, c in enumerate(clips, 1):
        print(f"  {i}. [{timestamp(c.start)}-{timestamp(c.end)}] {c.score}  {c.hook}")
        print(f"     {c.reason}\n")

    # The rejections are the more useful half when tuning: a clip you expected
    # and did not get will be in here with the reason it was dropped.
    if rejected:
        print(f"Rejected {len(rejected)}:\n")
        for r in rejected:
            c = r.candidate
            print(f"  [{timestamp(c.start)}-{timestamp(c.end)}] {c.score}  {r.reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
