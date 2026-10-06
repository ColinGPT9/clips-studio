"""Installed plugins, as the plugin manager leaves them on disk.

    <data_dir>/plugins/
        installed.json                     what is installed, active and enabled
        <publisher>/<name>/<version>/      one folder per installed version
        envs/<publisher>/<name>/<version>/ a version's own Python packages, if any
        runs/                              the job folders of recent runs

installed.json:

    {"format": 1,
     "plugins": {"example-dev/example-pipeline": {
         "active": "1.0.0", "enabled": true, "pinned": false,
         "versions": {"1.0.0": {"folder": "example-dev/example-pipeline/1.0.0",
                                "source": {"kind": "git", "url": "...", "commit": "..."},
                                "tier": "listed", "installed_at": "...", "python": null}}}}}

This module only reads. Installing, updating and removing are the plugin
manager's (plugins/manager.py), which writes the file atomically.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

STATE_FILE = "installed.json"
MANIFEST = "clipskitty.yaml"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}/[a-z0-9][a-z0-9-]{0,63}$")
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


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


def read_manifest(folder: Path) -> dict:
    import yaml

    data = yaml.safe_load((Path(folder) / MANIFEST).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{MANIFEST} is not a mapping")
    return data


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
    return plugin
