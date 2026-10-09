# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""The core of `python -m clipskitty_sdk listing`: the file that lists a
plugin in Awesome Clips Kitty, the catalog Clips Kitty's Marketplace reads,
made from the plugin's folder and its git repository.

    python -m clipskitty_sdk listing FOLDER --section gaming/generic [--alias WORDS ...]
                                     [--to EXISTING.yaml] [--out FILE]

In order, it:

1. checks the plugin as `validate` does, lint included;
2. refuses a plugin that still has a template's placeholders
   (PLACEHOLDERS, MADE_UP_LINE) in clipskitty.yaml or README.md;
3. reads the folder's git repository (place): the folder must have no
   changes that aren't committed, the repository's remote must be on GitHub
   and be the `repository` clipskitty.yaml names (when it names one), the
   id's publisher must own that repository, and the commit must be on a
   branch of that remote that was pushed;
4. writes the listing in the catalog's format (id, repository, path,
   section, aliases, `added` as today's UTC date, and this version with its
   full commit, plus `tag: v{version}` when that tag points at the commit),
   or with --to adds this version to a listing that exists, refusing a
   version that is already listed;
5. prints what to do next (NEXT): the pull request must carry the rebuilt
   index.json and README.md, because CI fails one without them.

Read only: the only git commands it runs are ones that read (read_only), with
optional locks off, so even `git status` writes nothing. It never pushes,
never opens a pull request and never fetches anything, so "pushed" means on
a remote-tracking branch, as of the last push or fetch, and the section isn't
checked against sections.yaml. Exit codes: 0 written, 2 refused. Standard
library only; YAML is read through manifest.yaml_data.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import devrun, samples, scaffold
from .manifest import MANIFEST_FILE, line_marks, validate_folder, yaml_data

# The catalog: the repository that holds it, and the folder a pipeline's listing goes in.
CATALOG_REPOSITORY = "ColinGPT9/awesome-clips-kitty"
CATALOG_FOLDER = "registry/pipelines"
# The catalog's own rules (plugins/catalog.py SECTION_RE and OFFICIAL_OWNERS, and
# plugins/registry.py check_listing); tests/test_registry.py checks they match.
SECTION_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*(/[a-z0-9]+(-[a-z0-9]+)*)?$"
# The Clips Kitty project's own GitHub owners, whose repositories may list
# plugins under other publisher names (its examples).
OFFICIAL_OWNERS = ("colingpt9",)
MAX_ALIASES = 10
MAX_ALIAS_LENGTH = 40
README_FILE = "README.md"

# What a plugin made from a template starts with, and the docs' example names.
# A catalog listing is for a real plugin, so `listing` refuses any of them.
PLACEHOLDERS = (scaffold.DEFAULT_PUBLISHER, "example-dev", scaffold.DEFAULT_GAME, "quark_burst", "your-game",
                "my_event")
MADE_UP_LINE = "Quarkbloom Arena (a made-up game)"

UNCOMMITTED = "the folder has changes that aren't committed. Commit them, so the listing points at what you tested"
UNPUSHED = "commit {short} isn't on a pushed branch yet. Push it, then run this again"
OTHER_REPOSITORY = "repository in clipskitty.yaml is {a}, but this folder's git remote is {b}"
NOT_THE_OWNER = "the id's publisher is {p}, but the repository's owner is {o}: they must be the same"
PLACEHOLDER = "the plugin still has the template's placeholder {word}: a catalog listing is for your real plugin"
ALREADY_LISTED = "version {v} is already listed; a listed version never changes"
NO_TAG = "no tag v{version} at this commit; the listing names the commit only"
CHECK_SECTION = f"check that {{section}} is in registry/sections.yaml of {CATALOG_REPOSITORY}"
NEXT = f"""Next, in a copy of {CATALOG_REPOSITORY}:
  1. {{first}}
  2. open a pull request with that one file.
Its check builds the catalog with your file and says why if it is refused. Leave index.json and README.md
alone: a job rebuilds both after the merge.
Once that is done, Clips Kitty versions with the Marketplace see it after Check for new pipelines."""
ADD_AS = "add this file as {where}"
REPLACE = "put this file in place of {where}"
IN_PLACE = "the file is already {where}"

