"""Gaming / Reaction layouts: where each part goes on the 1080x1920 Short.

Geometry and framing are pure Python (no numpy), so most of this runs in CI;
the real renders need FFmpeg and OpenCV and are skipped without them.
"""

import re
import subprocess
from pathlib import Path

import pytest

from gaming import framing, layout

ROOT = Path(__file__).resolve().parent.parent
W, H = 1920, 1080
CAM = (0.0, 0.0, 0.25, 1 / 3)                       # a webcam in the top-left corner
TIKTOK = framing.safe_zone("tiktok")


def _plan(preset, cam=CAM, heads=None, **settings):
    return layout.plan(W, H, {"preset": preset, "cam": list(cam) if cam else None, **settings}, heads)


# ---- the layouts ------------------------------------------------------------------------


@pytest.mark.parametrize("preset", sorted(layout.PRESETS))
def test_every_layout_stays_on_the_canvas_in_even_sizes(preset):
    extra = {"ui_box": [0.3, 0.0, 0.4, 0.08], "cam2": [0.75, 0.0, 0.25, 1 / 3]}
    p = _plan(preset, **extra)
    assert p.preset == preset
    for e in p.elements:
        x, y, w, h = e.dest
        assert 0 <= x and 0 <= y and x + w <= 1080 and y + h <= 1920, (preset, e)
        assert all(v % 2 == 0 for v in (*e.dest, *e.src)), (preset, e)
        sx, sy, sw, sh = e.src
        assert 0 <= sx and 0 <= sy and sx + sw <= W and sy + sh <= H, (preset, e)


@pytest.mark.parametrize("preset", [k for k, v in layout.PRESETS.items() if v["type"] == "stack"])
def test_stacked_rows_meet_exactly_top_to_bottom(preset):
    p = _plan(preset, ui_box=[0.3, 0.0, 0.4, 0.08], cam2=[0.75, 0.0, 0.25, 1 / 3])
    edges = sorted({(e.dest[1], e.dest[1] + e.dest[3]) for e in p.elements
                    if e.role not in layout.PRESETS[preset].get("overlay", [])})
    assert edges[0][0] == 0 and edges[-1][1] == 1920
    assert all(a[1] == b[0] for a, b in zip(edges, edges[1:]))


def test_camera_top_puts_the_camera_at_the_top_and_game_top_the_game():
    p = _plan("split", order="cam_top")
    assert p.element("cam").dest[1] == 0 and p.element("game").dest[1] + p.element("game").dest[3] == 1920
    p = _plan("split", order="game_top")
    assert p.element("game").dest[1] == 0 and p.element("cam").dest[1] + p.element("cam").dest[3] == 1920
    assert _plan("basecam").element("game").dest[1] == 0          # Basecam is game on top


def test_the_divider_sets_the_camera_band_within_the_layouts_range():
    assert _plan("split", divider=0.3).element("cam").dest[3] == 576
    assert _plan("split", divider=0.9).element("cam").dest[3] == 960      # capped at half
    assert _plan("split", divider=0.1).element("cam").dest[3] == 480      # at least a quarter
    assert _plan("half", divider=0.3).element("cam").dest[3] == 960       # Half is always half


@pytest.mark.parametrize("order", ["cam_top", "game_top"])
@pytest.mark.parametrize("safe", ["tiktok", "shorts", "none"])
def test_a_whole_game_sits_right_against_the_webcam_with_the_blur_above_and_below(order, safe):
    """Never a band of blur between the streamer and the game: the game row is
    the game's own height, touching the webcam, and what's left over goes
    above the two (just clear of the platform's top bar, so the head isn't
    under it) and below them."""
    for preset, spec in layout.PRESETS.items():
        if spec["type"] != "stack":
            continue
        p = _plan(preset, game_fit="fit", order=order, safe=safe, ui_box=[0.3, 0, 0.4, 0.08],
                  cam2=[0.75, 0.7, 0.25, 0.3])
        rows = sorted({(e.dest[1], e.dest[1] + e.dest[3]) for e in p.elements
                       if e.role != "bg" and e.role not in spec.get("overlay", [])})
        assert all(a[1] == b[0] for a, b in zip(rows, rows[1:])), preset          # no gap anywhere
        top, below = rows[0][0], 1920 - rows[-1][1]
        # Above: up to the top bar's height; the rest below. With less spare
        # than that, all of it goes above.
        assert top == framing.safe_zone(safe)["top"] or (top < framing.safe_zone(safe)["top"] and below <= 6), preset
        g = p.element("game")
        assert abs(g.dest[3] - 1080 * g.src[3] / g.src[2]) <= 2, preset            # the game's own height
        bg = p.elements[0]
        assert bg.role == "bg" and bg.fit == "blur" and bg.dest == (0, 0, 1080, 1920), preset


