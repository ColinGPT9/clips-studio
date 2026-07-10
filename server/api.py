"""Local HTTP API for the desktop app.

Bound to 127.0.0.1 only — this is a local service, not a web server.
Start with:  python main.py serve   (default port 8765)

The Electron renderer talks exclusively to this API; it never touches
Python or the filesystem directly.
"""

import asyncio
import json
import re
import shutil
import threading
from pathlib import Path

import requests as _requests
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.state import StateDB
from server.events import broadcaster
from server.jobs import Worker


# ---- request bodies ----------------------------------------------------------


class JobIn(BaseModel):
    url: str
    force: bool = False
    max_clips: int | None = None  # per-job override of clips.max_clips_per_video
    caption_style: dict | None = None  # style applied to every clip of this job
    captions: bool | None = None  # burn captions into this job's clips (default true)
    long_clips: bool | None = None  # 61-180s clips (TikTok monetization needs >60s)
    filter: str | None = None  # color preset name (video/filters.py) for the whole job
    min_score: int | None = None  # per-job quality bar override (0-100)
    longform: dict | None = None  # {"mode": short_clips|clips_140|highlights|edited_stream}


class ClipPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    hashtags: list[str] | None = None


class MergeIn(BaseModel):
    from_id: int
    into_id: int


class LearningIn(BaseModel):
    enabled: bool


class AccountIn(BaseModel):
    platform: str   # youtube | twitch | kick
    channel: str    # channel/username on that platform


class PreviewIn(BaseModel):
    edit: dict | None = None            # pending edit list from the editor
    caption_lines: list[dict] | None = None  # pending caption text, if changed
    crop: str | None = None             # pending layout (track/letterbox/center)


class LocalVideoIn(BaseModel):
    path: str                  # video file on this computer (mp4/mov/mkv/…)
    title: str = ""            # defaults to the file name
    channel: str = ""          # creator/channel name for the Creators tab
    platform: str = "youtube"  # which platform profile this creator belongs to
    captions: bool | None = None
    caption_style: dict | None = None
    long_clips: bool | None = None


class RenderIn(BaseModel):
    start: float | None = None
    end: float | None = None
    render_opts: dict | None = None  # crop / captions / caption_style / caption_lines


class CaptionsIn(BaseModel):
    lines: list[dict]  # [{"start", "end", "text"}] clip-relative


class CancelIn(BaseModel):
    video_id: str | None = None
    url: str | None = None


class AiEditIn(BaseModel):
    message: str


class ExportIn(BaseModel):
    folder: str


class BatchExportIn(BaseModel):
    clip_ids: list[int]
    folder: str


class ModelIn(BaseModel):
    tag: str


class SettingsPatch(BaseModel):
    model: str | None = None
    channel: str | None = None
    auto_upload: bool | None = None
    privacy: str | None = None


# ---- app factory ---------------------------------------------------------------


