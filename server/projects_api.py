"""The new editor's projects (editor suite step 5): the routes over
video_editor/project/ (model.py the document, ops.py the operations,
store.py where they are kept). Engine only: no screen uses them yet, and
nothing here runs unless one of these routes is called.

Every route is internal (server/api_stability.py) and needs both:
- the desktop app's session secret in the X-Clips-Kitty-Session header, the
  one the plugin, export and campaign routes take. Reading a project needs
  it too: a document can name a file on this PC (music a person picked);
- an Origin that is the app's own: none at all (a script), the packaged
  window's (file:// or null), the dev server's, or the engine's own address.
  A web page that somehow held the secret still carries its own http(s)
  origin, and is refused.

A change says which revision it was made on (expected_revision). When the
project has moved on, the answer is 409 with the revision it is at now, and
nothing has changed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StrictInt

from plugins import session
from video_editor.project import importer, model, ops, store
from video_editor.project.env import AppEnv

# The Vite dev server, the same two origins server/api.py's CORS lets in.
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def local_origin(origin: str | None, host: str | None = None) -> bool:
    """Whether a request's Origin header may use the project routes.

    Let through: no Origin at all (a script, or the app's main process), the
    packaged app's window, which loads a file (ui/src/main/index.ts
    `win.loadFile`) and so sends "file://" or "null", the dev server's
    origin, and the engine's own (`host` is the request's Host header, which
    server/api.py's TrustedHostMiddleware has already held to this PC).

    Refused: every other http(s) origin, a page on another port of this PC
    included, and anything that is none of the above. "null" is also what a
    sandboxed frame on a website sends; that page is kept out by the session
    secret, which it can't read, not by this check."""
    if origin is None:
        return True
    origin = origin.strip().lower()
    if origin in ("", "null", "file://"):
        return True
    if origin in DEV_ORIGINS:
        return True
    host = (host or "").strip().lower()
    if not host:
        return False
    try:
        parts = urlsplit(origin)
        _ = parts.port      # raises for a port that is no port
    except ValueError:
        return False
    return parts.scheme == "http" and parts.netloc == host and "@" not in parts.netloc


class CanvasIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int
    height: int


