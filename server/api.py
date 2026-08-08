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

from core import queue
from core.binaries import ffmpeg, ffprobe
from core.paths import picked_file, safe_name
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
    watermark_profile_id: int | None = None  # branding profile applied to all clips
    podcast: bool | None = None   # multi-cam podcast: letterbox, no subject tracking


class JobPatch(BaseModel):
    """Per-video settings for a job that hasn't started yet.

    The same options as JobIn minus url/force: the settings snapshot stays
    editable right up until the worker claims the row, and is untouchable
    afterwards — a job whose configuration changed halfway would render some
    of its clips one way and the rest another."""

    max_clips: int | None = None
    caption_style: dict | None = None
    captions: bool | None = None
    long_clips: bool | None = None
    filter: str | None = None
    min_score: int | None = None
    longform: dict | None = None
    watermark_profile_id: int | None = None
    podcast: bool | None = None
    # Options to drop back to the app-wide default. Needed because null means
    # "unchanged" above, so there would otherwise be no way to turn one off.
    clear: list[str] = []


class BatchItemIn(BaseModel):
    """One video and the settings chosen for IT.

    Options are per item, not per batch: the user stages a list and configures
    each row before any of it runs, so "these three as podcasts, that one
    longform" has to survive the trip. A batch-wide option set could not
    express that."""

    url: str
    force: bool = False
    max_clips: int | None = None
    caption_style: dict | None = None
    captions: bool | None = None
    long_clips: bool | None = None
    filter: str | None = None
    min_score: int | None = None
    longform: dict | None = None
    watermark_profile_id: int | None = None
    podcast: bool | None = None


class BatchJobIn(BaseModel):
    items: list[BatchItemIn] = []


class QueueMoveIn(BaseModel):
    delta: int = 0            # -1 earlier, +1 later
    to: str | None = None     # "top" | "bottom"


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
    caption_style: dict | None = None   # pending caption font/size/etc.
    watermark: dict | None = None       # pending branding config (or {} to clear)
    normalize_audio: bool | None = None  # pending loudness-matching toggle


class BrandingIn(BaseModel):
    name: str
    config: dict


# What the two "import a file from this computer" endpoints will accept. Kept
# next to the models that carry those paths so the two stay in step, and passed
# to picked_file() so a path is rejected before anything opens it.
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".flv", ".ts", ".mpg",
                   ".mpeg", ".wmv", ".m2ts", ".mts")


class BrandingAssetIn(BaseModel):
    path: str   # image file on this computer to import as a branding asset


class CreatorBrandingIn(BaseModel):
    branding_id: int | None = None   # default branding profile, or null to clear


class LocalVideoIn(BaseModel):
    path: str                  # video file on this computer (mp4/mov/mkv/…)
    title: str = ""            # defaults to the file name
    channel: str = ""          # creator/channel name for the Creators tab
    platform: str = "youtube"  # which platform profile this creator belongs to
    captions: bool | None = None
    caption_style: dict | None = None
    long_clips: bool | None = None
    podcast: bool | None = None  # multi-cam podcast: letterbox, no subject tracking
    # The rest of the per-video options, so an uploaded file can be set up
    # exactly like a pasted link — the list builder offers the same switches
    # for both, and a switch that silently did nothing on your own upload
    # would be worse than not offering it. _process_options reads these by
    # name, so listing them here is all that is needed.
    longform: dict | None = None
    watermark_profile_id: int | None = None
    filter: str | None = None
    min_score: int | None = None
    max_clips: int | None = None
    force: bool = False


class RenderIn(BaseModel):
    start: float | None = None
    end: float | None = None
    render_opts: dict | None = None  # crop / captions / caption_style / caption_lines


class CaptionsIn(BaseModel):
    lines: list[dict]  # [{"start", "end", "text"}] clip-relative


class TightenIn(BaseModel):
    """Which kinds of dead weight to propose cutting."""

    silence: bool = True
    fillers: bool = True


class TermIn(BaseModel):
    """A creator's ruling on one word for translation."""

    term: str
    rule: str = "protect"  # protect | ignore | auto (forget the ruling)


class TranslationPatch(BaseModel):
    """A creator's corrections to one language's translated captions."""

    lines: list[dict]           # [{"start", "end", "text"}] clip-relative
    post: dict | None = None    # {title, description, hashtags}, unchanged if omitted


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
    content_language: str | None = None  # auto / ISO code (es, pt, hi, id...)
    translation_model: str | None = None  # local model used for translation


class TranslateIn(BaseModel):
    """Multilingual publishing for one or more finished clips."""

    clip_ids: list[int]
    languages: list[str]          # ISO codes from multilingual.languages
    stage: str = "export"         # translate (review first) | export (write files)
    folder: str = ""              # where the files are written (export only)
    include_video: bool = False   # copy the clip AS IT IS (original captions
                                  # burned in) beside the translated ones —
                                  # opt-in, matching the editor's checkbox
    burn: bool = False            # also make a video per language with captions burned in
    dub: bool = False             # also speak the translation over the clip
    subtitles: bool = False       # write .srt/.vtt files as well
    post_text: bool = False       # write the translated post text as well
    voices: dict | None = None    # {language: voice id} chosen by the creator
    style: dict | None = None     # subtitle font/size/colour/position; falls
                                  # back to the clip's own caption style


class FeedbackIn(BaseModel):
    kind: str  # bug | feature | improvement
    title: str
    answers: dict = {}
    areas: list[str] = []
    severity: str = ""
    include_diagnostics: bool = True
    video_id: str | None = None
    images: list[dict] = []  # [{"b64": ..., "ext": "png"|"jpg"}]


# ---- app factory ---------------------------------------------------------------


_BUILD: dict | None = None


def _build_stamp() -> dict:
    """Which commit this PROCESS is running, and how long it has been up.

    Python imports a module once. A backend left running while the checkout
    moves on keeps executing the code it started with, and nothing anywhere
    says so — the app reports the version on disk, which can be hours ahead of
    what is actually running.

    That is not hypothetical. A 70-minute run once produced clips with a
    tracker bug that had been fixed two hours earlier, and the only way to
    tell was comparing the process start time against the commit timestamps.
    From the outside it looked exactly like the fix not working.

    Read once and cached: this is a subprocess call on a hot endpoint, and the
    answer cannot change without restarting the process — which is the whole
    point of reporting it.
    """
    import time  # module scope, not inside the cache guard: the return below
    #              uses it on EVERY call, not just the first

    global _BUILD
    if _BUILD is None:
        import subprocess

        sha = ""
        try:
            sha = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            pass  # a packaged build has no git; the field just stays empty
        _BUILD = {"build_sha": sha, "started_at": time.time()}
    return {**_BUILD, "uptime_seconds": round(time.time() - _BUILD["started_at"])}


