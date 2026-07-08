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
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            row = db.conn.execute(
                "SELECT payload FROM jobs WHERE status = 'queued' AND type = 'process' "
                "ORDER BY id LIMIT 1"
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
        """Block until an in-flight prefetch of THIS video finishes, so the
        job never starts a second yt-dlp run over a half-written file."""
        with self._lock:
            thread = self._thread if self._video_id == video_id else None
        if thread is not None:
            thread.join()

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
                db.upsert_video(video.video_id, title=video.title, channel_name=video.channel)
            finally:
                db.conn.close()
            print(f"      [prefetch] ready: {video.title}")
        except Exception as e:
            print(f"      [prefetch] failed (the job will download normally): {e}")
