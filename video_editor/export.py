"""Apply an edit list to a clip intermediate — one FFmpeg pass.

Called by the pipeline BETWEEN the clip cut and the vertical render, so
tracking/letterbox/captions all see the final (edited) timeline. Order inside
the pass: section mutes (original timeline) -> trim+concat cuts -> master
audio treatment (volume/fades, final timeline) -> optional caption burn (only
for the non-vertical path, where no later render stage exists to burn them).
"""

import subprocess
from pathlib import Path

from video.encoding import LOUDNORM, video_encoder_args
from video_editor.audio import master_filter, mute_filter
from video_editor.cuts import concat_graph
from video_editor.timeline import EditList


def apply_edits(
    input_path: Path,
    edit: EditList,
    output_path: Path,
    ass_path: Path | None = None,
    normalize: bool = False,
) -> Path:
    """normalize: loudness-normalise the voice. Only for a FINAL clip — the
    vertical path runs this on a staging file that the cropper encodes
    again afterwards, and normalising twice pumps.

    It is applied to the source audio BEFORE volume and fades: loudnorm's
    single pass rides the gain, so running it after a fade-out would fight
    the fade and flatten it."""
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

    # Background music: looped to the clip length, at its own volume, and
    # (optionally) DUCKED under the voice via sidechain compression — the
    # music dips automatically whenever the creator talks.
    inputs = ["-i", str(input_path.resolve())]
    with_music = edit.music is not None and Path(edit.music["path"]).exists()

    # Voice chain: loudness (first, so fades still shape the end) + speed +
    # volume/mute + fades (final timeline).
    voice_label = "avoice" if with_music else "aout"
    level = f"{LOUDNORM}," if (normalize and not edit.mute_all) else ""
    graph_parts.append(f"{audio_out}{level}{master_filter(edit)}[{voice_label}]")

    if with_music:
        inputs += ["-stream_loop", "-1", "-i", str(Path(edit.music["path"]).resolve())]
        dur = edit.final_duration()
        graph_parts.append(
            f"[1:a]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,"
            f"volume={edit.music['volume']:.2f}[mus]"
        )
        if edit.music.get("duck", True):
            graph_parts.append("[avoice]asplit=2[av1][av2]")
            graph_parts.append(
                "[mus][av2]sidechaincompress=threshold=0.02:ratio=12:attack=15:release=400[musd]"
            )
            graph_parts.append("[av1][musd]amix=inputs=2:duration=first:normalize=0[aout]")
        else:
            graph_parts.append("[avoice][mus]amix=inputs=2:duration=first:normalize=0[aout]")

    # Video chain after cuts: playback speed, then caption burn (only for the
    # non-vertical path — the vertical render burns captions itself).
    video_chain = []
    if abs(edit.speed - 1.0) >= 0.01:
        video_chain.append(f"setpts=PTS/{edit.speed:.4f}")
    if ass_path is not None:
        video_chain.append(f"subtitles={ass_path.name}")
    if video_chain:
        src = video_out if video_out.startswith("[") else f"[{video_out}]"
        graph_parts.append(f"{src}{','.join(video_chain)}[vout]")
        video_out = "[vout]"
    elif video_out == "[0:v]":
        # -map needs a stream specifier, not a filter label, for a passthrough.
        video_out = "0:v"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
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
