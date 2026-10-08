# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Starting a plugin from a template: `python -m clipskitty_sdk new`.

    python -m clipskitty_sdk new quarkbloom-bursts --template game-events --publisher your-github-name

writes a new plugin folder that validates, runs on the SDK's sample
(`run --sample`) and has its own tests and GitHub workflow. The templates
are files in templates/: one folder for each template, and _shared/ for the
files every template writes (a template's own file of the same name wins).
Files whose real name starts with a dot are stored without it (gitignore,
github/) and renamed as they are written, so they ship inside the package.

Placeholders in the files are @@TOKEN@@ words, replaced in one pass by plain
text, so `$schema=`, `${{ matrix.os }}` and PowerShell's `$env:` are left as
they are. Before anything is written, the plugin is made in a temporary
folder and its clipskitty.yaml is checked with manifest.validate_folder, so
`new` never writes a plugin Clips Kitty would refuse. Standard library only.
"""

from __future__ import annotations

import datetime
import json
import re
import tempfile
from pathlib import Path

from . import __version__
from .host import APP_PYTHON
from .manifest import MANIFEST_FILE, RESERVED_PUBLISHERS, SLUG_PATTERN, has_yaml, validate_folder

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
SHARED = "_shared"

# The templates, in the order `new --list` shows them, with what each does.
TEMPLATES = {
    "blank": "finds nothing yet: a start for your own checks",
    "transcript": "finds moments where your words are said",
    "game-events": "finds moments when a coloured banner shows and the sound gets louder",
    "rater": "rates moments others found, by the words said in them",
    "understander": "notes what happens in moments others found, from the screen and the words",
}

# The Clips Kitty versions a new plugin asks for (requires.clips_kitty). The
# templates import media, signals and text, which arrive with SDK 1.2.0, so
# this is right only while the first release that runs plugins includes SDK
# 1.2.0: 2.0.0 itself can't install any plugin. FLOOR_REVIEWED_AT is the
# newest release in CHANGELOG.md when this was last checked;
# tests/test_plugin_sdk_new.py fails when a newer release is added there.
TEMPLATE_REQUIRES = ">=2.0"
FLOOR_REVIEWED_AT = "2.0.0"

# What a plugin made where nobody can be asked gets.
DEFAULT_PUBLISHER = "your-github-name"
DEFAULT_GAME = "quarkbloom-arena"  # Quarkbloom Arena is a made-up game
DEFAULT_LICENSE = "MIT"
# The page `new` points to after making a plugin.
GUIDE = "https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/getting-started.md"

# The questions `new` asks on a terminal, for what the command line left out.
ASK_PUBLISHER = "Your GitHub name, in lower case (it becomes the publisher): "
ASK_NAME = "Plugin name: "

# The words a template's files may hold, as @@WORD@@.
TOKENS = ("ID", "NAME", "PUBLISHER", "GAME", "AUTHOR", "YEAR", "REQUIRES", "SDK_VERSION", "PYTHON", "TEMPLATE",
          "LICENSE")
_TOKEN = re.compile(r"@@([A-Z_]+)@@")
# Stored names, and the names they are written as.
RENAMED = {"gitignore": ".gitignore", "github": ".github"}
# Written only for the MIT licence; another licence's text is the developer's to add.
LICENSE_FILE = "LICENSE"
PUBLISHER_PATTERN = r"^[a-z0-9][a-z0-9-]{0,38}$"
_PLUGIN_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# What PyYAML is needed for here: reading back the clipskitty.yaml `new` writes.
NEEDS_YAML = ("new checks the clipskitty.yaml it writes, and reading it needs PyYAML, which isn't installed "
              'with this Python: pip install "clipskitty-sdk[yaml]". This is about your PC, not your plugin.')


class NewRefused(Exception):
    """Why `new` writes nothing: printed as `error: ...`, exit code 2."""


# ---- the names -------------------------------------------------------------------------


def check_publisher(publisher: str) -> str:
    """`publisher` when it can start a plugin's id, else NewRefused."""
    if publisher in RESERVED_PUBLISHERS:
        raise NewRefused(f"the publisher {publisher} is reserved for plugins that ship with Clips Kitty")
    if not re.match(PUBLISHER_PATTERN, publisher or ""):
        raise NewRefused("--publisher must be your GitHub name in lower case: letters, digits and hyphens")
    return publisher


def plugin_name(folder: Path) -> str:
    """The second part of the plugin's id, from its folder's name:
    "Quarkbloom Bursts" -> quarkbloom-bursts. NewRefused when nothing usable
    is left."""
    name = Path(folder).resolve().name
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64].rstrip("-")
    if not _PLUGIN_NAME.match(slug):
        raise NewRefused(f"the folder's name, {name!r}, gives the plugin no id: name the folder with letters, "
                         "digits and hyphens, such as quarkbloom-bursts")
    return slug


def display_name(folder: Path) -> str:
    """A plugin's name for people, from its folder's: quarkbloom-bursts -> Quarkbloom Bursts."""
    words = re.split(r"[-_\s]+", Path(folder).resolve().name)
    return " ".join(w[:1].upper() + w[1:] for w in words if w) or "My Plugin"


def one_line(text) -> str:
    """`text` on one line, its spaces tidied."""
    return " ".join(str(text or "").split())


# ---- the files ---------------------------------------------------------------------------


def _files_in(folder: Path) -> dict[str, Path]:
    out = {}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if path.is_file() and "__pycache__" not in rel.parts and path.suffix != ".pyc":
            out[rel.as_posix()] = path
    return out


def written_name(stored: str) -> str:
    """Where a stored template file is written: gitignore -> .gitignore,
    github/workflows/x.yml -> .github/workflows/x.yml."""
    first, sep, rest = stored.partition("/")
    return RENAMED.get(first, first) + sep + rest


