"""Where each part of a gaming clip comes from and goes. Pure geometry, no pixels.

A layout (a preset in layouts.json) places ELEMENTS on the 1080x1920 canvas:
the streamer's webcam ("cam", and "cam2" for a duo), the game ("game") and a
piece of the game's own UI ("ui": a scoreboard, a kill feed, a speedrun
timer). Each element goes through the same steps:

    canvas -> the region it is assigned -> the source's shape ->
    crop / scale / letterbox -> position

- cover:   the source is cropped to the region's shape and fills it; never
           stretched.
- contain: the whole source box is shown, as big as fits, ANCHORED to the
           region's outer edge (a game on top starts at the top of the Short,
           one at the bottom ends at its bottom), and a blur of it fills the
           rest of that region only. Only the Blurred layout, which is the
           game alone, sits in the middle.

Preset types:
- stack: rows from top to bottom (webcam band, game band, maybe a UI strip),
         the webcam band's height set by the divider; "order" puts the game
         on top instead.
- full:  the game alone (Fullscreen: zoomed to fill; Blurred: whole on a blur).
- pip:   the game fills the Short and the webcam sits over it near the top,
         inside the platforms' safe zone (Small facecam, Circle facecam, Dual).

The game region is never DETECTED: the old attempts looked for the part of
the screen with the most going on, and scrolling chat won every time. It is
the user's drawn box, or a fixed crop that only moves to leave out a known
webcam. Nothing here reads motion, activity or pixel values.
"""

from dataclasses import dataclass, field

from gaming import framing

LAYOUTS = framing.LAYOUTS
PRESETS: dict = LAYOUTS["presets"]
OUT_W, OUT_H = LAYOUTS["canvas"]
ALIGNS = ("left", "center", "right")
ORDERS = ("cam_top", "game_top")
FITS = ("fit", "fill")
PIP_GAP = 24               # px between two picture-in-picture webcams
PIP_MARGIN = 0.02          # of the canvas height, below the safe zone's top
PLACEABLE = ("cam", "cam2", "ui")     # layers moved and resized on the Short itself
MIN_PLACE = 0.12           # a placed layer is at least this much of the canvas width


@dataclass(frozen=True)
class Element:
    role: str                      # cam | cam2 | game | ui | bg
    src: tuple                     # source crop (x, y, w, h) px
    dest: tuple                    # its region on the canvas (x, y, w, h) px
    fit: str = "cover"             # cover | contain | blur (a wash of its colours)
    anchor: str = "center"         # contain: top | bottom | center
    shift: int = 0                 # cover: picture moved down this far (face clear of the UI)
    shape: str = "rect"            # rect | circle


@dataclass(frozen=True)
class Plan:
    preset: str
    elements: tuple = field(default_factory=tuple)
    order: str = "cam_top"
    safe: str = "tiktok"

    def element(self, role: str) -> Element | None:
        return next((e for e in self.elements if e.role == role), None)

    @property
    def kind(self) -> str:
        """"split" when a webcam is shown, "fill" for the game alone."""
        return "split" if self.element("cam") else "fill"


