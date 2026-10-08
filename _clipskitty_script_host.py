"""Run a pipeline's Python script on the Python inside Clips Kitty.

The installed app has no python.exe: its engine is Python frozen by
PyInstaller into api.exe. A pipeline's manifest says `run: ["{python}",
"src/main.py"]`, and in the installed app {python} is api.exe itself, started
with CLIPSKITTY_SCRIPT_HOST=1 (sdk/python/clipskitty_sdk/host.py plugin_env).
main.py sees that marker before it imports anything heavy and hands the
command line to main() here, which behaves like a small `python`:

    api.exe [-u] [-B] ... script.py [args]     run a script as __main__
    api.exe -c "code" [args]                   run a string
    api.exe -m module [args]                   run a module as __main__
    api.exe --multiprocessing-fork ...         a multiprocessing child

What this takes care of, because a frozen interpreter ignores PYTHONPATH,
PYTHONUNBUFFERED and the rest of the PYTHON* variables:

- sys.path: the script's folder, then each PYTHONPATH entry (the SDK and the
  pipeline's own folders), ahead of everything bundled with the app;
- output is line-buffered UTF-8, so progress lines reach Clips Kitty at once;
- Clips Kitty's own packages (core, plugins, video...) can't be imported from
  the bundle: they are not part of the plugin API, they change with every
  release, and keeping them out keeps a pipeline a separate program (D13).
  A pipeline's own folder of the same name still wins;
- a module the app doesn't include stops the run with a plain error line,
  and so does a part of the SDK this app's copy doesn't have yet (the
  pipeline was made with a newer SDK);
- multiprocessing works: a child started with --multiprocessing-fork loads
  the parent's script as __mp_main__ first, which CPython's spawn does not do
  for a frozen Windows program.

It uses the standard library only and imports nothing from the engine.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import json
import os
import runpy
import sys
import traceback
import types

MARKER = "CLIPSKITTY_SCRIPT_HOST"
SCRIPT = "CLIPSKITTY_SCRIPT"  # the running script, for multiprocessing children

# Clips Kitty's own top-level packages and modules. tests/test_script_host.py
# checks this against the repository, so a new engine package can't be missed.
ENGINE_PACKAGES = frozenset({
    "analysis", "core", "creator", "gaming", "llm", "longform", "main", "multilingual", "plugins",
    "publish", "remote_render", "server", "sources", "sports", "third_party", "transcription",
    "video", "video_editor", "_clipskitty_script_host",
})

# Engine commands (main.py). With one of these first, the command line is the
# engine's even when the marker is set, so a stray marker can never turn
# `api.exe serve` into something else.
ENGINE_COMMANDS = frozenset({
    "process", "run", "status", "outro-backfill", "auth", "upload", "serve", "mcp", "channels",
    "models", "render-worker", "--config", "-h", "--help",
})

# Interpreter options a tool may add when it starts sys.executable again
# (subprocess, multiprocessing). They change nothing here; they are accepted
# so the command line around them still works.
_FLAGS = set("uBEIsSOqbdPRv")
_WITH_VALUE = {"W", "X"}


class UsageError(Exception):
    pass


def wants_host(argv: list[str]) -> bool:
    """True when main.py should hand this command line to main()."""
    if os.environ.get(MARKER) != "1" or len(argv) < 2:
        return False
    return argv[1] not in ENGINE_COMMANDS


def parse(argv: list[str]) -> tuple[str, str | None, list[str]]:
    """(mode, target, rest) from a python-style command line (without the
    program name). mode is "script", "code", "module" or "fork"."""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--multiprocessing-fork":
            return "fork", None, argv[i:]
        if arg == "-c" or arg == "-m":
            if i + 1 >= len(argv):
                raise UsageError(f"{arg} needs a value")
            return ("code" if arg == "-c" else "module"), argv[i + 1], argv[i + 2:]
        if arg.startswith("-c") or arg.startswith("-m"):
            return ("code" if arg[1] == "c" else "module"), arg[2:], argv[i + 1:]
        if arg == "--":
            i += 1
            continue
        if arg.startswith("-") and arg != "-":
            letters = arg[1:]
            j = 0
            while j < len(letters):
                ch = letters[j]
                if ch in _WITH_VALUE:
                    if j + 1 < len(letters):  # -Xutf8
                        break
                    i += 1  # -X utf8
                    if i >= len(argv):
                        raise UsageError(f"-{ch} needs a value")
                    break
                if ch in "cm":
                    mode = "code" if ch == "c" else "module"
                    value = letters[j + 1:]
                    if value:
                        return mode, value, argv[i + 1:]
                    if i + 1 >= len(argv):
                        raise UsageError(f"-{ch} needs a value")
                    return mode, argv[i + 1], argv[i + 2:]
                if ch not in _FLAGS:
                    raise UsageError(f"option {arg!r} isn't supported by Clips Kitty's Python")
                j += 1
            i += 1
            continue
        if arg == "-":
            raise UsageError("reading a script from standard input isn't supported by Clips Kitty's Python")
        return "script", arg, argv[i + 1:]
    raise UsageError("no script to run")


def _python_path() -> list[str]:
    return [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]


def _front(paths: list[str]) -> None:
    """Put `paths` at the front of sys.path, in order, without duplicates."""
    for p in reversed(paths):
        while p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)


def _engine_roots() -> list[str]:
    """Where the engine's own code lives: the bundle in the installed app,
    the checkout's root in a source checkout."""
    roots = [os.path.dirname(os.path.abspath(__file__))]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.append(os.path.abspath(bundle))
    return roots


