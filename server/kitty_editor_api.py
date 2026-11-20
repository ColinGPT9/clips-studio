"""Clips Kitty Editor's routes (editor suite step K2): where the editor is,
its projects for a clip, a whole video or a file from the PC, and taking
its exports back in. The work is in kitty_editor/; the contract is
docs/editor-suite/api/k2-editor.md.

Every route is internal (server/api_stability.py) and guarded like the
project routes (server/projects_api.py): the desktop app's session secret in
the X-Clips-Kitty-Session header, and an Origin that is the app's own.

The engine takes no path from a client except a file the person picked
(checked by core.paths.picked_file, as POST /videos/local checks it) and
their export folder; an export is named by its file name only and is looked
for in that project's own out/ folder.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
import traceback
from datetime import date
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from core import in_use
from kitty_editor import automation, build, intake, registry
from plugins import session
from server.projects_api import local_origin

MESSAGES = {
    "missing": "The editor is not installed with this copy of Clips Kitty.",
    "not_ours": "The editor found on this PC is not Clips Kitty Editor.",
    "windows_only": "The editor runs on Windows only.",
}
UNFINISHED = "This project was not finished. Open it again to make it."


class OpenIn(BaseModel):
    """Open or make a project: {"kind": "clip", "clip_id", "fresh"}, {"kind":
    "video", "video_id", "shape"} or {"kind": "file", "path", "shape"}."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    clip_id: int | None = None
    fresh: bool | None = None
    video_id: str | None = None
    path: str | None = None
    shape: str | None = None


class ReopenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The person's export folder (Settings), for exports recovered on reopen
    # that have no clip to re-point; checked as take_in checks it.
    export_folder: str | None = None


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    export_folder: str | None = None


def supported() -> bool:
    """Whether the editor can run on this system at all."""
    import os

    return os.name == "nt"


