"""The SDK's messages: what `validate` and `run` print, the lint of a
plugin's code, and the lines a plugin's own run() writes.

Plain words where a creator reads them (a plugin's error line); precise words
for developers (log lines, and the command line). Like every
tests/test_plugin_sdk_*.py file, this needs only pytest and PyYAML and
imports nothing from Clips Kitty's engine, so it also runs in CI's SDK
(Windows) job. Nothing here needs FFmpeg.
"""

import ast
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import ContractError, host, lint, manifest, read_job, run  # noqa: E402
from clipskitty_sdk._hints import python_command  # noqa: E402
from clipskitty_sdk.job import MISTAKE, SettingMissing, no_job_folder_text  # noqa: E402

GOOD_MANIFEST = """\
manifest_version: 1
id: example-dev/{name}
name: {title}
version: 1.0.0
kind: pipeline
capability: highlight_detection
description: A test plugin for Quarkbloom Arena (a made-up game).
license: MIT
requires: {{clips_kitty: '>=2.0', plugin_api: 1}}
run:
  command: ['{{python}}', src/main.py]
{timeout}execution: local
inputs: [{inputs}]
outputs: [{outputs}]
permissions: [{permissions}]
{extra}"""

# A finder that records its job, reports progress and a log line, and finds two moments.
FINDER_MAIN = '''\
import json

from clipskitty_sdk import run


def main(job):
    (job.output_dir / "seen.json").write_text(json.dumps(job.data), encoding="utf-8")
    job.progress(0.25, "a quarter")
    job.progress(0.75, "three quarters")
    job.progress(1.0, "done")
    job.log("halfway there")
    job.add_range(5, 20, score=80, label="quark-burst", reason="the banner shows and it gets louder")
    job.add_range(30, 50, label="quark_burst")


run(main)
'''

# Answers every moment it is handed.
GRADER_MAIN = '''\
from clipskitty_sdk import run


def main(job):
    for m in job.moments:
        job.rate(m, 70, reason="graded")
        job.understand(m, "Something happens here")


run(main)
'''

SLEEPER_MAIN = '''\
import time

from clipskitty_sdk import run


def main(job):
    time.sleep(60)


run(main)
'''


def _plugin(tmp_path, name="quarkbloom-bursts", *, title="Quarkbloom Bursts", inputs="video",
            outputs="ranges", permissions="video.read", extra="", timeout=None, main=FINDER_MAIN) -> Path:
    """A plugin folder with a valid manifest and src/main.py."""
    folder = tmp_path / name
    (folder / "src").mkdir(parents=True)
    (folder / "clipskitty.yaml").write_text(GOOD_MANIFEST.format(
        name=name, title=title, inputs=inputs, outputs=outputs, permissions=permissions, extra=extra,
        timeout=f"  timeout_minutes: {timeout}\n" if timeout else ""), encoding="utf-8")
    (folder / "src" / "main.py").write_text(main, encoding="utf-8")
    return folder


def _grader(tmp_path, **kwargs) -> Path:
    return _plugin(tmp_path, "quarkbloom-grader", title="Quarkbloom Grader", inputs="moments, transcript",
                   outputs="ratings, context", permissions="transcript.read", main=GRADER_MAIN, **kwargs)


def _video(tmp_path) -> Path:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not decoded by the test plugins")
    return video


def _cli(*args) -> int:
    from clipskitty_sdk.__main__ import main

    return main([str(a) for a in args])


def _run(tmp_path, plugin: Path, *args, job="job") -> int:
    """`python -m clipskitty_sdk run`, without FFprobe, in tmp_path/job."""
    return _cli("run", plugin, "--job-dir", tmp_path / job, "--ffprobe", tmp_path / "no-ffprobe", *args)


def _job_json(tmp_path, job="job") -> dict:
    return json.loads((tmp_path / job / "job.json").read_text(encoding="utf-8"))


def _lint(folder: Path, files: dict, manifest_data=None) -> list[str]:
    for rel, text in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    data = manifest_data if manifest_data is not None else {"run": {"command": ["{python}", "src/main.py"]}}
    return lint.lint_folder(folder, data)


# ---- validate ---------------------------------------------------------------------