def test_a_zoomed_game_fills_its_row_with_no_blur_at_all():
    for preset, spec in layout.PRESETS.items():
        if spec["type"] == "stack":
            p = _plan(preset, game_fit="fill", ui_box=[0.3, 0, 0.4, 0.08], cam2=[0.75, 0, 0.25, 0.3])
            assert all(e.role != "bg" for e in p.elements), preset
            assert p.element("game").fit == "cover", preset


def test_the_blurred_layout_is_the_only_one_that_centres_the_game_on_the_short():
    assert _plan("blurred").element("game").anchor == "center"


def test_layouts_fall_back_when_a_box_they_need_is_missing():
    assert _plan("split", cam=None).preset == "blurred"
    assert _plan("fullscreen", cam=None).preset == "fullscreen"
    assert _plan("game_ui").preset == "split"                       # no Game UI box drawn
    assert _plan("mosaic").preset == "split"
    assert _plan("dual_cam").preset == "small_cam"                  # no second webcam
    assert _plan("duo_split").preset == "split"


def test_settings_from_before_layouts_are_half_with_the_game_right_against_the_webcam():
    """Clips made before layouts were Half, the whole game floating in the
    middle of its half. Re-rendered, the game touches the webcam instead."""
    p = layout.plan(W, H, {"cam": [0.0, 0.69, 0.17, 0.31], "cam_position": "bottom"})
    assert p.preset == "half" and p.order == "game_top" and p.element("game").fit == "contain"
    game, cam = p.element("game").dest, p.element("cam").dest
    top = TIKTOK["top"]                                          # blur above, clear of the top bar
    assert game == (0, top, 1080, 730) and cam == (0, top + 730, 1080, 960)


def test_a_zoomed_game_leaves_the_webcam_and_edge_chat_out():
    for preset in ("split", "half", "fullscreen"):
        g = _plan(preset, game_fit="fill").element("game")
        gx, _gy, gw, _gh = g.src
        assert gx >= CAM[2] * W - 2 or preset == "fullscreen", preset       # clear of the webcam
        assert gx + gw <= 1600 + 150, preset                                  # a chat panel at 1600+
    assert _plan("fullscreen", cam=None, game_fit="fill", game_align="left").element("game").src[0] == 0


def test_a_drawn_game_area_leaves_out_the_chat_under_the_game():
    game = [0.17, 0.0, 0.83, 0.83]
    assert _plan("split", game_box=game, game_fit="fit").element("game").src == (326, 0, 1592, 896)
    x, y, w, h = _plan("split", game_box=game, game_fit="fill").element("game").src
    assert y + h <= 0.83 * H + 2 and x >= 0.17 * W - 2 and x + w <= W


ZELDA_CAM = [0.0, 0.6926, 0.1625, 0.3074]
ZELDA_PANELS = [[0.0, 0.5833, 0.1667, 0.1037], [0.1625, 0.8296, 0.8375, 0.1704]]   # splits; black chat bar