def install(app, *, db, data_dir: Path, session_secret: str, broadcaster, config: dict,
            clip_captions=None) -> None:
    data_dir = Path(data_dir)

    def guard(request: Request) -> None:
        if not session.matches(session_secret, request.headers.get(session.HEADER)):
            raise HTTPException(403, f"This needs the {session.HEADER} header. The desktop app sends it; a "
                                     f"script finds it in {session.path(data_dir)}.")
        if not local_origin(request.headers.get("origin"), request.headers.get("host")):
            raise HTTPException(403, "The editor can only be used from the Clips Kitty app on this PC.")

    guarded = [Depends(guard)]

    def publish(event: dict) -> None:
        try:
            broadcaster.publish(event)
        except Exception:
            pass        # no window is listening; the app can still ask

    # ---- where the editor is (E1) ----------------------------------------

    versions: dict[str, str | None] = {}        # the --version run, once per program file

    def status() -> dict:
        from core.binaries import editor_exe

        if not supported():
            return {"found": False, "version": "", "message": MESSAGES["windows_only"], "reason": "windows_only"}
        exe = editor_exe()
        if exe is None:
            return {"found": False, "version": "", "message": MESSAGES["missing"], "reason": "missing"}
        if exe not in versions:
            versions[exe] = automation.version_of(exe)
        if versions[exe] is None:
            return {"found": False, "version": "", "message": MESSAGES["not_ours"], "reason": "not_ours"}
        return {"found": True, "version": versions[exe], "message": "", "reason": "", "exe": exe}

    def editor() -> str:
        """The editor's program file, or 503 with the plain reason."""
        found = status()
        if not found["found"]:
            raise HTTPException(503, {"message": found["message"], "reason": found["reason"]})
        return found["exe"]

    @app.get("/editor/status", dependencies=guarded)
    def editor_status():
        """Whether Clips Kitty Editor is there, in plain words when not."""
        return {k: v for k, v in status().items() if k != "exe"}

    @app.get("/editor/launch", dependencies=guarded)
    def editor_launch():
        """Where the editor's program is, for the desktop app's main process
        (the one place the editor is looked for)."""
        exe = editor()
        return {"exe": exe, "cwd": str(Path(exe).parent)}

    # ---- projects (E2, E3) -------------------------------------------------

    building: set[str] = set()                  # waiting for the build thread, or on it
    jobs: queue.Queue = queue.Queue()
    worker_lock = threading.Lock()
    worker: list[threading.Thread] = []

    def view(manifest: dict, d) -> dict:
        """A project as the app is told about it: the manifest, plus whether
        its clip changed or went, and whether its media is still there."""
        out = dict(manifest)
        if out.get("state") == "building" and out["id"] not in building:
            # Its build ended since the manifest was read, or the engine
            # stopped while it was being made: read again to tell.
            try:
                out = dict(registry.load(data_dir, out["id"]))
            except registry.NotFound:
                pass
            if out.get("state") == "building" and out["id"] not in building:
                out.update(state="failed", message=UNFINISHED)
        clip = out.get("clip") if out.get("kind") == "clip" else None
        changed, gone = False, False
        if isinstance(clip, dict) and clip.get("id") is not None:
            try:
                row = d.get_clip(int(clip["id"]))
            except OverflowError:
                row = None
            gone = row is None
            if row is not None and clip.get("file"):
                changed = not (registry.same_path(row["path"], clip["file"])
                               and registry.stamp(row["path"]) == clip.get("file_stamp"))
        # The file the editor's project plays (video_file, K-D20), else the
        # media it was made from; a clip project made before K-D20 plays
        # media/base.mp4.
        shown = out.get("video_file")
        files = [shown] if shown else list(out.get("media") or ())
        missing = any(not Path(str(p)).is_file() for p in files)
        if out.get("kind") == "clip" and out.get("state") == "ready" and not shown:
            missing = missing or not (registry.root(data_dir) / out["id"] / "media" / "base.mp4").is_file()
        out.update(clip_changed=changed, clip_gone=gone, media_missing=missing)
        if out.get("kind") != "clip" or gone:
            # Its export goes to the export folder under the name the sheet
            # shows: "<name> - <date>" to begin with (E4), or what the person
            # typed instead (intake keeps it).
            out["export_name"] = default_export_name(out)
        return out

    def default_export_name(manifest: dict) -> str:
        stem = registry.safe_name(manifest.get("export_name") or manifest.get("name") or "Video", "Video")
        return f"{stem[:47].rstrip(' .') or 'Video'} - {date.today().isoformat()}"

    def new_manifest(kind: str, name: str, **more) -> dict:
        project_id = registry.new_id()
        home = registry.root(data_dir) / project_id
        (home / "out").mkdir(parents=True, exist_ok=True)
        (home / "media").mkdir(parents=True, exist_ok=True)
        stamp = registry.now()
        manifest = {
            "schema": registry.SCHEMA, "id": project_id, "name": name, "kind": kind,
            "state": "building", "progress": 0.0, "message": "",
            "created_at": stamp, "last_opened_at": stamp,
            "canvas": None, "shape": "", "project_dir": str(home / "p" / name),
            "export_dir": str(home / "out"), "export_name": name,
            "clip": None, "video_id": None, "source_file": "", "media": [], "notes": [], "exports": [],
            **more,
        }
        return registry.save(data_dir, manifest)

    def start(project_id: str, exe: str) -> None:
        """Queue the project's build: one at a time, in the order asked."""
        building.add(project_id)
        registry.update(data_dir, project_id,
                        lambda m: m.update(state="building", progress=0.0, message=""))
        jobs.put((project_id, exe))
        with worker_lock:
            if not worker or not worker[0].is_alive():
                thread = threading.Thread(target=run_builds, name="kitty-editor-build", daemon=True)
                worker[:] = [thread]
                thread.start()

    def run_builds() -> None:
        while True:
            try:
                project_id, exe = jobs.get(timeout=30)
            except queue.Empty:
                with worker_lock:
                    if jobs.empty():
                        worker.clear()
                        return
                continue
            try:
                build_one(project_id, exe)
            finally:
                building.discard(project_id)

    def build_one(project_id: str, exe: str) -> None:
        last = [0.0, 0.0]       # when it was last said, and how far

        def progress(value: float, force: bool = False) -> None:
            # Never back: the base's own progress and the steps around it
            # come from different places (the render's thread among them).
            value = round(max(last[1], min(0.99, float(value))), 3)
            last[1] = value
            now = time.monotonic()
            if force or now - last[0] >= 0.5:      # at most twice a second
                last[0] = now
                registry.update(data_dir, project_id, lambda m: m.update(progress=value))
                publish({"type": "editor_project", "id": project_id, "state": "building", "progress": value})

        try:
            manifest = registry.load(data_dir, project_id)
            if manifest["kind"] == "clip":
                with in_use.holding([manifest["clip"]["id"]]):
                    changes = build_clip(manifest, exe, progress)
            else:
                changes = build_source(manifest, exe, progress)
            changes.update(state="ready", progress=1.0, message="")
        except Failure as e:
            changes = {"state": "failed", "message": str(e)}
        except automation.EditorError as e:
            print(f"  Editor project {project_id}: {e}")
            changes = {"state": "failed", "message": "The editor could not make this project."}
        except Exception as e:
            traceback.print_exc()
            print(f"  Editor project {project_id}: {type(e).__name__}: {e}")
            changes = {"state": "failed", "message": "This project could not be made."}
        shutil.rmtree(registry.root(data_dir) / project_id / "work", ignore_errors=True)
        try:
            saved = registry.update(data_dir, project_id, lambda m: m.update(changes))
            publish({"type": "editor_project", "id": project_id, "state": saved.get("state"),
                     "progress": saved.get("progress")})
        except registry.NotFound:
            pass        # deleted meanwhile

    class Failure(Exception):
        """A build that can't be done, with a sentence for the person."""

    def build_clip(manifest: dict, exe: str, progress) -> dict:
        from core.state import in_project_exports

        home = registry.root(data_dir) / manifest["id"]
        d = db()
        try:
            row = d.get_clip(int(manifest["clip"]["id"]))
        finally:
            d.close()
        if row is None:
            raise Failure("This clip isn't in Clips Kitty any more.")
        opts = json.loads(row["render_opts"]) if row["render_opts"] else {}
        clip_file = Path(row["path"]) if row["path"] else None
        stood_on = {"file": str(clip_file) if clip_file else "", "file_stamp": registry.stamp(clip_file)}
        base = _video_target(manifest, ".mp4")
        plain = None        # set when the clip's finished file is the video: nothing else is carried
        progress(0.05, force=True)
        made = {}
        if clip_file is not None and in_project_exports(clip_file, data_dir):
            plain = build.NOTE_EXPORT
        else:
            key = build.base_key(row, config, data_dir)
            if _kept_base(manifest, key, base):
                made = {"base_key": key}
            else:
                began = time.monotonic()
                rendered = build.render_base(row, config, data_dir, home / "work",
                                             progress=lambda v: progress(0.05 + 0.75 * max(0.0, min(1.0, v))))
                if rendered is None:
                    plain = build.NOTE_SOURCE
                else:
                    base.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(rendered), str(base))
                    made = {"base_key": key}
                    # Kept with the project at once: a build that fails after
                    # this reuses it when it is made again.
                    registry.update(data_dir, manifest["id"], lambda m: m.update(made, video_file=str(base)))
                    print(f"  Editor project {manifest['id']}: the clip's video made in "
                          f"{time.monotonic() - began:.1f} s")
        planned = time.monotonic()
        if plain is not None:
            if clip_file is None or not clip_file.is_file():
                raise Failure("This clip's video file is gone, so it can't be opened in the editor.")
            base.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(clip_file, base)
        progress(0.8, force=True)

        info = build.probe(base)
        if not info["video"]:
            raise Failure("This clip's video could not be read.")
        canvas = {"width": info["width"], "height": info["height"],
                  "fps_num": info["fps_num"], "fps_den": info["fps_den"]}
        if plain is not None:
            plan = {"pieces": [{"in": 0.0, "out": info["seconds"], "muted": False}], "speed": 1.0, "volume": 1.0,
                    "fade_in": 0.0, "fade_out": 0.0, "length": info["seconds"], "captions": [], "hook": None,
                    "music": None, "watermark_text": None, "watermark_image": None, "notes": [plain]}
        else:
            clips_cfg = config.get("clips") or {}
            # Whether the clip burns captions: core.pipeline.captions_on's rule.
            wants = bool(clips_cfg.get("captions", True) and opts.get("captions", opts.get("job_captions", True)))
            style = opts.get("caption_style") or clips_cfg.get("caption_style")
            lines = []
            if wants and clip_captions is not None:
                lines = _spoken_by(clip_captions(row), opts, style)
            watermark = opts["watermark"] if "watermark" in opts else clips_cfg.get("watermark")
            plan = build.plan_clip(
                opts=opts, window=float(row["end_s"]) - float(row["start_s"]), base_seconds=info["seconds"],
                lines=lines, canvas=(info["width"], info["height"]), default_style=clips_cfg.get("caption_style"),
                captions=wants, music_file=_own_copy(_music_file(opts), home, "music"),
                watermark=watermark if isinstance(watermark, dict) else None,
                watermark_image=_own_copy(_logo_file(watermark), home, "watermark"))
        with build.open_session(exe, home, [base.parent]) as editor_session:
            progress(0.9, force=True)
            project_dir = build.apply_clip_plan(editor_session, home, manifest["name"], canvas, base, plan)
        print(f"  Editor project {manifest['id']}: the editor's project made in {time.monotonic() - planned:.1f} s")
        _drop_old_video(manifest, base)
        return {"canvas": canvas, "shape": _shape_of(info["width"], info["height"]), "project_dir": project_dir,
                "notes": plan["notes"], "media": [], "clip": {**manifest["clip"], **stood_on},
                "video_file": str(base), **made}

    def _kept_base(manifest: dict, key: str, target: Path) -> bool:
        """The clip's video made before from the same window, framing and
        files (build.base_key), put at `target` instead of being made again:
        this project's own from a build that failed after it, or a copy of
        another project's of the same clip (a copy, not a link: the editor
        may have that one open, and a link to an open file can't be deleted
        with its project). False when there is none."""
        if manifest.get("base_key") == key and manifest.get("video_file") and Path(manifest["video_file"]).is_file():
            kept = Path(manifest["video_file"])
            if registry.same_path(kept, target):
                return True
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(kept, target)
                return True
            except OSError:
                return False
        clip_id = (manifest.get("clip") or {}).get("id")
        for other in registry.list_all(data_dir):
            if (other.get("id") == manifest["id"] or other.get("kind") != "clip" or other.get("state") != "ready"
                    or (other.get("clip") or {}).get("id") != clip_id or other.get("base_key") != key):
                continue
            made = Path(str(other.get("video_file") or ""))
            if not made.is_file():
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(made, target)
                return True
            except OSError:
                continue
        return False

    def _video_target(manifest: dict, suffix: str) -> Path:
        """Where the project's video goes in its media folder, named for
        people (K-D20): the clip's or the video's title as the project's own
        name already has it (registry.safe_name: no character Windows
        refuses, no device name, at most 60 characters), so the editor's
        media list and timeline show that and never an id. The names of the
        project's other media (music, watermark) are never taken."""
        stem = registry.safe_name(manifest.get("name"), "Video")
        if stem.casefold() in ("music", "watermark"):
            stem = f"{stem} video"
        return registry.root(data_dir) / manifest["id"] / "media" / f"{stem}{suffix}"

    def _drop_old_video(manifest: dict, kept: Path) -> None:
        """The video an earlier build of this project left in its own media
        folder under another name (a failed build made again). Only ever a
        file inside the project's media folder."""
        media = registry.root(data_dir) / manifest["id"] / "media"
        for old in (manifest.get("video_file"), str(media / "base.mp4")):
            if not old or registry.same_path(old, kept):
                continue
            old_path = Path(str(old))
            try:
                if old_path.parent.resolve() == media.resolve() and old_path.is_file():
                    old_path.unlink()
            except OSError:
                pass

    def _linked(source: Path, target: Path) -> Path:
        """A Library video under its title, inside the project's folder: a
        hard link to the Library's own download (no copy, no space taken;
        deleting the project removes only the link). Where the drive can't
        link (FAT32, exFAT, another drive), the download itself, which the
        editor then shows under its file name: the editor's automation has no
        display name for a media item, and a 2-hour video is never copied."""
        try:
            if target.exists():
                if os.path.samefile(target, source):
                    return target
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, target)
            return target
        except (OSError, NotImplementedError, AttributeError):
            return source

    def _spoken_by(lines, opts: dict, style) -> list:
        """The caption lines, marked where the other speaker says them when
        the clip colours that speaker (as the render marks them)."""
        if not (style or {}).get("second_speaker"):
            return lines
        try:
            from video.captions import paint_turns, tag_lines

            said = paint_turns(opts.get("speaker_turns"), opts.get("speaker_edits"))
            return tag_lines(lines, said) if said else lines
        except Exception:
            return lines

    def _music_file(opts: dict) -> Path | None:
        music = (opts.get("edit") or {}).get("music") if isinstance(opts.get("edit"), dict) else None
        if not isinstance(music, dict):
            return None
        if isinstance(music.get("path"), str) and music["path"].strip():
            found = Path(music["path"].strip())
        elif isinstance(music.get("library"), str) and music["library"].strip():
            try:
                from video import audio_library

                found = audio_library.resolve(music["library"].strip(), kind="music")
            except Exception:
                found = None
        else:
            found = None
        return found if found is not None and found.is_file() else None

    def _logo_file(watermark) -> Path | None:
        if not isinstance(watermark, dict) or watermark.get("type") not in ("image", "both"):
            return None
        from core.paths import safe_name

        name = safe_name(str(watermark.get("image_asset") or ""))
        found = data_dir / "branding" / "assets" / name if name else None
        return found if found is not None and found.is_file() else None

    def _own_copy(source: Path | None, home: Path, stem: str) -> Path | None:
        """A small media file copied into the project's own media folder."""
        if source is None:
            return None
        target = home / "media" / f"{stem}{source.suffix.lower()}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target

    def build_source(manifest: dict, exe: str, progress) -> dict:
        home = registry.root(data_dir) / manifest["id"]
        source = Path(manifest["media"][0])
        if not source.is_file():
            raise Failure("The video's file is gone.")
        info = build.probe(source)
        if not info["video"]:
            raise Failure("That file doesn't look like a video.")
        width, height = registry.SHAPES[manifest["shape"]]
        canvas = {"width": width, "height": height, "fps_num": info["fps_num"], "fps_den": info["fps_den"]}
        # A Library video's download is named by the engine's cache
        # (local_<hash>.mp4, <video id>.mp4); a file from the PC keeps its own name.
        shown = _linked(source, _video_target(manifest, source.suffix.lower()))             if manifest["kind"] == "video" else source
        progress(0.3, force=True)
        with build.open_session(exe, home, [source.parent]) as editor_session:
            project_dir = build.build_whole(editor_session, home, manifest["name"], canvas, shown)
        if manifest["kind"] == "video":
            _drop_old_video(manifest, shown)
        return {"canvas": canvas, "project_dir": project_dir, "video_file": str(shown)}

    def _shape_of(width: int, height: int) -> str:
        for name, (w, h) in registry.SHAPES.items():
            if width * h == height * w:
                return name
        return f"{width}x{height}"

    def _shape(value) -> str:
        shape = value or "16:9"
        if shape not in registry.SHAPES:
            raise HTTPException(400, f"shape is one of {', '.join(registry.SHAPES)}.")
        return shape

    def _only(body: OpenIn, *allowed: str) -> None:
        extra = [k for k in ("clip_id", "fresh", "video_id", "path", "shape")
                 if getattr(body, k) is not None and k not in allowed]
        if extra:
            raise HTTPException(400, f"{', '.join(extra)}: not part of opening a {body.kind}.")

    @app.post("/editor/projects/open", dependencies=guarded)
    def open_project(body: OpenIn):
        """The project for a clip, a whole video or a file from this PC:
        the one there already ("created": false), or a new one, built in the
        background (its `state` is "building" until it is "ready")."""
        if body.kind not in registry.KINDS:
            raise HTTPException(400, "kind is clip, video or file.")
        d = db()
        try:
            if body.kind == "clip":
                _only(body, "clip_id", "fresh")
                if body.clip_id is None:
                    raise HTTPException(400, "clip_id is needed to open a clip.")
                row = d.get_clip(body.clip_id) if 0 < body.clip_id < 2**63 else None
                if row is None:
                    raise HTTPException(404, "There is no such clip.")
                existing = None if body.fresh else registry.find_for_clip(data_dir, row["id"])
                if existing is not None and view(existing, d)["state"] != "failed":
                    return {"project": view(existing, d), "created": False}
                exe = editor()
                if existing is not None:        # a build that failed: again, in the same folder
                    start(existing["id"], exe)
                    return {"project": view(registry.load(data_dir, existing["id"]), d), "created": False}
                name = registry.safe_name(row["title"] or row["hook"], f"Clip {row['id']}")
                made = new_manifest("clip", name, video_id=row["video_id"], clip={
                    "id": int(row["id"]), "video_id": row["video_id"], "start_s": float(row["start_s"]),
                    "end_s": float(row["end_s"]), "file": "", "file_stamp": None})
            elif body.kind == "video":
                _only(body, "video_id", "shape")
                shape = _shape(body.shape)
                if not body.video_id:
                    raise HTTPException(400, "video_id is needed to open a video.")
                video = d.conn.execute("SELECT video_id, title FROM videos WHERE video_id = ?",
                                       (body.video_id,)).fetchone()
                if video is None:
                    raise HTTPException(404, "There is no such video.")
                from core.paths import cached_source

                source = cached_source(data_dir / "downloads", video["video_id"])
                if source is None or not source.is_file():
                    raise HTTPException(404, "This video's file isn't in Clips Kitty any more.")
                existing = registry.find_for_video(data_dir, video["video_id"], shape)
                if existing is not None and view(existing, d)["state"] != "failed":
                    return {"project": view(existing, d), "created": False}
                exe = editor()
                if existing is not None:
                    start(existing["id"], exe)
                    return {"project": view(registry.load(data_dir, existing["id"]), d), "created": False}
                name = registry.safe_name(video["title"], "Video")
                made = new_manifest("video", name, video_id=video["video_id"], shape=shape,
                                    media=[str(source.resolve())])
            else:
                _only(body, "path", "shape")
                shape = _shape(body.shape)
                from core.paths import picked_file
                from server.api import _VIDEO_SUFFIXES

                picked = picked_file(body.path or "", _VIDEO_SUFFIXES)
                if picked is None:
                    raise HTTPException(400, "not a video file this app can open")
                source = picked[0]
                if not build.probe(source)["video"]:
                    raise HTTPException(400, "that file doesn't look like a video")
                existing = registry.find_for_file(data_dir, source, shape)
                if existing is not None and view(existing, d)["state"] != "failed":
                    return {"project": view(existing, d), "created": False}
                exe = editor()
                if existing is not None:
                    start(existing["id"], exe)
                    return {"project": view(registry.load(data_dir, existing["id"]), d), "created": False}
                name = registry.safe_name(source.stem, "Video")
                made = new_manifest("file", name, shape=shape, source_file=str(source), media=[str(source)])
            start(made["id"], exe)
            return {"project": view(registry.load(data_dir, made["id"]), d), "created": True}
        finally:
            d.close()

    def found(project_id: str) -> dict:
        if not registry.is_id(project_id):
            raise HTTPException(404, "There is no such project.")
        try:
            return registry.load(data_dir, project_id)
        except registry.NotFound as e:
            raise HTTPException(404, "There is no such project.") from e

    @app.get("/editor/projects", dependencies=guarded)
    def list_projects():
        """Every project, last opened first. One whose record can't be read
        is listed as failed, never hidden."""
        d = db()
        try:
            return {"projects": [view(m, d) for m in registry.list_all(data_dir)]}
        finally:
            d.close()

    @app.get("/editor/projects/{project_id}", dependencies=guarded)
    def get_project(project_id: str):
        manifest = found(project_id)
        d = db()
        try:
            return view(manifest, d)
        finally:
            d.close()

    @app.post("/editor/projects/{project_id}/open", dependencies=guarded)
    def reopen_project(project_id: str, body: ReopenIn | None = None):
        """Open a project again: it is marked as opened now, and exports the
        app never heard the end of are taken in (`recovered`). A project
        whose making failed is made again (its `state` is then "building")."""
        manifest = found(project_id)
        if project_id in building:
            raise HTTPException(409, {"message": "This project is still being made.", "reason": "building"})
        recovered: list[dict] = []
        if manifest.get("damaged"):
            pass
        elif manifest.get("state") != "ready":
            start(project_id, editor())
        else:
            recovered = intake.recover(data_dir, project_id, open_db=db, publish=publish,
                                       folder=body.export_folder if body is not None else None)
        if not manifest.get("damaged"):
            manifest = registry.update(data_dir, project_id, lambda m: m.update(last_opened_at=registry.now()))
        d = db()
        try:
            return {**view(manifest, d), "recovered": recovered}
        finally:
            d.close()

    @app.delete("/editor/projects/{project_id}", dependencies=guarded)
    def delete_project(project_id: str):
        """Remove a project. Exports already taken in stay where they are."""
        found(project_id)
        if project_id in building:
            raise HTTPException(409, {"message": "This project is still being made.", "reason": "building"})
        try:
            registry.delete(data_dir, project_id)
        except registry.InUse as e:
            raise HTTPException(409, {"message": "This project is open in the editor. Close it there first.",
                                      "reason": "open_in_editor"}) from e
        except registry.NotFound as e:
            raise HTTPException(404, "There is no such project.") from e
        return {"deleted": True}

    # ---- exports come back (E4) --------------------------------------------

    @app.post("/editor/projects/{project_id}/exports", dependencies=guarded)
    def take_export(project_id: str, body: ExportIn):
        """An export the editor finished, named by its file name in the
        project's own out/ folder: taken in as the clip's video, kept beside
        a clip that changed meanwhile, or saved to the person's export
        folder."""
        found(project_id)
        try:
            # While a reopen's recover (or another report) takes this project's
            # exports in, wait for it: the export it took is then answered as
            # it was recorded ("again"), never as busy or failed (K-D17).
            return intake.take_in(data_dir, project_id, body.file, folder=body.export_folder, open_db=db,
                                  publish=publish, wait=intake.LOCK_WAIT)
        except intake.BadName as e:
            raise HTTPException(400, "file is the export's file name, with no folder.") from e
        except intake.BadFolder as e:
            raise HTTPException(400, str(e)) from e
        except (intake.NoSuchExport, registry.NotFound) as e:
            raise HTTPException(404, "There is no such export in this project.") from e
        except intake.Busy as e:
            raise HTTPException(409, {"message": "This export is being saved right now.", "reason": "busy"}) from e
        except intake.Failed as e:
            raise HTTPException(500, {"message": str(e)}) from e