def test_validate_without_pyyaml_blames_the_pc_not_the_plugin(tmp_path, capsys, monkeypatch):
    folder = _plugin(tmp_path)
    monkeypatch.setitem(sys.modules, "yaml", None)  # `import yaml` now fails, as without PyYAML
    line = ("error: reading clipskitty.yaml needs PyYAML, which isn't installed with this Python: "
            'pip install "clipskitty-sdk[yaml]". This is about your PC, not your plugin.\n')
    assert _cli("validate", folder) == 2
    out = capsys.readouterr().out
    assert out == line and "would refuse" not in out
    assert _cli("run", folder, "--video", _video(tmp_path)) == 2
    assert capsys.readouterr().err == line
    # The app's own message, from the shared loader, is unchanged.
    with pytest.raises(manifest.ManifestError, match=r"^reading clipskitty.yaml needs PyYAML: pip install pyyaml$"):
        manifest.load(folder)


def test_validate_without_a_manifest_names_the_folder(tmp_path, capsys):
    assert _cli("validate", tmp_path) == 2
    out = capsys.readouterr().out
    assert out == f"error: no clipskitty.yaml in {tmp_path.resolve()}: is this the plugin's folder?\n"
    assert _cli("run", tmp_path, "--video", _video(tmp_path)) == 2
    assert "is this the plugin's folder?" in capsys.readouterr().err


def test_errors_carry_their_yaml_line(tmp_path, capsys):
    pytest.importorskip("yaml")
    folder = _plugin(tmp_path, outputs="ranges, edits", extra=(
        "settings:\n"
        "  min_kills: {type: integer, default: 9, maximum: 6}\n"
        "colour: red\n"))
    assert _cli("validate", folder) == 1
    out = capsys.readouterr().out
    assert "error: outputs[1]: output 'edits' is planned, not supported by plugin API 1 (clipskitty.yaml line 14)\n" \
        in out
    assert "error: settings.min_kills.default: 9 is above the maximum, 6 (clipskitty.yaml line 17)\n" in out
    assert "warning: colour: unknown field, ignored (clipskitty.yaml line 18)\n" in out
    # A field that is missing is marked at the field that should hold it, when there is one.
    text = (folder / "clipskitty.yaml").read_text(encoding="utf-8").replace("{clips_kitty: '>=2.0', plugin_api: 1}",
                                                                               "{plugin_api: 1}")
    (folder / "clipskitty.yaml").write_text(text, encoding="utf-8")
    assert _cli("validate", folder) == 1
    assert "error: requires.clips_kitty: is required: the Clips Kitty versions it works with, e.g. \">=2.0\" " \
           "(clipskitty.yaml line 9)\n" in capsys.readouterr().out
    # Only the command line adds marks: the shared report, which the app uses, has none.
    _, report = manifest.validate_folder(folder)
    assert report.errors and not any("clipskitty.yaml line" in e for e in report.errors + report.warnings)


def test_an_unknown_field_gets_a_did_you_mean(tmp_path, capsys):
    pytest.importorskip("yaml")
    folder = _plugin(tmp_path, extra="permisions: [video.read]\nflavour: mint\nsettings:\n"
                                     "  lead_in_seconds: {type: number, default: 6, titel: Lead-in}\n")
    assert _cli("validate", folder) == 0
    out = capsys.readouterr().out
    assert ("warning: permisions: unknown field, ignored (clipskitty.yaml line 16)\n"
            "         did you mean permissions?\n") in out
    assert ("warning: settings.lead_in_seconds.titel: unknown field, ignored (clipskitty.yaml line 19)\n"
            "         did you mean title?\n") in out
    assert "warning: flavour: unknown field, ignored (clipskitty.yaml line 17)\nwarning:" in out  # nothing close
    assert out.endswith("example-dev/quarkbloom-bursts 1.0.0: valid, 3 warning(s)\n")
    data = {"permisions": [], "run": {"comand": []}}
    report = manifest.validate(data)
    assert report.hints == {"permisions": "permissions", "run.comand": "command"}
    assert "permisions: unknown field, ignored" in report.warnings  # the warning itself is unchanged


def test_a_numeric_version_says_how_to_write_it():
    for number, written in ((0.1, "0.1.0"), (2, "2.0.0"), (1.25, "1.25.0"), (1e-05, "1.0.0")):
        report = manifest.validate({"version": number})
        assert f"version: YAML read this as the number {number!r}; write a version like {written}" in report.errors
    assert "version: '1.0' is not a version like 1.2.0 (SemVer)" in manifest.validate({"version": "1.0"}).errors
    assert "version: must be text" in manifest.validate({"version": True}).errors


