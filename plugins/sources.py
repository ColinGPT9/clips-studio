"""A plugin's files, fetched into a folder of Clips Kitty's own. Nothing from
the plugin runs here: files are copied or written, never executed.

Two kinds of source:

    {"kind": "folder", "path": "C:/Users/me/my-plugin"}
        a folder on this PC, copied (a developer's own checkout, or a download)

    {"kind": "git", "url": "https://github.com/example-dev/example-plugin",
     "commit": "<the full 40-character commit hash>"}
        one commit of a Git repository

A Git source is fetched with `git` when it is installed: the one commit is
fetched into an empty bare repository and its files are written out from Git's
object store, so nothing is checked out. That means no hooks, no filters (Git
LFS included), no submodules and no symbolic links, and every file is the one
the commit hash names, because Git checks objects against their hashes. With no
`git` on the PC, a GitHub repository is downloaded as GitHub's archive of that
commit instead and unpacked here; that archive is GitHub's word for what the
commit holds, not checked against its hash.

Either way a symbolic link, a path that would land outside the folder, a `.git`
entry or two names that differ only in case (one file on Windows) refuse the
whole plugin, and so does a plugin over MAX_FILES files or MAX_BYTES bytes.
Not tested on Windows.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

MAX_FILES = 5000
MAX_BYTES = 1024 * 1024 * 1024  # 1 GB: a plugin may ship its own program; models come separately
GIT_TIMEOUT = 300
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
GIT_URL_RE = re.compile(r"^(https://[^\s/?#]+/[^\s?#]+|file:///?[^\s?#]+)$")
GITHUB_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"
SKIPPED_DIRS = {".git", "__pycache__"}
# Names Windows cannot create, whatever the extension.
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)


class SourceError(ValueError):
    """A source that can't be fetched, or files Clips Kitty won't install. The message is for the user."""


# ---- what a source is --------------------------------------------------------------


def clean_source(spec) -> dict:
    """A source as a caller gave it, checked for shape. Raises SourceError."""
    if not isinstance(spec, dict):
        raise SourceError('a source is an object: {"kind": "folder", "path": ...} or '
                          '{"kind": "git", "url": ..., "commit": ...}')
    kind = spec.get("kind")
    if kind == "folder":
        path = spec.get("path")
        if not isinstance(path, str) or not path.strip():
            raise SourceError("a folder source needs the folder's path")
        return {"kind": "folder", "path": str(Path(path.strip()).expanduser())}
    if kind == "git":
        url, commit = spec.get("url"), spec.get("commit")
        if not isinstance(url, str) or not GIT_URL_RE.match(url.strip()):
            raise SourceError("a Git source needs the repository's https:// address (or file:// for one on this PC)")
        if not isinstance(commit, str) or not COMMIT_RE.match(commit.strip().lower()):
            raise SourceError("a Git source needs the full 40-character commit hash: a branch or tag can change "
                              "after you looked at it, a commit can't")
        return {"kind": "git", "url": url.strip().rstrip("/"), "commit": commit.strip().lower()}
    raise SourceError(f"unknown kind of source {kind!r}; expected folder or git")


def describe(source: dict) -> str:
    """A source in a line, as the install screen shows it."""
    if source.get("kind") == "git":
        place = re.sub(r"^https://", "", source["url"]).removesuffix(".git")
        return f"{place} · commit {source['commit'][:7]}"
    if source.get("kind") == "folder":
        return f"the folder {source['path']}"
    if source.get("kind") == "index":
        return f"{source.get('index', 'a registry index')}"
    return "unknown source"


# ---- checks shared by every source -------------------------------------------------


def _safe_relative(name: str) -> str:
    """A path from a plugin, as a relative POSIX path, or SourceError."""
    if "\\" in name or ":" in name or "\0" in name:
        raise SourceError(f"{name!r}: a file name with \\ or : can't be installed on Windows")
    p = PurePosixPath(name)
    if p.is_absolute() or not p.parts or any(part in ("..", ".") for part in p.parts):
        raise SourceError(f"{name!r}: a path outside the plugin's folder")
    for part in p.parts:
        if part.lower() == ".git":
            raise SourceError(f"{name!r}: a plugin can't contain a .git entry")
        if _WINDOWS_RESERVED.match(part) or part.endswith((" ", ".")):
            raise SourceError(f"{name!r}: Windows can't create a file with that name")
    return p.as_posix()


class _Budget:
    """Counts files and bytes against the limits, and names colliding paths."""

    def __init__(self):
        self.files = 0
        self.bytes = 0
        self.seen: dict[str, str] = {}

    def add(self, name: str, size: int) -> None:
        self.files += 1
        self.bytes += max(0, size)
        if self.files > MAX_FILES:
            raise SourceError(f"the plugin has more than {MAX_FILES} files")
        if self.bytes > MAX_BYTES:
            raise SourceError(f"the plugin is larger than {MAX_BYTES // (1024 * 1024)} MB")
        folded = name.lower()
        if folded in self.seen and self.seen[folded] != name:
            raise SourceError(f"{self.seen[folded]!r} and {name!r} differ only in case, which is one file on Windows")
        self.seen[folded] = name


def _write(dest: Path, rel: str, data: bytes, executable: bool, warnings: list) -> None:
    target = dest / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "xb") as f:  # "x": never through something already there
        f.write(data)
    if executable and os.name != "nt":
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if data.startswith(LFS_POINTER):
        warnings.append(f"{rel} is stored with Git LFS, which Clips Kitty does not fetch: "
                        "the plugin gets a small pointer file instead of the real one")


# ---- a folder ------------------------------------------------------------------------


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def copy_folder(folder: Path, dest: Path) -> list[str]:
    """Copy a plugin folder into `dest` (which must not exist). `.git` and
    `__pycache__` folders are left out; a symbolic link anywhere refuses it.
    Returns warnings."""
    folder = Path(folder)
    if not folder.is_dir():
        raise SourceError(f"{folder} is not a folder")
    if _is_link(folder):
        raise SourceError(f"{folder} is a symbolic link; give the folder it points to")
    budget, warnings, files = _Budget(), [], []
    for here, dirs, names in os.walk(folder, followlinks=False):
        base = Path(here)
        for d in list(dirs):
            if _is_link(base / d):
                raise SourceError(f"{(base / d).relative_to(folder).as_posix()}: symbolic links are not allowed "
                                  "in a plugin")
            if d in SKIPPED_DIRS:
                dirs.remove(d)
        for name in names:
            path = base / name
            rel = path.relative_to(folder).as_posix()
            if _is_link(path):
                raise SourceError(f"{rel}: symbolic links are not allowed in a plugin")
            if not path.is_file():
                raise SourceError(f"{rel}: not a regular file")
            if name.endswith(".pyc"):
                continue
            budget.add(_safe_relative(rel), path.stat().st_size)
            files.append((rel, path))
    dest.mkdir(parents=True)
    for rel, path in files:
        _write(dest, rel, path.read_bytes(), os.access(path, os.X_OK) and os.name != "nt", warnings)
    return warnings


# ---- Git ------------------------------------------------------------------------------


def find_git() -> str | None:
    return shutil.which("git")


def _git_env() -> dict:
    env = dict(os.environ)
    # Never ask for a password: a public plugin needs none, and a prompt would
    # hang the engine where nobody can see it.
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_ASKPASS="", SSH_ASKPASS="")
    return env


def _git(git: str, args: list[str], *, cwd: Path, hooks: Path, input: bytes | None = None) -> bytes:
    command = [
        git,
        "-c", f"core.hooksPath={hooks}",   # an empty folder: no hook runs
        "-c", "core.fsmonitor=false",
        "-c", "core.symlinks=false",
        "-c", "credential.helper=",
        "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always",
        "-c", "protocol.file.allow=always",
        "-c", "transfer.fsckObjects=true",
        "-c", "submodule.recurse=false",
        *args,
    ]
    try:
        done = subprocess.run(command, cwd=cwd, input=input, capture_output=True, timeout=GIT_TIMEOUT,
                              env=_git_env(), check=False)
    except subprocess.TimeoutExpired as e:
        raise SourceError(f"Git took longer than {GIT_TIMEOUT // 60} minutes") from e
    except OSError as e:
        raise SourceError(f"Git could not be started ({e})") from e
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise SourceError("Git: " + (detail[-1] if detail else f"failed with code {done.returncode}"))
    return done.stdout


def _read_batch(raw: bytes, count: int) -> list[bytes]:
    """The contents out of `git cat-file --batch` output."""
    out, i = [], 0
    for _ in range(count):
        end = raw.index(b"\n", i)
        header = raw[i:end].split(b" ")
        if len(header) != 3 or header[1] != b"blob":
            raise SourceError("Git returned something other than a file")
        size = int(header[2])
        start = end + 1
        out.append(raw[start:start + size])
        i = start + size + 1
    return out


def fetch_git(url: str, commit: str, dest: Path, *, git: str) -> list[str]:
    """Write the files of one commit into `dest` (which must not exist).
    Returns warnings."""
    with tempfile.TemporaryDirectory(prefix="clipskitty-git-") as tmp:
        tmp = Path(tmp)
        hooks = tmp / "no-hooks"
        hooks.mkdir()
        repo = tmp / "repo.git"
        _git(git, ["init", "--bare", "--quiet", str(repo)], cwd=tmp, hooks=hooks)
        try:
            _git(git, ["fetch", "--quiet", "--depth", "1", "--no-tags", "--no-recurse-submodules", url, commit],
                 cwd=repo, hooks=hooks)
        except SourceError:
            # A server that won't hand out a commit by its hash: take its
            # branches and tags, then look for the commit among them.
            _git(git, ["fetch", "--quiet", "--no-tags", "--no-recurse-submodules", url,
                       "+refs/heads/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*"], cwd=repo, hooks=hooks)
        try:
            kind = _git(git, ["cat-file", "-t", commit], cwd=repo, hooks=hooks).strip()
        except SourceError as e:
            raise SourceError(f"the repository has no commit {commit[:7]}") from e
        if kind != b"commit":
            raise SourceError(f"{commit[:7]} is not a commit")
        listing = _git(git, ["ls-tree", "-r", "-z", "--long", "--full-tree", commit], cwd=repo, hooks=hooks)
        budget, entries = _Budget(), []
        for line in filter(None, listing.split(b"\0")):
            meta, _, name_raw = line.partition(b"\t")
            mode, kind, sha, size = meta.split()
            name = name_raw.decode("utf-8", "surrogateescape")
            if mode == b"120000":
                raise SourceError(f"{name}: symbolic links are not allowed in a plugin")
            if mode == b"160000" or kind == b"commit":
                raise SourceError(f"{name}: the plugin uses a Git submodule, which Clips Kitty does not fetch")
            if kind != b"blob":
                continue
            rel = _safe_relative(name)
            if PurePosixPath(rel).name.endswith(".pyc") or "__pycache__" in PurePosixPath(rel).parts:
                continue
            budget.add(rel, int(size))
            entries.append((rel, sha.decode(), mode == b"100755"))
        raw = _git(git, ["cat-file", "--batch"], cwd=repo, hooks=hooks,
                   input=b"".join(sha.encode() + b"\n" for _, sha, _ in entries))
        contents = _read_batch(raw, len(entries))
    warnings: list[str] = []
    dest.mkdir(parents=True)
    for (rel, _sha, executable), data in zip(entries, contents):
        _write(dest, rel, data, executable, warnings)
    return warnings


# ---- GitHub's archive of a commit ---------------------------------------------------


def github_archive_url(url: str, commit: str) -> str | None:
    m = GITHUB_RE.match(url)
    if not m:
        return None
    return f"https://github.com/{m.group(1)}/{m.group(2)}/archive/{commit}.tar.gz"


def download(url: str, path: Path, *, limit: int = MAX_BYTES) -> None:
    """Fetch a URL to a file over HTTPS with the standard library."""
    import urllib.request

    if not url.startswith("https://"):
        raise SourceError("only https:// downloads")
    request = urllib.request.Request(url, headers={"User-Agent": "Clips-Kitty-plugin-manager"})
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(path, "wb") as out:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise SourceError(f"the download is larger than {limit // (1024 * 1024)} MB")
                out.write(chunk)
    except OSError as e:
        raise SourceError(f"the download failed ({e})") from e


def unpack_archive(archive: Path, commit: str, dest: Path) -> list[str]:
    """Unpack GitHub's .tar.gz of one commit into `dest` (which must not exist),
    without the archive's top folder. Returns warnings."""
    budget, entries, warnings = _Budget(), [], []
    try:
        with tarfile.open(archive, "r:gz") as tar:
            stamped = (tar.pax_headers or {}).get("comment")
            if stamped and stamped.strip() != commit:
                raise SourceError(f"the archive is of commit {stamped.strip()[:7]}, not {commit[:7]}")
            top = None
            for member in tar:
                parts = PurePosixPath(member.name).parts
                if not parts:
                    continue
                top = top or parts[0]
                if parts[0] != top:
                    raise SourceError("the archive is not one repository folder")
                if len(parts) == 1 and member.isdir():
                    continue
                rel = _safe_relative("/".join(parts[1:]))
                if member.isdir():
                    continue
                if member.issym() or member.islnk():
                    raise SourceError(f"{rel}: symbolic links are not allowed in a plugin")
                if not member.isfile():
                    raise SourceError(f"{rel}: not a regular file")
                if rel.endswith(".pyc") or "__pycache__" in PurePosixPath(rel).parts:
                    continue
                budget.add(rel, member.size)
                handle = tar.extractfile(member)
                entries.append((rel, handle.read() if handle else b"", bool(member.mode & 0o111)))
    except (tarfile.TarError, OSError, EOFError) as e:
        raise SourceError(f"the archive could not be read ({e})") from e
    dest.mkdir(parents=True)
    for rel, data, executable in entries:
        _write(dest, rel, data, executable, warnings)
    return warnings


