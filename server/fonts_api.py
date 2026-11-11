"""The bundled caption fonts and the caption presets, for the UI.

- GET /fonts lists the fonts that ship with the app (video/fonts.py): each
  family's name, weight, the writing systems it covers, and its files' URLs.
- GET /fonts/{file} serves one of those files, so the caption preview shows
  the very file the render burns. Only a bare file name the manifest lists,
  present and matching its sha256; anything else, a path included, is 404.
- GET /caption-presets returns config/caption_presets.json: named looks, each
  a caption_style.

Read-only and public, like the clip media routes: they serve files that ship
inside the app and nothing a person made.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from video import fonts

PRESETS_PATH = Path(__file__).resolve().parent.parent / "config" / "caption_presets.json"

# The files never change inside one version of the app, and a new version's
# files have their own sha256: a year's cache is safe.
_CACHE = "public, max-age=31536000, immutable"


def presets() -> dict:
    """The caption presets file, or an empty list when it cannot be read."""
    try:
        data = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"presets": []}
    return data if isinstance(data, dict) else {"presets": []}


def install(app: FastAPI) -> None:
    @app.get("/fonts")
    def list_fonts():
        out = []
        for entry in fonts.listing():
            out.append({**entry, "urls": [f"/fonts/{name}" for name in entry["files"]]})
        return {"fonts": out}

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