def test_python_requirements_warning_names_clips_kittys_python():
    report = manifest.validate({"run": {"command": ["{python}", "src/main.py"], "python_requirements": "req.txt"}})
    assert ("run.python_requirements: per-plugin Python packages are planned; until then a pipeline runs on "
            "Clips Kitty's own Python with only the standard library and clipskitty_sdk, so these packages "
            "won't be there") in report.warnings


# ---- the lint -----------------------------------------------------------------------


def test_lint_warns_about_imports_clips_kitty_doesnt_promise(tmp_path):
    problems = _lint(tmp_path, {"src/main.py": (
        "import json\n"
        "import numpy\n"
        "from core.state import thing\n"
        "import tkinter\n"
        "from clipskitty_sdk import run\n"
        "from clipskitty_sdk.job import Moment\n"
        "try:\n"
        "    import cv2\n"
        "except ImportError:\n"
        "    cv2 = None\n"
        "def main():\n"
        "    import yaml\n")})
    assert problems == [
        ("src/main.py line 2: imports numpy. Pipelines run on Clips Kitty's own Python, which promises only the "
         "standard library and clipskitty_sdk, so this may fail on creators' PCs"),
        ("src/main.py line 3: imports core, which is Clips Kitty's own code: pipelines can't use it, and Clips "
         "Kitty stops a run that tries"),
        "src/main.py line 4: imports tkinter, which Clips Kitty's Python leaves out",
        ("src/main.py line 12: imports yaml. Pipelines run on Clips Kitty's own Python, which promises only the "
         "standard library and clipskitty_sdk, so this may fail on creators' PCs"),
    ]


def test_lint_allows_the_plugins_own_modules(tmp_path):
    problems = _lint(tmp_path, {
        "src/main.py": "import helper\nimport quarkbloom.banner\nfrom quarkbloom import colours\nimport helpers\n",
        "src/helper.py": "VALUE = 1\n",
        "src/quarkbloom/__init__.py": "",
        "src/quarkbloom/banner.py": "from quarkbloom import colours\nfrom . import colours as again\n",
        "src/quarkbloom/colours.py": "RED = 'e0303a'\n",
        "helpers.py": "VALUE = 2\n",
        "tools/make_data.py": "import helper_of_tools\n",
        "tools/helper_of_tools.py": "",
    })
    assert problems == [
        ("src/main.py line 4: imports helpers, but helpers.py is at the plugin's root, which isn't on the path "
         "when Clips Kitty runs src/main.py: move it into src/"),
    ]
    # A plugin whose script is at its root has the root on its path.
    assert _lint(tmp_path / "flat", {"main.py": "import helpers\n", "helpers.py": ""},
                 {"run": {"command": ["{python}", "main.py"]}}) == []


def test_lint_warns_about_modules_windows_lacks(tmp_path):
    assert _lint(tmp_path, {"src/main.py": "import fcntl\nimport os.path\n"}) == [
        "src/main.py line 1: imports fcntl, which isn't on Windows, where Clips Kitty runs"]
    assert lint.NOT_ON_WINDOWS <= lint.NOT_BUNDLED and not lint.NOT_ON_WINDOWS & lint.APP_LEAVES_OUT
    assert lint.NOT_ON_WINDOWS <= sys.stdlib_module_names


def test_lint_skips_tests(tmp_path):
    assert _lint(tmp_path, {
        "src/main.py": "from clipskitty_sdk import run\n",
        "tests/test_main.py": "import pytest\nimport numpy\n",
        "tests/helpers.py": "import pytest\n",
        "src/test_banner.py": "import pytest\n",
        "conftest.py": "import pytest\n",
        ".venv/lib/site.py": "import numpy\n",
    }) == []


@pytest.mark.skipif(sys.version_info < (3, 12), reason="this Python can't read the newer syntax at all")
def test_lint_warns_about_syntax_newer_than_clips_kittys_python(tmp_path):
    assert host.APP_PYTHON == (3, 11)
    assert _lint(tmp_path, {"src/main.py": "import json\n\ntype Seconds = float\n"}) == [
        ("src/main.py line 3: this needs a newer Python than Clips Kitty's (3.11): Type statement is only "
         "supported in Python 3.12 and greater")]


