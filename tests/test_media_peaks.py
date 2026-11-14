"""Editor suite step 6: the waveform pyramid (video_editor/media/peaks.py).

- The standard-library path and the numpy path give the same bytes.
- Every level is the min of mins and the max of maxes of the level below.
- The format reads back as it was written, and a part of it can be cut out.
- From a generated tone-then-silence file, through real FFmpeg: the tone's
  level where the tone is, zero where the silence is, on both paths.

The PCM tests run on CI's light install; numpy and FFmpeg skip without them.
"""

import math
import struct
from array import array

import pytest
from media_helpers import make_tone_and_silence, make_video

from video_editor.media import peaks


def _pcm(samples) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def _sine(n, amplitude=16000, period=37):
    return [int(amplitude * math.sin(2 * math.pi * i / period)) for i in range(n)]


def test_bins_from_pcm_in_any_pieces():
    samples = _sine(16 * 100 + 5)         # 100 whole bins and a part of one
    data = _pcm(samples)
    whole = peaks.Builder(use_numpy=False)
    whole.feed(data)
    pieces = peaks.Builder(use_numpy=False)
    for i in range(0, len(data), 333):     # odd sizes, even half a sample
        pieces.feed(data[i:i + 333])
    a, b = whole.finish(), pieces.finish()
    assert a.to_bytes() == b.to_bytes()
    base = a.levels[0]
    assert base[0] == 512 and len(base[2]) == 101
    for i in range(100):
        chunk = samples[16 * i: 16 * i + 16]
        assert base[2][i] == min(chunk) >> 8 and base[3][i] == max(chunk) >> 8
    assert base[2][100] == min(samples[1600:]) >> 8
    assert a.duration_us == len(samples) * 1_000_000 // peaks.SAMPLE_RATE


def test_the_extremes_and_silence():
    b = peaks.Builder(use_numpy=False)
    b.feed(_pcm([32767, -32768] * 8 + [0] * 16 + [-1] * 16))
    base = b.finish().levels[0]
    assert list(base[2]) == [-128, 0, -1] and list(base[3]) == [127, 0, -1]


def test_each_level_halves_the_one_below():
    b = peaks.Builder(use_numpy=False)
    b.feed(_pcm(_sine(16 * 1000 + 7, period=500)))
    made = b.finish()
    assert [lv[0] for lv in made.levels] == [512, 256, 128, 64, 32, 16, 8, 4, 2, 1]
    for (_, _, fine_lo, fine_hi), (_, _, lo, hi) in zip(made.levels, made.levels[1:]):
        assert len(lo) == -(-len(fine_lo) // 2)
        for i in range(len(lo)):
            assert lo[i] == min(fine_lo[2 * i: 2 * i + 2]) and hi[i] == max(fine_hi[2 * i: 2 * i + 2])


def test_the_numpy_path_gives_the_same_bytes():
    pytest.importorskip("numpy")
    data = _pcm([*_sine(16 * 5000 + 9, period=91), 32767, -32768, 0])
    plain, fast = peaks.Builder(use_numpy=False), peaks.Builder(use_numpy=None)
    assert fast.np is not None
    for i in range(0, len(data), 4099):
        plain.feed(data[i:i + 4099])
        fast.feed(data[i:i + 4099])
    assert plain.finish().to_bytes() == fast.finish().to_bytes()


def test_the_format_reads_back_and_cuts():
    b = peaks.Builder(use_numpy=False)
    b.feed(_pcm(_sine(peaks.SAMPLE_RATE * 3, period=300)))      # 3 seconds
    made = b.finish()
    data = made.to_bytes()
    assert data[:4] == b"CKPK" and struct.unpack_from("<HHIQ", data, 4) == (1, 10, 8192, 3_000_000)
    back = peaks.Peaks.from_bytes(data)
    assert back.to_bytes() == data
    part = back.select(64, start_us=1_000_000, end_us=2_000_000)
    assert [lv[0] for lv in part.levels] == [64, 32, 16, 8, 4, 2, 1]
    rate, first, lo, hi = part.levels[0]
    assert first == 64 and len(lo) == 64
    assert list(lo) == list(made.levels[3][2][64:128])
    assert peaks.Peaks.from_bytes(part.to_bytes()).to_bytes() == part.to_bytes()
    assert next(lv[0] for lv in back.select(100).levels) == 64       # rounded down to a level
    as_json = part.to_json()
    assert as_json["levels"][0]["per_second"] == 64 and as_json["levels"][0]["first"] == 64
    with pytest.raises(ValueError):
        peaks.Peaks.from_bytes(b"NOPE" + data[4:])


def test_two_hours_at_64_a_second_is_small():
    bins = 7200 * peaks.BASE_RATE
    made = peaks.Peaks.from_base(array("b", bytes(bins)), array("b", bytes(bins)), 7_200_000_000)
    sent = made.select(64).to_bytes()
    assert len(sent) < 2_000_000
    assert len(made.to_bytes()) < 16_000_000


@pytest.mark.parametrize("use_numpy", [False, None])
def test_a_tone_then_silence_through_ffmpeg(tmp_path, use_numpy):
    if use_numpy is None:
        pytest.importorskip("numpy")
    source = make_tone_and_silence(tmp_path / "tone.wav", tone_s=1.0, silence_s=1.0, amplitude=0.5)
    made = peaks.compute(source, use_numpy=use_numpy, duration_us=2_000_000)
    assert abs(made.duration_us - 2_000_000) < 10_000
    # At 128 bins a second each bin (7.8 ms) holds more than one period of
    # the 200 Hz tone (5 ms), so every bin of the tone reaches its peak:
    # half of full scale is 64 as a signed byte.
    rate, _, lo, hi = made.levels[2]
    assert rate == 128
    tone = range(4, 124)                  # well inside the first second
    silence = range(132, 252)             # well inside the second
    assert all(60 <= hi[i] <= 66 and -66 <= lo[i] <= -60 for i in tone), (list(lo[4:124]), list(hi[4:124]))
    assert all(lo[i] in (0, -1) and hi[i] in (0, -1) for i in silence)
    base_lo, base_hi = made.levels[0][2], made.levels[0][3]
    assert max(base_hi[16:496]) <= 66 and min(base_lo[16:496]) >= -66
    assert all(base_lo[i] in (0, -1) and base_hi[i] in (0, -1) for i in range(528, 1008))
    per_second = made.levels[-1]
    assert per_second[0] == 1 and 60 <= per_second[3][0] <= 66 and per_second[3][1] in (0, -1)


def test_both_paths_agree_on_a_real_file(tmp_path):
    pytest.importorskip("numpy")
    source = make_video(tmp_path / "v.mp4", seconds=3)
    assert peaks.compute(source, use_numpy=False).to_bytes() == peaks.compute(source, use_numpy=None).to_bytes()


def test_a_file_with_no_sound_fails_plainly(tmp_path):
    source = make_video(tmp_path / "silent.mp4", seconds=1, audio=False)
    with pytest.raises(RuntimeError, match="couldn't read the sound"):
        peaks.compute(source)