def create_app(config: dict, settings_path: Path) -> FastAPI:
    app = FastAPI(title="Clips Studio API", version="0.1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Vite dev server
        allow_methods=["*"],
        allow_headers=["*"],
    )

    data_dir = Path(config["paths"]["data_dir"]).resolve()
    db_path = data_dir / "state.db"
    worker = Worker(config)

    def db() -> StateDB:
        # sqlite connections aren't shareable across FastAPI's threadpool
        # threads; per-request connections are effectively free.
        return StateDB(db_path)

    @app.on_event("startup")
    async def _startup():
        broadcaster.attach_loop(asyncio.get_running_loop())
        worker.start()

    @app.on_event("shutdown")
    async def _shutdown():
        worker.stop()

    # ---- health / system -----------------------------------------------

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/system/stats")
    def system_stats():
        import psutil

        disk = shutil.disk_usage(data_dir)
        stats = {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "ram_percent": psutil.virtual_memory().percent,
            "data_dir_bytes": sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file()),
            "disk_free_bytes": disk.free,
            "gpu": _gpu_stats(),
        }
        return stats

    # ---- jobs -------------------------------------------------------------

    @app.post("/jobs")
    def create_job(body: JobIn, status_code=201):
        # Re-pasting an already-done URL without force would silently no-op —
        # tell the UI instead, so it can offer "process again with current
        # settings" (e.g. the same video in both 60s+ and regular modes).
        # Longform jobs skip the guard: making longform outputs of an
        # already-processed video is the normal case, not a re-run.
        if not body.force and not body.longform:
            from sources.dispatch import identify

            _, vid = identify(body.url)
            if vid:
                d0 = db()
                try:
                    status = d0.video_status(vid)
                finally:
                    d0.close()
                if status == "done":
                    return {"job_id": None, "already_processed": True, "video_id": vid}

        payload: dict = {"url": body.url, "force": body.force}
        if body.max_clips is not None:
            payload["max_clips"] = max(1, min(10, body.max_clips))
        if body.caption_style:
            payload["caption_style"] = body.caption_style
        if body.captions is not None:
            payload["captions"] = body.captions
        if body.long_clips:
            payload["long_clips"] = True
        if body.longform:
            payload["longform"] = body.longform
        if body.filter:
            from video.filters import is_valid

            if not is_valid(body.filter):
                raise HTTPException(400, f"unknown filter '{body.filter}'")
            payload["filter"] = body.filter
        if body.min_score is not None:
            payload["min_score"] = max(0, min(100, body.min_score))
        d = db()
        try:
            job_id = d.add_job("process", json.dumps(payload))
        finally:
            d.close()
        worker.notify()
        return {"job_id": job_id}

    @app.post("/videos/local")
    def add_local_video(body: LocalVideoIn):
        """Import a video FILE from this computer and run the normal clip
        pipeline on it — for editing/clipping a video before it's published
        anywhere. The user's title/creator/platform fill the same fields a
        downloaded video would get, so it lands in the library and the
        Creators tab exactly like a processed URL."""
        import hashlib
        import subprocess as sp

        src = Path(body.path)
        if not src.exists() or not src.is_file():
            raise HTTPException(400, f"file not found: {body.path}")

        # Must contain a video stream (catches audio files / random files).
        probe = sp.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(src)],
            capture_output=True, text=True,
        )
        if probe.returncode != 0 or not probe.stdout.strip():
            raise HTTPException(400, "that file doesn't look like a video")

        stat = src.stat()
        vid = "local_" + hashlib.md5(
            f"{src.resolve()}|{stat.st_size}|{int(stat.st_mtime)}".encode()
        ).hexdigest()[:12]
        dest = data_dir / "downloads" / f"{vid}.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)

        if not dest.exists():
            # Remux (no re-encode — fast, lossless) into the pipeline's mp4
            # layout; fall back to an NVENC/CPU re-encode for codecs mp4
            # can't carry (e.g. VP9 in webm).
            remux = sp.run(
                ["ffmpeg", "-y", "-i", str(src), "-c", "copy",
                 "-movflags", "+faststart", str(dest)],
                capture_output=True, text=True,
            )
            if remux.returncode != 0:
                dest.unlink(missing_ok=True)
                from video.encoding import video_encoder_args

                reenc = sp.run(
                    ["ffmpeg", "-y", "-i", str(src), *video_encoder_args(),
                     "-c:a", "aac", "-b:a", "160k",
                     "-movflags", "+faststart", str(dest)],
                    capture_output=True, text=True,
                )
                if reenc.returncode != 0:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        400,
                        "couldn't convert this file — export it as MP4 (H.264) and try again",
                    )

        title = body.title.strip() or src.stem
        platform = body.platform if body.platform in ("youtube", "twitch", "kick") else "youtube"
        d = db()
        try:
            d.upsert_video(vid, title=title, channel_name=body.channel.strip())
            if body.channel.strip():
                from creator.identity import tag_video

                tag_video(d, vid, body.channel.strip(), platform=platform)
            payload: dict = {"url": f"local:{vid}"}
            if body.captions is not None:
                payload["captions"] = body.captions
            if body.caption_style:
                payload["caption_style"] = body.caption_style
            if body.long_clips:
                payload["long_clips"] = True
            job_id = d.add_job("process", json.dumps(payload))
        finally:
            d.close()
        worker.notify()
        return {"job_id": job_id, "video_id": vid}

    @app.get("/jobs")
    def jobs():
        d = db()
        try:
            return [dict(r) for r in d.list_jobs()]
        finally:
            d.close()

    @app.get("/jobs/{job_id}")
    def job(job_id: int):
        d = db()
        try:
            row = d.get_job(job_id)
        finally:
            d.close()
        if row is None:
            raise HTTPException(404, "no such job")
        return dict(row)

    @app.post("/cancel")
    def cancel_processing(body: CancelIn):
        """Cancel the in-flight processing of a video. Cooperative — the
        pipeline stops at its next stage boundary (or aborts the download)."""
        from core import cancel
        from sources.dispatch import identify

        vid = body.video_id
        if not vid and body.url:
            _, vid = identify(body.url)
        if not vid:
            raise HTTPException(400, "provide video_id or a resolvable url")
        cancel.request_cancel(vid)
        return {"cancelling": vid}

    def _log_feedback(d: StateDB, row, action: str, extra: dict | None = None) -> None:
        """Append a learning signal for creator preference learning (which
        clip styles this creator's user keeps, edits, exports). Snapshot the
        clip's stats — the clip row itself may be deleted later. Best-effort:
        a logging failure must never fail the user's actual request."""
        try:
            from core.state import _now

            v = d.conn.execute(
                "SELECT creator_id FROM videos WHERE video_id = ?", (row["video_id"],)
            ).fetchone()
            meta = {
                "score": row["score"],
                "scores": json.loads(row["scores"]) if row["scores"] else None,
                "duration": round(row["end_s"] - row["start_s"], 1),
                **(extra or {}),
            }
            d.conn.execute(
                "INSERT INTO clip_feedback (creator_id, clip_id, action, clip_meta, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (v["creator_id"] if v else None, row["id"], action, json.dumps(meta), _now()),
            )
            d.conn.commit()
        except Exception:
            pass

    @app.delete("/videos/{video_id}")
    def delete_video(video_id: str):
        """Delete a video: its download, transcript, clip files, and all its
        database rows. Only blocked if the video is ACTIVELY processing right
        now (not merely stuck in an in-progress status from a past crash)."""
        from core import cancel

        if cancel.active_video() == video_id:
            raise HTTPException(409, "video is processing right now — cancel it first")

        # Remove files: download, transcript, and the clip folder.
        for f in (data_dir / "downloads").glob(f"{video_id}.*"):
            f.unlink(missing_ok=True)
        (data_dir / "transcripts" / f"{video_id}.json").unlink(missing_ok=True)
        for clip_dir in data_dir.glob(f"clips/*/*[[]{video_id}[]]"):
            if clip_dir.is_dir():
                shutil.rmtree(clip_dir, ignore_errors=True)

        d = db()
        try:
            d.delete_video(video_id)
        finally:
            d.close()
        return {"deleted": video_id}

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        queue = broadcaster.subscribe()
        try:
            while True:
                event = await queue.get()
                await socket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            broadcaster.unsubscribe(queue)

    # ---- videos + clips ------------------------------------------------------

    @app.get("/videos")
    def videos():
        d = db()
        try:
            rows = d.conn.execute(
                """SELECT v.*, COUNT(c.id) AS clip_count, cr.display_name AS creator_name
                   FROM videos v
                   LEFT JOIN clips c ON c.video_id = v.video_id
                   LEFT JOIN creators cr ON cr.creator_id = v.creator_id
                   GROUP BY v.video_id ORDER BY v.created_at DESC"""
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            d.close()

    @app.get("/videos/{video_id}/clips")
    def clips_for_video(video_id: str):
        d = db()
        try:
            return [_clip_json(r) for r in d.clips_for_video(video_id)]
        finally:
            d.close()

    @app.patch("/clips/{clip_id}")
    def patch_clip(clip_id: int, body: ClipPatch):
        d = db()
        try:
            if d.get_clip(clip_id) is None:
                raise HTTPException(404, "no such clip")
            fields = {}
            if body.title is not None:
                fields["title"] = body.title.strip()[:100]
            if body.description is not None:
                fields["description"] = body.description.strip()
            if body.hashtags is not None:
                fields["hashtags"] = json.dumps(body.hashtags)
            if fields:
                d.set_clip(clip_id, **fields)
            return _clip_json(d.get_clip(clip_id))
        finally:
            d.close()

    def _clip_captions(row) -> list[dict]:
        """Current caption lines for a clip: the user-corrected override when
        one exists, otherwise regenerated from the transcript."""
        opts = json.loads(row["render_opts"]) if row["render_opts"] else {}
        if opts.get("caption_lines"):
            return opts["caption_lines"]

        from core.models import ClipCandidate, Segment
        from video.captions import DEFAULT_STYLE, build_caption_lines

        transcript_path = data_dir / "transcripts" / f"{row['video_id']}.json"
        if not transcript_path.exists():
            return []
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        segments = [Segment(**s) for s in transcript["segments"]]
        candidate = ClipCandidate(start=row["start_s"], end=row["end_s"], score=row["score"])
        words = opts.get("caption_style", {}).get(
            "words_per_caption", DEFAULT_STYLE["words_per_caption"]
        )
        return build_caption_lines(segments, candidate, words)

    @app.get("/clips/{clip_id}/captions")
    def get_captions(clip_id: int):
        d = db()
        try:
            row = d.get_clip(clip_id)
        finally:
            d.close()
        if row is None:
            raise HTTPException(404, "no such clip")
        return {"lines": _clip_captions(row)}

    @app.put("/clips/{clip_id}/captions")
    def put_captions(clip_id: int, body: CaptionsIn):
        """Save corrected caption text and queue a re-render burning it in."""
        d = db()
        try:
            row = d.get_clip(clip_id)
            if row is None:
                raise HTTPException(404, "no such clip")
            lines = [
                {"start": float(l["start"]), "end": float(l["end"]), "text": str(l.get("text", ""))}
                for l in body.lines
                if "start" in l and "end" in l
            ]
            payload = {"clip_id": clip_id, "render_opts": {"caption_lines": lines}}
            job_id = d.add_job("render", json.dumps(payload))
            _log_feedback(d, row, "captions_edited")
        finally:
            d.close()
        worker.notify()
        return {"job_id": job_id}

    @app.post("/clips/{clip_id}/ai-edit")
    def ai_edit(clip_id: int, body: AiEditIn):
        """Chat-driven editing: plain language in, validated edit + re-render out."""
        from analysis.clip_edit import interpret_edit
        from llm.registry import create_backend

        d = db()
        try:
            row = d.get_clip(clip_id)
            if row is None:
                raise HTTPException(404, "no such clip")

            opts = json.loads(row["render_opts"]) if row["render_opts"] else {}
            caption_lines = _clip_captions(row)
            transcript_path = data_dir / "transcripts" / f"{row['video_id']}.json"
            source_duration = 0.0
            if transcript_path.exists():
                transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
                if transcript["segments"]:
                    source_duration = float(transcript["segments"][-1]["end"])

            clip_state = {
                "start": row["start_s"],
                "end": row["end_s"],
                "duration": round(row["end_s"] - row["start_s"], 1),
                "crop": opts.get("crop", "track"),
                "captions_enabled": opts.get("captions", True),
                "caption_style": opts.get("caption_style", {}),
            }

            result = interpret_edit(
                body.message,
                clip_state=clip_state,
                caption_lines=caption_lines,
                source_duration=source_duration,
                llm=create_backend(config["llm"]),
            )

            job_id = None
            if result["needs_render"]:
                payload: dict = {"clip_id": clip_id}
                if result["start"] is not None:
                    payload["start"] = result["start"]
                if result["end"] is not None:
                    payload["end"] = result["end"]
                if result["render_opts"]:
                    payload["render_opts"] = result["render_opts"]
                job_id = d.add_job("render", json.dumps(payload))
                _log_feedback(
                    d, row,
                    "timestamps_adjusted"
                    if (result["start"] is not None or result["end"] is not None)
                    else "rerendered",
                )
        finally:
            d.close()

        if job_id is not None:
            worker.notify()
        return {"reply": result["reply"], "job_id": job_id}

    @app.post("/clips/{clip_id}/render")
    def rerender_clip(clip_id: int, body: RenderIn):
        d = db()
        try:
            row = d.get_clip(clip_id)
            if row is None:
                raise HTTPException(404, "no such clip")
            payload = {"clip_id": clip_id}
            if body.start is not None:
                payload["start"] = body.start
            if body.end is not None:
                payload["end"] = body.end
            if body.render_opts:
                payload["render_opts"] = body.render_opts
            job_id = d.add_job("render", json.dumps(payload))
            _log_feedback(
                d, row,
                "timestamps_adjusted"
                if (body.start is not None or body.end is not None)
                else "rerendered",
            )
        finally:
            d.close()
        worker.notify()
        return {"job_id": job_id}

    @app.post("/clips/{clip_id}/preview")
    def preview_clip(clip_id: int, body: PreviewIn):
        """Render this clip with every pending edit applied — into a preview
        file, through the REAL render path (face tracking, letterbox,
        captions, hook, music, speed), so the preview's framing and zoom are
        exactly what Apply will produce. Slower than a rough draft, but
        what you see is what you export."""
        d = db()
        try:
            row = d.get_clip(clip_id)
        finally:
            d.close()
        if row is None:
            raise HTTPException(404, "no such clip")
        source = data_dir / "downloads" / f"{row['video_id']}.mp4"
        if not source.exists():
            raise HTTPException(404, "source video missing — cannot preview-render")

        segments = []
        tpath = data_dir / "transcripts" / f"{row['video_id']}.json"
        if tpath.exists():
            from core.models import Segment

            tdata = json.loads(tpath.read_text(encoding="utf-8"))
            segments = [Segment(**s) for s in tdata["segments"]]

        from core.models import ClipCandidate
        from core.pipeline import _render_files

        opts = json.loads(row["render_opts"]) if row["render_opts"] else {}
        opts["edit"] = body.edit  # pending edit (None = cleared)
        if body.caption_lines is not None:
            opts["caption_lines"] = body.caption_lines
        if body.crop:
            opts["crop"] = body.crop

        candidate = ClipCandidate(
            start=row["start_s"], end=row["end_s"],
            score=row["score"] or 0, hook=row["hook"] or "",
        )
        prev_dir = data_dir / "previews"
        prev_dir.mkdir(parents=True, exist_ok=True)
        out = prev_dir / f"clip_{clip_id}.mp4"
        try:
            rendered, _ = _render_files(source, candidate, segments, prev_dir, config, opts)
            out.unlink(missing_ok=True)
            rendered.rename(out)
        except Exception as e:
            raise HTTPException(500, f"preview render failed: {str(e)[:400]}")
        import time as _time

        return {"url": f"/media/preview/{clip_id}?v={int(_time.time())}"}

    @app.get("/media/preview/{clip_id}")
    def media_preview(clip_id: int):
        path = (data_dir / "previews" / f"clip_{clip_id}.mp4").resolve()
        if not path.exists():
            raise HTTPException(404, "no draft preview")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/clips/{clip_id}/words")
    def clip_words(clip_id: int):
        """Word-level Whisper timestamps within this clip (clip-relative
        seconds) — powers the editor's clickable transcript."""
        d = db()
        try:
            row = d.get_clip(clip_id)
        finally:
            d.close()
        if row is None:
            raise HTTPException(404, "no such clip")
        tpath = data_dir / "transcripts" / f"{row['video_id']}.json"
        if not tpath.exists():
            return {"words": []}
        data = json.loads(tpath.read_text(encoding="utf-8"))
        start, end = row["start_s"], row["end_s"]
        words = []
        for seg in data.get("segments", []):
            for w in seg.get("words") or []:
                if w["end"] > start and w["start"] < end:
                    words.append(
                        {
                            "start": round(max(0.0, w["start"] - start), 2),
                            "end": round(min(end - start, w["end"] - start), 2),
                            "word": w["word"],
                        }
                    )
        return {"words": words}

    @app.get("/media/{clip_id}")
    def media(clip_id: int):
        d = db()
        try:
            row = d.get_clip(clip_id)
        finally:
            d.close()
        if row is None or not row["path"]:
            raise HTTPException(404, "no such clip")
        path = Path(row["path"]).resolve()
        if not path.exists() or data_dir not in path.parents:
            raise HTTPException(404, "clip file missing")
        return FileResponse(path, media_type="video/mp4")

    @app.post("/clips/{clip_id}/export")
    def export_clip(clip_id: int, body: ExportIn):
        return {"exported": _export([clip_id], Path(body.folder))}

    @app.post("/export/batch")
    def export_batch(body: BatchExportIn):
        return {"exported": _export(body.clip_ids, Path(body.folder))}

    def _export(clip_ids: list[int], folder: Path) -> list[str]:
        folder.mkdir(parents=True, exist_ok=True)
        d = db()
        exported = []
        try:
            for cid in clip_ids:
                row = d.get_clip(cid)
                if row is None or not row["path"] or not Path(row["path"]).exists():
                    continue
                name = _slugify(row["title"] or row["hook"] or Path(row["path"]).stem)
                target = _unique_path(folder, name)
                shutil.copy2(row["path"], target)
                exported.append(str(target))
                _log_feedback(d, row, "exported")  # exports = strongest "keep" signal
        finally:
            d.close()
        return exported

    # ---- creator profiles (creator intelligence) -----------------------------

    @app.get("/creators")
    def creators():
        """All creator profiles with library stats, plus possible same-person
        matches across platforms (suggestions only — merging is a user action)."""
        from creator.identity import suggestions

        d = db()
        try:
            rows = d.conn.execute(
                """SELECT c.creator_id, c.display_name, c.aliases, c.learning_enabled,
                          COUNT(DISTINCT v.video_id) AS videos,
                          COUNT(cl.id) AS clips,
                          ROUND(AVG(cl.score), 1) AS avg_score
                   FROM creators c
                   LEFT JOIN videos v ON v.creator_id = c.creator_id
                   LEFT JOIN clips cl ON cl.video_id = v.video_id
                   GROUP BY c.creator_id
                   ORDER BY videos DESC, c.display_name"""
            ).fetchall()
            accounts = d.conn.execute("SELECT * FROM platform_accounts").fetchall()
            sugg = suggestions(d)
        finally:
            d.close()
        by_creator: dict[int, list] = {}
        for a in accounts:
            by_creator.setdefault(a["creator_id"], []).append(
                {"account_id": a["account_id"], "platform": a["platform"], "username": a["username"]}
            )
        return {
            "creators": [
                {
                    **dict(r),
                    "aliases": json.loads(r["aliases"] or "[]"),
                    "accounts": by_creator.get(r["creator_id"], []),
                }
                for r in rows
            ],
            "suggestions": sugg,
        }

    @app.get("/creators/{creator_id}")
    def creator_detail(creator_id: int):
        """Everything learned about one creator: knowledge, events, feedback."""
        d = db()
        try:
            c = d.conn.execute(
                "SELECT * FROM creators WHERE creator_id = ?", (creator_id,)
            ).fetchone()
            if c is None:
                raise HTTPException(404, "no such creator")
            knowledge = d.conn.execute(
                "SELECT * FROM creator_knowledge WHERE creator_id = ?"
                " ORDER BY knowledge_type, created_at DESC",
                (creator_id,),
            ).fetchall()
            events = d.conn.execute(
                "SELECT * FROM creator_events WHERE creator_id = ? ORDER BY detected_date DESC",
                (creator_id,),
            ).fetchall()
            feedback = d.conn.execute(
                "SELECT action, COUNT(*) AS n FROM clip_feedback WHERE creator_id = ?"
                " GROUP BY action",
                (creator_id,),
            ).fetchall()
            accounts = d.conn.execute(
                "SELECT account_id, platform, username FROM platform_accounts WHERE creator_id = ?",
                (creator_id,),
            ).fetchall()
            from creator.learning import preferences

            prefs = preferences(d, creator_id)
        finally:
            d.close()
        return {
            **dict(c),
            "aliases": json.loads(c["aliases"] or "[]"),
            "accounts": [dict(a) for a in accounts],
            "knowledge": [dict(k) for k in knowledge],
            "events": [dict(e) for e in events],
            "feedback": {f["action"]: f["n"] for f in feedback},
            "preferences": prefs,
        }

    @app.post("/creators/{creator_id}/accounts")
    def add_creator_account(creator_id: int, body: AccountIn):
        """Manually attach a channel the automatic matcher didn't connect.
        Future videos from that channel resolve straight to this profile."""
        from creator.identity import add_account

        d = db()
        try:
            account_id = add_account(d, creator_id, body.platform, body.channel)
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            d.close()
        return {"account_id": account_id, "creator_id": creator_id}

    @app.post("/creators/merge")
    def merge_creators(body: MergeIn):
        """Fold one profile into another (same person on two platforms)."""
        from creator.identity import merge

        d = db()
        try:
            merge(d, body.from_id, body.into_id)
        except ValueError as e:
            raise HTTPException(404, str(e))
        finally:
            d.close()
        return {"merged": body.from_id, "into": body.into_id}

    @app.post("/creators/split/{account_id}")
    def split_creator_account(account_id: int):
        """Detach one platform account into its own profile (undo a merge)."""
        from creator.identity import split_account

        d = db()
        try:
            new_id = split_account(d, account_id)
        except ValueError as e:
            raise HTTPException(404, str(e))
        finally:
            d.close()
        return {"new_creator_id": new_id}

    @app.delete("/creators/{creator_id}/knowledge/{knowledge_id}")
    def delete_knowledge(creator_id: int, knowledge_id: int):
        """Remove one learned fact the user says is wrong."""
        d = db()
        try:
            d.conn.execute(
                "DELETE FROM creator_knowledge WHERE creator_id = ? AND knowledge_id = ?",
                (creator_id, knowledge_id),
            )
            d.conn.commit()
        finally:
            d.close()
        return {"deleted": knowledge_id}

    @app.delete("/creators/{creator_id}/memory")
    def wipe_creator_memory(creator_id: int):
        """Erase everything LEARNED about a creator — knowledge, storyline
        events, and feedback history — from this computer. The profile, its
        channels, videos and clips stay; only the intelligence data goes."""
        d = db()
        try:
            wiped = 0
            for table in ("creator_knowledge", "creator_events", "clip_feedback"):
                cur = d.conn.execute(f"DELETE FROM {table} WHERE creator_id = ?", (creator_id,))
                wiped += cur.rowcount
            d.conn.commit()
        finally:
            d.close()
        return {"creator_id": creator_id, "wiped": wiped}

    @app.post("/creators/{creator_id}/learning")
    def set_learning(creator_id: int, body: LearningIn):
        """Enable/disable knowledge learning for one creator."""
        d = db()
        try:
            d.conn.execute(
                "UPDATE creators SET learning_enabled = ? WHERE creator_id = ?",
                (1 if body.enabled else 0, creator_id),
            )
            d.conn.commit()
        finally:
            d.close()
        return {"creator_id": creator_id, "learning_enabled": body.enabled}

    # ---- models ------------------------------------------------------------

    ollama_host = config["llm"].get("ollama_host", "http://localhost:11434").rstrip("/")

    @app.get("/models")
    def models():
        from llm.manager import RECOMMENDATIONS, installed_models

        try:
            installed = installed_models(ollama_host)
        except Exception:
            raise HTTPException(503, "Ollama is not reachable — is it running?")
        return {
            "active": config["llm"]["backend"],
            "installed": installed,
            "recommendations": [
                {"hardware": h, "model": m, "note": n} for h, m, n in RECOMMENDATIONS
            ],
        }

    @app.post("/models/activate")
    def activate_model(body: ModelIn):
        from llm.manager import installed_models, switch_model

        try:
            installed = {m["name"] for m in installed_models(ollama_host)}
        except Exception:
            installed = set()
        if installed and body.tag not in installed:
            raise HTTPException(400, f"'{body.tag}' is not pulled yet")
        spec = switch_model(settings_path, body.tag)
        config["llm"]["backend"] = spec  # live config follows the file
        return {"active": spec}

    @app.post("/models/pull")
    def pull_model(body: ModelIn):
        def _pull():
            try:
                with _requests.post(
                    f"{ollama_host}/api/pull", json={"model": body.tag}, stream=True, timeout=3600
                ) as resp:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        info = json.loads(line)
                        broadcaster.publish(
                            {
                                "type": "model_pull",
                                "tag": body.tag,
                                "status": info.get("status", ""),
                                "completed": info.get("completed"),
                                "total": info.get("total"),
                            }
                        )
                broadcaster.publish({"type": "model_pull", "tag": body.tag, "status": "done"})
            except Exception as e:
                broadcaster.publish({"type": "model_pull", "tag": body.tag, "status": "error", "error": str(e)})

        threading.Thread(target=_pull, daemon=True).start()
        return {"started": body.tag}

    @app.delete("/models/{tag:path}")
    def delete_model(tag: str):
        resp = _requests.delete(f"{ollama_host}/api/delete", json={"model": tag}, timeout=60)
        if resp.status_code != 200:
            raise HTTPException(400, f"Ollama refused: {resp.text[:200]}")
        return {"deleted": tag}

    # ---- settings (quick-setup keys only) -----------------------------------

    @app.get("/settings")
    def get_settings():
        return {
            "model": config["llm"]["backend"].split("/", 1)[-1],
            "channel": config.get("channel", ""),
            "auto_upload": config.get("upload", {}).get("enabled", False),
            "privacy": config.get("upload", {}).get("privacy", "public"),
        }

    @app.patch("/settings")
    def patch_settings(body: SettingsPatch):
        text = settings_path.read_text(encoding="utf-8")
        edits = {
            "model": body.model,
            "channel": f'"{body.channel}"' if body.channel is not None else None,
            "auto_upload": str(body.auto_upload).lower() if body.auto_upload is not None else None,
            "privacy": body.privacy,
        }
        for key, value in edits.items():
            if value is None:
                continue
            text, n = re.subn(rf"(?m)^({key}:\s*)\S*", rf"\g<1>{value}", text, count=1)
            if n == 0:
                raise HTTPException(400, f"no '{key}:' line in settings.yaml")
        settings_path.write_text(text, encoding="utf-8")
        return {"ok": True, "note": "restart serve to apply pipeline-level changes"}

    return app


# ---- helpers --------------------------------------------------------------------


def _clip_json(row) -> dict:
    d = dict(row)
    d["hashtags"] = json.loads(d["hashtags"]) if d.get("hashtags") else []
    d["scores"] = json.loads(d["scores"]) if d.get("scores") else {}
    d["render_opts"] = json.loads(d["render_opts"]) if d.get("render_opts") else {}
    return d


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    slug = re.sub(r"[\s_]+", "-", slug)[:60].strip("-")
    return slug or "clip"


def _unique_path(folder: Path, name: str) -> Path:
    target = folder / f"{name}.mp4"
    i = 2
    while target.exists():
        target = folder / f"{name}-{i}.mp4"
        i += 1
    return target


def _gpu_stats() -> dict | None:
    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        return {
            "name": pynvml.nvmlDeviceGetName(handle),
            "vram_used": mem.used,
            "vram_total": mem.total,
            "gpu_percent": util.gpu,
        }
    except Exception:
        return None  # no NVIDIA GPU / driver — the UI shows CPU-only mode
