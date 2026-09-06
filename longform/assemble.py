"""Chronological assembly: keep-ranges -> one 1920x1080 video.

A 3-hour VOD can produce hundreds of keep-ranges; a single FFmpeg filter
graph that size is fragile. Instead each range is cut (GPU-encoded with
identical parameters) and the pieces are joined losslessly with the concat
demuxer — same result, scales safely, and cancellation can land between
segments.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

from core import cancel
from core.binaries import ffmpeg
from core.paths import discard
from video.encoding import video_encoder_args

_FIT = (
    "scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
)


def assemble(
    source: Path,
    keep: list[tuple[float, float]],
    output_path: Path,
    video_id: str,
    on_progress: Callable[[int, int], None] | None = None,
    config: dict | None = None,
) -> Path:
    """Join the keep-ranges into one video, with the end card as the last part.

    The card is appended HERE, as one more entry in the concat list, rather
    than added to the finished file afterwards. Every part is already encoded
    to identical parameters for a lossless join, so a matching card slots in
    for free -- and the output is written once, complete, which is what stops a
    clip open in the app's preview from costing it the card.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    segdir = output_path.parent / (output_path.stem + ".parts")
    segdir.mkdir(exist_ok=True)
    try:
        parts: list[Path] = []
        for i, (a, b) in enumerate(keep):
            cancel.check(video_id)
            seg = segdir / f"seg_{i:05d}.mp4"
            cmd = [
                ffmpeg(), "-y",
                "-ss", f"{a:.2f}", "-i", str(source.resolve()),
                "-t", f"{b - a:.2f}",
                "-vf", _FIT,
                *video_encoder_args(),
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                "-af", "aresample=async=1",
                "-fps_mode", "cfr",
                str(seg.resolve()),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"segment {i} cut failed:\n{r.stderr[-800:]}")
            parts.append(seg)
            if on_progress:
                on_progress(i + 1, len(keep))

        # The end card, matched to the parts' own format so the join stays
        # lossless. It goes in the LIST rather than onto the finished file:
        # the output is then written once, complete, which is what stops a
        # video open in the app's preview from costing it the card.
        #
        # Failure here must never cost the video -- without it the assembly
        # simply carries on unbranded.
        if config is not None and parts:
            try:
                from video import outro

                if outro.enabled(config):
                    fmt = outro.probe(parts[0])
                    if fmt:
                        parts.append(outro.ensure_outro(fmt, config))
                        outro._tally["added"] += 1
            except Exception as exc:            # noqa: BLE001
                print(f"  end card skipped: {type(exc).__name__}: {exc}")

        # Identical codec parameters on every part -> lossless concat join.
        listfile = segdir / "concat.txt"
        listfile.write_text(
            "".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8"
        )
        r = subprocess.run(
            [
                ffmpeg(), "-y", "-f", "concat", "-safe", "0",
                "-i", str(listfile.resolve()),
                "-c", "copy", "-movflags", "+faststart",
                str(output_path.resolve()),
            ],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"concat join failed:\n{r.stderr[-800:]}")
        return output_path
    finally:
        for f in segdir.glob("*"):
            discard(f)
        try:
            segdir.rmdir()
        except OSError:
            pass  # a file we could not remove is still in it; swept later
