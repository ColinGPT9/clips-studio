"""Apply an edit list to a clip intermediate — one FFmpeg pass.

Called by the pipeline BETWEEN the clip cut and the vertical render, so
tracking/letterbox/captions all see the final (edited) timeline. Order inside
the pass: section mutes (original timeline) -> trim+concat cuts -> master
audio treatment (volume/fades, final timeline) -> optional caption burn (only
for the non-vertical path, where no later render stage exists to burn them).
"""

import subprocess
from pathlib import Path

from core.binaries import ffmpeg
from video.encoding import LOUDNORM, final_movflags, video_encoder_args
from video.fonts import subtitles_filter
from video_editor.audio import master_filter, mute_filter
from video_editor.cuts import concat_graph
from video_editor.timeline import EditList

# "Duck under voice": how far the music dips while the clip's own sound is loud.
# _DUCK_OWN is what the person's own music file has always had. On a clip
# whose sound never goes quiet (a street, a crowd, game sound) it holds the
# music about 20 dB down for the whole clip: at the editor's volumes that is
# 35 dB or more under the voice, which nobody hears. It stays as it is for
# their own file so that those clips render exactly as before.
# _DUCK_FREE is for the app's free tracks (new in editor suite step 4): a dip
# of about 7 dB under a normal voice, so the music stays in the clip.
_DUCK_OWN = "threshold=0.02:ratio=12:attack=15:release=400"
_DUCK_FREE = "threshold=0.05:ratio=2:attack=15:release=400"


def _music_file(music: dict | None) -> Path | None:
    """The file a clip's music plays: the person's own (music["path"]), when
    it exists, or one of the app's free tracks (music["library"], an id),
    when this copy of the app has it unchanged (video/audio_library.py; a
    render PC finds its own copy the same way). None: the clip is made
    without music, and for a free track the job log says why."""
    if music is None:
        return None
    if music.get("library"):
        from video import audio_library

        # kind: a sound effect is not music (it would loop under the whole clip).
        return audio_library.resolve(music["library"], kind="music",
                                     log=lambda line: print(f"      {line}, so this clip has no music"))
    path = Path(music["path"])
    return path if path.exists() else None


def apply_edits(
    input_path: Path,
    edit: EditList,
    output_path: Path,
    ass_path: Path | None = None,
    normalize: bool = False,
    final: bool = False,
) -> Path:
    """normalize: loudness-normalise the voice. Only for a FINAL clip — the
    vertical path runs this on a staging file that the cropper encodes
    again afterwards, and normalising twice pumps.

    It is applied to the source audio BEFORE volume and fades: loudnorm's
    single pass rides the gain, so running it after a fade-out would fight
    the fade and flatten it.

    final: this pass IS the clip (horizontal and Vertical Live clips with
    edits): written with final_movflags(), so its first frame is at 0 in
    every app."""
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
    music_file = _music_file(edit.music)
    with_music = music_file is not None

    # Voice chain: loudness (first, so fades still shape the end) + speed +
    # volume/mute + fades (final timeline).
    voice_label = "avoice" if with_music else "aout"
    level = f"{LOUDNORM}," if (normalize and not edit.mute_all) else ""
    graph_parts.append(f"{audio_out}{level}{master_filter(edit)}[{voice_label}]")

    if with_music:
        inputs += ["-stream_loop", "-1", "-i", str(music_file.resolve())]
        dur = edit.final_duration()
        graph_parts.append(
            f"[1:a]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,"
            f"volume={edit.music['volume']:.2f}[mus]"
        )
        if edit.music.get("duck", True):
            graph_parts.append("[avoice]asplit=2[av1][av2]")
            graph_parts.append(
                f"[mus][av2]sidechaincompress={_DUCK_FREE if edit.music.get('library') else _DUCK_OWN}[musd]"
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
        video_chain.append(subtitles_filter(ass_path))
    if video_chain:
        src = video_out if video_out.startswith("[") else f"[{video_out}]"
        graph_parts.append(f"{src}{','.join(video_chain)}[vout]")
        video_out = "[vout]"
    elif video_out == "[0:v]":
        # -map needs a stream specifier, not a filter label, for a passthrough.
        video_out = "0:v"

    cmd = [
        ffmpeg(), "-y",
        *inputs,
        "-filter_complex", ";".join(graph_parts),
        "-map", video_out, "-map", "[aout]",
        *video_encoder_args(),
        "-c:a", "aac", "-b:a", "128k",
        "-fps_mode", "cfr",
        *(final_movflags() if final else ["-movflags", "+faststart"]),
        str(output_path.resolve()),
    ]
    workdir = ass_path.parent if ass_path is not None else None
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir)
    if result.returncode != 0:
        raise RuntimeError(f"video edit failed:\n{result.stderr[-2000:]}")
    return output_path
