"""Test plugin: answers about the moments it is handed, as its settings say.

`scores` and `notes` say what to answer for which moment (`*` for every
one); a note of `{said}` is what is said during the moment. `mode` makes it
fail, write an answer Clips Kitty can't use, answer for moments it wasn't
handed, add ranges, or run until it is stopped. Three more modes write a
result.json that can't even be read as one: a folder (`folder`), a score of
400 digits (`huge`) and notes nested too deep to parse (`deep`). With
`trace`, each run adds one line to that file: the plugin, its steps and the
moments it was handed.
"""

import json
import time

from clipskitty_sdk import run

RAW = {
    # m2's score is out of range, so the whole answer is refused, m1's too.
    "bad": {"plugin_api": 1, "ranges": [], "moments": [{"id": "m1", "score": 90}, {"id": "m2", "score": 101}]},
    "unknown_ids": {"plugin_api": 1, "ranges": [], "moments": [
        {"id": "m1", "score": 90, "reason": "a big play"}, {"id": "m999", "score": 80}]},
    "ranges": {"plugin_api": 1, "ranges": [{"start": 1, "end": 20, "score": 70}, {"start": 30, "end": 50}],
               "moments": [{"id": "m1", "context": ["A quark burst opens the round"]}]},
    # Too large for a float: checking it once raised OverflowError.
    "huge": {"plugin_api": 1, "ranges": [], "moments": [{"id": "m1", "score": int("9" * 400)}]},
}
DEEP = '{"plugin_api": 1, "ranges": [], "notes": ' + "[" * 200_000 + "]" * 200_000 + "}"


def _pairs(text: str) -> dict:
    out = {}
    for item in filter(None, (text or "").split(",")):
        key, _, value = item.partition("=")
        out[key.strip()] = value
    return out


def main(job):
    (job.output_dir / "seen.json").write_text(json.dumps({"job": job.data}), encoding="utf-8")
    if job.settings.get("trace"):
        with open(job.settings["trace"], "a", encoding="utf-8") as trace:
            trace.write(json.dumps({"plugin": job.plugin_id, "steps": list(job.steps),
                                    "moments": job.data.get("moments")}) + "\n")
    mode = job.settings.get("mode", "ok")
    job.progress(0.5, "half way")
    if mode == "sleep":
        time.sleep(120)
    if mode in RAW or mode in ("folder", "deep"):
        result = job.folder / "result.json"
        if mode == "folder":
            result.mkdir()
        else:
            result.write_text(DEEP if mode == "deep" else json.dumps(RAW[mode]), encoding="utf-8")
        job._finished = True
        return
    scores, notes = _pairs(job.settings.get("scores")), _pairs(job.settings.get("notes"))
    for m in job.moments:
        score = scores.get(m.id, scores.get("*"))
        if score:
            value = m.score + float(score) if score[0] in "+-" else float(score)
            job.rate(m, max(0.0, min(100.0, value)), reason=f"rated {value:g} in the test")
        for note in filter(None, (notes.get(m.id, notes.get("*")) or "").split("|")):
            job.understand(m, job.text(m) if note == "{said}" else note)
    if mode == "fail":
        # Answers given before a failure are never used.
        job.fail(job.settings.get("message") or "the arena feed could not be read")


if __name__ == "__main__":
    run(main)
