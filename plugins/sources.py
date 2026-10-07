"""A plugin's files, fetched into a folder of Clips Kitty's own. Nothing from
the plugin runs here: files are copied or written, never executed.

Two kinds of source:

    {"kind": "folder", "path": "C:/Users/me/my-plugin"}
        a folder on this PC, copied (a developer's own checkout, or a download)

    {"kind": "git", "url": "https://github.com/example-dev/example-plugin",
     "commit": "<the full 40-character commit hash>", "path": "optional/folder"}
        one commit of a Git repository (with `path`, the folder in it that
        holds clipskitty.yaml)

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

A download or Git that fails raises FetchFailed, whose message never carries
the error's own text: that goes to the engine's log. Its `kind` says what went
wrong (OFFLINE, GONE, DISK, DAMAGED or OTHER, see failure()), and the manager
answers with a plain sentence for that kind.
"""

from __future__ import annotations

import errno
import http.client
import logging
import os
import re
import shutil
import socket
import ssl
import stat
import subprocess
import tarfile
import tempfile
import urllib.error
import zlib
from pathlib import Path, PurePosixPath

from plugins._sdk import manifest

log = logging.getLogger(__name__)

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
# Said once on the install screen when fetch() names files it couldn't fetch.
FILES_MISSING = "Some of this pipeline's files couldn't be downloaded, so it may not work. Ask its developer."
TRY_AGAIN = "Try again; if it happens again, send a bug report from Feedback (it includes the details)."
NOT_SAVED = f"its files couldn't be saved on this PC. {TRY_AGAIN}"

# What a failed download or Git fetch was (FetchFailed.kind).
OFFLINE = "offline"   # no connection, a timeout, a dropped connection
GONE = "gone"         # the address, repository or commit isn't there (or is private)
DISK = "disk"         # this PC's disk is full
DAMAGED = "damaged"   # the download arrived, but isn't what it should be
OTHER = "other"       # anything else; the log says what
_DISK_ERRNOS = {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}
_DISK_WINERRORS = {112, 39}  # ERROR_DISK_FULL, ERROR_HANDLE_DISK_FULL
# No route to the server (the other connection errors are ConnectionError or TimeoutError).
_NETWORK_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN}
# A Windows junction (Path.is_junction is Python 3.12+; the app builds with 3.11).
_MOUNT_POINT = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


class SourceError(ValueError):
    """A source that can't be fetched, or files Clips Kitty won't install. The message is for the user."""


class TooLarge(SourceError):
    """A download stopped because it passed its size limit."""


class FetchFailed(SourceError):
    """The network, Git, the disk or a damaged download failed, not the
    plugin. `kind` is OFFLINE, GONE, DISK, DAMAGED or OTHER; the message never
    carries the error's own text (that is in the log)."""

    def __init__(self, message: str, kind: str = OTHER):
        super().__init__(message)
        self.kind = kind


def failure(error: BaseException) -> str:
    """What kind of failure a download error is: GONE for an HTTP 401, 403,
    404 or 410 (GitHub and Hugging Face answer 401 or 403 for a private or
    missing repository), DISK for a full disk, OFFLINE for no connection, a
    timeout or a connection that broke off, else OTHER."""
    if isinstance(error, urllib.error.HTTPError):
        return GONE if error.code in (401, 403, 404, 410) else OTHER
    if isinstance(error, OSError) and (error.errno in _DISK_ERRNOS
                                       or getattr(error, "winerror", None) in _DISK_WINERRORS):
        return DISK
    if isinstance(error, http.client.InvalidURL):
        return OTHER
    if isinstance(error, (urllib.error.URLError, ConnectionError, TimeoutError, http.client.HTTPException,
                          socket.gaierror, socket.herror, ssl.SSLError)):
        return OFFLINE
    if isinstance(error, OSError) and error.errno in _NETWORK_ERRNOS:
        return OFFLINE
    return OTHER


# ---- what a source is --------------------------------------------------------------


