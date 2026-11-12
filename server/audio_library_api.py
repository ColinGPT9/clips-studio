"""The bundled free music and sound effects, for the UI.

- GET /assets/library lists what ships with the app (video/audio_library.py):
  each item's id, kind ("music" or "sfx"), title, mood, length in seconds and
  the URL of its file.
- GET /assets/library/{file} serves one of those files, so the editor plays
  the very file the render mixes in. Only a bare file name the manifest
  lists, present and matching its sha256; anything else, a path, the
  manifest or a licence file included, is 404.

Read-only and public, like the /fonts routes: they serve files that ship
inside the app and nothing a person made.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from video import audio_library

# The files never change inside one version of the app, and a new version's
# files have their own sha256: a year's cache is safe.
_CACHE = "public, max-age=31536000, immutable"
_TYPES = {".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav"}


def install(app: FastAPI) -> None:
    @app.get("/assets/library")
    def list_library():
        return {"items": [{**entry, "url": f"/assets/library/{entry['file']}"}
                          for entry in audio_library.listing()]}

    @app.get("/assets/library/{file}")
    def library_file(file: str):
        found = audio_library.file_named(file)
        if found is None:
            raise HTTPException(404, "No bundled sound has that name.")
        path, _entry = found
        return FileResponse(path, media_type=_TYPES.get(path.suffix.lower(), "application/octet-stream"),
                            headers={"Cache-Control": _CACHE, "Access-Control-Allow-Origin": "*"})
