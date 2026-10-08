"""`python -m clipskitty_sdk listing`: the file that lists a finished plugin
in Awesome Clips Kitty, made from its folder and its git repository
(clipskitty_sdk.listing).

Each test makes a git repository whose remote is named
https://github.com/test-owner/word-moments, with a push address of a bare
repository in the test's own folder, so pushing works and nothing reaches
the network. Git runs with an empty global configuration. Like every
tests/test_plugin_sdk_*.py file, this needs only pytest, PyYAML and git, and
imports nothing from Clips Kitty's engine, so it also runs in CI's SDK
(Windows) job. tests/test_registry.py checks a listing made here with the
registry's own rules.
"""

import ast
import datetime
import hashlib
import inspect
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import listing, scaffold  # noqa: E402
from clipskitty_sdk.__main__ import main  # noqa: E402

yaml = pytest.importorskip("yaml")  # listing checks clipskitty.yaml first
GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="needs git")

OWNER = "test-owner"
NAME = "word-moments"
PLUGIN_ID = f"{OWNER}/{NAME}"
GITHUB = f"https://github.com/{OWNER}/{NAME}"
TODAY = "2026-10-07"
MANIFEST = """\
manifest_version: 1
id: {id}
name: Word Moments
version: {version}
kind: pipeline
capability: highlight_detection
description: Finds the moments when the commentary says one of your words.
license: MIT
{repository}requires: {{clips_kitty: ">=2.0", plugin_api: 1}}
run:
  command: ["{{python}}", "src/main.py"]
execution: local
inputs: [transcript]
outputs: [ranges]
permissions: [transcript.read]
"""
MAIN = """\
from clipskitty_sdk import run


def main(job):
    job.finish()


if __name__ == "__main__":
    run(main)
"""
NEXT = [
    "Next, in a copy of ColinGPT9/clips-studio:",
    f"  1. add this file as awesome-clips-kitty/registry/pipelines/{PLUGIN_ID}.yaml",
    "  2. run: python scripts/build_registry_index.py   (it needs PyYAML and the network)",
    "  3. open a pull request with the file and the updated awesome-clips-kitty/index.json and README.md.",
    "CI checks that index.json and README.md match the listings, so a pull request with the file alone fails.",
    "Once merged, Clips Kitty versions with the Marketplace see it after Check for new pipelines.",
]


def git(cwd, *args) -> str:
    return subprocess.run([GIT, *args], cwd=str(cwd), check=True, capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def quiet_git(tmp_path, monkeypatch):
    """Git with no global or system configuration, and a fixed author."""
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "Test Owner")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "test-owner@example.com")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(listing, "_today", lambda: TODAY)


