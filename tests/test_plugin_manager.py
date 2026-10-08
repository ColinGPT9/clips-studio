"""The plugin manager: install from a folder or a Git commit, update beside the
old version, roll back, turn off, pin, remove, and refuse what it should.

Every Git source is a repository made in a temporary folder and reached as
file://; nothing touches the network. GitHub's archive (the path for a PC
without Git) is a tarball made here and handed over by a fake fetcher.
"""

import ast
import errno
import http.client
import io
import json
import os
import re
import stat
import tarfile
import urllib.error
from pathlib import Path

import pytest

pytest.importorskip("yaml")

from plugins import manager, permissions, sources, store

APP = "2.0.0"
COMMIT_X = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data"


def _git_source(repo: Path, commit: str) -> dict:
    return {"kind": "git", "url": repo.resolve().as_uri(), "commit": commit}


def _plan(data, source, **kw):
    kw.setdefault("app_version", APP)
    return manager.plan(data, source, **kw)


def _install(data, source, **kw):
    plan = _plan(data, source, **kw)
    assert plan["ok"], plan["errors"]
    return manager.install(data, plan["plan_id"], app_version=APP, blocked=kw.get("blocked"))


# ---- nothing runs at install --------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="the trap hooks and filter are shell scripts")
def test_nothing_from_the_plugin_runs_when_it_is_planned_or_installed(data, plugin_source, tmp_path, monkeypatch):
    marker = tmp_path / "markers"
    marker.mkdir()
    # Traps a careless installer would spring: a setup.py, the plugin's own
    # code, and Git hooks and a smudge filter from the user's Git settings.
    hooks = tmp_path / "user-hooks"
    hooks.mkdir()
    for hook in ("post-checkout", "post-merge", "reference-transaction", "post-index-change"):
        (hooks / hook).write_text(f"#!/bin/sh\ntouch {marker}/hook-{hook}\n")
        (hooks / hook).chmod(0o755)
    smudge = tmp_path / "trap-filter"
    smudge.write_text(f"#!/bin/sh\ntouch {marker}/filter\ncat\n")
    smudge.chmod(0o755)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f"[core]\n\thooksPath = {hooks}\n[filter \"trap\"]\n\tsmudge = {smudge}\n\trequired = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    files = {
        "setup.py": f"open({str(marker / 'setup')!r}, 'w').close()\n",
        "src/main.py": f"open({str(marker / 'main')!r}, 'w').close()\n",
        ".gitattributes": "* filter=trap\n",
    }
    # Made and committed with the traps off, so only the installer could
    # spring them (newer Git runs the reference-transaction hook at `git init`).
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    repo = plugin_source.repo()
    commit = plugin_source.commit(repo, files=files)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))

    plan = _plan(data, _git_source(repo, commit))
    assert plan["ok"], plan["errors"]
    assert not (store.root(data) / store.STATE_FILE).exists()  # a plan installs nothing
    manager.install(data, plan["plan_id"], app_version=APP)
    assert list(marker.iterdir()) == []
    installed = store.get(data, "fixture-dev/manager-test")
    assert (installed.folder / "setup.py").read_text() == files["setup.py"]
    # The traps are real: an ordinary clone springs the hook and the filter.
    import subprocess

    subprocess.run(["git", "clone", "-q", str(repo), str(tmp_path / "clone")], check=True, capture_output=True)
    assert {"hook-post-checkout", "filter"} <= {p.name for p in marker.iterdir()}


# ---- plan --------------------------------------------------------------------------------


def test_a_plan_shows_permissions_and_what_leaves_the_pc_before_anything_installs(data, plugin_source):
    manifest = plugin_source.manifest(
        execution="hybrid", permissions=["video.read", "network", "ffmpeg"], network=["api.example.com"],
        sends=[{"data": "video", "to": "Example Cloud", "when": "you choose Cloud"}, "transcript"],
        settings={"api_key": {"type": "secret", "title": "Key"}},
        requirements={"gpu": "recommended", "vram_gb": 6, "disk_gb": 2})
    folder = plugin_source.folder(manifest=manifest)
    plan = _plan(data, {"kind": "folder", "path": str(folder)})
    assert plan["ok"] and plan["plan_id"] and plan["update"] is None
    d = plan["details"]
    assert d["notice"] == "This pipeline is a program from the internet. It can do anything you can do on this PC."
    assert d["tier_text"] == "Not listed · Clips Kitty has not checked this"
    assert {p["id"]: (p["label"], p["enforcement"]) for p in d["permissions"]} == {
        "video.read": ("Reads the video you process", "Clips Kitty hands this over"),
        "network": ("Connects to: api.example.com", "the developer says so"),
        "ffmpeg": ("Uses Clips Kitty's FFmpeg", "Clips Kitty hands this over"),
    }
    assert d["data_warnings"] == ["⚠ Sends your video to Example Cloud, when you choose Cloud",
                                  "⚠ Sends your video's transcript off this computer"]
    assert d["secrets"] == ["api_key"] and "Other pipelines and programs running as you can read them" in d["secrets_notice"]
    assert d["requirements"][0] == "A graphics card is recommended, 6 GB of video memory"
    text = permissions.render_text(plan)
    assert "⚠ Sends your video to Example Cloud, when you choose Cloud" in text
    assert "Reads the video you process (Clips Kitty hands this over)" in text
    assert "Connects to: api.example.com (the developer says so)" in text
    assert permissions.ENFORCEMENT_NOTE in text
    assert not (store.root(data) / store.STATE_FILE).exists()


# Words a creator shouldn't have to know (docs/developers keeps them for developers).
JARGON = re.compile(r"\b(plugins?|manifests?|commits?|repository|repositories|forks?|registry|checksums?|pickle|"
                    r"git)\b|settings\.yaml|exit code", re.IGNORECASE)


