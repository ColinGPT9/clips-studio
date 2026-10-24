"""The end card has to actually have frames in the finished clip.

It played as a frozen image with its audio running on every published clip.
The cause took three wrong guesses to find, and it was on stderr the whole
time: concat exits 0 while printing "Non-monotonic DTS" 86 times, and
`append()` discards stderr when the command succeeds.

Two things had to be true for the join to work, and only one of them was:

* the card must have NO B-frames. They give the first packets negative DTS
  (-0.067, -0.033, 0), which walks the timeline backwards when appended to a
  clip already at 29 seconds. FFmpeg clamps each to previous+1 and the card's
  86 frames collapse into 86 ticks.
* the card must share the clip's exact frame rate and container timescale.
  29.97 is 30000/1001; a card built at the rounded 2997/100 lands in timebase
  1/11988 against the clip's 1/30000, and the rescale loses it too.

Measured on a real clip: before, video 2.836s shorter than the container.
After, 0.067s, which is two frames of rounding.

The join also has to start the video at 0. Without `-copyts` FFmpeg moved
the whole join until the audio's AAC priming started at 0, which left the
first video frame 10-23 ms late behind an empty edit: a black first frame on
phones and the platforms. And without +negative_cts_offsets the clip's
B-frames leave its first frame two frames in, at 0 only through the edit
list, so an app that ignores edit lists opens on black.
"""

import json
from pathlib import Path

import pytest

outro = pytest.importorskip("video.outro")

FMT = {
    "w": 1080, "h": 1920, "fps": 29.97, "rate": "30000/1001", "timescale": 30000,
    "pix_fmt": "yuv420p", "sample_rate": 48000, "channels": 2,
}


# ---- the card's own encoding -------------------------------------------------


def _render_cmd(monkeypatch, tmp_path, fmt):
    seen = {}

    def fake_run(cmd, *a, **k):
        cmd = [str(c) for c in cmd]
        if any(c.endswith(".wav") for c in cmd):
            seen["cmd"] = cmd

        class R:
            returncode = 1
            stderr = "stopped before encoding"
        return R()

    monkeypatch.setattr(outro.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="outro encode failed"):
        outro._render(fmt, tmp_path / "card.mp4")
    return seen.get("cmd", [])


def test_the_card_is_built_without_b_frames(monkeypatch, tmp_path):
    """The whole bug. B-frames put negative DTS at the start of the card."""
    cmd = _render_cmd(monkeypatch, tmp_path, FMT)
    assert "-bf" in cmd and cmd[cmd.index("-bf") + 1] == "0"


def test_the_card_uses_the_clips_exact_frame_rate(monkeypatch, tmp_path):
    """30000/1001, not the rounded 29.97, so the timebases agree."""
    cmd = _render_cmd(monkeypatch, tmp_path, FMT)
    assert cmd[cmd.index("-framerate") + 1] == "30000/1001"


def test_the_card_uses_the_clips_container_timescale(monkeypatch, tmp_path):
    cmd = _render_cmd(monkeypatch, tmp_path, FMT)
    assert cmd[cmd.index("-video_track_timescale") + 1] == "30000"


# ---- what probe() has to hand over ------------------------------------------


def test_the_timescale_comes_from_the_time_base():
    """1/30000 means timescale 30000. Taking it from the frame rate gives
    1001, which is the wrong number and silently reintroduces the bug."""
    assert outro._timescale("1/30000") == 30000
    assert outro._timescale("1/11988") == 11988


def test_a_useless_timescale_falls_back_rather_than_quantising_to_seconds():
    assert outro._timescale("1/1") == 30000
    assert outro._timescale(None) == 30000
    assert outro._timescale("nonsense") == 30000


# ---- the cache name ----------------------------------------------------------


def test_two_rates_that_round_the_same_do_not_share_a_card():
    """29.97 is both 30000/1001 and 2997/100. They are different timebases,
    and keying on the decimal handed one card to both."""
    a = outro._key({**FMT, "rate": "30000/1001"})
    b = outro._key({**FMT, "rate": "2997/100"})
    assert a != b


def test_integer_rates_keep_the_shipped_name():
    """Cards ship with the app for 30 and 60fps. They have to be found."""
    key = outro._key({**FMT, "fps": 30.0, "rate": "30/1", "sample_rate": 48000})
    assert key == "outro2_1080x1920_30_yuv420p_48000_2.mp4"


