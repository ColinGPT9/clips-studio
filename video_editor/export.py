"""Apply an edit list to a clip intermediate — one FFmpeg pass.

Called by the pipeline BETWEEN the clip cut and the vertical render, so
tracking/letterbox/captions all see the final (edited) timeline. Order inside
the pass: section mutes (original timeline) -> trim+concat cuts -> master
audio treatment (volume/fades, final timeline) -> optional caption burn (only
for the non-vertical path, where no later render stage exists to burn them).
"""

import subprocess
from pathlib import Path

from video.encoding import video_encoder_args
from video_editor.audio import master_filter, mute_filter
from video_editor.cuts import concat_graph
from video_editor.timeline import EditList


def apply_edits(
    input_path: Path,
    edit: EditList,
    output_path: Path,
    ass_path: Path | None = None,
) -> Path:
    graph_parts = []
    audio_label = "0:a"

    mf = mute_filter(edit.mutes)
    if mf:
        graph_parts.append(f"[0:a]{mf}[amuted]")
        audio_label = "amuted"

    if edit.keep is not None:
        cut_graph, video_out, audio_out = concat_graph(edit.keep, audio_label)
        graph_parts.append(cut_graph)
    else:
        video_out = "[0:v]"
        # concat consumed the named stream; without cuts wrap it in brackets.
        audio_out = f"[{audio_label}]" if audio_label != "0:a" else "[0:a]"

    graph_parts.append(f"{audio_out}{master_filter(edit)}[aout]")

    if ass_path is not None:
        graph_parts.append(f"{video_out}subtitles={ass_path.name}[vout]")
        video_out = "[vout]"
    elif video_out != "[0:v]":
        pass  # already a named intermediate label
    else:
        # -map needs a stream specifier, not a filter label, for a passthrough.
        video_out = "0:v"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path.resolve()),
        "-filter_complex", ";".join(graph_parts),
        "-map", video_out, "-map", "[aout]",
        *video_encoder_args(),
        "-c:a", "aac", "-b:a", "128k",
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        str(output_path.resolve()),
    ]
    workdir = ass_path.parent if ass_path is not None else None
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir)
    if result.returncode != 0:
        raise RuntimeError(f"video edit failed:\n{result.stderr[-2000:]}")
    return output_path
