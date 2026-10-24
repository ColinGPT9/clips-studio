"""The join without re-encoding (video.encoding.concat) and where a video
starts (_video_starts_late), which every end card, Longform video and story
reel goes through.

The join keeps each part's timestamps (-copyts) and writes the final flags,
so a clip's first frame is at 0 whether an app reads the MP4's edit list or
not. When the joined video still starts late, a part already did; the join
as it was made before -copyts is then made beside it and kept only when its
video starts sooner. A clip whose own video starts late keeps its audio
priming behind an edit, and joined as before that priming is moved to 0 as
well: the video starts later still (33 ms became 54 ms).

These live in video/encoding.py, not video/outro.py (which draws the card
with numpy and Pillow), so Longform and the reels need neither to join, and
these tests run on CI. The real joins need FFmpeg and skip without it.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from video import encoding

ROOT = Path(__file__).resolve().parent.parent
FINAL = "+faststart+negative_cts_offsets"


class _Ran:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _kind(cmd):
    """Which of concat()'s joins `cmd` is."""
    if "-copyts" in cmd:
        return "copyts"
    return "final" if FINAL in cmd else "old"


def _tools(monkeypatch, starts=None, fails=()):
    """FFmpeg and ffprobe as concat() sees them. FFmpeg writes what it is
    asked to, saying which join made it, except the joins named in `fails`
    ("copyts", "final", "old"). ffprobe reads the video of a join of kind k
    starting at starts[k] seconds both ways (0 when not given), and "" for
    one it can't read. Returns the joins run."""
    joins = []
    starts = starts or {}

    def run(cmd, *a, **k):
        cmd = [str(c) for c in cmd]
        if "ffprobe" not in Path(cmd[0]).name.lower():
            joins.append(cmd)
            if _kind(cmd) in fails:
                return _Ran(1, stderr="refused")
            Path(cmd[-1]).write_text(_kind(cmd), encoding="utf-8")
            return _Ran()
        assert "-read_intervals" in cmd, cmd
        return _Ran(stdout=starts.get(Path(cmd[-1]).read_text(encoding="utf-8"), "0.000000\n"))

    monkeypatch.setattr(subprocess, "run", run)
    return joins


def _join(tmp_path):
    listing = tmp_path / "list.txt"
    listing.write_text("file 'a.mp4'\nfile 'card.mp4'\n", encoding="utf-8")
    return listing, tmp_path / "clip.mp4"


def _left(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir())


# ---- the join -------------------------------------------------------------------------------


