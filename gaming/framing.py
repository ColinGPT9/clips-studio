"""Where the streamer's face goes: camera crops placed from the face, clear
of the platforms' own UI.

A camera region is filled by a crop of the webcam cut at the region's shape
(never stretched). Centring that crop on the webcam box cut heads off:
measured on a hero-shooter stream, the top of the streamer's head landed 2 to
26 px from the top of the Short, under TikTok's "Following | For You" bar,
because the webcam box started at the top of the head and the crop was centred.

So the crop is placed from the head instead: centred on it across, and down
so the top of the head lands clear of the region's top edge (HEADROOM) and,
for a region at the top of the Short, clear of the platform's top bar too.
Some streamers sit high in their webcam, with no picture above their head to
show (the same streamer: 22 px). Then the camera picture is moved down within
its region by exactly the shortfall, and a blur of it fills the gap: only when
needed, and only as much as needed. Nothing is decided by size or motion; the
head is the streamer's, already chosen by TalkNet or drawn by the user.

The safe zones are the published design defaults for 1080x1920 (layouts.json);
they vary by device and caption length, so the editor shows them and lets the
platform be chosen.
"""

import json
from pathlib import Path

LAYOUTS = json.loads(Path(__file__).with_name("layouts.json").read_text(encoding="utf-8"))
CANVAS_W, CANVAS_H = LAYOUTS["canvas"]
SAFE_ZONES = LAYOUTS["safe_zones"]
HEADROOM = LAYOUTS["headroom"]
MAX_SHIFT = 0.3          # never move a camera picture down by more of its region than this


def safe_zone(name: str | None) -> dict:
    return SAFE_ZONES.get(name or "tiktok", SAFE_ZONES["tiktok"])


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def _pos(v: float) -> int:
    return max(0, int(v) // 2 * 2)


def cam_crop(box: tuple, head: tuple | None, dest: tuple, safe: dict) -> tuple[tuple, int]:
    """(source crop, shift) for a camera region.

    box:  the webcam, source px (x, y, w, h).
    head: the streamer's head, source px (centre x, top, chin), or None.
    dest: the region on the 1080x1920 canvas (x, y, w, h).
    safe: a SAFE_ZONES entry.

    The crop has dest's shape and is the largest that fits in the webcam box.
    shift: how far the picture moves down within dest (px) because the webcam
    has too little picture above the head; the caller fills the gap with a
    blur of it.
    """
    bx, by, bw, bh = box
    aspect = dest[2] / dest[3]
    if bw / bh > aspect:
        ch = bh
        cw = ch * aspect
    else:
        cw = bw
        ch = cw / aspect
    if head is None:
        x, y = bx + (bw - cw) / 2, by + (bh - ch) / 2
        return (_pos(x), _pos(y), _even(cw), _even(ch)), 0

    hx, top, chin = head
    scale = dest[3] / ch
    dy, dh = dest[1], dest[3]
    want_top = dy + HEADROOM * dh
    if dy < safe["top"]:
        want_top = max(want_top, safe["top"])      # a region at the top of the Short
    # The chin comes first: when forehead to chin won't fit below the top bar
    # (a tight webcam in a short band), the hair goes under the bar, and when
    # it won't fit in the band at all, the top of the head is cut, never the
    # mouth and chin.
    want_top = min(want_top, dy + dh - (chin - top + 2) * scale)     # + the crop's even rounding
    x = _clamp(hx - cw / 2, bx, bx + bw - cw)
    y = _clamp(top - (want_top - dy) / scale, by, by + bh - ch)
    lands = dy + (top - y) * scale                  # where the head top ends up
    shift = int(round(min(max(0.0, want_top - lands), MAX_SHIFT * dh)))
    return (_pos(x), _pos(y), _even(cw), _even(ch)), shift


def head_on_canvas(head: tuple, crop: tuple, dest: tuple, shift: int) -> tuple[float, float]:
    """(top, chin) of the head on the canvas for a camera element, for checks
    and the editor's face badge."""
    _hx, top, chin = head
    scale = dest[3] / crop[3]
    return dest[1] + shift + (top - crop[1]) * scale, dest[1] + shift + (chin - crop[1]) * scale


def face_clear(head: tuple | None, crop: tuple, dest: tuple, shift: int, safe: dict) -> dict:
    """Whether the head is on screen and clear of the platform's top bar and
    bottom caption area: {"top": bool, "bottom": bool}."""
    if head is None:
        return {"top": True, "bottom": True}
    top, chin = head_on_canvas(head, crop, dest, shift)
    return {"top": top >= max(dest[1], safe["top"]) - 1,
            "bottom": chin <= min(dest[1] + dest[3], CANVAS_H - safe["bottom"]) + 1}