def test_the_name_changed_when_b_frames_were_removed():
    """Cards built before the fix are cached on every machine that ever made
    one, and they freeze. The old name must stop being used."""
    assert outro._key(FMT).startswith("outro2_")


# ---- the guard ---------------------------------------------------------------


def test_a_join_that_lost_the_cards_video_is_rejected(monkeypatch, tmp_path):
    """append() used to return True for a file whose video ended three
    seconds before its container. That is the frozen card, and it shipped."""
    joined = tmp_path / "j.mp4"
    joined.write_bytes(b"x")

    def fake_run(cmd, *a, **k):
        cmd = [str(c) for c in cmd]

        class R:
            returncode = 0
            stdout = "29.573\n" if "frame=pts_time" in " ".join(cmd) else "32.409\n"
            stderr = ""
        return R()

    monkeypatch.setattr(outro.subprocess, "run", fake_run)
    short = outro._video_falls_short(joined)
    assert short is not None and short > 2.5


def test_a_healthy_join_passes(monkeypatch, tmp_path):
    joined = tmp_path / "j.mp4"
    joined.write_bytes(b"x")

    def fake_run(cmd, *a, **k):
        cmd = [str(c) for c in cmd]

        class R:
            returncode = 0
            stdout = "32.342\n" if "frame=pts_time" in " ".join(cmd) else "32.409\n"
            stderr = ""
        return R()

    monkeypatch.setattr(outro.subprocess, "run", fake_run)
    assert outro._video_falls_short(joined) is None


def test_an_unreadable_file_does_not_throw_a_clip_away(monkeypatch, tmp_path):
    """The check must never be the reason a good clip is discarded."""
    def boom(*a, **k):
        raise OSError("ffprobe missing")

    monkeypatch.setattr(outro.subprocess, "run", boom)
    assert outro._video_falls_short(tmp_path / "nope.mp4") is None


# ---- the join starts the video at 0 ----------------------------------------

# The muxer flags of every final write (video/encoding.py), the joins' too.
FINAL = "+faststart+negative_cts_offsets"


class _Ran:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _join_shape(cmd, dst, copyts=True, flags=FINAL):
    """The one join, as concat() runs it."""
    return cmd[1:] == ["-y", "-v", "error", "-f", "concat", "-safe", "0",
                       "-i", cmd[cmd.index("-i") + 1], *(["-copyts"] if copyts else []),
                       "-c", "copy", "-movflags", flags, str(dst)]


def _kind(cmd):
    """Which of concat()'s three joins `cmd` is."""
    if "-copyts" in cmd:
        return "copyts"
    return "final" if FINAL in cmd else "old"


def _fake_tools(monkeypatch, tmp_path, first_frame="0.000000\n", without_edits=None, fails=(), starts=None):
    """FFmpeg and ffprobe as the joins see them. FFmpeg writes what it is asked
    to, except the joins named in `fails` ("copyts", "final", "old"), which it
    refuses. ffprobe reports a healthy 30fps clip whose video starts at
    `first_frame` as the edit list shows it, and at `without_edits` (the same
    when None) as an app that ignores the edit list does; `starts` gives a
    join of that kind its own start, both ways. Returns the joins run, with
    each one's list file."""
    joins = []
    card = tmp_path / "card.mp4"
    card.write_bytes(b"card")
    monkeypatch.setattr(outro, "ensure_outro", lambda *a, **k: card)
    monkeypatch.setattr(outro, "_tally", {"added": 0, "skipped": 0})

    def run(cmd, *a, **k):
        cmd = [str(c) for c in cmd]
        if "ffprobe" not in Path(cmd[0]).name.lower():
            made = b"made by ffmpeg"
            if "concat" in cmd:
                listing = Path(cmd[cmd.index("-i") + 1]).read_text(encoding="utf-8")
                joins.append((cmd, listing))
                if _kind(cmd) in fails:
                    return _Ran(1, stderr="refused")
                made += f": {_kind(cmd)}".encode()
            Path(cmd[-1]).write_bytes(made)
            return _Ran()
        if "-read_intervals" in cmd:            # where the joined video starts
            ignored = "-ignore_editlist" in cmd
            kind = Path(cmd[-1]).read_bytes().decode().rpartition(": ")[2]
            if starts and kind in starts:
                return _Ran(stdout=starts[kind])
            return _Ran(stdout=(without_edits or first_frame) if ignored else first_frame)
        if "-show_streams" in cmd:              # probe()
            return _Ran(stdout=json.dumps({"streams": [
                {"codec_type": "video", "width": 1080, "height": 1920,
                 "r_frame_rate": "30/1", "time_base": "1/15360", "pix_fmt": "yuv420p"},
                {"codec_type": "audio", "sample_rate": "48000", "channels": 2}]}))
        if "frame=pts_time" in cmd:             # _video_falls_short: frames to the end
            return _Ran(stdout="5.866667\n")
        if "format=duration" in cmd:
            return _Ran(stdout="5.900000\n")
        return _Ran(1, stderr="not expected here")

    monkeypatch.setattr(outro.subprocess, "run", run)
    return joins


