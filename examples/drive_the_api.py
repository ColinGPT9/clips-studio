"""Drive the engine over HTTP, the way the desktop app does.

The Electron UI is only a client. Everything it can do is a request to
`127.0.0.1:8765`, which means anything else can do it too — a script that
clips a folder overnight, a Discord bot, a different interface entirely.
This walks the whole path so the shape is clear.

    python main.py serve                    # in another terminal
    python examples/drive_the_api.py                          # health only
    python examples/drive_the_api.py --url https://twitch.tv/videos/123
    python examples/drive_the_api.py --file "D:/footage/stream.mp4"

Uses only `requests` (in requirements.txt) and `websockets` (which arrives
with `uvicorn[standard]`, so it is already installed too).

Nothing here is authenticated, because the API has no authentication — it is
bound to localhost precisely because of that. If you ever expose it, put
something in front of it; it reads and writes video files on the machine it
runs on.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

API = "http://127.0.0.1:8765"


def show_health() -> bool:
    """Preflight is what the Setup Wizard reads. Worth doing first: it
    reports a missing FFmpeg or a stopped Ollama as data, rather than
    letting a job fail halfway through with a stack trace."""
    try:
        requests.get(f"{API}/health", timeout=5).raise_for_status()
    except requests.RequestException:
        print(f"Engine not reachable at {API}. Start it with: python main.py serve")
        return False

    report = requests.get(f"{API}/health/preflight", timeout=30).json()
    print("Preflight:")
    for check in report["checks"]:
        # `blocking` is the distinction that matters: a failed non-blocking
        # check (no GPU, low disk) means slower, not broken.
        mark = "ok  " if check["ok"] else ("FAIL" if check["blocking"] else "warn")
        print(f"  [{mark}] {check['name']}: {check['detail']}")
        if not check["ok"] and check["fix"]:
            print(f"         -> {check['fix']}")
    print()

    if not report["ready"]:
        print("Something blocking is wrong; a job would fail. Continuing anyway.\n")
    return True


def submit(url: str | None, file: str | None) -> tuple[int, str | None] | None:
    """Two entry points, same pipeline afterwards: a URL is downloaded first,
    a local file is imported in place.

    Returns (job_id, video_id). The video id is known immediately for a local
    file, but not for a URL — that one is only resolved once the source has
    been identified, which is inside the job.
    """
    if url:
        body = {"url": url, "max_clips": 3}
        response = requests.post(f"{API}/jobs", json=body, timeout=60)
    else:
        body = {"path": file, "title": Path(file).stem, "channel": "Example Creator"}
        response = requests.post(f"{API}/videos/local", json=body, timeout=120)

    if response.status_code >= 400:
        print(f"Rejected ({response.status_code}): {response.text}")
        return None

    data = response.json()
    # A URL processed before returns no job unless force=True — that is the
    # API telling you it already has the clips, not an error.
    if data.get("already_processed"):
        print(f"Already processed as video {data['video_id']}. Pass force=True to redo it.")
        return None

    job_id = data.get("job_id")
    print(f"Job {job_id} accepted.\n")
    return job_id, data.get("video_id")


TERMINAL = {"done", "failed"}


async def _show_progress() -> None:
    """Print pipeline events as they arrive on the WebSocket.

    This is the same feed the app's progress bar reads: every stage emits
    into one broadcast channel. Stages seen in practice are download,
    transcribe, signals, analyze, reactions, render, done — but the set is
    not fixed, so print whatever turns up rather than matching on names.
    """
    import websockets

    async with websockets.connect("ws://127.0.0.1:8765/ws") as socket:
        while True:
            event = json.loads(await socket.recv())

            # The channel also carries non-pipeline messages (model pulls and
            # the like), which have a `type` instead of a `stage`.
            stage = event.get("stage")
            if not stage:
                continue

            if event.get("fraction") is not None:
                print(f"  {stage}: {event['fraction'] * 100:.0f}%")
            elif event.get("total"):
                print(f"  {stage}: {event.get('current')}/{event['total']}")
            else:
                print(f"  {stage}")


async def _await_finish(job_id: int) -> dict:
    """Poll the job row until it is done or failed.

    Why poll at all, when there is a WebSocket? Because the socket cannot
    answer this question. Two gaps, both worth knowing before you build on
    it:

      * Events carry no job id. It is one broadcast channel for the whole
        engine, so a `done` on it may belong to somebody else's job — or to
        the background prefetcher.
      * There is no failure event. A job that dies emits nothing further,
        and a reader waiting for a terminal event waits forever.

    The socket is for showing progress. The job row is the truth.
    """
    while True:
        job = requests.get(f"{API}/jobs/{job_id}", timeout=10).json()
        if job.get("status") in TERMINAL:
            return job
        await asyncio.sleep(2)


async def follow(job_id: int, video_id: str | None) -> None:
    progress = asyncio.create_task(_show_progress())
    try:
        job = await _await_finish(job_id)
    finally:
        progress.cancel()

    print(f"\nJob {job.get('status')}." + (f" {job['error']}" if job.get("error") else ""))
    if job.get("status") != "done":
        return

    if video_id is None:
        # The URL path does not hand back a video id, and the jobs table has
        # no column for one. /videos is newest-first, so the job we just
        # watched finish is the head of it.
        videos = requests.get(f"{API}/videos", timeout=10).json()
        if not videos:
            return
        video_id = videos[0]["video_id"]

    clips = requests.get(f"{API}/videos/{video_id}/clips", timeout=10).json()
    if not clips:
        # Normal for the generated test video: no speech, so nothing scores.
        print("No clips. Either nothing cleared clips.min_score, or the source had no speech.")
        return

    print(f"{len(clips)} clip(s):")
    for clip in clips:
        print(f"  [{clip.get('score')}] {clip.get('hook') or clip.get('title', '')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--url", help="Twitch / Kick / YouTube URL")
    group.add_argument("--file", help="a video file on this machine")
    args = parser.parse_args()

    if not show_health():
        return 1
    if not args.url and not args.file:
        print("Health only. Pass --url or --file to actually process something.")
        return 0

    accepted = submit(args.url, args.file)
    if accepted is None:
        return 1
    job_id, video_id = accepted

    print("Following progress (Ctrl-C to stop watching; the job keeps going):\n")
    try:
        asyncio.run(follow(job_id, video_id))
    except KeyboardInterrupt:
        # Detaching the watcher is not cancelling the work. POST /cancel does
        # that, and it is a separate decision.
        print("\nStopped watching. The job is still running.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
