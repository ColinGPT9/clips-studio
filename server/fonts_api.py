"""The bundled caption fonts and the caption presets, for the UI.

- GET /fonts lists the fonts that ship with the app (video/fonts.py): each
  family's name, weight, the writing systems it covers, and its files' URLs.
- GET /fonts/{file} serves one of those files, so the caption preview shows
  the very file the render burns. Only a bare file name the manifest lists,
  present and matching its sha256; anything else, a path included, is 404.
- GET /caption-presets returns config/caption_presets.json: named looks, each
  a caption_style.
- The list's `system` (editor suite step 9, 9-D9): the Windows caption fonts
  (video/captions.py WINDOWS_FONTS) this PC has, each family's regular and
  bold files, served by GET /fonts/system/{file}, so the new editor's live
  captions (JASSUB) draw Arial, Arial Black, Impact... with the very files
  the burn uses, not a stand-in. Only the file names below, from this PC's
  Windows font folders; any other name is 404. Those files are Windows', not
  the app's: they and the list's `system` part are for the app on this PC
  only (server/projects_api.local_origin, 9-D15); another web page's Origin
  gets 403 and an empty `system`.

The bundled fonts and the presets are read-only and public, like the clip
media routes: they serve files that ship inside the app and nothing a
person made.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from server.projects_api import local_origin
from video import caption_fit, fonts

PRESETS_PATH = Path(__file__).resolve().parent.parent / "config" / "caption_presets.json"

# The files never change inside one version of the app, and a new version's
# files have their own sha256: a year's cache is safe.
_CACHE = "public, max-age=31536000, immutable"


# The files of each Windows caption font: its regular face and the bold one
# libass picks with a style's Bold flag (video/caption_fit.py _WINDOWS_FILES).
SYSTEM_FACES = {
    "Arial": ("arial.ttf", "arialbd.ttf"),
    "Arial Black": ("ariblk.ttf",),
    "Impact": ("impact.ttf",),
    "Verdana": ("verdana.ttf", "verdanab.ttf"),
    "Tahoma": ("tahoma.ttf", "tahomabd.ttf"),
    "Trebuchet MS": ("trebuc.ttf", "trebucbd.ttf"),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf"),
    "Georgia": ("georgia.ttf", "georgiab.ttf"),
    "Comic Sans MS": ("comic.ttf", "comicbd.ttf"),
    "Courier New": ("cour.ttf", "courbd.ttf"),
    "Nirmala UI": ("Nirmala.ttf", "NirmalaB.ttf"),
    "Yu Gothic UI": ("YuGothR.ttc", "YuGothB.ttc"),
    "Malgun Gothic": ("malgun.ttf", "malgunbd.ttf"),
    "Microsoft YaHei": ("msyh.ttc", "msyhbd.ttc"),
    "Leelawadee UI": ("LeelawUI.ttf", "LeelaUIb.ttf"),
}


def system_file(name: str) -> Path | None:
    """The Windows font file `name` (one of SYSTEM_FACES' names, exactly)
    from this PC's font folders, or None."""
    if not any(name in files for files in SYSTEM_FACES.values()):
        return None
    for folder in caption_fit._windows_fonts():
        path = folder / name
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def system_fonts() -> list[dict]:
    """The Windows caption fonts this PC has: [{family, files, urls}]."""
    out = []
    for family, files in SYSTEM_FACES.items():
        have = [name for name in files if system_file(name) is not None]
        if have:
            out.append({"family": family, "files": have, "urls": [f"/fonts/system/{name}" for name in have]})
    return out


def presets() -> dict:
    """The caption presets file, or an empty list when it cannot be read."""
    try:
        data = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"presets": []}
    return data if isinstance(data, dict) else {"presets": []}


def install(app: FastAPI) -> None:
    @app.get("/fonts")
    def list_fonts(request: Request):
        out = []
        for entry in fonts.listing():
            out.append({**entry, "urls": [f"/fonts/{name}" for name in entry["files"]]})
        # 9-D15: which Windows fonts this PC has only for the app itself.
        mine = local_origin(request.headers.get("origin"), request.headers.get("host"))
        return {"fonts": out, "system": system_fonts() if mine else []}

    @app.get("/fonts/system/{file}")
    def system_font_file(file: str, request: Request):
        # 9-D15: Windows' own font files are not the app's to hand out: only
        # the app on this PC may read them (the project media routes' Origin
        # check), never another web page.
        origin = request.headers.get("origin")
        if not local_origin(origin, request.headers.get("host")):
            raise HTTPException(403, "This PC's fonts can only be read from the Clips Kitty app on this PC.")
        path = system_file(file)
        if path is None:
            raise HTTPException(404, "This PC has no Windows caption font file of that name.")
        kind = "font/collection" if path.suffix.lower() == ".ttc" else "font/ttf"
        # JASSUB's worker fetches them in CORS mode: the app's own origin
        # ("null" or "file://" in the packaged app, the dev server's) is
        # named back, never "*". A day's cache: Windows updates may change a file.
        headers = {"Cache-Control": "private, max-age=86400", "Vary": "Origin"}
        if origin is not None:
            headers["Access-Control-Allow-Origin"] = origin.strip()
        return FileResponse(path, media_type=kind, headers=headers)

    @app.get("/fonts/{file}")
    def font_file(file: str):
        path = fonts.file_named(file)
        if path is None:
            raise HTTPException(404, "No bundled font file has that name.")
        # A web font is fetched in CORS mode, and the packaged app's page
        # (file://) has no origin the CORS settings list. The files are the
        # app's own public fonts, so any page on this PC may read them.
        return FileResponse(path, media_type="font/ttf",
                            headers={"Cache-Control": _CACHE, "Access-Control-Allow-Origin": "*"})

    @app.get("/caption-presets")
    def caption_presets():
        return presets()