def _shown_text(module) -> list[str]:
    """The text in a module a person can be shown: every string and f-string
    part, leaving out docstrings and lower-case identifiers such as dict keys."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body
                  and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
            and not re.fullmatch(r"[a-z0-9_.{}-]*", node.value)]


def test_the_install_screen_text_is_in_plain_words():
    shown = _shown_text(permissions)
    assert permissions.NOTICE in shown and permissions.ENFORCEMENT_NOTE in shown  # the scan sees them
    assert [text for text in shown if JARGON.search(text) or re.search(r"\bPATH\b", text)] == []
    assert not JARGON.search("Comments and ideas (opens GitHub's website)")  # word boundaries: GitHub is fine


@pytest.mark.parametrize("model, pickle, validator_says", [
    ({"source": "huggingface", "id": "example-org/example-model", "revision": "a" * 40,
      "files": ["config.json", "weights/model.PT"]}, True, True),
    ({"source": "url", "id": "https://example.com/dl/model.ckpt?download=1", "sha256": "c" * 64}, True, True),
    # The download names the file without its #fragment, and asks; so does the validator.
    ({"source": "url", "id": "https://example.com/dl/model.pt#v1", "sha256": "c" * 64}, True, True),
    # A long name is shortened when it's saved, but keeps its extension, so the download still asks.
    ({"source": "url", "id": "https://example.com/dl/" + "m" * 130 + ".pt", "sha256": "c" * 64}, True, True),
    ({"source": "huggingface", "id": "example-org/example-model", "revision": "a" * 40,
      "files": ["model.safetensors"]}, False, False),
    ({"source": "url", "id": "https://example.com/dl/model.onnx", "sha256": "c" * 64}, False, False),
])
def test_a_model_file_that_can_run_code_is_said_plainly_before_install(data, plugin_source, model, pickle,
                                                                       validator_says):
    """The validator's warning is in Technical details, which is folded; the
    person pressing Install sees a plain line too, whenever the download
    will ask about the file (models.download decides that)."""
    from plugins import models

    manifest = plugin_source.manifest(models=[{"name": "detector", **model}])
    plan = _plan(data, {"kind": "folder", "path": str(plugin_source.folder(manifest=manifest))})
    assert plan["ok"], plan["errors"]
    assert (manager.PICKLE_MODEL in plan["warnings"]) is pickle
    assert any("pickle-format" in line for line in plan["technical"]) is validator_says
    assert any(models.is_pickle(f) for f in models.parse({"name": "detector", **model})["files"]) is pickle
    assert not JARGON.search(manager.PICKLE_MODEL)


def test_an_invalid_plugin_is_refused_with_every_reason_and_nothing_is_kept(data, plugin_source):
    manifest = plugin_source.manifest(version="one", permissions=["video.read", "webcam"])
    del manifest["license"]
    plan = _plan(data, {"kind": "folder", "path": str(plugin_source.folder(manifest=manifest))})
    assert not plan["ok"] and plan["plan_id"] is None
    assert any(e.startswith("license:") for e in plan["errors"])
    assert any(e.startswith("version:") for e in plan["errors"])
    assert any("webcam" in e for e in plan["errors"])
    assert list((store.root(data) / manager.STAGING).iterdir()) == []


def test_a_plugin_for_another_clips_kitty_is_refused(data, plugin_source):
    manifest = plugin_source.manifest(requires={"clips_kitty": ">=3.0", "plugin_api": 1})
    plan = _plan(data, {"kind": "folder", "path": str(plugin_source.folder(manifest=manifest))})
    assert plan["errors"] == ["Can't install: it needs Clips Kitty >=3.0, and this is 2.0.0"]


def test_a_source_must_be_well_formed(data):
    for source, fragment in (
        ({"kind": "git", "url": "https://example.com/a/b", "commit": "main"}, "40-character commit"),
        ({"kind": "git", "url": "http://example.com/a/b", "commit": COMMIT_X}, "https://"),
        ({"kind": "git", "url": "ext::sh -c touch% /tmp/x", "commit": COMMIT_X}, "https://"),
        ({"kind": "git", "url": "https://example.com/a/b", "commit": COMMIT_X, "path": 3}, "names its folder"),
        ({"kind": "folder"}, "wasn't told which folder it's in"),
        ({"kind": "zip", "path": "x"}, "doesn't know how to install from 'zip'"),
        ("a string", "wasn't told where its files are"),
    ):
        with pytest.raises(manager.ManagerError, match=fragment) as e:
            _plan(data, source)
        assert str(e.value).startswith("Couldn't install this pipeline: ")
        assert not re.search(r"\b(plugins?|a Git source)\b", str(e.value))


@pytest.mark.parametrize("path, said", [
    ("plugins/aux", "('plugins/aux') that Windows can't create"),
    ("plugins/x.", "('plugins/x.') that Windows can't create"),
    ("plugins\\x", "('plugins\\\\x') with \\ or : in its name, which Windows doesn't allow"),
    ("../elsewhere", "('../elsewhere') outside its own files"),
    ("plugins/.git/x", ("('plugins/.git/x') inside a .git folder of version history, which Clips Kitty doesn't "
                        "install")),
])
def test_a_listed_folder_that_cant_be_installed_says_why_and_who_fixes_it(data, path, said):
    """A Marketplace listing's folder reaches clean_source as `path`; its
    reason is kept, in words, with the folder named."""
    source = {"kind": "git", "url": "https://github.com/example-dev/example", "commit": COMMIT_X, "path": path}
    with pytest.raises(manager.ManagerError) as e:
        _plan(data, source, git="git-is-never-run", fetcher=lambda url, p: pytest.fail("nothing to download"))
    assert str(e.value) == (f"Couldn't install this pipeline: its listing or link names a folder {said}. Its "
                            "developer needs to correct the listing or link.")


def test_a_listed_folder_of_slash_is_the_repository_itself():
    source = {"kind": "git", "url": "https://github.com/example-dev/example", "commit": COMMIT_X}
    assert sources.clean_source({**source, "path": "/"}) == source
    assert sources.clean_source({**source, "path": "plugins//one/"})["path"] == "plugins/one"


def test_a_folder_that_is_missing_is_refused(data, tmp_path):
    with pytest.raises(manager.ManagerError, match="Couldn't install this pipeline: there is no folder at"):
        _plan(data, {"kind": "folder", "path": str(tmp_path / "nowhere")})


def test_a_folder_without_clipskitty_yaml_is_named_as_the_person_chose_it(data, plugin_source):
    chosen = plugin_source.write(plugin_source.base / "Downloads", files={"notes.txt": "x\n"})
    plugin_source.write(chosen / "one", files={"a.txt": "a\n"})
    plan = _plan(data, {"kind": "folder", "path": str(chosen)})
    assert plan["errors"] == [f"The folder you chose ({chosen}) has no clipskitty.yaml in it."]
    assert manager.STAGING not in plan["errors"][0]


def test_the_outer_folder_of_a_download_zip_installs_from_the_one_folder_inside(data, plugin_source):
    """GitHub's "Download ZIP", unpacked by Windows, puts the plugin one folder
    down: example-plugin-main/example-plugin-main/clipskitty.yaml."""
    outer = plugin_source.base / "example-plugin-main"
    inner = plugin_source.write(outer / "example-plugin-main", plugin_source.manifest(), {"src/main.py": "x\n"})
    view = _install(data, {"kind": "folder", "path": str(outer)})
    assert view["source"] == {"kind": "folder", "path": str(inner)}
    assert (store.get(data, "fixture-dev/manager-test").folder / "src/main.py").read_text() == "x\n"
    # Two folders with a clipskitty.yaml: Clips Kitty doesn't guess.
    plugin_source.write(outer / "another", plugin_source.manifest(id="fixture-dev/another"))
    plan = _plan(data, {"kind": "folder", "path": str(outer)})
    assert plan["errors"] == [f"The folder you chose ({outer}) has no clipskitty.yaml in it."]


# ---- Git -------------------------------------------------------------------------------------


def test_install_from_a_git_commit_puts_exactly_that_commit_in_place(data, plugin_source):
    repo = plugin_source.repo()
    first = plugin_source.commit(repo, files={"src/main.py": "print('first')\n"})
    plugin_source.commit(repo, plugin_source.manifest(version="1.1.0"), files={"src/main.py": "print('second')\n"})
    view = _install(data, _git_source(repo, first))
    assert view["version"] == "1.0.0" and view["source"]["commit"] == first
    assert view["source_text"].endswith(f"commit {first[:7]}")
    plugin = store.get(data, "fixture-dev/manager-test")
    assert (plugin.folder / "src/main.py").read_text() == "print('first')\n"
    assert plugin.folder.relative_to(store.root(data)).as_posix() == "installed/fixture-dev/manager-test/1.0.0"
    assert not (plugin.folder / ".git").exists()
    assert store.installed_choice(data, {"id": "fixture-dev/manager-test"}).version == "1.0.0"


def test_a_commit_the_repository_does_not_have_is_refused(data, plugin_source, caplog):
    repo = plugin_source.repo()
    plugin_source.commit(repo)
    with pytest.raises(manager.ManagerError) as e:
        _plan(data, _git_source(repo, COMMIT_X))
    assert str(e.value) == manager.DOWNLOAD_GONE
    assert f"has no commit {COMMIT_X}" in caplog.text  # the developer's detail is in the log


def test_a_symbolic_link_in_the_repository_refuses_the_plugin(data, plugin_source):
    repo = plugin_source.repo()
    plugin_source.write(repo, plugin_source.manifest(), {"src/main.py": "x\n"})
    blob = plugin_source.git(repo, "hash-object", "-w", "--stdin", input=b"/etc/passwd")
    plugin_source.git(repo, "add", "-A")
    plugin_source.git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},src/secrets")
    plugin_source.git(repo, "commit", "-q", "-m", "link")
    commit = plugin_source.git(repo, "rev-parse", "HEAD")
    with pytest.raises(manager.ManagerError, match=r"it contains a shortcut \(src/secrets\), which Clips Kitty "
                                                    r"doesn't install\. Its developer needs to replace it"):
        _plan(data, _git_source(repo, commit))


def test_a_submodule_refuses_the_plugin(data, plugin_source):
    repo = plugin_source.repo()
    plugin_source.write(repo, plugin_source.manifest(), {"src/main.py": "x\n"})
    plugin_source.git(repo, "add", "-A")
    plugin_source.git(repo, "update-index", "--add", "--cacheinfo", f"160000,{COMMIT_X},vendor/lib")
    plugin_source.git(repo, "commit", "-q", "-m", "submodule")
    commit = plugin_source.git(repo, "rev-parse", "HEAD")
    with pytest.raises(manager.ManagerError, match="it includes vendor/lib, a folder linked in from another "
                                                    "project"):
        _plan(data, _git_source(repo, commit))


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_a_program_the_plugin_ships_stays_executable(data, plugin_source):
    manifest = plugin_source.manifest(run={"command": ["bin/detect"]})
    repo = plugin_source.repo()
    plugin_source.write(repo, manifest, {"bin/detect": "#!/bin/sh\necho\n"})
    plugin_source.git(repo, "add", "-A")
    plugin_source.git(repo, "update-index", "--chmod=+x", "bin/detect")
    plugin_source.git(repo, "commit", "-q", "-m", "program")
    commit = plugin_source.git(repo, "rev-parse", "HEAD")
    _install(data, _git_source(repo, commit))
    mode = (store.get(data, "fixture-dev/manager-test").folder / "bin/detect").stat().st_mode
    assert mode & stat.S_IXUSR


def test_a_git_lfs_pointer_is_said_plainly_and_named_in_the_details(data, plugin_source):
    repo = plugin_source.repo()
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 12\n"
    commit = plugin_source.commit(repo, files={"model.onnx": pointer})
    plan = _plan(data, _git_source(repo, commit))
    assert plan["ok"]
    assert plan["warnings"] == [("Some of this pipeline's files couldn't be downloaded, so it may not work. "
                                 "Ask its developer.")]
    assert any(line.startswith("model.onnx is stored with Git LFS") for line in plan["technical"])


# ---- update, roll back, keep two -----------------------------------------------------------


def test_an_update_shows_what_changes_installs_beside_the_old_one_and_rolls_back(data, plugin_source):
    repo = plugin_source.repo()
    v1 = plugin_source.commit(repo)
    v2 = plugin_source.commit(repo, plugin_source.manifest(
        version="1.1.0", execution="hybrid", permissions=["video.read", "network"], network=["api.example.com"],
        sends=[{"data": "frames", "to": "Example Cloud"}]))
    v3 = plugin_source.commit(repo, plugin_source.manifest(version="1.2.0"))
    pid = "fixture-dev/manager-test"
    _install(data, _git_source(repo, v1))

    plan = _plan(data, _git_source(repo, v2))
    assert plan["update"] == {
        "from": "1.0.0", "direction": "update", "added_permissions": ["network"], "removed_permissions": [],
        "added_hosts": ["api.example.com"], "removed_hosts": [],
        "added_data_warnings": ["⚠ Sends frames from your video to Example Cloud"], "execution_changed": True,
        "added_steps": []}
    assert store.get(data, pid).version == "1.0.0"  # still the old one until installed
    view = manager.install(data, plan["plan_id"], app_version=APP)
    assert (view["version"], view["previous"], view["versions"]) == ("1.1.0", "1.0.0", ["1.1.0", "1.0.0"])
    assert store.get(data, pid, "1.0.0").folder.is_dir()

    view = manager.rollback(data, pid, app_version=APP)
    assert (view["version"], view["previous"]) == ("1.0.0", "1.1.0")
    assert store.installed_choice(data, {"id": pid}).version == "1.0.0"
    assert manager.rollback(data, pid, app_version=APP)["version"] == "1.1.0"  # and forward again

    old_folder = store.get(data, pid, "1.0.0").folder
    view = _install(data, _git_source(repo, v3))
    assert (view["version"], view["previous"], view["versions"]) == ("1.2.0", "1.1.0", ["1.2.0", "1.1.0"])
    assert not old_folder.exists()  # only the active and the previous version are kept


def test_rollback_needs_an_earlier_version(data, plugin_source):
    _install(data, {"kind": "folder", "path": str(plugin_source.folder())})
    with pytest.raises(manager.ManagerError, match="no earlier version") as e:
        manager.rollback(data, "fixture-dev/manager-test", app_version=APP)
    assert e.value.status == 409


def test_installing_the_same_version_again_replaces_its_files(data, plugin_source):
    folder = plugin_source.folder(files={"src/main.py": "print(1)\n"})
    _install(data, {"kind": "folder", "path": str(folder)})
    first = store.get(data, "fixture-dev/manager-test").folder
    (folder / "src/main.py").write_text("print(2)\n")
    plan = _plan(data, {"kind": "folder", "path": str(folder)})
    assert plan["update"]["direction"] == "reinstall"
    view = manager.install(data, plan["plan_id"], app_version=APP)
    now = store.get(data, "fixture-dev/manager-test").folder
    assert (now / "src/main.py").read_text() == "print(2)\n"
    assert view["versions"] == ["1.0.0"] and view["previous"] is None
    assert not first.exists() and now.name == "1.0.0~2"


def test_a_plan_is_used_once_and_must_be_one(data, plugin_source):
    plan = _plan(data, {"kind": "folder", "path": str(plugin_source.folder())})
    manager.install(data, plan["plan_id"], app_version=APP)
    with pytest.raises(manager.ManagerError, match="expired or was already used") as e:
        manager.install(data, plan["plan_id"], app_version=APP)
    assert e.value.status == 404
    with pytest.raises(manager.ManagerError, match="not an install plan"):
        manager.install(data, "../../etc", app_version=APP)


def test_files_changed_after_the_plan_are_not_installed(data, plugin_source):
    plan = _plan(data, {"kind": "folder", "path": str(plugin_source.folder())})
    staged = store.root(data) / manager.STAGING / plan["plan_id"] / "files" / "clipskitty.yaml"
    staged.write_text(json.dumps(plugin_source.manifest(version="6.6.6")))
    with pytest.raises(manager.ManagerError, match="changed after they were checked"):
        manager.install(data, plan["plan_id"], app_version=APP)
    assert store.get(data, "fixture-dev/manager-test") is None


def test_old_staging_folders_are_cleared(data, plugin_source):
    plan = _plan(data, {"kind": "folder", "path": str(plugin_source.folder())})
    stage = store.root(data) / manager.STAGING / plan["plan_id"]
    old = stage.stat().st_mtime - manager.STAGING_MAX_AGE - 10
    os.utime(stage, (old, old))
    _plan(data, {"kind": "folder", "path": str(plugin_source.folder())})
    assert not stage.exists()


# ---- on, off, pin, remove, secrets ------------------------------------------------------------


def test_turning_a_plugin_off_and_on(data, plugin_source):
    _install(data, {"kind": "folder", "path": str(plugin_source.folder())})
    pid = "fixture-dev/manager-test"
    assert manager.set_enabled(data, pid, False)["enabled"] is False
    with pytest.raises(ValueError, match="turned off"):
        store.installed_choice(data, {"id": pid})
    assert manager.set_enabled(data, pid, True)["enabled"] is True
    assert store.installed_choice(data, {"id": pid}).id == pid
    with pytest.raises(manager.ManagerError, match="isn't installed") as e:
        manager.set_enabled(data, "fixture-dev/nothing", True)
    assert e.value.status == 404


def test_pinning(data, plugin_source):
    _install(data, {"kind": "folder", "path": str(plugin_source.folder())})
    pid = "fixture-dev/manager-test"
    assert manager.set_pinned(data, pid, True)["pinned"] is True
    assert store.load(data)["plugins"][pid]["pinned"] is True
    assert manager.set_pinned(data, pid, False)["pinned"] is False


def test_remove_deletes_its_files_and_keys(data, plugin_source):
    manifest = plugin_source.manifest(settings={"api_key": {"type": "secret"}})
    _install(data, {"kind": "folder", "path": str(plugin_source.folder(manifest=manifest))})
    pid = "fixture-dev/manager-test"
    folder = store.get(data, pid).folder
    manager.set_secrets(data, pid, {"api_key": "sk-test-0000"})
    removed = manager.remove(data, pid)
    assert removed == {"removed": pid, "files_left": False, "keys_removed": True}
    assert store.get(data, pid) is None and not folder.exists()
    assert not (store.root(data) / manager.INSTALLED / "fixture-dev").exists()
    from core import secrets
    from plugins.runner import secret_name

    assert secrets.load(data, secret_name(pid)) is None


def test_remove_never_deletes_a_folder_installed_in_place(data, plugin_source, install_plugin):
    folder = plugin_source.folder()
    install_plugin(data, folder)
    manager.remove(data, "fixture-dev/manager-test")
    assert (folder / "clipskitty.yaml").is_file()
    assert store.get(data, "fixture-dev/manager-test") is None


def test_secrets_are_stored_by_name_and_never_shown(data, plugin_source):
    manifest = plugin_source.manifest(settings={"api_key": {"type": "secret"}, "region": {"type": "string"}})
    _install(data, {"kind": "folder", "path": str(plugin_source.folder(manifest=manifest))})
    pid = "fixture-dev/manager-test"
    assert manager.set_secrets(data, pid, {"api_key": "sk-test-1234"}) == ["api_key"]
    view = manager.view(data, pid, app_version=APP)
    assert view["secrets_set"] == ["api_key"] and "sk-test-1234" not in json.dumps(view)
    from plugins.runner import secrets_for

    assert secrets_for(store.get(data, pid), data) == {"api_key": "sk-test-1234"}
    with pytest.raises(manager.ManagerError, match="'region' is not one of Manager test's secret settings"):
        manager.set_secrets(data, pid, {"region": "eu"})
    assert manager.set_secrets(data, pid, {"api_key": None}) == []
    assert secrets_for(store.get(data, pid), data) == {}


# ---- the block list ------------------------------------------------------------------------------


def test_a_blocked_version_is_refused_and_a_delisted_one_warned_about(data, plugin_source):
    folder = plugin_source.folder()
    blocked = {"severity": "blocked", "reason": "uploads videos to an unknown server"}
    plan = _plan(data, {"kind": "folder", "path": str(folder)}, blocked=lambda pid, v: blocked)
    assert plan["errors"] == ["Blocked: uploads videos to an unknown server"] and plan["plan_id"] is None
    plan = _plan(data, {"kind": "folder", "path": str(folder)},
                 blocked=lambda pid, v: {"severity": "delisted", "reason": "broken by a game patch"})
    assert plan["ok"] and "No longer listed: broken by a game patch" in plan["warnings"]
    # Blocked between the plan and the click: still refused.
    plan = _plan(data, {"kind": "folder", "path": str(folder)})
    with pytest.raises(manager.ManagerError, match="Blocked"):
        manager.install(data, plan["plan_id"], app_version=APP, blocked=lambda pid, v: blocked)


def test_rollback_to_a_blocked_version_is_refused(data, plugin_source):
    _install(data, {"kind": "folder", "path": str(plugin_source.folder("a"))})
    _install(data, {"kind": "folder", "path": str(plugin_source.folder("b", plugin_source.manifest(version="1.1.0")))})
    with pytest.raises(manager.ManagerError, match="Blocked: bad"):
        manager.rollback(data, "fixture-dev/manager-test", app_version=APP,
                         blocked=lambda pid, v: {"severity": "blocked", "reason": "bad"} if v == "1.0.0" else None)


# ---- folders ---------------------------------------------------------------------------------------


def test_a_folder_install_leaves_out_git_and_caches(data, plugin_source):
    folder = plugin_source.folder(files={".git/config": "[core]\n", "src/__pycache__/main.cpython-311.pyc": b"\0",
                                         "docs/notes.md": "notes\n"})
    _install(data, {"kind": "folder", "path": str(folder)})
    installed = store.get(data, "fixture-dev/manager-test").folder
    names = sorted(p.relative_to(installed).as_posix() for p in installed.rglob("*") if p.is_file())
    assert names == ["clipskitty.yaml", "docs/notes.md", "src/main.py"]


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need extra rights on Windows")
def test_a_symbolic_link_in_a_folder_refuses_it(data, plugin_source, tmp_path):
    folder = plugin_source.folder()
    (folder / "elsewhere").symlink_to(tmp_path)
    with pytest.raises(manager.ManagerError, match=r"a shortcut \(elsewhere\)"):
        _plan(data, {"kind": "folder", "path": str(folder)})


def test_a_plugin_over_the_size_limits_is_refused(data, plugin_source, monkeypatch):
    monkeypatch.setattr(sources, "MAX_FILES", 2)
    folder = plugin_source.folder(files={"a.txt": "a", "b.txt": "b"})
    with pytest.raises(manager.ManagerError, match="it has more than 2 files, more than Clips Kitty installs"):
        _plan(data, {"kind": "folder", "path": str(folder)})


@pytest.mark.parametrize("name, fragment", [
    ("../outside.py", "outside its own folder"),
    ("/etc/passwd", "outside its own folder"),
    ("src\\main.py", "Windows doesn't allow"),
    ("C:/Windows/x", "Windows doesn't allow"),
    (".git/hooks/post-checkout", "part of a .git folder"),
    ("src/.GIT/config", "part of a .git folder"),
    ("aux.txt", "Windows can't create"),
    ("trailing. ", "Windows can't create"),
])
def test_paths_that_would_land_elsewhere_are_refused(name, fragment):
    with pytest.raises(sources.SourceError, match=fragment):
        sources._safe_relative(name)


def test_names_that_differ_only_in_case_are_refused():
    budget = sources._Budget()
    budget.add("README.md", 1)
    with pytest.raises(sources.SourceError, match=r"differ only in capital letters \('README.md' and 'readme.md'\)"):
        budget.add("readme.md", 1)


# ---- GitHub's archive, when the PC has no Git ------------------------------------------------------


def _archive(files: dict, commit: str, *, stamp: str | None = None, top: str = "plugin") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT,
                      pax_headers={"comment": stamp or commit}) as tar:
        info = tarfile.TarInfo(f"{top}-{commit}")
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
        for name, content in files.items():
            if isinstance(content, tarfile.TarInfo):
                content.name = f"{top}-{commit}/{name}"
                tar.addfile(content)
                continue
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(f"{top}-{commit}/{name}")
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _no_git(monkeypatch):
    monkeypatch.setattr(sources, "find_git", lambda: None)


def test_without_git_a_github_commit_comes_from_its_archive(data, plugin_source, monkeypatch):
    _no_git(monkeypatch)
    files = {"clipskitty.yaml": json.dumps(plugin_source.manifest()), "src/main.py": "print('archive')\n"}
    asked = []

    def fetcher(url, path):
        asked.append(url)
        Path(path).write_bytes(_archive(files, COMMIT_X))

    source = {"kind": "git", "url": "https://github.com/fixture-dev/manager-test", "commit": COMMIT_X}
    view = _install(data, source, fetcher=fetcher)
    assert asked == [f"https://github.com/fixture-dev/manager-test/archive/{COMMIT_X}.tar.gz"]
    assert view["source"] == {"kind": "git", "url": source["url"], "commit": COMMIT_X}
    assert (store.get(data, "fixture-dev/manager-test").folder / "src/main.py").read_text() == "print('archive')\n"


@pytest.mark.parametrize("extra, fragment", [
    ("symlink", r"a shortcut \(src/data\)"),
    ("escape", "outside its own folder"),
    ("stamp", "the download arrived damaged"),  # an archive of another commit
])
def test_an_archive_that_could_escape_or_is_of_another_commit_is_refused(data, plugin_source, monkeypatch, extra,
                                                                         fragment):
    _no_git(monkeypatch)
    files = {"clipskitty.yaml": json.dumps(plugin_source.manifest()), "src/main.py": "x\n"}
    stamp = None
    if extra == "symlink":
        link = tarfile.TarInfo()
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        files["src/data"] = link
    elif extra == "escape":
        files["../../evil.py"] = "x\n"
    else:
        stamp = "f" * 40

    def fetcher(url, path):
        Path(path).write_bytes(_archive(files, COMMIT_X, stamp=stamp))

    source = {"kind": "git", "url": "https://github.com/fixture-dev/manager-test", "commit": COMMIT_X}
    with pytest.raises(manager.ManagerError, match=fragment):
        _plan(data, source, fetcher=fetcher)


def test_a_failed_download_says_so_plainly_and_keeps_the_details_in_the_log(data, monkeypatch, caplog):
    import urllib.error
    import urllib.request

    _no_git(monkeypatch)

    def offline(request, timeout=None):
        raise urllib.error.URLError(OSError("[Errno 101] Network is unreachable: C:/Users/someone/AppData"))

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    source = {"kind": "git", "url": "https://github.com/fixture-dev/manager-test", "commit": COMMIT_X}
    with pytest.raises(manager.ManagerError) as e:
        _plan(data, source)
    assert str(e.value) == "Couldn't download this pipeline. Check your internet connection and try again."
    assert "Network is unreachable" in caplog.text
    assert list((store.root(data) / manager.STAGING).iterdir()) == []


def test_a_git_repository_that_isnt_there_says_its_files_are_gone(data, tmp_path, caplog):
    """A mistyped, deleted or private repository is not a connection problem."""
    if not sources.find_git():
        pytest.skip("git is not installed")
    for url in ((tmp_path / "no-such-repository").resolve().as_uri(), "file:///nonexistent/repo-typo"):
        with pytest.raises(manager.ManagerError) as e:
            _plan(data, {"kind": "git", "url": url, "commit": "a" * 40})
        assert str(e.value) == manager.DOWNLOAD_GONE
        assert "internet" not in str(e.value) and "repo" not in str(e.value)
    assert "git fetch failed" in caplog.text and "does not appear to be a git repository" in caplog.text
    assert list((store.root(data) / manager.STAGING).iterdir()) == []


def test_the_download_sentences_are_plain_words():
    for text in [*manager.FETCH_FAILED.values(), manager.PICKLE_MODEL, sources.NOT_SAVED]:
        assert not JARGON.search(text), text
    assert set(manager.FETCH_FAILED) == {sources.OFFLINE, sources.GONE, sources.DISK, sources.DAMAGED, sources.OTHER}


def _http_error(code: int):
    return urllib.error.HTTPError("https://github.com/x", code, "status", {}, None)


def _failing(make):
    def fetcher(url, path):
        raise make()

    return fetcher


@pytest.mark.parametrize("fetcher, said", [
    (_failing(lambda: _http_error(404)), manager.DOWNLOAD_GONE),  # a commit or repository no longer there
    (_failing(lambda: _http_error(410)), manager.DOWNLOAD_GONE),
    (_failing(lambda: _http_error(403)), manager.DOWNLOAD_GONE),  # GitHub's answer for a private repository
    (_failing(lambda: _http_error(503)), manager.DOWNLOAD_OTHER),
    (_failing(lambda: urllib.error.URLError(OSError(errno.ENETUNREACH, "Network is unreachable"))),
     manager.DOWNLOAD_FAILED),
    (_failing(lambda: ConnectionResetError(104, "Connection reset by peer")), manager.DOWNLOAD_FAILED),
    (_failing(lambda: TimeoutError("timed out")), manager.DOWNLOAD_FAILED),
    (_failing(lambda: http.client.IncompleteRead(b"x", 10)), manager.DOWNLOAD_FAILED),
    (_failing(lambda: OSError(errno.ENOSPC, "No space left on device")), manager.DISK_FULL),
    (_failing(lambda: PermissionError(errno.EACCES, "Access is denied")), manager.DOWNLOAD_OTHER),
    (lambda url, path: Path(path).write_bytes(b"not a gzip file"), manager.DOWNLOAD_DAMAGED),
])
def test_each_kind_of_download_failure_says_its_own_cause(data, monkeypatch, caplog, fetcher, said):
    _no_git(monkeypatch)
    source = {"kind": "git", "url": "https://github.com/fixture-dev/manager-test", "commit": COMMIT_X}
    with pytest.raises(manager.ManagerError) as e:
        _plan(data, source, fetcher=fetcher)
    assert str(e.value) == said
    assert e.value.status == 400
    assert said == manager.DOWNLOAD_FAILED or "internet" not in str(e.value)
    assert caplog.text  # what went wrong is in the log
    assert list((store.root(data) / manager.STAGING).iterdir()) == []


def test_a_full_disk_while_saving_the_files_says_so(data, plugin_source, monkeypatch):
    def full(*_a, **_k):
        raise OSError(errno.ENOSPC, "No space left on device")

    folder = plugin_source.folder()
    monkeypatch.setattr(sources, "_write", full)
    with pytest.raises(manager.ManagerError) as e:
        _plan(data, {"kind": "folder", "path": str(folder)})
    assert str(e.value) == manager.DISK_FULL

    def broken(*_a, **_k):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(sources, "_write", broken)
    with pytest.raises(manager.ManagerError) as e:
        _plan(data, {"kind": "folder", "path": str(folder)})
    assert str(e.value) == ("Couldn't install this pipeline: its files couldn't be saved on this PC. Try again; "
                            "if it happens again, send a bug report from Feedback (it includes the details).")


def test_download_classifies_what_went_wrong(tmp_path, monkeypatch):
    import urllib.request

    class Response:
        def __init__(self, error=None):
            self.error, self.sent = error, False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n):
            if self.error:
                raise self.error
            if self.sent:
                return b""
            self.sent = True
            return b"x" * 10

    # A connection that broke off mid-download: an http.client error, not an OSError.
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: Response(
        http.client.IncompleteRead(b"x", 100)))
    with pytest.raises(sources.FetchFailed) as e:
        sources.download("https://example.com/a.tar.gz", tmp_path / "a")
    assert e.value.kind == sources.OFFLINE
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: Response(_http_error(404)))
    with pytest.raises(sources.FetchFailed) as e:
        sources.download("https://example.com/a.tar.gz", tmp_path / "a")
    assert e.value.kind == sources.GONE
    if Path("/dev/full").exists():  # a file that is always on a full disk
        monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: Response())
        with pytest.raises(sources.FetchFailed) as e:
            sources.download("https://example.com/a.tar.gz", Path("/dev/full"))
        assert e.value.kind == sources.DISK


@pytest.mark.parametrize("error, kind", [
    (_http_error(401), sources.GONE),
    (_http_error(500), sources.OTHER),
    (OSError(errno.ENOSPC, "No space left on device"), sources.DISK),
    (OSError(getattr(errno, "EDQUOT", errno.ENOSPC), "Disk quota exceeded"), sources.DISK),
    (OSError(errno.ENETUNREACH, "Network is unreachable"), sources.OFFLINE),
    (ConnectionRefusedError(), sources.OFFLINE),
    (OSError(errno.EIO, "Input/output error"), sources.OTHER),
    (ValueError("anything else"), sources.OTHER),
])
def test_what_a_download_error_is(error, kind):
    assert sources.failure(error) == kind


def test_a_windows_full_disk_is_a_full_disk():
    error = OSError(errno.EIO, "There is not enough space on the disk")
    error.winerror = 112  # set by Windows only; ERROR_DISK_FULL
    assert sources.failure(error) == sources.DISK


@pytest.mark.parametrize("stderr, kind", [
    ("remote: Repository not found.\nfatal: repository 'https://github.com/example-dev/x/' not found", sources.GONE),
    ("fatal: could not read Username for 'https://github.com': terminal prompts disabled", sources.GONE),
    ("fatal: '/home/someone/x' does not appear to be a git repository\nfatal: Could not read from remote "
     "repository.\n\nPlease make sure you have the correct access rights\nand the repository exists.", sources.GONE),
    ("error: Server does not allow request for unadvertised object 0123456", sources.GONE),
    ("fatal: couldn't find remote ref 0123456789abcdef0123456789abcdef01234567", sources.GONE),
    ("fatal: unable to access 'https://example.com/x/': The requested URL returned error: 404", sources.GONE),
    ("fatal: unable to access 'https://github.com/example-dev/x/': Could not resolve host: github.com",
     sources.OFFLINE),
    ("fatal: unable to access 'https://github.com/example-dev/x/': Failed to connect to github.com port 443",
     sources.OFFLINE),
    ("fatal: unable to access 'https://github.com/example-dev/x/': SSL certificate problem: unable to get local "
     "issuer certificate", sources.OFFLINE),
    ("fatal: unable to access 'https://github.com/example-dev/x/': OpenSSL SSL_read: Connection was reset",
     sources.OFFLINE),
    # A word in the address says nothing: an "ssl" repository that isn't there is gone.
    ("fatal: repository 'https://github.com/example-dev/ssl/' not found", sources.GONE),
    ("fatal: write error: No space left on device", sources.DISK),
    ("error: object 0123456: hasDotgit: contains '.git'\nfatal: fsck error in packed object", sources.OTHER),
    # The server's own disk is full: Git passes its words on ("remote: ..."), and this PC's disk is fine.
    ("remote: fatal: Unable to create temporary file: No space left on device\nerror: git upload-pack: "
     "git-pack-objects died with error.\nfatal: the remote end hung up unexpectedly", sources.OTHER),
    ("remote: error: internal server error\nfatal: early EOF", sources.OTHER),  # the server failed, not the line
    ("remote: Repository not found.\nfatal: Authentication failed", sources.GONE),  # the server can say it's gone
    ("error: RPC failed; curl 56 Recv failure: Connection reset by peer\nfatal: early EOF", sources.OFFLINE),
    # A Git that can't run its own helper is not a missing repository.
    ("error: cannot run git-remote-https: No such file or directory", sources.OTHER),
])
def test_what_a_git_failure_is(stderr, kind):
    assert sources._git_failure(stderr) == kind


@pytest.mark.parametrize("stderr, kind", [
    ("fatal: cannot mkdir /tmp/clipskitty-git-x/repo.git: No such file or directory", sources.OTHER),
    ("fatal: Not a valid object name 0123456", sources.OTHER),
    ("fatal: unable to create temporary file: No space left on device", sources.DISK),
])
def test_only_a_fetch_is_read_as_the_repository_or_the_connection(tmp_path, monkeypatch, stderr, kind):
    """init, cat-file and ls-tree work on this PC only: their failure is the
    disk or something else, never a missing repository or the internet."""
    import subprocess

    assert sources._git_failure(stderr, fetching=False) == kind

    def failed(command, **_k):
        return subprocess.CompletedProcess(command, 128, b"", stderr.encode())

    monkeypatch.setattr(subprocess, "run", failed)
    for command in ("init", "cat-file", "ls-tree"):
        with pytest.raises(sources.FetchFailed) as e:
            sources._git("git", [command], cwd=tmp_path, hooks=tmp_path)
        assert e.value.kind == kind
    monkeypatch.setattr(subprocess, "run", lambda command, **_k: subprocess.CompletedProcess(
        command, 128, b"", b"fatal: '/x' does not appear to be a git repository"))
    with pytest.raises(sources.FetchFailed) as e:
        sources._git("git", ["fetch"], cwd=tmp_path, hooks=tmp_path)
    assert e.value.kind == sources.GONE


def test_git_speaks_english_whatever_the_pcs_language(monkeypatch):
    """_git_failure reads Git's English words; Git for Windows ships its
    messages in many languages, and so does the app."""
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setenv("LC_MESSAGES", "de_DE.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("LANGUAGE", "de")
    env = sources._git_env()
    assert env["LC_ALL"] == "C"  # wins over LANG and LC_MESSAGES
    assert "LANGUAGE" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_git_that_times_out_or_cant_start(tmp_path, monkeypatch):
    import subprocess

    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired("git", sources.GIT_TIMEOUT)

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(sources.FetchFailed) as e:
        sources._git("git", ["fetch"], cwd=tmp_path, hooks=tmp_path)
    assert e.value.kind == sources.OFFLINE
    with pytest.raises(sources.FetchFailed) as e:
        sources._git("git", ["cat-file", "--batch"], cwd=tmp_path, hooks=tmp_path)
    assert e.value.kind == sources.OTHER  # works on this PC only: not the connection

    def missing(*_a, **_k):
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", "git")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(sources.FetchFailed) as e:
        sources._git("git", ["fetch"], cwd=tmp_path, hooks=tmp_path)
    assert e.value.kind == sources.OTHER


# ---- reading a folder ----------------------------------------------------------------------------


def test_a_folder_that_cant_be_read_is_named_not_a_crash(data, plugin_source, monkeypatch):
    """plugin_folder and copy_folder read the folder the person chose; a file
    they can't read (locked by another program, a cloud copy that won't come
    down) is named, and the request never fails with a raw error."""
    folder = plugin_source.folder()
    real_is_file = Path.is_file

    def locked(self, *a, **k):
        if self.name == "clipskitty.yaml" and folder in self.parents:
            raise PermissionError(errno.EACCES, "Access is denied", str(self))
        return real_is_file(self, *a, **k)

    monkeypatch.setattr(Path, "is_file", locked)
    assert sources.plugin_folder(folder) == folder
    with pytest.raises(manager.ManagerError) as e:
        _plan(data, {"kind": "folder", "path": str(folder)})
    assert str(e.value) == ("Couldn't install this pipeline: Clips Kitty couldn't read clipskitty.yaml in that "
                            "folder. If another program has it open, close it and try again.")
    assert list((store.root(data) / manager.STAGING).iterdir()) == []


def test_a_file_that_cant_be_read_while_copying_is_named(data, plugin_source, monkeypatch, caplog):
    folder = plugin_source.folder()
    real_read = Path.read_bytes

    def locked(self):
        if self.name == "main.py":
            raise PermissionError(errno.EACCES, "The process cannot access the file", str(self))
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", locked)
    with pytest.raises(manager.ManagerError) as e:
        _plan(data, {"kind": "folder", "path": str(folder)})
    assert str(e.value) == ("Couldn't install this pipeline: Clips Kitty couldn't read src/main.py in that "
                            "folder. If another program has it open, close it and try again.")
    assert "The process cannot access the file" in caplog.text


def test_a_subfolder_that_cant_be_opened_is_named(data, plugin_source, monkeypatch):
    """os.walk skips a folder it can't list unless told otherwise: the
    pipeline would install without its files."""
    folder = plugin_source.folder(files={"private/notes.txt": "x\n"})
    real_scandir = os.scandir

    def scandir(path="."):
        if isinstance(path, (str, os.PathLike)) and Path(path).name == "private":
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    with pytest.raises(manager.ManagerError) as e:
        _plan(data, {"kind": "folder", "path": str(folder)})
    assert str(e.value) == ("Couldn't install this pipeline: Clips Kitty couldn't open the folder private in that "
                            "folder. Check that you can open it yourself, then try again.")


def test_a_windows_junction_counts_as_a_link_on_every_python(tmp_path, monkeypatch):
    """Path.is_junction only exists from Python 3.12; the app builds with
    3.11. A junction's reparse tag is the mount-point one; other reparse
    points (OneDrive's placeholders) are ordinary files."""
    import types

    junction, cloud, plain = (tmp_path / name for name in ("junction", "cloud", "plain"))
    for path in (junction, cloud, plain):
        path.mkdir()
    real_lstat = os.lstat
    tags = {"junction": 0xA0000003, "cloud": 0x9000601A}

    def lstat(path, *a, **k):
        name = Path(path).name
        if name in tags:
            return types.SimpleNamespace(st_reparse_tag=tags[name], st_mode=stat.S_IFDIR)
        return real_lstat(path, *a, **k)

    monkeypatch.setattr(os, "lstat", lstat)
    assert sources._is_link(junction) is True
    assert sources._is_link(cloud) is False
    assert sources._is_link(plain) is False

    def unreadable(path, *a, **k):
        raise PermissionError(errno.EACCES, "Access is denied")

    monkeypatch.setattr(os, "lstat", unreadable)
    assert sources._is_link(plain) is False  # what follows reads it, and says so if it can't


def test_a_junction_in_a_folder_refuses_it(data, plugin_source, monkeypatch):
    import types

    folder = plugin_source.folder(files={"linked/a.txt": "a\n"})
    real_lstat = os.lstat

    def lstat(path, *a, **k):
        if Path(path).name == "linked":
            return types.SimpleNamespace(st_reparse_tag=0xA0000003, st_mode=stat.S_IFDIR)
        return real_lstat(path, *a, **k)

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(manager.ManagerError, match=r"it contains a shortcut \(linked\), which Clips Kitty doesn't "
                                                   r"install\. Its developer needs to replace it with the real "
                                                   r"folder\."):
        _plan(data, {"kind": "folder", "path": str(folder)})


def test_without_git_a_repository_off_github_says_git_is_needed(data, monkeypatch):
    _no_git(monkeypatch)
    source = {"kind": "git", "url": "https://example.com/fixture-dev/manager-test", "commit": COMMIT_X}
    with pytest.raises(manager.ManagerError, match="needs Git, a free program that isn't installed on this PC"):
        _plan(data, source, fetcher=lambda url, path: pytest.fail("nothing to download"))


# ---- what GET /plugins shows ------------------------------------------------------------------------


def test_the_listing_shows_installed_plugins_and_the_official_modes(data, plugin_source):
    _install(data, {"kind": "folder", "path": str(plugin_source.folder())})
    listing = manager.listing(data, app_version=APP)
    (entry,) = listing["plugins"]
    assert entry["id"] == "fixture-dev/manager-test" and entry["enabled"] and not entry["builtin"]
    assert entry["details"]["tier_text"] == "Not listed · Clips Kitty has not checked this"
    assert entry["problem"] is None and entry["flag"] is None
    assert sorted(b["id"] for b in listing["builtin"]) == ["clipskitty/gaming", "clipskitty/shorts",
                                                           "clipskitty/sports"]
    assert all(b["details"]["tier_text"] == "Official" and b["details"]["notice"] is None for b in listing["builtin"])


def test_a_plugin_an_app_update_left_behind_says_why(data, plugin_source, monkeypatch):
    manifest = plugin_source.manifest(requires={"clips_kitty": ">=2.0, <3", "plugin_api": 1})
    _install(data, {"kind": "folder", "path": str(plugin_source.folder(manifest=manifest))})
    pid = "fixture-dev/manager-test"
    assert manager.view(data, pid, app_version="2.4.0")["problem"] is None
    assert manager.view(data, pid, app_version="3.0.0")["problem"] == "it needs Clips Kitty >=2.0, <3, and this is 3.0.0"
    # ...and a job naming it is refused with that reason, at submit and at run.
    monkeypatch.setattr(store, "app_version", lambda: "3.0.0")
    with pytest.raises(ValueError, match=r"can't run here: it needs Clips Kitty >=2\.0, <3, and this is 3\.0\.0"):
        store.installed_choice(data, {"id": pid})


# ---- what it does with a video's moments (Rate & understand) ------------------------------------------


def _rater(plugin_source, **changes) -> dict:
    """A plugin that rates moments of Quarkbloom Arena, a made-up game."""
    return plugin_source.manifest(**{
        "name": "Quarkbloom Rater", "description": "Rates moments of Quarkbloom Arena (a made-up game).",
        "inputs": ["moments", "transcript"], "outputs": ["ratings"], "permissions": ["transcript.read"], **changes})


def test_installed_summary_reports_inputs_and_outputs(data, plugin_source):
    view = _install(data, {"kind": "folder", "path": str(plugin_source.folder(manifest=_rater(plugin_source)))})
    assert (view["inputs"], view["outputs"]) == (["moments", "transcript"], ["ratings"])
    listing = manager.listing(data, app_version=APP)
    (entry,) = listing["plugins"]
    assert (entry["inputs"], entry["outputs"]) == (["moments", "transcript"], ["ratings"])
    assert all((b["inputs"], b["outputs"]) == (["video", "transcript"], ["ranges"]) for b in listing["builtin"])
    plan = _plan(data, {"kind": "folder", "path": str(plugin_source.folder("finder"))})
    assert (plan["plugin"]["inputs"], plan["plugin"]["outputs"]) == (["video"], ["ranges"])


def test_an_update_that_starts_rating_says_so(data, plugin_source):
    repo = plugin_source.repo()
    v1 = plugin_source.commit(repo)  # finds moments
    v2 = plugin_source.commit(repo, plugin_source.manifest(
        version="1.1.0", inputs=["video", "transcript", "moments"], outputs=["ranges", "ratings"],
        permissions=["video.read", "transcript.read"]))
    _install(data, _git_source(repo, v1))
    plan = _plan(data, _git_source(repo, v2))
    assert plan["ok"] and plan["update"]["added_steps"] == ["Rates moments"]
    assert plan["details"]["steps"] == ["Finds moments", "Rates moments"]
    text = permissions.render_text(plan)
    assert "What it does: Finds moments · Rates moments" in text
    assert "  Now also: Rates moments" in text
    # An update that does what it did before has nothing new to say.
    _install(data, _git_source(repo, v2))
    v3 = plugin_source.commit(repo, plugin_source.manifest(
        version="1.2.0", inputs=["video", "transcript", "moments"], outputs=["ranges", "ratings"],
        permissions=["video.read", "transcript.read"]))
    plan = _plan(data, _git_source(repo, v3))
    assert plan["update"]["added_steps"] == [] and "Now also" not in permissions.render_text(plan)


def test_details_carry_steps_and_a_time_limit(plugin_source):
    from plugins import runner

    command = ["{python}", "src/main.py"]
    manifests = {
        "finder": plugin_source.manifest(),
        "notes finder": plugin_source.manifest(inputs=["video", "transcript"], outputs=["ranges", "context"],
                                               permissions=["video.read", "transcript.read"]),
        "understander": _rater(plugin_source, outputs=["context"]),
        "rater": _rater(plugin_source, run={"command": command, "timeout_minutes": 5}),
        "all three": plugin_source.manifest(inputs=["video", "transcript", "moments"],
                                            outputs=["ranges", "context", "ratings"],
                                            permissions=["video.read", "transcript.read"],
                                            run={"command": command, "timeout_minutes": 1}),
    }
    got = {name: (d["steps"], d["time_limit"])
           for name, d in ((name, permissions.describe(m)) for name, m in manifests.items())}
    limit = "Clips Kitty stops it after {} when it rates or understands a video’s moments."
    assert got == {
        "finder": (["Finds moments"], None),
        "notes finder": (["Finds moments", "Understands what it finds"], None),
        "understander": (["Understands moments"], limit.format("10 minutes")),
        "rater": (["Rates moments"], limit.format("5 minutes")),
        "all three": (["Finds moments", "Understands moments", "Rates moments"], limit.format("1 minute")),
    }
    # The limit it shows is the one the app stops it at.
    assert runner.timeout_seconds(manifests["understander"], default=runner.MOMENT_TIMEOUT_MINUTES) == 10 * 60
    # The install screen says what it does right after how it runs.
    rater = manifests["rater"]
    lines = permissions.render_text({"plugin": rater, "details": permissions.describe(rater)}).splitlines()
    assert lines[lines.index(permissions.EXECUTION["local"]) + 1] == "What it does: Rates moments"
    # Something that isn't a manifest yet (a plan refused as invalid) says nothing about it.
    assert (permissions.describe({})["steps"], permissions.describe({})["time_limit"]) == ([], None)
