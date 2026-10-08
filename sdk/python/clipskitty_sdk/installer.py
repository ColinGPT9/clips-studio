# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""The core of `python -m clipskitty_sdk install`: put a plugin you are
writing into the Clips Kitty running on this PC, as Marketplace › Browse ›
For developers does, from the command line.

    python -m clipskitty_sdk install FOLDER [--watch] [--yes] [--data-dir DIR] [--api http://127.0.0.1:8765]

In order, it:

1. refuses development folders (.venv, venv, node_modules) and symbolic
   links, which Clips Kitty would copy into every install or refuse, and warns
   about .clipskitty and big videos, which it would copy (development_files);
2. checks the plugin as `validate` does, lint included;
3. talks only to Clips Kitty on this PC (127.0.0.1, localhost or ::1), never
   through a proxy (_loopback);
4. asks GET /health whether Clips Kitty is running, and GET /plugins whether it
   can install plugins at all, before it looks for anything else;
5. finds the session file Clips Kitty writes for scripts (session_file):
   --data-dir, then the path Clips Kitty's own 403 answer names, then the
   installed app's data folder;
6. asks Clips Kitty for a plan (POST /plugins/plan), prints its install screen
   as text, asks, and installs (POST /plugins/install).

`--yes` skips the question only when nothing is new (nothing_new): the same
plugin is installed and the update adds no permission, network host, data
sent off the PC, change to where it runs, or step. `--watch` reinstalls on
each save with the same check, and stops when a save adds something.

The session secret is read from its file for each request, sent only in the
X-Clips-Kitty-Session header to Clips Kitty on this PC, and never printed,
logged, written anywhere, or put in a command line or the environment. It
keeps out web pages and scripts that don't know it, not programs running as
you: any program running as the user can read its file, plugins included.
Only _call here sends that header. LocalAPI never does: plugins use LocalAPI,
and a plugin must not install or remove plugins.

Exit codes: 0 installed (or --watch stopped with Ctrl+C), 1 not installed
(Clips Kitty refused it, the question was answered no, or --yes or --watch
found something new), 2 it couldn't get as far as a plan. Standard library
only.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import _loopback, devrun
from .manifest import validate_folder

DEFAULT_API = "http://127.0.0.1:8765"
# The header Clips Kitty's plugin manager asks for, and the file it writes the
# secret to, inside its data folder (plugins/session.py in Clips Kitty).
HEADER = "X-Clips-Kitty-Session"
SESSION_FILE = "session.secret"
_SECRET_SHAPE = re.compile(r"[A-Za-z0-9_-]{16,256}")
# The root README's "From source" heading.
FROM_SOURCE = "https://github.com/ColinGPT9/clips-studio#from-source"

# Folders a developer's tools make, which Clips Kitty would copy into every install.
DEV_FOLDERS = (".venv", "venv", "node_modules")
# What Clips Kitty leaves out when it copies a plugin folder.
NOT_COPIED = (".git", "__pycache__")
# Copied, though only there for development.
DEV_ONLY = (".clipskitty",)
VIDEO_SUFFIXES = frozenset({".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".mts", ".m2ts"})
BIG_VIDEO_BYTES = 50 * 1024 * 1024
# What --watch doesn't look at.
WATCH_SKIPS = frozenset(NOT_COPIED + DEV_FOLDERS + DEV_ONLY)

HEALTH_TIMEOUT = 10.0
# A plan copies the folder and an install moves it into place: big folders take a while.
PLAN_TIMEOUT = 300.0
# A plan id that names no plan, sent without the header to learn where the session file is.
_PROBE = {"plan_id": "0" * 16}

QUESTION = "Install it? [y/N] "
INSTALLED = "Installed {id} {version}. Choose it in Clips Kitty: Pipeline (to find moments) or Rate & understand."
REINSTALLED = "Reinstalled {id} {version} at {time}"
NOT_INSTALLED = "Not installed."
STOPPED = "Stopped watching."

DEV_FOLDER = ("{name} is inside the plugin's folder, and Clips Kitty would copy it into every install. Move it next "
              "to the folder, then run this again.")
COPIED = "{name} is inside the plugin's folder; Clips Kitty copies everything there except .git and __pycache__."
LINK = "{path} is a symbolic link, and Clips Kitty refuses to install a folder that has one"
OFF_THIS_PC = "install only talks to Clips Kitty on this PC (127.0.0.1, localhost or ::1)"
NOT_RUNNING = "Clips Kitty isn't running: nothing answered at {api}. Open the app, then run this again."
NOT_CLIPS_KITTY = "something answered at {api}, but not as Clips Kitty does: is --api right?"
TOO_OLD = ("This Clips Kitty ({app_version}) can't install plugins: it came out before plugin support. Use a newer "
           f"Clips Kitty, or run it from source: {FROM_SOURCE}")
NO_SESSION_FILE = ("couldn't find Clips Kitty's session file. Pass --data-dir with Clips Kitty's data folder (the one "
                   "holding plugins/session.secret)")
SESSION_REFUSED = ("Clips Kitty didn't accept the session file {path}: it may belong to another Clips Kitty, or one "
                   "that has since restarted. Pass --data-dir with the data folder of the one that is running (the "
                   "one holding plugins/session.secret)")
YES_REFUSED = ("--yes only skips the question when nothing is new; this adds: {new}. Run without --yes and answer "
               "the question.")
WATCH_REFUSED = "Stopped watching: this change adds {new}. Run install again to see it and answer."
APP_REFUSED = "Clips Kitty won't install it, for the reasons above"

# What a plan's `update` says is new, in the order the install screen shows it.
_NEW_LISTS = {"added_permissions": "permissions", "added_hosts": "network hosts",
              "added_data_warnings": "data sent off the PC", "added_steps": "steps"}
FIRST_INSTALL = "a plugin that isn't installed in this Clips Kitty yet"
UNSAID = "{what} (this Clips Kitty's plan doesn't say, so it counts as new)"


class InstallRefused(Exception):
    """Why install stops: printed as `error: ...`, with `code` as the exit code."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


# ---- the plugin's folder -------------------------------------------------------------


def _is_link(path: Path) -> bool:
    """A symbolic link, or a Windows junction (which Path.is_symlink doesn't
    report before Python 3.12): Clips Kitty refuses both."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    junction = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
    return getattr(info, "st_reparse_tag", 0) == junction


def development_files(folder: str | os.PathLike) -> list[str]:
    """Check a plugin folder for what Clips Kitty shouldn't copy. Raises
    InstallRefused for a development folder (.venv, venv, node_modules) or a
    symbolic link; returns a warning for .clipskitty and each video over 50
    MB, which it would copy. Clips Kitty copies everything but .git and
    __pycache__, on every --watch save too."""
    folder = Path(folder)
    if _is_link(folder):
        raise InstallRefused(LINK.format(path=folder))
    dev, links, warnings = [], [], []
    for here, dirs, names in os.walk(folder, followlinks=False):
        base = Path(here)
        dirs.sort()
        for name in list(dirs):
            rel = (base / name).relative_to(folder).as_posix()
            if name in DEV_FOLDERS:
                dev.append(rel)
                dirs.remove(name)
            elif _is_link(base / name):
                links.append(rel)
                dirs.remove(name)
            elif name in NOT_COPIED:
                dirs.remove(name)
            elif name in DEV_ONLY:
                warnings.append(COPIED.format(name=rel))
        for name in sorted(names):
            path = base / name
            rel = path.relative_to(folder).as_posix()
            if _is_link(path):
                links.append(rel)
            elif path.suffix.lower() in VIDEO_SUFFIXES:
                try:
                    big = path.stat().st_size > BIG_VIDEO_BYTES
                except OSError:
                    big = False
                if big:
                    warnings.append(COPIED.format(name=rel))
    # A virtual environment holds symbolic links: say what it is, not what is in it.
    if dev:
        raise InstallRefused(DEV_FOLDER.format(name=dev[0]))
    if links:
        raise InstallRefused(LINK.format(path=links[0]))
    return warnings


def _checked(folder: Path, err) -> Path:
    """The development-file check, then the manifest and the code as
    `validate` checks them, printing warnings to `err`. Returns the folder,
    resolved."""
    for warning in development_files(folder):
        _say(err, f"warning: {warning}")
    folder = folder.resolve()
    refusal = devrun.manifest_refusal(folder)
    if refusal:
        raise InstallRefused(refusal)
    manifest, report = validate_folder(folder)
    for line in devrun.report_lines(folder, report, manifest):
        _say(err, line)
    if not report.ok:
        raise InstallRefused("fix the manifest first; Clips Kitty would refuse to install this plugin")
    return folder


# ---- talking to Clips Kitty -------------------------------------------------------------


def _secret_in(path: Path) -> str:
    """The session secret in `path`, read now. Never kept, printed or logged."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise InstallRefused(NO_SESSION_FILE) from None
    if not _SECRET_SHAPE.fullmatch(value):
        raise InstallRefused(NO_SESSION_FILE)
    return value


def _call(api: str, method: str, path: str, body=None, secret: Path | None = None, *,
          timeout: float = HEALTH_TIMEOUT) -> tuple[int, object]:
    """One request to Clips Kitty on this PC: (HTTP status, its JSON answer,
    or None). With `secret` (the session file), the secret read from it goes
    in the session header: the only place the SDK sends it. Every request goes
    through _loopback, so proxy settings never route it off this PC."""
    if not _loopback.is_this_pc(api):
        raise InstallRefused(OFF_THIS_PC)
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if secret is not None:
        headers[HEADER] = _secret_in(secret)
    request = urllib.request.Request(api.rstrip("/") + path, data=data, method=method, headers=headers)
    try:
        with _loopback.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        if isinstance(getattr(e, "reason", e), TimeoutError):
            raise InstallRefused(f"Clips Kitty didn't answer {method} {path} within {timeout:g} seconds", 1) from None
        raise InstallRefused(NOT_RUNNING.format(api=api)) from None
    try:
        return status, (json.loads(raw) if raw else None)
    except ValueError:
        return status, None


def _detail(answer) -> str:
    """The message in a refusal's answer ({"detail": ...}, as Clips Kitty gives it)."""
    if isinstance(answer, dict) and isinstance(answer.get("detail"), str):
        return answer["detail"]
    return "" if answer is None else json.dumps(answer)


def can_install_plugins(api: str) -> str:
    """Check that Clips Kitty is running at `api` and has plugin routes, and
    return its version. An app from before plugin support (2.0.0, say) answers
    404 to GET /plugins, which needs no secret, so this comes before any
    looking for the session file."""
    status, info = _call(api, "GET", "/health")
    if status != 200 or not isinstance(info, dict) or not info.get("ok"):
        raise InstallRefused(NOT_CLIPS_KITTY.format(api=api))
    version = str(info.get("app_version") or "?")
    status, answer = _call(api, "GET", "/plugins")
    if status == 404:
        raise InstallRefused(TOO_OLD.format(app_version=version))
    if status != 200:
        raise InstallRefused(f"Clips Kitty answered {status} to GET /plugins: {_detail(answer)}", 1)
    return version


def _is_session_file(path: Path) -> bool:
    try:
        return path.name == SESSION_FILE and path.parent.name == "plugins" and path.is_file()
    except OSError:
        return False


def path_from_403(detail) -> Path | None:
    """The session file a 403 answer names ("... a script finds it in
    <data folder>/plugins/session.secret."), when that is an existing file
    called session.secret in a folder called plugins; else None."""
    if not isinstance(detail, str):
        return None
    for match in re.finditer(r"\bin ", detail):
        rest = detail[match.end():]
        end = rest.rfind(SESSION_FILE)
        if end >= 0:
            path = Path(rest[:end + len(SESSION_FILE)])
            if _is_session_file(path):
                return path
    return None


def installed_app_file() -> Path | None:
    """Where the installed app keeps its session file:
    %LOCALAPPDATA%\\Clips Studio\\data\\plugins\\session.secret (the data
    folder kept its old name). None off Windows without LOCALAPPDATA."""
    base = os.environ.get("LOCALAPPDATA")
    if not base and sys.platform == "win32":
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "Clips Studio" / "data" / "plugins" / SESSION_FILE if base else None


def session_file(api: str, data_dir: str | os.PathLike | None = None) -> Path:
    """The session file to read the secret from: --data-dir's
    plugins/session.secret, else the one Clips Kitty's 403 answer names
    (asked with no header, for a plan that doesn't exist), else the
    installed app's."""
    if data_dir:
        given = Path(data_dir) / "plugins" / SESSION_FILE
        if given.is_file():
            return given
    status, answer = _call(api, "POST", "/plugins/install", _PROBE)
    if status == 403:
        named = path_from_403(_detail(answer))
        if named is not None:
            return named
    fallback = installed_app_file()
    if fallback is not None and fallback.is_file():
        return fallback
    raise InstallRefused(NO_SESSION_FILE)


def _refusal(status: int, answer, secret: Path, what: str) -> InstallRefused:
    if status == 403:
        return InstallRefused(SESSION_REFUSED.format(path=secret))
    detail = _detail(answer)
    if status in (400, 404, 409):
        return InstallRefused(detail or f"Clips Kitty refused the {what} ({status})", 1)
    return InstallRefused(f"Clips Kitty answered {status} to the {what}: {detail}", 1)


def plan(api: str, folder: Path, secret: Path) -> dict:
    """POST /plugins/plan for the folder: what installing it would do."""
    status, answer = _call(api, "POST", "/plugins/plan", {"source": {"kind": "folder", "path": str(folder)}},
                           secret, timeout=PLAN_TIMEOUT)
    if status == 200 and isinstance(answer, dict):
        return answer
    raise _refusal(status, answer, secret, "plan")


def install_plan(api: str, planned: dict, secret: Path) -> dict:
    """POST /plugins/install for a plan: the installed plugin, as Clips Kitty lists it."""
    status, answer = _call(api, "POST", "/plugins/install", {"plan_id": planned.get("plan_id")}, secret,
                           timeout=PLAN_TIMEOUT)
    if status == 200 and isinstance(answer, dict):
        return answer
    raise _refusal(status, answer, secret, "install")


# ---- what a plan says ---------------------------------------------------------------


def plan_text(planned: dict) -> str:
    """The plan's install screen as text: Clips Kitty's own (`text`), or for
    an app that doesn't send it, the name, version, permissions, data
    warnings, errors and warnings."""
    text = planned.get("text")
    if isinstance(text, str) and text:
        return text
    plugin = planned.get("plugin") if isinstance(planned.get("plugin"), dict) else {}
    details = planned.get("details") if isinstance(planned.get("details"), dict) else {}
    name = plugin.get("name") or plugin.get("id") or "this plugin"
    lines = [f"Install {name} {plugin.get('version') or ''}".rstrip() + "?"]
    labels = [p.get("label") or p.get("id") for p in details.get("permissions") or [] if isinstance(p, dict)]
    if labels:
        lines += ["It will"] + [f"  {label}" for label in labels]
    lines += [str(w) for w in details.get("data_warnings") or []]
    lines += [f"✗ {e}" for e in planned.get("errors") or []]
    lines += [f"! {w}" for w in planned.get("warnings") or []]
    return "\n".join(lines)


def what_is_new(planned: dict) -> list[str]:
    """What a plan adds that the person installing hasn't agreed to yet, in
    words; empty when nothing is. Clips Kitty's plan puts what an update
    changes straight into `update` (added_permissions, added_hosts,
    added_data_warnings, execution_changed, added_steps). A first install
    (no `update`) is new, and so is a key the plan leaves out."""
    update = planned.get("update") if isinstance(planned, dict) else None
    if not isinstance(update, dict):
        return [FIRST_INSTALL]
    new = []
    for key in ("added_permissions", "added_hosts", "added_data_warnings"):
        new += _listed(update, key)
    changed = update.get("execution_changed")
    if changed is True:
        new.append("a change to where it runs")
    elif changed is not False:
        new.append(UNSAID.format(what="where it runs"))
    new += _listed(update, "added_steps")
    return new


def _listed(update: dict, key: str) -> list[str]:
    value = update.get(key)
    if not isinstance(value, list):
        return [UNSAID.format(what=_NEW_LISTS[key])]
    return [f"{_NEW_LISTS[key]}: {', '.join(map(str, value))}"] if value else []


def nothing_new(planned: dict) -> bool:
    """Whether a plan adds nothing the person installing hasn't agreed to:
    the same plugin is installed, and the update adds no permission, network
    host, data sent off the PC, change to where it runs, or step. Only then
    may --yes and --watch install without asking."""
    return not what_is_new(planned)


# ---- the command --------------------------------------------------------------------


def _say(stream, text: str) -> None:
    """Print `text`, replacing what the stream's encoding can't show (a
    Windows console redirected to a file may not take ⚠ or ✓)."""
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        text = text.encode(encoding, errors="replace").decode(encoding)
    except LookupError:
        pass
    print(text, file=stream, flush=True)


def _input(question: str) -> str:
    return input(question)


def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """Each file's modification time and size, as --watch compares them,
    leaving out .git, __pycache__ and the development folders."""
    out = {}
    for here, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in WATCH_SKIPS]
        for name in names:
            path = Path(here, name)
            try:
                info = path.stat()
            except OSError:
                continue
            out[path.relative_to(folder).as_posix()] = (info.st_mtime_ns, info.st_size)
    return out


def _first_install(api: str, folder: Path, secret: Path, *, yes: bool, ask, out, err) -> int:
    planned = plan(api, folder, secret)
    _say(out, plan_text(planned))
    if not planned.get("ok"):
        raise InstallRefused(APP_REFUSED, 1)
    if yes:
        new = what_is_new(planned)
        if new:
            raise InstallRefused(YES_REFUSED.format(new="; ".join(new)), 1)
    else:
        try:
            answer = ask(QUESTION)
        except EOFError:
            answer = ""
        if str(answer).strip().lower() not in ("y", "yes"):
            _say(out, NOT_INSTALLED)
            return 1
    view = install_plan(api, planned, secret)
    _say(out, INSTALLED.format(id=view.get("id"), version=view.get("version")))
    return 0


def _reinstall(api: str, given: Path, secret: Path, *, out, err) -> bool:
    """One --watch round. False when watching should stop."""
    try:
        folder = _checked(given, err)
        planned = plan(api, folder, secret)
        if not planned.get("ok"):
            for problem in planned.get("errors") or []:
                _say(err, f"error: {problem}")
            raise InstallRefused(APP_REFUSED, 1)
        new = what_is_new(planned)
        if new:
            _say(err, WATCH_REFUSED.format(new="; ".join(new)))
            return False
        for warning in planned.get("warnings") or []:
            _say(err, f"warning: {warning}")
        view = install_plan(api, planned, secret)
    except InstallRefused as e:
        _say(err, f"error: {e}")
        _say(err, "Still watching: save again to try again.")
        return True
    _say(out, REINSTALLED.format(id=view.get("id"), version=view.get("version"), time=time.strftime("%H:%M:%S")))
    return True


def _watch(api: str, given: Path, folder: Path, secret: Path, *, out, err, sleep, interval: float) -> int:
    seen = snapshot(folder)
    while True:
        sleep(interval)
        now = snapshot(folder)
        if now == seen:
            continue
        while True:  # wait until the saving stops
            sleep(interval)
            later = snapshot(folder)
            if later == now:
                break
            now = later
        seen = now
        if not _reinstall(api, given, secret, out=out, err=err):
            return 1


def run(folder, *, api: str = DEFAULT_API, data_dir=None, yes: bool = False, watch: bool = False, ask=None,
        out=None, err=None, sleep=None, interval: float = 1.0) -> int:
    """`install`: check the folder, install it into the Clips Kitty on this
    PC, and with `watch`, reinstall it on each save until a save adds
    something new or Ctrl+C. Returns the exit code."""
    out = out or sys.stdout
    err = err or sys.stderr
    ask = ask or _input
    sleep = sleep or time.sleep
    given = Path(folder)
    watching = False
    try:
        resolved = _checked(given, err)
        if not _loopback.is_this_pc(api):
            raise InstallRefused(OFF_THIS_PC)
        can_install_plugins(api)
        secret = session_file(api, data_dir)
        code = _first_install(api, resolved, secret, yes=yes, ask=ask, out=out, err=err)
        if code or not watch:
            return code
        watching = True
        _say(out, f"Watching {resolved} for saves; Ctrl+C stops.")
        return _watch(api, given, resolved, secret, out=out, err=err, sleep=sleep, interval=interval)
    except InstallRefused as e:
        _say(err, f"error: {e}")
        return e.code
    except KeyboardInterrupt:
        _say(out, STOPPED if watching else NOT_INSTALLED)
        return 0 if watching else 1
