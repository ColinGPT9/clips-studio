"""Shared yt-dlp behavior for all sources: live download progress + cancel,
parallel fragment fetching, and pointing yt-dlp at the bundled FFmpeg.

Without a progress hook the UI's bar sits still during a long VOD download
(the "stuck at 3%" feeling). This emits real percent as bytes arrive and
aborts promptly if the video is cancelled.
"""

from pathlib import Path

from core import cancel, progress
from core.binaries import ffmpeg


def _ffmpeg_dir() -> str | None:
    """The folder holding ffmpeg and ffprobe, for yt-dlp's own use.

    yt-dlp does not call core.binaries — it looks for ffmpeg on PATH itself,
    and merging separate video and audio streams is the one thing it cannot do
    without one. A developer's machine has ffmpeg on PATH, so this is invisible
    there. An installed copy does not, and every download that needs merging
    dies with "ffmpeg is not installed" — which is most YouTube downloads,
    because the good video and the good audio arrive as separate streams.

    Returns None when nothing resolved, leaving yt-dlp to search PATH exactly
    as before rather than handing it a path that isn't there.
    """
    resolved = ffmpeg()
    if resolved == "ffmpeg" or not Path(resolved).exists():
        return None
    return str(Path(resolved).parent)


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

    opts = {
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

    # Only set when we actually have one, so a checkout with ffmpeg on PATH
    # keeps working the way it always did.
    #
    # This is what separates Twitch from YouTube on a clean install: a Twitch
    # VOD is one already-muxed HLS stream and needs no merge, while YouTube
    # serves video and audio separately and cannot be assembled without
    # ffmpeg. Miss this and half the sources look fine while the biggest one
    # fails on every single video.
    ffmpeg_dir = _ffmpeg_dir()
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir

    return opts