class Repo:
    """A plugin's git repository with a GitHub-looking remote whose pushes go
    to a bare repository beside it. The plugin is at the root, or in `path`."""

    def __init__(self, base: Path, *, path: str = ".", version: str = "1.0.0", repository: str | None = None,
                 plugin_id: str = PLUGIN_ID):
        self.root = base / "work" / NAME
        self.folder = self.root if path == "." else self.root / path
        self.bare = base / "remote.git"
        self.folder.mkdir(parents=True)
        (self.folder / "src").mkdir()
        (self.folder / "src" / "main.py").write_text(MAIN, encoding="utf-8")
        self.write_manifest(version=version, repository=repository, plugin_id=plugin_id)
        (self.root / ".gitignore").write_text(".venv/\n", encoding="utf-8")
        git(base, "init", "-q", "--bare", str(self.bare))
        git(self.root, "init", "-q")
        git(self.root, "checkout", "-q", "-b", "main")
        git(self.root, "remote", "add", "origin", GITHUB)
        git(self.root, "config", "remote.origin.pushurl", str(self.bare))
        self.commit("first")
        git(self.root, "push", "-q", "-u", "origin", "main")

    def write_manifest(self, *, version="1.0.0", repository=None, plugin_id=PLUGIN_ID):
        line = f"repository: {repository}\n" if repository else ""
        (self.folder / "clipskitty.yaml").write_text(
            MANIFEST.format(id=plugin_id, version=version, repository=line), encoding="utf-8")

    def commit(self, message: str = "change") -> str:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)
        return self.head()

    def push(self) -> None:
        git(self.root, "push", "-q", "--tags", "origin", "main")

    def head(self) -> str:
        return git(self.root, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    made = Repo(tmp_path)
    monkeypatch.chdir(made.root.parent)  # listing writes <name>.yaml here, beside the plugin's folder
    return made


def _listing(capsys, *args):
    code = main(["listing", *[str(a) for a in args]])
    out, err = capsys.readouterr()
    return code, out, err


def _errors(err: str) -> list[str]:
    return [line for line in err.splitlines() if line.startswith("error: ")]


# ---- the file ------------------------------------------------------------------------------


def test_listing_writes_the_catalog_format(repo, capsys):
    commit = repo.head()
    code, out, err = _listing(capsys, NAME, "--section", "gaming/generic", "--alias", "word moments", "spoken")
    assert code == 0, err
    written = repo.root.parent / f"{NAME}.yaml"
    # The fields and their order are the ones in awesome-clips-kitty/CONTRIBUTING.md.
    assert written.read_text(encoding="utf-8") == (
        f"id: {PLUGIN_ID}\nrepository: {GITHUB}\npath: .\nsection: gaming/generic\n"
        f"aliases: [word moments, spoken]\nadded: {TODAY}\nversions:\n  - version: 1.0.0\n    commit: {commit}\n")
    assert yaml.safe_load(written.read_text(encoding="utf-8")) == {
        "id": PLUGIN_ID, "repository": GITHUB, "path": ".", "section": "gaming/generic",
        "aliases": ["word moments", "spoken"], "added": datetime.date(2026, 10, 7),
        "versions": [{"version": "1.0.0", "commit": commit}]}
    assert out.splitlines() == [f"wrote {NAME}.yaml: {PLUGIN_ID} 1.0.0 at commit {commit[:7]}", *NEXT]
    assert err.splitlines() == ["note: no tag v1.0.0 at this commit; the listing names the commit only",
                                "note: check that gaming/generic is in awesome-clips-kitty/registry/sections.yaml"]

    # A tag v{version} at the commit is listed; another tag isn't.
    git(repo.root, "tag", "v0.9.0")
    git(repo.root, "tag", "-a", "-m", "the first release", "v1.0.0")
    code, out, err = _listing(capsys, NAME, "--section", "general", "--out", "tagged.yaml")
    assert code == 0, err
    data = yaml.safe_load((repo.root.parent / "tagged.yaml").read_text(encoding="utf-8"))
    assert data["versions"] == [{"version": "1.0.0", "commit": commit, "tag": "v1.0.0"}]
    assert "aliases" not in data and "no tag" not in err


def test_listing_names_the_folder_inside_the_repository(tmp_path, monkeypatch, capsys):
    made = Repo(tmp_path, path="plugins/word-moments")
    monkeypatch.chdir(made.root.parent)
    code, out, err = _listing(capsys, made.folder, "--section", "gaming/generic")
    assert code == 0, err
    data = yaml.safe_load((made.root.parent / f"{NAME}.yaml").read_text(encoding="utf-8"))
    assert data["path"] == "plugins/word-moments" and data["repository"] == GITHUB


def test_listing_checks_its_arguments_and_where_it_writes(repo, capsys, monkeypatch):
    for args, message in [
        ((), listing.NO_SECTION),
        (("--section", "Gaming/Generic"), listing.BAD_SECTION),
        (("--section", "gaming/generic", "--alias", "x" * 41), listing.BAD_ALIASES),
        (("--section", "gaming/generic", "--alias", *[f"word{i}" for i in range(11)]), listing.BAD_ALIASES),
    ]:
        code, out, err = _listing(capsys, NAME, *args)
        assert (code, _errors(err)) == (2, [f"error: {message}"]), args
    assert not (repo.root.parent / f"{NAME}.yaml").exists()

    # Never inside a plugin's folder, which Clips Kitty copies on install. The
    # example is outside it from its src folder too, so following it works.
    inside = ("error: that is inside a plugin's folder, and Clips Kitty copies everything there on install. Write "
              "the listing somewhere else, for example: --out {}")
    monkeypatch.chdir(repo.folder / "src")
    example = os.path.join("..", "..", f"{NAME}.yaml")
    for extra in ([], ["--out", os.path.join("..", f"{NAME}.yaml")]):
        code, out, err = _listing(capsys, "..", "--section", "gaming/generic", *extra)
        assert (code, _errors(err)) == (2, [inside.format(example)]), extra
    monkeypatch.chdir(repo.folder)
    code, out, err = _listing(capsys, ".", "--section", "gaming/generic")
    assert (code, _errors(err)) == (2, [inside.format(os.path.join("..", f"{NAME}.yaml"))])
    # Never over another file: a listing that exists gets a version with --to.
    (repo.root.parent / "taken.yaml").write_text("keep\n", encoding="utf-8")
    code, out, err = _listing(capsys, ".", "--section", "gaming/generic", "--out", "../taken.yaml")
    assert (code, _errors(err)) == (2, [(f"error: {Path('../taken.yaml')} already exists. To add this version to "
                                         f"that listing, pass --to {Path('../taken.yaml')}")])
    assert (repo.root.parent / "taken.yaml").read_text(encoding="utf-8") == "keep\n"
    assert git(repo.root, "status", "--porcelain") == ""


# ---- the repository ------------------------------------------------------------------------


def test_listing_refuses_uncommitted_changes(tmp_path, monkeypatch, capsys):
    made = Repo(tmp_path, path="plugins/word-moments")
    monkeypatch.chdir(made.root.parent)
    refused = [("error: the folder has changes that aren't committed. Commit them, so the listing points at what "
                "you tested")]
    main_py = made.folder / "src" / "main.py"
    main_py.write_text(MAIN + "\n# changed\n", encoding="utf-8")  # a change to a committed file
    code, out, err = _listing(capsys, made.folder, "--section", "gaming/generic")
    assert (code, _errors(err), out) == (2, refused, "")
    main_py.write_text(MAIN, encoding="utf-8")
    (made.folder / "src" / "helpers.py").write_text("X = 1\n", encoding="utf-8")  # a new file
    code, out, err = _listing(capsys, made.folder, "--section", "gaming/generic")
    assert (code, _errors(err)) == (2, refused)
    assert not (made.root.parent / f"{NAME}.yaml").exists()

    # Ignored files and changes elsewhere in the repository don't count.
    (made.folder / "src" / "helpers.py").unlink()
    (made.folder / ".venv").mkdir()
    (made.folder / ".venv" / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    (made.root / "NOTES.md").write_text("elsewhere\n", encoding="utf-8")
    code, out, err = _listing(capsys, made.folder, "--section", "gaming/generic")
    assert code == 0, err


def test_listing_refuses_an_unpushed_commit(repo, capsys):
    repo.write_manifest(version="1.1.0")
    commit = repo.commit("1.1.0")
    code, out, err = _listing(capsys, NAME, "--section", "gaming/generic")
    assert (code, _errors(err)) == (2, [(f"error: commit {commit[:7]} isn't on a pushed branch yet. Push it, then "
                                         "run this again")])
    git(repo.root, "push", "-q")
    code, out, err = _listing(capsys, NAME, "--section", "gaming/generic")
    assert code == 0, err
    assert f"commit: {commit}" in (repo.root.parent / f"{NAME}.yaml").read_text(encoding="utf-8")


def test_listing_checks_the_remote_and_the_publisher(repo, capsys):
    def refused():
        code, out, err = _listing(capsys, NAME, "--section", "gaming/generic", "--out", "out.yaml")
        assert not (repo.root.parent / "out.yaml").exists()
        return code, _errors(err)

    def listed():
        code, out, err = _listing(capsys, NAME, "--section", "gaming/generic", "--out", "out.yaml")
        assert code == 0, err
        written = repo.root.parent / "out.yaml"
        data = yaml.safe_load(written.read_text(encoding="utf-8"))
        written.unlink()
        return data

    # The manifest's repository must be the remote's, in any of the ways it can be written.
    repo.write_manifest(repository="https://github.com/test-owner/other-plugin")
    repo.commit()
    repo.push()
    assert refused() == (2, [("error: repository in clipskitty.yaml is https://github.com/test-owner/other-plugin, "
                              f"but this folder's git remote is {GITHUB}")])
    repo.write_manifest(repository="https://github.com/Test-Owner/Word-Moments")
    repo.commit()
    repo.push()
    assert listed()["repository"] == GITHUB

    # A remote written the ssh way is the same repository.
    for address in (f"git@github.com:{OWNER}/{NAME}.git", f"ssh://git@github.com/{OWNER}/{NAME}.git",
                    f"{GITHUB}.git/"):
        git(repo.root, "remote", "set-url", "origin", address)
        assert listed()["repository"] == GITHUB, address

    # A remote that isn't on GitHub is named without its user name or password.
    git(repo.root, "remote", "set-url", "origin", "https://someone:hunter2@git.example.org/test-owner/word-moments.git")
    repo.write_manifest()
    repo.commit()
    repo.push()
    code, errors = refused()
    assert (code, errors) == (2, [("error: this folder's git remote is https://git.example.org/test-owner/word-moments"
                                   ".git, which isn't a GitHub repository: a listing's repository must be "
                                   "https://github.com/<owner>/<repo>")])
    assert "hunter2" not in "".join(errors) and "someone" not in "".join(errors)

    # The id's publisher must own the repository, as the registry checks.
    git(repo.root, "remote", "set-url", "origin", "https://github.com/someone-else/word-moments")
    assert refused() == (2, [("error: the id's publisher is test-owner, but the repository's owner is someone-else: "
                              "they must be the same")])
    # ... except in the Clips Kitty project's own repositories, which list its examples.
    git(repo.root, "remote", "set-url", "origin", "https://github.com/ColinGPT9/clips-studio")
    assert listed()["repository"] == "https://github.com/ColinGPT9/clips-studio"

    # The remote is the one the branch tracks, else origin.
    git(repo.root, "remote", "set-url", "origin", GITHUB)
    git(repo.root, "remote", "add", "elsewhere", "https://github.com/someone-else/word-moments")
    assert listed()["repository"] == GITHUB


def test_listing_outside_a_repository_says_so(tmp_path, monkeypatch, capsys):
    folder = tmp_path / NAME
    (folder / "src").mkdir(parents=True)
    (folder / "src" / "main.py").write_text(MAIN, encoding="utf-8")
    (folder / "clipskitty.yaml").write_text(MANIFEST.format(id=PLUGIN_ID, version="1.0.0", repository=""),
                                            encoding="utf-8")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))  # don't find a repository above tmp_path
    monkeypatch.chdir(tmp_path)
    code, out, err = _listing(capsys, NAME, "--section", "gaming/generic")
    assert (code, _errors(err)) == (2, [(f"error: {folder.resolve()} isn't in a git repository: a listing names a "
                                         "commit of your plugin's GitHub repository. Put the folder in one, push it "
                                         "to GitHub, then run this again")])