# ---- one entry point -------------------------------------------------------------------


def fetch(source: dict, dest: Path, *, git: str | None = None, fetcher=None) -> list[str]:
    """Put a cleaned source's files in `dest` (which must not exist). Returns
    warnings for the install screen. Raises SourceError.

    `git` is the git program (found on PATH when not given); `fetcher(url, path)`
    downloads a URL to a file (download() when not given), for GitHub's archive
    when there is no git.
    """
    dest = Path(dest)
    try:
        if source["kind"] == "folder":
            return copy_folder(Path(source["path"]), dest)
        if source["kind"] == "git":
            git = git or find_git()
            if git:
                return fetch_git(source["url"], source["commit"], dest, git=git)
            archive_url = github_archive_url(source["url"], source["commit"])
            if not archive_url:
                raise SourceError("installing from this address needs Git, which isn't installed on this PC. "
                                  "Install Git, or install from a folder")
            with tempfile.TemporaryDirectory(prefix="clipskitty-archive-") as tmp:
                path = Path(tmp) / "plugin.tar.gz"
                (fetcher or download)(archive_url, path)
                return unpack_archive(path, source["commit"], dest)
    except SourceError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(dest, ignore_errors=True)
        raise SourceError(f"the plugin's files could not be written ({e})") from e
    raise SourceError(f"unknown kind of source {source.get('kind')!r}")
