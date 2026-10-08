"""Test plugin: suggests edits for the clips it is handed, as its settings say.

`edits` is JSON: an edit for each moment id, or for `*` (every clip), with
the times of its cuts and mutes counted from the clip's start, so one edit
fits every clip. The answer is written as the plugin gives it; Clips Kitty
fits it. `mode` makes it fail, write an answer Clips Kitty can't use (a speed
of 9), or run until it is stopped. With `trace`, each run adds one line to
that file: the plugin, its steps, the clips it was handed and its limits.
"""

import json
import time

from clipskitty_sdk import run


def _edit_for(spec: dict, start: float) -> dict:
    edit = dict(spec)
    for key in ("cuts", "mutes"):
        if key in edit:
            edit[key] = [[start + a, start + b] for a, b in edit[key]]
    return edit


def main(job):
    (job.output_dir / "seen.json").write_text(json.dumps({"job": job.data}), encoding="utf-8")
    if job.settings.get("trace"):
        with open(job.settings["trace"], "a", encoding="utf-8") as trace:
            trace.write(json.dumps({"plugin": job.plugin_id, "steps": list(job.steps),
                                    "moments": job.data.get("moments"), "limits": job.data.get("limits")}) + "\n")
    mode = job.settings.get("mode", "ok")
    job.progress(0.5, "half way")
    if mode == "sleep":
        time.sleep(120)
    if mode == "fail":
        job.fail(job.settings.get("message") or "the arena feed could not be read")  # stops here
    wanted = json.loads(job.settings.get("edits") or "{}")
    answers = []
    for m in job.moments:
        spec = wanted.get(m.id, wanted.get("*"))
        if spec:
            answers.append({"id": m.id, "edit": _edit_for(spec, m.start)})
    if mode == "bad" and answers:
        answers[0]["edit"]["speed"] = 9  # outside the render's limits: the whole answer is refused
    (job.folder / "result.json").write_text(json.dumps({"plugin_api": 1, "ranges": [], "moments": answers}),
                                            encoding="utf-8")
    job._finished = True


if __name__ == "__main__":
    run(main)
