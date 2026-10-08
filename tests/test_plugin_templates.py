"""The SDK's templates (`python -m clipskitty_sdk new`) in Clips Kitty itself.

A plugin made from a template must plan cleanly in the plugin manager on this
app's version, run through the app's own runner, and run on Clips Kitty's own
Python: main.py's script host with PYTHONPATH set to the repository's SDK
alone, as the installed app starts it, so a template that imports a part of
the SDK the app doesn't bundle fails here. The runs on the sample video need
FFmpeg; the transcript, rater and editor templates need none.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"
SDK = ROOT / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import devrun, host, samples, scaffold, testing  # noqa: E402
from clipskitty_sdk.contract import CROPS, parse_line  # noqa: E402

FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
READS_THE_VIDEO = {"blank", "game-events", "understander"}
STEPS = {"blank": ["Finds moments"], "transcript": ["Finds moments"], "game-events": ["Finds moments"],
         "rater": ["Rates moments"], "understander": ["Understands moments"], "editor": ["Suggests edits"]}
# What each finder finds on the sample: (start, end, label, reason).
FOUND = {
    "blank": [],
    "transcript": [(13.5, 32.0, "words_said", 'the commentary says "quark burst"')],
    "game-events": [(16.0, 29.75, "quark_burst",
                     "the banner shows from 22 to 26.75 s, and the sound gets 25 dB louder")],
}
# What the rater and the understander answer about the 5 sample moments.
ANSWERS = {
    "rater": {"m3": {"score": 75.0, "reason": 'the commentary says "quark burst"'},
              "m5": {"score": 10.0, "reason": 'the commentary says "waiting", which this rater marks as dull'}},
    "understander": {"m1": {"context": ['The commentary says "round one" here']},
                     "m3": {"context": ["The quark burst banner shows from 22.0 s"]}},
}
# What the editor suggests for the 5 sample clips, as Clips Kitty keeps it.
EDITS = {"m3": {"edit": {"title_overlay": {"text": "Quark burst!", "seconds": 3}},
                "reason": 'Adds a hook title where the commentary says "quark burst"'}}
CONFIG = {"clips": {"min_score": 55, "min_duration": 10, "max_duration": 60, "max_clips_per_video": 0},
          "llm": {}}


def _made(where: Path, template: str) -> Path:
    pytest.importorskip("yaml")
    folder = where / f"quarkbloom-{template}"
    scaffold.make(folder, template)
    return folder


def _needs_ffmpeg(template: str) -> None:
    if template in READS_THE_VIDEO and not FFMPEG:
        pytest.skip("needs FFmpeg and FFprobe")


@pytest.fixture(scope="module")
def sample(tmp_path_factory) -> Path | None:
    """The SDK's sample video, when FFmpeg can make it."""
    if not FFMPEG:
        return None
    return samples.make_sample(tmp_path_factory.mktemp("sample") / "sample.mp4", shutil.which("ffmpeg"))[0]


def _segments():
    from core.models import Segment

    return [Segment(s["start"], s["end"], s["text"], s["words"]) for s in samples.sample_transcript()["segments"]]


def _video(tmp_path, sample):
    from core.models import DownloadedVideo

    path = sample
    if path is None:  # a plugin that never reads the video gets none, so a stand-in will do
        path = tmp_path / "stand-in.mp4"
        path.write_bytes(b"not a video")
    return DownloadedVideo(video_id="quarkbloom", title="A Quarkbloom Arena match", path=path,
                           duration=samples.SAMPLE_VIDEO_SECONDS)


def _sample_moments() -> list[dict]:
    """The 5 moments `run --sample` hands over, as the engine builds them."""
    from core.models import ClipCandidate
    from plugins.steps import moments_of

    found = [ClipCandidate(start=m["start"], end=m["end"], score=m["score"], hook="", reason="")
             for m in devrun.sample_moments(samples.SAMPLE_VIDEO_SECONDS)]
    return [moments_of(f"m{i}", c) for i, c in enumerate(found, 1)]


