"""Pipelines run on the Python inside Clips Kitty (_clipskitty_script_host.py).

The installed app's engine is Python frozen into api.exe; started with the
plugin marker it runs a pipeline's script like a small `python` would. These
run main.py the same way with this test's Python, which goes through the same
code path up to the point a frozen build differs (multiprocessing on frozen
Windows is proven by scripts/build_installer.py's smoke test on a real build).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import _clipskitty_script_host as script_host
from plugins import runner

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"
SDK = ROOT / "sdk" / "python"


def _run(args, tmp_path, *, python_path=(), marker="1", cwd=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLIPSKITTY_", "PYTHON"))}
    if marker is not None:
        env["CLIPSKITTY_SCRIPT_HOST"] = marker
    env["PYTHONPATH"] = os.pathsep.join([str(SDK), *map(str, python_path)])
    return subprocess.run([sys.executable, str(MAIN), *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd or tmp_path), env=env, timeout=120)


def _error_lines(stdout: str) -> list[str]:
    out = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "error":
            out.append(event["message"])
    return out


def test_a_script_runs_as_main_with_its_arguments_paths_and_exit_code(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "helper.py").write_text("VALUE = 'from the plugin path'\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "import json, sys, helper\n"
        "import clipskitty_sdk\n"
        "print(json.dumps({'name': __name__, 'argv': sys.argv, 'path': sys.path[:3], 'value': helper.VALUE,\n"
        "                  'heavy': sorted(m for m in ('yaml', 'numpy', 'core', 'plugins') if m in sys.modules)}))\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(3)\n")
    done = _run([src / "main.py", "job-folder", "--flag"], tmp_path, python_path=[lib])
    assert done.returncode == 3, done.stderr
    got = json.loads(done.stdout.splitlines()[0])
    assert got["name"] == "__main__"
    assert got["argv"] == [str(src / "main.py"), "job-folder", "--flag"]
    assert got["path"] == [str(src), str(SDK), str(lib)]
    assert got["value"] == "from the plugin path"
    assert got["heavy"] == []  # the engine's start-up imports never ran


def test_interpreter_options_before_the_script_are_accepted(tmp_path):
    (tmp_path / "s.py").write_text("print('ran')\n")
    done = _run(["-u", "-X", "utf8", "-B", tmp_path / "s.py"], tmp_path)
    assert done.returncode == 0 and done.stdout.strip() == "ran", done.stderr


def test_a_crash_exits_with_1_and_keeps_the_traceback(tmp_path):
    (tmp_path / "s.py").write_text("raise RuntimeError('boom')\n")
    done = _run([tmp_path / "s.py"], tmp_path)
    assert done.returncode == 1
    assert "RuntimeError: boom" in done.stderr


def test_code_and_modules_run_like_python(tmp_path):
    done = _run(["-c", "import sys; print(__name__, sys.argv)", "a"], tmp_path)
    assert done.returncode == 0 and done.stdout.strip() == "__main__ ['-c', 'a']", done.stderr
    (tmp_path / "tool.py").write_text("import sys\nif __name__ == '__main__':\n    print('tool', sys.argv[1:])\n")
    done = _run(["-m", "tool", "x"], tmp_path)
    assert done.returncode == 0 and done.stdout.strip() == "tool ['x']", done.stderr


def test_the_engines_own_code_is_refused_with_a_plain_message(tmp_path):
    (tmp_path / "s.py").write_text("import core.pipeline\n")
    done = _run([tmp_path / "s.py"], tmp_path)
    assert done.returncode == 1
    assert _error_lines(done.stdout) == [
        ("This pipeline tried to use Clips Kitty's own code (core), which pipelines can't use. "
         "Ask its developer to update it.")]


def test_a_pipelines_own_package_with_an_engine_name_still_wins(tmp_path):
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "__init__.py").write_text("WHOSE = 'mine'\n")
    (tmp_path / "s.py").write_text("import plugins\nprint(plugins.WHOSE)\n")
    done = _run([tmp_path / "s.py"], tmp_path)
    assert done.returncode == 0 and done.stdout.strip() == "mine", done.stderr


def test_a_module_the_app_lacks_gives_a_plain_error_line(tmp_path):
    (tmp_path / "s.py").write_text("import not_a_real_package_xyz\n")
    done = _run([tmp_path / "s.py"], tmp_path)
    assert done.returncode == 1
    assert _error_lines(done.stdout) == [
        ("This pipeline needs not_a_real_package_xyz, which this version of Clips Kitty doesn't include. "
         "Ask its developer to update it.")]


def test_a_multiprocessing_child_loads_the_parents_script_first(tmp_path):
    """A frozen Windows child is started as `api.exe --multiprocessing-fork`
    and CPython doesn't reload the main script for it; the host does, as
    __mp_main__. (Off frozen Windows, freeze_support() then does nothing, so
    the host reports it.)"""
    script = tmp_path / "s.py"
    script.write_text(f"if __name__ == '__mp_main__':\n    open({str(tmp_path / 'loaded')!r}, 'w').write('yes')\n")
    env_script = {"CLIPSKITTY_SCRIPT": str(script)}
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLIPSKITTY_", "PYTHON"))}
    env.update(env_script, CLIPSKITTY_SCRIPT_HOST="1", PYTHONPATH=str(SDK))
    done = subprocess.run([sys.executable, str(MAIN), "--multiprocessing-fork", "parent_pid=1"],
                          capture_output=True, text=True, cwd=str(tmp_path), env=env, timeout=120)
    assert (tmp_path / "loaded").read_text() == "yes"
    assert done.returncode == 2 and _error_lines(done.stdout)


def test_without_the_marker_main_py_is_the_engine(tmp_path):
    (tmp_path / "s.py").write_text("print('ran')\n")
    done = _run([tmp_path / "s.py"], tmp_path, marker=None)
    assert done.returncode != 0 and "ran" not in done.stdout  # argparse refuses it as a command


def test_engine_commands_stay_the_engines_even_with_the_marker(monkeypatch):
    monkeypatch.setenv("CLIPSKITTY_SCRIPT_HOST", "1")
    for command in ("serve", "status", "render-worker", "--config", "process"):
        assert not script_host.wants_host(["api.exe", command])
    assert script_host.wants_host(["api.exe", "src/main.py"])
    assert script_host.wants_host(["api.exe", "-c", "pass"])
    monkeypatch.setenv("CLIPSKITTY_SCRIPT_HOST", "0")
    assert not script_host.wants_host(["api.exe", "src/main.py"])


@pytest.mark.parametrize("argv, expected", [
    (["s.py", "a"], ("script", "s.py", ["a"])),
    (["-u", "s.py"], ("script", "s.py", [])),
    (["-uB", "-X", "utf8", "s.py"], ("script", "s.py", [])),
    (["-Xutf8", "-W", "ignore", "s.py", "-c"], ("script", "s.py", ["-c"])),
    (["-c", "print(1)", "x"], ("code", "print(1)", ["x"])),
    (["-uc", "print(1)"], ("code", "print(1)", [])),
    (["-cprint(1)"], ("code", "print(1)", [])),
    (["-m", "pkg.mod", "--x"], ("module", "pkg.mod", ["--x"])),
    (["--multiprocessing-fork", "parent_pid=1"], ("fork", None, ["--multiprocessing-fork", "parent_pid=1"])),
])
def test_command_lines_are_read_like_python_reads_them(argv, expected):
    assert script_host.parse(argv) == expected


@pytest.mark.parametrize("argv", [[], ["-c"], ["-m"], ["--version"], ["-"], ["-X"]])
def test_command_lines_it_cant_run_are_refused(argv):
    with pytest.raises(script_host.UsageError):
        script_host.parse(argv)


def test_every_engine_package_is_guarded():
    """A new top-level package in the engine must join ENGINE_PACKAGES, or a
    pipeline could import it from the bundle."""
    packages = {p.name for p in ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    modules = {p.stem for p in ROOT.glob("*.py")}
    assert packages | modules <= script_host.ENGINE_PACKAGES


def test_the_installed_app_runs_plugins_on_its_own_python(monkeypatch):
    """Frozen: {python} is the engine itself, and PATH is never searched, so
    Windows' "python" shortcut to the Store can't be picked."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Clips Kitty\resources\backend\api.exe")
    monkeypatch.setattr(runner.host, "find_python", lambda *a, **k: pytest.fail("searched for a Python"))
    assert runner.python_for(None, {}) == r"C:\Program Files\Clips Kitty\resources\backend\api.exe"
    # a developer's own choice still wins
    assert runner.python_for(None, {"plugins": {"python": "C:/Python312/python.exe"}}) == "C:/Python312/python.exe"


def test_a_source_checkout_uses_its_own_python(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert runner.python_for(None, {}) == sys.executable


def test_a_folder_the_pipeline_adds_at_run_time_counts_as_its_own(tmp_path):
    vendor = tmp_path / "vendor"
    (vendor / "video").mkdir(parents=True)
    (vendor / "video" / "__init__.py").write_text("WHOSE = 'vendored'\n")
    (tmp_path / "s.py").write_text(f"import sys\nsys.path.insert(0, {str(vendor)!r})\nimport video\nprint(video.WHOSE)\n")
    done = _run([tmp_path / "s.py"], tmp_path)
    assert done.returncode == 0 and done.stdout.strip() == "vendored", done.stderr