def test_lint_warns_about_text_files_without_encoding(tmp_path):
    problems = _lint(tmp_path, {"src/main.py": (
        "import gzip, io, zipfile\n"
        "from pathlib import Path\n"
        "open('a.txt')\n"
        "open('a.txt', 'w')\n"
        "open('a.bin', 'rb')\n"
        "open('a.txt', encoding='utf-8')\n"
        "open('a.txt', 'r', -1, 'utf-8')\n"
        "Path('a').read_text()\n"
        "Path('a').read_text('utf-8')\n"
        "Path('a').write_text('hi')\n"
        "Path('a').write_text('hi', encoding='utf-8')\n"
        "Path('a').open('a')\n"
        "Path('a').open()\n"
        "Path('a').open('rb')\n"
        "io.open('a.txt')\n"
        "gzip.open('a.gz')\n"
        "zipfile.ZipFile('a.zip').open('notes.txt')\n"
        "open('a.txt', mode)\n"
        "open('a.txt', **options)\n")})
    said = ("without encoding=. Clips Kitty's Python doesn't use UTF-8 mode, so on Windows text files would "
            'use the PC\'s own code page: pass encoding="utf-8"')
    assert problems == [f"src/main.py line {line}: {call} {said}" for line, call in (
        (3, "open()"), (4, "open()"), (8, "read_text()"), (10, "write_text()"), (12, "open()"), (13, "open()"),
        (15, "open()"))]