def _unlink_best_effort(path: Path, root: Path) -> bool:
    """Delete a file inside `root`, or report that it could not be deleted.

    Never raises, and never touches anything outside `root`. The containment
    check is here, at the unlink itself, rather than only at the call sites:
    every caller already checks, but this is the one line that actually
    removes a file, so it is the one line where being wrong costs a user
    their data. A path that escapes is a bug, not a request to be honoured.

    On Windows a file that is currently open cannot be removed at all: the
    app's own video players hold the clip they are showing, so deleting a
    clip the user is looking at raised WinError 32. The row had already gone
    by then, so the clip vanished from the library, the file stayed on disk
    and the request returned 500 — the user saw a failure for something that
    had mostly worked.

    Removing the clip from the library is what "delete" means to the person
    clicking it. The file is disk space, and core/housekeeping already finds
    and reclaims clip files with no row pointing at them, so a locked file is
    recovered on the next cleanup rather than lost.
    """
    try:
        base = root.resolve()
        # relative_to raises if `path` is not under root. The segments it
        # gives back are then used to WALK DOWN from the trusted root: each
        # step is whatever the directory listing says is there, matched by
        # name, so the thing that finally gets deleted came off the
        # filesystem rather than out of the request. That is belt to the
        # check's braces, and unlike a bare "raise if outside" it is a form a
        # taint scanner can follow — the same reason the branding asset
        # lookup is written this way.
        parts = path.resolve().relative_to(base).parts
    except (ValueError, OSError):
        print(f"refusing to delete {path} — outside {root}")
        return False
    target = base
    for part in parts:
        try:
            found = next((e for e in target.iterdir() if e.name == part), None)
        except OSError:
            return False
        if found is None:
            return True  # not on disk; missing_ok=True means that is a success
        target = found
    if target == base or not target.is_file():
        return True      # a directory, or the root itself: not ours to unlink
    try:
        target.unlink(missing_ok=True)
        return True
    except OSError as e:
        print(f"could not remove {path.name} yet ({e.__class__.__name__}); "
              f"housekeeping will reclaim it")
        return False


def _process_options(body, into: dict | None = None) -> dict:
    """Turn job options into the payload the worker reads.

    Shared by POST /jobs, POST /jobs/batch and PATCH /jobs/{id}: three doors
    onto the same settings, and a limit enforced at only two of them is not a
    limit. `into` lets a patch merge onto an existing snapshot instead of
    replacing it, since an unset field there means "leave this alone"."""
    payload: dict = dict(into or {})
    if getattr(body, "max_clips", None) is not None:
        payload["max_clips"] = max(1, min(10, body.max_clips))
    if getattr(body, "caption_style", None):
        payload["caption_style"] = body.caption_style
    if getattr(body, "captions", None) is not None:
        payload["captions"] = body.captions
    if getattr(body, "long_clips", None):
        payload["long_clips"] = True
    if getattr(body, "podcast", None):
        payload["podcast"] = True
    if getattr(body, "longform", None):
        payload["longform"] = body.longform
    if getattr(body, "watermark_profile_id", None):
        payload["watermark_profile_id"] = body.watermark_profile_id
    if getattr(body, "filter", None):
        from video.filters import is_valid

        if not is_valid(body.filter):
            raise HTTPException(400, f"unknown filter '{body.filter}'")
        payload["filter"] = body.filter
    if getattr(body, "min_score", None) is not None:
        payload["min_score"] = max(0, min(100, body.min_score))
    # Explicit "back to the default" — an absent field means unchanged, so a
    # toggle being switched off needs to say so.
    for key in getattr(body, "clear", []) or []:
        payload.pop(key, None)
    return payload