@pytest.mark.parametrize("preset", ["split", "half", "basecam", "small_cam", "circle_cam", "fullscreen"])
def test_a_zoomed_game_keeps_the_black_chat_bar_out(preset):
    """Measured on a speedrun stream: a black bar with chat under the game,
    the full width right of the webcam. The zoom stops above it and stays on
    the middle of the game picture rather than sliding off to a clear edge."""
    p = _plan(preset, cam=ZELDA_CAM, panels=ZELDA_PANELS, game_fit="fill")
    x, y, w, h = p.element("game").src
    assert y + h <= 0.8296 * H + 2, preset                     # nothing of the bar
    assert x <= 0.58 * W <= x + w, preset                      # the middle of the game picture
    for box in ZELDA_PANELS:
        bx, by, bw, bh = (round(v * s) for v, s in zip(box, (W, H, W, H)))
        assert x + w <= bx or bx + bw <= x or y + h <= by or by + bh <= y, (preset, box)


def test_a_whole_game_leaves_the_black_chat_bar_out():
    g = _plan("split", cam=ZELDA_CAM, panels=ZELDA_PANELS, game_fit="fit").element("game")
    x, y, w, h = g.src
    assert y + h <= 0.8296 * H + 2 and x >= 0.1667 * W - 2 and x + w == W


def test_without_panels_the_zoom_is_the_same_centred_crop_as_before():
    assert _plan("split", game_fit="fill").element("game").src == (480, 0, 978, 1080)   # slid off the webcam
    assert _plan("fullscreen", cam=None).element("game").src == (656, 0, 606, 1080)


def test_a_webcam_across_the_middle_bottom_gets_a_zoom_above_it():
    x, y, w, h = _plan("split", cam=(0.4, 0.7, 0.2, 0.3), game_fit="fill").element("game").src
    assert y + h <= 0.7 * H + 2 and x <= W / 2 <= x + w


def test_a_small_facecam_sits_inside_the_platforms_safe_zone():
    for preset in ("small_cam", "circle_cam", "dual_cam"):
        for safe in ("tiktok", "reels", "shorts"):
            p = _plan(preset, safe=safe, cam2=[0.75, 0, 0.25, 0.3])
            zone = framing.safe_zone(safe)
            for e in p.elements:
                if e.role.startswith("cam"):
                    assert e.dest[1] >= zone["top"] and e.dest[0] >= zone["left"], (preset, safe)
                    assert e.dest[0] + e.dest[2] <= 1080 - zone["right"] or preset == "dual_cam", (preset, safe)
    assert _plan("circle_cam").element("cam").shape == "circle"


UI = [0.3, 0.0, 0.4, 0.08]


@pytest.mark.parametrize("preset", ["game_ui", "mosaic"])
def test_the_game_ui_is_cut_to_its_space_with_no_blur_round_it(preset):
    e = _plan(preset, ui_box=UI).element("ui")
    assert e.fit == "cover"
    assert abs(e.src[2] / e.src[3] - e.dest[2] / e.dest[3]) < 0.05       # the same shape: nothing to fill


def test_the_game_ui_layer_sits_on_the_game_against_the_webcam_until_it_is_moved():
    for order in ("cam_top", "game_top"):
        p = _plan("game_ui", ui_box=UI, order=order)
        ui, game = p.element("ui").dest, p.element("game").dest
        assert ui[0] == 0 and ui[2] == 1080
        assert ui[1] == game[1] if order == "cam_top" else ui[1] + ui[3] == game[1] + game[3]
        assert p.elements[-1].role == "ui"                                  # drawn over the game
    moved = _plan("game_ui", ui_box=UI, places={"ui": [0.1, 0.8, 0.5, 0.06]}).element("ui")
    assert moved.dest == (108, 1536, 540, 114)
    assert abs(moved.src[2] / moved.src[3] - 540 / 114) < 0.05