def clean_source(spec) -> dict:
    """A source as a caller gave it, checked for shape. Raises SourceError,
    whose message reads after "Couldn't install this pipeline: "."""
    if not isinstance(spec, dict):
        raise SourceError("Clips Kitty wasn't told where its files are. Give a folder on this PC, or a "
                          "repository's https:// address and a commit.")
    kind = spec.get("kind")
    if kind == "folder":
        path = spec.get("path")
        if not isinstance(path, str) or not path.strip():
            raise SourceError("Clips Kitty wasn't told which folder it's in. Choose the folder that holds its "
                              f"{manifest.MANIFEST_FILE}.")
        try:
            folder = Path(path.strip()).expanduser()
        except RuntimeError:  # ~someone, with no such user: copy_folder says there is no such folder
            folder = Path(path.strip())
        return {"kind": "folder", "path": str(folder)}
    if kind == "git":
        url, commit = spec.get("url"), spec.get("commit")
        if not isinstance(url, str) or not GIT_URL_RE.match(url.strip()):
            raise SourceError("its address isn't one Clips Kitty downloads from. Give the repository's https:// "
                              "address (or a file:// address for one on this PC).")
        if not isinstance(commit, str) or not COMMIT_RE.match(commit.strip().lower()):
            raise SourceError("Clips Kitty needs the full 40-character commit, not a branch or tag name: a branch "
                              "or tag can change after you looked at it, a commit can't.")
        out = {"kind": "git", "url": url.strip().rstrip("/"), "commit": commit.strip().lower()}
        path = spec.get("path")
        if path not in (None, "", "."):
            if not isinstance(path, str):
                raise SourceError("its listing or link names its folder in a way Clips Kitty can't read. Its "
                                  "developer needs to correct the listing or link.")
            folder = path.strip().strip("/")
            if folder not in ("", "."):  # "/" is the repository's own top folder
                problem = _name_problem(folder)
                if problem:
                    raise SourceError(_FOLDER_PROBLEMS[problem].format(name=path.strip()))
                out["path"] = PurePosixPath(folder).as_posix()
        return out
    raise SourceError(f"Clips Kitty doesn't know how to install from {kind!r}. It installs from a folder on this "
                      "PC or from a repository's commit.")


def describe(source: dict) -> str:
    """A source in a line, as the install screen shows it."""
    if source.get("kind") == "git":
        place = re.sub(r"^https://", "", source["url"]).removesuffix(".git")
        inside = f" · {source['path']}" if source.get("path") else ""
        return f"{place}{inside} · commit {source['commit'][:7]}"
    if source.get("kind") == "folder":
        return f"the folder {source['path']}"
    return "unknown source"


def plugin_folder(folder: Path) -> Path:
    """The folder to install from: `folder` when clipskitty.yaml is in it,
    else its one subfolder that has clipskitty.yaml (the outer folder Windows
    makes when it unpacks GitHub's "Download ZIP"), else `folder` as it is.
    A folder it can't read is returned as it is: copy_folder says what it
    couldn't read."""
    folder = Path(folder)
    try:
        if not folder.is_dir() or _is_link(folder) or (folder / manifest.MANIFEST_FILE).is_file():
            return folder
        inside = [p for p in folder.iterdir()
                  if p.is_dir() and not _is_link(p) and (p / manifest.MANIFEST_FILE).is_file()]
    except OSError:
        return folder
    return inside[0] if len(inside) == 1 else folder


# ---- checks shared by every source -------------------------------------------------


# Why a path can't be installed (_name_problem), said of a file in the
# plugin, or of the folder a listing or link names (clean_source).
_FILE_PROBLEMS = {
    "characters": ("it has a file named {name!r}, and Windows doesn't allow \\ or : in a file name. "
                   "Its developer needs to rename it."),
    "outside": ("it names a file outside its own folder ({name!r}), which Clips Kitty doesn't install. "
                "Its developer needs to fix it."),
    "git": ("it contains {name!r}, part of a .git folder of version history, which Clips Kitty doesn't install. "
            "Its developer needs to remove it."),
    "windows": "it has a file named {name!r}, which Windows can't create. Its developer needs to rename it.",
}
_FOLDER_PROBLEMS = {
    "characters": ("its listing or link names a folder ({name!r}) with \\ or : in its name, which Windows doesn't "
                   "allow. Its developer needs to correct the listing or link."),
    "outside": ("its listing or link names a folder ({name!r}) outside its own files. Its developer needs to "
                "correct the listing or link."),
    "git": ("its listing or link names a folder ({name!r}) inside a .git folder of version history, which Clips "
            "Kitty doesn't install. Its developer needs to correct the listing or link."),
    "windows": ("its listing or link names a folder ({name!r}) that Windows can't create. Its developer needs to "
                "correct the listing or link."),
}