class ProjectIn(BaseModel):
    """An empty project. `canvas` is 9:16, 16:9, 1:1 or 4:5, or a size of
    its own; `fps` a number (23.976, 29.97 and 59.94 are the broadcast
    rates) or {num, den}."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    canvas: str | CanvasIn = "9:16"
    fps: Any = None


class OpsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: StrictInt       # a whole number as JSON has it: never true, "1" or 1.0
    ops: list[Any]
    dry_run: bool = False
    label: str = ""


class StepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: StrictInt


def install(app, *, db, data_dir: Path, session_secret: str) -> None:
    def guard(request: Request) -> None:
        if not session.matches(session_secret, request.headers.get(session.HEADER)):
            raise HTTPException(403, f"This needs the {session.HEADER} header. The desktop app sends it; a "
                                     f"script finds it in {session.path(data_dir)}.")
        if not local_origin(request.headers.get("origin"), request.headers.get("host")):
            raise HTTPException(403, "Projects can only be opened from the Clips Kitty app on this PC.")

    guarded = [Depends(guard)]

    def sent(payload) -> Response:
        """The answer written once, as JSON. A project's document is plain
        JSON already; FastAPI's own pass over every value of it took most of
        an operation's time at 300 items."""
        return Response(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                        media_type="application/json")

    def answer(call, *args, **kwargs):
        """A store call, with what it refuses as the HTTP answer for it."""
        try:
            return call(*args, **kwargs)
        except store.NotFound as e:
            raise HTTPException(404, str(e)) from e
        except store.Conflict as e:
            raise HTTPException(409, {"message": str(e), "current_revision": e.current}) from e
        except store.ReadOnly as e:
            raise HTTPException(423, str(e)) from e
        except store.NothingToDo as e:
            raise HTTPException(400, str(e)) from e
        except ops.OpError as e:
            raise HTTPException(400, {"message": str(e), "op": e.op, "field": e.field}) from e
        except store.Damaged as e:
            raise HTTPException(500, str(e)) from e
        except store.StoreError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/clips/{clip_id}/project", dependencies=guarded)
    def project_from_clip(clip_id: int):
        """The clip's project: made from the clip as it is saved the first
        time, and the same project every time after ("created" says which).
        The clip itself is not changed."""
        d = db()
        try:
            row = d.get_clip(clip_id)
        except OverflowError:
            row = None
        if row is None:
            raise HTTPException(404, "There is no such clip.")
        existing = store.for_clip(d.conn, clip_id)
        if existing is not None:
            return sent({**answer(store.get, d.conn, existing), "created": False})
        try:
            facts = AppEnv(db=d, data_dir=data_dir).video_facts(row["video_id"])
            document = importer.import_clip(importer.clip_of_row(row), facts)
        except (importer.ClipImportError, ValueError, TypeError) as e:
            raise HTTPException(400, f"This clip can't be opened as a project: {e}") from e
        return sent(answer(store.create, d.conn, document, clip_id=clip_id, video_id=row["video_id"]))

    @app.post("/projects", dependencies=guarded)
    def new_project(body: ProjectIn):
        """An empty project: V1, a text track, A1 and a music track."""
        if isinstance(body.canvas, str):
            if body.canvas not in model.CANVAS_PRESETS:
                raise HTTPException(400, f"canvas is one of {', '.join(model.CANVAS_PRESETS)}, or "
                                         "{width, height}.")
            width, height = model.CANVAS_PRESETS[body.canvas]
        else:
            width, height = body.canvas.width, body.canvas.height
        name = ops.clean_text(body.name).replace("\n", " ")
        if len(name) > model.NAME_MAX:
            raise HTTPException(400, f"name is at most {model.NAME_MAX} characters.")
        try:
            document = model.new_project(name=name, width=width, height=height, fps=body.fps)
        except model.ModelError as e:
            raise HTTPException(400, f"That project can't be made: {e}.") from e
        return sent(answer(store.create, db().conn, document))

    @app.get("/projects/{project_id}", dependencies=guarded)
    def project(project_id: str):
        """The project as it is now: its document, its revision, and whether
        there is something to undo or redo."""
        return sent(answer(store.get, db().conn, project_id))

    @app.post("/projects/{project_id}/ops", dependencies=guarded)
    def project_ops(project_id: str, body: OpsIn):
        """Apply operations (video_editor/project/ops.py) as one undo step,
        all or nothing. dry_run checks them and answers what would happen,
        saving nothing. 409 when the project is no longer at
        expected_revision."""
        if len(body.ops) > ops.MAX_OPS:
            raise HTTPException(400, {"message": f"One call takes at most {ops.MAX_OPS} operations.",
                                      "op": "ops", "field": "ops"})
        d = db()
        return sent(answer(store.apply_ops, d.conn, project_id, body.expected_revision, body.ops,
                           AppEnv(db=d, data_dir=data_dir), label=body.label, dry_run=body.dry_run))

    @app.post("/projects/{project_id}/undo", dependencies=guarded)
    def project_undo(project_id: str, body: StepIn):
        """Take the last change back."""
        return sent(answer(store.undo, db().conn, project_id, body.expected_revision))

    @app.post("/projects/{project_id}/redo", dependencies=guarded)
    def project_redo(project_id: str, body: StepIn):
        """Make the change undo took back again."""
        return sent(answer(store.redo, db().conn, project_id, body.expected_revision))

    @app.get("/projects/{project_id}/revisions", dependencies=guarded)
    def project_revisions(project_id: str, limit: int = 100):
        """The project's history, newest first."""
        return sent(answer(store.revisions, db().conn, project_id, limit))

    @app.delete("/projects/{project_id}", dependencies=guarded)
    def delete_project(project_id: str):
        """Remove a project and its history. Its clip and its media stay."""
        if not answer(store.delete, db().conn, project_id):
            raise HTTPException(404, "There is no such project.")
        return {"deleted": project_id}