NO_GIT = "listing reads the plugin's git repository, and git isn't on PATH. Install Git, then run this again"
NOT_A_REPOSITORY = ("{folder} isn't in a git repository: a listing names a commit of your plugin's GitHub "
                    "repository. Put the folder in one, push it to GitHub, then run this again")
NO_COMMIT = "the repository has no commit yet. Commit the plugin and push it, then run this again"
NOT_SHA1 = "commit {commit} isn't a 40-character commit hash, which is what the catalog lists"
NO_REMOTE = "this folder's git repository has no remote. Push it to GitHub, then run this again"
WHICH_REMOTE = ("this folder's git repository has several remotes, none called origin, and this branch tracks "
                "none of them. Push it with git push -u <remote> <branch>, then run this again")
NOT_GITHUB = ("this folder's git remote is {b}, which isn't a GitHub repository: a listing's repository must be "
              "https://github.com/<owner>/<repo>")
GIT_FAILED = "git {command} failed: {why}"
GIT_SUGGESTS = "{failed}. Git says to run: {command}"
NO_SECTION = (f"--section is needed: a pipeline section id from registry/sections.yaml of {CATALOG_REPOSITORY}, "
              "such as gaming/generic")
BAD_SECTION = (f"--section must be a section id from registry/sections.yaml of {CATALOG_REPOSITORY}, such as "
               "gaming/generic")
BAD_ALIASES = (f"--alias: at most {MAX_ALIASES} words or short phrases, each up to {MAX_ALIAS_LENGTH} "
               "characters on one line")
TO_ONLY_ADDS = "--to only adds a version: change the section or aliases in {file} by hand"
OUT_EXISTS = "{out} already exists. To add this version to that listing, pass --to {out}"
INSIDE_PLUGIN = ("that is inside a plugin's folder, and Clips Kitty copies everything there on install. Write the "
                 "listing somewhere else, for example: --out {example}")
NOT_A_LISTING = "{file} isn't a listing: {why}"
OTHER_PLUGIN = "{file} is the listing of {other}, not {id}"
OTHER_PLACE = ("{file} lists {id} from {theirs}, but this folder is {ours}: every version of a listing comes "
               "from the same repository and folder")
CANT_ADD = ("couldn't add the version to {file} without rewriting it: its versions aren't a list with each "
            "entry on lines of its own. Add this entry to its versions by hand:\n{entry}")
NOT_WRITTEN = "couldn't write {file}: {why}"

_SECTION = re.compile(SECTION_PATTERN)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
# A GitHub repository as git names its remote: https, ssh, git or scp-like.
_GITHUB_URL = re.compile(
    r"^(?:(?:https?|ssh|git)://(?:[^/@\s]+@)?github\.com(?::\d+)?/|(?:[^/@:\s]+@)?github\.com:)"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", re.IGNORECASE)
# Scalars written without quotes, when YAML reads them back as the same text.
_PLAIN = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9 ._/:+-]*$")
_PLAIN_IN_LIST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]*$")


class ListingRefused(Exception):
    """Why `listing` writes nothing: each message is printed as `error: ...`,
    and the exit code is 2."""

    def __init__(self, *messages: str):
        super().__init__(*messages)
        self.messages = messages

    def __str__(self) -> str:
        return "\n".join(self.messages)


# ---- the plugin ----------------------------------------------------------------------------


def _word_pattern(word: str) -> re.Pattern:
    """`word` on its own, not as part of a longer name, in any case."""
    return re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(word)}(?![A-Za-z0-9_-])", re.IGNORECASE)


def _phrase_pattern(phrase: str) -> re.Pattern:
    """`phrase`, in any case, even when it is wrapped over lines."""
    return re.compile(r"\s+".join(re.escape(part) for part in phrase.split()), re.IGNORECASE)


_FOUND = (*((word, _word_pattern(word)) for word in PLACEHOLDERS), (MADE_UP_LINE, _phrase_pattern(MADE_UP_LINE)))