def test_a_join_that_starts_at_0_is_made_once_with_its_timestamps_kept(monkeypatch, tmp_path):
    joins = _tools(monkeypatch)
    listing, dst = _join(tmp_path)

    assert encoding.concat(listing, dst).returncode == 0
    [cmd] = joins
    assert cmd[1:] == ["-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                       "-copyts", "-c", "copy", "-movflags", FINAL, str(dst)]
    assert dst.read_text(encoding="utf-8") == "copyts"


def test_a_join_that_fails_is_made_as_before_and_then_exactly_as_before(monkeypatch, tmp_path, capsys):
    """A join not made at all would cost the clip its card."""
    joins = _tools(monkeypatch, fails=("copyts",))
    listing, dst = _join(tmp_path)
    assert encoding.concat(listing, dst).returncode == 0
    assert [_kind(c) for c in joins] == ["copyts", "final"]
    assert dst.read_text(encoding="utf-8") == "final"
    assert "(refused); joined as before instead" in capsys.readouterr().out

    joins = _tools(monkeypatch, fails=("copyts", "final"))
    encoding.concat(listing, dst)
    assert [_kind(c) for c in joins] == ["copyts", "final", "old"]
    assert joins[-1][-5:] == ["-c", "copy", "-movflags", "+faststart", str(dst)]
    assert dst.read_text(encoding="utf-8") == "old"


def test_a_late_join_is_replaced_when_the_join_as_before_starts_sooner(monkeypatch, tmp_path, capsys):
    """Made beside it, under a hidden name, and moved into place."""
    joins = _tools(monkeypatch, starts={"copyts": "0.021333\n", "final": "0.000000\n"})
    listing, dst = _join(tmp_path)

    r = encoding.concat(listing, dst)
    assert r.returncode == 0
    assert [_kind(c) for c in joins] == ["copyts", "final"]
    assert joins[1][-1] == str(tmp_path / ".clip.before.mp4")
    assert dst.read_text(encoding="utf-8") == "final"
    assert "21 ms late; joined as before instead (0 ms)" in capsys.readouterr().out
    assert _left(tmp_path) == ["clip.mp4", "list.txt"]


@pytest.mark.parametrize(("starts", "why"), [
    ({"copyts": "0.033008\n", "final": "0.053971\n"}, "later"),       # its own video starts late
    ({"copyts": "0.021000\n", "final": "0.021000\n"}, "the same"),    # a clip carded before this
    ({"copyts": "0.033000\n", "final": "0.031500\n"}, "rounding"),    # sooner by under 2 ms
    ({"copyts": "0.033000\n", "final": ""}, "unreadable"),
])
def test_a_late_join_stays_unless_the_join_as_before_starts_sooner(monkeypatch, tmp_path, capsys, starts, why):
    joins = _tools(monkeypatch, starts=starts)
    listing, dst = _join(tmp_path)

    assert encoding.concat(listing, dst).returncode == 0
    assert [_kind(c) for c in joins] == ["copyts", "final"], why
    assert dst.read_text(encoding="utf-8") == "copyts", why
    # Not "no sooner" when it could not be told: its own message.
    said = ("the join as before could not be made or read, so it is kept" if why == "unreadable"
            else "joined as before it would be no sooner, so it is kept")
    out = capsys.readouterr().out
    assert f"as a part does; {said}" in out and out.count("Join:") == 1, out
    assert _left(tmp_path) == ["clip.mp4", "list.txt"], "the other join was left behind"


def test_a_late_join_stays_when_the_join_as_before_fails(monkeypatch, tmp_path, capsys):
    joins = _tools(monkeypatch, starts={"copyts": "0.500000\n"}, fails=("final",))
    listing, dst = _join(tmp_path)

    assert encoding.concat(listing, dst).returncode == 0
    assert [_kind(c) for c in joins] == ["copyts", "final"], "and not the old join on top"
    assert dst.read_text(encoding="utf-8") == "copyts"
    out = capsys.readouterr().out
    assert "500 ms late, as a part does; the join as before could not be made or read" in out
    assert "no sooner" not in out


def test_a_clip_held_open_gets_the_sooner_join_written_into_it(monkeypatch, tmp_path):
    """A clip previewed in the app can be written but not replaced (finish()
    writes it for that reason): the join as before is then made into it."""
    joins = _tools(monkeypatch, starts={"copyts": "0.021333\n", "final": "0.000000\n"})
    listing, dst = _join(tmp_path)
    real = Path.replace

    def held(self, target):
        if Path(target) == dst:
            raise PermissionError(5, "Access is denied")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", held)
    assert encoding.concat(listing, dst).returncode == 0
    assert [(_kind(c), Path(c[-1]).name) for c in joins] == [
        ("copyts", "clip.mp4"), ("final", ".clip.before.mp4"), ("final", "clip.mp4")]
    assert dst.read_text(encoding="utf-8") == "final"
    assert _left(tmp_path) == ["clip.mp4", "list.txt"]

    # And if that write fails, this join is written back.
    joins = _tools(monkeypatch, starts={"copyts": "0.021333\n", "final": "0.000000\n"})
    real_run = subprocess.run
    runs = {"n": 0}

    def third_fails(cmd, *a, **k):
        if "ffprobe" not in Path(str(cmd[0])).name.lower():
            runs["n"] += 1
            if runs["n"] == 3:
                joins.append([str(c) for c in cmd])
                return _Ran(1, stderr="refused")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", third_fails)
    assert encoding.concat(listing, dst).returncode == 0
    assert [_kind(c) for c in joins] == ["copyts", "final", "final", "copyts"]
    assert dst.read_text(encoding="utf-8") == "copyts"


# ---- reading where the video starts ---------------------------------------------------------


def _first_frame_says(monkeypatch, stdout="", returncode=0, without_edits=None):
    """ffprobe answering `stdout` as the edit list shows the file, and
    `without_edits` (the same when None) as an app that ignores it."""
    seen = []

    def run(cmd, *a, **k):
        cmd = [str(c) for c in cmd]
        seen.append(cmd)
        ignored = "-ignore_editlist" in cmd
        return _Ran(returncode, stdout=without_edits if ignored and without_edits is not None else stdout)

    monkeypatch.setattr(subprocess, "run", run)
    return seen


def test_a_late_first_frame_is_reported_in_seconds(monkeypatch, tmp_path):
    """21.3 ms is the AAC priming at 48 kHz: the gap the old join left."""
    seen = _first_frame_says(monkeypatch, "0.021333\n")
    assert round(encoding._video_starts_late(tmp_path / "clip.mp4"), 3) == 0.021
    # Only the first few packets of the video are read, never the whole clip,
    # once as the edit list shows it and once ignoring it.
    assert len(seen) == 2
    assert all("-read_intervals" in cmd and "v:0" in cmd for cmd in seen)
    assert ["-ignore_editlist" in cmd for cmd in seen] == [False, True]
    assert seen[1][seen[1].index("-ignore_editlist") + 1] == "1"
    # The frames after the first say nothing new.
    _first_frame_says(monkeypatch, "0.021333\n0.054667\n0.088000\n")
    assert round(encoding._video_starts_late(tmp_path / "clip.mp4"), 3) == 0.021


def test_a_first_frame_at_0_only_through_the_edit_list_is_late(monkeypatch, tmp_path):
    """Two B-frames' delay at 30fps, which only the edit list hides."""
    _first_frame_says(monkeypatch, "0.000000\n0.033333\n", without_edits="0.066667\n0.100000\n")
    assert round(encoding._video_starts_late(tmp_path / "clip.mp4"), 3) == 0.067


def test_the_later_of_the_two_views_counts(monkeypatch, tmp_path):
    _first_frame_says(monkeypatch, "0.021000\n", without_edits="0.066667\n")
    assert round(encoding._video_starts_late(tmp_path / "clip.mp4"), 3) == 0.067
    assert round(encoding._video_start(tmp_path / "clip.mp4"), 3) == 0.067
    _first_frame_says(monkeypatch, "0.021000\n", without_edits="0.000000\n")
    assert round(encoding._video_starts_late(tmp_path / "clip.mp4"), 3) == 0.021


def test_a_view_that_cannot_be_read_says_nothing(monkeypatch, tmp_path):
    """An ffprobe without -ignore_editlist answers nothing for that view; the
    other still counts."""
    _first_frame_says(monkeypatch, "0.021000\n", without_edits="")
    assert round(encoding._video_starts_late(tmp_path / "clip.mp4"), 3) == 0.021
    _first_frame_says(monkeypatch, "0.000000\n", without_edits="")
    assert encoding._video_starts_late(tmp_path / "clip.mp4") is None


def test_a_video_that_starts_at_0_is_not_late(monkeypatch, tmp_path):
    _first_frame_says(monkeypatch, "0.000000\n")
    assert encoding._video_starts_late(tmp_path / "clip.mp4") is None
    assert encoding._video_start(tmp_path / "clip.mp4") == 0.0
    # Rounding is not lateness.
    _first_frame_says(monkeypatch, "0.001500\n")
    assert encoding._video_starts_late(tmp_path / "clip.mp4") is None


def test_an_unreadable_start_is_never_late(monkeypatch, tmp_path):
    """It must never be the reason a clip is dropped or joined again."""
    clip = tmp_path / "clip.mp4"
    for stdout, code in (("", 1), ("N/A\n", 0), ("", 0)):
        _first_frame_says(monkeypatch, stdout, code)
        assert encoding._video_starts_late(clip) is None
        assert encoding._video_start(clip) is None

    def boom(*a, **k):
        raise OSError("ffprobe missing")

    monkeypatch.setattr(subprocess, "run", boom)
    assert encoding._video_starts_late(clip) is None
    assert encoding._video_start(clip) is None


# ---- Longform joins without the end card's module ---------------------------------------------


def test_longform_assembly_imports_neither_numpy_nor_pillow():
    """Longform's assembly joins with concat() and used to import nothing of
    the end card's at load time; it still doesn't."""
    code = ("import sys; import longform.assemble; "
            "print(sorted(m for m in ('numpy', 'PIL', 'cv2', 'video.outro') if m in sys.modules))")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"


def test_longform_joins_at_0_when_the_end_card_cannot_be_loaded(monkeypatch, tmp_path, capsys):
    """Without numpy and Pillow the card is skipped, as it always was, and
    the parts are still joined, with the same join."""
    from longform import assemble as longform

    # `from video import outro` fails, as it does without numpy and Pillow.
    monkeypatch.setitem(sys.modules, "video.outro", None)
    monkeypatch.delattr("video.outro", raising=False)
    monkeypatch.setattr(longform, "video_encoder_args", lambda *a, **k: ["-c:v", "libx264"])
    joins = []

    def run(cmd, *a, **k):
        cmd = [str(c) for c in cmd]
        if "concat" in cmd:
            joins.append((cmd, Path(cmd[cmd.index("-i") + 1]).read_text(encoding="utf-8")))
        Path(cmd[-1]).write_bytes(b"made")
        return _Ran(stdout="0.000000\n")

    monkeypatch.setattr(subprocess, "run", run)
    source, out = tmp_path / "stream.mp4", tmp_path / "Longform" / "highlights.mp4"
    source.write_bytes(b"stream")

    assert longform.assemble(source, [(0.0, 5.0), (9.0, 12.0)], out, "vid", config={}) == out
    assert "end card skipped" in capsys.readouterr().out
    [(cmd, listing)] = joins
    assert cmd[cmd.index("-i") + 2:] == ["-copyts", "-c", "copy", "-movflags", FINAL, str(out.resolve())]
    assert len(listing.splitlines()) == 2


# ---- real joins -----------------------------------------------------------------------------


def _ffmpeg_or_skip() -> str:
    from core.binaries import ffmpeg, ffprobe

    for exe in (ffmpeg(), ffprobe()):
        if not (Path(exe).exists() or shutil.which(exe)):
            pytest.skip("FFmpeg isn't installed here")
    return ffmpeg()


def _encode(ffmpeg: str, out: Path, *args: str, b_frames: bool = True) -> Path:
    """Three seconds as the app writes a clip: H.264 with or without
    B-frames, AAC at 48 kHz (its priming before 0, behind an edit), final flags."""
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x568:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=f=220:r=48000:d=3", *args,
                    "-c:v", "libx264", "-preset", "veryfast", *([] if b_frames else ["-bf", "0"]),
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
                    "-movflags", FINAL, str(out)], check=True, capture_output=True)
    return out