def create_app(config: dict, settings_path: Path) -> FastAPI:
    from server import feedback as feedback_mod

    feedback_mod.install_log_capture()  # pipeline prints -> bug-report log tail
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

    @app.get("/health/preflight")
    def preflight_check():
        """Can this install actually make a clip?

        An installed copy can be half-ready in ways a dev checkout never is —
        no FFmpeg, no Ollama, no model, no disk. Each used to surface as a
        stack trace deep inside a stage, long after the user pressed Generate.
        This names the missing piece and what to do about it, up front.
        """
        from core import preflight

        return preflight.run(config).as_dict()

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
            **_build_stamp(),
        }
        return stats

    # ---- feedback hub -----------------------------------------------------

    @app.get("/feedback/diagnostics")
    def feedback_diagnostics(video_id: str | None = None):
        """The auto-collected diagnostics block, exactly as it would be sent
        — the UI shows this under 'see what will be shared'."""
        d = db()
        try:
            return feedback_mod.collect_diagnostics(config, d, video_id)
        finally:
            d.conn.close()

    @app.post("/feedback/submit")
    def feedback_submit(body: FeedbackIn):
        """Build the report and send it through the feedback relay (which
        files it as a GitHub issue — no account needed by the user). The
        Markdown comes back either way, so the UI can save it to a file
        when the relay is unreachable or not configured."""
        missing = feedback_mod.missing_fields(body.kind, body.answers)
        if missing:
            raise HTTPException(400, f"please answer: {', '.join(missing)}")
        diagnostics = None
        if body.include_diagnostics:
            d = db()
            try:
                diagnostics = feedback_mod.collect_diagnostics(config, d, body.video_id)
            finally:
                d.conn.close()
        markdown = feedback_mod.build_markdown(body.kind, body.answers, diagnostics)
        title = feedback_mod.redact(body.title.strip())[:140]

        relay = (config.get("feedback") or {}).get("relay_url", "").strip()
        if not relay:
            return {"ok": False, "markdown": markdown,
                    "error": "feedback relay not configured in this build"}
        try:
            res = feedback_mod.submit_to_relay(
                relay, body.kind, title, markdown,
                body.areas, body.severity, feedback_mod.encode_images(body.images),
            )
            return {"ok": True, "url": res.get("url", ""), "markdown": markdown}
        except Exception as e:
            # The exception text stays here. An error from an HTTP call can
            # carry the relay URL, a token in a query string, or a path with
            # the user's name in it, and nothing needs it: FeedbackHub only
            # reads `ok`, and on false it saves the report to a file and
            # explains that itself. Passing the detail out gained nobody
            # anything and risked putting a secret on screen.
            print(f"feedback relay failed: {e}")  # local console only
            return {
                "ok": False,
                "markdown": markdown,
                "error": "could not reach the report service",
            }

    # ---- jobs -------------------------------------------------------------

    @app.post("/jobs")
    def create_job(body: JobIn, status_code=201):
        # Re-pasting an already-done URL without force would silently no-op —
        # tell the UI instead, so it can offer "process again with current
        # settings" (e.g. the same video in both 60s+ and regular modes).
        # Longform jobs skip the guard: making longform outputs of an
        # already-processed video is the normal case, not a re-run.
        from sources.dispatch import identify

        vid = ""
        if not body.force and not body.longform:
            _, vid = identify(body.url)
            if vid:
                d0 = db()
                try:
                    status = d0.video_status(vid)
                finally:
                    d0.close()
                if status == "done":
                    return {"job_id": None, "already_processed": True, "video_id": vid}
        elif not body.longform:
            _, vid = identify(body.url)

        payload = _process_options(body, {"url": body.url, "force": body.force})
        d = db()
        try:
            if queue.capacity(d) <= 0:
                raise HTTPException(
                    409,
                    f"the queue is full ({queue.MAX_ACTIVE} videos) — let one finish or remove one first",
                )
            # Same video already waiting or running: with a queue the likely
            # mistake is pasting a link twice into a batch, and processing it
            # twice costs an hour and produces duplicate clips.
            existing = queue.duplicate_of(d, vid) if vid else None
            if existing is not None:
                return {"job_id": None, "already_queued": True, "video_id": vid,
                        "queued_job_id": existing}
            # `identify` returns None for anything without an extractable
            # video id (a channel page, a live URL, a typo). The column is NOT
            # NULL, so passing None through turns a perfectly ordinary link
            # into a 500 — the pipeline is what should report a bad URL.
            job_id = d.add_job("process", json.dumps(payload), video_id=vid or "")
        finally:
            d.close()
        worker.notify()
        broadcaster.publish({"type": "queue"})
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

        # Normalised and checked before anything reads it — see picked_file().
        # The path comes from a native file dialog, so a rejection here means
        # the request did not come from the app. It hands back the resolved
        # path and the stat it already took, so nothing below has to ask the
        # filesystem about a user-supplied string a second time.
        picked = picked_file(body.path, _VIDEO_SUFFIXES)
        if picked is None:
            raise HTTPException(400, f"not a video file this app can open: {body.path}")
        src, _suffix, src_stat = picked

        # Must contain a video stream (catches audio files / random files).
        probe = sp.run(
            [ffprobe(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(src)],
            capture_output=True, text=True,
        )
        if probe.returncode != 0 or not probe.stdout.strip():
            raise HTTPException(400, "that file doesn't look like a video")

        # Length, while we already have the file open. Local files are the one
        # path where the queue can know a video's duration before it runs, and
        # duration is what its time estimate scales by.
        seconds = 0.0
        try:
            dur = sp.run(
                [ffprobe(), "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(src)],
                capture_output=True, text=True,
            )
            seconds = float(dur.stdout.strip() or 0)
        except (ValueError, OSError):
            pass  # only costs a less precise estimate

        # usedforsecurity=False because this is a naming scheme, not a defence:
        # it turns a path into a stable short id so re-importing the same file
        # reuses its downloads/ entry. Nothing trusts it, and collisions cost a
        # duplicate import rather than anything worse. Saying so explicitly
        # keeps the security scanners from reading it as a weak digest.
        #
        # src is already resolved and src_stat already taken, both by
        # picked_file() — re-doing either here would be a second unchecked
        # look at the same user-supplied string.
        vid = "local_" + hashlib.md5(
            f"{src}|{src_stat.st_size}|{int(src_stat.st_mtime)}".encode(),
            usedforsecurity=False,
        ).hexdigest()[:12]
        dest = data_dir / "downloads" / f"{vid}.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)

        codec = probe.stdout.strip()
        if not dest.exists():
            # H.264 sources are remuxed (no re-encode — fast, lossless) into
            # the pipeline's mp4 layout. Anything else — phone/GoPro HEVC,
            # AV1, VP9, ProRes, old AVI codecs — is converted to H.264 ONCE
            # here: every later stage (tracking + one decode per clip render)
            # reads this file, and non-H.264 codecs decode in software.
            converted = False
            if codec == "h264":
                remux = sp.run(
                    [ffmpeg(), "-y", "-i", str(src), "-c", "copy",
                     "-movflags", "+faststart", str(dest)],
                    capture_output=True, text=True,
                )
                converted = remux.returncode == 0
                if not converted:
                    dest.unlink(missing_ok=True)  # e.g. PCM audio mp4 can't carry
            if not converted:
                from video.encoding import hwaccel_input_args, video_encoder_args

                # -pix_fmt yuv420p: 10-bit sources (phone HDR, HEVC main10)
                # aren't accepted by h264_nvenc — normalize to 8-bit.
                reenc = sp.run(
                    [ffmpeg(), "-y", *hwaccel_input_args(), "-i", str(src),
                     *video_encoder_args(), "-pix_fmt", "yuv420p",
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
            if queue.capacity(d) <= 0:
                raise HTTPException(
                    409,
                    f"the queue is full ({queue.MAX_ACTIVE} videos) — let one finish or remove one first",
                )
            d.upsert_video(
                vid, title=title, channel_name=body.channel.strip(), duration=seconds
            )
            if body.channel.strip():
                from creator.identity import tag_video

                tag_video(d, vid, body.channel.strip(), platform=platform)
            payload = _process_options(body, {"url": f"local:{vid}"})
            job_id = d.add_job("process", json.dumps(payload), video_id=vid, title=title)
        finally:
            d.close()
        worker.notify()
        broadcaster.publish({"type": "queue"})
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

    # ---- queue ------------------------------------------------------------
    # Management around the job queue that already existed: ordering, pausing,
    # retrying, and an estimate. GET /jobs above is deliberately left alone —
    # ProcessingBar uses it as its liveness oracle when the WebSocket drops.

    @app.get("/queue")
    def queue_snapshot():
        d = db()
        try:
            return queue.snapshot(d)
        finally:
            d.close()

    @app.post("/queue/pause")
    def queue_pause():
        d = db()
        try:
            queue.set_paused(d, True)
        finally:
            d.close()
        broadcaster.publish({"type": "queue"})
        return {"paused": True}

    @app.post("/queue/resume")
    def queue_resume():
        d = db()
        try:
            queue.set_paused(d, False)
        finally:
            d.close()
        worker.notify()  # start the next video now, not after the idle poll
        broadcaster.publish({"type": "queue"})
        return {"paused": False}

    @app.post("/jobs/{job_id}/move")
    def move_job(job_id: int, body: QueueMoveIn):
        d = db()
        try:
            if body.to in ("top", "bottom"):
                # "Run this one next" is the actual need, and stepping a job up
                # six places one click at a time is not a way to express it.
                steps = len(d.queued_jobs())
                delta = -1 if body.to == "top" else 1
                moved = False
                for _ in range(steps):
                    if not queue.move(d, job_id, delta):
                        break
                    moved = True
            else:
                moved = queue.move(d, job_id, body.delta)
        finally:
            d.close()
        broadcaster.publish({"type": "queue"})
        return {"moved": moved}

    @app.post("/jobs/{job_id}/retry")
    def retry_job(job_id: int):
        d = db()
        try:
            new_id = queue.retry(d, job_id)
        finally:
            d.close()
        if new_id is None:
            raise HTTPException(409, "only a failed or cancelled job can be retried")
        worker.notify()
        broadcaster.publish({"type": "queue"})
        return {"job_id": new_id}

    @app.patch("/jobs/{job_id}")
    def patch_job(job_id: int, body: JobPatch):
        """Change one queued video's settings. Never touches any other job."""
        d = db()
        try:
            row = d.get_job(job_id)
            if row is None:
                raise HTTPException(404, "no such job")
            if row["status"] != "queued":
                raise HTTPException(409, "that video has already started — cancel it first")
            if row["type"] != "process":
                raise HTTPException(409, "only video jobs have these settings")
            current = json.loads(row["payload"]) if row["payload"] else {}
            keep = {k: current[k] for k in ("url", "force") if k in current}
            payload = _process_options(body, {**current, **keep})
            queue.update_settings(d, job_id, payload)
        finally:
            d.close()
        broadcaster.publish({"type": "queue"})
        return {"ok": True}

    @app.delete("/jobs/{job_id}")
    def delete_job(job_id: int):
        """Drop a waiting job. Any video already downloaded for it stays on
        disk — Settings > Storage owns file cleanup, not the queue."""
        d = db()
        try:
            row = d.get_job(job_id)
            if row is None:
                raise HTTPException(404, "no such job")
            if row["status"] == "running":
                raise HTTPException(409, "that video is processing — cancel it instead")
            d.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            d.conn.commit()
        finally:
            d.close()
        broadcaster.publish({"type": "queue"})
        return {"deleted": job_id}

    @app.post("/queue/clear")
    def clear_queue(body: dict):
        what = str(body.get("what", "completed"))
        d = db()
        try:
            removed = queue.clear(d, what)
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            d.close()
        broadcaster.publish({"type": "queue"})
        return {"deleted": removed}

    @app.post("/jobs/batch")
    def create_jobs_batch(body: BatchJobIn):
        """Queue a staged list, each video carrying its own settings.

        Deliberately tolerant: one unreadable link in a list of twelve reports
        itself and the other eleven are still queued. Failing the whole batch
        would mean re-staging the good ones."""
        from sources.dispatch import identify

        created: list[dict] = []
        skipped: list[dict] = []
        d = db()
        try:
            for item in body.items:
                url = item.url.strip()
                if not url:
                    continue
                # Pasting a block of text is how this box gets used, so a
                # stray line that isn't a link at all is an ordinary mistake —
                # catch it here rather than an hour later in the pipeline.
                # Only obvious non-links are refused: channel and live pages
                # have no video id either, and the single-URL path has always
                # accepted them, so URL-shaped input still goes through.
                if not (url.startswith("http://") or url.startswith("https://")
                        or url.startswith("local:")):
                    skipped.append({"url": url, "reason": "unrecognized"})
                    continue
                try:
                    _, vid = identify(url)
                except Exception as e:
                    skipped.append({"url": url, "reason": "unrecognized", "detail": str(e)[:200]})
                    continue
                if vid and not item.force:
                    if d.video_status(vid) == "done":
                        skipped.append({"url": url, "reason": "already_processed", "video_id": vid})
                        continue
                    if queue.duplicate_of(d, vid) is not None:
                        skipped.append({"url": url, "reason": "already_queued", "video_id": vid})
                        continue
                # Enforced here as well as in the UI: the cap is a real limit,
                # not a disabled button. Reported per item so the videos that
                # do fit are still queued.
                if queue.capacity(d) <= 0:
                    skipped.append({"url": url, "reason": "queue_full"})
                    continue
                # Same builder as POST /jobs and PATCH, so this row's options
                # get the identical clamps and filter validation.
                try:
                    payload = _process_options(item, {"url": url, "force": item.force})
                except HTTPException as e:
                    skipped.append({"url": url, "reason": "bad_option", "detail": str(e.detail)[:200]})
                    continue
                job_id = d.add_job("process", json.dumps(payload), video_id=vid or "")
                created.append({"url": url, "job_id": job_id, "video_id": vid or ""})
        finally:
            d.close()
        if created:
            worker.notify()  # no-op while stopped; wakes it if already running
        broadcaster.publish({"type": "queue"})
        return {"created": created, "skipped": skipped}

    @app.get("/jobs/{job_id}/log")
    def job_log(job_id: int, tail: int = 300):
        """The run's own log. The in-memory ring only holds minutes, which is
        no help for a batch that failed at 3am."""
        d = db()
        try:
            row = d.get_job(job_id)
        finally:
            d.close()
        if row is None:
            raise HTTPException(404, "no such job")
        path = Path(row["log_path"]) if row["log_path"] else None
        if path is None or not path.exists():
            return {"log": "", "missing": True}
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as e:
            raise HTTPException(500, f"could not read the log: {e}")
        return {"log": "\n".join(lines[-max(1, tail):]), "missing": False}

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

    @app.get("/storage")
    def storage():
        """Where the disk went, and how much of it is safe to reclaim."""
        from core import housekeeping

        d = db()
        try:
            found = housekeeping.survey(d, data_dir)
        finally:
            d.close()
        found.pop("_groups", None)  # Paths aren't JSON, and the UI doesn't need them
        return found

    @app.get("/storage/videos")
    def storage_videos():
        """What each processed video costs on disk, biggest first.

        The cleanup above only removes leftovers — it deliberately never
        touches a source or clip the library knows about, which on a real
        library is a rounding error next to the sources themselves. Deleting
        individual clips does not help either: it frees the clip file and
        leaves the multi-gigabyte source behind.

        So this lists the actual cost per video, and the UI pairs it with
        DELETE /videos/{id}, which removes the clips, the source, the
        transcript and the rows together.
        """
        def total(paths) -> int:
            out = 0
            for p in paths:
                try:
                    if p.is_file():
                        out += p.stat().st_size
                except OSError:
                    pass  # vanished under us; it costs nothing either way
            return out

        d = db()
        try:
            rows = d.conn.execute(
                "SELECT v.video_id, v.title, v.channel_name, v.created_at,"
                "       COUNT(c.id) AS clips"
                "  FROM videos v LEFT JOIN clips c ON c.video_id = v.video_id"
                " GROUP BY v.video_id"
            ).fetchall()
        finally:
            d.close()

        downloads, transcripts, clips_root = (
            data_dir / "downloads", data_dir / "transcripts", data_dir / "clips"
        )
        out = []
        for r in rows:
            vid = r["video_id"]
            source = total(
                p for p in downloads.iterdir()
                if p.is_file() and p.name.startswith(f"{vid}.")
            ) if downloads.is_dir() else 0
            transcript = total([transcripts / f"{vid}.json"])
            clip_bytes = 0
            if clips_root.is_dir():
                for creator in clips_root.iterdir():
                    if not creator.is_dir():
                        continue
                    for folder in creator.iterdir():
                        # Clip folders are named "<title> [<video_id>]".
                        if folder.is_dir() and folder.name.endswith(f"[{vid}]"):
                            clip_bytes += total(folder.rglob("*"))
            out.append({
                "video_id": vid,
                "title": r["title"] or vid,
                "channel": r["channel_name"] or "",
                "created_at": r["created_at"],
                "clips": r["clips"],
                "source_bytes": source,
                "transcript_bytes": transcript,
                "clip_bytes": clip_bytes,
                "total_bytes": source + transcript + clip_bytes,
            })
        out.sort(key=lambda v: -v["total_bytes"])
        return {"videos": out, "total_bytes": sum(v["total_bytes"] for v in out)}

    @app.post("/storage/cleanup")
    def storage_cleanup():
        """Delete leftovers from failed renders, interrupted downloads and
        old previews. Never removes a clip or a source the library still
        references."""
        from core import housekeeping

        d = db()
        try:
            result = housekeeping.clean(d, data_dir)
        finally:
            d.close()
        print(f"  Housekeeping: freed {result['bytes_freed']/1e9:.2f} GB "
              f"across {result['files_removed']} file(s)")
        return result

    @app.delete("/videos/{video_id}")
    def delete_video(video_id: str):
        """Delete a video: its download, transcript, clip files, and all its
        database rows. Only blocked if the video is ACTIVELY processing right
        now (not merely stuck in an in-progress status from a past crash)."""
        from core import cancel

        # This id comes off the URL and is then used to unlink files. A path
        # parameter cannot contain "/" — the route would not match — but it
        # CAN contain "\", which traverses just as well on Windows, and the
        # transcript line below is an unlink(). Real ids are the platform's
        # own (tw_2814378156, grMkMHCx9Bo, local_a7266e1b1a02), so requiring
        # a plain name costs nothing.
        if safe_name(video_id) is None:
            raise HTTPException(400, "invalid video id")

        if cancel.active_video() == video_id:
            raise HTTPException(409, "video is processing right now — cancel it first")

        # Remove files: download, transcript, and the clip folder.
        #
        # Everything below is chosen by LISTING a folder and comparing names,
        # never by building a path out of the id. That matters because these
        # are unlinks and an rmtree: a path assembled from a parameter is one
        # missed check away from deleting something else, whereas a path taken
        # from a directory listing is, by construction, a thing that is in
        # that directory.
        downloads = data_dir / "downloads"
        if downloads.is_dir():
            for f in downloads.iterdir():
                # Mirrors the old glob("<id>.*") exactly. Not `f.stem == id`,
                # which would miss yt-dlp's intermediates ("<id>.f137.mp4",
                # "<id>.mp4.part") and quietly leave gigabytes behind.
                if f.is_file() and f.name.startswith(f"{video_id}."):
                    _unlink_best_effort(f, data_dir)

        transcripts = data_dir / "transcripts"
        if transcripts.is_dir():
            for f in transcripts.iterdir():
                if f.is_file() and f.name == f"{video_id}.json":
                    _unlink_best_effort(f, data_dir)

        clips_root = data_dir / "clips"
        if clips_root.is_dir():
            for creator_dir in clips_root.iterdir():
                if not creator_dir.is_dir():
                    continue
                for clip_dir in creator_dir.iterdir():
                    # Folders are named "<title> [<video_id>]".
                    if clip_dir.is_dir() and clip_dir.name.endswith(f"[{video_id}]"):
                        shutil.rmtree(clip_dir, ignore_errors=True)

        d = db()
        try:
            d.delete_video(video_id)
        finally:
            d.close()
        return {"deleted": video_id}

    @app.delete("/clips/{clip_id}")
    def delete_clip(clip_id: int):
        """Delete ONE clip — its file, its editor preview, and its rows —
        so the creator can cull clips they won't post and reclaim the space.
        The video and every other clip are untouched."""
        d = db()
        try:
            path = d.delete_clip(clip_id)
        finally:
            d.close()
        if path is None:
            raise HTTPException(404, "no such clip")
        freed = 0
        clip_file = Path(path)
        # Only delete inside our own data dir — never follow a stray path out.
        if clip_file.exists() and data_dir in clip_file.resolve().parents:
            size = clip_file.stat().st_size
            if _unlink_best_effort(clip_file, data_dir):
                freed = size
        _unlink_best_effort(data_dir / "previews" / f"clip_{clip_id}.mp4", data_dir)
        return {"deleted": clip_id, "bytes_freed": freed}

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

    @app.post("/clips/{clip_id}/tighten")
    def tighten_clip(clip_id: int, body: TightenIn):
        """Propose cuts removing dead air and filler words. Returns keep
        ranges for the editor to draw — nothing is rendered or saved."""
        from analysis import tighten

        d = db()
        try:
            row = d.get_clip(clip_id)
            if row is None:
                raise HTTPException(404, "no such clip")
        finally:
            d.close()

        path = Path(row["path"]) if row["path"] else None
        if path is None or not path.exists():
            raise HTTPException(404, "clip file missing")

        # Word timings for this clip's window, made clip-relative.
        words: list[dict] = []
        tpath = data_dir / "transcripts" / f"{row['video_id']}.json"
        if tpath.exists():
            transcript = json.loads(tpath.read_text(encoding="utf-8"))
            for seg in transcript["segments"]:
                for w in seg.get("words") or []:
                    if w["end"] > row["start_s"] and w["start"] < row["end_s"]:
                        words.append({
                            "start": w["start"] - row["start_s"],
                            "end": w["end"] - row["start_s"],
                            "word": w.get("word", ""),
                        })

        return tighten.propose(
            path,
            words,
            duration=float(row["end_s"]) - float(row["start_s"]),
            drop_silence=body.silence,
            drop_fillers=body.fillers,
        )

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
        if body.caption_style:
            opts["caption_style"] = {**(opts.get("caption_style") or {}), **body.caption_style}
        if body.watermark is not None:
            # {} clears the watermark for this clip; a dict sets it.
            opts["watermark"] = body.watermark or None
        if body.normalize_audio is not None:
            opts["normalize_audio"] = body.normalize_audio

        candidate = ClipCandidate(
            start=row["start_s"], end=row["end_s"],
            score=row["score"] or 0, hook=row["hook"] or "",
        )
        prev_dir = data_dir / "previews"
        prev_dir.mkdir(parents=True, exist_ok=True)
        out = prev_dir / f"clip_{clip_id}.mp4"
        try:
            from transcription.transcriber import detected_language

            content_lang = detected_language(row["video_id"], data_dir / "transcripts")
            rendered, _ = _render_files(
                source, candidate, segments, prev_dir, config, opts, content_lang
            )
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

    # ---- multilingual publishing (separate pipeline; see multilingual/) ----

    @app.get("/languages")
    def list_languages():
        from multilingual import dub as dubber
        from multilingual.languages import LANGUAGES
        from video.captions import caption_font_for

        return {
            "languages": [
                {
                    "code": c,
                    "name": n,
                    "native": nat,
                    "can_dub": dubber.supported(c),
                    # The font a burn would actually use — non-Latin scripts
                    # get swapped to one that has the glyphs. None means the
                    # clip's own caption font is fine. The editor preview
                    # needs this or it shows tofu boxes for Hindi/Thai/etc.
                    "caption_font": caption_font_for(c, None),
                }
                for c, (n, nat, _p) in LANGUAGES.items()
            ],
            # Dubbing needs an optional local TTS package; everything else
            # works without it.
            "dubbing_available": dubber.available(),
        }

    @app.get("/voices")
    def list_voices(language: str):
        """Every dubbing voice available for a language, so the creator can
        choose one that matches the person on screen."""
        from multilingual import dub as dubber
        from multilingual.voices import DEFAULTS, list_for

        if not dubber.available():
            return {"voices": [], "default": None}
        return {
            "voices": list_for(language, data_dir / "voices"),
            "default": DEFAULTS.get(language),
        }

    @app.get("/voices/preview")
    def preview_voice(language: str, voice: str | None = None):
        """A spoken sample of one voice. GET so the player can point at it
        directly — the app's CSP allows media from this API, not blobs."""
        from multilingual import dub as dubber
        from multilingual.languages import sample_text
        from multilingual.voices import resolve

        if not dubber.available():
            raise HTTPException(400, "dubbing package not installed")
        voices_dir = data_dir / "voices"
        name = dubber.ensure_voice(language, voices_dir, voice)
        if name is None:
            raise HTTPException(400, f"no voice available for {language}")
        _n, speaker = resolve(voice, language)
        # Must be spoken IN the language being auditioned. Reading English
        # through a Turkish voice only demonstrates an accent, which tells
        # you nothing about the dub — so refuse rather than mislead.
        sample = sample_text(language)
        if sample is None:
            raise HTTPException(400, f"no sample sentence for {language}")
        # Built from the RESOLVED name, not the raw `voice` parameter. The old
        # line stripped "#" and "/" from whatever the caller sent, which left
        # "\" and ".." intact — enough to write the preview outside previews/
        # on Windows. resolve() has already validated the shape of `name`.
        safe = f"{name}_{speaker}" if speaker is not None else name
        out = data_dir / "previews" / f"voice_{safe}.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists() and not dubber._speak(
            sample, name, voices_dir, out, speaker=speaker
        ):
            raise HTTPException(500, "could not synthesize a sample")
        return FileResponse(out, media_type="audio/wav")

    @app.post("/translate")
    def translate_clips(body: TranslateIn):
        """Queue translation or export. Runs on finished clips only — the
        clips themselves are never modified.

        stage='translate' only produces text for review; stage='export'
        writes the files using whatever text has been reviewed."""
        from multilingual.languages import is_supported

        langs = [c for c in body.languages if is_supported(c)]
        if not langs:
            raise HTTPException(400, "pick at least one supported language")
        if not body.clip_ids:
            raise HTTPException(400, "no clips selected")
        stage = body.stage if body.stage in ("translate", "export") else "export"
        if stage == "export" and not body.folder:
            raise HTTPException(400, "choose an export folder")
        d = db()
        try:
            job_id = d.add_job("translate", json.dumps({
                "clip_ids": body.clip_ids[:50],
                "languages": langs,
                "stage": stage,
                "folder": body.folder,
                "include_video": body.include_video,
                "burn": body.burn,
                "dub": body.dub,
                "subtitles": body.subtitles,
                "post_text": body.post_text,
                "voices": body.voices or {},
                "style": body.style or {},
            }))
        finally:
            d.close()
        worker.notify()
        return {"job_id": job_id, "languages": langs, "clips": len(body.clip_ids)}

    @app.get("/clips/{clip_id}/translations")
    def clip_translations(clip_id: int):
        """The translated caption text held for review, per language, with
        the original lines beside it so the two can be compared."""
        d = db()
        try:
            clip = d.get_clip(clip_id)
            if clip is None:
                raise HTTPException(404, "clip not found")
            return {
                "source": _clip_captions(clip),
                "translations": [
                    {
                        "language": r["language"],
                        "lines": json.loads(r["lines"]),
                        "post": json.loads(r["post"] or "{}"),
                        "edited": bool(r["edited"]),
                        "updated_at": r["updated_at"],
                    }
                    for r in d.translations_for(clip_id)
                ],
            }
        finally:
            d.close()

    @app.put("/clips/{clip_id}/translations/{language}")
    def save_clip_translation(clip_id: int, language: str, body: TranslationPatch):
        """Store a creator's corrections. Marked `edited`, which stops a
        later re-translation from overwriting them."""
        from multilingual.languages import is_supported

        if not is_supported(language):
            raise HTTPException(400, f"unsupported language {language!r}")
        d = db()
        try:
            if d.get_clip(clip_id) is None:
                raise HTTPException(404, "clip not found")
            existing = d.get_translation(clip_id, language)
            post = body.post if body.post is not None else (
                json.loads(existing["post"] or "{}") if existing else {}
            )
            d.save_translation(
                clip_id, language,
                json.dumps([dict(line) for line in body.lines], ensure_ascii=False),
                json.dumps(post, ensure_ascii=False),
                edited=True,
            )
        finally:
            d.close()
        return {"saved": language, "lines": len(body.lines)}

    def _creator_of(d, clip) -> int | None:
        row = d.conn.execute(
            "SELECT creator_id FROM videos WHERE video_id = ?", (clip["video_id"],)
        ).fetchone()
        return row["creator_id"] if row else None

    @app.get("/clips/{clip_id}/glossary")
    def clip_glossary(clip_id: int):
        """Words kept out of translation for this clip's creator: the list
        actually in force, plus anything explicitly ruled out."""
        from multilingual import glossary

        d = db()
        try:
            clip = d.get_clip(clip_id)
            if clip is None:
                raise HTTPException(404, "clip not found")
            creator_id = _creator_of(d, clip)
            vrow = d.conn.execute(
                "SELECT title FROM videos WHERE video_id = ?", (clip["video_id"],)
            ).fetchone()
            rules = {r["term"]: r["rule"] for r in d.terms_for(creator_id)}
            return {
                "protected": glossary.build(d, creator_id, vrow["title"] if vrow else ""),
                "ignored": [t for t, r in rules.items() if r == "ignore"],
                "mine": [t for t, r in rules.items() if r == "protect"],
            }
        finally:
            d.close()

    @app.post("/clips/{clip_id}/glossary")
    def rule_clip_term(clip_id: int, body: TermIn):
        """protect = keep this word as written; ignore = translate it
        normally even if detected; auto = forget the ruling."""
        term = body.term.strip()
        if not term:
            raise HTTPException(400, "empty term")
        if body.rule not in ("protect", "ignore", "auto"):
            raise HTTPException(400, "rule must be protect, ignore or auto")
        d = db()
        try:
            clip = d.get_clip(clip_id)
            if clip is None:
                raise HTTPException(404, "clip not found")
            creator_id = _creator_of(d, clip)
            if body.rule == "auto":
                d.clear_term(creator_id, term)
            else:
                d.set_term(creator_id, term, body.rule)
        finally:
            d.close()
        return {"term": term, "rule": body.rule}

    @app.delete("/clips/{clip_id}/translations/{language}")
    def discard_clip_translation(clip_id: int, language: str):
        """Throw away a stored translation so the next Translate run redoes
        it — the way out once corrections are no longer wanted."""
        d = db()
        try:
            d.delete_translation(clip_id, language)
        finally:
            d.close()
        return {"discarded": language}

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

    # ---- watermark & branding ------------------------------------------------

    @app.get("/branding")
    def list_branding():
        d = db()
        try:
            rows = d.list_branding()
        finally:
            d.close()
        return [{"id": r["id"], "name": r["name"], "config": json.loads(r["config"])} for r in rows]

    @app.post("/branding")
    def create_branding(body: BrandingIn):
        d = db()
        try:
            pid = d.add_branding(body.name.strip() or "Branding", json.dumps(body.config))
        finally:
            d.close()
        return {"id": pid}

    @app.put("/branding/{profile_id}")
    def update_branding(profile_id: int, body: BrandingIn):
        d = db()
        try:
            if d.get_branding(profile_id) is None:
                raise HTTPException(404, "no such branding profile")
            d.update_branding(profile_id, body.name.strip() or "Branding", json.dumps(body.config))
        finally:
            d.close()
        return {"id": profile_id}

    @app.delete("/branding/{profile_id}")
    def delete_branding(profile_id: int):
        d = db()
        try:
            d.delete_branding(profile_id)
        finally:
            d.close()
        return {"deleted": profile_id}

    @app.post("/branding/asset")
    def upload_branding_asset(body: BrandingAssetIn):
        """Import a logo file from this computer into the branding assets
        folder, deduped by content hash. Returns the stored asset filename to
        put in a profile's config.image_asset."""
        import hashlib

        # The suffix check lives in picked_file() so the path is validated
        # before it is read, not after — this used to load up to 20 MB off any
        # path the request named and only then decide it was the wrong type.
        picked = picked_file(body.path, _IMAGE_SUFFIXES)
        if picked is None:
            raise HTTPException(400, "use a PNG (transparent preferred), JPG or WebP")
        src, suffix, _st = picked

        # Reading the file the user picked is the feature; code scanning flags
        # it as py/path-injection and it is dismissed there.
        data = src.read_bytes()
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(400, "image too large (max 20 MB)")
        # `suffix` is the constant that matched out of _IMAGE_SUFFIXES, not the
        # text on the end of the filename, so the name written into the assets
        # folder can only ever end in one of the four extensions above.
        name = hashlib.sha256(data).hexdigest()[:16] + suffix
        assets = data_dir / "branding" / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        dest = assets / name
        if not dest.exists():  # dedup: identical content is stored once
            dest.write_bytes(data)
        return {"asset": name}

    @app.get("/branding/asset/{name}")
    def get_branding_asset(name: str):
        # Found by listing the folder rather than by joining the name onto
        # it, so the served file can only ever be one that is genuinely in
        # there. The previous version resolved the path and then checked its
        # parents, which was correct but relied on getting that check right;
        # this cannot construct a path outside the folder in the first place.
        assets = data_dir / "branding" / "assets"
        if safe_name(name) is None:
            raise HTTPException(404, "no such asset")
        try:
            match = next((p for p in assets.iterdir() if p.name == name and p.is_file()), None)
        except OSError:
            match = None
        if match is None:
            raise HTTPException(404, "no such asset")
        return FileResponse(match)

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
                " ORDER BY knowledge_type, times_seen DESC, created_at DESC",
                (creator_id,),
            ).fetchall()
            # Say WHY each fact is or isn't being used, using the same rules
            # scoring does, so the page never shows a catchphrase as if it
            # were in play when it's still waiting to be heard again.
            from creator.models import (
                DORMANT_DAYS,
                DORMANT_VIDEOS,
                MIN_PHRASE_REPEATS,
                PHRASE_TYPES,
            )
            from creator.retrieval import dormant_before

            cutoff = dormant_before(d, creator_id, DORMANT_DAYS, DORMANT_VIDEOS)

            def knowledge_state(k) -> str:
                heard = k["last_seen"] or k["created_at"]
                if cutoff is not None and heard < cutoff:
                    return "dormant"
                if k["knowledge_type"] in PHRASE_TYPES and (k["times_seen"] or 1) < MIN_PHRASE_REPEATS:
                    return "candidate"
                return "active"

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
            "knowledge": [{**dict(k), "state": knowledge_state(k)} for k in knowledge],
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

    @app.delete("/creators/{creator_id}/knowledge")
    def clear_unused_knowledge(creator_id: int):
        """Drop everything that isn't in play — unconfirmed catchphrases and
        dormant facts.

        Profiles learned before repetition was tracked are full of phrases the
        creator said exactly once. They already score nothing, but they're
        still wrong to look at, and this clears them in one go without
        touching the facts that earned their place."""
        from creator.models import DORMANT_DAYS, DORMANT_VIDEOS, MIN_PHRASE_REPEATS, PHRASE_TYPES
        from creator.retrieval import dormant_before

        d = db()
        try:
            cutoff = dormant_before(d, creator_id, DORMANT_DAYS, DORMANT_VIDEOS)
            slots = ",".join("?" * len(PHRASE_TYPES))
            cur = d.conn.execute(
                "DELETE FROM creator_knowledge WHERE creator_id = ? AND ("
                f"  (knowledge_type IN ({slots}) AND times_seen < ?)"
                "  OR (? IS NOT NULL AND COALESCE(last_seen, created_at) < ?))",
                (creator_id, *PHRASE_TYPES, MIN_PHRASE_REPEATS, cutoff, cutoff),
            )
            d.conn.commit()
            deleted = cur.rowcount
        finally:
            d.close()
        return {"deleted": deleted}

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

    @app.delete("/creators/{creator_id}")
    def delete_creator(creator_id: int):
        """Remove a creator profile entirely — channels, knowledge, events,
        feedback and glossary rulings.

        Videos and clips are kept and simply unlinked, so tidying the list
        can never cost footage. A video left behind is unattributed until a
        creator is detected for it again."""
        d = db()
        try:
            row = d.conn.execute(
                "SELECT display_name FROM creators WHERE creator_id = ?", (creator_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(404, "no such creator")
            counts = d.delete_creator(creator_id)
        finally:
            d.close()
        return {"deleted": creator_id, "name": row["display_name"], **counts}

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

    @app.post("/creators/{creator_id}/branding")
    def set_creator_branding(creator_id: int, body: CreatorBrandingIn):
        """Set this creator's DEFAULT branding profile — auto-applied to their
        videos when a job doesn't pick one. For clippers who make videos for
        several creators, each gets their own logo without re-picking."""
        d = db()
        try:
            d.conn.execute(
                "UPDATE creators SET default_branding_id = ? WHERE creator_id = ?",
                (body.branding_id, creator_id),
            )
            d.conn.commit()
        finally:
            d.close()
        return {"creator_id": creator_id, "default_branding_id": body.branding_id}

    # ---- models ------------------------------------------------------------

    ollama_host = config["llm"].get("ollama_host", "http://localhost:11434").rstrip("/")

    @app.get("/models")
    def models():
        from llm.manager import RECOMMENDATIONS, installed_models, recommend_for

        try:
            installed = installed_models(ollama_host)
        except Exception:
            raise HTTPException(503, "Ollama is not reachable — is it running?")

        # Pick the model for THIS machine server-side, so the setup wizard and
        # the Models page can never give contradictory advice.
        vram_gb = None
        try:
            import pynvml

            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            vram_gb = pynvml.nvmlDeviceGetMemoryInfo(handle).total / 1e9
            pynvml.nvmlShutdown()
        except Exception:
            pass  # no NVIDIA GPU, or the library isn't available — CPU advice

        return {
            "active": config["llm"]["backend"],
            "installed": installed,
            "recommendations": [
                {"hardware": h, "model": m, "note": n} for h, m, n in RECOMMENDATIONS
            ],
            "recommended": recommend_for(vram_gb),
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
            "content_language": config.get("content_language", "auto"),
            "translation_model": config.get("llm", {}).get("translation_model", ""),
        }

    @app.patch("/settings")
    def patch_settings(body: SettingsPatch):
        text = settings_path.read_text(encoding="utf-8")
        if body.translation_model is not None:
            # Nested under llm:, so patch it in place rather than via the
            # flat top-level key rewrite below.
            text = settings_path.read_text(encoding="utf-8")
            text, n = re.subn(r'(?m)^(\s*translation_model:\s*).*$',
                              rf'\g<1>"{body.translation_model}"', text, count=1)
            if n:
                settings_path.write_text(text, encoding="utf-8")
                config.setdefault("llm", {})["translation_model"] = body.translation_model
        if body.content_language is not None and not re.fullmatch(
            r"auto|[a-z]{2,3}", body.content_language
        ):
            raise HTTPException(400, "content_language must be 'auto' or an ISO code")
        edits = {
            "model": body.model,
            "channel": f'"{body.channel}"' if body.channel is not None else None,
            "auto_upload": str(body.auto_upload).lower() if body.auto_upload is not None else None,
            "privacy": body.privacy,
            "content_language": body.content_language,
        }
        for key, value in edits.items():
            if value is None:
                continue
            text, n = re.subn(rf"(?m)^({key}:\s*)\S*", rf"\g<1>{value}", text, count=1)
            if n == 0:
                raise HTTPException(400, f"no '{key}:' line in settings.yaml")
        settings_path.write_text(text, encoding="utf-8")
        if body.content_language is not None:
            # Applies to the NEXT processed video — no restart needed.
            config["content_language"] = body.content_language
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