def test_finish_joins_with_copyts_and_the_final_flags(monkeypatch, tmp_path):
    """The render path. -copyts keeps the clip's video at 0 and its AAC
    priming before 0, where the clip itself had them; +negative_cts_offsets
    puts the first frame at 0 without the edit list too."""
    joins = _fake_tools(monkeypatch, tmp_path)
    src, dst = tmp_path / "pre-card.mp4", tmp_path / "clip.mp4"
    src.write_bytes(b"clip")

    assert outro.finish(src, dst, {}) is True
    assert len(joins) == 1, "a join that starts at 0 is made once"
    cmd, listing = joins[0]
    assert _join_shape(cmd, dst)
    assert listing.splitlines()[-1].endswith("card.mp4'")


def test_append_joins_with_copyts_and_the_final_flags(monkeypatch, tmp_path):
    joins = _fake_tools(monkeypatch, tmp_path)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"clip")

    appended = outro.append(clip, {})
    assert appended is True
    assert len(joins) == 1
    assert _join_shape(joins[0][0], tmp_path / ".clip.outro.mp4")
    assert clip.read_bytes() == b"made by ffmpeg: copyts"


def test_the_joins_flags_are_the_final_writes_own():
    """One set of flags for everything posted, kept in video/encoding.py."""
    from video.encoding import final_movflags

    assert final_movflags() == ["-movflags", FINAL]


def test_a_late_join_keeps_the_join_as_before_only_when_it_starts_sooner(monkeypatch, tmp_path, capsys):
    """If the video still starts late, a part already did. The join as it was
    made before -copyts is made beside it, once, and replaces it only when
    its video starts sooner; the clip keeps its card either way. (The joins
    themselves: tests/test_video_join.py.)"""
    joins = _fake_tools(monkeypatch, tmp_path, starts={"copyts": "0.500000\n", "final": "0.000000\n"})
    src, dst = tmp_path / "pre-card.mp4", tmp_path / "clip.mp4"
    src.write_bytes(b"clip")

    assert outro.finish(src, dst, {}) is True
    assert len(joins) == 2, "made again once, not until it gives up"
    assert _join_shape(joins[0][0], dst) and _join_shape(joins[1][0], tmp_path / ".clip.before.mp4", copyts=False)
    assert dst.read_bytes() == b"made by ffmpeg: final"
    assert "500 ms late; joined as before instead" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["card.mp4", "clip.mp4"]


def test_a_late_join_stays_when_the_join_as_before_is_no_sooner(monkeypatch, tmp_path, capsys):
    """A clip whose own video starts late: as before, its priming would be
    moved to 0 as well, and the video start later still."""
    joins = _fake_tools(monkeypatch, tmp_path, starts={"copyts": "0.033000\n", "final": "0.054000\n"})
    src, dst = tmp_path / "pre-card.mp4", tmp_path / "clip.mp4"
    src.write_bytes(b"clip")

    assert outro.finish(src, dst, {}) is True
    assert [_kind(cmd) for cmd, _l in joins] == ["copyts", "final"]
    assert dst.read_bytes() == b"made by ffmpeg: copyts"
    assert "33 ms late, as a part does; joined as before it would be no sooner" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["card.mp4", "clip.mp4"]


def test_a_join_at_0_only_through_its_edit_list_counts_as_late(monkeypatch, tmp_path, capsys):
    """B-frames written without +negative_cts_offsets: the edit list pulls
    the first frame back to 0, and an app that ignores it opens on black for
    two frames. That is late too."""
    joins = _fake_tools(monkeypatch, tmp_path, without_edits="0.066667\n",
                        starts={"final": "0.000000\n"})
    src, dst = tmp_path / "pre-card.mp4", tmp_path / "clip.mp4"
    src.write_bytes(b"clip")

    assert outro.finish(src, dst, {}) is True
    assert [_kind(cmd) for cmd, _l in joins] == ["copyts", "final"]
    assert "67 ms late; joined as before instead (0 ms)" in capsys.readouterr().out


