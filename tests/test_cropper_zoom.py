"""The vertical renderer's zoom: a crop that widens past 9:16.

Basketball framing steps the crop out (4:5, 1:1, the whole 16:9 frame) to
keep a pass or the basket in view, and hands video/cropper a "zoom" path with
a key per frame. Those frames are composed at 1080x1920, the wider picture
over a blurred wash. Everything else must render exactly as it did: no zoom,
or a zoom that never gets wider than 9:16, gives today's FFmpeg command and
today's frames, byte for byte.

The interpolation tests need only the standard library and run on CI, which
has no cv2 or numpy to import video/cropper with: they load the functions
from its source. The rendering tests skip there.
"""

from __future__ import annotations

import ast
import bisect
import random
import subprocess
from pathlib import Path

import pytest

CROPPER = Path(__file__).resolve().parent.parent / "video" / "cropper.py"


def _from_source(*names):
    """Top-level functions of video/cropper.py, compiled on their own so the
    module's cv2 and numpy imports never run."""
    tree = ast.parse(CROPPER.read_text(encoding="utf-8"))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace: dict = {"bisect_left": bisect.bisect_left}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(CROPPER), "exec"), namespace)
    return [namespace[n] for n in names]


def _interpolate():
    return _from_source("_interpolate", "_key_time")[0]


