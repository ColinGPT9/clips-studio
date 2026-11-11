"""The caption fonts that ship with the app (assets/fonts/).

Twelve static TTF files under the SIL Open Font License 1.1, each with its
OFL.txt, listed in assets/fonts/manifest.json with the family name libass
matches, the weight, the sha256 and the writing systems the font covers in
full. Standard library only: server/mcp.py and the /fonts routes read it too.

A Windows font is found by libass among the installed fonts. A bundled one is
not installed anywhere, so the render that burns it copies its files into a
folder beside the ASS file (stage()) and gives FFmpeg's subtitles filter
`fontsdir=fonts`. Nothing about a render that uses only Windows fonts changes:
no folder, no file read, the same filter text as before.

A font file that is missing or does not match its sha256 is never staged. The
captions then burn in Arial and the job log says so (video/captions.py checks
usable() before it writes the ASS file). A render never fails over a font.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
from functools import lru_cache
from pathlib import Path

STAGED = "fonts"     # the folder, beside an ASS file, its burn reads bundled fonts from

_LOCK = threading.Lock()     # staging and releasing a folder, across the render threads
_CHECKED: dict[tuple, bool] = {}     # (path, size, mtime) -> matches its sha256


def _roots() -> list[Path]:
    """Where the fonts folder can be: in a packaged engine (PyInstaller
    one-dir puts data under _internal/ beside the exe, resources\\backend in
    the installed app), then the source checkout. Found the same way as the
    prebuilt end cards (video/outro.py)."""
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        roots += [exe_dir / "assets" / "fonts", exe_dir / "_internal" / "assets" / "fonts"]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass) / "assets" / "fonts")
    roots.append(Path(__file__).resolve().parent.parent / "assets" / "fonts")
    return roots


@lru_cache(maxsize=1)
def _loaded() -> tuple[Path | None, tuple[dict, ...]]:
    """(the fonts folder, its manifest's file entries). No folder, or a
    manifest that cannot be read: (None, ()), and only Windows fonts exist."""
    for root in _roots():
        manifest = root / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = tuple(
            e for e in data.get("files") or ()
            if isinstance(e, dict) and isinstance(e.get("family"), str) and isinstance(e.get("file"), str)
            and isinstance(e.get("sha256"), str)
        )
        return root, entries
    return None, ()


def reload() -> None:
    """Read the manifest again (tests point _roots at another folder)."""
    _loaded.cache_clear()
    _CHECKED.clear()


def folder() -> Path | None:
    """The bundled fonts folder, or None when this copy has none."""
    return _loaded()[0]


def families() -> list[str]:
    """Every bundled family, as libass and the ASS header name it, in the
    manifest's order."""
    return list(dict.fromkeys(e["family"] for e in _loaded()[1]))


def canonical(name: str | None) -> str | None:
    """The bundled family `name` means, matched without regard to case, or None."""
    wanted = str(name or "").strip().casefold()
    return next((f for f in families() if f.casefold() == wanted), None) if wanted else None


def is_bundled(family: str | None) -> bool:
    return bool(family) and family in families()


def _entries(family: str) -> list[dict]:
    return [e for e in _loaded()[1] if e["family"] == family]


def files_for(family: str) -> list[Path]:
    """The family's files, where they are in this copy (they may be missing)."""
    root = folder()
    if root is None:
        return []
    return [root / e["file"] for e in _entries(family)]


def scripts_for(family: str) -> list[str]:
    """The writing systems the family has every letter of (the manifest's
    "scripts": latin, latin-ext, cyrillic, cyrillic-ext, greek, vietnamese,
    thai; video/alphabets.py). Empty for a font that is not bundled."""
    out: list[str] = []
    for e in _entries(family):
        out += [s for s in e.get("scripts") or () if s not in out]
    return out


def codepoints(family: str) -> frozenset[int] | None:
    """Every character the family's files have (their character maps), or
    None when a file is missing, damaged or cannot be read."""
    from video import fontfile

    root = folder()
    entries = _entries(family)
    if root is None or not entries:
        return None
    have: set[int] = set()
    for e in entries:
        path = root / e["file"]
        font = fontfile.load(path, family) if _matches(path, e["sha256"]) else None
        if font is None:
            return None
        have |= font.codepoints()
    return frozenset(have)


def covers(family: str, letters: str) -> bool | None:
    """Whether the family has every one of `letters`; None when its files
    cannot be read (the caller then goes by the manifest's scripts)."""
    have = codepoints(family)
    if have is None:
        return None
    return all(ord(c) in have for c in letters)


def weight_for(family: str) -> int | None:
    """The family's weight (400 regular, 700 bold, 800 extra bold, 900 black)."""
    weights = [int(e["weight"]) for e in _entries(family) if isinstance(e.get("weight"), int)]
    return max(weights) if weights else None


def _matches(path: Path, sha256: str) -> bool:
    """Whether the file is there and is the one the manifest names."""
    try:
        st = path.stat()
    except OSError:
        return False
    key = (str(path), st.st_size, st.st_mtime_ns, sha256)
    if key not in _CHECKED:
        try:
            _CHECKED[key] = hashlib.sha256(path.read_bytes()).hexdigest() == sha256.lower()
        except OSError:
            return False
    return _CHECKED[key]


def usable(family: str) -> bool:
    """Whether every file of the bundled family is present and unchanged."""
    root = folder()
    entries = _entries(family)
    return bool(root is not None and entries and all(_matches(root / e["file"], e["sha256"]) for e in entries))


_STYLE_FONT = re.compile(r"^Style:\s*[^,]*,([^,]*),", re.MULTILINE)
_OVERRIDE_FONT = re.compile(r"\\fn([^\\}]*)")


def fonts_in_ass(text: str) -> list[str]:
    """Every font an ASS file names: its styles' and any \\fn override's."""
    names = [m.strip() for m in _STYLE_FONT.findall(text)] + [m.strip() for m in _OVERRIDE_FONT.findall(text)]
    return list(dict.fromkeys(n for n in names if n))


def stage(families_used, ass_dir: Path) -> str | None:
    """Copy the files of the bundled families an ASS file uses into
    ass_dir/fonts/. Returns "fonts", the folder to give the subtitles filter
    as fontsdir, or None when none of them is bundled (Windows fonts only) or
    none could be staged. A file that is missing or does not match its
    sha256 is not copied, and nothing here raises."""
    wanted = [f for f in dict.fromkeys(families_used or ()) if is_bundled(f)]
    root = folder()
    if not wanted or root is None:
        return None
    dest = Path(ass_dir) / STAGED
    staged = False
    with _LOCK:
        for family in wanted:
            entries = _entries(family)
            if not all(_matches(root / e["file"], e["sha256"]) for e in entries):
                print(f"      The caption font {family} is missing or damaged in this copy of the app; "
                      f"it was left out")
                continue
            try:
                dest.mkdir(parents=True, exist_ok=True)
                for e in entries:
                    src = root / e["file"]
                    target = dest / src.name
                    if _matches(target, e["sha256"]):
                        continue        # staged already, by this clip's neighbour
                    # A short name: the copy must not need a longer path than
                    # the clip's own files, or a long clip folder loses its font.
                    part = target.with_name(f"~{os.getpid():x}{threading.get_ident():x}.tmp")
                    shutil.copyfile(src, part)
                    os.replace(part, target)
                staged = True
            except OSError as e:
                if len(str(dest)) > 220:
                    print(f"      The caption font {family} could not be copied because this clip's folder "
                          f"path is too long for Windows, so the captions use Arial")
                else:
                    print(f"      Could not get the caption font {family} ready ({e}); it was left out")
    return STAGED if staged else None


def subtitles_filter(ass_path: Path) -> str:
    """FFmpeg's subtitles filter for an ASS file burned from its own folder
    (the filter is given the bare name, and FFmpeg's working folder is the
    file's). `:fontsdir=fonts` is added only when the file uses a bundled
    font and its files were staged; for Windows fonts the text is exactly
    "subtitles=<name>", as it always was."""
    ass_path = Path(ass_path)
    used: list[str] = []
    if families():
        try:
            used = fonts_in_ass(ass_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            used = []
    fontsdir = stage(used, ass_path.parent) if any(is_bundled(f) for f in used) else None
    return f"subtitles={ass_path.name}" + (f":fontsdir={fontsdir}" if fontsdir else "")


def release(ass_dir: Path) -> None:
    """Remove ass_dir/fonts once no ASS file is left in ass_dir: clips render
    side by side in one folder, and each burns from the same staged copy.
    Only files the manifest names are removed, then the folder if it is
    empty. Never raises."""
    dest = Path(ass_dir) / STAGED
    names = {Path(e["file"]).name for e in _loaded()[1]}
    with _LOCK:
        try:
            if not dest.is_dir() or any(Path(ass_dir).glob("*.ass")):
                return
            for item in dest.iterdir():
                if item.is_file() and (item.name in names or item.name.endswith(".part")
                                       or (item.name.startswith("~") and item.name.endswith(".tmp"))):
                    item.unlink()
            dest.rmdir()
        except OSError:
            pass        # in use or not empty: left for later, harmless


def listing() -> list[dict]:
    """The bundled families for the UI: name, weight, scripts and file names
    (the /fonts routes serve them)."""
    out = []
    for family in families():
        out.append({
            "family": family,
            "weight": weight_for(family),
            "scripts": scripts_for(family),
            "files": [Path(e["file"]).name for e in _entries(family)],
            "licence": "OFL-1.1",
        })
    return out


def file_named(name: str) -> Path | None:
    """The bundled file the manifest names `name` (a bare file name), if it
    is present and unchanged. Anything else, a path included: None."""
    root = folder()
    if root is None or not name or name != Path(name).name or "/" in name or "\\" in name:
        return None
    for e in _loaded()[1]:
        if Path(e["file"]).name == name:
            path = root / e["file"]
            return path if _matches(path, e["sha256"]) else None
    return None