def test_a_late_start_never_costs_append_its_card(monkeypatch, tmp_path):
    for starts, kept in (({"copyts": "0.500000\n", "final": "0.000000\n"}, b"final"),
                         ({"copyts": "0.500000\n", "final": "0.500000\n"}, b"copyts")):
        joins = _fake_tools(monkeypatch, tmp_path, starts=starts)
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"clip")

        appended = outro.append(clip, {})
        assert appended is True
        assert [_kind(cmd) for cmd, _l in joins] == ["copyts", "final"]
        assert clip.read_bytes() == b"made by ffmpeg: " + kept


def test_a_join_that_fails_with_copyts_is_made_again_as_before(monkeypatch, tmp_path, capsys):
    """A join that is not made at all would cost the clip its card, which is
    worse than a late start."""
    joins = _fake_tools(monkeypatch, tmp_path, fails=("copyts",))
    src, dst = tmp_path / "pre-card.mp4", tmp_path / "clip.mp4"
    src.write_bytes(b"clip")

    assert outro.finish(src, dst, {}) is True
    assert [_kind(cmd) for cmd, _l in joins] == ["copyts", "final"]
    assert _join_shape(joins[1][0], dst, copyts=False)
    assert "(refused); joined as before instead" in capsys.readouterr().out


def test_if_the_new_flags_are_refused_the_join_is_made_exactly_as_before(monkeypatch, tmp_path):
    """The last resort is the join as it always was, flags and all, so the
    card is never lost to this change."""
    joins = _fake_tools(monkeypatch, tmp_path, fails=("copyts", "final"))
    src, dst = tmp_path / "pre-card.mp4", tmp_path / "clip.mp4"
    src.write_bytes(b"clip")

    assert outro.finish(src, dst, {}) is True
    assert [_kind(cmd) for cmd, _l in joins] == ["copyts", "final", "old"]
    assert _join_shape(joins[2][0], dst, copyts=False, flags="+faststart")
    assert outro._tally == {"added": 1, "skipped": 0}


def test_a_clip_whose_card_cannot_be_made_is_still_written_with_the_final_flags(monkeypatch, tmp_path):
    """finish()'s fallback copies the clip to dst without a card. A plain
    remux would put its first frame back behind the edit list."""
    _fake_tools(monkeypatch, tmp_path)
    remuxed = []
    run = outro.subprocess.run

    def spy(cmd, *a, **k):
        cmd = [str(c) for c in cmd]
        if "-i" in cmd and "concat" not in cmd and "ffprobe" not in Path(cmd[0]).name.lower():
            remuxed.append(cmd)
        return run(cmd, *a, **k)

    monkeypatch.setattr(outro.subprocess, "run", spy)
    monkeypatch.setattr(outro, "probe", lambda _p: None)    # the card cannot be matched
    src, dst = tmp_path / "pre-card.mp4", tmp_path / "clip.mp4"
    src.write_bytes(b"clip")

    assert outro.finish(src, dst, {}) is False
    [cmd] = remuxed
    assert cmd[1:] == ["-y", "-v", "error", "-i", str(src.resolve()), "-c", "copy",
                       "-movflags", FINAL, str(dst.resolve())]
    assert dst.read_bytes() == b"made by ffmpeg"


def test_longforms_join_starts_the_video_at_0_too(monkeypatch, tmp_path):
    """Highlights and Edited Streams join their parts and the card in one
    concat; it is the same join."""
    from longform import assemble as longform

    joins = _fake_tools(monkeypatch, tmp_path)
    monkeypatch.setattr(longform, "video_encoder_args", lambda *a, **k: ["-c:v", "libx264"])
    source, out = tmp_path / "stream.mp4", tmp_path / "Longform" / "highlights.mp4"
    source.write_bytes(b"stream")

    assert longform.assemble(source, [(0.0, 5.0), (9.0, 12.0)], out, "vid", config={}) == out
    assert len(joins) == 1
    cmd, listing = joins[0]
    assert _join_shape(cmd, out.resolve())
    assert len(listing.splitlines()) == 3 and listing.splitlines()[-1].endswith("card.mp4'")
