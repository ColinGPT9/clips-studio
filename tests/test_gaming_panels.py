"""The stream's solid panels (gaming/panels.py): a black chat bar is found and
kept out of the game crop; chat drawn see-through over the game is not."""

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from gaming import panels  # noqa: E402

W, H = 960, 540
CHAT = ["streamer_fan: that was insane", "modbot: be nice in chat", "viewer42: LETS GO",
        "someone: how long is the run", "another: first time here hi", "lurker: gg"]


def _game(seed):
    """A game picture that is different on every frame: soft colour blobs."""
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (9, 16, 3)).astype(np.uint8)
    return cv2.resize(small, (W, H), interpolation=cv2.INTER_CUBIC)


def _lines(img, x, y, seed, colour=(255, 255, 255)):
    for i in range(5):
        cv2.putText(img, CHAT[(seed + i) % len(CHAT)], (x, y + i * 16), cv2.FONT_HERSHEY_SIMPLEX, 0.4, colour, 1,
                    cv2.LINE_AA)


def test_a_black_chat_bar_under_the_game_is_found_to_its_whole_width():
    frames = []
    for i in range(5):
        f = _game(i)
        f[448:, 156:] = 0                                   # the bar, the full width right of the webcam
        f[370:, :156] = (40, 90, 160)                       # the webcam
        _lines(f, 162, 466, i)                               # chat only on its left part
        frames.append(f)
    found = panels.solid_panels(frames)
    assert len(found) == 1
    x, y, w, h = found[0]
    assert abs(x - 156 / W) < 0.01 and abs(y - 448 / H) < 0.01
    assert x + w > 0.99 and y + h > 0.99                     # grown past the text to the bar's own end


def test_chat_drawn_over_the_gameplay_is_part_of_the_picture():
    frames = []
    for i in range(5):
        f = _game(i)
        _lines(f, 10, 300, i)                                # see-through: the game changes behind it
        frames.append(f)
    assert panels.solid_panels(frames) == []


def test_the_games_own_text_that_moves_is_not_a_panel():
    frames = []
    for i in range(5):
        f = _game(i)
        box = (100 + 150 * i, 60 + 60 * i)
        f[box[1]:box[1] + 90, box[0]:box[0] + 240] = 0     # a dialogue box, somewhere else each time
        _lines(f, box[0] + 6, box[1] + 18, i)
        frames.append(f)
    assert panels.solid_panels(frames) == []


def test_fewer_than_three_frames_find_nothing():
    f = _game(0)
    f[448:, 156:] = 0
    _lines(f, 162, 466, 0)
    assert panels.solid_panels([f, f.copy()]) == []


def test_frames_of_any_size_are_looked_at_the_same_way():
    frames = []
    for i in range(5):
        f = _game(i)
        f[448:, 156:] = 0
        _lines(f, 162, 466, i)
        frames.append(cv2.resize(f, (1920, 1080), interpolation=cv2.INTER_NEAREST))
    (x, y, _w, _h), = panels.solid_panels(frames)
    assert abs(x - 156 / W) < 0.01 and abs(y - 448 / H) < 0.01


def test_a_video_is_read_at_frames_spread_through_it(tmp_path):
    import subprocess

    from core.binaries import ffmpeg

    try:
        subprocess.run([ffmpeg(), "-version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("FFmpeg isn't available")
    for i in range(5):
        f = _game(i)
        f[448:, 156:] = 0
        _lines(f, 162, 466, i)
        cv2.imwrite(str(tmp_path / f"f{i}.png"), f)
    video = tmp_path / "stream.mp4"
    subprocess.run([ffmpeg(), "-v", "error", "-framerate", "1", "-i", str(tmp_path / "f%d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12", str(video)], check=True)
    (x, y, _w, _h), = panels.panels_in_video(video)
    assert abs(y - 448 / H) < 0.01