def test_line_marks_come_from_manifest_py(tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    text = ("id: example-dev/x\n"
            "outputs:\n"
            "  - ranges\n"
            "  - edits\n"
            "settings:\n"
            "  min_kills:\n"
            "    default: 9\n")
    assert manifest.line_marks(text) == {"id": 1, "outputs": 2, "outputs[0]": 3, "outputs[1]": 4, "settings": 5,
                                         "settings.min_kills": 6, "settings.min_kills.default": 7}
    assert manifest.line_marks("id: [unclosed\n") == {}
    monkeypatch.setitem(sys.modules, "yaml", None)
    assert manifest.line_marks(text) == {}
    # Only manifest.py imports PyYAML, lazily: the command line, devrun and the lint don't.
    for name in ("__main__.py", "devrun.py", "lint.py", "_hints.py"):
        tree = ast.parse((SDK / "clipskitty_sdk" / name).read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        assert "yaml" not in imported, name


# ---- run --------------------------------------------------------------------------


def test_a_refused_run_leaves_no_job_folder(tmp_path, capsys, monkeypatch):
    pytest.importorskip("yaml")
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    folder, video = _plugin(tmp_path), _video(tmp_path)
    for args in (["--set", "min_kills=4"], ["--steps", "edit"], ["--model", "killfeed=models"],
                 ["--timeout", "0"], ["--video", tmp_path / "missing.mp4"]):
        assert _cli("run", folder, "--video", video, *args) == 2, args
        assert _run(tmp_path, folder, "--video", video, *args) == 2, args
        assert "error: " in capsys.readouterr().err
        assert not (tmp_path / "job").exists() and list(temp.iterdir()) == [], args
    assert _cli("run", folder, "--video", video) == 0  # a run that starts makes one
    assert [p.name[:len("clipskitty-job-")] for p in temp.iterdir()] == ["clipskitty-job-"]


def test_an_unknown_setting_lists_the_real_ones(tmp_path, capsys):
    pytest.importorskip("yaml")
    video = _video(tmp_path)
    folder = _plugin(tmp_path, extra=(
        "settings:\n"
        "  louder_by_db: {type: number, default: 6}\n"
        "  scene_threshold: {type: number, default: 0.3}\n"
        "  api_key: {type: secret}\n"
        "  lead_in_seconds: {type: number, default: 6}\n"))
    assert _run(tmp_path, folder, "--video", video, "--set", "min_kills=4") == 2
    assert capsys.readouterr().err.endswith(
        "error: --set: this pipeline has no setting called 'min_kills'; its settings are louder_by_db, "
        "scene_threshold, lead_in_seconds\n")
    bare = _plugin(tmp_path, "quarkbloom-bare")
    assert _run(tmp_path, bare, "--video", video, "--set", "min_kills=4") == 2
    assert capsys.readouterr().err.endswith(
        "error: --set: this pipeline has no setting called 'min_kills'; it has no settings\n")
    secret = _plugin(tmp_path, "quarkbloom-secret", extra="settings:\n  api_key: {type: secret}\n")
    assert _run(tmp_path, secret, "--video", video, "--set", "min_kills=4") == 2
    assert capsys.readouterr().err.endswith(
        "error: --set: this pipeline has no setting called 'min_kills'; its only settings are secrets: "
        "pass them with --secret\n")
    # The app's own message is unchanged.
    with pytest.raises(ValueError, match=r"^this pipeline has no setting called 'min_kills'$"):
        host.job_settings({"settings": {}}, {"min_kills": 4})


def test_set_follows_the_settings_type(tmp_path, capsys):
    pytest.importorskip("yaml")
    folder = _plugin(tmp_path, extra=(
        "settings:\n"
        "  banner_colour: {type: string, default: e0303a}\n"
        "  louder_by_db: {type: number, default: 6}\n"
        "  rounds: {type: integer, default: 3}\n"
        "  needs_loud: {type: boolean, default: false}\n"
        "  mode: {type: choice, options: ['1', two], default: two}\n"
        "  words: {type: string, default: ''}\n"))
    assert _run(tmp_path, folder, "--video", _video(tmp_path), "--set", "banner_colour=303030",
                "--set", "louder_by_db=6.5", "--set", "rounds=4", "--set", "needs_loud=true", "--set", "mode=1",
                "--set", "words=[quark burst]") == 0, capsys.readouterr()
    assert _job_json(tmp_path)["settings"] == {"banner_colour": "303030", "louder_by_db": 6.5, "rounds": 4,
                                               "needs_loud": True, "mode": "1", "words": "[quark burst]"}
    capsys.readouterr()
    assert _run(tmp_path, folder, "--video", _video(tmp_path), "--set", "rounds=many", job="bad") == 2
    assert "error: setting 'rounds': 'many' is not a number" in capsys.readouterr().err


def test_steps_takes_commas_or_separate_words(tmp_path, capsys):
    pytest.importorskip("yaml")
    grader = _grader(tmp_path)
    # PowerShell hands an unquoted understand,rate over as two words.
    for job, steps in (("words", ["understand", "rate"]), ("commas", ["understand,rate"]),
                       ("spaces", ["understand, rate"])):
        assert _run(tmp_path, grader, "--duration", "60", "--steps", *steps, job=job) == 0, steps
        assert _job_json(tmp_path, job)["steps"] == ["understand", "rate"], steps
    assert "Understand and rate: 5 of 5 moment(s) answered" in capsys.readouterr().out


def test_steps_edit_is_planned(tmp_path, capsys):
    pytest.importorskip("yaml")
    grader = _grader(tmp_path)
    for step in ("edit", "export"):
        assert _run(tmp_path, grader, "--duration", "60", "--steps", "rate", step) == 2
        assert capsys.readouterr().err.endswith(
            f"error: --steps: {step} is planned, not part of plugin contract 1 yet; use find, understand or rate\n")
    assert _run(tmp_path, grader, "--duration", "60", "--steps", "polish") == 2
    assert capsys.readouterr().err.endswith("error: --steps: unknown step 'polish'; expected find, understand or rate\n")


def test_hints_name_the_python_that_is_running(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    other = str(Path(sys.executable).parent / "another" / "python.exe")
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: other if name == "python" else None)
    assert python_command() == "py -m clipskitty_sdk"
    assert "To try it, run: py -m clipskitty_sdk run <the plugin's folder> --sample\n" in no_job_folder_text()
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    assert python_command() == "py -m clipskitty_sdk"  # no python on PATH at all
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: sys.executable if name == "python" else None)
    assert python_command() == "python -m clipskitty_sdk"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: other)
    assert python_command() == "python -m clipskitty_sdk"
    assert "To try it, run: python -m clipskitty_sdk run <the plugin's folder> --sample\n" in no_job_folder_text()


