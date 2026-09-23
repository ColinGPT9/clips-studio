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
"""

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
