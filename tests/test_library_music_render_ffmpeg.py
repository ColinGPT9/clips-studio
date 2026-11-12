"""A clip with one of the app's free tracks, rendered for real by FFmpeg
(editor suite step 4).

Needs FFmpeg; skips without it. Nothing is downloaded: the clip is FFmpeg's
own solid colour with a silent sound track, so anything heard in the result
is the music. The editor's pass (video_editor/export.py) runs exactly as a
render runs it.

- With a free track the result is loud; the same clip with no music is
  silent. Both are as long as the clip.
- With an id the app doesn't have, or a damaged file, the clip is made
  without music, the job log says why, and the render does not fail.
"""

import re
import subprocess
from pathlib import Path

import pytest

from core import binaries
from video import encoding
from video_editor import export
from video_editor.timeline import EditList

SECONDS = 3.0


@pytest.fixture
def ffmpeg(monkeypatch):
    if binaries.missing():
        pytest.skip("needs FFmpeg")
    exe = binaries.ffmpeg()
    try:
        encoders = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                  timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("FFmpeg could not be run")
    if " libx264 " not in encoders:
        pytest.skip("this FFmpeg has no libx264")
    # The CPU encoder, whatever this PC has: the sound is what is checked.
    monkeypatch.setattr(encoding, "_selected", ("cpu", ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30"]))
    return exe


def _silent_clip(exe: str, path: Path) -> Path:
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"color=c=0x203050:s=320x180:r=25:d={SECONDS}",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(SECONDS),
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    return path


def _loudness(exe: str, path: Path) -> tuple[float, float]:
    """(mean volume in dB, seconds of sound) of a file's sound track."""
    out = subprocess.run([exe, "-hide_banner", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    mean = re.search(r"mean_volume: (-?[0-9.]+|-inf) dB", out.stderr)
    time = re.findall(r"time=(\d+):(\d+):([0-9.]+)", out.stderr)
    assert mean and time, out.stderr[-800:]
    h, m, s = time[-1]
    return float(mean.group(1)), int(h) * 3600 + int(m) * 60 + float(s)


def _render(clip: Path, music: dict | None, out: Path) -> Path:
    # A fade keeps the pass worth running when there is no music, as a saved edit would.
    edit = EditList.from_dict({"fade_in": 0.1, **({"music": music} if music else {})}, duration=SECONDS)
    return export.apply_edits(clip, edit, out)


def test_a_free_track_is_heard_in_the_clip(ffmpeg, tmp_path, capsys):
    clip = _silent_clip(ffmpeg, tmp_path / "clip.source.mp4")
    assert _loudness(ffmpeg, clip)[0] < -80                          # the clip itself is silent

    without = _render(clip, None, tmp_path / "without.mp4")
    with_track = _render(clip, {"library": "music-doodles", "volume": 0.5, "duck": False}, tmp_path / "with.mp4")
    ducked = _render(clip, {"library": "music-roof-tops", "volume": 0.5, "duck": True}, tmp_path / "ducked.mp4")
    assert capsys.readouterr().out == ""

    quiet, quiet_seconds = _loudness(ffmpeg, without)
    loud, loud_seconds = _loudness(ffmpeg, with_track)
    under_voice, _ = _loudness(ffmpeg, ducked)
    assert quiet < -80, quiet
    assert loud > -45 and loud - quiet > 35, (loud, quiet)           # the music is plainly there
    assert under_voice > -45, under_voice                            # nobody is talking: it is not ducked away
    assert abs(loud_seconds - SECONDS) < 0.25 and abs(quiet_seconds - SECONDS) < 0.25


def _talking_clip(exe: str, path: Path) -> Path:
    """A clip whose own sound never goes quiet: a steady low tone about as
    loud as a voice (-15 dB), like a street or a crowd under the talking."""
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"color=c=0x203050:s=320x180:r=25:d={SECONDS}",
           "-f", "lavfi", "-i", f"sine=frequency=300:sample_rate=44100:duration={SECONDS}",
           "-af", "volume=2", "-ac", "2", "-t", str(SECONDS),
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    return path


def _above_the_voice(exe: str, path: Path) -> float:
    """Mean volume (dB) of what a file has above 2 kHz: the music, since the
    clip's own tone is far below that."""
    high = ",".join(["highpass=f=2000"] * 4)
    out = subprocess.run([exe, "-hide_banner", "-i", str(path), "-vn", "-af", f"{high},volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    mean = re.search(r"mean_volume: (-?[0-9.]+|-inf) dB", out.stderr)
    assert mean, out.stderr[-800:]
    return float(mean.group(1).replace("-inf", "-200"))


def test_a_free_track_is_still_heard_under_a_clip_that_never_goes_quiet(ffmpeg, tmp_path):
    """"Duck under voice" dips a free track; it does not take it out. (Found
    by the black-box test: on street footage the old setting held the music
    35 dB or more under the voice for the whole clip.)"""
    clip = _talking_clip(ffmpeg, tmp_path / "clip.source.mp4")
    track = {"library": "music-doodles", "volume": 0.4}
    none = _above_the_voice(ffmpeg, _render(clip, None, tmp_path / "none.mp4"))
    full = _above_the_voice(ffmpeg, _render(clip, {**track, "duck": False}, tmp_path / "full.mp4"))
    ducked = _above_the_voice(ffmpeg, _render(clip, {**track, "duck": True}, tmp_path / "ducked.mp4"))
    assert full - none > 20, (full, none)                            # the band holds the music, not the clip
    assert 2 < full - ducked < 10, (full, ducked, none)              # it dips by a few dB, and is still there


def test_an_unknown_or_damaged_track_gives_a_clip_without_music_and_says_so(ffmpeg, tmp_path, capsys, monkeypatch):
    from video import audio_library

    clip = _silent_clip(ffmpeg, tmp_path / "clip.source.mp4")
    unknown = _render(clip, {"library": "no-such-track", "volume": 0.5, "duck": True}, tmp_path / "unknown.mp4")
    assert "isn't in this copy of the app, so this clip has no music" in capsys.readouterr().out
    monkeypatch.setattr(audio_library, "_matches", lambda path, sha256: False)
    damaged = _render(clip, {"library": "music-doodles", "volume": 0.5, "duck": True}, tmp_path / "damaged.mp4")
    assert ('The free music "Doodles" is missing or damaged in this copy of the app, so this clip has no music'
            in capsys.readouterr().out)
    for made in (unknown, damaged):
        mean, seconds = _loudness(ffmpeg, made)
        assert made.is_file() and mean < -80 and abs(seconds - SECONDS) < 0.25
