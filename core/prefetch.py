"""Download prefetcher: overlap the NEXT queued video's download with the
CURRENT video's processing.

Downloading is pure network I/O while transcription/analysis/rendering are
CPU/GPU work, so they overlap perfectly — by the time the worker reaches the
next job, its source file is (usually) already on disk and the download stage
is skipped. One slot only: at most one video is prefetched ahead, so disk and
bandwidth use stay bounded.

Safety model: prefetching is BEST-EFFORT. Any failure here is logged and
ignored — the job itself will re-attempt the download and surface errors
through the normal path. The worker calls wait_for() before processing a
video so a job never races its own half-written prefetch download.
"""

import json
import threading
from pathlib import Path

from core import progress

# How long a job will wait for a prefetch of its own video before giving up on
# it. Generous: a real download of a multi-hour VOD on a slow line still lands
# inside this, so the timeout only fires on one that has genuinely stopped.
_PREFETCH_JOIN_TIMEOUT = 20 * 60  # seconds


class Prefetcher:
    def __init__(self, db_path: Path, downloads_dir: Path):
        self.db_path = db_path
        self.downloads_dir = downloads_dir
        self._thread: threading.Thread | None = None
        self._video_id: str | None = None
        self._lock = threading.Lock()

    def maybe_start(self, db) -> None:
        """Peek the oldest queued process job and start downloading its video
        in the background. No-op if a prefetch is already in flight, nothing
        is queued, or the file is already on disk. Runs on the worker thread
        (uses the worker's DB connection for the read)."""
        from core import queue

        # A paused queue should stop using the network and the disk too, not
        # just the GPU — otherwise "stopped" still fills the drive overnight.
        if queue.is_paused(db):
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            # Same order the worker claims in: the user can reorder the queue,
            # and prefetching the video they just demoted helps nobody.
            row = db.conn.execute(
                "SELECT payload FROM jobs WHERE status = 'queued' AND type = 'process' "
                "ORDER BY position, id LIMIT 1"
            ).fetchone()
            if row is None:
                return
            url = json.loads(row["payload"]).get("url")
            if not url:
                return
            from sources.dispatch import identify

            try:
                _, video_id = identify(url)
            except Exception:
                return  # bad URL: let the job itself produce the real error
            if not video_id or (self.downloads_dir / f"{video_id}.mp4").exists():
                return
            self._video_id = video_id
            self._thread = threading.Thread(
                target=self._run, args=(url, video_id), daemon=True, name="download-prefetch"
            )
            self._thread.start()

    def wait_for(self, video_id: str) -> None:
        """Wait — with a limit — for an in-flight prefetch of THIS video, so
        the job never starts a second yt-dlp run over a half-written file.

        The limit is the point. This used to be a bare join() with no timeout,
        which is only safe if a download can be trusted to finish or fail, and
        it cannot: a stalled fragment fetch just sits there. When that
        happened the SINGLE worker thread blocked here forever, so every job
        queued afterwards stayed 'queued' and the app silently stopped
        processing anything. Five jobs had piled up behind one dead download,
        with nothing in the UI to say why.

        Prefetching is best-effort by design (see the module docstring), so a
        prefetch that has gone quiet for this long is abandoned rather than
        waited on. The job then does its own download, which is the path that
        reports errors properly. A slow-but-alive prefetch still finishes well
        inside the limit — this only fires on one that is genuinely stuck.
        """
        with self._lock:
            thread = self._thread if self._video_id == video_id else None
        if thread is None:
            return
        thread.join(timeout=_PREFETCH_JOIN_TIMEOUT)
        if thread.is_alive():
            print(f"      [prefetch] {video_id} is stuck; abandoning it and "
                  f"downloading in the job instead")
            with self._lock:
                # Disown it so a later wait_for cannot block on it again. The
                # thread is a daemon, so it cannot keep the app alive.
                if self._video_id == video_id:
                    self._thread, self._video_id = None, None

    def _run(self, url: str, video_id: str) -> None:
        from core.state import StateDB
        from sources import dispatch

        # Restamp this thread's progress events so the UI shows them as
        # background prefetch, not as progress of the currently running job.
        progress.set_thread_tags(stage="prefetch", prefetch=True)
        try:
            print(f"      [prefetch] downloading next queued video ({video_id}) in the background")
            video = dispatch.download(url, self.downloads_dir)
            db = StateDB(self.db_path)  # sqlite: own connection on this thread
            try:
                # Title and length recorded now: this runs a job AHEAD, so the
                # queue can show a real name and a real time estimate for the
                # next video before it starts.
                db.upsert_video(
                    video.video_id,
                    title=video.title,
                    channel_name=video.channel,
                    duration=video.duration,
                )
            finally:
                db.conn.close()
            print(f"      [prefetch] ready: {video.title}")
        except Exception as e:
            print(f"      [prefetch] failed (the job will download normally): {e}")
