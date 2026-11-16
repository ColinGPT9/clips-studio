"""A project's export (editor suite step 7): the routes over the export
compiler (video_editor/compose.py). Engine only: no screen uses them yet,
and nothing here runs unless one of these routes is called.

Every route is internal (server/api_stability.py lists none of them) and is
protected exactly as the project routes are (projects_api.session_guard):
the X-Clips-Kitty-Session header and an Origin of this PC's own.

    POST /projects/{id}/export {revision, preset}
    GET  /projects/{id}/exports

An export is a job on the editor lane (server/editor_lane.py), listed with
step 1's exports (GET /export/jobs, "export" events, POST
/export/jobs/{id}/cancel). Which branch makes it:

- **Branch A**: the project still projects to today's render options
  (video_editor/project/projection.py) and the preset is the clip's own
  (Original). The job is today's re-render of the clip
  (server/jobs.py Worker._rerender_clip) with `render_opts_replace`, so the
  file is the one Apply makes. It replaces the clip, as Apply does.
- **Branch B**: everything else. The compiler's plan, run on the lane. The
  file goes to data/clips/projects/<project id>-r<revision>.mp4; a project
  made from a clip that still exists has that clip's file replaced by it (the
  clip keeps its id, title and posts). A project with no clip only gets the
  file and its row; when the request names the person's export folder
  (`folder`, Settings' Export folder), such a project's file goes there
  instead, named after the project and the day (step 8's UX fix:
  "own_clip_45s - 2026-11-15.mp4").

Either way the new file is complete and registered before the old one is
deleted, and a failed or cancelled export leaves the clip, the project and
every source as they were. Every export is a row of project_exports.

docs/editor-suite/api/step7-compiler.md has the fields and answers.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StrictInt

from core.paths import discard
from core.state import StateDB, _now
from plugins import session
from server.editor_lane import unique_target
from server.events import broadcaster
from server.media_api import media_token, serve_file
from server.projects_api import local_origin, session_guard
from video import export_presets
from video_editor import compose
from video_editor.media import cache, resolver
from video_editor.project import importer, projection, store

PROJECTS_FOLDER = ("clips", "projects")      # under the data folder (core/housekeeping.PROJECT_EXPORTS)


class ExportIn(BaseModel):
    """`revision`: the project's revision the export is of; it must be the
    current one (409 otherwise), and absent means the current one. `preset`:
    an export preset (video/export_presets.py), "original" or null for the
    clip's own encode, "default" for the one saved in Settings → Export;
    absent means the project's own (document.export.preset)."""

    model_config = ConfigDict(extra="forbid")

    revision: StrictInt | None = None
    preset: Any = None
    # The project of a clip that was changed in today's editor since the
    # project was made or last exported (8-D6): exporting would replace the
    # clip's file with the project's older version of it. Refused (409,
    # reason "clip_changed") unless the person chose that.
    replace_clip_changes: bool = False
    # The person's export folder (Settings' Export folder, as the clip
    # exports send it). Used only for a project with no clip (made by hand,
    # or whose clip was deleted): its file goes there, named after the
    # project and the day. A project made from a clip that exists replaces
    # that clip's file, as before. Absent, or not an absolute path: the
    # file stays in data/clips/projects/ as <project id>-r<revision>.mp4.
    folder: str | None = None


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def export_stem(name: str, day: str) -> str:
    """The file name (without .mp4) of a project export that goes to the
    person's export folder: the project's name and the day, made safe as a
    Windows file name ("own_clip_45s - 2026-11-15"). A project without a
    usable name is called "Project". A video's extension at the end of the
    name (an imported file's project is named after the file) is left out,
    so the file is never "own_clip_45s.mp4 - 2026-11-15.mp4"."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name or "")).strip()
    cleaned = re.sub(r"\.(mp4|mov|m4v|mkv|webm|avi|flv|ts|wmv)$", "", cleaned, flags=re.IGNORECASE).strip().rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned)[:80].strip().rstrip(". ") or "Project"
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = f"{cleaned} project"
    return f"{cleaned} - {day}"


class DraftIn(BaseModel):
    """`revision`: the project's current revision (409 otherwise); absent
    means the current one."""

    model_config = ConfigDict(extra="forbid")

    revision: StrictInt | None = None


DRAFT_PRODUCT, STILL_PRODUCT = "draft", "stills"     # in data/media_cache/project-<id>/ (cache.PRODUCTS)
STILLS_KEPT = 200                                    # stills kept per project; the oldest go first
SUPERSEDED = "A newer still of this project was asked for."


class StillSlots:
    """One still's FFmpeg at a time per project, and a newer request stops
    an older one (step 7's fault, fixed in step 8: scrubbing started an
    FFmpeg per request, with no limit). Each request takes a ticket; a
    newer ticket cancels the task of the one running and makes every older
    one that is still waiting give up."""

    def __init__(self):
        self._lock = threading.Lock()
        self._slots: dict[str, dict] = {}

    def ticket(self, project_id: str) -> tuple[dict, int]:
        """A new ticket for `project_id`: stops the still being made for it."""
        with self._lock:
            slot = self._slots.setdefault(project_id, {"newest": 0, "task": None, "lock": threading.Lock()})
            slot["newest"] += 1
            task = slot["task"]
        if task is not None:
            task.cancel()
        return slot, slot["newest"]

    def current(self, slot: dict, ticket: int) -> bool:
        with self._lock:
            return slot["newest"] == ticket

    def running(self, slot: dict, task) -> None:
        with self._lock:
            slot["task"] = task

    def done(self, slot: dict, task) -> None:
        with self._lock:
            if slot["task"] is task:
                slot["task"] = None


def project_token(secret: str, project_id: str) -> str:
    """The token a project's read routes take in the address (?t=), for an
    <img> or <video> that can't send the session header: step 6's media
    token of `project:<id>`."""
    return media_token(secret, f"project:{project_id}")


def preview_folder(data_dir: Path, project_id: str) -> Path:
    """Where a project's draft and stills are kept: a folder of the editor
    cache (data/media_cache/project-<id>/), never in clips/ or downloads/.
    Clear and the cap count it; a draft is made again when it is gone."""
    return cache.root(data_dir) / f"project-{project_id}"


class ExportFailed(RuntimeError):
    """An export that can't be made, in plain words."""