@pytest.mark.parametrize("template", list(scaffold.TEMPLATES))
def test_every_template_plans_cleanly_on_this_apps_version(template, tmp_path):
    from plugins import manager

    folder = _made(tmp_path, template)
    app = json.loads((ROOT / "ui" / "package.json").read_text(encoding="utf-8"))["version"]
    plan = manager.plan(tmp_path / "data", {"kind": "folder", "path": str(folder)}, app_version=app)
    assert plan["ok"] and not plan["errors"], plan["errors"]
    assert not plan["warnings"] and not plan["technical"], (plan["warnings"], plan["technical"])
    assert plan["plugin"]["id"] == f"{scaffold.DEFAULT_PUBLISHER}/quarkbloom-{template}"
    assert plan["details"]["steps"] == STEPS[template]


@pytest.mark.parametrize("template", list(scaffold.TEMPLATES))
def test_every_template_runs_through_the_apps_runner(template, tmp_path, sample, install_plugin):
    _needs_ffmpeg(template)
    from plugins import runner

    folder = _made(tmp_path, template)
    data_dir = tmp_path / "data"
    plugin_id, _ = install_plugin(data_dir, folder)
    video = _video(tmp_path, sample)
    if template in FOUND:
        clips = runner.find_clips({"id": plugin_id}, video=video, segments=_segments(), language="en",
                                  config=CONFIG, data_dir=data_dir)
        assert [(c.start, c.end, c.subscores["plugin_label"], c.reason) for c in clips] == FOUND[template]
        assert all(c.source == f"plugin:{plugin_id}@0.1.0" for c in clips)
    elif template == "editor":
        out = runner.answer_moments({"id": plugin_id, "settings": {}}, ["edit"], _sample_moments(), video=video,
                                    segments=_segments(), language="en", config=CONFIG, data_dir=data_dir,
                                    stage="edit", crops=CROPS)
        assert out["edits"] == EDITS
        assert out["ignored"] == []
    else:
        steps = ["rate"] if template == "rater" else ["understand"]
        out = runner.answer_moments({"id": plugin_id, "settings": {}}, steps, _sample_moments(), video=video,
                                    segments=_segments(), language="en", config=CONFIG, data_dir=data_dir,
                                    stage="ranking" if template == "rater" else "understand")
        assert out["answers"] == ANSWERS[template]
        assert out["ignored"] == []


@pytest.mark.parametrize("template", list(scaffold.TEMPLATES))
def test_every_template_runs_on_clips_kittys_python(template, tmp_path, sample):
    _needs_ffmpeg(template)
    folder = _made(tmp_path, template)
    job = testing.make_job(tmp_path / "job", folder, video=sample if template in READS_THE_VIDEO else None,
                           transcript=samples.sample_transcript(), duration=samples.SAMPLE_VIDEO_SECONDS)
    base = {k: v for k, v in os.environ.items() if not k.startswith(("CLIPSKITTY_", "PYTHON"))}
    env = host.plugin_env(base, job_folder=job, sdk_dir=SDK)
    assert env["PYTHONPATH"] == str(SDK) and env["CLIPSKITTY_SCRIPT_HOST"] == "1"
    # {python} is the app's engine, main.py, started with the script host's marker.
    done = subprocess.run([sys.executable, str(MAIN), "src/main.py"], cwd=folder, env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    assert [e for e in map(parse_line, done.stdout.splitlines()) if e["type"] == "error"] == []

    data = json.loads((job / "job.json").read_text(encoding="utf-8"))
    if template == "editor":
        limits = data["limits"]
        edits, lines = host.read_edits(job, windows={m["id"]: (m["start"], m["end"]) for m in data["moments"]},
                                       crops=limits["crops"], min_length=max(1.0, limits["min_duration"]),
                                       max_length=limits["max_duration"])
        assert data["steps"] == ["edit"] and limits["crops"] == list(CROPS)
        assert edits == EDITS and lines == []
    elif template in FOUND:
        result = host.read_result(job, duration=samples.SAMPLE_VIDEO_SECONDS, max_clips=None,
                                  steps=data.get("steps"))
        assert [(r["start"], r["end"], r["label"], r["reason"]) for r in result["ranges"]] == FOUND[template]
    else:
        answers, ignored = host.read_answers(job, steps=data["steps"], ids=[m["id"] for m in data["moments"]])
        assert answers == ANSWERS[template] and ignored == []
