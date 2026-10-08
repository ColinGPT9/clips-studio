"""The example rater and understander, examples/pipelines/keyword-rater.

It must work the way an outside developer's plugin would: installed through
the plugin manager on this app's version, run through the app's own runner,
importing nothing from Clips Kitty but the SDK, and answering only about the
moments where one of the creator's words is said. Every game named is
Quarkbloom Arena, a made-up game.
"""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from core.models import ClipCandidate, DownloadedVideo, Segment

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "pipelines" / "keyword-rater"
SDK = ROOT / "sdk" / "python"
PLUGIN_ID = "clips-kitty-examples/keyword-rater"

SEGMENTS = [Segment(0.0, 30.0, "round one starts in the arena"),
            Segment(100.0, 130.0, "a Quark  Burst, what a play"),
            Segment(200.0, 230.0, "they wait for the respawn timer")]


def _sdk_on_path():
    if str(SDK) not in sys.path:
        sys.path.insert(0, str(SDK))


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "match.mp4"
    path.write_bytes(b"not really a video")
    return DownloadedVideo(video_id="kw123", title="A Quarkbloom Arena match", path=path, duration=600.0)


def _moments() -> list[dict]:
    """Three moments as the engine hands them over, one of which says a word."""
    from plugins.steps import moments_of

    found = [ClipCandidate(start=5.0, end=25.0, score=70, hook="Round one", reason="loud reaction"),
             ClipCandidate(start=105.0, end=125.0, score=72, hook="What a play", reason="fast speech"),
             ClipCandidate(start=205.0, end=225.0, score=64, hook="Waiting", reason="calm")]
    return [moments_of(f"m{i}", c) for i, c in enumerate(found, 1)]


def _answer(data_dir, video, steps, **settings):
    from plugins import runner

    config = {"clips": {"min_score": 55, "min_duration": 10, "max_duration": 60, "max_clips_per_video": 3},
              "llm": {}}
    return runner.answer_moments({"id": PLUGIN_ID, "settings": settings}, steps, _moments(), video=video,
                                 segments=SEGMENTS, language="en", config=config, data_dir=data_dir,
                                 stage="understand" if steps == ["understand"] else "ranking")


# ---- it stays an outside plugin --------------------------------------------------------


def test_it_imports_only_the_standard_library_and_the_sdk():
    source = (EXAMPLE / "src" / "main.py").read_text(encoding="utf-8")
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            modules.add(node.module.split(".")[0])
    outside = {m for m in modules if m not in sys.stdlib_module_names and m != "clipskitty_sdk"}
    assert not outside, f"the example imports {outside}; an outside plugin can't"
    assert "__import__" not in source