def _strings(value, path: str = ""):
    """Every text in a manifest, with its path as validate() names it."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _strings(item, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, value


def placeholders(folder: str | os.PathLike, manifest: dict) -> list[str]:
    """Each template placeholder still in the plugin, as the line `listing`
    refuses it with: the manifest's values (not its comments), then
    README.md. Empty when there are none."""
    folder = Path(folder)
    found = []
    try:
        marks = line_marks((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        marks = {}
    for path, text in _strings(manifest):
        for word, pattern in _FOUND:
            if pattern.search(text):
                where = f"{MANIFEST_FILE} line {marks[path]}" if path in marks else f"{MANIFEST_FILE}, {path}"
                found.append(f"{PLACEHOLDER.format(word=word)} ({where})")
    readme = folder / README_FILE
    try:
        text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
    except (OSError, UnicodeDecodeError):
        text = ""
    for word, pattern in _FOUND:
        m = pattern.search(text)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            found.append(f"{PLACEHOLDER.format(word=word)} ({README_FILE} line {line})")
    return found


def check_section(section: str | None) -> str:
    if not section:
        raise ListingRefused(NO_SECTION)
    if not _SECTION.match(section):
        raise ListingRefused(BAD_SECTION)
    return section


def check_aliases(aliases) -> list[str]:
    """The aliases as a listing holds them: trimmed, each once, in order."""
    out: list[str] = []
    for alias in aliases or ():
        alias = " ".join(str(alias).split())
        if not alias or len(alias) > MAX_ALIAS_LENGTH or not alias.isprintable():
            raise ListingRefused(BAD_ALIASES)
        if alias not in out:
            out.append(alias)
    if len(out) > MAX_ALIASES:
        raise ListingRefused(BAD_ALIASES)
    return out


# ---- git, read only ------------------------------------------------------------------------


def read_only(args) -> bool:
    """Whether a git command (its arguments after `git`) only reads. These
    are the only commands `listing` runs: rev-parse, for-each-ref, status
    (with optional locks off, so it doesn't refresh the index) and remote,
    listing the remotes or getting one's address."""
    args = list(args)
    if not args:
        return False
    if args[0] in ("rev-parse", "for-each-ref", "status"):
        return True
    return args[0] == "remote" and (len(args) == 1 or (len(args) == 3 and args[1] == "get-url"))


def _git(git: str, folder: Path, *args: str) -> subprocess.CompletedProcess:
    """One git command that only reads, run in `folder`."""
    if not read_only(args):
        raise ValueError(f"listing runs only git commands that read, not git {' '.join(args)}")
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    return subprocess.run([git, *args], cwd=str(folder), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, env=env)


def _failed(args, result) -> ListingRefused:
    said = (result.stderr or result.stdout or "").strip().splitlines()
    why = said[0] if said else f"exit code {result.returncode}"
    return ListingRefused(GIT_FAILED.format(command=" ".join(args), why=why))


def _no_repository(folder: Path, result) -> bool:
    """Whether a failed `git rev-parse` means the folder isn't in a
    repository, rather than another problem git's own words explain better
    (a repository owned by another user, a broken one). Git's English
    message says so; in another language, there is no .git at or above the
    folder."""
    if "not a git repository" in (result.stderr or "").lower():
        return True
    here = Path(folder).resolve()
    return not any((where / ".git").exists() for where in (here, *here.parents))


def _repository_failed(args, result) -> ListingRefused:
    """git's first line, and the command it suggests when it gives one, such
    as `git config --global --add safe.directory ...` for a repository owned
    by another user."""
    failed = _failed(args, result)
    suggested = [line.strip() for line in (result.stderr or "").splitlines() if line.strip().startswith("git ")]
    return ListingRefused(GIT_SUGGESTS.format(failed=failed, command=suggested[0])) if suggested else failed


def _out(git: str, folder: Path, *args: str) -> str:
    result = _git(git, folder, *args)
    if result.returncode != 0:
        raise _failed(args, result)
    return result.stdout


def github_repository(url) -> tuple[str, str] | None:
    """(owner, repo) of a GitHub repository address as git or a manifest
    gives it (https, ssh or git@github.com:owner/repo.git), else None."""
    m = _GITHUB_URL.match(str(url or "").strip())
    return (m.group(1), m.group(2)) if m else None


def shown_url(url: str) -> str:
    """A remote's address as messages show it: a GitHub one as
    https://github.com/owner/repo, any other without a user name or
    password in it."""
    found = github_repository(url)
    if found:
        return f"https://github.com/{found[0]}/{found[1]}"
    url = re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]*@", r"\1", url.strip())
    return re.sub(r"^[^/@:\s]+@([^/:\s]+:)", r"\1", url)


