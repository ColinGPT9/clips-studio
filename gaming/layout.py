"""Where each part of a gaming clip comes from. Pure geometry, no pixels.

Two layouts, both 1080x1920:
- split: the streamer's webcam in one 1080x960 band, the game in the other.
- fill:  no webcam; the game takes the whole screen.

How the game sits in its space (game_fit):
- "fit" (the default): the game shown whole, letterboxed, on a blurred copy
  of itself rather than black bars. With a webcam, "the game" is the biggest
  picture beside the webcam that leaves it out, so the streamer isn't shown
  twice; with none, the whole stream.
- "fill": zoomed to fill the space, a fixed crop of the canvas the streamer
  plays on, centred by default, where chat panels and alerts (which sit at
  the edges) fall outside it. It slides clear of the webcam, or goes where the
  user's Left/Centre/Right choice puts it.
A game area the user drew replaces both: fitted whole, or zoomed into.

The game region is never DETECTED. Earlier attempts looked for the part of
the screen with the most going on, and scrolling chat won every time: it
changes on every frame and the game often doesn't. Nothing here reads motion,
activity or pixel values; the webcam box is only ever excluded.
"""

from dataclasses import dataclass

OUT_W, OUT_H = 1080, 1920
BAND_H = 960                       # equal halves: webcam band, game band
SPLIT_ASPECT = OUT_W / BAND_H      # 1.125: a game band's width / height
FILL_ASPECT = OUT_W / OUT_H        # 0.5625: 9:16

ALIGNS = ("left", "center", "right")
POSITIONS = ("top", "bottom")
FITS = ("fit", "fill")


@dataclass(frozen=True)
class Plan:
    kind: str                      # "split" | "fill"
    game: tuple                    # (x, y, w, h) in source pixels
    cam: tuple | None = None       # (x, y, w, h) in source pixels, split only
    cam_position: str = "top"      # where the webcam band goes, split only
    game_fit: str = "fill"         # "fit": letterboxed on a blur; "fill": game is the crop


def _even(v: float) -> int:
    """An even size of at least 2 (FFmpeg's crop and scale want even sizes)."""
    return max(2, int(v) // 2 * 2)


def _pos(v: float) -> int:
    """An even position, which can be 0: the frame's own edge."""
    return max(0, int(v) // 2 * 2)


def _clamp_box(box_norm: tuple, src_w: int, src_h: int) -> tuple:
    """A normalized (x, y, w, h) box as even source pixels, inside the frame."""
    x, y, w, h = box_norm
    x0 = min(max(0.0, x), 1.0) * src_w
    y0 = min(max(0.0, y), 1.0) * src_h
    x1 = min(max(x + w, 0.0), 1.0) * src_w
    y1 = min(max(y + h, 0.0), 1.0) * src_h
    return (_pos(x0), _pos(y0), _even(max(2.0, x1 - x0)), _even(max(2.0, y1 - y0)))


def _aligned(src_w: int, crop_w: int, align: str) -> int:
    if align == "left":
        return 0
    if align == "right":
        return src_w - crop_w
    return _pos((src_w - crop_w) / 2)


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


def _cover(box: tuple, aspect: float) -> tuple:
    """The largest crop of an (x, y, w, h) pixel box at `aspect`, centred in
    it: everything shown is inside the box the user drew."""
    x, y, w, h = box
    if w / h > aspect:
        cw = _even(h * aspect)
        return (_pos(x + (w - cw) / 2), y, cw, h)
    ch = _even(w / aspect)
    return (x, _pos(y + (h - ch) / 2), w, ch)


def _shown(box: tuple, aspect: float) -> float:
    """The area a box covers when fitted whole into a band of this aspect
    (band height 1): how big the game ends up on screen."""
    w, h = box[2], box[3]
    scale = min(aspect / w, 1.0 / h)
    return w * h * scale * scale


def _beside(src_w: int, src_h: int, cam: tuple, aspect: float) -> tuple:
    """The strip of the frame beside the webcam (left, right, above or below
    it, full length) that shows biggest when fitted into the band. Leaving
    out a KNOWN box, never looking for anything."""
    cx, cy, cw, ch = cam
    strips = [
        (0, 0, cx, src_h),                              # left of the webcam
        (cx + cw, 0, src_w - cx - cw, src_h),           # right of it
        (0, 0, src_w, cy),                              # above it
        (0, cy + ch, src_w, src_h - cy - ch),           # below it
    ]
    strips = [s for s in strips if s[2] >= 0.2 * src_w and s[3] >= 0.2 * src_h]
    if not strips:
        return (0, 0, _even(src_w), _even(src_h))       # a webcam filling the frame: show it all
    x, y, w, h = max(strips, key=lambda s: _shown(s, aspect))
    return (_pos(x), _pos(y), _even(w), _even(h))


def plan(src_w: int, src_h: int, cam_box: tuple | None, *,
         cam_position: str = "top", game_align: str = "center", game_box: tuple | None = None,
         game_fit: str = "fit") -> Plan:
    """The layout for one clip.

    cam_box: the webcam as a normalized (x, y, w, h), from TalkNet or drawn by
    the user, or None for no webcam. game_fit: "fit" shows the game whole on
    a blurred copy of itself, "fill" zooms it to fill. game_align ("fill"
    only): "center" slides clear of the webcam; "left" / "right" is the
    user's own choice and is kept as given. game_box: the game (or the video
    being reacted to) drawn by the user, as a normalized (x, y, w, h): it
    replaces the automatic region, so chat under the game is left out by
    drawing above it.
    """
    if cam_position not in POSITIONS:
        cam_position = "top"
    if game_align not in ALIGNS:
        game_align = "center"
    if game_fit not in FITS:
        game_fit = "fit"
    aspect = FILL_ASPECT if cam_box is None else SPLIT_ASPECT
    cam = _clamp_box(cam_box, src_w, src_h) if cam_box is not None else None

    if game_box is not None:
        region = _clamp_box(game_box, src_w, src_h)
        game = region if game_fit == "fit" else _cover(region, aspect)
    elif game_fit == "fit":
        game = (0, 0, _even(src_w), _even(src_h)) if cam is None else _beside(src_w, src_h, cam, aspect)
    elif cam is None:
        crop_w = min(src_w, _even(src_h * aspect))
        game = (_aligned(src_w, crop_w, game_align), 0, crop_w, _even(src_h))
    else:
        crop_w = min(src_w, _even(src_h * aspect))
        x = _clear_of(src_w, crop_w, cam) if game_align == "center" else _aligned(src_w, crop_w, game_align)
        game = (_pos(x), 0, crop_w, _even(src_h))

    if cam is None:
        return Plan("fill", game, game_fit=game_fit)
    return Plan("split", game, cam, cam_position, game_fit)