def test_run_warns_when_ffmpeg_is_missing(tmp_path, capsys, monkeypatch):
    pytest.importorskip("yaml")
    folder = _plugin(tmp_path, permissions="video.read, ffmpeg")
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name in ("ffmpeg", "ffprobe")
                        else which(name, *a, **k))
    warning = ("warning: this plugin asks for ffmpeg, but FFmpeg isn't on PATH. Install FFmpeg, or pass --ffmpeg "
               "and --ffprobe.\n")
    assert _run(tmp_path, folder, "--video", _video(tmp_path)) == 0
    assert warning in capsys.readouterr().err
    assert _run(tmp_path, folder, "--video", _video(tmp_path), "--ffmpeg", tmp_path / "ffmpeg", job="given") == 0
    assert warning not in capsys.readouterr().err
    assert _run(tmp_path, _plugin(tmp_path, "no-ffmpeg"), "--video", _video(tmp_path), job="none") == 0
    assert warning not in capsys.readouterr().err  # a plugin that doesn't ask for it isn't warned


def test_run_uses_clips_kittys_time_limit(tmp_path, capsys, monkeypatch):
    pytest.importorskip("yaml")
    video = _video(tmp_path)
    finder, rater = _plugin(tmp_path), _grader(tmp_path)
    capped = _plugin(tmp_path, "quarkbloom-capped", timeout=5)
    asked = []

    def stopped(command, *, timeout=None, **kwargs):
        asked.append(timeout)
        return host.RunOutcome(None, timed_out=True)

    monkeypatch.setattr(host, "run_plugin", stopped)
    for plugin, args, seconds, line in (
            (capped, ["--video", video], 5 * 60,
             "failed: the plugin ran past its 5 minute limit, as Clips Kitty would stop it\n"),
            (finder, ["--video", video], 60 * 60,
             "failed: the plugin ran past its 60 minute limit, as Clips Kitty would stop it\n"),
            (rater, ["--duration", "60"], 10 * 60,
             "failed: the plugin ran past its 10 minute limit, as Clips Kitty would stop it\n"),
            (finder, ["--video", video, "--timeout", "90"], 90,
             "failed: the plugin ran past the 90 second limit --timeout set\n")):
        asked.clear()
        assert _run(tmp_path, plugin, *args, job=f"job-{len(line)}-{seconds}") == 1
        assert asked == [seconds] and capsys.readouterr().err.endswith(line)
    for plugin, steps, minutes in ((finder, ("find",), 60), (rater, ("rate",), 10), (capped, ("find",), 5)):
        data = manifest.load(plugin)
        assert host.timeout_seconds(data, host.FIND_TIMEOUT_MINUTES if "find" in steps
                                    else host.MOMENT_TIMEOUT_MINUTES) == minutes * 60
    monkeypatch.undo()
    # A real plugin, really stopped.
    sleeper = _plugin(tmp_path, "quarkbloom-sleeper", main=SLEEPER_MAIN)
    assert _run(tmp_path, sleeper, "--video", video, "--timeout", "1", job="sleeper") == 1
    assert capsys.readouterr().err.endswith("failed: the plugin ran past the 1 second limit --timeout set\n")


class _Terminal(io.StringIO):
    def isatty(self):
        return True


def test_progress_rewrites_one_line_on_a_terminal(tmp_path, capsys, monkeypatch):
    pytest.importorskip("yaml")
    folder, video = _plugin(tmp_path), _video(tmp_path)
    assert _run(tmp_path, folder, "--video", video, job="pipe") == 0
    assert ("   25%  a quarter\n   75%  three quarters\n  100%  done\n  log: halfway there\n"
            in capsys.readouterr().out)  # not a terminal: a line each, as always
    terminal = _Terminal()
    monkeypatch.setattr(sys, "stdout", terminal)
    assert _run(tmp_path, folder, "--video", video, job="terminal") == 0
    assert ("\r   25%  a quarter\r   75%  three quarters\r  100%  done          \n  log: halfway there\n"
            in terminal.getvalue())


def test_run_shows_the_reason_and_warns_about_labels_not_in_events(tmp_path, capsys):
    pytest.importorskip("yaml")
    video = _video(tmp_path)
    folder = _plugin(tmp_path, extra="events: [quark_burst]\n")
    assert _run(tmp_path, folder, "--video", video, "--duration", "60") == 0
    out, err = capsys.readouterr()
    assert ("       5.0s      20.0s  score  80  quark-burst\n"
            "       why: the banner shows and it gets louder\n"
            "      30.0s      50.0s  score   -  quark_burst\n") in out
    assert err.endswith("warning: r1's label 'quark-burst' isn't in events in clipskitty.yaml (quark_burst); "
                        "the Marketplace lists only those\n")
    assert _run(tmp_path, _plugin(tmp_path, "no-events"), "--video", video, job="no-events") == 0
    assert "isn't in events" not in capsys.readouterr().err