def _even(v: float) -> int:
    """An even size of at least 2 (FFmpeg's crop and scale want even sizes)."""
    return max(2, int(v) // 2 * 2)


def _pos(v: float) -> int:
    """An even position, which can be 0: the frame's own edge."""
    return max(0, int(v) // 2 * 2)


def _clamp_box(box_norm, src_w: int, src_h: int) -> tuple:
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


def _clear_of(src_w: int, crop_w: int, cams: list) -> int:
    """The x for a full-height crop that overlaps the webcam boxes least,
    nearest the centre among equals. Exclusion of KNOWN boxes, not a search
    for anything interesting."""
    centre = (src_w - crop_w) / 2
    best, best_key = 0, None
    for x in range(0, src_w - crop_w + 1, 2):
        overlap = sum(max(0, min(x + crop_w, c[0] + c[2]) - max(x, c[0])) for c in cams)
        key = (overlap, abs(x - centre))
        if best_key is None or key < best_key:
            best, best_key = x, key
    return best


def _cover(box: tuple, aspect: float) -> tuple:
    """The largest crop of an (x, y, w, h) pixel box at `aspect`, centred in it."""
    x, y, w, h = box
    if w / h > aspect:
        cw = _even(h * aspect)
        return (_pos(x + (w - cw) / 2), y, cw, h)
    ch = _even(w / aspect)
    return (x, _pos(y + (h - ch) / 2), w, ch)


def _shown(box: tuple, aspect: float) -> float:
    """The area a box covers when fitted whole into a region of this aspect
    (region height 1): how big the game ends up on screen."""
    w, h = box[2], box[3]
    scale = min(aspect / w, 1.0 / h)
    return w * h * scale * scale


def _clear(box: tuple, obstacles: list) -> bool:
    """Whether a box overlaps none of the obstacles."""
    x, y, w, h = box
    return all(x + w <= o[0] or o[0] + o[2] <= x or y + h <= o[1] or o[1] + o[3] <= y for o in obstacles)


def _open_areas(src_w: int, src_h: int, obstacles: list) -> list:
    """Every rectangle of the frame that overlaps no obstacle and can't grow:
    each of its sides is the frame's edge or runs along an obstacle."""
    lefts = sorted({0} | {o[0] + o[2] for o in obstacles if o[0] + o[2] < src_w})
    rights = sorted({src_w} | {o[0] for o in obstacles if o[0] > 0})
    tops = sorted({0} | {o[1] + o[3] for o in obstacles if o[1] + o[3] < src_h})
    bottoms = sorted({src_h} | {o[1] for o in obstacles if o[1] > 0})
    areas = []
    for x0 in lefts:
        for x1 in rights:
            if x1 <= x0:
                continue
            for y0 in tops:
                for y1 in bottoms:
                    if y1 <= y0:
                        continue
                    a = (x0, y0, x1 - x0, y1 - y0)
                    if not _clear(a, obstacles):
                        continue

                    def across(o, a=a):
                        return o[1] < a[1] + a[3] and o[1] + o[3] > a[1]

                    def along(o, a=a):
                        return o[0] < a[0] + a[2] and o[0] + o[2] > a[0]

                    if ((x0 == 0 or any(o[0] + o[2] == x0 and across(o) for o in obstacles))
                            and (x1 == src_w or any(o[0] == x1 and across(o) for o in obstacles))
                            and (y0 == 0 or any(o[1] + o[3] == y0 and along(o) for o in obstacles))
                            and (y1 == src_h or any(o[1] == y1 and along(o) for o in obstacles))):
                        areas.append(a)
    return areas


def _whole(src_w: int, src_h: int, obstacles: list, aspect: float) -> tuple:
    """The game shown whole: the open area beside the webcams and the stream's
    solid panels (a chat bar, a splits timer) that shows biggest when fitted
    into the region, so the streamer isn't shown twice and a black chat bar
    isn't shown at all."""
    areas = [a for a in _open_areas(src_w, src_h, obstacles) if a[2] >= 0.2 * src_w and a[3] >= 0.2 * src_h]
    if not areas:
        return (0, 0, _even(src_w), _even(src_h))
    x, y, w, h = max(areas, key=lambda a: _shown(a, aspect))
    return (_pos(x), _pos(y), _even(w), _even(h))


def _picture(src_w: int, src_h: int, panels: list) -> tuple:
    """The game picture: the biggest open area beside the solid panels. A
    webcam sits on top of the game, so it doesn't count."""
    areas = _open_areas(src_w, src_h, panels)
    return max(areas, key=lambda a: a[2] * a[3]) if areas else (0, 0, src_w, src_h)


def _spots(t: float, size: int, limit: int, obstacles: list, axis: int, align: str) -> list:
    """Where a crop `size` long could start along one axis: lined up on the
    target, flush with each obstacle's sides, or at the frame's edges."""
    near = t - size / 2 if align == "center" else t if align == "left" else t - size
    raw = [near, 0, limit - size]
    for o in obstacles:
        raw += [o[axis] + o[axis + 2], o[axis] - size]
    return sorted({_pos(min(max(v, 0), limit - size)) for v in raw})


def _zoom(src_w: int, src_h: int, aspect: float, align: str, cams: list, panels: list) -> tuple | None:
    """A crop at `aspect` for a game that fills its region: as tall as it can
    be while it keeps clear of the webcams and the solid panels and still
    holds the middle of the game picture (its left or right edge for Left /
    Right), then nearest lined up on it. None when nothing at least half the
    frame's height manages that."""
    obstacles = cams + panels
    gx, gy, gw, gh = _picture(src_w, src_h, panels)
    tx = gx if align == "left" else gx + gw if align == "right" else gx + gw / 2
    ty = gy + gh / 2
    full_h = src_h if src_h * aspect <= src_w else src_w / aspect
    tops = [0] + [o[1] + o[3] for o in obstacles]
    bottoms = [src_h] + [o[1] for o in obstacles]
    lefts = [0] + [o[0] + o[2] for o in obstacles]
    rights = [src_w] + [o[0] for o in obstacles]
    heights = {full_h}
    heights |= {b - t for t in tops for b in bottoms if b > t}
    heights |= {(r - left) / aspect for left in lefts for r in rights if r > left}
    for ch in sorted({_even(h) for h in heights if full_h / 2 <= h <= full_h}, reverse=True):
        cw = _even(ch * aspect)
        best = None
        for x in _spots(tx, cw, src_w, obstacles, 0, align):
            for y in _spots(ty, ch, src_h, obstacles, 1, "center"):
                crop = (x, y, cw, ch)
                if not (x <= tx <= x + cw and y <= ty <= y + ch) or not _clear(crop, obstacles):
                    continue
                lined = x if align == "left" else x + cw if align == "right" else x + cw / 2
                key = (abs(lined - tx) + abs(y + ch / 2 - ty), x, y)
                if best is None or key < best[0]:
                    best = (key, crop)
        if best is not None:
            return best[1]
    return None


def resolve(settings: dict) -> dict:
    """A clip's gaming settings with a preset that can actually be drawn.

    Settings from before layouts existed (cam_position / game_fit, no preset)
    become Half, which is what they rendered as. A layout that needs a box it
    doesn't have falls back: no webcam -> the game alone (Blurred, or
    Fullscreen if it was zoomed), no Game UI box -> Split, no second webcam ->
    the one-webcam version."""
    s = dict(settings or {})
    preset = s.get("preset")
    if preset not in PRESETS:
        preset = "half"
        s.setdefault("order", "game_top" if s.get("cam_position") == "bottom" else "cam_top")
        s.setdefault("game_fit", s.get("game_fit") or "fit")
    spec = PRESETS[preset]
    roles = _roles(spec)
    if "cam" in roles and not s.get("cam"):
        # No webcam: the game alone, whole on a blur. (Fullscreen is its own
        # choice; a zoomed 9:16 cut of a wide game loses most of it.)
        preset = "blurred"
    elif "ui" in roles and not s.get("ui_box"):
        preset = "split"
    elif "cam2" in roles and not s.get("cam2"):
        preset = {"dual_cam": "small_cam", "duo_split": "split"}.get(preset, preset)
    s["preset"] = preset
    return s


def _roles(spec: dict) -> set:
    """Every part a preset shows: its rows, picture-in-picture webcams and overlays."""
    return ({r for row in spec.get("rows", []) for r in row} | set(spec.get("cams", []))
            | set(spec.get("overlay", [])))


def _placed(box, aspect: float | None = None) -> tuple:
    """A layer placed on the Short by the user (normalized x, y, w, h of the
    canvas) as even canvas px, kept on the canvas. With `aspect` (a webcam's
    shape: round, or the small facecam's), the height follows the width."""
    x, y, w, h = (float(v) for v in box)
    w = min(max(w, MIN_PLACE), 1.0) * OUT_W
    if aspect:
        h = w / aspect
        if h > OUT_H:
            h, w = OUT_H, OUT_H * aspect
    else:
        h = min(max(h, 0.02), 1.0) * OUT_H
    x = min(max(x * OUT_W, 0.0), OUT_W - w)
    y = min(max(y * OUT_H, 0.0), OUT_H - h)
    return (_pos(x), _pos(y), _even(w), _even(h))


def _stack_regions(spec: dict, order: str, divider: float, game_h: float | None = None,
                   top: float = 0) -> list:
    """[(role, dest, anchor)] for a stack preset, top to bottom.

    game_h: the game's own height when it is shown whole. Its row is then
    exactly that tall and right against the webcam, never with a band of blur
    between them. The space left over goes above the rows (up to `top`, the
    platform's top bar, so the streamer's head clears it) and below them."""
    rows = [list(r) for r in spec["rows"]]
    if order == "game_top":
        rows.reverse()
    lo, hi, default = spec["divider"]
    cam_h = OUT_H * min(max(divider if divider is not None else default, lo), hi)
    heights = []
    for row in rows:
        if "game" in row:
            heights.append(None)
        elif "ui" in row and len(row) == 1:
            heights.append(OUT_H * spec.get("ui_share", 0.12))
        else:
            heights.append(cam_h)
    rest = OUT_H - sum(h for h in heights if h is not None)
    packed = game_h is not None and game_h < rest
    heights = [(game_h if packed else rest) if h is None else h for h in heights]
    # Row edges rounded once, so rows meet exactly; filling, the last one ends
    # at the bottom of the canvas.
    edges = [_pos(min(rest - game_h, top)) if packed else 0]
    for h in heights[:-1]:
        edges.append(_pos(edges[-1] + h))
    edges.append(_pos(edges[-1] + heights[-1]) if packed else OUT_H)
    out = []
    for i, row in enumerate(rows):
        anchor = "top" if i == 0 else "bottom" if i == len(rows) - 1 else "center"
        cols = [_pos(j * OUT_W / len(row)) for j in range(len(row))] + [OUT_W]
        for j, role in enumerate(row):
            out.append((role, (cols[j], edges[i], cols[j + 1] - cols[j], edges[i + 1] - edges[i]), anchor))
    return out


def _pip_regions(spec: dict, safe: dict) -> list:
    cams = spec["cams"]
    w = _even(OUT_W * spec["pip_width"])
    h = _even(w / spec["pip_aspect"])
    y = _pos(safe["top"] + PIP_MARGIN * OUT_H)
    total = len(cams) * w + (len(cams) - 1) * PIP_GAP
    x0 = (OUT_W - total) / 2
    return [(role, (_pos(x0 + i * (w + PIP_GAP)), y, w, h)) for i, role in enumerate(cams)]


def _game_src(src_w: int, src_h: int, s: dict, aspect: float, fit: str, cams: list, panels: list) -> tuple:
    """The part of the frame the game element shows, for a region of `aspect`."""
    if s.get("game_box"):
        region = _clamp_box(s["game_box"], src_w, src_h)
        return region if fit == "fit" else _cover(region, aspect)
    if fit == "fit":
        return _whole(src_w, src_h, cams + panels, aspect)
    align = s.get("game_align") if s.get("game_align") in ALIGNS else "center"
    zoomed = _zoom(src_w, src_h, aspect, align, cams, panels)
    if zoomed is not None:
        return zoomed
    crop_w = min(src_w, _even(src_h * aspect))
    x = _clear_of(src_w, crop_w, cams) if (cams and align == "center") else _aligned(src_w, crop_w, align)
    crop_h = min(src_h, _even(crop_w / aspect)) if crop_w == src_w else _even(src_h)
    return (_pos(x), _pos((src_h - crop_h) / 2), crop_w, crop_h)


def plan(src_w: int, src_h: int, settings: dict, heads: dict | None = None) -> Plan:
    """The layout for one clip.

    settings: a clip's gaming settings (gaming/run.py lists the keys):
      preset, order, divider, safe, game_fit, game_align, the normalized
      boxes cam, cam2, game_box, ui_box and panels (the stream's solid panels,
      gaming/panels.py), and places: where the user put the facecams and the
      Game UI on the Short.
    heads: {"cam": (centre x, top, chin), ...} in source px, the streamer's
      head inside each webcam, for face-safe framing (gaming/framing.py).
    """
    s = resolve(settings)
    preset = s["preset"]
    spec = PRESETS[preset]
    order = s.get("order") if s.get("order") in ORDERS else spec.get("order", "cam_top")
    safe_name = s.get("safe") if s.get("safe") in framing.SAFE_ZONES else "tiktok"
    safe = framing.safe_zone(safe_name)
    # Blurred and Fullscreen ARE the game shown whole or zoomed: the layout
    # decides, not the Whole / Zoom switch.
    fit = spec["game_fit"] if spec["type"] == "full" else (
        s.get("game_fit") if s.get("game_fit") in FITS else spec.get("game_fit", "fill"))
    heads = heads or {}
    cam_boxes = {role: _clamp_box(s[role], src_w, src_h) for role in ("cam", "cam2") if s.get(role)}
    # The game keeps clear of the webcams this layout shows (a second webcam
    # drawn for another layout is just part of the picture here).
    uses = _roles(spec)
    places = s.get("places") if isinstance(s.get("places"), dict) else {}
    shown_cams = [cam_boxes[r] for r in cam_boxes if r == "cam" or r in uses]
    panels = [_clamp_box(b, src_w, src_h) for b in (s.get("panels") or [])]

    elements = []

    def camera(role: str, dest: tuple, shape: str = "rect") -> None:
        crop, shift = framing.cam_crop(cam_boxes[role], heads.get(role), dest, safe)
        elements.append(Element(role, crop, dest, "cover", "center", shift, shape))

    def game(dest: tuple, anchor: str, src: tuple | None = None) -> None:
        if src is None:
            src = _game_src(src_w, src_h, s, dest[2] / dest[3], fit, shown_cams, panels)
        elements.append(Element("game", src, dest, "contain" if fit == "fit" else "cover", anchor))

    if spec["type"] == "full":
        game((0, 0, OUT_W, OUT_H), "center")
    elif spec["type"] == "pip":
        game((0, 0, OUT_W, OUT_H), "center")
        size = None
        for role, dest in _pip_regions(spec, safe):
            if places.get(role):
                dest = _placed(places[role], spec["pip_aspect"])
            if size is None:
                size = dest[2:]
            elif dest[2:] != size:
                # Two webcams are always the same size: the first one's.
                dest = (min(dest[0], OUT_W - size[0]), min(dest[1], OUT_H - size[1]), *size)
            camera(role, dest, spec.get("shape", "rect"))
    else:
        regions = _stack_regions(spec, order, s.get("divider"))
        whole = None
        if fit == "fit":
            # The whole game right against the webcam: its row is the game's
            # own height, and what's left is blur above and below the two.
            space = next(dest for role, dest, _a in regions if role == "game")
            whole = _game_src(src_w, src_h, s, space[2] / space[3], fit, shown_cams, panels)
            regions = _stack_regions(spec, order, s.get("divider"), OUT_W * whole[3] / whole[2], safe["top"])
            if regions[0][1][1] > 0 or regions[-1][1][1] + regions[-1][1][3] < OUT_H:
                elements.append(Element("bg", whole, (0, 0, OUT_W, OUT_H), "blur"))
        for role, dest, anchor in regions:
            if role == "game":
                game(dest, anchor, whole)
            elif role == "ui":
                # Cut to its space's shape, like every other part: no blur round it.
                ui = _clamp_box(s["ui_box"], src_w, src_h)
                elements.append(Element("ui", _cover(ui, dest[2] / dest[3]), dest, "cover"))
            else:
                camera(role, dest)
        if "ui" in spec.get("overlay", []):
            # The Game UI layer: over the game, against the webcam, until it's
            # moved or resized on the Short.
            gx, gy, gw, gh = next(e.dest for e in elements if e.role == "game")
            h = _even(OUT_H * spec.get("ui_share", 0.12))
            dest = (0, _pos(gy if order == "cam_top" else gy + gh - h), OUT_W, h)
            if places.get("ui"):
                dest = _placed(places["ui"])
            ui = _clamp_box(s["ui_box"], src_w, src_h)
            elements.append(Element("ui", _cover(ui, dest[2] / dest[3]), dest, "cover"))
    return Plan(preset, tuple(elements), order, safe_name)