def _under(path: str, roots: list[str]) -> bool:
    full = os.path.abspath(path or os.curdir)
    return any(full == r or full.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


class EngineGuard(importlib.abc.MetaPathFinder):
    """Refuses Clips Kitty's own packages unless the pipeline's own folders
    have a module of that name, which then wins. The pipeline's folders are
    the ones it was started with plus any sys.path entry outside the engine's
    own (a pipeline may add a folder of its own at run time)."""

    def __init__(self, own_paths: list[str]):
        self.own_paths = list(own_paths)
        self.engine_roots = _engine_roots()

    def find_spec(self, fullname, path=None, target=None):
        top = fullname.partition(".")[0]
        if top not in ENGINE_PACKAGES or path is not None:
            return None  # not ours, or a submodule of something already allowed
        search = self.own_paths + [p for p in sys.path if isinstance(p, str) and p not in self.own_paths
                                   and not _under(p, self.engine_roots)]
        spec = importlib.machinery.PathFinder.find_spec(fullname, search)
        if spec is not None:
            return spec
        raise ModuleNotFoundError(
            f"No module named {fullname!r}: Clips Kitty's own code isn't available to pipelines", name=fullname)


def _setup_output() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError, OSError):
            pass  # not a text stream that can change (replaced, or a pipe that's gone): leave it as it is


def _say_error(message: str) -> None:
    # The contract's error line (sdk/python/clipskitty_sdk/contract.py
    # error_line), written here so this module needs nothing from the SDK.
    try:
        print(json.dumps({"type": "error", "message": message}, ensure_ascii=False), flush=True)
    except Exception:
        pass  # nowhere to report it: the exit code still says the run failed


# The error line for a pipeline that imports a part of the SDK this app's
# copy doesn't have. The SDK's run() says the same for an import inside
# main() (sdk/python/clipskitty_sdk/job.py NEWER_VERSION); written here so
# this module needs nothing from the SDK.
NEWER_VERSION = ("This pipeline needs a newer version of Clips Kitty. Update Clips Kitty, or ask the pipeline's "
                 "developer which version it needs.")


