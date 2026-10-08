# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Warnings about a plugin's Python code that would work on the developer's
PC and fail on a creator's: `python -m clipskitty_sdk validate` and `run`
print them. Clips Kitty itself never runs this (the manifest checks in
manifest.validate are the shared rule book).

    from clipskitty_sdk.lint import lint_folder

    for line in lint_folder("."):
        print("warning:", line)   # src/main.py line 3: imports numpy. ...

It reads each .py file in the plugin's folder (never runs it) and warns
about:

- syntax newer than Clips Kitty's Python (host.APP_PYTHON);
- imports Clips Kitty's Python doesn't promise: anything but the standard
  library (less the modules the app leaves out, and those Windows lacks),
  clipskitty_sdk and the plugin's own modules beside the script;
- text files opened without `encoding=`.

Files under tests/, and test_*.py and conftest.py anywhere, are skipped:
they run only on the developer's PC, so `import pytest` there is fine.
Standard library only.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

from .host import APP_PYTHON

# Clips Kitty's own top-level packages and modules: a pipeline can't import
# them, and the app stops a run that tries (_clipskitty_script_host.py
# ENGINE_PACKAGES; tests/test_script_host.py checks the two lists agree).
ENGINE_PACKAGES = frozenset({
    "analysis", "core", "creator", "gaming", "llm", "longform", "main", "multilingual", "plugins",
    "publish", "remote_render", "server", "sources", "sports", "third_party", "transcription",
    "video", "video_editor", "_clipskitty_script_host",
})

# Standard library modules the installed app leaves out on purpose: GUI
# toolkits, the test suite and tools for building or installing Python
# (STDLIB_SKIP in clips-studio.spec; tests/test_packaging.py checks).
APP_LEAVES_OUT = frozenset({
    "tkinter", "_tkinter", "turtle", "turtledemo", "idlelib", "test", "ensurepip", "venv", "lib2to3",
    "distutils", "pydoc_data", "antigravity", "this",
})

# Standard library modules that don't exist on Windows, where Clips Kitty
# runs. The app bundles only what the Windows PC that builds it has, so these
# are missing there. CI's SDK (Windows) job checks each fails to import.
NOT_ON_WINDOWS = frozenset({
    "fcntl", "grp", "pwd", "resource", "termios", "tty", "pty", "readline", "curses", "syslog", "posix",
})

# Every standard library module Clips Kitty's Python doesn't have.
NOT_BUNDLED = APP_LEAVES_OUT | NOT_ON_WINDOWS

# Folders never read: version control, caches, virtual environments, and
# tests (they run on the developer's PC only).
SKIPPED_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", ".clipskitty", "node_modules", "tests"})

_MODE = re.compile(r"^[rwxabt+U]{1,4}$")
_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})


def _python_files(folder: Path) -> list[Path]:
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS and not os.path.islink(os.path.join(root, d)))
        for name in sorted(files):
            path = Path(root) / name
            if (name.endswith(".py") and not name.startswith("test_") and name != "conftest.py"
                    and not path.is_symlink()):
                out.append(path)
    return out


def _script(folder: Path, manifest: dict | None) -> Path | None:
    """The script run.command starts with {python}, when there is one."""
    run = (manifest or {}).get("run")
    command = run.get("command") if isinstance(run, dict) else None
    if (isinstance(command, list) and len(command) > 1 and command[0] == "{python}"
            and isinstance(command[1], str) and command[1].endswith(".py")):
        script = (folder / command[1]).resolve()
        if folder in script.parents:
            return script
    return None


def _home(path: Path, folder: Path, script: Path | None) -> Path:
    """The folder on sys.path when `path` runs or is imported. Clips Kitty
    puts only the script's own folder there (with the SDK), so for the
    script and anything under its folder that is the script's folder. For
    another file it is the file's own folder, or for a module inside a
    package (a folder with __init__.py), the folder holding the outermost
    package, never above the plugin's folder."""
    if script is not None and (path == script or script.parent in path.parents):
        return script.parent
    home = path.parent
    while home != folder and (home / "__init__.py").is_file():
        home = home.parent
    return home


def _modules_in(folder: Path) -> set[str]:
    """The top-level names a folder on sys.path provides: its .py files and
    its folders (packages, or namespace packages)."""
    names = set()
    try:
        entries = list(folder.iterdir())
    except OSError:
        return names
    for entry in entries:
        if entry.is_file() and entry.suffix == ".py":
            names.add(entry.stem)
        elif entry.is_dir() and entry.name not in SKIPPED_DIRS and entry.name.isidentifier():
            names.add(entry.name)
    return names


def _guarded(tree: ast.AST) -> set[int]:
    """The ids of import nodes inside a `try` that catches ImportError (or
    something wider): the plugin is ready for them to fail."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches = False
        for handler in node.handlers:
            kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            if handler.type is None or any(isinstance(k, ast.Name) and k.id in _IMPORT_ERRORS for k in kinds):
                catches = True
        if catches:
            for statement in node.body:
                guarded |= {id(n) for n in ast.walk(statement) if isinstance(n, (ast.Import, ast.ImportFrom))}
    return guarded


def _imported(tree: ast.AST) -> list[tuple[str, int, int]]:
    """(top-level module, line, node id) for each absolute import."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((alias.name.split(".")[0], node.lineno, id(node)) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append((node.module.split(".")[0], node.lineno, id(node)))
    return sorted(out, key=lambda item: item[1])


