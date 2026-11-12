"""Every prompt a run sends about one video, recorded (editor suite step 4).

record(folder) runs the real clip finder (analysis/fusion.py find_clips: the
chunk prompts, the window prompts, the ranking prompt) and the real title,
description and hashtag writer (analysis/metadata.py) with a model that only
writes down what it is asked, for one video, three times:

- "no_creator": the video has no creator;
- "nothing_learned": a creator the app has learned nothing about;
- "learned": a creator with a storyline, a series, a catchphrase and a
  collaborator learned.

For each it returns the prompts in the order they were sent. Each creator is
used as the app made it: nothing is written in "About this creator" unless
`about` is given.

It only uses what the app had before "About this creator" existed, so the
same file, run against that version of the app, recorded
tests/fixtures/creator_note/prompts_before.json
(tests/test_creator_description.py compares with it).
"""

import json
from pathlib import Path

SCENARIOS = ("no_creator", "nothing_learned", "learned")

CONFIG = {
    "clips": {"min_duration": 10, "max_duration": 60, "min_score": 40, "max_clips_per_video": 0},
    "analysis": {"chunk_seconds": 600, "chunk_overlap_seconds": 30,
                 "long_video_threshold_seconds": 3600, "max_overlap": 0.3,
                 "max_text_similarity": 0.8, "max_segment_reuse": 0.5},
    "scoring": {"rerank_pool": 8},
    "tracking": {"detector": "yolov8n-pose.pt"},
}


class Recorder:
    """A model that writes down each prompt and answers enough to go on."""

    name = "recorder"

    def __init__(self):
        self.prompts: list[str] = []

    def generate(self, prompt, **_kwargs):
        self.prompts.append(prompt)
        if "TRANSCRIPT:" in prompt:
            return json.dumps({"clips": [
                {"start": 100.0, "end": 130.0, "score": 80, "engagement": 70, "trending": False,
                 "hook": "we are so back", "reason": "a strong moment"},
                {"start": 450.0, "end": 480.0, "score": 70, "engagement": 60, "trending": False,
                 "hook": "the raid went wild", "reason": "a story with a payoff"},
            ]})
        if "CANDIDATES:" in prompt:
            return json.dumps({"order": [1, 0, 2, 3, 4, 5, 6, 7]})
        return "{}"     # windows stay at a neutral 50; titles fall back to the hook


def segments():
    from core.models import Segment

    segs = [Segment(start=float(s), end=float(s + 5), text="just chatting about nothing much")
            for s in range(0, 600, 5)]
    for s in segs:
        if 100 <= s.start < 130:
            s.text = "we are so back, the marathon stream starts today with the crew"
        if 450 <= s.start < 480:
            s.text = "we did the raid last night and it was wild"
        if 300 <= s.start < 310:
            s.text = "haha that was hilarious, I can't stop laughing"
    return segs


def _creator(db, name: str) -> int:
    cur = db.conn.execute(
        "INSERT INTO creators (display_name, aliases, created_at) VALUES (?, '[]', '2026-01-01T00:00:00')",
        (name,),
    )
    db.conn.commit()
    return cur.lastrowid


def _teach(db, creator_id: int) -> None:
    """A storyline, a series, a usual topic, a catchphrase heard often and a collaborator."""
    now = "2026-01-02T00:00:00"
    db.conn.execute(
        "INSERT INTO creator_events (creator_id, event_name, description, status, detected_date)"
        " VALUES (?, 'Marathon stream', 'a 24 hour charity stream', 'in_progress', ?)",
        (creator_id, now),
    )
    for kind, info, seen in (("series", "Friday raid nights", 1), ("topic", "fantasy games", 1),
                             ("catchphrase", "we are so back - said when things go well", 5),
                             ("collaborator", "The Crew - the regular squad", 1)):
        db.conn.execute(
            "INSERT INTO creator_knowledge (creator_id, knowledge_type, information, confidence,"
            " created_at, times_seen, last_seen) VALUES (?, ?, ?, 'high', ?, ?, ?)",
            (creator_id, kind, info, now, seen, now),
        )
    db.conn.commit()


def _prompts(context) -> list[str]:
    """One video's prompts with this creator context (None: no creator)."""
    import numpy as np

    from analysis import fusion
    from analysis.metadata import generate_metadata_batch

    llm = Recorder()
    audio = np.zeros(600)
    audio[300:310] = 1.0        # the laughing: a window found by its sound
    signals = ({"spike": audio}, {"motion": np.zeros(600)})
    real = fusion.reaction_for_window
    fusion.reaction_for_window = lambda *_a, **_k: 0.5      # no video is read
    try:
        kept, _rejected = fusion.find_clips("vod.mp4", segments(), llm, json.loads(json.dumps(CONFIG)),
                                            signals=signals, creator_context=context)
    finally:
        fusion.reaction_for_window = real
    generate_metadata_batch(kept, segments(), "Friday stream", llm,
                            creator_context=(context.summary if context else ""))
    return llm.prompts


def record(folder: Path, about: str | None = None) -> dict:
    """{scenario: [prompt, ...]}. `about`: text saved as each creator's
    "About this creator" before the run (only where the app has the box)."""
    from core.state import StateDB
    from creator import retrieval

    db = StateDB(Path(folder) / "state.db")
    try:
        quiet = _creator(db, "Quiet Creator")
        known = _creator(db, "Known Creator")
        _teach(db, known)
        if about is not None:
            for creator_id in (quiet, known):
                db.set_creator_description(creator_id, about)
        return {
            "no_creator": _prompts(None),
            "nothing_learned": _prompts(retrieval.context_for(db, quiet)),
            "learned": _prompts(retrieval.context_for(db, known)),
        }
    finally:
        db.conn.close()