def media_ids(doc: dict) -> list[str]:
    """The media the export reads: every item's on the video and audio
    tracks. (The captions' media is read as a transcript, not a file:
    caption_facts.)"""
    seen: list[str] = []
    for track in doc.get("tracks") or []:
        if track.get("kind") not in ("video", "audio"):
            continue
        for item in track.get("items") or []:
            if item.get("media_id") and item["media_id"] not in seen:
                seen.append(item["media_id"])
    return seen


def resolve_media(conn, data_dir: Path, project_id: str, doc: dict, *, tracking: bool = False) -> dict:
    """{media id: compose.MediaFile} for what the export reads, each the
    original file (never a proxy, ED7). `tracking`: give AI-framed media its
    cache folder, for the tracking results. Raises resolver.NotFound."""
    from video_editor.project import model

    ai = {i["media_id"] for i in (model.track(doc, model.MAIN_VIDEO) or {}).get("items", [])
          if compose.is_ai_item(doc, i)} if tracking else set()
    out = {}
    for mid in media_ids(doc):
        entry = (doc.get("media") or {}).get(mid)
        if not isinstance(entry, dict):
            raise resolver.NotFound(f"The project has no media {mid!r}.")
        found = resolver.resolve_entry(conn, data_dir, f"project:{project_id}:{mid}", mid, entry)
        folder = str(cache.folder_for(data_dir, found)) if mid in ai else None
        out[mid] = compose.MediaFile(path=str(found.path), kind=entry.get("kind") or found.kind,
                                     width=int(entry.get("width") or 0), height=int(entry.get("height") or 0),
                                     has_audio=bool(entry.get("has_audio", True)), cache=folder)
    return out


_VIDEO_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_PROJECT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
# What an export's file can be, for GET /projects/{id}/exports/{row}/file.
EXPORT_FILE_TYPES = (".mp4", ".mov", ".m4v", ".mkv", ".webm")


def transcript_file(data_dir: Path, doc: dict) -> Path | None:
    """The transcript of the captions' media (data/transcripts/<video
    id>.json), when that media is a video of the library."""
    mid = (doc.get("captions") or {}).get("media_id")
    source = (((doc.get("media") or {}).get(mid) or {}).get("source") or {}) if mid else {}
    video_id = source.get("video_id") if source.get("type") == "video" else None
    if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id) or ".." in video_id:
        return None
    return Path(data_dir) / "transcripts" / f"{video_id}.json"


def caption_facts(data_dir: Path, doc: dict) -> dict:
    """What the captions need besides the document: the transcript of the
    captions' media (transcript_file) as `segments`, and its `language`.
    Nothing for any other media: a project's own caption lines still burn."""
    path = transcript_file(data_dir, doc)
    if path is None:
        return {"segments": None, "language": "en"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"segments": None, "language": "en"}
    segments = data.get("segments") if isinstance(data, dict) else None
    language = str(data.get("language") or "en").lower() if isinstance(data, dict) else "en"
    return {"segments": segments if isinstance(segments, list) else None, "language": language}


def branding_facts(data_dir: Path, doc: dict, config: dict) -> dict:
    """The settings' caption style, captions switch and watermark (what a
    clip's render falls back on), and the image watermark's file, found the
    way watermark.apply_image finds it: by safe name, in the branding assets
    folder, never from a path."""
    from core.paths import safe_name
    from video_editor import watermark

    clips = (config or {}).get("clips") or {}
    style = clips.get("caption_style")
    default_wm = clips.get("watermark") if isinstance(clips.get("watermark"), dict) else None
    branding = doc.get("branding") or {}
    wm = branding["watermark"] if "watermark" in branding else default_wm
    logo = None
    if isinstance(wm, dict) and wm.get("type") in ("image", "both"):
        name = safe_name(str(wm.get("image_asset") or ""))
        found = watermark._asset_in(Path(data_dir) / "branding" / "assets", name) if name else None
        logo = str(found) if found is not None else None
    return {"caption_style": style if isinstance(style, dict) else None,
            "job_captions": bool(clips.get("captions", True)), "watermark": default_wm, "logo": logo}