def _import_problem(name: str, own: set[str], folder: Path, *, at_root: bool, script: str,
                    home: str) -> str | None:
    if name == "clipskitty_sdk" or name in own:
        return None
    if name in ENGINE_PACKAGES:
        return (f"imports {name}, which is Clips Kitty's own code: pipelines can't use it, and Clips Kitty "
                "stops a run that tries")
    if name in NOT_ON_WINDOWS:
        return f"imports {name}, which isn't on Windows, where Clips Kitty runs"
    if name in NOT_BUNDLED:
        return f"imports {name}, which Clips Kitty's Python leaves out"
    if name in sys.stdlib_module_names:
        return None
    if not at_root and name in _modules_in(folder):
        shown = f"{name}.py" if (folder / f"{name}.py").is_file() else f"{name}/"
        return (f"imports {name}, but {shown} is at the plugin's root, which isn't on the path when Clips "
                f"Kitty runs {script}: move it into {home}/")
    return (f"imports {name}. Pipelines run on Clips Kitty's own Python, which promises only the standard "
            "library and clipskitty_sdk, so this may fail on creators' PCs")


def _call_name(node: ast.Call) -> tuple[str, bool]:
    """The called name, and whether it is called on something (x.name())."""
    if isinstance(node.func, ast.Name):
        return node.func.id, False
    if isinstance(node.func, ast.Attribute):
        return node.func.attr, True
    return "", False


def _without_encoding(node: ast.Call, modules: set[str]) -> str | None:
    """The name of a text-file call made without encoding=, or None."""
    name, method = _call_name(node)
    keywords = {k.arg for k in node.keywords}
    if None in keywords or "encoding" in keywords:  # **kwargs may hold it
        return None
    receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
    on_io = isinstance(receiver, ast.Name) and receiver.id == "io"
    if name == "open" and (not method or on_io):
        # open(file, mode, buffering, encoding)
        mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
        if len(node.args) > 3:
            return None
    elif name == "open":
        # Path.open(mode, buffering, encoding). Other things have an open()
        # too (zip files, gzip, tarfile, webbrowser), so only a call that is
        # clearly Path.open counts: no arguments, or a mode first.
        if isinstance(receiver, ast.Name) and receiver.id in modules:
            return None
        if node.args:
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str) and _MODE.match(first.value)):
                return None
            if len(node.args) > 2:
                return None
            mode = first
        else:
            mode = next((k.value for k in node.keywords if k.arg == "mode"), None)
    elif name == "read_text" and method:
        return None if node.args else "read_text()"
    elif name == "write_text" and method:
        return None if len(node.args) > 1 else "write_text()"
    else:
        return None
    if mode is None:
        return "open()"
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return None if "b" in mode.value else "open()"
    return None  # a mode worked out at run time: can't tell


def _module_names(tree: ast.AST) -> set[str]:
    """The names `import x` binds in a file, to tell gzip.open from path.open."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.asname or alias.name.split(".")[0] for alias in node.names}
    return names


def lint_file(path: Path, folder: Path, *, script: Path | None = None) -> list[str]:
    """The warnings for one file of the plugin in `folder`, each as
    "src/main.py line 3: ..." (without "warning: ")."""
    rel = path.relative_to(folder).as_posix()
    try:
        source = path.read_bytes()
    except OSError as e:
        return [f"{rel}: can't read it ({e.strerror or e})"]
    try:
        tree = ast.parse(source, filename=rel)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as e:
        line = getattr(e, "lineno", None)
        where = f"{rel} line {line}" if line else rel
        return [f"{where}: this isn't Python this PC can read: {getattr(e, 'msg', None) or e or type(e).__name__}"]
    problems = []
    try:
        ast.parse(source, filename=rel, feature_version=APP_PYTHON)
    except SyntaxError as e:
        version = ".".join(map(str, APP_PYTHON))
        problems.append(f"{rel} line {e.lineno}: this needs a newer Python than Clips Kitty's ({version}): {e.msg}")
    except (ValueError, RecursionError, MemoryError):
        pass  # the first parse read it; nothing more to say

    home = _home(path, folder, script)
    own = _modules_in(home)
    at_root = home == folder
    script_rel = script.relative_to(folder).as_posix() if script is not None else rel
    home_rel = home.relative_to(folder).as_posix()
    guarded = _guarded(tree)
    for name, line, node_id in _imported(tree):
        if node_id in guarded:
            continue
        problem = _import_problem(name, own, folder, at_root=at_root, script=script_rel, home=home_rel)
        if problem:
            problems.append(f"{rel} line {line}: {problem}")

    modules = _module_names(tree)
    calls = sorted((n for n in ast.walk(tree) if isinstance(n, ast.Call)), key=lambda n: (n.lineno, n.col_offset))
    for node in calls:
        called = _without_encoding(node, modules)
        if called:
            problems.append(f"{rel} line {node.lineno}: {called} without encoding=. Clips Kitty's Python doesn't "
                            "use UTF-8 mode, so on Windows text files would use the PC's own code page: pass "
                            'encoding="utf-8"')
    return problems


def lint_folder(folder: str | os.PathLike, manifest: dict | None = None) -> list[str]:
    """Every warning for the plugin in `folder`, file by file. `manifest`
    (its clipskitty.yaml, when read) names the script Clips Kitty starts."""
    folder = Path(folder).resolve()
    script = _script(folder, manifest)
    out = []
    for path in _python_files(folder):
        out += lint_file(path, folder, script=script)
    return out


__all__ = ["APP_LEAVES_OUT", "ENGINE_PACKAGES", "NOT_BUNDLED", "NOT_ON_WINDOWS", "lint_file", "lint_folder"]