def _name_problem(name: str) -> str | None:
    """Why a relative path from a plugin can't be installed (a key of
    _FILE_PROBLEMS), or None."""
    if "\\" in name or ":" in name or "\0" in name:
        return "characters"
    p = PurePosixPath(name)
    if p.is_absolute() or not p.parts or any(part in ("..", ".") for part in p.parts):
        return "outside"
    for part in p.parts:
        if part.lower() == ".git":
            return "git"
        if _WINDOWS_RESERVED.match(part) or part.endswith((" ", ".")):
            return "windows"
    return None


def _safe_relative(name: str) -> str:
    """A path from a plugin, as a relative POSIX path, or SourceError."""
    problem = _name_problem(name)
    if problem:
        raise SourceError(_FILE_PROBLEMS[problem].format(name=name))
    return PurePosixPath(name).as_posix()


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
            raise SourceError(f"it has more than {MAX_FILES} files, more than Clips Kitty installs for one "
                              "pipeline. Its developer needs to make it smaller.")
        if self.bytes > MAX_BYTES:
            raise SourceError(f"it's larger than {MAX_BYTES // (1024 * 1024)} MB, more than Clips Kitty installs "
                              "for one pipeline. Its developer needs to make it smaller.")
        folded = name.lower()
        if folded in self.seen and self.seen[folded] != name:
            raise SourceError(f"it has two files whose names differ only in capital letters ({self.seen[folded]!r} "
                              f"and {name!r}), and Windows treats them as one file. Its developer needs to "
                              "rename one.")
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
                        "the pipeline gets a small pointer file instead of the real one")


# ---- a folder ------------------------------------------------------------------------


def _is_link(path: Path) -> bool:
    """A symbolic link or a Windows junction. Path.is_junction is Python
    3.12+, so a junction is read from its reparse tag (st_reparse_tag is only
    on Windows). Other reparse points, such as OneDrive's placeholders, are
    ordinary files and folders."""
    try:
        return path.is_symlink() or getattr(os.lstat(path), "st_reparse_tag", 0) == _MOUNT_POINT
    except OSError:
        return False  # the reads that follow fail, and say so


def _shortcut(rel: str, what: str = "file") -> SourceError:
    return SourceError(f"it contains a shortcut ({rel}), which Clips Kitty doesn't install. Its developer needs to "
                       f"replace it with the real {what}.")


def _not_ordinary(rel: str) -> SourceError:
    return SourceError(f"it contains {rel}, which isn't an ordinary file or folder, so Clips Kitty doesn't install "
                       "it. Its developer needs to remove it.")


def _no_folder(folder: str) -> SourceError:
    return SourceError(f"its listing or link points to a folder ({folder}) that isn't in its files. Its developer "
                       "needs to correct the listing or link.")


def _unreadable(rel: str) -> SourceError:
    return SourceError(f"Clips Kitty couldn't read {rel} in that folder. If another program has it open, close it "
                       "and try again.")