def test_game_and_game_hint_shapes(tmp_path):
    pytest.importorskip("yaml")
    assert _run(tmp_path, _plugin(tmp_path), "--video", _video(tmp_path), "--duration", "812.5",
                "--game", "Quarkbloom Arena", "--game-hint", "quarkbloom arena ranked") == 0
    assert _job_json(tmp_path)["video"]["games"] == [
        {"name": "Quarkbloom Arena", "start": 0.0, "end": 812.5},
        {"name": "", "start": 0.0, "end": 812.5, "hint": "quarkbloom arena ranked"}]
    assert _run(tmp_path, _plugin(tmp_path, "plain"), "--video", _video(tmp_path), job="plain") == 0
    assert _job_json(tmp_path, "plain")["video"]["games"] == []


def test_model_takes_files_from_the_manifest(tmp_path, capsys):
    pytest.importorskip("yaml")
    folder = _plugin(tmp_path, extra=(
        "models:\n"
        "  - name: killfeed\n"
        "    source: huggingface\n"
        "    id: example-dev/quarkbloom-killfeed\n"
        f"    revision: {'a' * 40}\n"
        "    files: [model.onnx, labels.txt]\n"))
    where = tmp_path / "models" / "killfeed"
    assert _run(tmp_path, folder, "--video", _video(tmp_path), "--model", f"killfeed={where}") == 0
    assert _job_json(tmp_path)["models"] == {"killfeed": {
        "source": "huggingface", "id": "example-dev/quarkbloom-killfeed", "path": str(where), "revision": "a" * 40,
        "files": {"model.onnx": str(where / "model.onnx"), "labels.txt": str(where / "labels.txt")}}}
    capsys.readouterr()
    assert _run(tmp_path, folder, "--video", _video(tmp_path), "--model", f"x={where}", job="x") == 2
    assert capsys.readouterr().err.endswith("error: --model: the manifest lists no model called 'x'; "
                                            "it lists: killfeed\n")


def test_plugin_env_switches_utf8_mode_off(tmp_path):
    for base in ({}, {"PYTHONUTF8": "1"}, {"PYTHONUTF8": "0"}, {"pythonutf8": "1"}):
        env = host.plugin_env({"PATH": "/bin", **base}, job_folder=tmp_path)
        assert env["PYTHONUTF8"] == "0" and "pythonutf8" not in env, base


# ---- a plugin's own run() --------------------------------------------------------------


def _job(tmp_path, settings_spec=None, settings=None) -> Path:
    data = {"id": "example-dev/quarkbloom-bursts", "version": "1.0.0", "permissions": [],
            "settings": settings_spec or {}}
    job, transcript = host.build_job(data, settings=settings, output_dir=tmp_path / "job" / "out")
    host.write_job(tmp_path / "job", job, transcript)
    return tmp_path / "job"


def _lines(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines()]


