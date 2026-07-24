"""Shared yt-dlp behavior for all sources: live download progress + cancel,
and parallel fragment fetching.

Without a progress hook the UI's bar sits still during a long VOD download
(the "stuck at 3%" feeling). This emits real percent as bytes arrive and
aborts promptly if the video is cancelled.
"""

from core import cancel, progress


def progress_opts(video_id: str | None) -> dict:
    def hook(d: dict) -> None:
        if video_id and cancel.is_cancelled(video_id):
            raise cancel.CancelledError(video_id)  # aborts the yt-dlp download
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes")
            if total and done:
                progress.emit(
                    stage="download",
                    fraction=min(1.0, done / total),
                    video_id=video_id,
                    downloaded=done,
                    total=total,
                )

    return {
        "progress_hooks": [hook],
        # VODs are HLS: thousands of small fragments. Fetching them one at a
        # time leaves most of the connection idle — parallel fragments cut
        # download time by 2-4x on long streams.
        "concurrent_fragment_downloads": 6,
        # Long downloads WILL hit a slow or dropped fragment. Without retries a
        # single "Read timed out" or transient 403 kills the whole job after
        # minutes of progress. Retry the fragment instead, and cap how long we
        # wait on a stalled socket so a dead connection fails fast enough to
        # retry rather than hanging.
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        # Pull large (non-fragmented) streams in 10 MB HTTP range chunks. A
        # 900 MB YouTube DASH stream is otherwise one long GET, and a single
        # stall late in it ("Read timed out" against googlevideo) throws away
        # the whole transfer. Chunked, a timeout loses only the current 10 MB
        # and retries just that — which is what makes the retries above
        # actually recover a big download instead of restarting it.
        "http_chunk_size": 10 * 1024 * 1024,
        # A retry immediately after a 403/timeout usually hits the same wall;
        # a short backoff lets throttling clear. Capped so we don't stall.
        "retry_sleep_functions": {
            "http": lambda n: min(5, 2 ** n),
            "fragment": lambda n: min(5, 2 ** n),
        },
    }