def test_listing_shows_gits_own_words_when_the_repository_cant_be_read(repo, tmp_path, monkeypatch):
    """A repository git refuses to read (owned by another user, as on a
    shared or external drive) isn't called "not a git repository": git's
    first line, and the command it suggests, are shown."""
    real = listing._git
    said = {}

    def git_saying(git_path, folder, *args):
        if args == ("rev-parse", "--show-prefix") and said:
            return subprocess.CompletedProcess([git_path, *args], 128, "", said["stderr"])
        return real(git_path, folder, *args)

    monkeypatch.setattr(listing, "_git", git_saying)
    manifest = {"id": PLUGIN_ID, "version": "1.0.0"}
    said["stderr"] = (f"fatal: detected dubious ownership in repository at '{repo.root}'\n"
                      "To add an exception for this directory, call:\n\n"
                      f"\tgit config --global --add safe.directory {repo.root}\n")
    with pytest.raises(listing.ListingRefused) as refused:
        listing.place(repo.folder, manifest)
    assert str(refused.value) == (f"git rev-parse --show-prefix failed: fatal: detected dubious ownership in "
                                  f"repository at '{repo.root}'. Git says to run: git config --global --add "
                                  f"safe.directory {repo.root}")
    # Git in another language: no .git at or above the folder still means no repository.
    outside = tmp_path / "outside"
    outside.mkdir()
    if any((where / ".git").exists() for where in (outside.resolve(), *outside.resolve().parents)):
        pytest.skip("the test's folder is inside a git repository")
    said["stderr"] = "fatal: Kein Git-Repository (oder irgendeines der Elternverzeichnisse): .git\n"
    with pytest.raises(listing.ListingRefused) as refused:
        listing.place(outside, manifest)
    assert str(refused.value) == listing.NOT_A_REPOSITORY.format(folder=outside)


