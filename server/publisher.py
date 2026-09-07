"""The thread that uploads finished clips to YouTube.

Deliberately NOT the pipeline worker, for three reasons that each on their own
would be enough:

* An upload is minutes of network I/O. Behind an hour of GPU work, "Upload now"
  is a lie; in front of it, one person's slow upstream stalls an overnight
  batch.
* The pipeline worker claims nothing while the queue is paused (core/queue.py),
  and paused is its DEFAULT state. An upload queued there would simply never
  start, with no visible reason.
* `recover_interrupted_jobs()` re-queues every running job on startup. For a
  video that resumes a stage. For an upload it would post the same video to
  someone's channel twice.

That last one is why publish jobs live in their own table with the opposite
recovery rule: a publish caught by a crash becomes 'interrupted' and stops
there, and the user is told to check their channel rather than have the app
guess.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.state import StateDB
from server import youtube_service as service
from server.events import broadcaster

# How long after an upload to read the video's status back. YouTube needs a
# moment to process before rejection or the privacy lock is visible.
VERIFY_AFTER_SECONDS = 60


class PublishWorker(threading.Thread):
    def __init__(self, config: dict, db_path: Path, data_dir: Path):
        super().__init__(daemon=True, name="publish-worker")
        self.config = config
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._cancelled: set[int] = set()
        self._verify: list[tuple[float, int, str]] = []  # (due_at, clip_id, video_id)

    # ---- lifecycle -------------------------------------------------------

    def notify(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def cancel(self, publish_job_id: int) -> None:
        self._cancelled.add(int(publish_job_id))

    def run(self) -> None:
        db = StateDB(self.db_path)  # one connection per thread; sqlite insists
        try:
            recovered = db.recover_running_publish_jobs()
            if recovered:
                print(f"{recovered} upload(s) were interrupted by a restart and will not retry.")

            while not self._stop.is_set():
                self._run_due_verifications(db)
                job = db.claim_next_publish_job()
                if job is None:
                    self._wake.wait(timeout=2.0)
                    self._wake.clear()
                    continue
                try:
                    self._publish(db, job)
                except Exception as e:
                    self._fail(db, job, e)
        finally:
            db.close()

    # ---- one job ---------------------------------------------------------

    def _publish(self, db: StateDB, job) -> None:
        from publish.base import PublishRequest
        from publish.errors import PublishCancelled, PublishError, QuotaExceeded

        job_id = job["id"]
        request_data = json.loads(job["request"])

        clip = self._resolve_clip(db, job)
        if clip is None:
            raise PublishError(
                "That clip no longer exists. It may have been deleted while the upload was queued."
            )
        if clip["id"] != job["clip_id"]:
            # A chained render replaced the row. Follow it.
            db.set_publish_job_clip(job_id, clip["id"])
        clip_id = clip["id"]

        path = Path(clip["path"] or "")
        if not path.exists():
            raise PublishError("This clip's file is missing. Re-render it and try again.")

        ledger = service.load_ledger(db)
        if ledger.exhausted():
            raise QuotaExceeded(
                "Your API key's daily upload allowance is used up. It resets at "
                "midnight Pacific time.",
                resets_at=ledger.blocked_until,
            )

        self._emit(job_id, clip_id, phase="prepare", fraction=0.02,
                   message="Getting the clip ready")

        # channel_id selects which connected channel's token to publish with;
        # it is not part of the video's metadata, so it comes out here.
        channel_id = request_data.pop("channel_id", None)
        publisher = service.make_publisher(
            self.config, self.data_dir, channel_id=channel_id
        )
        request = PublishRequest(
            video_path=path,
            **{k: v for k, v in request_data.items() if k != "video_path"},
        )
        if request.thumbnail:
            request.thumbnail = Path(request.thumbnail)

        def on_progress(label: str, done: int, total: int) -> None:
            fraction = 0.05 + 0.85 * (done / total) if total else 0.05
            self._emit(job_id, clip_id, phase="upload", fraction=min(fraction, 0.9),
                       message=label)

        def cancelled() -> bool:
            return job_id in self._cancelled

        try:
            result = publisher.publish(request, on_progress=on_progress, should_cancel=cancelled)
        except PublishCancelled:
            self._cancelled.discard(job_id)
            db.finish_publish_job(job_id, "cancelled", error="Cancelled.")
            self._emit(job_id, clip_id, phase="cancelled", fraction=0,
                       message="Upload cancelled", terminal="cancelled")
            return
        except QuotaExceeded as e:
            ledger.record_quota_error()
            service.save_ledger(db, ledger)
            raise e

        ledger.record_upload()
        service.save_ledger(db, ledger)

        self._emit(job_id, clip_id, phase="metadata", fraction=0.95,
                   message="Applying metadata")

        db.record_publish(clip_id, {
            "youtube_id": result.video_id,
            "video_id": clip["video_id"],
            "start_s": clip["start_s"],
            "end_s": clip["end_s"],
            "title": request.title,
            "privacy": result.requested_privacy,
            "actual_privacy": result.actual_privacy,
            "publish_at": result.publish_at or "",
            "channel_id": result.channel_id,
            "channel_title": result.channel_title,
            "thumbnail_set": 1 if result.thumbnail_set else 0,
            "playlist_id": request.playlist_id or "",
            "state": "locked_private" if result.locked_private else "uploaded",
            "error": "",
        })
        db.finish_publish_job(job_id, "done", youtube_id=result.video_id)

        self._emit(
            job_id, clip_id,
            phase="done",
            fraction=1.0,
            message="Scheduled on YouTube" if result.publish_at else "Published on YouTube",
            terminal="done",
            youtube_id=result.video_id,
            url=result.url,
            warnings=result.warnings,
            locked_private=result.locked_private,
        )
        self._verify.append((time.monotonic() + VERIFY_AFTER_SECONDS, clip_id, result.video_id))

    def _resolve_clip(self, db: StateDB, job):
        """Find the clip, following a re-render that gave it a new id."""
        clip = db.get_clip(job["clip_id"])
        if clip is not None:
            return clip
        if not job["video_id"]:
            return None
        return db.conn.execute(
            "SELECT * FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
            (job["video_id"], round(job["start_s"], 2), round(job["end_s"], 2)),
        ).fetchone()

    def _fail(self, db: StateDB, job, error: Exception) -> None:
        from publish.errors import PublishError
        from server.feedback import redact

        message = error.message if isinstance(error, PublishError) else str(error)
        # Never let a raw exception string reach the UI or the log: an OAuth
        # failure can carry a token fragment in its body.
        message = redact(message)[:500]
        db.finish_publish_job(job["id"], "failed", error=message)
        print(f"Publish job {job['id']} failed: {message}")
        self._emit(job["id"], job["clip_id"], phase="failed", fraction=0,
                   message=message, terminal="failed", error=message)

    # ---- deferred verification -------------------------------------------

    def _run_due_verifications(self, db: StateDB) -> None:
        """A minute after an upload, read the video back.

        Three things only become visible after YouTube processes the file:
        rejection (duplicate, copyright), processing failure, and confirmation
        that an unaudited project had its upload locked to private. One
        quota unit each, and without it the app would report success for a
        video that never plays.
        """
        if not self._verify:
            return
        now = time.monotonic()
        due = [item for item in self._verify if item[0] <= now]
        if not due:
            return
        self._verify = [item for item in self._verify if item[0] > now]

        for _, clip_id, video_id in due:
            row = db.get_upload(clip_id)
            if row is None or row["youtube_id"] != video_id:
                continue
            try:
                # Read back through the channel it was published to — another
                # channel's token cannot see a private video on this one.
                publisher = service.make_publisher(
                    self.config, self.data_dir, channel_id=row["channel_id"] or None
                )
                status = publisher.video_status(video_id)
            except Exception:
                continue  # advisory only; never fail a finished upload over it
            if not status:
                continue
            state = "uploaded"
            error = ""
            if status.get("upload_status") == "rejected":
                state = "rejected"
                error = f"YouTube rejected this video: {status.get('rejection_reason') or 'unknown'}."
            elif status.get("upload_status") == "failed":
                state = "failed"
                error = f"YouTube could not process this video: {status.get('failure_reason') or 'unknown'}."
            elif (
                row["privacy"] in ("public", "unlisted")
                and status.get("privacy") == "private"
                and not row["publish_at"]
            ):
                state = "locked_private"
            db.record_publish(clip_id, {
                "actual_privacy": status.get("privacy", ""),
                "state": state,
                "error": error,
                "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "youtube_id": video_id,
            })
            if state != "uploaded":
                self._emit(0, clip_id, phase="checked", fraction=1.0, message=error or
                           "YouTube locked this video to private.", terminal="checked",
                           state=state, youtube_id=video_id)

    # ---- events ----------------------------------------------------------

    def _emit(self, job_id: int, clip_id: int, **fields) -> None:
        """Publish straight to the broadcaster rather than through
        core.progress: the pipeline worker installs a global progress handler
        that stamps every event with the currently running JOB id, which would
        attribute an upload to whatever video happens to be rendering.

        The event type is 'publish', not 'job', so lib/jobProgress.ts leaves
        the global processing bar alone.
        """
        broadcaster.publish({
            "type": "publish",
            "publish_job": job_id,
            "clip_id": clip_id,
            "stage": "publish",
            **fields,
        })


def next_reset_local_hint() -> str:
    """A human-readable UTC instant for the next quota reset."""
    from publish.quota import next_reset

    return next_reset().isoformat().replace("+00:00", "Z")


def seconds_until(iso: str) -> float:
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.0
    return max(0.0, (moment - datetime.now(timezone.utc)) / timedelta(seconds=1))