@pytest.mark.parametrize("preset,aspect", [("small_cam", 1.6), ("circle_cam", 1.0)])
def test_a_facecam_moved_and_resized_on_the_short_keeps_its_shape(preset, aspect):
    e = _plan(preset, places={"cam": [0.5, 0.6, 0.3, 0.9]}).element("cam")
    x, y, w, h = e.dest
    assert (x, y, w) == (540, 1152, 324) and abs(w / h - aspect) < 0.02
    off = _plan(preset, places={"cam": [0.95, 0.99, 0.5, 0.5]}).element("cam").dest
    assert off[0] + off[2] <= 1080 and off[1] + off[3] <= 1920                 # kept on the Short
    tiny = _plan(preset, places={"cam": [0.1, 0.1, 0.01, 0.01]}).element("cam").dest
    assert tiny[2] >= layout.MIN_PLACE * 1080 - 2


def test_dual_facecam_is_two_circles_always_the_same_size():
    cam2 = [0.75, 0.0, 0.25, 0.3]
    p = _plan("dual_cam", cam2=cam2)
    a, b = p.element("cam"), p.element("cam2")
    assert a.shape == b.shape == "circle" and a.dest[2:] == b.dest[2:] and a.dest[2] == a.dest[3]
    p = _plan("dual_cam", cam2=cam2, places={"cam": [0.1, 0.1, 0.45, 0.3], "cam2": [0.6, 0.1, 0.2, 0.2]})
    assert p.element("cam").dest[2:] == p.element("cam2").dest[2:] == (486, 486)
    d = _plan("duo_split", cam2=cam2)
    assert d.element("cam").dest[2:] == d.element("cam2").dest[2:]


def test_placements_from_the_editor_are_checked():
    from core import modes

    got = modes.clean_gaming({"places": {"cam": [0.1, 0.2, 0.3, 0.3], "ui": [0.0, 0.5, 1.0, 0.1],
                                         "game": [0, 0, 1, 1], "cam2": "nope"}})
    assert got["places"] == {"cam": [0.1, 0.2, 0.3, 0.3], "ui": [0.0, 0.5, 1.0, 0.1]}


# ---- the streamer's face -----------------------------------------------------------------


# Measured on a hero-shooter stream: the webcam's top border at 0.268 of the
# frame height, the streamer's head top at 0.288, head centre 0.361. Rendered
# with the webcam band on top, the head landed 2-26 px from the top of the
# Short, under TikTok's top bar.
HIGH_CAM = (0.0396, 0.270, 0.1417, 0.243)
HIGH_HEAD = (0.11 * W, 0.288 * H, 0.41 * H)


@pytest.mark.parametrize("preset", ["split", "half", "small_cam", "circle_cam"])
def test_a_streamer_high_in_their_webcam_keeps_their_head_below_the_top_bar(preset):
    p = _plan(preset, cam=HIGH_CAM, heads={"cam": HIGH_HEAD})
    e = p.element("cam")
    top, _chin = framing.head_on_canvas(HIGH_HEAD, e.src, e.dest, e.shift)
    assert top >= TIKTOK["top"] - 1
    assert framing.face_clear(HIGH_HEAD, e.src, e.dest, e.shift, TIKTOK)["top"]


def test_the_picture_moves_down_only_when_the_webcam_has_no_room_above_the_head():
    roomy = (0.0, 0.1, 0.3, 0.5)
    head = (0.15 * W, 0.3 * H, 0.45 * H)                  # plenty of webcam above the head
    assert _plan("split", cam=roomy, heads={"cam": head}).element("cam").shift == 0
    assert _plan("split", cam=HIGH_CAM, heads={"cam": HIGH_HEAD}).element("cam").shift > 0


@pytest.mark.parametrize(("where", "head"), [
    ("top", (0.15, 0.13, 0.25)), ("bottom", (0.15, 0.3, 0.42)), ("left", (0.05, 0.2, 0.32)),
    ("right", (0.25, 0.2, 0.32)), ("centre", (0.15, 0.2, 0.32)),
])
def test_the_face_stays_in_its_band_wherever_it_sits_in_the_webcam(where, head):
    cam = (0.0, 0.05, 0.3, 0.4)
    h = (head[0] * W, head[1] * H, head[2] * H)
    for order in ("cam_top", "game_top"):
        e = _plan("split", cam=cam, heads={"cam": h}, order=order).element("cam")
        top, chin = framing.head_on_canvas(h, e.src, e.dest, e.shift)
        x_on_canvas = e.dest[0] + (h[0] - e.src[0]) * e.dest[2] / e.src[2]
        assert e.dest[1] <= top and top < chin, (where, order)
        assert e.dest[0] < x_on_canvas < e.dest[0] + e.dest[2], (where, order)
        if order == "cam_top":
            assert top >= TIKTOK["top"] - 1, where


