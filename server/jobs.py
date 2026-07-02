"""Single background worker processing the SQLite job queue.

One worker, sequential jobs: video processing saturates the GPU/CPU anyway,
so parallel jobs on consumer hardware only make everything slower. The queue
lives in SQLite, so it survives restarts; jobs left 'running' by a crash are
re-queued at startup, and the pipeline resumes from its last completed stage.

Job types:
  process - {"url": ...}                       full pipeline for one video
  render  - {"clip_id", "start"?, "end"?}      re-render one clip (edited
                                               timestamps and/or captions)
"""

import json
import threading
import traceback
from pathlib import Path

from core import progress
from core.state import StateDB
from server.events import broadcaster


class Worker(threading.Thread):
    def __init__(self, config: dict):
        super().__init__(daemon=True, name="pipeline-worker")
        self.config = config
        self.db_path = Path(config["paths"]["data_dir"]) / "state.db"
        self._wake = threading.Event()
        self._stop = threading.Event()

    def notify(self) -> None:
        """Called by the API when a job is enqueued."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        db = StateDB(self.db_path)  # sqlite: one connection per thread
        requeued = db.recover_interrupted_jobs()
        if requeued:
            print(f"Re-queued {requeued} interrupted job(s)")

        # Pipeline progress events get tagged with the active job and fanned
        # out to UI clients.
        current_job_id: list[int | None] = [None]
        progress.set_handler(
            lambda event: broadcaster.publish({"type": "progress", "job_id": current_job_id[0], **event})
        )

        while not self._stop.is_set():
            job = db.claim_next_job()
            if job is None:
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                continue

            current_job_id[0] = job["id"]
            payload = json.loads(job["payload"])
            broadcaster.publish({"type": "job", "job_id": job["id"], "status": "running"})
            try:
                if job["type"] == "process":
                    import copy

                    from core.pipeline import process_video

                    cfg = self.config
                    if payload.get("max_clips") or payload.get("caption_style") or "captions" in payload:
                        cfg = copy.deepcopy(self.config)
                    if "captions" in payload:
                        cfg["clips"]["captions"] = bool(payload["captions"])
                    if payload.get("max_clips"):
                        n = int(payload["max_clips"])
                        cfg["clips"]["max_clips_per_video"] = n
                        # The rerank pool must be at least as big as the ask.
                        pool = cfg.setdefault("scoring", {}).get("rerank_pool", 8)
                        cfg["scoring"]["rerank_pool"] = max(pool, n)
                    if payload.get("caption_style"):
                        # Style chosen in the Generate bar: applied to every
                        # clip of this job (and persisted per clip).
                        cfg["clips"]["caption_style"] = payload["caption_style"]
                    process_video(payload["url"], cfg, db, force=payload.get("force", False))
                elif job["type"] == "render":
                    self._rerender_clip(db, payload)
                else:
                    raise ValueError(f"Unknown job type {job['type']!r}")
                db.set_job(job["id"], status="done")
                broadcaster.publish({"type": "job", "job_id": job["id"], "status": "done"})
            except Exception as e:
                traceback.print_exc()
                db.set_job(job["id"], status="failed", error=str(e)[:2000])
                broadcaster.publish(
                    {"type": "job", "job_id": job["id"], "status": "failed", "error": str(e)[:500]}
                )
            finally:
                current_job_id[0] = None

    def _rerender_clip(self, db: StateDB, payload: dict) -> None:
        """Re-render one clip from the original source video, with optionally
        edited timestamps and/or render options (crop mode, caption style).
        The clip's user-visible metadata survives the re-render."""
        from core.models import ClipCandidate, Segment
        from core.pipeline import _render_one, _safe_name  # same render path as the pipeline
        import json as _json

        clip = db.get_clip(payload["clip_id"])
        if clip is None:
            raise ValueError(f"No clip with id {payload['clip_id']}")

        video_id = clip["video_id"]
        data_dir = Path(self.config["paths"]["data_dir"])
        source = data_dir / "downloads" / f"{video_id}.mp4"
        if not source.exists():
            raise FileNotFoundError(f"Source video missing: {source}")

        transcript = _json.loads((data_dir / "transcripts" / f"{video_id}.json").read_text(encoding="utf-8"))
        segments = [Segment(**s) for s in transcript["segments"]]

        start = float(payload.get("start", clip["start_s"]))
        end = float(payload.get("end", clip["end_s"]))
        candidate = ClipCandidate(
            start=start, end=end, score=clip["score"], hook=clip["hook"] or "",
            subscores=_json.loads(clip["scores"]) if clip["scores"] else None,
        )

        # Persisted render options, overlaid with this edit's changes.
        render_opts = _json.loads(clip["render_opts"]) if clip["render_opts"] else {}
        incoming = payload.get("render_opts") or {}
        if "caption_style" in incoming:
            merged_style = {**render_opts.get("caption_style", {}), **(incoming["caption_style"] or {})}
            render_opts["caption_style"] = merged_style
        render_opts.update({k: v for k, v in incoming.items() if k != "caption_style"})

        from llm.registry import create_backend

        llm = create_backend(self.config["llm"])
        vrow = db.conn.execute(
            "SELECT title, channel_name FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        video_title = vrow["title"] if vrow else video_id
        channel = vrow["channel_name"] if vrow else ""

        clip_dir = (
            data_dir / "clips"
            / _safe_name(channel, "unknown-channel")
            / f"{_safe_name(video_title, video_id)} [{video_id}]"
        )

        # Keep the user-facing metadata across the re-render.
        keep = {
            "title": clip["title"],
            "description": clip["description"],
            "hashtags": clip["hashtags"],
            "status": clip["status"],
        }
        old_path = Path(clip["path"]) if clip["path"] else None
        db.conn.execute("DELETE FROM clips WHERE id = ?", (clip["id"],))  # avoid UNIQUE clash
        db.conn.commit()

        rendered = _render_one(
            source, video_id, video_title, candidate,
            segments, clip_dir, self.config, db, llm, render_opts=render_opts,
        )
        new_row = db.conn.execute(
            "SELECT id FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
            (video_id, round(start, 2), round(end, 2)),
        ).fetchone()
        if new_row:
            restore = {k: v for k, v in keep.items() if v}
            restore["render_opts"] = _json.dumps(render_opts)
            db.set_clip(new_row["id"], **restore)
        if rendered and old_path and old_path.exists() and old_path != rendered.path:
            old_path.unlink(missing_ok=True)