def test_a_clip_whose_own_video_starts_late_keeps_the_join_with_its_timestamps(tmp_path, capsys):
    """The case that made the join as before the worse one: a clip whose
    video starts a frame (33 ms) late while its sound starts at 0 with the
    AAC priming behind an edit. Kept as it is, the join starts the video 33
    ms in; joined as before, the priming moves to 0 too and the video 54 ms
    in. concat() keeps the 33 ms join and leaves nothing beside it."""
    ffmpeg = _ffmpeg_or_skip()
    clip = _encode(ffmpeg, tmp_path / "late.mp4", "-vf", "setpts=PTS+1/(30*TB)")
    tail = _encode(ffmpeg, tmp_path / "tail.mp4", b_frames=False)
    listing = tmp_path / "list.txt"
    listing.write_text(f"file '{clip.as_posix()}'\nfile '{tail.as_posix()}'\n", encoding="utf-8")

    as_before = tmp_path / "as-before.mp4"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", "-movflags", FINAL, str(as_before)], check=True, capture_output=True)
    assert abs(encoding._video_start(clip) - 1 / 30) < 0.001
    assert encoding._video_start(as_before) > 0.050, "the join as before is the later one"

    joined = tmp_path / "joined.mp4"
    assert encoding.concat(listing, joined).returncode == 0
    assert abs(encoding._video_start(joined) - 1 / 30) < 0.001, "the clip's own 33 ms, and no more"
    assert "33 ms late, as a part does; joined as before it would be no sooner" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "as-before.mp4", "joined.mp4", "late.mp4", "list.txt", "tail.mp4"]


def test_a_real_join_starts_at_0_both_ways(tmp_path):
    ffmpeg = _ffmpeg_or_skip()
    clip = _encode(ffmpeg, tmp_path / "clip.mp4")
    tail = _encode(ffmpeg, tmp_path / "tail.mp4", b_frames=False)
    listing = tmp_path / "list.txt"
    listing.write_text(f"file '{clip.as_posix()}'\nfile '{tail.as_posix()}'\n", encoding="utf-8")
    joined = tmp_path / "joined.mp4"

    assert encoding.concat(listing, joined).returncode == 0
    assert encoding._first_video_frame(joined) == 0.0
    assert encoding._first_video_frame(joined, edit_list=False) == 0.0
