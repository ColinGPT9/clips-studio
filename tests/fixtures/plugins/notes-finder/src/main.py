"""Test plugin: finds two moments and says what happens in the first.

Its notes hold a link and, in `raw` mode, newlines and a tab: Clips Kitty
takes those out itself before a note reaches a clip. `raw` writes
result.json by hand, as a plugin without the SDK would.
"""

import json

from clipskitty_sdk import run

RAW = {"plugin_api": 1, "ranges": [
    {"start": 10, "end": 40, "score": 80, "label": "quark_burst", "title": "First quark burst",
     "context": ["First quark burst\nof the match", "The replay is at https://example.com/clips/1\tright now",
                 "www.example.com/only-a-link"]},
    {"start": 100, "end": 130, "score": 70, "label": "quiet"}]}


def main(job):
    (job.output_dir / "seen.json").write_text(json.dumps({"job": job.data}), encoding="utf-8")
    if job.settings.get("mode") == "raw":
        (job.folder / "result.json").write_text(json.dumps(RAW), encoding="utf-8")
        job._finished = True
        return
    burst = job.add_range(10, 40, score=80, label="quark_burst", title="First quark burst")
    job.understand(burst, "First quark burst of the match")
    job.understand(burst, "The replay is at https://example.com/clips/1 right now")
    job.add_range(100, 130, score=70, label="quiet")


if __name__ == "__main__":
    run(main)
