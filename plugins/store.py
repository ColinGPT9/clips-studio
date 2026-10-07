"""Installed plugins, as the plugin manager leaves them on disk.

    <data_dir>/plugins/
        installed.json                               what is installed, active and enabled
        installed/<publisher>/<name>/<version>/      one folder per installed version
        envs/<publisher>/<name>/<version>/           a version's own Python packages (planned)
        staging/                                     files fetched for an install plan, until installed
        runs/                                        the job folders of recent runs
        cache/                                       the last good copy of each registry index
        session.secret                               the plugin-manager routes' session secret

installed.json:

    {"format": 1,
     "plugins": {"example-dev/example-pipeline": {
         "active": "1.0.0", "previous": "0.9.0", "enabled": true, "pinned": false,
         "versions": {"1.0.0": {"folder": "installed/example-dev/example-pipeline/1.0.0",
                                "source": {"kind": "git", "url": "...", "commit": "..."},
                                "tier": "listed", "installed_at": "...", "python": null},
                      "0.9.0": {...}}}}}

`previous` is the version Roll back returns to; only the active and previous
versions are kept.

This module only reads. Installing, updating and removing are the plugin
manager's (plugins/manager.py), which writes the file atomically.

`requires.clips_kitty` is checked here as well as at install, so a plugin an
app update leaves behind is refused with a reason instead of failing oddly;
so is the registry's block list (plugins/registry.py), so a blocked version
is refused when a job names it and when it runs.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from pathlib import Path

from plugins._sdk import host, manifest

STATE_FILE = "installed.json"
MANIFEST = manifest.MANIFEST_FILE
ID_RE = manifest.ID_RE
VERSION_RE = manifest.VERSION_RE


def root(data_dir) -> Path:
    return Path(data_dir) / "plugins"


def load(data_dir) -> dict:
    """installed.json, or an empty store when there is none (or it is unreadable)."""
    try:
        data = json.loads((root(data_dir) / STATE_FILE).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("plugins"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"format": 1, "plugins": {}}


@functools.lru_cache(maxsize=1)
def app_version() -> str:
    """This Clips Kitty's version (ui/package.json, as GET /health reports it), or "?"."""
    from server.feedback import _app_version

    return str(_app_version().get("app") or "?")


def compatibility_problem(manifest_data: dict, version: str | None = None) -> str | None:
    """Why a plugin can't run on this Clips Kitty, or None. An unknown app
    version is not held against the plugin."""
    spec = (manifest_data.get("requires") or {}).get("clips_kitty")
    version = version or app_version()
    if not isinstance(spec, str) or not VERSION_RE.match(version):
        return None
    try:
        ok = manifest.version_satisfies(version, spec)
    except ValueError:
        return f"its Clips Kitty version range {spec!r} can't be read"
    return None if ok else f"it needs Clips Kitty {spec}, and this is {version}"


def read_manifest(folder: Path) -> dict:
    """An installed version's manifest (validated when it was installed)."""
    return manifest.load(folder)


@dataclass
class Installed:
    id: str
    version: str
    folder: Path
    manifest: dict
    enabled: bool = True
    tier: str = "link"
    source: dict = field(default_factory=dict)
    python: str | None = None  # the version's own interpreter, when it has packages

    @property
    def name(self) -> str:
        return str(self.manifest.get("name") or self.id)


def get(data_dir, plugin_id: str, version: str | None = None) -> Installed | None:
    """One installed plugin: the version asked for, else the active one."""
    entry = load(data_dir)["plugins"].get(plugin_id)
    if not entry:
        return None
    version = version or entry.get("active")
    info = (entry.get("versions") or {}).get(version)
    if not info:
        return None
    base = root(data_dir)
    folder = base / info["folder"]
    try:
        manifest = read_manifest(folder)
    except (OSError, ValueError, ImportError):
        return None
    python = info.get("python")
    return Installed(
        id=plugin_id, version=version, folder=folder, manifest=manifest,
        enabled=bool(entry.get("enabled", True)), tier=info.get("tier", "link"),
        source=dict(info.get("source") or {}), python=str(base / python) if python else None,
    )


def clean_choice(value) -> dict:
    """A job's `pipeline` option, checked for shape: {id, version?, settings?}.

    A plain string is taken as the id. Whether the plugin is installed is
    checked separately, against a data folder (see installed_choice).
    """
    if isinstance(value, str):
        value = {"id": value}
    if not isinstance(value, dict):
        raise ValueError("pipeline must be an object: {\"id\": \"publisher/name\"}")
    unknown = set(value) - {"id", "version", "settings"}
    if unknown:
        raise ValueError(f"pipeline has unknown fields: {', '.join(sorted(unknown))}")
    pid = value.get("id")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        raise ValueError("pipeline.id must look like publisher/name (lower case, digits and hyphens)")
    out = {"id": pid}
    version = value.get("version")
    if version is not None:
        if not isinstance(version, str) or not VERSION_RE.match(version):
            raise ValueError("pipeline.version must be a version like 1.2.0")
        out["version"] = version
    settings = value.get("settings")
    if settings is not None:
        if not isinstance(settings, dict):
            raise ValueError("pipeline.settings must be an object")
        if len(json.dumps(settings)) > 16_000:
            raise ValueError("pipeline.settings is too large")
        out["settings"] = settings
    return out


def installed_choice(data_dir, choice: dict) -> Installed:
    """The installed, enabled plugin a cleaned choice names, or ValueError saying why not."""
    plugin = get(data_dir, choice["id"], choice.get("version"))
    if plugin is None:
        wanted = choice["id"] + (f" {choice['version']}" if choice.get("version") else "")
        raise ValueError(f"the pipeline {wanted} isn't installed")
    if not plugin.enabled:
        raise ValueError(f"the pipeline {plugin.name} is turned off; turn it on in Plugins first")
    problem = compatibility_problem(plugin.manifest)
    if problem:
        raise ValueError(f"the pipeline {plugin.name} can't run here: {problem}")
    from plugins import registry

    hit = registry.blocked_check(data_dir)(plugin.id, plugin.version)
    if hit and hit.get("severity") == "blocked":
        raise ValueError(f"the pipeline {plugin.name} {plugin.version} is blocked: {hit.get('reason')}. Remove it in Plugins.")
    host.job_settings(plugin.manifest, choice.get("settings"))  # refuses unknown or ill-typed settings now
    return plugin
