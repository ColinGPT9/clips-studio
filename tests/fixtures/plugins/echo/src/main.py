"""Test plugin: records what it was handed, then does what `mode` says."""

import json
import os
import sys
import time

from clipskitty_sdk import run


def main(job):
    seen = {
        "job": job.data,
        "cwd": os.getcwd(),
        "env": dict(os.environ),
        "secret": job.secret("api_key"),
        "transcript": job.transcript.segments() if job.transcript else None,
        "models": {name: {"path": str(m.path) if m.path else None, "files": m.files, "source": m.source,
                          "id": m.id, "revision": m.revision} for name, m in job.models.items()},
    }
    (job.output_dir / "seen.json").write_text(json.dumps(seen), encoding="utf-8")
    mode = job.settings.get("mode", "ok")
    if mode == "fail":
        job.fail("the kill feed could not be read")
    if mode == "crash":
        raise RuntimeError("boom inside the plugin")
    if mode == "bad":
        (job.folder / "result.json").write_text('{"plugin_api": 1, "ranges": [{"start": 9, "end": 3}]}')
        job._finished = True
        return
    if mode == "silent":
        job._finished = True
        return
    if mode == "sleep":
        time.sleep(120)
    if mode == "noisy":
        print("a plain print from the plugin", flush=True)
        print("something on standard error", file=sys.stderr, flush=True)
    job.progress(0.25, "a quarter")
    job.progress(0.75, "three quarters")
    for item in filter(None, (job.settings.get("ranges") or "").split(",")):
        span, _, score = item.partition(":")
        start, end = (float(x) for x in span.split("-"))
        job.add_range(start, end, score=float(score) if score else None, label="moment",
                      title=f"Moment at {start:g}", reason="asked for in the test")


if __name__ == "__main__":
    run(main)