def copy_folder(folder: Path, dest: Path) -> list[str]:
    """Copy a plugin folder into `dest` (which must not exist). `.git` and
    `__pycache__` folders are left out; a symbolic link anywhere refuses it.
    A file or folder it can't read is named. Returns warnings."""
    folder = Path(folder)

    def cannot_list(e: OSError):
        log.warning("Couldn't read the folder %s: %s", e.filename, e)
        try:
            name = Path(e.filename).relative_to(folder).as_posix()
        except (TypeError, ValueError):
            name = "."
        name = str(folder) if name == "." else f"{name} in that folder"
        raise SourceError(f"Clips Kitty couldn't open the folder {name}. Check that you can open it yourself, "
                          "then try again.") from e

    try:
        is_folder = folder.is_dir()
    except OSError as e:
        cannot_list(e)
    if not is_folder:
        raise SourceError(f"there is no folder at {folder}.")
    if _is_link(folder):
        raise SourceError(f"{folder} is a shortcut to another folder. Choose the folder it points to instead.")

    budget, warnings, files = _Budget(), [], []
    for here, dirs, names in os.walk(folder, onerror=cannot_list, followlinks=False):
        base = Path(here)
        for d in list(dirs):
            if _is_link(base / d):
                raise _shortcut((base / d).relative_to(folder).as_posix(), "folder")
            if d in SKIPPED_DIRS:
                dirs.remove(d)
        for name in names:
            path = base / name
            rel = path.relative_to(folder).as_posix()
            try:
                if _is_link(path):
                    raise _shortcut(rel)
                if not path.is_file():
                    raise _not_ordinary(rel)
                if name.endswith(".pyc"):
                    continue
                size = path.stat().st_size
            except OSError as e:
                log.warning("Couldn't read %s: %s", path, e)
                raise _unreadable(rel) from e
            budget.add(_safe_relative(rel), size)
            files.append((rel, path))
    dest.mkdir(parents=True)
    for rel, path in files:
        try:
            data = path.read_bytes()
        except OSError as e:
            log.warning("Couldn't read %s: %s", path, e)
            raise _unreadable(rel) from e
        _write(dest, rel, data, os.access(path, os.X_OK) and os.name != "nt", warnings)
    return warnings


# ---- Git ------------------------------------------------------------------------------


def find_git() -> str | None:
    return shutil.which("git")


def _git_env() -> dict:
    env = dict(os.environ)
    # Never ask for a password: a public plugin needs none, and a prompt would
    # hang the engine where nobody can see it.
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_ASKPASS="", SSH_ASKPASS="")
    # Git's messages in English whatever the PC's language: _git_failure reads them.
    env["LC_ALL"] = "C"
    env.pop("LANGUAGE", None)
    return env


# What Git says, lower-cased with its quoted addresses and paths taken out, for
# each kind of failure.
_GIT_DISK = ("no space left on device", "not enough space on the disk", "disk quota exceeded")
_GIT_GONE = ("repository not found", "not found", "could not read username", "does not appear to be a git repository",
             "not our ref", "unadvertised object", "couldn't find remote ref",
             "returned error: 401", "returned error: 403", "returned error: 404", "returned error: 410")
_GIT_OFFLINE = ("could not resolve host", "could not resolve proxy", "failed to connect", "connection timed out",
                "operation timed out", "connection refused", "connection reset", "network is unreachable",
                "rpc failed", "early eof", "remote end hung up", "gnutls", "schannel", "certificate")
_GIT_SSL = re.compile(r"(?<![a-z])ssl(?![a-z])")


