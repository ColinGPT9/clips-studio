"""Installing, updating, turning on and off, rolling back and removing plugins.

    plan()      fetch a plugin's files into a staging folder and say what
                installing it would do; installs nothing
    install()   install what a plan staged
    set_enabled(), set_pinned(), rollback(), remove(), set_secrets()
    listing()   what is installed, for GET /plugins

Nothing from a plugin runs here: no install script, no package build, no hook.
Files are fetched (plugins/sources.py), the manifest is validated, and the
folder is moved into place. A new version installs beside the one in use and
becomes active only once it validates; the version it replaced is kept for
Roll back, and only those two are kept. Versions live in separate folders and
the active one is a pointer in installed.json, so nothing is overwritten in
place (a file in use on Windows can't be). installed.json is written whole,
then swapped in.

`blocked(id, version)` is how the registry's block list reaches the manager:
it returns {"severity": "blocked" | "delisted", "reason": ...} or None.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from plugins import permissions, sources, store
from plugins._sdk import manifest

INSTALLED = "installed"
STAGING = "staging"
STAGING_MAX_AGE = 3600
MAX_SECRET_LENGTH = 4096
PLAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"

_lock = threading.RLock()


class ManagerError(ValueError):
    """A request the manager refuses. The message is for the user; `status`
    is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _save(data_dir, state: dict) -> None:
    root = store.root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / store.STATE_FILE
    tmp = target.with_name(f"{store.STATE_FILE}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _remove_tree(path: Path, data_dir) -> bool:
    """Delete a folder of the manager's own (under installed/ or staging/), and
    nothing else. True when it is gone; a file in use is left for later."""
    root = store.root(data_dir)
    if not (_inside(path, root / INSTALLED) or _inside(path, root / STAGING)) or not path.exists():
        return not path.exists()
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()


def _prune(data_dir, state: dict) -> None:
    """Delete version folders installed.json no longer points at: ones a
    file lock left behind earlier."""
    base = store.root(data_dir) / INSTALLED
    if not base.is_dir():
        return
    wanted = set()
    for entry in state["plugins"].values():
        for info in (entry.get("versions") or {}).values():
            folder = store.root(data_dir) / str(info.get("folder", ""))
            if _inside(folder, base):
                wanted.add(folder.resolve())
    for publisher in base.iterdir():
        for name in publisher.iterdir() if publisher.is_dir() else ():
            for version in name.iterdir() if name.is_dir() else ():
                if version.resolve() not in wanted:
                    _remove_tree(version, data_dir)
            if name.is_dir() and not any(name.iterdir()):
                name.rmdir()
        if publisher.is_dir() and not any(publisher.iterdir()):
            publisher.rmdir()


def _clean_staging(data_dir) -> None:
    staging = store.root(data_dir) / STAGING
    if not staging.is_dir():
        return
    for stage in staging.iterdir():
        try:
            old = time.time() - stage.stat().st_mtime > STAGING_MAX_AGE
        except OSError:
            continue
        if old:
            _remove_tree(stage, data_dir)


def _entry(data_dir, plugin_id: str) -> tuple[dict, dict]:
    if not isinstance(plugin_id, str) or not store.ID_RE.match(plugin_id):
        raise ManagerError(f"{plugin_id!r} is not a plugin id", 404)
    state = store.load(data_dir)
    entry = state["plugins"].get(plugin_id)
    if not entry:
        raise ManagerError(f"{plugin_id} isn't installed", 404)
    return state, entry


def _summary(data: dict | None) -> dict:
    data = data or {}
    keys = ("id", "name", "version", "kind", "description", "license", "repository", "category", "tags",
            "games", "events", "execution", "author", "links", "requirements", "based_on")
    return {k: data.get(k) for k in keys if data.get(k) is not None}


def _version_order(a: str, b: str) -> int:
    ta, tb = manifest._version_tuple(a), manifest._version_tuple(b)
    return (ta > tb) - (ta < tb)


def _block_problems(blocked, plugin_id: str, version: str) -> tuple[list, list]:
    hit = blocked(plugin_id, version) if blocked and plugin_id and version else None
    if not hit:
        return [], []
    if hit.get("severity") == "blocked":
        return [f"Blocked: {hit.get('reason') or 'on the registry block list'}"], []
    return [], [f"No longer listed: {hit.get('reason') or 'removed from the registry'}"]


# ---- plan and install -------------------------------------------------------------------


def plan(data_dir, source, *, app_version: str | None = None, tier: str = "link", git: str | None = None,
         fetcher=None, blocked=None, expect: dict | None = None, listed_in: str | None = None) -> dict:
    """Fetch a source into a staging folder and say what installing it would do.

    The answer has everything the install screen shows (`details`, from
    plugins/permissions.py), the manifest's errors and warnings, and, when the
    plugin is already installed, what the change would be (`update`). With no
    errors it carries a `plan_id` for install(). `expect` ({id, version}) is
    what a registry listing says the files are; a mismatch is an error.
    `listed_in` names the index a listed plugin came from (recorded with it).
    """
    try:
        source = sources.clean_source(source)
    except sources.SourceError as e:
        raise ManagerError(str(e)) from e
    if listed_in:
        source["listed_in"] = listed_in
    _clean_staging(data_dir)
    plan_id = secrets.token_hex(8)
    stage = store.root(data_dir) / STAGING / plan_id
    stage.mkdir(parents=True)
    try:
        warnings = sources.fetch(source, stage / "files", git=git, fetcher=fetcher)
    except sources.SourceError as e:
        _remove_tree(stage, data_dir)
        raise ManagerError(f"Couldn't get the plugin's files: {e}") from e

    data, report = manifest.validate_folder(stage / "files")
    errors, warnings = list(report.errors), warnings + list(report.warnings)
    pid, version = (data or {}).get("id"), (data or {}).get("version")
    if data and report.ok:
        problem = store.compatibility_problem(data, app_version)
        if problem:
            errors.append(f"Can't install: {problem}")
        if expect:
            for key in ("id", "version"):
                if expect.get(key) and expect[key] != data.get(key):
                    errors.append(f"the listing says {key} {expect[key]}, the files say {data.get(key)}")
        more_errors, more_warnings = _block_problems(blocked, pid, version)
        errors += more_errors
        warnings += more_warnings

    update = None
    current = store.get(data_dir, pid) if data and report.ok else None
    if current:
        order = _version_order(version, current.version)
        update = {"from": current.version,
                  "direction": "update" if order > 0 else "downgrade" if order < 0 else "reinstall",
                  **permissions.changes(current.manifest, data)}

    ok = not errors
    out = {
        "plan_id": plan_id if ok else None,
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "plugin": _summary(data),
        "source": source,
        "source_text": sources.describe(source),
        "details": permissions.describe(data or {}, tier=tier),
        "update": update,
    }
    if ok:
        (stage / "plan.json").write_text(json.dumps({"plan_id": plan_id, "id": pid, "version": version,
                                                    "source": source, "tier": tier, "created": _now()}),
                                         encoding="utf-8")
    else:
        _remove_tree(stage, data_dir)
    return out


def _free_folder(data_dir, plugin_id: str, version: str) -> str:
    base = Path(INSTALLED) / plugin_id / version
    name, n = base, 1
    while (store.root(data_dir) / name).exists():
        n += 1
        name = base.with_name(f"{version}~{n}")
    return name.as_posix()


def install(data_dir, plan_id: str, *, app_version: str | None = None, blocked=None) -> dict:
    """Install what plan() staged. Returns the plugin as listing() shows it."""
    if not isinstance(plan_id, str) or not PLAN_ID_RE.match(plan_id):
        raise ManagerError("that is not an install plan", 404)
    stage = store.root(data_dir) / STAGING / plan_id
    try:
        meta = json.loads((stage / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ManagerError("This install plan has expired or was already used. Look at the plugin again.", 404) from e
    files = stage / "files"
    # Checked again: the staged files are what gets installed, not what was described.
    data, report = manifest.validate_folder(files)
    if not report.ok or not data or (data.get("id"), data.get("version")) != (meta["id"], meta["version"]):
        _remove_tree(stage, data_dir)
        raise ManagerError("The plugin's files changed after they were checked. Look at the plugin again.", 409)
    problem = store.compatibility_problem(data, app_version)
    errors, _ = _block_problems(blocked, meta["id"], meta["version"])
    if problem or errors:
        _remove_tree(stage, data_dir)
        raise ManagerError(f"Can't install: {problem}" if problem else errors[0], 409)

    plugin_id, version = meta["id"], meta["version"]
    with _lock:
        state = store.load(data_dir)
        entry = state["plugins"].get(plugin_id) or {"enabled": True, "pinned": False, "versions": {}}
        versions = entry.setdefault("versions", {})
        folder = _free_folder(data_dir, plugin_id, version)
        target = store.root(data_dir) / folder
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(files, target)
        old_active = entry.get("active")
        versions[version] = {"folder": folder, "source": meta["source"], "tier": meta["tier"],
                             "installed_at": _now(), "python": None}
        if old_active and old_active != version:
            entry["previous"] = old_active
        entry["active"] = version
        for v in [v for v in versions if v not in (version, entry.get("previous"))]:
            del versions[v]
        if entry.get("previous") not in versions:
            entry.pop("previous", None)
        state["plugins"][plugin_id] = entry
        _save(data_dir, state)
        _prune(data_dir, state)
    _remove_tree(stage, data_dir)
    return view(data_dir, plugin_id, blocked=blocked)


# ---- changing an installed plugin --------------------------------------------------


def set_enabled(data_dir, plugin_id: str, enabled: bool, *, blocked=None) -> dict:
    with _lock:
        state, entry = _entry(data_dir, plugin_id)
        entry["enabled"] = bool(enabled)
        _save(data_dir, state)
    return view(data_dir, plugin_id, blocked=blocked)


def set_pinned(data_dir, plugin_id: str, pinned: bool, *, blocked=None) -> dict:
    """Pin: the Marketplace stops offering this plugin's updates. Installing
    another version by hand still works, and stays pinned."""
    with _lock:
        state, entry = _entry(data_dir, plugin_id)
        entry["pinned"] = bool(pinned)
        _save(data_dir, state)
    return view(data_dir, plugin_id, blocked=blocked)


def rollback(data_dir, plugin_id: str, *, app_version: str | None = None, blocked=None) -> dict:
    """Make the previous version active again (and the current one previous)."""
    with _lock:
        state, entry = _entry(data_dir, plugin_id)
        previous = entry.get("previous")
        if not previous or previous not in (entry.get("versions") or {}):
            raise ManagerError(f"{plugin_id} has no earlier version to go back to", 409)
        older = store.get(data_dir, plugin_id, previous)
        if older is None:
            raise ManagerError(f"{plugin_id} {previous}'s files are missing or can't be read", 409)
        problem = store.compatibility_problem(older.manifest, app_version)
        errors, _ = _block_problems(blocked, plugin_id, previous)
        if problem or errors:
            raise ManagerError(f"Can't go back to {previous}: {problem}" if problem else errors[0], 409)
        entry["previous"], entry["active"] = entry["active"], previous
        _save(data_dir, state)
    return view(data_dir, plugin_id, blocked=blocked)


def remove(data_dir, plugin_id: str) -> dict:
    """Remove a plugin: every version Clips Kitty installed, and its stored
    keys. A folder installed in place (a developer's own) is forgotten, never
    deleted."""
    from core import secrets as secret_store
    from plugins.runner import secret_name

    with _lock:
        state, entry = _entry(data_dir, plugin_id)
        del state["plugins"][plugin_id]
        _save(data_dir, state)
        folder = store.root(data_dir) / INSTALLED / plugin_id
        gone = _remove_tree(folder, data_dir)
        publisher = folder.parent
        if publisher.is_dir() and not any(publisher.iterdir()):
            publisher.rmdir()
    keys_gone = secret_store.wipe(Path(data_dir), secret_name(plugin_id))
    return {"removed": plugin_id, "files_left": not gone, "keys_removed": keys_gone}


def set_secrets(data_dir, plugin_id: str, values: dict) -> list[str]:
    """Store a plugin's `secret` settings (an empty value deletes one).
    Returns the names now stored, never the values."""
    from core import secrets as secret_store
    from plugins.runner import secret_name

    plugin = store.get(data_dir, plugin_id) if store.ID_RE.match(str(plugin_id)) else None
    if plugin is None:
        raise ManagerError(f"{plugin_id} isn't installed", 404)
    declared = {n for n, s in (plugin.manifest.get("settings") or {}).items()
                if isinstance(s, dict) and s.get("type") == "secret"}
    if not isinstance(values, dict) or not values:
        raise ManagerError("values must name at least one secret setting")
    for name, value in values.items():
        if name not in declared:
            raise ManagerError(f"{name!r} is not one of {plugin.name}'s secret settings"
                               + (f" ({', '.join(sorted(declared))})" if declared else ": it has none"))
        if value is not None and (not isinstance(value, str) or len(value) > MAX_SECRET_LENGTH):
            raise ManagerError(f"{name}: a secret is text of at most {MAX_SECRET_LENGTH} characters")
    with _lock:
        current = secret_store.load(Path(data_dir), secret_name(plugin_id)) or {}
        for name, value in values.items():
            if value:
                current[name] = value
            else:
                current.pop(name, None)
        if current:
            secret_store.save(Path(data_dir), secret_name(plugin_id), current)
        else:
            secret_store.wipe(Path(data_dir), secret_name(plugin_id))
    return sorted(current)


# ---- what is installed ---------------------------------------------------------------


def view(data_dir, plugin_id: str, *, app_version: str | None = None, blocked=None) -> dict:
    """One installed plugin as GET /plugins shows it."""
    from core import secrets as secret_store
    from plugins.runner import secret_name

    entry = store.load(data_dir)["plugins"].get(plugin_id) or {}
    active = entry.get("active")
    info = (entry.get("versions") or {}).get(active) or {}
    plugin = store.get(data_dir, plugin_id)
    data = plugin.manifest if plugin else {}
    tier = info.get("tier", "link")
    problem = None
    if plugin is None:
        problem = "its files are missing or its manifest can't be read; remove it and install it again"
    else:
        problem = store.compatibility_problem(data, app_version)
    hit = blocked(plugin_id, active) if blocked and active else None
    source = dict(info.get("source") or {})
    stored = secret_store.load(Path(data_dir), secret_name(plugin_id)) or {}
    return {
        **_summary(data),
        "id": plugin_id,
        "name": data.get("name") or plugin_id,
        "version": active,
        "enabled": bool(entry.get("enabled", True)),
        "pinned": bool(entry.get("pinned", False)),
        "previous": entry.get("previous"),
        "versions": sorted(entry.get("versions") or {}, key=manifest._version_tuple, reverse=True),
        "installed_at": info.get("installed_at"),
        "source": source,
        "source_text": sources.describe(source) if source else "",
        "builtin": False,
        "details": permissions.describe(data, tier=tier),
        "settings": data.get("settings") or {},
        "secrets_set": sorted(k for k in stored if stored.get(k)),
        "problem": problem,
        "flag": ({"severity": hit.get("severity"), "reason": hit.get("reason")} if hit else None),
    }


def builtin_plugins() -> list[dict]:
    """The modes that ship with Clips Kitty, described by their manifests (Official)."""
    import yaml

    out = []
    for path in sorted(BUILTIN_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            out.append({**_summary(data), "builtin": True, "enabled": True,
                        "details": permissions.describe(data, tier="official")})
    return out


def listing(data_dir, *, app_version: str | None = None, blocked=None) -> dict:
    state = store.load(data_dir)
    return {
        "plugins": [view(data_dir, pid, app_version=app_version, blocked=blocked) for pid in sorted(state["plugins"])],
        "builtin": builtin_plugins(),
    }