def test_with_no_head_to_go_on_the_crop_is_centred():
    e = _plan("split", cam=(0.1, 0.1, 0.2, 0.4)).element("cam")
    sx, sy, sw, sh = e.src
    assert abs((sx + sw / 2) - 0.2 * W) <= 2 and abs((sy + sh / 2) - 0.3 * H) <= 2 and e.shift == 0


def test_a_face_under_the_caption_area_is_flagged():
    """A small webcam scaled up into a bottom band can put the chin under
    TikTok's captions; the editor shows it rather than hiding it."""
    e = _plan("basecam", cam=HIGH_CAM, heads={"cam": HIGH_HEAD}).element("cam")
    assert framing.face_clear(HIGH_HEAD, e.src, e.dest, e.shift, TIKTOK)["bottom"] is False


def test_a_face_too_big_for_its_band_keeps_its_chin_rather_than_clearing_the_top_bar():
    """Measured on a speedrun: a tight webcam scaled into Split's band. With the
    head placed under TikTok's top bar the chin was cut by the game; the whole
    face stays in the band and the hair goes under the bar instead."""
    head = (0.0859 * W, 0.7318 * H, 0.9563 * H)
    e = _plan("split", cam=ZELDA_CAM, heads={"cam": head}, game_fit="fill").element("cam")
    top, chin = framing.head_on_canvas(head, e.src, e.dest, e.shift)
    assert chin <= e.dest[1] + e.dest[3]                              # the chin, even if the hair is cut
    assert framing.face_clear(head, e.src, e.dest, e.shift, TIKTOK) == {"top": False, "bottom": True}
    # Half with the whole game: a taller band, starting below the bar, fits both.
    roomy = _plan("half", cam=ZELDA_CAM, heads={"cam": head}, game_fit="fit").element("cam")
    assert framing.face_clear(head, roomy.src, roomy.dest, roomy.shift, TIKTOK) == {"top": True, "bottom": True}


def test_nothing_in_the_layout_or_framing_reads_motion_or_pixels():
    """The old failure was choosing the region that moved most (chat). Layout
    and framing are geometry only."""
    for name in ("layout.py", "framing.py"):
        src = (ROOT / "gaming" / name).read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""|#.*', "", src, flags=re.S)
        for banned in ("np.", "cv2", "diff(", "std(", "activity", "motion", "video_capture"):
            assert banned not in code, (name, banned)


# ---- the render ---------------------------------------------------------------------------


def test_the_graph_places_every_element_at_its_region():
    from gaming import compose

    p = _plan("split", game_fit="fit", heads={"cam": (0.12 * W, 0.02 * H, 0.2 * H)})
    graph = compose.filter_graph(p)
    for e in p.elements:
        assert f"overlay={e.dest[0]}:{e.dest[1]}" in graph
    assert "[l0]" in graph and "gblur" in graph.split("[l0]")[0]     # the blur goes down first
    circle = compose.filter_graph(_plan("circle_cam"))
    assert "geq=" in circle and "hypot(X-W/2,Y-H/2)" in circle
    zoom = compose.filter_graph(_plan("fullscreen", cam=None, game_fit="fill"), vf_extra="eq=saturation=1.1",
                                ass_name="c.ass")
    assert zoom.endswith(";[v]eq=saturation=1.1[v];[v]subtitles=c.ass[v]")


