"""Generate a synthetic test video, so a contributor has footage to run.

Nobody can work on the pipeline without a video, and we cannot ship one:
real footage is other people's, and a two-minute 1080p file does not belong
in git anyway. So we build one from nothing, with FFmpeg, in a few seconds.

    python tests/assets/make_sample_video.py

Writes `tests/assets/sample_video.mp4` (gitignored). Two minutes, 1080p,
H.264 — the same codec every real source ends up as, so nothing downstream
is being tested against a format the app never sees.

The audio is not silence. It is a tone whose loudness rises and falls on a
fixed schedule, which gives `analysis/audio_features.py` real peaks to find
at times known in advance. Verified against the real extractor:

    0:20 - 0:26   moderate
    0:36 - 0:42   loud       <- the biggest peak
    1:12 - 1:18   loud
    1:38 - 1:44   moderate

Useful for checking that signal-based candidate detection lands where it
should. It cannot tell you whether clips are any *good* — that needs a real
video and a human. See tests/README.md.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.binaries import ffmpeg  # noqa: E402

OUT = Path(__file__).resolve().parent / "sample_video.mp4"
DURATION = 120

# Amplitude envelope: (start, end, gain). Everything else sits at 0.05, quiet
# enough to read as background. Matches the timings in the docstring above.
PEAKS = [(20, 26, 0.55), (36, 42, 0.95), (72, 78, 0.85), (98, 104, 0.5)]


def _envelope() -> str:
    """An aevalsrc expression: a 220 Hz tone scaled by the peak schedule.

    Built as nested if()s rather than a loop at render time because aevalsrc
    evaluates this per sample and has no notion of a table.
    """
    expr = "0.05"
    for start, end, gain in reversed(PEAKS):
        expr = f"if(between(t,{start},{end}),{gain},{expr})"
    return f"({expr})*sin(220*2*PI*t)"


def main() -> int:
    exe = ffmpeg()
    if exe is None:
        print("FFmpeg not found. Run: python scripts/fetch_ffmpeg.py")
        return 1

    cmd = [
        str(exe),
        "-y",
        # testsrc2 moves and has a running timestamp burnt in, so when a clip
        # comes out you can read off the source time and check it is right.
        "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={DURATION}",
        # The expression is quoted for FFMPEG, not for the shell. A comma is
        # how a filtergraph separates filters, and this expression is full of
        # them, so unquoted it parses as a dozen broken filters.
        "-f", "lavfi", "-i", f"aevalsrc=exprs='{_envelope()}':s=48000:d={DURATION}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(OUT),
    ]

    print(f"Writing {OUT.name} ({DURATION}s, 1080p30)...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # FFmpeg puts the actual reason in the last few lines of stderr; the
        # rest is banner noise nobody needs.
        print("FFmpeg failed:")
        print("\n".join(result.stderr.strip().splitlines()[-12:]))
        return 1

    mb = OUT.stat().st_size / 1_000_000
    print(f"Done: {OUT}  ({mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
