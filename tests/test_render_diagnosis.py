"""A failed render has to say why, and a report has to say which build.

Issue #84 arrived as "all 10 clips failed to cut" with no FFmpeg error and no
app version. Both were our own bugs, and together they made a critical report
undiagnosable:

  * `_render_failure_reason` took the FIRST line of the exception. For a cut
    failure that line is `video/cutter.py`'s own "ffmpeg cut failed:" prefix,
    so the 2000 characters of stderr underneath were captured and thrown away
    one line later.
  * `_app_version` read `ui/package.json` next to the source. In a frozen
    build that path is inside the executable and does not exist, so every
    installed copy reported `"app": "?"` and no report could be dated.

The third test covers the fix that makes the failure survivable rather than
merely legible: a GPU encoder that passes its synthetic probe can still refuse
real footage, and retrying on the CPU turns an empty job into a slow one.
"""

import json
import sys

import pytest

from core.pipeline import _render_failure_reason
from video.cutter import _swap_encoder
from video.encoding import CPU_ARGS, _CANDIDATES


# The exact shape video/cutter.py raises: our prefix, then FFmpeg's stderr.
CUT_FAILURE = (
    "ffmpeg cut failed:\n"
    "  Stream #0:0 -> #0:0 (h264 (native) -> h264 (h264_nvenc))\n"
    "[h264_nvenc @ 0000021] 10 bit encode not supported\n"
    "Error while opening encoder for output stream #0:0"
)


def test_the_ffmpeg_error_survives_not_our_prefix():
    """The regression that made #84 impossible to triage."""
    reason = _render_failure_reason(RuntimeError(CUT_FAILURE))

    assert "ffmpeg cut failed:" != reason, "returned our own prefix again"
    assert "Error while opening encoder" in reason


def test_a_bare_prefix_still_says_something():
    """Empty stderr must not degrade to an empty string — the prefix is all
    there is, so it is what should be shown."""
    assert _render_failure_reason(RuntimeError("ffmpeg cut failed:")) == "ffmpeg cut failed:"


def test_a_plain_error_is_unchanged():
    assert _render_failure_reason(RuntimeError("boom")) == "boom"


def test_out_of_memory_keeps_its_advice():
    """The malloc case names an action; a raw libav line would not."""
    err = RuntimeError("ffmpeg cut failed:\n[libx264] malloc of size 41943040 failed")
    reason = _render_failure_reason(err)

    assert "ran out of memory" in reason
    assert "parallel_renders" in reason


def test_the_reason_stays_one_line():
    """The wall-of-noise problem this function exists for."""
    err = RuntimeError("ffmpeg cut failed:\n" + "\n".join(f"noise line {i}" for i in range(200)))
    reason = _render_failure_reason(err)

    assert len(reason) <= 300
    assert "\n" not in reason


def test_falling_back_to_cpu_changes_only_the_encoder():
    """Rebuilding the command instead of swapping the encoder block is how the
    retry would silently lose the seek, the subtitles or the audio settings."""
    hw = _CANDIDATES["nvenc"]
    cmd = ["ffmpeg", "-y", "-hwaccel", "auto", "-ss", "674.00", "-i", "in.mp4",
           "-t", "66.00", "-vsync", "cfr", *hw,
           "-c:a", "aac", "-b:a", "128k", "-vf", "subtitles=x.ass", "out.mp4"]

    swapped = _swap_encoder(cmd, hw, CPU_ARGS)
    i = cmd.index(hw[0])

    assert swapped[:i] == cmd[:i], "arguments before the encoder changed"
    assert swapped[i:i + len(CPU_ARGS)] == CPU_ARGS
    assert swapped[i + len(CPU_ARGS):] == cmd[i + len(hw):], "arguments after the encoder changed"
    assert "h264_nvenc" not in swapped


def test_the_app_version_is_reported_from_a_checkout():
    from server.feedback import _app_version

    assert _app_version()["app"] != "?", "a report that cannot be dated cannot be triaged"


def test_the_app_version_survives_freezing(monkeypatch, tmp_path):
    """In a frozen build ui/package.json is not beside the code — it is
    bundled, and sys._MEIPASS is where it lands. Reading only the source-tree
    path is what produced three undateable reports."""
    (tmp_path / "package.json").write_text(json.dumps({"version": "9.9.9"}), encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    from server.feedback import _app_version

    assert _app_version()["app"] == "9.9.9"
