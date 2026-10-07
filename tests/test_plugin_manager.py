"""The plugin manager: install from a folder or a Git commit, update beside the
old version, roll back, turn off, pin, remove, and refuse what it should.

Every Git source is a repository made in a temporary folder and reached as
file://; nothing touches the network. GitHub's archive (the path for a PC
without Git) is a tarball made here and handed over by a fake fetcher.
"""

import io
import json
import os
import stat
import tarfile
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
    repo = plugin_source.repo()
    # Committed with the trap filter off, so only a checkout would run it.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
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
    assert d["notice"] == "This plugin is code from the internet. It runs on this PC with your rights."
    assert d["tier_text"] == "Not listed · Clips Kitty has not checked this"
    assert {p["id"]: (p["label"], p["enforcement"]) for p in d["permissions"]} == {
        "video.read": ("Reads the video you process", "enforced for the hand-over"),
        "network": ("Connects to: api.example.com", "declared by the developer"),
        "ffmpeg": ("Uses Clips Kitty's FFmpeg", "enforced for the hand-over"),
    }
    assert d["data_warnings"] == ["⚠ Sends your video to Example Cloud, when you choose Cloud",
                                  "⚠ Sends your video's transcript off this computer"]
    assert d["secrets"] == ["api_key"] and "Other plugins and programs running as you can read them" in d["secrets_notice"]
    assert d["requirements"][0] == "A graphics card is recommended, 6 GB of video memory"
    text = permissions.render_text(plan)
    assert "⚠ Sends your video to Example Cloud, when you choose Cloud" in text
    assert "Connects to: api.example.com (declared by the developer)" in text
    assert not (store.root(data) / store.STATE_FILE).exists()


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
        ({"kind": "folder"}, "folder's path"),
        ({"kind": "zip", "path": "x"}, "unknown kind"),
        ("a string", "a source is an object"),
    ):
        with pytest.raises(manager.ManagerError, match=fragment):
            _plan(data, source)


def test_a_folder_that_is_missing_is_refused(data, tmp_path):
    with pytest.raises(manager.ManagerError, match="is not a folder"):
        _plan(data, {"kind": "folder", "path": str(tmp_path / "nowhere")})


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


def test_a_commit_the_repository_does_not_have_is_refused(data, plugin_source):
    repo = plugin_source.repo()
    plugin_source.commit(repo)
    with pytest.raises(manager.ManagerError, match="no commit 0123456"):
        _plan(data, _git_source(repo, COMMIT_X))


def test_a_symbolic_link_in_the_repository_refuses_the_plugin(data, plugin_source):
    repo = plugin_source.repo()
    plugin_source.write(repo, plugin_source.manifest(), {"src/main.py": "x\n"})
    blob = plugin_source.git(repo, "hash-object", "-w", "--stdin", input=b"/etc/passwd")
    plugin_source.git(repo, "add", "-A")
    plugin_source.git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},src/secrets")
    plugin_source.git(repo, "commit", "-q", "-m", "link")
    commit = plugin_source.git(repo, "rev-parse", "HEAD")
    with pytest.raises(manager.ManagerError, match="src/secrets: symbolic links are not allowed"):
        _plan(data, _git_source(repo, commit))


def test_a_submodule_refuses_the_plugin(data, plugin_source):
    repo = plugin_source.repo()
    plugin_source.write(repo, plugin_source.manifest(), {"src/main.py": "x\n"})
    plugin_source.git(repo, "add", "-A")
    plugin_source.git(repo, "update-index", "--add", "--cacheinfo", f"160000,{COMMIT_X},vendor/lib")
    plugin_source.git(repo, "commit", "-q", "-m", "submodule")
    commit = plugin_source.git(repo, "rev-parse", "HEAD")
    with pytest.raises(manager.ManagerError, match="vendor/lib: the plugin uses a Git submodule"):
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


def test_a_git_lfs_pointer_is_named_in_the_warnings(data, plugin_source):
    repo = plugin_source.repo()
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 12\n"
    commit = plugin_source.commit(repo, files={"model.onnx": pointer})
    plan = _plan(data, _git_source(repo, commit))
    assert plan["ok"]
    assert any(w.startswith("model.onnx is stored with Git LFS") for w in plan["warnings"])


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
        "added_data_warnings": ["⚠ Sends frames from your video to Example Cloud"], "execution_changed": True}
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
    assert manager.remove(data, pid) == {"removed": pid, "files_left": False, "keys_removed": True}
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
    with pytest.raises(manager.ManagerError, match="elsewhere: symbolic links are not allowed"):
        _plan(data, {"kind": "folder", "path": str(folder)})


def test_a_plugin_over_the_size_limits_is_refused(data, plugin_source, monkeypatch):
    monkeypatch.setattr(sources, "MAX_FILES", 2)
    folder = plugin_source.folder(files={"a.txt": "a", "b.txt": "b"})
    with pytest.raises(manager.ManagerError, match="more than 2 files"):
        _plan(data, {"kind": "folder", "path": str(folder)})


@pytest.mark.parametrize("name, fragment", [
    ("../outside.py", "outside the plugin's folder"),
    ("/etc/passwd", "outside the plugin's folder"),
    ("src\\main.py", "can't be installed on Windows"),
    ("C:/Windows/x", "can't be installed on Windows"),
    (".git/hooks/post-checkout", "can't contain a .git entry"),
    ("src/.GIT/config", "can't contain a .git entry"),
    ("aux.txt", "Windows can't create"),
    ("trailing. ", "Windows can't create"),
])
def test_paths_that_would_land_elsewhere_are_refused(name, fragment):
    with pytest.raises(sources.SourceError, match=fragment):
        sources._safe_relative(name)


def test_names_that_differ_only_in_case_are_refused():
    budget = sources._Budget()
    budget.add("README.md", 1)
    with pytest.raises(sources.SourceError, match="differ only in case"):
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
    ("symlink", "symbolic links are not allowed"),
    ("escape", "outside the plugin's folder"),
    ("stamp", "the archive is of commit fffffff"),
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


def test_without_git_a_repository_off_github_says_git_is_needed(data, monkeypatch):
    _no_git(monkeypatch)
    source = {"kind": "git", "url": "https://example.com/fixture-dev/manager-test", "commit": COMMIT_X}
    with pytest.raises(manager.ManagerError, match="needs Git, which isn't installed"):
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
