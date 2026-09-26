"""Where each part of a gaming clip comes from. Pure geometry, no pixels.

Two layouts, both 1080x1920:
- split: the streamer's webcam in one 1080x960 band, the game in the other.
- fill:  no webcam; the game fills the screen as a 9:16 crop.

The game region is never DETECTED. Earlier attempts looked for the part of
the screen with the most going on, and scrolling chat won every time: it
changes on every frame and the game often doesn't. So the game band is a
fixed crop of the canvas the streamer plays on, centred by default, where
chat panels and alerts (which sit at the edges) fall outside it. The only
thing that moves it is the webcam box, which it slides clear of so the
streamer isn't shown twice, and the user's own Left/Centre/Right choice.
Nothing here reads motion, activity or pixel values.
"""

from dataclasses import dataclass

OUT_W, OUT_H = 1080, 1920
BAND_H = 960                       # equal halves: webcam band, game band
SPLIT_ASPECT = OUT_W / BAND_H      # 1.125: a game band's width / height
FILL_ASPECT = OUT_W / OUT_H        # 0.5625: 9:16

ALIGNS = ("left", "center", "right")
POSITIONS = ("top", "bottom")


@dataclass(frozen=True)
class Plan:
    kind: str                      # "split" | "fill"
    game: tuple                    # (x, y, w, h) in source pixels
    cam: tuple | None = None       # (x, y, w, h) in source pixels, split only
    cam_position: str = "top"      # where the webcam band goes, split only


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def _clamp_box(box_norm: tuple, src_w: int, src_h: int) -> tuple:
    """A normalized (x, y, w, h) box as even source pixels, inside the frame."""
    x, y, w, h = box_norm
    x0 = min(max(0.0, x), 1.0) * src_w
    y0 = min(max(0.0, y), 1.0) * src_h
    x1 = min(max(x + w, 0.0), 1.0) * src_w
    y1 = min(max(y + h, 0.0), 1.0) * src_h
    return (_even(x0), _even(y0), _even(max(2.0, x1 - x0)), _even(max(2.0, y1 - y0)))


def _aligned(src_w: int, crop_w: int, align: str) -> int:
    if align == "left":
        return 0
    if align == "right":
        return src_w - crop_w
    return _even((src_w - crop_w) / 2)


def _clear_of(src_w: int, crop_w: int, cam: tuple) -> int:
    """The x for a full-height crop that overlaps the webcam box least, nearest
    the centre among equals. Exclusion of a KNOWN box, not a search for
    anything interesting."""
    cx0, cx1 = cam[0], cam[0] + cam[2]
    centre = (src_w - crop_w) / 2
    best, best_key = 0, None
    for x in range(0, src_w - crop_w + 1, 2):
        overlap = max(0, min(x + crop_w, cx1) - max(x, cx0))
        key = (overlap, abs(x - centre))
        if best_key is None or key < best_key:
            best, best_key = x, key
    return best


def plan(src_w: int, src_h: int, cam_box: tuple | None, *,
         cam_position: str = "top", game_align: str = "center") -> Plan:
    """The layout for one clip.

    cam_box: the webcam as a normalized (x, y, w, h), from TalkNet or drawn by
    the user, or None for no webcam. game_align: "center" slides clear of the
    webcam; "left" / "right" is the user's own choice and is kept as given.
    """
    if cam_position not in POSITIONS:
        cam_position = "top"
    if game_align not in ALIGNS:
        game_align = "center"

    if cam_box is None:
        crop_w = min(src_w, _even(src_h * FILL_ASPECT))
        return Plan("fill", (_aligned(src_w, crop_w, game_align), 0, crop_w, _even(src_h)))

    cam = _clamp_box(cam_box, src_w, src_h)
    crop_w = min(src_w, _even(src_h * SPLIT_ASPECT))
    x = _clear_of(src_w, crop_w, cam) if game_align == "center" else _aligned(src_w, crop_w, game_align)
    return Plan("split", (_even(x), 0, crop_w, _even(src_h)), cam, cam_position)
