"""Each keyframe picture's own time, for the score bug and the cutaways.

Decoding only a video's keyframes (sports/core/scorebug.keyframe_crops),
ffmpeg dates each picture by its "best effort" time. Once a keyframe comes
out of the decoder out of order (B-frames, an open GOP), that falls back to
the time of the packet that pushed the picture out: the next keyframe's.
On an NBA game, 9 of 10 keyframes were dated 1.5-5 s late and two came out
swapped, so a score was dated a keyframe after it showed, and an older
score could follow a newer one. Decoding every frame dates them right but
is many times slower.

ffprobe, decoding the same keyframes the same way, prints each picture's
own time beside the one ffmpeg used. It runs alongside ffmpeg; when its
list matches ffmpeg's times, its own times replace them. Otherwise (no
ffprobe, a list that doesn't match) ffmpeg's times stand, as before.

Basketball's only: soccer's reader keeps the shared times as they are.
"""

import contextlib
import subprocess
import threading

SAME = 0.1      # seconds within which ffmpeg's and ffprobe's dates are the same picture's (showinfo
                # prints 6 digits: tenths past 10,000 s; keyframes are half a second or more apart)


def own_times(times: list[float], listing: str) -> list[float]:
    """`times` (ffmpeg's, in the order it passed the pictures on) with each
    replaced by the picture's own time from ffprobe's `listing` (compact
    lines with pts_time and best_effort_timestamp_time, in the same order),
    when the listing's best effort times match `times`; else `times`."""
    rows = []
    for line in listing.splitlines():
        fields = dict(part.split("=", 1) for part in line.strip().split("|") if "=" in part)
        try:
            best = float(fields["best_effort_timestamp_time"])
        except (KeyError, ValueError):
            continue
        try:
            own = float(fields.get("pts_time", ""))
        except ValueError:
            own = None
        rows.append((best, own))
    if not times or len(rows) < len(times):
        return times
    # ffmpeg counts from the file's start time; ffprobe prints the stream's own.
    offset = rows[0][0] - times[0]
    if any(abs(best - offset - t) > SAME for (best, _own), t in zip(rows, times)):
        return times
    return [t if own is None else round(own - offset, 3) for (_best, own), t in zip(rows, times)]


@contextlib.contextmanager
def listing(path):
    """Starts ffprobe listing `path`'s keyframes and yields fix(times), which
    waits for it and returns own_times(times, its listing). Stops ffprobe if
    the block ends early (cancelled)."""
    from core.binaries import ffprobe

    out: list[str] = []
    try:
        proc = subprocess.Popen([ffprobe(), "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
                                 "-show_entries", "frame=pts_time,best_effort_timestamp_time",
                                 "-of", "compact=p=0", str(path)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        proc = None
    reader = None
    if proc is not None:
        reader = threading.Thread(target=lambda: out.append(proc.stdout.read()), daemon=True)
        reader.start()

    def fix(times: list[float]) -> list[float]:
        if proc is None:
            return times
        proc.wait()
        reader.join()
        fixed = own_times(times, out[0] if out else "")
        if times and fixed is times:
            print("      Keyframes: ffprobe's list didn't match ffmpeg's, dated as decoded")
        moved = sum(abs(a - b) > SAME for a, b in zip(fixed, times))
        if moved:
            print(f"      Keyframes: {moved} of {len(times)} dated by their own time, not the next one's")
        return fixed

    try:
        yield fix
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