def needs_newer_sdk(error: BaseException) -> bool:
    """An import of a module or name the bundled SDK doesn't have: `import
    clipskitty_sdk.media`, `from clipskitty_sdk import media` (name
    clipskitty_sdk) or `from clipskitty_sdk.media import Region` (name
    clipskitty_sdk.media). The SDK's job.needs_newer_sdk is the same rule."""
    name = getattr(error, "name", None) or ""
    return isinstance(error, ImportError) and (name == "clipskitty_sdk" or name.startswith("clipskitty_sdk."))


def missing_module_message(error: ModuleNotFoundError) -> str:
    name = (error.name or "").partition(".")[0] or "a module"
    if name in ENGINE_PACKAGES:
        return (f"This pipeline tried to use Clips Kitty's own code ({name}), which pipelines can't use. "
                "Ask its developer to update it.")
    return (f"This pipeline needs {name}, which this version of Clips Kitty doesn't include. "
            "Ask its developer to update it.")


def _install_guard(own_paths: list[str]) -> None:
    sys.meta_path.insert(0, EngineGuard(own_paths))


def _run_main_code(code: str, filename: str, argv0: str, rest: list[str]) -> None:
    module = types.ModuleType("__main__")
    module.__dict__["__builtins__"] = __builtins__
    module.__file__ = filename if filename != "<string>" else None
    if module.__file__ is None:
        del module.__file__
    sys.modules["__main__"] = module
    sys.argv = [argv0, *rest]
    exec(compile(code, filename, "exec"), module.__dict__)


def _fork_child(rest: list[str]) -> None:
    """A multiprocessing child: load the parent's script as __mp_main__ (as
    CPython's spawn does for an ordinary Python), then let multiprocessing
    take over. freeze_support() runs the child and exits."""
    script = os.environ.get(SCRIPT)
    if script and os.path.isfile(script):
        _front([os.path.dirname(script)])
        content = runpy.run_path(script, run_name="__mp_main__")
        main_module = types.ModuleType("__mp_main__")
        main_module.__dict__.update(content)
        sys.modules["__mp_main__"] = sys.modules["__main__"] = main_module
    sys.argv = [sys.argv[0], *rest]
    import multiprocessing
    multiprocessing.freeze_support()
    raise UsageError("--multiprocessing-fork without a multiprocessing parent")


def main(argv: list[str]) -> int:
    """Run the command line `argv` (without the program name). Returns the
    exit code; a SystemExit from the script passes through untouched."""
    _setup_output()
    try:
        mode, target, rest = parse(argv)
    except UsageError as e:
        _say_error(f"Clips Kitty's Python: {e}")
        return 2

    own = _python_path()
    script = os.path.abspath(target) if mode == "script" else ""
    if mode == "script":
        own = [os.path.dirname(script), *own]
        os.environ[SCRIPT] = script
    elif mode == "fork":
        parent = os.environ.get(SCRIPT)
        own = ([os.path.dirname(parent)] if parent else []) + own
    else:
        os.environ.pop(SCRIPT, None)  # a -c or -m child must not reload some other script
        own = [os.getcwd(), *own]
    _front(own)
    _install_guard(own)

    try:
        if mode == "fork":
            _fork_child(rest)
        elif mode == "code":
            _run_main_code(target, "<string>", "-c", rest)
        elif mode == "module":
            sys.argv = [target, *rest]
            runpy.run_module(target, run_name="__main__", alter_sys=True)
        else:
            if not os.path.isfile(script):
                _say_error(f"Clips Kitty's Python: can't find the script {target!r}")
                return 2
            sys.argv = [target, *rest]
            runpy.run_path(script, run_name="__main__")
    except SystemExit:
        raise
    except UsageError as e:
        _say_error(f"Clips Kitty's Python: {e}")
        return 2
    except ImportError as e:
        traceback.print_exc()
        if needs_newer_sdk(e):
            _say_error(NEWER_VERSION)
        elif isinstance(e, ModuleNotFoundError):
            _say_error(missing_module_message(e))
        return 1  # any other ImportError is a crash in the script, as below
    except Exception:  # a crash in the script is exit code 1, as in python; Ctrl+C passes through
        traceback.print_exc()
        return 1
    return 0