def _git_failure(stderr: str, *, fetching: bool = True) -> str:
    """The kind of failure (OFFLINE, GONE, DISK or OTHER) Git's error output
    describes. Lines Git passes on from the server ("remote: ...") can say a
    repository isn't there, but never what this PC's disk or connection did;
    an error the server reported is OTHER, not the connection. Only a fetch
    reaches a repository, so any other command's failure is DISK or OTHER."""
    lines = [re.sub(r"'[^'\n]*'", "''", line).strip()  # an address or path could hold any word
             for line in stderr.lower().splitlines()]
    from_server = [line for line in lines if line.startswith("remote:")]
    own = "\n".join(line for line in lines if not line.startswith("remote:"))
    if any(phrase in own for phrase in _GIT_DISK):
        return DISK
    if not fetching:
        return OTHER
    if any(phrase in text for text in (own, *from_server) for phrase in _GIT_GONE):
        return GONE
    if any(line.startswith(("remote: fatal", "remote: error")) for line in from_server):
        return OTHER  # the server failed, not the connection
    if any(phrase in own for phrase in _GIT_OFFLINE) or _GIT_SSL.search(own):
        return OFFLINE
    return OTHER


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
    fetching = args[0] == "fetch"  # the one command that reaches the repository
    try:
        done = subprocess.run(command, cwd=cwd, input=input, capture_output=True, timeout=GIT_TIMEOUT,
                              env=_git_env(), check=False)
    except subprocess.TimeoutExpired as e:
        log.warning("git %s took longer than %d minutes", args[0], GIT_TIMEOUT // 60)
        raise FetchFailed("Git took too long", OFFLINE if fetching else OTHER) from e
    except OSError as e:
        log.warning("git %s could not be started: %s", args[0], e)
        raise FetchFailed("Git could not be started", OTHER) from e
    if done.returncode != 0:
        stderr = done.stderr.decode("utf-8", "replace")
        lines = [line for line in stderr.strip().splitlines() if line.strip()]
        # Git's own "fatal:" and "error:" lines say what happened; advice follows them.
        detail = [line for line in lines if line.lower().startswith(("fatal:", "error:"))] or lines
        log.warning("git %s failed with code %d: %s", args[0], done.returncode, " / ".join(detail[-3:]))
        raise FetchFailed("Git failed", _git_failure(stderr, fetching=fetching))
    return done.stdout


def _read_batch(raw: bytes, count: int) -> list[bytes]:
    """The contents out of `git cat-file --batch` output."""
    out, i = [], 0
    for _ in range(count):
        end = raw.find(b"\n", i)
        header = raw[i:end].split(b" ") if end >= 0 else []
        if len(header) != 3 or header[1] != b"blob" or not header[2].isdigit():
            raise FetchFailed("Git returned something other than a file", OTHER)
        size = int(header[2])
        start = end + 1
        out.append(raw[start:start + size])
        i = start + size + 1
    return out


def _under(rel: str, folder: str | None) -> str | None:
    """`rel` relative to `folder` (the plugin's folder in a repository), or
    None when it is outside it."""
    if not folder:
        return rel
    prefix = folder.rstrip("/") + "/"
    return rel[len(prefix):] if rel.startswith(prefix) else None


def fetch_git(url: str, commit: str, dest: Path, *, git: str, folder: str | None = None) -> list[str]:
    """Write the files of one commit into `dest` (which must not exist); with
    `folder`, only that folder's files, as the plugin's root. Returns warnings."""
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
            log.warning("%s has no commit %s", url, commit)
            raise FetchFailed(f"the repository has no commit {commit[:7]}", GONE) from e
        if kind != b"commit":
            log.warning("%s in %s is not a commit", commit, url)
            raise FetchFailed(f"{commit[:7]} is not a commit", GONE)
        listing = _git(git, ["ls-tree", "-r", "-z", "--long", "--full-tree", commit], cwd=repo, hooks=hooks)
        budget, entries = _Budget(), []
        for line in filter(None, listing.split(b"\0")):
            meta, _, name_raw = line.partition(b"\t")
            mode, kind, sha, size = meta.split()
            name = name_raw.decode("utf-8", "surrogateescape")
            if folder and _under(name, folder) is None:
                continue
            if mode == b"120000":
                raise _shortcut(name)
            if mode == b"160000" or kind == b"commit":
                raise SourceError(f"it includes {name}, a folder linked in from another project (a submodule), "
                                  "which Clips Kitty doesn't download. Its developer needs to put those files in "
                                  "the pipeline itself.")
            if kind != b"blob":
                continue
            rel = _under(_safe_relative(name), folder)
            if rel is None or PurePosixPath(rel).name.endswith(".pyc") or "__pycache__" in PurePosixPath(rel).parts:
                continue
            budget.add(rel, int(size))
            entries.append((rel, sha.decode(), mode == b"100755"))
        raw = _git(git, ["cat-file", "--batch"], cwd=repo, hooks=hooks,
                   input=b"".join(sha.encode() + b"\n" for _, sha, _ in entries))
        contents = _read_batch(raw, len(entries))
    if folder and not entries:
        raise _no_folder(folder)
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
        raise SourceError("Clips Kitty downloads only from secure (https://) addresses, and this one isn't.")
    request = urllib.request.Request(url, headers={"User-Agent": "Clips-Kitty-plugin-manager"})
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(path, "wb") as out:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise TooLarge(f"the download is larger than {limit // (1024 * 1024)} MB, the most Clips Kitty "
                                   "takes. Its developer needs to make it smaller.")
                out.write(chunk)
    except (OSError, http.client.HTTPException) as e:  # IncompleteRead is not an OSError
        log.warning("Couldn't download %s: %s", url, e)
        raise FetchFailed("the download failed", failure(e)) from e


def unpack_archive(archive: Path, commit: str, dest: Path, *, folder: str | None = None) -> list[str]:
    """Unpack GitHub's .tar.gz of one commit into `dest` (which must not exist),
    without the archive's top folder; with `folder`, only that folder's files.
    Returns warnings."""
    budget, entries, warnings = _Budget(), [], []
    try:
        with tarfile.open(archive, "r:gz") as tar:
            stamped = (tar.pax_headers or {}).get("comment")
            if stamped and stamped.strip() != commit:
                log.warning("The archive of commit %s is of commit %s", commit, stamped.strip())
                raise FetchFailed(f"the archive is of commit {stamped.strip()[:7]}, not {commit[:7]}", DAMAGED)
            top = None
            for member in tar:
                parts = PurePosixPath(member.name).parts
                if not parts:
                    continue
                top = top or parts[0]
                if parts[0] != top:
                    log.warning("The archive of commit %s is not one folder: %s and %s", commit, top, parts[0])
                    raise FetchFailed("the archive is not one repository folder", DAMAGED)
                if len(parts) == 1 and member.isdir():
                    continue
                rel = _under(_safe_relative("/".join(parts[1:])), folder)
                if rel is None or member.isdir():
                    continue
                if member.issym() or member.islnk():
                    raise _shortcut(rel)
                if not member.isfile():
                    raise _not_ordinary(rel)
                if rel.endswith(".pyc") or "__pycache__" in PurePosixPath(rel).parts:
                    continue
                budget.add(rel, member.size)
                handle = tar.extractfile(member)
                entries.append((rel, handle.read() if handle else b"", bool(member.mode & 0o111)))
    except (tarfile.TarError, OSError, EOFError, zlib.error) as e:
        log.warning("Couldn't read the archive of commit %s: %s", commit, e)
        raise FetchFailed("the downloaded archive could not be read", DAMAGED) from e
    if folder and not entries:
        raise _no_folder(folder)
    dest.mkdir(parents=True)
    for rel, data, executable in entries:
        _write(dest, rel, data, executable, warnings)
    return warnings


# ---- one entry point -------------------------------------------------------------------


def fetch(source: dict, dest: Path, *, git: str | None = None, fetcher=None) -> list[str]:
    """Put a cleaned source's files in `dest` (which must not exist). Returns a
    line for each file it couldn't fetch (Git LFS), for the install screen's
    technical details; FILES_MISSING says it plainly. Raises SourceError.

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
                return fetch_git(source["url"], source["commit"], dest, git=git, folder=source.get("path"))
            archive_url = github_archive_url(source["url"], source["commit"])
            if not archive_url:
                raise SourceError("downloading it from this address needs Git, a free program that isn't installed "
                                  "on this PC. Install Git and try again, or ask its developer to put it on GitHub.")
            with tempfile.TemporaryDirectory(prefix="clipskitty-archive-") as tmp:
                path = Path(tmp) / "plugin.tar.gz"
                try:
                    (fetcher or download)(archive_url, path)
                except (OSError, http.client.HTTPException) as e:  # a fetcher other than download()
                    log.warning("Couldn't download %s: %s", archive_url, e)
                    raise FetchFailed("the download failed", failure(e)) from e
                return unpack_archive(path, source["commit"], dest, folder=source.get("path"))
    except SourceError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(dest, ignore_errors=True)
        log.warning("Couldn't copy the plugin's files into %s: %s", dest, e)
        if failure(e) == DISK:
            raise FetchFailed("this PC's disk is full", DISK) from e
        raise SourceError(NOT_SAVED) from e
    raise SourceError(f"Clips Kitty doesn't know how to install from {source.get('kind')!r}.")