def template_files(template: str) -> dict[str, Path]:
    """The files a template writes, by the path they are written to: the
    shared ones, then the template's own, which win."""
    if template not in TEMPLATES:
        raise NewRefused(f"--template: there is no template called {template!r}; choose one of "
                         f"{', '.join(TEMPLATES)} (new --list says what each does)")
    files = {**_files_in(TEMPLATES_DIR / SHARED), **_files_in(TEMPLATES_DIR / template)}
    return {written_name(rel): path for rel, path in files.items()}


def values(*, plugin_id: str, name: str, publisher: str, game: str, author: str, license: str, template: str,
           year: int | None = None) -> dict[str, str]:
    """Each @@TOKEN@@'s text."""
    return {
        "ID": plugin_id, "NAME": name, "PUBLISHER": publisher, "GAME": game, "AUTHOR": author,
        "YEAR": str(year or datetime.date.today().year), "REQUIRES": TEMPLATE_REQUIRES,
        "SDK_VERSION": __version__, "PYTHON": ".".join(map(str, APP_PYTHON)), "TEMPLATE": template,
        "LICENSE": license,
    }


def _in_yaml(text: str) -> str:
    """`text` as it goes between double quotes in YAML (JSON's escapes are YAML's too)."""
    return json.dumps(text, ensure_ascii=False)[1:-1]


def render(text: str, given: dict[str, str], *, yaml: bool = False) -> str:
    """`text` with each @@TOKEN@@ replaced, in one pass: a value is never
    read again for tokens. In a YAML file the values are escaped for double
    quotes, where the templates put free text."""
    def value(match: re.Match) -> str:
        token = match.group(1)
        if token not in given:
            raise KeyError(f"unknown template token @@{token}@@")
        return _in_yaml(given[token]) if yaml else given[token]

    return _TOKEN.sub(value, text)


def render_template(template: str, given: dict[str, str]) -> dict[str, str]:
    """Every file the template writes, by path, rendered. LICENSE (MIT) is
    left out for any other licence."""
    out = {}
    for rel, path in template_files(template).items():
        if rel == LICENSE_FILE and given["LICENSE"] != DEFAULT_LICENSE:
            continue
        out[rel] = render(path.read_text(encoding="utf-8"), given, yaml=rel.endswith((".yaml", ".yml")))
    return out


def _write(folder: Path, files: dict[str, str]) -> list[Path]:
    written = []
    for rel, text in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        written.append(path)
    return written


# ---- making a plugin ---------------------------------------------------------------------


def check_folder(folder: Path) -> None:
    """NewRefused unless `folder` is new or empty."""
    folder = Path(folder)
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise NewRefused(f"{folder} isn't empty; new only writes into a new or empty folder")


def make(folder, template: str, *, publisher: str = DEFAULT_PUBLISHER, name: str | None = None,
         game: str = DEFAULT_GAME, author: str | None = None, license: str = DEFAULT_LICENSE,
         year: int | None = None) -> list[Path]:
    """Write a new plugin from `template` into `folder` (new, or empty) and
    return the files written.

    The id is `publisher`/the folder's name in lower case; `name` (the name
    people see) defaults to the folder's name, `author` to the publisher.
    Raises NewRefused, with nothing written, for a full folder, a publisher
    that can't start an id, or a plugin whose clipskitty.yaml Clips Kitty
    would refuse (its errors joined)."""
    folder = Path(folder)
    if template not in TEMPLATES:
        template_files(template)  # raises NewRefused with the choices
    check_publisher(publisher)
    if not re.match(SLUG_PATTERN, game or ""):
        raise NewRefused("--game must be the game's short name in lower case: letters, digits and hyphens, "
                         f"such as {DEFAULT_GAME}")
    check_folder(folder)
    if not has_yaml():
        raise NewRefused(NEEDS_YAML)
    slug = plugin_name(folder)
    given = values(plugin_id=f"{publisher}/{slug}", name=one_line(name) or display_name(folder),
                   publisher=publisher, game=game, author=one_line(author) or publisher,
                   license=one_line(license) or DEFAULT_LICENSE, template=template, year=year)
    files = render_template(template, given)
    with tempfile.TemporaryDirectory(prefix="clipskitty-new-") as scratch:
        staged = Path(scratch) / slug
        _write(staged, files)
        _, report = validate_folder(staged)
    if not report.ok:
        raise NewRefused(f"Clips Kitty would refuse this plugin, so new wrote nothing: {'; '.join(report.errors)}")
    folder.mkdir(parents=True, exist_ok=True)
    return _write(folder, files)


def license_note(license: str) -> str | None:
    """What `new` says about the licence: nothing for MIT, whose text it writes."""
    if one_line(license) in ("", DEFAULT_LICENSE):
        return None
    return f"add the text of {one_line(license)} as LICENSE"


def list_text() -> str:
    """What `new --list` prints."""
    width = max(map(len, TEMPLATES)) + 3
    return "".join(f"{name:<{width}}{what}\n" for name, what in TEMPLATES.items())


__all__ = ["ASK_NAME", "ASK_PUBLISHER", "DEFAULT_GAME", "DEFAULT_LICENSE", "DEFAULT_PUBLISHER",
           "FLOOR_REVIEWED_AT", "GUIDE", "MANIFEST_FILE", "NEEDS_YAML", "RENAMED", "TEMPLATES", "TEMPLATES_DIR",
           "TEMPLATE_REQUIRES", "TOKENS", "NewRefused", "check_folder", "check_publisher", "display_name",
           "license_note", "list_text", "make", "plugin_name", "render", "render_template", "template_files",
           "values", "written_name"]