def test_it_is_laid_out_as_its_own_repository():
    for name in ("clipskitty.yaml", "README.md", "LICENSE", "src/main.py"):
        assert (EXAMPLE / name).is_file(), name
    assert (EXAMPLE / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    for name in ("clipskitty.yaml", "README.md", "src/main.py"):
        text = (EXAMPLE / name).read_text(encoding="utf-8")
        assert not re.search(r"https?://|www\.", text), f"{name} links somewhere"
        # The made-up game is only an example of words, in the README.
        assert ("Quarkbloom" in text) == (name == "README.md"), name
    assert "Quarkbloom Arena (a made-up game)" in (EXAMPLE / "README.md").read_text(encoding="utf-8")


def test_its_manifest_is_valid_with_no_warnings():
    pytest.importorskip("yaml")
    _sdk_on_path()
    from clipskitty_sdk import manifest

    data, report = manifest.validate_folder(EXAMPLE)
    assert report.ok and not report.warnings, (report.errors, report.warnings)
    assert data["id"] == PLUGIN_ID and data["license"] == "MIT" and "repository" not in data
    assert data["inputs"] == ["moments", "transcript"] and data["outputs"] == ["context", "ratings"]
    assert data["permissions"] == ["transcript.read"] and data["sends"] == [] and data["execution"] == "local"
    assert data["requires"] == {"clips_kitty": ">=2.0", "plugin_api": 1}
    assert manifest.offers(data) == ("understand", "rate")
    assert manifest.step_problem(data, "rate") is None and manifest.step_problem(data, "understand") is None
    assert manifest.step_problem(data, "find")  # never offered as the job's Pipeline


def test_it_installs_on_this_apps_version(tmp_path):
    pytest.importorskip("yaml")
    from plugins import manager

    app = json.loads((ROOT / "ui" / "package.json").read_text(encoding="utf-8"))["version"]
    plan = manager.plan(tmp_path / "data", {"kind": "folder", "path": str(EXAMPLE)}, app_version=app)
    assert plan["ok"] and not plan["errors"], plan["errors"]
    assert not plan["warnings"] and not plan["technical"], (plan["warnings"], plan["technical"])
    assert plan["plugin"]["id"] == PLUGIN_ID
    assert plan["details"]["steps"] == ["Understands moments", "Rates moments"]
    assert plan["details"]["time_limit"] == ("Clips Kitty stops it after 10 minutes when it rates or "
                                             "understands a video’s moments.")


# ---- what it answers ---------------------------------------------------------------------


def test_through_the_apps_runner_it_rates_and_notes_sample_moments(tmp_path, video, install_plugin, capsys):
    pytest.importorskip("yaml")
    data_dir = tmp_path / "data"
    install_plugin(data_dir, EXAMPLE)
    out = _answer(data_dir, video, ["understand", "rate"], words="quark burst, triple bloom, window", bonus=20)
    assert out["plugin"] == PLUGIN_ID and out["version"] == "1.0.0" and out["steps"] == ["understand", "rate"]
    # Only m2 says a word ("Quark  Burst", in another case and spacing); m1 and m3 keep what they had.
    assert out["answers"] == {"m2": {"score": 92.0, "reason": 'the commentary says "quark burst"',
                                     "context": ['The commentary says "quark burst" here']}}
    assert out["ignored"] == []
    assert f"[{PLUGIN_ID}] One of the words is said in 1 of 3 moment(s)." in capsys.readouterr().out


def test_asked_for_one_step_it_answers_only_that_one(tmp_path, video, install_plugin):
    pytest.importorskip("yaml")
    data_dir = tmp_path / "data"
    install_plugin(data_dir, EXAMPLE)
    rated = _answer(data_dir, video, ["rate"], words="quark burst")
    assert rated["answers"] == {"m2": {"score": 87.0, "reason": 'the commentary says "quark burst"'}}
    noted = _answer(data_dir, video, ["understand"], words="quark burst, what a play")
    assert noted["answers"] == {"m2": {"context": ['The commentary says "quark burst" here',
                                                   'The commentary says "what a play" here']}}
    assert rated["ignored"] == noted["ignored"] == []  # it never answers what it wasn't asked


def test_with_no_words_it_stops_and_says_why(tmp_path, video, install_plugin):
    pytest.importorskip("yaml")
    from plugins import runner

    data_dir = tmp_path / "data"
    install_plugin(data_dir, EXAMPLE)
    with pytest.raises(runner.PluginError) as e:
        _answer(data_dir, video, ["rate"])
    assert e.value.why == "It said: No words are set to listen for: add some to the words setting."


def test_its_readme_command_runs_with_only_the_sdk_on_its_path(tmp_path):
    """The README's own `run` line, started from outside the repository with
    PYTHONPATH set to the SDK alone, as an outside developer would run it:
    no video, five sample moments, one of which says a word."""
    pytest.importorskip("yaml")
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({"language": "en", "segments": [
        {"start": 0, "end": 30, "text": "round one starts"},
        {"start": 30, "end": 50, "text": "what a Quark Burst"},
        {"start": 50, "end": 180, "text": "nothing much happens"}]}), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, "-m", "clipskitty_sdk", "run", str(EXAMPLE), "--transcript", str(transcript),
         "--set", "words=quark burst, triple bloom", "--job-dir", str(tmp_path / "job")],
        capture_output=True, text=True, timeout=300, cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SDK),
             **({"SYSTEMROOT": os.environ["SYSTEMROOT"]} if "SYSTEMROOT" in os.environ else {})},
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "note: no --moments given, so the plugin gets 5 sample moments spread through the video" in out.stderr
    lines = out.stdout.splitlines()
    # The transcript ends at 180 s, so the samples start at 30, 60, 90, 120 and 150 s.
    assert 'm1   30.0s-50.0s  score 60 -> 75  the commentary says "quark burst"' in lines
    assert '       note: The commentary says "quark burst" here' in lines
    assert "m2   60.0s-80.0s  score 60" in lines
    assert "notes: One of the words is said in 1 of 5 moment(s)." in lines


def test_words_match_whole_words_in_any_case():
    import importlib.util

    _sdk_on_path()
    spec = importlib.util.spec_from_file_location("keyword_rater_example", EXAMPLE / "src" / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    words = module.words_of(" quark burst,, Win ,QUARK  BURST, arena wipe ")
    assert words == ["quark burst", "Win", "arena wipe"]
    patterns = [(w, module.pattern_for(w)) for w in words]
    assert module.words_said("Look out the window, a win! Quark\nburst!", patterns) == ["quark burst", "Win"]
    assert module.words_said("windows and quarkbursts", patterns) == []
