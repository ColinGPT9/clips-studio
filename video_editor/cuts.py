"""Cut/split filter graph: keep-ranges -> FFmpeg trim + concat.

Produces one continuous video from the kept sections. The audio comes in as
a named stream (mutes are applied to it FIRST — see audio.py — so mute
coordinates stay in the original timeline).
"""


def concat_graph(keep: list[tuple[float, float]], audio_in: str) -> tuple[str, str, str]:
    """Filter-graph text for cutting to the keep-ranges.
    Returns (graph, video_out_label, audio_out_label)."""
    parts = []
    for i, (a, b) in enumerate(keep):
        parts.append(
            f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}];"
            f"[{audio_in}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
    pairs = "".join(f"[v{i}][a{i}]" for i in range(len(keep)))
    parts.append(f"{pairs}concat=n={len(keep)}:v=1:a=1[vcut][acut]")
    return ";".join(parts), "[vcut]", "[acut]"