def _linear(path, t):
    """_interpolate as it was before the binary search: the reference."""
    if t <= path[0][0]:
        return path[0][1]
    if t >= path[-1][0]:
        return path[-1][1]
    for (t0, x0), (t1, x1) in zip(path, path[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return x0
            return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
    return path[-1][1]


# ---- interpolation (pure) ---------------------------------------------------------------------


def test_the_binary_search_gives_the_linear_scans_values_to_the_last_bit():
    interpolate = _interpolate()
    rng = random.Random(7)
    for _ in range(300):
        n = rng.randint(1, 40)
        times = sorted(round(rng.uniform(0, 10), rng.choice((1, 2, 4))) for _ in range(n))
        path = [(t, rng.random()) for t in times]          # rounded times repeat now and then
        probes = [*times, -1.0, 11.0, *(rng.uniform(-0.5, 10.5) for _ in range(30))]
        probes += [(a + b) / 2 for a, b in zip(times, times[1:])]
        for t in probes:
            assert interpolate(path, t) == _linear(path, t), (path, t)


def test_keys_as_lists_work_too():
    """A path that went through JSON (the editor, remote rendering) has lists, not tuples."""
    interpolate = _interpolate()
    path = [[0.0, 0.2], [1.0, 0.6], [2.0, 0.4]]
    assert interpolate(path, 0.5) == pytest.approx(0.4)
    assert interpolate(path, 1.5) == pytest.approx(0.5)


def test_a_cut_between_two_frames_is_not_blended_into_either():
    """One key per frame, as basketball framing writes it, with the crop
    snapping across the court at a cut: each frame reads its own key."""
    interpolate = _interpolate()
    fps, cut = 59.94, 90
    xs = [0.30 + 0.0005 * f if f < cut else 0.78 - 0.0005 * (f - cut) for f in range(180)]
    path = [(round(f / fps, 4), x) for f, x in enumerate(xs)]
    for (t, x) in path:
        assert interpolate(path, t) == pytest.approx(x, abs=1e-12)
        assert interpolate(path, t) == _linear(path, t)
    assert interpolate(path, path[cut - 1][0]) == pytest.approx(xs[cut - 1], abs=1e-12)   # the old crop
    assert interpolate(path, path[cut][0]) == pytest.approx(xs[cut], abs=1e-12)           # snapped
    # The renderer asks at frame / fps, which the keys' 4-decimal times miss
    # by up to 50 microseconds: a cut then leaks a third of a percent of its
    # jump into its neighbour, a pixel or two, and no more.
    for f in (cut - 1, cut):
        assert interpolate(path, f / fps) == pytest.approx(xs[f], abs=0.002)


# ---- rendering (cv2 and numpy) ------------------------------------------------------------------


def _striped_clip(tmp_path, np, cv2, w=640, h=360, n=12, fps=10.0, name="clip.mp4"):
    """A clip of bold upright stripes: every row of the picture has sharp
    edges, the wash made from it is smooth, and neither is black, so the
    picture and the wash can be told apart row by row."""
    stripe = ((np.arange(w) // 12) % 2).astype(bool)
    frame = np.empty((h, w, 3), np.uint8)
    frame[:, stripe] = (60, 60, 220)
    frame[:, ~stripe] = (60, 220, 60)
    path = tmp_path / name
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for _ in range(n):
        out.write(frame)
    out.release()
    return path


@pytest.fixture
def piped(monkeypatch):
    """Runs the renderer without FFmpeg's encode: keeps the command and
    every frame handed to it."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import video.cropper as cropper

    seen: dict = {}

    def run(cmd, ass_path, produce):
        chunks: list = []
        produce(chunks.append)
        seen.update(cmd=cmd, frames=chunks, ass_path=ass_path)

    monkeypatch.setattr(cropper, "_run_ffmpeg_piped", run)
    return cropper, seen


def _render(cropper, seen, clip, tracking, out, **kw):
    cropper.render_vertical(clip, tracking, out, **kw)
    return list(seen["cmd"]), list(seen["frames"])


def _sharp_rows(np, img):
    """Rows of a 1080x1920 frame holding the sharp picture rather than the
    blurred wash, which barely changes from one pixel to the next."""
    edge = np.abs(np.diff(img.astype(np.int16), axis=1)).sum(axis=2).max(axis=1)
    return np.flatnonzero(edge > 40)


def _picture_rows(np, frame_bytes):
    img = np.frombuffer(frame_bytes, np.uint8).reshape(1920, 1080, 3).astype(np.int16)
    return _sharp_rows(np, img), img


def test_no_zoom_and_a_zoom_that_stays_9x16_render_exactly_as_before(piped, tmp_path):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    cropper, seen = piped
    clip = _striped_clip(tmp_path, np, cv2)
    out, ass = tmp_path / "out.mp4", tmp_path / "subs.ass"
    path = [(0.0, 0.3), (0.6, 0.7), (1.2, 0.5)]
    kw = {"ass_path": ass, "vf_extra": "eq=saturation=1.1"}
    for rows in (None, (0.0, 0.75)):
        plain = {"mode": "track", "path": path, **({"rows": rows} if rows else {})}
        cmd, frames = _render(cropper, seen, clip, plain, out, **kw)
        crop_h = 360 if rows is None else 270
        crop_w = int(crop_h * 9 / 16) // 2 * 2
        # Today's command, as it always was: the crop on the pipe, FFmpeg scales.
        assert cmd[cmd.index("-s") + 1] == f"{crop_w}x{crop_h}"
        assert cmd[cmd.index("-vf") + 1] == "scale=1080:1920:flags=lanczos,setsar=1,eq=saturation=1.1,subtitles=subs.ass"
        assert len(frames) == 12 and len(frames[0]) == crop_w * crop_h * 3
        for slack in (0.0, 1.5):   # at 9:16, and within rounding of it
            width = (crop_w + slack) / 640
            zoomed = {**plain, "zoom": [(round(f / 10, 4), width) for f in range(12)]}
            assert _render(cropper, seen, clip, zoomed, out, **kw) == (cmd, frames)
    # Narrower than 9:16 is no zoom at all.
    assert _render(cropper, seen, clip, {"mode": "track", "path": path, "zoom": [(0.0, 0.1)]}, out, **kw)[0] \
        == _render(cropper, seen, clip, {"mode": "track", "path": path}, out, **kw)[0]


def test_a_wider_crop_is_composed_and_ffmpeg_only_filters_it(piped, tmp_path):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    cropper, seen = piped
    clip = _striped_clip(tmp_path, np, cv2)
    out, ass = tmp_path / "out.mp4", tmp_path / "subs.ass"
    path = [(0.0, 0.5)]
    kw = {"ass_path": ass, "vf_extra": "eq=saturation=1.1"}
    before, _ = _render(cropper, seen, clip, {"mode": "track", "path": path}, out, **kw)
    zoom = [(0.0, 0.316), (0.5, 1.0), (1.2, 1.0)]
    cmd, frames = _render(cropper, seen, clip, {"mode": "track", "path": path, "zoom": zoom}, out, **kw)
    assert cmd[cmd.index("-s") + 1] == "1080x1920"
    assert cmd[cmd.index("-vf") + 1] == "setsar=1,eq=saturation=1.1,subtitles=subs.ass"   # no scale step
    # Everything else is today's command: input order, maps, yuv420p, cfr, audio from input 1.
    same = [i for i, (a, b) in enumerate(zip(before, cmd)) if a != b]
    assert len(before) == len(cmd) and same == [before.index("-s") + 1, before.index("-vf") + 1]
    assert cmd[cmd.index("-pix_fmt", cmd.index("-vf")) + 1] == "yuv420p"
    assert "1:a:0?" in cmd and cmd[cmd.index("-fps_mode") + 1] == "cfr"
    assert seen["ass_path"] == ass
    assert len(frames) == 12 and all(len(f) == 1080 * 1920 * 3 for f in frames)

    # The first frame is at 9:16 and fills the screen; the last is the whole
    # 16:9 frame, 1080 wide and 608 tall, over a wash that is not black.
    rows, _ = _picture_rows(np, frames[0])
    assert len(rows) >= 1900
    rows, img = _picture_rows(np, frames[-1])
    assert abs(len(rows) - 608) <= 4 and abs(rows[0] - 656) <= 2 and abs(rows[-1] - 1263) <= 2
    for band in (img[:640], img[1280:]):
        assert band.mean() > 60 and band.reshape(-1, 3).mean(axis=0).min() > 30
    assert np.abs(np.diff(img[:600], axis=0)).mean() < 0.5      # blurred, not a copy of the picture


def test_the_composed_crop_keeps_only_the_rows_it_is_given(piped, tmp_path):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    cropper, seen = piped
    clip = _striped_clip(tmp_path, np, cv2)
    tracking = {"mode": "track", "path": [(0.0, 0.5)], "rows": (0.0, 0.75), "zoom": [(0.0, 1.0)]}
    _, frames = _render(cropper, seen, clip, tracking, tmp_path / "out.mp4")
    rows, _ = _picture_rows(np, frames[0])
    assert abs(len(rows) - 456) <= 4      # 640x270 at 1080 wide


def test_the_wash_is_remade_every_second_frame(piped, tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    cropper, seen = piped
    clip = _striped_clip(tmp_path, np, cv2)
    made = []
    real = cropper._wash
    monkeypatch.setattr(cropper, "_wash", lambda region: made.append(1) or real(region))
    _render(cropper, seen, clip, {"mode": "track", "path": [(0.0, 0.5)], "zoom": [(0.0, 0.8)]}, tmp_path / "o.mp4")
    assert len(made) == 6     # 12 frames


def test_the_face_safe_layout_ignores_a_zoom(piped, tmp_path):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    cropper, seen = piped
    clip = _striped_clip(tmp_path, np, cv2)
    base = {"mode": "track", "path": [(0.0, 0.5)], "face_y": 0.1}
    out = tmp_path / "o.mp4"
    assert _render(cropper, seen, clip, {**base, "zoom": [(0.0, 1.0)]}, out) == _render(cropper, seen, clip, base, out)


def _ffmpeg_or_skip() -> str:
    from core.binaries import ffmpeg

    binary = ffmpeg()
    try:
        subprocess.run([binary, "-version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("FFmpeg isn't available")
    return binary


def test_a_zoom_out_to_the_whole_frame_renders_a_real_vertical_clip(tmp_path):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    _ffmpeg_or_skip()
    import video.cropper as cropper

    clip = _striped_clip(tmp_path, np, cv2, n=20)
    out = tmp_path / "out.mp4"
    zoom = [(0.0, 0.316), (0.8, 1.0), (2.0, 1.0)]
    cropper.render_vertical(clip, {"mode": "track", "path": [(0.0, 0.5)], "zoom": zoom}, out)
    cap = cv2.VideoCapture(str(out))
    try:
        assert (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == (1080, 1920)
        frames = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(frame)
    finally:
        cap.release()
    assert len(frames) == 20
    last = frames[-1].astype(np.int16)
    picture = _sharp_rows(np, last)
    assert abs(len(picture) - 608) <= 12
    assert last[:600].mean() > 60 and last[1330:].mean() > 60    # the wash bands are not black


def test_a_top_band_keeps_the_picture_under_the_apps_bar(piped, tmp_path):
    # Basketball's layout (sports/basketball/action.picture_share): the picture
    # fills the screen below a 250 px band, wider than 9:16, the band a blur of it.
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    cropper, seen = piped
    clip = _striped_clip(tmp_path, np, cv2)
    out = tmp_path / "out.mp4"
    tracking = {"mode": "track", "path": [(0.0, 0.5)], "band_top": 250 / 1920}
    cmd, frames = _render(cropper, seen, clip, tracking, out)
    assert cmd[cmd.index("-s") + 1] == "1080x1920" and cmd[cmd.index("-vf") + 1] == "setsar=1"
    assert len(frames) == 12 and all(len(f) == 1080 * 1920 * 3 for f in frames)
    rows, img = _picture_rows(np, frames[0])
    assert abs(rows[0] - 250) <= 2 and rows[-1] >= 1915        # the picture: from 250 px to the bottom
    assert np.abs(np.diff(img[:240], axis=0)).mean() < 0.5      # the band: blurred
    # The crop is wider than 9:16 by the band: 1080 / 1670 of the 360 rows.
    plain = _render(cropper, seen, clip, {"mode": "track", "path": [(0.0, 0.5)]}, out)[0]
    assert plain[plain.index("-s") + 1] == f"{int(360 * 9 / 16) // 2 * 2}x360"


def _composers(monkeypatch, cropper):
    """Every _ZoomComposer the renderer makes, as (src_w, crop width, y0, y1, top, bottom)."""
    made = []
    real = cropper._ZoomComposer

    def make(src_w, base_w, y0, y1, top=None, bottom=None):
        made.append((src_w, base_w, y0, y1, top, bottom))
        return real(src_w, base_w, y0, y1, top=top, bottom=bottom)

    monkeypatch.setattr(cropper, "_ZoomComposer", make)
    return made


def test_the_picture_between_two_bands_is_cropped_to_their_shape_never_squashed(piped, tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    cropper, seen = piped
    made = _composers(monkeypatch, cropper)
    both = {"mode": "track", "path": [(0.0, 0.5)], "band_top": 250 / 1920, "band_bottom": 420 / 1920}
    # A broadcast: the crop is as much wider than 9:16 as the picture is shorter (1080 x 1250).
    _render(cropper, seen, _striped_clip(tmp_path, np, cv2), both, tmp_path / "out.mp4")
    _src_w, crop_w, y0, y1, top, bottom = made[-1]
    assert (y0, y1, top, bottom) == (0, 360, 250, 420)
    assert abs(crop_w - 360 * 1080 / 1250) <= 2
    # A 3:4 phone video, narrower than that: its middle rows that fit are kept, at its own shape.
    tall = _striped_clip(tmp_path, np, cv2, w=480, h=640, name="tall.mp4")
    _render(cropper, seen, tall, both, tmp_path / "out.mp4")
    _src_w, crop_w, y0, y1, _top, _bottom = made[-1]
    assert crop_w == 480 and abs((y1 - y0) - 480 * 1250 / 1080) <= 2 and abs(y0 - (640 - y1)) <= 2


def test_bands_above_and_below_keep_the_picture_clear_of_the_apps_text(piped, tmp_path):
    # Basketball's default Crop (sports/basketball/action.bands): the picture
    # between a 250 px band under the apps' top bar and a 420 px band under
    # their caption, wider than 9:16 by both, the bands a blur of it.
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    cropper, seen = piped
    clip = _striped_clip(tmp_path, np, cv2)
    out = tmp_path / "out.mp4"
    tracking = {"mode": "track", "path": [(0.0, 0.5)], "band_top": 250 / 1920, "band_bottom": 420 / 1920}
    cmd, frames = _render(cropper, seen, clip, tracking, out)
    assert cmd[cmd.index("-s") + 1] == "1080x1920"
    assert len(frames) == 12 and all(len(f) == 1080 * 1920 * 3 for f in frames)
    rows, img = _picture_rows(np, frames[0])
    assert abs(rows[0] - 250) <= 2 and abs(rows[-1] - (1920 - 420)) <= 3   # the picture: 250 px to 1500 px
    assert np.abs(np.diff(img[:240], axis=0)).mean() < 0.5      # the top band: blurred
    assert np.abs(np.diff(img[1510:], axis=0)).mean() < 0.5     # the bottom band: blurred
    assert img[1510:].mean() > 20                               # and not black
    # A bottom band alone works the same way.
    rows = _picture_rows(np, _render(cropper, seen, clip, {"mode": "track", "path": [(0.0, 0.5)],
                                                            "band_bottom": 420 / 1920}, out)[1][0])[0]
    assert rows[0] <= 2 and abs(rows[-1] - 1500) <= 3
