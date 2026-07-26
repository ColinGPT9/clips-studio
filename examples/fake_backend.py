"""A deterministic stand-in for the LLM, so scoring can be run without one.

Two uses.

**Working on scoring without Ollama.** Pulling a model is gigabytes and a
wait. If what you are changing is the selection logic in
`analysis/highlights.py` or `analysis/fusion.py` — dedup, overlap, ranking,
the parsing of the model's reply — you do not need a real model at all. You
need *a* reply, in the right shape, every time.

**Making a failure reproducible.** A real model gives a different answer
each run, so "it picks a bad clip sometimes" is close to impossible to pin
down. Hand this a fixed reply and the pipeline becomes a pure function of
its input, which is what a bug report needs.

    from examples.fake_backend import FakeBackend

    llm = FakeBackend(reply='{"clips": [{"start": 36, "end": 50, "score": 88}]}')

This is not a mock of the pipeline — everything downstream is the real code.
It replaces exactly one thing: the network call to the model.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm.base import LLMBackend  # noqa: E402

# Scores the moments a person would pick out of tests/assets/sample_transcript.json:
# the gym punchline and the surprise donation.
#
# Shape matters. `analysis.highlights._parse_clips_json` wants a JSON OBJECT
# with a "clips" list — a bare array parses as valid JSON and is then thrown
# away, which shows up as "unparseable LLM output, skipping chunk".
DEFAULT_REPLY = """{"clips": [
  {"start": 19.0, "end": 46.7, "score": 87, "engagement": 90,
   "hook": "A stranger's mum thinks he needs to eat more",
   "reason": "Setup, turn and punchline inside one self-contained story."},
  {"start": 70.3, "end": 87.6, "score": 79, "engagement": 82,
   "hook": "Tries to give back a $50 donation",
   "reason": "Genuine unscripted reaction; the refusal is the memorable part."},
  {"start": 95.7, "end": 116.6, "score": 68, "engagement": 64,
   "hook": "Why he stopped posting for three months",
   "reason": "Honest and relatable, but it is talking rather than happening."}
]}"""


class FakeBackend(LLMBackend):
    """Returns a canned reply and records every prompt it was given."""

    def __init__(self, reply: str = DEFAULT_REPLY, name: str = "fake"):
        self.reply = reply
        self._name = name
        # Kept because it is usually the interesting half: when scoring goes
        # wrong the cause is often what the prompt said, not what came back.
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        # A property, not an attribute: LLMBackend declares it as one, and
        # assigning over an inherited property raises at construction time.
        return self._name

    def generate(self, prompt: str, *, json_mode: bool = False) -> str:
        self.prompts.append(prompt)
        return self.reply