def _remote(git: str, folder: Path) -> str:
    """The remote the listing's repository is: the one this branch tracks,
    else origin, else the only one."""
    remotes = _out(git, folder, "remote").split()
    if not remotes:
        raise ListingRefused(NO_REMOTE)
    tracked = _git(git, folder, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if tracked.returncode == 0:
        upstream = tracked.stdout.strip()
        names = [r for r in remotes if upstream.startswith(r + "/")]
        if names:
            return max(names, key=len)
    if "origin" in remotes:
        return "origin"
    if len(remotes) == 1:
        return remotes[0]
    raise ListingRefused(WHICH_REMOTE)


def place(folder: str | os.PathLike, manifest: dict, *, git: str | None = None) -> dict:
    """Where the plugin's code is, from its git repository, as a listing
    names it: {repository, owner, path, commit, tag}. `tag` is v{version}
    when that tag points at the commit, else None. Raises ListingRefused
    when the folder isn't committed or pushed, or the repository isn't the
    manifest's or isn't owned by the id's publisher."""
    folder = Path(folder)
    git = git or shutil.which("git")
    if not git:
        raise ListingRefused(NO_GIT)
    prefix = _git(git, folder, "rev-parse", "--show-prefix")
    if prefix.returncode != 0:
        if _no_repository(folder, prefix):
            raise ListingRefused(NOT_A_REPOSITORY.format(folder=folder))
        raise _repository_failed(("rev-parse", "--show-prefix"), prefix)
    path = prefix.stdout.strip().strip("/") or "."
    if _out(git, folder, "status", "--porcelain", "--untracked-files=all", "--", ".").strip():
        raise ListingRefused(UNCOMMITTED)
    head = _git(git, folder, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if head.returncode != 0:
        raise ListingRefused(NO_COMMIT)
    commit = head.stdout.strip()
    if not _COMMIT.match(commit):
        raise ListingRefused(NOT_SHA1.format(commit=commit))

    remote = _remote(git, folder)
    address = _out(git, folder, "remote", "get-url", remote).strip()
    found = github_repository(address)
    if not found:
        raise ListingRefused(NOT_GITHUB.format(b=shown_url(address)))
    owner, repo = found
    repository = f"https://github.com/{owner}/{repo}"
    declared = manifest.get("repository")
    if declared:
        theirs = github_repository(declared)
        if not theirs or (theirs[0].lower(), theirs[1].lower()) != (owner.lower(), repo.lower()):
            raise ListingRefused(OTHER_REPOSITORY.format(a=declared, b=repository))
    publisher = str(manifest.get("id", "")).split("/", 1)[0]
    if owner.lower() != publisher and owner.lower() not in OFFICIAL_OWNERS:
        raise ListingRefused(NOT_THE_OWNER.format(p=publisher, o=owner))

    pushed = _out(git, folder, "for-each-ref", "--contains", commit, "--format=%(refname)", f"refs/remotes/{remote}")
    if not pushed.strip():
        raise ListingRefused(UNPUSHED.format(short=commit[:7]))
    tag = f"v{manifest.get('version')}"
    tagged = _git(git, folder, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    return {"repository": repository, "owner": owner, "repo": repo, "path": path, "commit": commit,
            "tag": tag if tagged.returncode == 0 and tagged.stdout.strip() == commit else None}


# ---- the listing file ----------------------------------------------------------------------


def _scalar(value, *, in_list: bool = False) -> str:
    """`value` as a YAML scalar: plain when YAML reads it back as the same
    text, else in double quotes."""
    text = str(value)
    plain = bool((_PLAIN_IN_LIST if in_list else _PLAIN).match(text)) and text == text.strip() and ": " not in text
    if plain:
        try:
            plain = yaml_data(text) == text
        except ValueError:
            plain = False
    return text if plain else json.dumps(text, ensure_ascii=not text.isprintable())


def version_entry(manifest: dict, where: dict) -> dict:
    entry = {"version": str(manifest["version"]), "commit": where["commit"]}
    if where.get("tag"):
        entry["tag"] = where["tag"]
    return entry


def _entry_lines(entry: dict, indent: str = "  ", gap: str = " ") -> list[str]:
    """A version as lines of a block list, its dash at `indent`."""
    keys = list(entry)
    pad = " " * (len(indent) + 1 + len(gap))
    return ([f"{indent}-{gap}{keys[0]}: {_scalar(entry[keys[0]])}"]
            + [f"{pad}{key}: {_scalar(entry[key])}" for key in keys[1:]])


def listing_text(manifest: dict, where: dict, *, section: str, aliases=(), added: str) -> str:
    """A new listing, in the catalog's format (CONTRIBUTING.md in its repository)."""
    lines = [f"id: {_scalar(manifest['id'])}", f"repository: {_scalar(where['repository'])}",
             f"path: {_scalar(where['path'])}", f"section: {_scalar(section)}"]
    if aliases:
        lines.append(f"aliases: [{', '.join(_scalar(a, in_list=True) for a in aliases)}]")
    lines += [f"added: {added}", "versions:", *_entry_lines(version_entry(manifest, where))]
    return "\n".join(lines) + "\n"


def read_listing(file: Path, manifest: dict) -> tuple[str, dict]:
    """The text and data of the listing --to names, checked against the
    plugin: the same id, and this version not listed yet."""
    try:
        text = file.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise ListingRefused(NOT_A_LISTING.format(file=file, why=getattr(e, "strerror", None) or str(e))) from e
    try:
        data = yaml_data(text)
    except ValueError as e:
        raise ListingRefused(NOT_A_LISTING.format(file=file, why=str(e))) from e
    if not isinstance(data, dict) or not isinstance(data.get("versions"), list):
        raise ListingRefused(NOT_A_LISTING.format(file=file, why="it has no list of versions"))
    if data.get("id") != manifest["id"]:
        raise ListingRefused(OTHER_PLUGIN.format(file=file, other=data.get("id"), id=manifest["id"]))
    listed = {str(v.get("version")) for v in data["versions"] if isinstance(v, dict)}
    if str(manifest["version"]) in listed:
        raise ListingRefused(ALREADY_LISTED.format(v=manifest["version"]))
    return text, data


def _from(repository, path) -> str:
    return str(repository) + ("" if path in (None, "", ".") else f", folder {path}")


def check_same_place(file: Path, data: dict, where: dict) -> None:
    """A version added with --to comes from the repository and folder the
    listing already names."""
    theirs = github_repository(data.get("repository"))
    same_repository = theirs is not None and (theirs[0].lower(), theirs[1].lower()) == (
        where["owner"].lower(), where["repo"].lower())
    if not same_repository or (str(data.get("path", ".")).strip("/") or ".") != where["path"]:
        raise ListingRefused(OTHER_PLACE.format(file=file, id=data.get("id"),
                                                theirs=_from(data.get("repository"), data.get("path")),
                                                ours=_from(where["repository"], where["path"])))


def add_version(text: str, data: dict, entry: dict, file: Path) -> str:
    """`text` with `entry` added after the last of its versions, written in
    the same indentation. Everything else in the file is left as it is."""
    newline = "\r\n" if "\r\n" in text else "\n"
    marks = line_marks(text)
    lines = text.splitlines(keepends=True)
    start, first = marks.get("versions"), marks.get("versions[0]")
    shape = re.match(r"^( *)-( +)\S", lines[first - 1]) if first and start and first > start else None
    if not shape:
        raise ListingRefused(CANT_ADD.format(file=file, entry="\n".join(_entry_lines(entry))))
    top = sorted(line for key, line in marks.items() if "." not in key and "[" not in key)
    end = next((line for line in top if line > start), len(lines) + 1)  # the next field's line, from 1
    last = max(i for i in range(start - 1, end - 1)
               if lines[i].strip() and not lines[i].lstrip().startswith("#"))
    if not lines[last].endswith(("\n", "\r")):
        lines[last] += newline
    added = [line + newline for line in _entry_lines(entry, shape.group(1), shape.group(2))]
    out = "".join(lines[:last + 1] + added + lines[last + 1:])
    # Check the result reads back as the same listing with one more version.
    try:
        after = yaml_data(out)
    except ValueError:
        after = None
    want = dict(data, versions=[*data["versions"], entry])
    if after != want:
        raise ListingRefused(CANT_ADD.format(file=file, entry="\n".join(_entry_lines(entry))))
    return out


def catalog_path(manifest: dict) -> str:
    """Where the listing goes in the catalog's repository."""
    return f"{CATALOG_FOLDER}/{manifest['id']}.yaml"


def next_text(manifest: dict, target: Path, *, adding: bool) -> str:
    """What to do after `listing`, as it prints it."""
    where = catalog_path(manifest)
    parts = Path(target).resolve().parts
    if tuple(p.lower() for p in parts[-len(where.split("/")):]) == tuple(where.lower().split("/")):
        first = IN_PLACE.format(where=where)
    else:
        first = (REPLACE if adding else ADD_AS).format(where=where)
    return NEXT.format(first=first)


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _target(manifest: dict, to, out) -> Path:
    """Where the listing is written: --out, else the --to file, else
    {name}.yaml here. Never inside a plugin's folder, and never over another
    file than the --to one."""
    name = manifest["id"].split("/", 1)[1]
    target = Path(out) if out else Path(to) if to else Path(f"{name}.yaml")
    plugin = samples.plugin_folder_holding(target)
    if plugin is not None:
        raise ListingRefused(INSIDE_PLUGIN.format(example=samples.beside_plugin(plugin, f"{name}.yaml")))
    if target.exists() and not (to and _same_file(target, Path(to))):
        raise ListingRefused(OUT_EXISTS.format(out=target))
    return target


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return Path(a).resolve() == Path(b).resolve()


# ---- the command ---------------------------------------------------------------------------


def run(folder, *, section: str | None = None, aliases=(), to=None, out=None, stdout=None, stderr=None,
        today: str | None = None, git: str | None = None) -> int:
    """`listing`: check the plugin and its repository, write its listing (or
    add this version to the one --to names), and say what to do next.
    Returns the exit code: 0 written, 2 refused."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        if to is None:
            section = check_section(section)
            aliases = check_aliases(aliases)
        elif section or aliases:
            raise ListingRefused(TO_ONLY_ADDS.format(file=to))
        folder = Path(folder).resolve()
        refusal = devrun.manifest_refusal(folder)
        if refusal:
            raise ListingRefused(refusal)
        manifest, report = validate_folder(folder)
        for line in devrun.report_lines(folder, report, manifest):
            print(line, file=stderr)
        if not report.ok:
            raise ListingRefused("fix the manifest first; Clips Kitty would refuse to install this plugin")
        left = placeholders(folder, manifest)
        if left:
            raise ListingRefused(*left)
        if to is not None:
            text, data = read_listing(Path(to), manifest)
        target = _target(manifest, to, out)
        where = place(folder, manifest, git=git)
        if to is not None:
            check_same_place(Path(to), data, where)
            written = add_version(text, data, version_entry(manifest, where), Path(to))
        else:
            written = listing_text(manifest, where, section=section, aliases=aliases, added=today or _today())
        try:
            target.write_bytes(written.encode("utf-8"))
        except OSError as e:
            raise ListingRefused(NOT_WRITTEN.format(file=target, why=e.strerror or e)) from e
    except ListingRefused as e:
        for message in e.messages:
            print(f"error: {message}", file=stderr)
        return 2
    if not where["tag"]:
        print(f"note: {NO_TAG.format(version=manifest['version'])}", file=stderr)
    if to is None:
        print(f"note: {CHECK_SECTION.format(section=section)}", file=stderr)
    print(f"wrote {target}: {manifest['id']} {manifest['version']} at commit {where['commit'][:7]}", file=stdout)
    print(next_text(manifest, target, adding=to is not None), file=stdout)
    return 0