def _ffmpeg_or_skip() -> str:
    from core.binaries import ffmpeg

    binary = ffmpeg()
    try:
        subprocess.run([binary, "-version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("FFmpeg isn't available")
    return binary


@pytest.fixture
def stream(tmp_path):
    """A 1080p "stream": blue game, red webcam top-left, green chat down the right edge."""
    pytest.importorskip("cv2")
    source = tmp_path / "stream.mp4"
    subprocess.run([_ffmpeg_or_skip(), "-v", "error",
                    "-f", "lavfi", "-i", ",".join(["color=c=blue:s=1920x1080:r=30:d=2",
                                                   "drawbox=x=0:y=0:w=480:h=360:color=red:t=fill",
                                                   "drawbox=x=1720:y=0:w=200:h=1080:color=green:t=fill"]),
                    "-f", "lavfi", "-i", "sine=frequency=440:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
                   check=True)
    return source


def _render(stream, tmp_path, p):
    import cv2

    from gaming import compose

    out = compose.render(stream, tmp_path / f"{p.preset}.mp4", p)
    cap = cv2.VideoCapture(str(out))
    cap.set(cv2.CAP_PROP_POS_MSEC, 1000)
    ok, frame = cap.read()
    cap.release()
    assert ok and frame.shape[:2] == (1920, 1080)
    return frame


def _share(region, channel):
    """Share of pixels where one BGR channel clearly dominates."""
    import numpy as np

    others = [c for c in range(3) if c != channel]
    return float(np.mean((region[..., channel] > 150) & (region[..., others].max(axis=-1) < 90)))


def test_a_real_camera_top_split(stream, tmp_path):
    frame = _render(stream, tmp_path, _plan("split", game_fit="fill"))
    cam_h = _plan("split").element("cam").dest[3]
    assert _share(frame[:cam_h], 2) > 0.9                     # the webcam band is the webcam
    game = frame[cam_h:]
    assert _share(game, 0) > 0.9 and _share(game, 1) < 0.01 and _share(game, 2) < 0.01


@pytest.mark.parametrize("preset", ["split", "basecam"])
def test_a_real_whole_game_touches_the_webcam(stream, tmp_path, preset):
    """The game ends where the webcam starts (or the other way round): no band
    of blur between them; the blur is above and below the two."""
    p = _plan(preset, game_fit="fit")
    frame = _render(stream, tmp_path, p)
    cam, game = p.element("cam").dest, p.element("game").dest
    first, second = (cam, game) if cam[1] < game[1] else (game, cam)
    seam = first[1] + first[3]
    assert seam == second[1]
    colour = {id(cam): 2, id(game): 0}
    assert _share(frame[seam - 12:seam - 2, 40:900], colour[id(first)]) > 0.9      # (the green chat is
    assert _share(frame[seam + 2:seam + 12, 40:900], colour[id(second)]) > 0.9     # part of the game)
    assert first[1] == TIKTOK["top"] and float(frame[:100].max(axis=-1).mean()) > 40     # blur above, not black


def test_a_real_circle_facecam_shows_the_game_in_its_corners(stream, tmp_path):
    p = _plan("circle_cam")
    frame = _render(stream, tmp_path, p)
    x, y, w, h = p.element("cam").dest
    assert _share(frame[y + h // 2 - 20:y + h // 2 + 20, x + w // 2 - 20:x + w // 2 + 20], 2) > 0.8   # centre: webcam
    assert _share(frame[y:y + 20, x:x + 20], 0) > 0.8                                                  # corner: game


def test_a_real_blurred_layout_has_no_black_bars(stream, tmp_path):
    frame = _render(stream, tmp_path, _plan("blurred", cam=None))
    assert float(frame[:200].max(axis=-1).mean()) > 40 and float(frame[-200:].max(axis=-1).mean()) > 40


def test_blurred_and_fullscreen_decide_how_the_game_is_shown():
    """They ARE the game whole on a blur and the game zoomed: the Whole / Zoom
    switch (set for another layout) doesn't turn one into the other."""
    assert _plan("blurred", cam=None, game_fit="fill").element("game").fit == "contain"
    assert _plan("fullscreen", cam=None, game_fit="fit").element("game").fit == "cover"