def test_running_main_py_directly_explains_and_exits_2(tmp_path, monkeypatch):
    folder = _plugin(tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLIPSKITTY_", "PYTHON"))}
    env["PYTHONPATH"] = str(SDK)
    done = subprocess.run([sys.executable, "src/main.py"], cwd=str(folder), env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 2 and done.stdout == ""
    assert done.stderr == no_job_folder_text() == (
        "This is a Clips Kitty plugin: Clips Kitty starts it with a job folder.\n"
        f"To try it, run: {python_command()} run <the plugin's folder> --sample\n"
        "(no job folder: pass it as the first argument or set CLIPSKITTY_JOB)\n")
    # read_job's own message is unchanged.
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.delenv("CLIPSKITTY_JOB", raising=False)
    with pytest.raises(ContractError, match=r"^job: no job folder: pass it as the first argument or set "
                                            r"CLIPSKITTY_JOB$"):
        read_job()


def test_a_missing_setting_says_how_to_fix_it(tmp_path, capsys):
    plain = "the setting min_kills has no value: choose one in the pipeline's settings, or ask its developer"
    hint = ("no setting called 'min_kills' in job.settings: declare it under settings in clipskitty.yaml with a "
            "default, or use job.settings.get('min_kills', <default>)")
    # Declared without a default and left empty, as a creator can: the same as not declared.
    folder = _job(tmp_path, {"min_kills": {"type": "integer"}, "rounds": {"type": "integer", "default": 3}})
    job = read_job(folder, out=io.StringIO())
    assert job.settings == {"rounds": 3} and job.settings.get("min_kills", 2) == 2 and "min_kills" not in job.settings
    with pytest.raises(SettingMissing) as e:
        job.settings["min_kills"]
    assert isinstance(e.value, KeyError) and str(e.value) == plain and e.value.hint == hint
    with pytest.raises(SystemExit) as stop:
        run(lambda job: job.settings["min_kills"], folder)
    assert stop.value.code == 1
    lines = _lines(capsys.readouterr().out)
    assert lines[-1] == {"type": "error", "message": plain}
    assert {"type": "log", "message": hint} in lines


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"quarkbloom_main_{abs(hash(path))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_programming_errors_get_a_plain_line_and_a_log_line(tmp_path, capsys, monkeypatch):
    plugin = tmp_path / "plugin"
    (plugin / "src").mkdir(parents=True)
    (plugin / "src" / "main.py").write_text(
        "def lookup(job):\n"
        "    return job.data['frame']\n"
        "def types(job):\n"
        "    return 'quark' + 1\n"
        "def arithmetic(job):\n"
        "    return 1 / 0\n"
        "def attribute(job):\n"
        "    return job.frames\n"
        "def name(job):\n"
        "    return frames\n"
        "def worded(job):\n"
        "    raise ValueError('the kill feed could not be read')\n", encoding="utf-8")
    folder = _job(tmp_path)
    monkeypatch.chdir(plugin)  # where Clips Kitty starts a plugin
    code = _load(plugin / "src" / "main.py")
    for main, logged in ((code.lookup, "KeyError: 'frame' (src/main.py line 2)"),
                         (code.types, 'TypeError: can only concatenate str (not "int") to str (src/main.py line 4)'),
                         (code.arithmetic, "ZeroDivisionError: division by zero (src/main.py line 6)"),
                         (code.attribute, ("AttributeError: 'Job' object has no attribute 'frames' "
                                           "(src/main.py line 8)")),
                         (code.name, "NameError: name 'frames' is not defined (src/main.py line 10)")):
        with pytest.raises(SystemExit):
            run(main, folder)
        lines = _lines(capsys.readouterr().out)
        assert lines[-1] == {"type": "error", "message": MISTAKE}, main.__name__
        assert lines[-2] == {"type": "log", "message": logged}
        assert any("Traceback" in line["message"] for line in lines if line["type"] == "log")
    assert MISTAKE == "it stopped on a mistake in its own code. Ask its developer to fix it."
    with pytest.raises(SystemExit):
        run(code.worded, folder)
    assert _lines(capsys.readouterr().out)[-1] == {"type": "error", "message": "the kill feed could not be read"}


def test_a_score_error_shows_the_value(tmp_path):
    job = read_job(_job(tmp_path), out=io.StringIO())
    with pytest.raises(ContractError) as e:
        job.add_range(0, 10, score=140)
    assert str(e.value) == "add_range: range: score must be a number from 0 to 100, or left out (got 140)"
    own = job.add_range(0, 10, score=60)
    with pytest.raises(ContractError) as e:
        job.rate(own, 140)
    assert str(e.value) == "rate: score must be a number from 0 to 100 (got 140)"
    with pytest.raises(ContractError) as e:
        job.rate(own, int("9" * 400))
    assert str(e.value).endswith("(got " + "9" * 37 + "...)")
    with pytest.raises(ContractError) as e:
        job.add_range(10, 5)
    assert str(e.value) == "add_range: range: needs 0 <= start < end (got 10.0 to 5.0)"  # no second "got"
    # The contract's messages, which creators see, are unchanged.
    from clipskitty_sdk import check_result

    assert check_result({"plugin_api": 1, "ranges": [{"start": 0, "end": 10, "score": 140}]}) == [
        "ranges[0]: score must be a number from 0 to 100, or left out"]


@pytest.mark.skipif(sys.platform != "win32", reason="checks Windows itself (CI's SDK (Windows) job)")
def test_not_on_windows_is_true_on_windows():
    for name in sorted(lint.NOT_ON_WINDOWS):
        done = subprocess.run([sys.executable, "-c", f"import {name}"], capture_output=True, text=True, timeout=60)
        assert done.returncode != 0, f"{name} imports on Windows: take it out of lint.NOT_ON_WINDOWS"