def _stamp_of(path) -> list | None:
    try:
        st = Path(path).stat()
    except (OSError, TypeError):
        return None
    return [st.st_mtime_ns, st.st_size]


def facts_key(data_dir: Path, doc: dict, config: dict) -> str:
    """A short hash of what a draft or still is made from besides the
    document (fix 7-D6): the settings' caption style, captions switch and
    watermark, the logo's file and the transcript's file (each by its time
    and size, so nothing large is read). It goes into the draft's and the
    stills' cache names, so a change that doesn't raise the revision (the
    settings, a new logo, a transcript made again) makes them again rather
    than serving the old ones."""
    import hashlib

    branding = branding_facts(data_dir, doc, config)
    facts = {"branding": branding, "logo": _stamp_of(branding["logo"]) if branding["logo"] else None,
             "transcript": _stamp_of(transcript_file(data_dir, doc))}
    text = json.dumps(facts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def branch_of(project: dict, preset: dict) -> dict:
    """{"branch": "A", "start", "end", "render_opts"} when today's re-render
    makes this export (the project projects to render options, it has its
    clip, and the preset is the clip's own), else {"branch": "B", "reasons"}."""
    answer = projection.to_clip(project["document"])
    if answer["branch"] == "A":
        if project.get("clip_id") is None:
            return {"branch": "B", "reasons": ["The clip this project was made from is gone."]}
        if preset["kind"] != "original":
            return {"branch": "B", "reasons": ["The export is in a size or format of its own."]}
    return answer


def install(app, *, db, data_dir: Path, session_secret: str, lane, worker=None, config: dict | None = None,
            saved_preset=None) -> None:
    data_dir = Path(data_dir)
    config = config or {}
    guarded = [Depends(session_guard(session_secret, data_dir))]
    db_path = data_dir / "state.db"
    still_slots = StillSlots()
    app.state.still_slots = still_slots

    def sent(payload, status: int = 200) -> Response:
        return Response(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), status_code=status,
                        media_type="application/json")

    def facts(doc: dict) -> dict:
        """The Inputs fields of part C: the transcript, the settings'
        captions and watermark, the logo's file."""
        return {**caption_facts(data_dir, doc), **branding_facts(data_dir, doc, config)}

    def read_guard(request: Request, project_id: str) -> None:
        """A read route (a still, the draft's file): an Origin of this PC's
        own, and the session header or the project's token (?t=), as step
        6's media routes take them."""
        if not local_origin(request.headers.get("origin"), request.headers.get("host")):
            raise HTTPException(403, "A project's pictures can only be opened from the Clips Kitty app on this PC.")
        if session.matches(session_secret, request.headers.get(session.HEADER)):
            return
        given = request.query_params.get("t") or ""
        if given and _PROJECT_ID.fullmatch(project_id or "") and \
                hmac.compare_digest(given, project_token(session_secret, project_id)):
            return
        raise HTTPException(403, f"This needs the {session.HEADER} header, or the address the project's "
                                 "draft or export answer gives.")

    def urls(project_id: str, revision: int) -> dict:
        t = project_token(session_secret, project_id)
        return {"still": f"/projects/{project_id}/still?rev={revision}&t={t}&t_us=",
                "draft": f"/projects/{project_id}/draft?rev={revision}&t={t}"}

    def current(project_id: str, revision) -> tuple[dict, int]:
        """The project and its revision, refused as the export refuses: 404,
        423 for a newer version's, 409 for another revision than the
        current one."""
        d = db()
        try:
            try:
                project = store.get(d.conn, project_id)
            except store.NotFound as e:
                raise HTTPException(404, str(e)) from e
            except store.Damaged as e:
                raise HTTPException(500, str(e)) from e
        finally:
            d.close()
        if project.get("read_only"):
            raise HTTPException(423, "This project was saved by a newer version of Clips Kitty. Update Clips "
                                     "Kitty to see it here.")
        head = int(project["revision"])
        if revision is not None and revision != head:
            raise HTTPException(409, {"message": "The project has changed since that revision.",
                                      "current_revision": head})
        if not _PROJECT_ID.fullmatch(project_id):
            raise HTTPException(404, "There is no such project.")
        return project, head

    def read_preset(body: ExportIn, doc: dict) -> dict:
        raw = body.preset if "preset" in body.model_fields_set else (doc.get("export") or {}).get("preset")
        if raw == "default":
            if saved_preset is None:
                raise HTTPException(400, 'There is no saved export preset to use for "default".')
            return saved_preset()
        try:
            preset = export_presets.normalize(raw)
            if preset["kind"] != "original":
                export_presets.pick_encoder(preset)       # 400 when this PC can't encode it at all
            return preset
        except (export_presets.PresetError, export_presets.ExportError) as e:
            raise HTTPException(400, str(e)) from e

    def export_place(project: dict, clip_id, folder: str | None) -> tuple[Path, str] | None:
        """Where a project with no clip (made by hand, or whose clip was
        deleted) puts its file when the person's export folder is named:
        that folder, and the project's name with the day. None (the app's
        own folder, <project id>-r<revision>) for a clip's project, or when
        no absolute folder is given."""
        if clip_id is not None or not folder or not str(folder).strip():
            return None
        place = Path(str(folder).strip())
        if not place.is_absolute():
            return None
        name = (project.get("document") or {}).get("name") or project.get("name") or ""
        return place, export_stem(name, _now()[:10])

    # ---- the routes --------------------------------------------------------------------------

    @app.post("/projects/{project_id}/export", dependencies=guarded)
    def export_project(project_id: str, body: ExportIn):
        """Export the project as it is at its current revision: a job on the
        editor lane, listed with the exports. Answers at once with the job;
        progress arrives as "export" events."""
        d = db()
        try:
            try:
                project = store.get(d.conn, project_id)
            except store.NotFound as e:
                raise HTTPException(404, str(e)) from e
            except store.Damaged as e:
                raise HTTPException(500, str(e)) from e
            if project.get("read_only"):
                raise HTTPException(423, "This project was saved by a newer version of Clips Kitty, so it "
                                         "can't be exported here. Update Clips Kitty to export it.")
            revision = int(project["revision"])
            if body.revision is not None and body.revision != revision:
                raise HTTPException(409, {"message": "The project has changed since that revision. Export it as "
                                                     "it is now, or undo back to it first.",
                                          "current_revision": revision})
            doc = project["document"]
            clip_now = clip_changed(d, project_id, project.get("clip_id"))
            if clip_now and not body.replace_clip_changes:
                raise HTTPException(409, {
                    "message": "This clip was changed in the clip editor after this project was made. "
                               "Exporting puts the project's version in its place, so those changes "
                               "would be lost.",
                    "reason": "clip_changed", "clip_id": project.get("clip_id")})
            preset = read_preset(body, doc)
            branch = branch_of(project, preset)
            name = f"{project_id}-r{revision}"
            folder = data_dir.joinpath(*PROJECTS_FOLDER)
            notes: list[str] = []
            if branch["branch"] == "B":
                # Checked now, so a project that can't be made is refused
                # here rather than failing on the lane: its media, and its plan.
                try:
                    media = resolve_media(d.conn, data_dir, project_id, doc)
                    plan = compose.compile(doc, revision, preset, "export", inputs=compose.Inputs(
                        work=lane.work_root / "check", media=media, project_id=project_id))
                    notes = plan.notes
                except resolver.NotFound as e:
                    raise HTTPException(404, str(e)) from e
                except compose.CompileError as e:
                    raise HTTPException(400, str(e)) from e
            clip_id = project.get("clip_id")
            out = export_place(project, clip_id, body.folder)
            if out is not None:
                folder, name = out
            export_id = store.add_export(d.conn, project_id, revision, clip_id=clip_id,
                                         preset=None if preset["kind"] == "original" else preset,
                                         status="queued", branch=branch["branch"])
        finally:
            d.close()
        extra = {"project_id": project_id, "revision": revision, "branch": branch["branch"],
                 "project_export_id": export_id}
        if branch["branch"] == "A":
            work = _branch_a(project_id, export_id, revision, int(clip_id), branch)
        else:
            work = _branch_b(project_id, export_id, revision, doc, preset, clip_id, out)
        job = lane.submit_project_export(work, folder=folder, name=name, preset=preset, clip_id=clip_id,
                                         extra=extra,
                                         on_dropped=lambda _job: _record(export_id, status="cancelled"))
        d = db()
        try:
            store.set_export(d.conn, export_id, job_id=job["export_id"])
        finally:
            d.close()
        return sent({"export": job, "branch": branch["branch"], "reasons": branch.get("reasons", []),
                     "project_export_id": export_id, "revision": revision, "notes": notes,
                     "urls": urls(project_id, revision)})

    # ---- the draft and the still -------------------------------------------------------------

    def draft_path(project_id: str, revision: int, doc: dict) -> Path:
        """r<revision>-<facts_key>.mp4: one per revision and settings."""
        key = facts_key(data_dir, doc, config)
        return preview_folder(data_dir, project_id) / DRAFT_PRODUCT / f"r{revision}-{key}.mp4"

    def draft_answer(project_id: str, revision: int, job, path: Path | None) -> dict:
        made = None
        if path is not None and path.is_file():
            made = {"revision": revision, "bytes": path.stat().st_size, "url": urls(project_id, revision)["draft"]}
        return {"job": job, "revision": revision, "draft": made, "urls": urls(project_id, revision)}

    @app.post("/projects/{project_id}/draft", dependencies=guarded)
    def make_draft(project_id: str, body: DraftIn):
        """A quick look at the whole project as it is now (compose purpose
        "draft"): 540 on the short side, x264 ultrafast, no export preset and
        no end card. A task on the editor lane ("media_job" events with its
        fraction and stage); kept per revision in the editor cache, and
        replaced when a newer one is made. Answers at once: `draft` when it
        is already made, else the `job` making it."""
        project, revision = current(project_id, body.revision)
        doc = project["document"]
        path = draft_path(project_id, revision, doc)
        if path.is_file():
            return sent(draft_answer(project_id, revision, None, path))
        d = db()
        try:
            try:
                media = resolve_media(d.conn, data_dir, project_id, doc)
                compose.compile(doc, revision, None, "draft", inputs=compose.Inputs(
                    work=lane.work_root / "check", media=media, project_id=project_id))
            except resolver.NotFound as e:
                raise HTTPException(404, str(e)) from e
            except compose.CompileError as e:
                raise HTTPException(400, str(e)) from e
        finally:
            d.close()

        def work(ctx) -> dict:
            workdir = lane.work_root / f"job-{ctx.job.id}"
            try:
                from core.binaries import ffmpeg

                d = StateDB(db_path)
                try:
                    try:
                        found = resolve_media(d.conn, data_dir, project_id, doc, tracking=True)
                    except resolver.NotFound as e:
                        raise ExportFailed(str(e)) from e
                finally:
                    d.close()
                binary = ffmpeg()
                plan = compose.compile(doc, revision, None, "draft", inputs=compose.Inputs(
                    work=workdir, media=found, ffmpeg=binary, version=compose.ffmpeg_version(binary),
                    project_id=project_id, **facts(doc)))
                made = compose.run_plan(plan, ctx, config=config)
                ctx.check()
                folder = preview_folder(data_dir, project_id)
                target = path
                with cache.hold(folder):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(made["path"], target)
                    cache.write_json(target.parent / "index.json", {"version": 1})
                    # One draft per project: the newer one replaces the rest.
                    for old in target.parent.glob("r*.mp4"):
                        if old != target:
                            discard(old)
                return {"project_id": project_id, "revision": revision, "bytes": target.stat().st_size,
                        "url": urls(project_id, revision)["draft"]}
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

        job = lane.submit_task("project_draft", f"project_draft:{project_id}:r{revision}:{path.stem}", work,
                               name=f"{project_id}-r{revision}",
                               extra={"project_id": project_id, "revision": revision})
        return sent(draft_answer(project_id, revision, job, None))

    @app.get("/projects/{project_id}/draft")
    def get_draft(project_id: str, request: Request, rev: int | None = None):
        """The draft's file (Range and ETag, as step 6's sources): the
        session header or the project's token (?t=). 404 until it is made."""
        read_guard(request, project_id)
        project, revision = current(project_id, rev)
        path = draft_path(project_id, revision, project["document"])
        if not path.is_file():
            raise HTTPException(404, "There is no draft of this revision yet. Make one with POST "
                                     f"/projects/{project_id}/draft.")
        return serve_file(request, path, folder=preview_folder(data_dir, project_id))

    @app.get("/projects/{project_id}/still")
    def get_still(project_id: str, request: Request, t_us: int = 0, rev: int | None = None):
        """One JPEG of the program at `t_us` (compose purpose "still"): the
        frame the export shows then, every layer drawn. Made in the request
        (one short FFmpeg, not queued behind exports on the lane), kept by
        (revision, facts_key, frame time) in the editor cache. An AI item uses the
        tracking an export or a draft kept; without it the still is
        approximate (the middle of the frame) and isn't kept, and the answer
        says so in X-Clips-Kitty-Still-Note."""
        read_guard(request, project_id)
        project, revision = current(project_id, rev)
        doc = project["document"]
        if t_us < 0:
            raise HTTPException(400, "t_us is a time from 0, in microseconds.")
        from video_editor.project import model

        if not (model.track(doc, model.MAIN_VIDEO) or {}).get("items"):
            raise HTTPException(400, "The project has nothing on its main video track.")
        frame_us = model.frame_us(compose.still_frame(doc, t_us), doc["canvas"]["fps"])
        folder = preview_folder(data_dir, project_id)
        kept = folder / STILL_PRODUCT / f"r{revision}-{facts_key(data_dir, doc, config)}-{frame_us}.jpg"
        headers = {"Cache-Control": "no-cache", "X-Clips-Kitty-Still-Time": str(frame_us)}
        # This request is now the newest of the project's: an older still
        # still being made is stopped, and one still waiting gives up.
        slot, ticket = still_slots.ticket(project_id)
        if kept.is_file():
            return Response(kept.read_bytes(), media_type="image/jpeg", headers=headers)
        with slot["lock"]:
            if not still_slots.current(slot, ticket):
                raise HTTPException(409, {"message": SUPERSEDED, "superseded": True})
            return _make_still(project_id, slot, revision, doc, t_us, folder, kept, headers)

    def _make_still(project_id: str, slot: dict, revision: int, doc: dict, t_us: int, folder: Path, kept: Path,
                    headers: dict) -> Response:
        """The still made in the request, one FFmpeg at a time per project
        (the caller holds the project's slot)."""
        d = db()
        try:
            try:
                media = resolve_media(d.conn, data_dir, project_id, doc, tracking=True)
            except resolver.NotFound as e:
                raise HTTPException(404, str(e)) from e
        finally:
            d.close()
        from core.binaries import ffmpeg

        binary = ffmpeg()
        workdir = lane.work_root / f"still-{uuid.uuid4().hex[:12]}"
        inputs = compose.Inputs(work=workdir, media=media, ffmpeg=binary, version=compose.ffmpeg_version(binary),
                                project_id=project_id, **facts(doc))
        try:
            run = compose.still_run(doc, inputs, t_us)
            if run is not None:
                tracking = compose.read_cached_tracking(run[0], config)
                if tracking is not None:
                    inputs.tracking = {"tracking": tracking, "run_start_us": run[1]}
            plan = compose.compile(doc, revision, None, "still", inputs=inputs, at_us=t_us)
            task = compose.LocalTask()
            still_slots.running(slot, task)
            try:
                made = compose.run_plan(plan, task, config=config)
            except export_presets.Cancelled as e:
                raise HTTPException(409, {"message": SUPERSEDED, "superseded": True}) from e
            except compose.StageFailed as e:
                raise HTTPException(500, f"The still couldn't be made: {e}") from e
            finally:
                still_slots.done(slot, task)
            body = Path(made["path"]).read_bytes()
            if plan.notes:
                headers["X-Clips-Kitty-Still-Note"] = "approximate"
            else:
                with cache.hold(folder):
                    kept.parent.mkdir(parents=True, exist_ok=True)
                    part = kept.with_name(f"{kept.stem}.{uuid.uuid4().hex[:8]}.tmp")
                    part.write_bytes(body)
                    os.replace(part, kept)
                    cache.write_json(kept.parent / "index.json", {"version": 1})
                    stills = sorted(kept.parent.glob("r*.jpg"), key=lambda p: p.stat().st_mtime)
                    for old in stills[:-STILLS_KEPT]:
                        discard(old)
            return Response(body, media_type="image/jpeg", headers=headers)
        except compose.CompileError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @app.get("/projects/{project_id}/exports", dependencies=guarded)
    def project_exports(project_id: str):
        """The project's exports, newest first (project_exports rows)."""
        d = db()
        try:
            try:
                store.get(d.conn, project_id)
            except store.NotFound as e:
                raise HTTPException(404, str(e)) from e
            rows = store.exports(d.conn, project_id)
            for row in rows:
                # One the app was closed in the middle of: the lane forgets
                # its jobs at a restart, and the export never finished.
                if row["status"] in ("queued", "running") and row.get("job_id") is not None \
                        and lane.get(int(row["job_id"])) is None:
                    row["status"] = "interrupted"
                    store.set_export(d.conn, row["id"], status="interrupted")
        finally:
            d.close()
        for row in rows:
            try:
                row["preset"] = json.loads(row["preset"]) if row.get("preset") else None
            except ValueError:
                row["preset"] = None
            # Whether the file is still there to play or show (step 8): a
            # later export, an Apply or a person may have replaced or deleted
            # it. A row whose file went is never offered as done, nor one
            # whose path now holds another file (8-D4: today's Apply writes
            # a clip's file in place, and that is a Branch A export's file).
            row["file_exists"] = bool(row.get("path")) and Path(str(row["path"])).is_file()
            if row["status"] == "done" and row["file_exists"] and not store.file_is_its_own(row):
                row["status"] = "replaced"
                _record(int(row["id"]), status="replaced")
            # Where the editor plays it (step 8 part C): only a finished
            # export whose file is there, with the project's token.
            row["url"] = (f"/projects/{project_id}/exports/{int(row['id'])}/file?t="
                          f"{project_token(session_secret, project_id)}"
                          if row["status"] == "done" and row["file_exists"] else None)
        return sent({"exports": rows})

    @app.get("/projects/{project_id}/exports/{export_row}/file")
    def project_export_file(project_id: str, export_row: int, request: Request):
        """A finished export's file, to play it in the editor's export
        dialog (step 8 part C): Range and ETag as step 6's sources, the
        session header or the project's token. Only a row of this project
        whose status is done, a video by its extension, and whose file is
        there; never any other path."""
        read_guard(request, project_id)
        d = db()
        try:
            row = next((r for r in store.exports(d.conn, project_id) if int(r["id"]) == export_row), None)
        finally:
            d.close()
        if row is None or row.get("status") != "done" or not row.get("path"):
            raise HTTPException(404, "This project has no such finished export.")
        path = Path(str(row["path"]))
        if path.suffix.lower() not in EXPORT_FILE_TYPES or not path.is_file():
            raise HTTPException(404, "That export's file is gone.")
        if not store.file_is_its_own(row):
            raise HTTPException(404, "That export's file was replaced by a newer one.")
        return serve_file(request, path)

    # ---- the jobs ----------------------------------------------------------------------------

    def _record(export_id: int, **fields) -> None:
        d = StateDB(db_path)
        try:
            store.set_export(d.conn, export_id, **fields)
        finally:
            d.close()

    def _finish(export_id: int, run) -> dict:
        """Run an export's work, its row following what happened."""
        _record(export_id, status="running")
        try:
            result = run()
        except export_presets.Cancelled:
            _record(export_id, status="cancelled")
            raise
        except Exception as e:
            _record(export_id, status="failed", error=(str(e).strip() or type(e).__name__)[:500])
            raise
        stamp = _stamp(Path(result["path"])) if result.get("path") else None
        fields = {k: result[k] for k in ("path", "bytes", "duration_us", "clip_id") if k in result}
        if stamp:
            fields.update(bytes=stamp[0], file_mtime_ns=stamp[1])
        _record(export_id, status="done", error="", **fields)
        # Earlier exports whose file this one replaced (the clip's old file,
        # or the same name written again) are marked so, never offered as
        # done with a file that is gone or is another export's (step 7's
        # fault, fixed in step 8).
        d = StateDB(db_path)
        try:
            store.mark_replaced(d.conn, [result.get("path"), result.get("replaced")], keep=export_id)
        finally:
            d.close()
        return result

    def _branch_b(project_id: str, export_id: int, revision: int, doc: dict, preset: dict, clip_id,
                  out: tuple[Path, str] | None = None):
        def work(ctx) -> dict:
            workdir = lane.work_root / f"job-{ctx.job.id}"

            def run() -> dict:
                from core.binaries import ffmpeg
                from video import encoding, outro

                d = StateDB(db_path)
                try:
                    try:
                        media = resolve_media(d.conn, data_dir, project_id, doc, tracking=True)
                    except resolver.NotFound as e:
                        raise ExportFailed(str(e)) from e
                    # The clip's file as the export found it (7-D1): the
                    # register step replaces only this file, never one a
                    # re-render of the clip wrote meanwhile.
                    row = d.get_clip(int(clip_id)) if clip_id is not None else None
                    start_file = (row["path"], _stamp(Path(row["path"]))) if row and row["path"] else None
                finally:
                    d.close()
                binary = ffmpeg()
                inputs = compose.Inputs(
                    work=workdir, media=media, ffmpeg=binary, version=compose.ffmpeg_version(binary),
                    encoder=None if preset["kind"] == "original" else export_presets.pick_encoder(preset),
                    render_args=encoding.video_encoder_args() if preset["kind"] == "original" else None,
                    card_seconds=outro.DURATION if outro.enabled(config) else None, project_id=project_id,
                    **facts(doc))
                plan = compose.compile(doc, revision, preset, "export", inputs=inputs)
                made = compose.run_plan(plan, ctx, config=config)
                ctx.check()
                return _register_b(project_id, revision, Path(made["path"]), clip_id, made, ctx, start_file,
                                   export_id=export_id, out=out)

            try:
                return _finish(export_id, run)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

        return work

    def _register_b(project_id: str, revision: int, made: Path, clip_id, outcome: dict,
                    ctx=None, start_file=None, export_id: int | None = None,
                    out: tuple[Path, str] | None = None) -> dict:
        """The finished file into data/clips/projects/, then the clip pointed
        at it, then the clip's old file deleted: in that order, so nothing is
        lost if any step fails.

        For a clip's project this holds the clip's lock (jobs.clip_lock, as
        Branch A and today's Apply do; 7-D1), taken before the file moves so a
        cancel while waiting leaves nothing behind. Under the lock the row is
        read again: if the clip is gone, or its file is no longer the one the
        export started from (an Apply re-rendered it meanwhile), the newer
        render wins and the clip is not touched; the export stays a project
        export in clips/projects/ (recorded in project_exports)."""
        if clip_id is None:
            return _register_b_locked(project_id, revision, made, None, outcome, start_file, export_id, out)
        from server.jobs import clip_lock

        lock = clip_lock(clip_id)
        while not lock.acquire(timeout=0.5):
            if ctx is not None:
                ctx.check()
        try:
            return _register_b_locked(project_id, revision, made, clip_id, outcome, start_file, export_id)
        finally:
            lock.release()

    def _register_b_locked(project_id: str, revision: int, made: Path, clip_id, outcome: dict,
                           start_file, export_id: int | None = None,
                           out: tuple[Path, str] | None = None) -> dict:
        folder, stem = data_dir.joinpath(*PROJECTS_FOLDER), f"{project_id}-r{revision}"
        if out is not None and clip_id is None:
            # A project with no clip, and the person's export folder named:
            # the file goes there, named after the project (step 8's UX
            # fix). If that folder can't be made, the app's own folder.
            try:
                out[0].mkdir(parents=True, exist_ok=True)
                folder, stem = out
            except OSError:
                pass
        folder.mkdir(parents=True, exist_ok=True)
        target = unique_target(folder, stem)
        if export_id is not None:
            # The row names the file before it is there, so a crash between
            # the move and the clip's update leaves a file that its export
            # row names, never one nothing names (step 7's fault, fixed in
            # step 8). Storage never sweeps this folder.
            _record(export_id, path=str(target))
        try:
            os.replace(made, target)
        except OSError:
            shutil.move(str(made), str(target))
        try:
            duration_us = int(round(export_presets.probe(target).duration * 1_000_000))
        except export_presets.ExportError:
            duration_us = 0
        result = {"path": str(target), "file": target.name, "folder": str(target.parent),
                  "bytes": target.stat().st_size,
                  "duration_us": duration_us, "retried": outcome.get("retried", False),
                  "fell_back_to_cpu": outcome.get("fell_back_to_cpu", False)}
        if clip_id is None:
            return result
        d = StateDB(db_path)
        try:
            row = d.get_clip(int(clip_id))
            if row is None:
                return result
            now_file = (row["path"], _stamp(Path(row["path"]))) if row["path"] else None
            if now_file != start_file:
                return result       # re-rendered while the export ran: the newer file stays
            old = Path(row["path"]) if row["path"] else None
            d.set_clip(int(clip_id), path=str(target), edited_at=_now())
            # The person exported over the clip as it is now (8-D6): from
            # here the project is compared with that.
            store.set_clip_basis(d.conn, project_id, importer.clip_basis(row), revision=None)
            result["clip_id"] = int(clip_id)
            if old is not None:
                result["replaced"] = str(old)
        finally:
            d.close()
        if old is not None and old != target and old.exists() and _inside(data_dir, old):
            discard(old)
        discard(data_dir / "previews" / f"clip_{int(clip_id)}.mp4")
        broadcaster.publish({"type": "clip_file_replaced", "clip_id": int(clip_id), "project_id": project_id})
        return result

    def _branch_a(project_id: str, export_id: int, revision: int, clip_id: int, branch: dict):
        def work(ctx) -> dict:
            workdir = lane.work_root / f"job-{ctx.job.id}"

            def run() -> dict:
                from server.jobs import Worker

                d = StateDB(db_path)
                try:
                    row = d.get_clip(clip_id)
                    if row is None:
                        raise ExportFailed("The clip this project was made from is gone.")
                    old = Path(row["path"]) if row["path"] else None
                    backup = None
                    before = _stamp(old)
                    if old is not None and old.is_file():
                        # Today's re-render may write the clip's own file
                        # in place (the same window, the same name): a copy
                        # puts it back if the export fails or is cancelled.
                        workdir.mkdir(parents=True, exist_ok=True)
                        backup = workdir / f"before{old.suffix}"
                        shutil.copyfile(old, backup)
                    rendered: list[Path] = []

                    def before_register(final_path) -> None:
                        rendered.append(Path(final_path))
                        ctx.check()       # a cancel asked for during the render stops it here

                    payload = {"clip_id": clip_id, "start": branch["start"], "end": branch["end"],
                               "render_opts": branch["render_opts"], "render_opts_replace": True}
                    owner = worker if worker is not None else _ConfigOnly(config)
                    ctx.progress(0.0, {"stage": "render"})
                    try:
                        new_id = Worker._rerender_clip(owner, d, payload, before_register=before_register,
                                                       while_waiting=ctx.check)
                    except BaseException:
                        for path in rendered:
                            if old is None or path.resolve() != old.resolve():
                                discard(path)
                        if old is not None and backup is not None and _stamp(old) != before:
                            shutil.copyfile(backup, old)
                        raise
                    new = d.get_clip(new_id) if new_id is not None else None
                    if new is not None:
                        # The clip is now the project's window and options
                        # at the exported revision, not at the head now:
                        # edits made during the export stay "changes of
                        # its own" (8-D6, 8-D12).
                        store.set_clip_basis(d.conn, project_id, importer.clip_basis(new), revision=revision)
                finally:
                    d.close()
                if new is None:
                    raise ExportFailed("The render registered no clip.")
                path = Path(new["path"])
                try:
                    duration_us = int(round(export_presets.probe(path).duration * 1_000_000))
                except export_presets.ExportError:
                    duration_us = 0
                return {"path": str(path), "file": path.name, "bytes": path.stat().st_size,
                        "duration_us": duration_us, "clip_id": int(new_id),
                        "replaced": str(old) if old is not None else None}

            try:
                return _finish(export_id, run)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

        return work


def clip_changed(d, project_id: str, clip_id) -> bool:
    """Whether the clip a project was made from was changed since the
    project was made or last exported (8-D6): its window or render options
    are no longer what the project recorded. False for a project made by
    hand, one whose clip is gone, and one made before this was recorded."""
    if clip_id is None:
        return False
    basis, _unchanged = store.clip_basis(d.conn, project_id)
    if not basis:
        return False
    try:
        row = d.get_clip(int(clip_id))
    except (OverflowError, ValueError, TypeError):
        return False
    return row is not None and importer.clip_basis(row) != basis


class _ConfigOnly:
    """What Worker._rerender_clip reads from its worker: the config."""

    def __init__(self, config: dict):
        self.config = config


def _stamp(path: Path | None):
    try:
        st = path.stat()
        return st.st_size, st.st_mtime_ns
    except (AttributeError, OSError):
        return None


def _inside(base: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (OSError, ValueError):
        return False
