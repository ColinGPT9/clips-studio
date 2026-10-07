"""Shared fixtures.

The repo root goes on sys.path so tests import the engine the same way the
app does (`from creator import retrieval`), with no packaging step.
"""

import sys
from pathlib import Path
from typing import ClassVar

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.state import StateDB, _now  # noqa: E402


@pytest.fixture
def db(tmp_path):
    """A real, empty state database in a temp directory.

    Deliberately the real StateDB rather than a mock: the schema and its
    migrations are part of what these tests are checking, and a mock that
    drifts from the real schema tests nothing.
    """
    database = StateDB(tmp_path / "state.db")
    yield database
    database.conn.close()


@pytest.fixture
def creator(db):
    """One creator row, returning its id."""
    cur = db.conn.execute(
        "INSERT INTO creators (display_name, aliases, learning_enabled, created_at)"
        " VALUES (?, '[]', 1, ?)",
        ("Test Creator", _now()),
    )
    db.conn.commit()
    return cur.lastrowid


@pytest.fixture
def sample_transcript():
    """The committed sample transcript, parsed into Segments.

    Longer and messier than the `segments` fixture below — two minutes of
    plausible stream, with both a genuinely repeated phrase and a vivid
    one-off that must not be mistaken for one. See tests/assets/README.md.
    """
    import json

    from core.models import Segment

    raw = json.loads(
        (ROOT / "tests" / "assets" / "sample_transcript.json").read_text(encoding="utf-8")
    )
    return [Segment(**seg) for seg in raw["segments"]]


@pytest.fixture
def sample_video():
    """Path to the generated test video, skipping the test if it is absent.

    Not committed — it is built on demand by
    `python tests/assets/make_sample_video.py`. Skipping rather than failing
    is deliberate: a contributor who has not generated it has not broken
    anything, and CI has no FFmpeg to generate it with.
    """
    path = ROOT / "tests" / "assets" / "sample_video.mp4"
    if not path.exists():
        pytest.skip("No sample video. Run: python tests/assets/make_sample_video.py")
    return path


@pytest.fixture
def segments():
    """A short transcript as the pipeline would hand it over."""
    from core.models import Segment

    return [
        Segment(start=0.0, end=2.0, text="yo what is good chat"),
        Segment(start=2.0, end=4.5, text="let's get it, we grinding today"),
        Segment(start=4.5, end=7.0, text="man I only said this once, believe me"),
        Segment(start=7.0, end=9.0, text="alright let's get it, run it back"),
    ]


@pytest.fixture
def install_plugin():
    """Record a plugin folder as installed in a data folder, in place.

    What the plugin manager does after copying a plugin, minus the copy: the
    store points at the folder as it is, which is also how a developer's own
    checkout is run. Returns the plugin's id and version.
    """
    import json

    def install(data_dir, folder, *, enabled=True, tier="link"):
        from plugins import store

        manifest = store.read_manifest(Path(folder))
        root = store.root(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        state = store.load(data_dir)
        state["plugins"][manifest["id"]] = {
            "active": manifest["version"], "enabled": enabled, "pinned": False,
            "versions": {manifest["version"]: {"folder": str(Path(folder).resolve()), "tier": tier,
                                               "source": {"kind": "folder", "path": str(folder)}}},
        }
        (root / store.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
        return manifest["id"], manifest["version"]

    return install



class PluginSource:
    """Plugin folders and Git repositories of them, made in a temporary
    folder, for the plugin manager's tests. Nothing touches the network: a
    repository is reached as file://."""

    BASE: ClassVar[dict] = {
        "manifest_version": 1, "id": "fixture-dev/manager-test", "name": "Manager test", "version": "1.0.0",
        "kind": "pipeline", "capability": "highlight_detection", "description": "A plugin for the manager's tests.",
        "license": "MIT", "requires": {"clips_kitty": ">=2.0", "plugin_api": 1},
        "run": {"command": ["{python}", "src/main.py"]}, "execution": "local", "inputs": ["video"],
        "outputs": ["ranges"], "permissions": ["video.read"], "category": "utilities",
    }

    def __init__(self, base: Path):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)

    def manifest(self, **changes) -> dict:
        import copy

        data = copy.deepcopy(self.BASE)
        data.update(changes)
        return data

    def write(self, folder: Path, manifest: dict | None = None, files: dict | None = None) -> Path:
        """Write a manifest (as JSON, which is YAML too) and files into a folder."""
        import json

        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        if manifest is not None:
            (folder / "clipskitty.yaml").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        for rel, content in (files or {}).items():
            path = folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        return folder

    def folder(self, name: str = "plugin", manifest: dict | None = None, files: dict | None = None) -> Path:
        files = {"src/main.py": "print('hello')\n", **(files or {})}
        return self.write(self.base / name, manifest or self.manifest(), files)

    def git(self, repo: Path, *args: str, input: bytes | None = None) -> str:
        import subprocess

        done = subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                               "-c", "commit.gpgsign=false", *args], input=input, capture_output=True, check=True)
        return done.stdout.decode().strip()

    def repo(self, name: str = "repo") -> Path:
        import shutil
        import subprocess

        if not shutil.which("git"):
            pytest.skip("git is not installed")
        repo = self.base / name
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        return repo

    def commit(self, repo: Path, manifest: dict | None = None, files: dict | None = None,
               message: str = "change") -> str:
        files = {"src/main.py": "print('hello')\n", **(files or {})}
        self.write(repo, manifest or self.manifest(), files)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")


@pytest.fixture
def plugin_source(tmp_path):
    return PluginSource(tmp_path / "sources")
