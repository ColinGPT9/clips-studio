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
                    from core.pipeline import process_video

                    process_video(payload["url"], self.config, db, force=payload.get("force", False))
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
        """Re-render one clip, optionally with edited timestamps."""
        from core.models import ClipCandidate, Segment
        from core.pipeline import _render_one  # same render path as the pipeline
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

        from llm.registry import create_backend

        llm = create_backend(self.config["llm"])
        row = db.conn.execute("SELECT title FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        video_title = row["title"] if row else video_id

        # Render to the (possibly new) timestamp-based filename, then update
        # the row in place so the clip keeps its id and metadata.
        old_path = Path(clip["path"]) if clip["path"] else None
        db.conn.execute("DELETE FROM clips WHERE id = ?", (clip["id"],))  # avoid UNIQUE clash
        db.conn.commit()
        rendered = _render_one(
            source, video_id, video_title, candidate,
            segments, data_dir / "clips" / video_id, self.config, db, llm,
        )
        if rendered and old_path and old_path.exists() and old_path != rendered.path:
            old_path.unlink(missing_ok=True)
