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

from core import cancel, progress
from core.cancel import CancelledError
from core.prefetch import Prefetcher
from core.state import StateDB
from server.events import broadcaster


class Worker(threading.Thread):
    def __init__(self, config: dict):
        super().__init__(daemon=True, name="pipeline-worker")
        self.config = config
        self.db_path = Path(config["paths"]["data_dir"]) / "state.db"
        self.prefetch = Prefetcher(
            self.db_path, Path(config["paths"]["data_dir"]) / "downloads"
        )
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
        # Videos orphaned mid-pipeline by a crash/force-close get marked failed
        # so they're never permanently "stuck" and can be deleted or retried.
        recovered = db.recover_stuck_videos()
        if recovered:
            print(f"Recovered {recovered} interrupted video(s) (marked failed)")
        # One-time catch-up: organize the existing library into creator
        # profiles (no-op once every video is tagged).
        try:
            from creator import identity

            tagged = identity.backfill(db)
            if tagged:
                print(f"Creator profiles: organized {tagged} existing video(s)")
        except Exception as e:
            print(f"Creator backfill failed (non-fatal): {e}")

        # Pipeline progress events get tagged with the active job and fanned
        # out to UI clients.
        current_job_id: list[int | None] = [None]
        progress.set_handler(
            lambda event: broadcaster.publish(
                {
                    "type": "progress",
                    # Prefetch downloads belong to a FUTURE job, not the one
                    # running now — never attribute them to it.
                    "job_id": None if event.get("prefetch") else current_job_id[0],
                    **event,
                }
            )
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
            if job["type"] == "process":
                from sources.dispatch import identify

                _, vid = identify(payload["url"])
                cancel.set_active(vid)  # mark which video is genuinely running
                # Never race a half-written prefetch of THIS video; once it's
                # settled, kick off the download of the NEXT queued video so
                # it overlaps this job's GPU work.
                self.prefetch.wait_for(vid)
            self.prefetch.maybe_start(db)
            try:
                if job["type"] == "process":
                    import copy

                    from core.pipeline import process_video

                    cfg = self.config
                    if (
                        payload.get("max_clips")
                        or payload.get("caption_style")
                        or "captions" in payload
                        or payload.get("long_clips")
                        or payload.get("min_score") is not None
                        or payload.get("watermark_profile_id")
                        or payload.get("filter")
                        or payload.get("split_position")
                        or payload.get("reaction")
                    ):
                        cfg = copy.deepcopy(self.config)
                    if "captions" in payload:
                        cfg["clips"]["captions"] = bool(payload["captions"])
                    if payload.get("min_score") is not None:
                        cfg["clips"]["min_score"] = int(payload["min_score"])
                    if payload.get("long_clips"):
                        # TikTok monetization requires >60s: target 61-180s clips.
                        cfg["clips"]["min_duration"] = 61
                        cfg["clips"]["max_duration"] = 180
                    if payload.get("filter"):
                        cfg["clips"]["filter"] = payload["filter"]
                    if payload.get("reaction") in ("auto", "always"):
                        # Reaction pipeline for this job (isolated path;
                        # 'auto' still defers to the standard pipeline
                        # unless a two-region layout is detected).
                        cfg["clips"]["reaction"] = payload["reaction"]
                    if payload.get("split_position") in ("top", "bottom"):
                        # Facecam band default for gaming split layouts,
                        # chosen in the Generate bar (per-clip editor wins).
                        cfg["clips"]["split_position"] = payload["split_position"]
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
                    if payload.get("watermark_profile_id"):
                        # Branding chosen in the Generate bar: resolve the
                        # profile to its config and apply it to every clip.
                        row = db.get_branding(int(payload["watermark_profile_id"]))
                        if row:
                            cfg["clips"]["watermark"] = json.loads(row["config"])
                    if payload.get("longform"):
                        # Separate longform system (1920x1080 horizontal),
                        # built on the same stages — Shorts path untouched.
                        from longform.process import process_longform

                        process_longform(payload["url"], cfg, db, payload["longform"])
                    else:
                        process_video(payload["url"], cfg, db, force=payload.get("force", False))
                elif job["type"] == "render":
                    self._rerender_clip(db, payload)
                else:
                    raise ValueError(f"Unknown job type {job['type']!r}")
                db.set_job(job["id"], status="done")
                broadcaster.publish({"type": "job", "job_id": job["id"], "status": "done"})
            except CancelledError:
                db.set_job(job["id"], status="cancelled", error="Cancelled by user")
                broadcaster.publish({"type": "job", "job_id": job["id"], "status": "cancelled"})
                print(f"Job {job['id']} cancelled by user")
            except Exception as e:
                traceback.print_exc()
                db.set_job(job["id"], status="failed", error=str(e)[:2000])
                broadcaster.publish(
                    {"type": "job", "job_id": job["id"], "status": "failed", "error": str(e)[:500]}
                )
            finally:
                current_job_id[0] = None
                cancel.set_active(None)

    def _rerender_clip(self, db: StateDB, payload: dict) -> None:
        """Re-render one clip from the original source video, with optionally
        edited timestamps and/or render options (crop mode, caption style).
        The clip's user-visible metadata survives the re-render."""
        from core.models import ClipCandidate, Segment
        from core.pipeline import _register_clip, _render_files, _safe_name
        from analysis.metadata import ClipMetadata
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

        # Keep the user-facing metadata across the re-render — a re-render
        # never needs a fresh LLM metadata call.
        keep = {
            "title": clip["title"],
            "description": clip["description"],
            "hashtags": clip["hashtags"],
            "status": clip["status"],
        }
        old_path = Path(clip["path"]) if clip["path"] else None
        db.conn.execute("DELETE FROM clips WHERE id = ?", (clip["id"],))  # avoid UNIQUE clash
        db.conn.commit()

        from transcription.transcriber import detected_language

        content_lang = detected_language(video_id, data_dir / "transcripts")
        final_path, _ = _render_files(
            source, candidate, segments, clip_dir, self.config, render_opts, content_lang
        )
        meta = ClipMetadata(
            title=clip["title"] or "",
            description=clip["description"] or "",
            hashtags=_json.loads(clip["hashtags"]) if clip["hashtags"] else [],
        )
        rendered = _register_clip(db, video_id, candidate, final_path, meta, _json.dumps(render_opts))

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