# ---- placeholders --------------------------------------------------------------------------


def test_listing_refuses_template_placeholders(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for template in scaffold.TEMPLATES:
        for publisher in (scaffold.DEFAULT_PUBLISHER, OWNER):
            folder = tmp_path / publisher / template
            scaffold.make(folder, template, publisher=publisher)
            code, out, err = _listing(capsys, folder, "--section", "gaming/generic", "--out",
                                      tmp_path / f"{template}.yaml")
            errors = _errors(err)
            assert code == 2 and out == "" and errors, (template, err)
            assert all(e.startswith("error: the plugin still has the template's placeholder ")
                       and ": a catalog listing is for your real plugin (" in e for e in errors), errors
            assert (f"error: the plugin still has the template's placeholder {listing.MADE_UP_LINE}: a catalog "
                    "listing is for your real plugin (README.md line 5)") in errors, (template, errors)
            assert any(f"placeholder {scaffold.DEFAULT_PUBLISHER}:" in e for e in errors) == (
                publisher == scaffold.DEFAULT_PUBLISHER), (template, errors)
            if template == "game-events":
                assert ("error: the plugin still has the template's placeholder quark_burst: a catalog listing is "
                        "for your real plugin (clipskitty.yaml line 24)") in errors
                assert any("placeholder quarkbloom-arena:" in e for e in errors)
    assert not list(tmp_path.glob("*.yaml"))

    # Each placeholder, anywhere in the manifest's values or the README; not in a comment.
    folder = tmp_path / "own"
    (folder / "src").mkdir(parents=True)
    (folder / "src" / "main.py").write_text(MAIN, encoding="utf-8")
    manifest = yaml.safe_load(MANIFEST.format(id=PLUGIN_ID, version="1.0.0", repository=""))
    (folder / "clipskitty.yaml").write_text("# was made for quark_burst once\n" + MANIFEST.format(
        id=PLUGIN_ID, version="1.0.0", repository=""), encoding="utf-8")
    assert listing.placeholders(folder, manifest) == []
    readme = folder / "README.md"
    for word in listing.PLACEHOLDERS:
        readme.unlink(missing_ok=True)
        found = listing.placeholders(folder, {**manifest, "tags": [word.upper()]})
        assert found == [f"{listing.PLACEHOLDER.format(word=word)} (clipskitty.yaml, tags[0])"], word
        readme.write_text(f"# Word Moments\n\nFor {word}.\n", encoding="utf-8")
        assert listing.placeholders(folder, manifest) == [(f"{listing.PLACEHOLDER.format(word=word)} (README.md "
                                                           "line 3)")], word
    readme.write_text("Set up for Quarkbloom Arena (a made-up\ngame): replace it.\n", encoding="utf-8")
    assert listing.placeholders(folder, manifest) == [(f"{listing.PLACEHOLDER.format(word=listing.MADE_UP_LINE)} "
                                                       "(README.md line 1)")]
    # Only whole names: a longer name that holds one is the developer's own.
    readme.write_text("For quarkbloom-arena-2 and my_events.\n", encoding="utf-8")
    assert listing.placeholders(folder, manifest) == []


# ---- adding a version ----------------------------------------------------------------------


def test_listing_to_appends_a_version_and_refuses_a_duplicate(repo, capsys):
    first = repo.head()
    assert _listing(capsys, NAME, "--section", "gaming/generic", "--alias", "word moments")[0] == 0
    path = repo.root.parent / f"{NAME}.yaml"
    before = path.read_text(encoding="utf-8")

    repo.write_manifest(version="1.1.0")
    second = repo.commit("1.1.0")
    git(repo.root, "tag", "v1.1.0")
    repo.push()
    code, out, err = _listing(capsys, NAME, "--to", path)
    assert code == 0, err
    after = path.read_text(encoding="utf-8")
    assert after == before + f"  - version: 1.1.0\n    commit: {second}\n    tag: v1.1.0\n"
    assert yaml.safe_load(after)["versions"] == [{"version": "1.0.0", "commit": first},
                                                 {"version": "1.1.0", "commit": second, "tag": "v1.1.0"}]
    assert out.splitlines()[0] == f"wrote {path}: {PLUGIN_ID} 1.1.0 at commit {second[:7]}"
    assert out.splitlines()[2] == f"  1. put this file in place of awesome-clips-kitty/registry/pipelines/{PLUGIN_ID}.yaml"
    assert "check that" not in err  # the section is the listing's own

    # A listed version never changes.
    code, out, err = _listing(capsys, NAME, "--to", path)
    assert (code, _errors(err)) == (2, ["error: version 1.1.0 is already listed; a listed version never changes"])
    assert path.read_text(encoding="utf-8") == after
    # --to only adds a version.
    code, out, err = _listing(capsys, NAME, "--to", path, "--section", "general")
    assert (code, _errors(err)) == (2, [(f"error: --to only adds a version: change the section or aliases in {path} "
                                         "by hand")])


def test_listing_to_keeps_the_rest_of_a_hand_written_listing(repo, capsys):
    repo.write_manifest(version="1.2.0")
    third = repo.commit("1.2.0")
    repo.push()
    catalog = repo.root.parent / "catalog" / "awesome-clips-kitty" / "registry" / "pipelines" / OWNER
    catalog.mkdir(parents=True)
    path = catalog / f"{NAME}.yaml"
    old = "0123456789abcdef0123456789abcdef01234567"
    text = (f"# Word Moments\r\nid: {PLUGIN_ID}\r\nrepository: {GITHUB}\r\nsection: general\r\n"
            "versions:\r\n- version: 1.0.0   # the first\r\n  commit: " + old + "\r\n  date: 2026-09-01\r\n"
            "\r\n# checked by hand\r\nchecked: 2026-09-02\r\n")
    path.write_bytes(text.encode("utf-8"))
    code, out, err = _listing(capsys, NAME, "--to", path)
    assert code == 0, err
    after = path.read_bytes().decode("utf-8")
    assert after == text.replace("  date: 2026-09-01\r\n", f"  date: 2026-09-01\r\n- version: 1.2.0\r\n  commit: "
                                                         f"{third}\r\n")
    assert out.splitlines()[2] == f"  1. the file is already awesome-clips-kitty/registry/pipelines/{PLUGIN_ID}.yaml"

    # Not another plugin's listing, nor one whose code is elsewhere.
    other = repo.root.parent / "other.yaml"
    other.write_text(f"id: {OWNER}/other\nrepository: {GITHUB}\nversions:\n  - version: 1.0.0\n    commit: {old}\n",
                     encoding="utf-8")
    code, out, err = _listing(capsys, NAME, "--to", other)
    assert (code, _errors(err)) == (2, [f"error: {other} is the listing of {OWNER}/other, not {PLUGIN_ID}"])
    other.write_text(f"id: {PLUGIN_ID}\nrepository: {GITHUB}\npath: plugins/word-moments\nversions:\n"
                     f"  - version: 1.0.0\n    commit: {old}\n", encoding="utf-8")
    code, out, err = _listing(capsys, NAME, "--to", other)
    assert (code, _errors(err)) == (2, [(f"error: {other} lists {PLUGIN_ID} from {GITHUB}, folder plugins/word-moments"
                                         f", but this folder is {GITHUB}: every version of a listing comes from the "
                                         "same repository and folder")])
    # A list it can't add to without rewriting the file is left for the developer.
    other.write_text(f"id: {PLUGIN_ID}\nrepository: {GITHUB}\nversions: [{{version: 1.0.0, commit: '{old}'}}]\n",
                     encoding="utf-8")
    code, out, err = _listing(capsys, NAME, "--to", other)
    assert code == 2 and f"error: couldn't add the version to {other} without rewriting it" in err
    assert f"  - version: 1.2.0\n    commit: {third}" in err


# ---- read only -----------------------------------------------------------------------------


def _snapshot(folder: Path) -> dict:
    """Every file under `folder`, with its size, modification time and content."""
    out = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            info = path.stat()
            out[path.relative_to(folder).as_posix()] = (info.st_size, info.st_mtime_ns,
                                                        hashlib.sha256(path.read_bytes()).hexdigest())
    return out


def test_listing_runs_no_git_command_that_writes(repo, capsys, monkeypatch):
    ran = []
    real = listing._git

    def recording(git, folder, *args):
        ran.append(args)
        return real(git, folder, *args)

    monkeypatch.setattr(listing, "_git", recording)
    git(repo.root, "tag", "v1.0.0")
    # A file whose time changed but whose content didn't: a plain `git status` would refresh the index.
    later = (repo.folder / "src" / "main.py").stat().st_mtime + 5
    os.utime(repo.folder / "src" / "main.py", (later, later))
    before = (_snapshot(repo.root / ".git"), _snapshot(repo.bare))
    assert _listing(capsys, NAME, "--section", "gaming/generic")[0] == 0
    (repo.root / "untracked.txt").write_text("x\n", encoding="utf-8")  # a refused run reads too
    assert _listing(capsys, NAME, "--section", "gaming/generic", "--out", "again.yaml")[0] == 2
    assert (_snapshot(repo.root / ".git"), _snapshot(repo.bare)) == before

    assert ran and all(listing.read_only(args) for args in ran)
    assert {args[0] for args in ran} <= {"rev-parse", "status", "remote", "for-each-ref"}
    assert all(args[:2] == ("remote", "get-url") or args == ("remote",) for args in ran if args[0] == "remote")
    for args in (("push",), ("fetch",), ("pull",), ("ls-remote", "origin"), ("commit",), ("tag", "v1"),
                 ("remote", "add", "x", "y"), ("remote", "set-url", "origin", "y"), ("remote", "update"),
                 ("config", "user.name", "x"), ("gc",), ("checkout", "main"), ()):
        assert not listing.read_only(args), args
        with pytest.raises(ValueError):
            real(GIT, repo.root, *args)

    # listing.py starts processes only through _git, and opens no network connection.
    tree = ast.parse(inspect.getsource(listing))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            assert not any(n.split(".")[0] in ("urllib", "http", "socket", "ssl", "requests") for n in names)
    callers = [f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
               and any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "subprocess"
                       and n.attr in ("run", "Popen", "call", "check_call", "check_output") for n in ast.walk(f))]
    assert callers == ["_git"]


def test_listing_says_to_rebuild_the_index(repo, capsys):
    code, out, err = _listing(capsys, NAME, "--section", "gaming/generic")
    assert code == 0, err
    text = "\n".join(out.splitlines()[1:])
    assert text == "\n".join(NEXT)
    for name in ("build_registry_index.py", "index.json", "README.md"):
        assert name in text

    docs = (ROOT / "docs" / "developers" / "marketplace-publishing.md").read_text(encoding="utf-8")
    assert "adding one file" not in docs
    for words in ("python -m clipskitty_sdk listing", "python scripts/build_registry_index.py",
                  "the updated `awesome-clips-kitty/index.json` and `README.md`", "with the file alone fails",
                  "merged with the rebuilt `index.json`"):
        assert words in docs, words
    contributing = (ROOT / "awesome-clips-kitty" / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "Open a pull request adding" not in contributing
    assert "python scripts/build_registry_index.py" in contributing and "clipskitty_sdk listing" in contributing
