"""The branded end card appended to every clip.

Clip ends -> Clippy pounces on a ball of yarn -> "Clips Kitty" lands ->
"Made with" / "Free & Open Source" -> hard cut. 2.9 seconds.

This is organic marketing: a clip in someone's feed is the only distribution
the app gets for free, so a viewer should be able to see what made it. ON by
default, because a feature nobody enables markets nothing.

Everything about the animation lives in this one file, so it can be redrawn
later without the pipeline knowing. The pipeline's entire interface is
`append(clip, config)`.

Three things are worth knowing before changing anything here:

**Clippy is not redrawn.** `video/mascot_art.py` IS the mascot — the same
geometry that writes docs/brand/mascot.png. This module calls those functions
into separate layers and poses the layers, so the ears, whiskers, belly and
tail are the real ones. `verify()` composes the rest pose and diffs it against
the shipped PNG; it must stay at zero.

**The clip is never re-encoded.** The outro is generated to match the clip's
exact width, height, fps, pixel format and audio parameters, then appended
with the concat demuxer and `-c copy`. That match is what makes the append
free and lossless — and it is why the cache is keyed on all of it.

**It is built once per format, not once per video.** Prebuilt cards ship
for every format the pipeline actually emits -- both canvases at 30 and
60fps -- so in practice nothing is ever rendered on a user's machine.
Anything unusual renders once, in about 20 seconds, and is then cached.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

from core.paths import discard, resolve_data_dir
from video import mascot_art as art

# --------------------------------------------------------------------------
# The design, in reference coordinates. Everything scales uniformly from here,
# so a landscape or square clip gets the same composition on a wider field
# rather than a re-choreographed one.
# --------------------------------------------------------------------------
REF_W, REF_H = 1080, 1920
DURATION = 2.9                 # seconds

# Warm apricot. Sky blue sits between orange-500 and the light end in
# luminance, so nudging the ground one step lighter made contrast with Clippy
# WORSE (1.06 against 1.31); this clears him at 1.39 and still reads orange.
ORANGE = (252, 176, 104)

# The app's own lockup, from ui/src/renderer/src/App.tsx, which renders
# `Clips <span className="text-accent">Kitty</span>`. Both colours are
# near-invisible on orange alone (1.8:1 and 1.2:1), so rather than substitute
# darker ones -- which stops it being the logo -- it keeps its real colours
# and takes a navy outline. Every shape in the mascot is already a flat fill
# inside navy, so an outlined wordmark is the same construction.
CLIPS = art.CREAM              # --color-ink
KITTY = art.SKY                # --color-accent
STROKE = art.NAVY_DEEP         # --color-base
INK_SOFT = art.NAVY            # supporting lines

KICKER, WORDMARK, TAGLINE = "Made with", "Clips Kitty", "Free & Open Source"
KICK_PT, WORD_PT, TAG_PT, STROKE_PT = 68, 142, 68, 9
GAP_KICK, GAP_TAG, GAP_CAT = 20, 26, 58

# TikTok's published safe zones for a 1080x1920 creative, and the strictest of
# the platforms these get posted to. Nothing in the frame may enter them.
TOP_SAFE = 150                 # status bar; TikTok's own line is 130
BOTTOM_SAFE = REF_H - 483      # 1437: caption, username
RIGHT_RAIL = REF_W - 140       # 940: like / comment / share column

CAT_FRAC = 0.80                # of REF_W

# Landscape only. He is sized from the frame's HEIGHT (a 16:9 frame has height
# to spare and width to fill), and the frame is split into two columns.
LAND_CAT_FRAC = 0.66           # of frame height -> his ink is ~60% of it
LAND_CAT_CX = 0.26             # his mass centre, as a fraction of width
LAND_TEXT_CX = 0.71            # the type block's centre
SWAT_UP, SWAT_DOWN = 95.0, -35.0
TEXT_IN = 1.10                 # the wordmark starts resolving here

# Two locks, deliberately. `_art_lock` guards mascot_art.SS, which is a
# process-wide global that _layer() flips while drawing a part. `_build_lock`
# serialises rendering a cache entry so two clips never render the same card
# at once. They must be SEPARATE and the art one must be reentrant: a single
# non-reentrant lock deadlocked, because ensure_outro held it across _render,
# which reaches _parts() and tries to take it again.
_art_lock = threading.RLock()
_build_lock = threading.Lock()


# ==========================================================================
# Clippy, posed from the real artwork
# ==========================================================================
_parts_cache: dict[int, dict] = {}


def _layer(draw_fn, size, ss=3):
    """One part on its own transparent canvas, resolved to the output size."""
    old = art.SS
    art.SS = ss
    try:
        img = art.canvas()
        draw_fn(ImageDraw.Draw(img))
        return img.resize((size, size), Image.LANCZOS)
    finally:
        art.SS = old


def _head_back(d):
    """Ears, skull, face patch and the eye WHITES: everything the pupils sit
    on top of, in the artwork's own order."""
    X, Y, R = art.head_transform()
    art.draw_head_back(d, X, Y, R)
    art.draw_eyes(d, X, Y, R, whites=True, pupils=False)


def _head_front(d):
    """Blush, nose, mouth and whiskers: everything drawn OVER the eyes.

    Held in its own layer so the pupils can be sandwiched between the two and
    still move. Compositing the eyes last instead put the whites on top of the
    blush -- 529 pixels of drift from the shipped artwork, which verify()
    caught.
    """
    art.draw_head_front(d, *art.head_transform())


def _pupils(d):
    art.draw_eyes(d, whites=False, pupils=True)


def _lids(d):
    """Shut eyes: face-patch colour over the whites, plus a lash line."""
    for sx in (-1, 1):
        ex = 512 + sx * 104
        art.ell(d, ex, 424, 80, 84, art.SKY)
        d.arc(art.box(ex, 430, 58, 42), 200, 340, fill=art.NAVY_DEEP,
              width=art.s(11))


def _tile_rect(bbox, pivot):
    """The smallest square centred on `pivot` that holds `bbox`.

    Rotating a full canvas to swing an arm costs a megapixel per part per
    frame. A square centred on the pivot, just large enough to hold the
    content, can never clip under rotation -- for an arm that is a tenth of
    the pixels.
    """
    px, py = pivot
    r = int(math.ceil(max(math.hypot(x - px, y - py)
                          for x in (bbox[0], bbox[2])
                          for y in (bbox[1], bbox[3])))) + 2
    return (int(px) - r, int(py) - r, int(px) + r, int(py) + r)


def _tile(layer, pivot):
    bbox = layer.getbbox()
    if bbox is None:
        return layer, (0, 0)
    rect = _tile_rect(bbox, pivot)
    return layer.crop(rect), (rect[0], rect[1])


def _crop(layer):
    bbox = layer.getbbox()
    return (layer, (0, 0)) if bbox is None else (layer.crop(bbox), bbox[:2])


def _blit(dst, src, at):
    """alpha_composite that tolerates the source hanging off the canvas."""
    x, y = int(round(at[0])), int(round(at[1]))
    sx, sy = max(0, -x), max(0, -y)
    dx, dy = max(0, x), max(0, y)
    w = min(src.width - sx, dst.width - dx)
    h = min(src.height - sy, dst.height - dy)
    if w <= 0 or h <= 0:
        return
    if (sx, sy, w, h) != (0, 0, src.width, src.height):
        src = src.crop((sx, sy, sx + w, sy + h))
    dst.alpha_composite(src, (dx, dy))


# Pivots, in mascot_art's 1024 design space, taken from its own geometry.
HIP = (660, 740)                                # draw_tail's origin
SHOULDER = {-1: (362, 748), 1: (662, 748)}      # 512 -/+ 150
NECK = (512, 616)                               # base of the skull
EYE_DX, EYE_DY = 22, 18                         # pupil travel in the whites
BODY_PARTS = ("body", "arm_l", "arm_r", "head_back")


def _parts(size):
    """Every layer, pre-cropped and resolved to `size`. Built once per size."""
    cached = _parts_cache.get(size)
    if cached is not None:
        return cached
    with _art_lock:
        cached = _parts_cache.get(size)
        if cached is not None:
            return cached
        return _build_parts(size)


def _build_parts(size):
    k = size / art.SIZE
    P = {"body": _crop(_layer(art.draw_torso, size)),
         "tail": _tile(_layer(art.draw_tail, size), (HIP[0] * k, HIP[1] * k))}
    for sx, name in ((-1, "arm_l"), (1, "arm_r")):
        P[name] = _tile(_layer(lambda d, s=sx: art.draw_arm(d, s), size),
                        (SHOULDER[sx][0] * k, SHOULDER[sx][1] * k))
    # The head is three layers sharing one tile, so the pupils can move while
    # the blush and whiskers stay on top of them. The tile is sized from the
    # union of back and front -- the whiskers reach further out than the skull
    # does, so a tile measured from the back alone would clip them.
    back = _layer(_head_back, size)
    front = _layer(_head_front, size)
    union = ImageChops.lighter(back.getchannel("A"), front.getchannel("A"))
    rect = _tile_rect(union.getbbox(), (NECK[0] * k, NECK[1] * k))
    P["head_back"] = back.crop(rect)
    P["head_front"] = front.crop(rect)
    P["head_at"] = (rect[0], rect[1])
    for name, fn in (("pupils", _pupils), ("lids", _lids)):
        img, at = _crop(_layer(fn, size))
        P[name] = (img, (at[0] - rect[0], at[1] - rect[1]))
    P["eye"] = (EYE_DX * k, EYE_DY * k)
    _parts_cache[size] = P
    return P


def _spin(part, deg):
    layer, at = part
    if abs(deg) < 0.25:
        return layer, at
    return layer.rotate(deg, resample=Image.BICUBIC), at


def draw_clippy(size, head_tilt=0.0, head_dx=0.0, head_dy=0.0,
                look=(0.0, 0.0), blink=0.0,
                paw_l=0.0, paw_r=0.0, tail=0.0, squash=0.0):
    """Clippy at `size` px square, posed.

    paw_l / paw_r  degrees each paw swings up from its resting stub; past 90
                   it swings down and outward, which is the difference
                   between a wave and a swat
    tail           degrees of swish about the hip
    look           (-1..1, -1..1) where the eyes are pointing
    blink          0 open .. 1 shut
    squash         -1 stretched .. +1 squashed, about the floor
    """
    P = _parts(size)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    for layer, at in (_spin(P["tail"], tail), P["body"],
                      _spin(P["arm_l"], -paw_l), _spin(P["arm_r"], paw_r)):
        _blit(img, layer, at)

    head = P["head_back"].copy()
    head_at = P["head_at"]
    edx, edy = P["eye"]
    if blink < 0.98:
        pup, pat = P["pupils"]
        # Clamp to the unit disc: looking hard up AND hard sideways at once
        # pushed the pupil off the white and read as a squint.
        lx, ly = look
        mag = math.hypot(lx, ly)
        if mag > 1.0:
            lx, ly = lx / mag, ly / mag
        _blit(head, pup, (pat[0] + lx * edx, pat[1] + ly * edy))
    _blit(head, P["head_front"], (0, 0))     # blush and whiskers, over the eyes
    if blink > 0.02:
        lids, lat = P["lids"]
        if blink < 0.98:
            lids = lids.copy()
            lids.putalpha(lids.getchannel("A").point(lambda a: int(a * blink)))
        _blit(head, lids, lat)
    if abs(head_tilt) >= 0.25:
        head = head.rotate(head_tilt, resample=Image.BICUBIC)
    _blit(img, head, (head_at[0] + head_dx, head_at[1] + head_dy))

    if abs(squash) > 0.01:
        ky, kx = 1.0 - 0.11 * squash, 1.0 + 0.08 * squash
        wide, tall = max(1, int(size * kx)), max(1, int(size * ky))
        scaled = img.resize((wide, tall), Image.BILINEAR)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        _blit(img, scaled, ((size - wide) / 2, size * (1 - ky)))

    return img


def _mass(size, parts):
    acc = Image.new("L", (size, size), 0)
    P = _parts(size)
    for name in parts:
        entry = P[name]
        layer, at = (entry if isinstance(entry, tuple) else (entry, P["head_at"]))
        tile = Image.new("L", (size, size), 0)
        tile.paste(layer.getchannel("A"), (int(at[0]), int(at[1])))
        acc = ImageChops.lighter(acc, tile)
    return acc


def centre_of_mass(size, parts=BODY_PARTS):
    """Alpha-weighted centroid of his body -- where his mass actually is.

    A bounding box is set by whatever sticks out furthest: an ear tip, a
    whisker, and above all the tail, which curls out to one side and drags the
    box's centre with it. Centring on that centres the extremes, not the cat.
    The tail is excluded by default for exactly that reason. Measured: the box
    sat 38px right of his real mass and 20px above it.
    """
    a = np.asarray(_mass(size, parts), dtype=float)
    total = a.sum()
    if total <= 0:
        return size / 2, size / 2
    idx = np.arange(size, dtype=float)
    return (float((a.sum(axis=0) * idx).sum() / total),
            float((a.sum(axis=1) * idx).sum() / total))


# ==========================================================================
# The ball of yarn
# ==========================================================================
YARN_GAP = (138, 197, 239)     # what shows between strands: the depth cue
YARN_LIT = (216, 240, 255)     # lit top of a strand
YARN_EDGE = (74, 146, 199)     # its shaded underside
YARN_RIM = art.NAVY_DEEP       # silhouette, so it reads on a light ground

_tex_cache: dict[int, tuple] = {}


def _winding(d_px, ss=3):
    """The wound sphere, unshaded, so it can be rotated without the light.

    Nested loops at three crossing tilts. A great circle on a sphere reads as
    an ellipse; straight crosshatch read as a printed chevron pattern.
    """
    n = d_px * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    c, r = n / 2, n / 2
    dr.ellipse([0, 0, n - 1, n - 1], fill=YARN_GAP)
    w = max(2, int(n * 0.075))

    def loop(rmaj, rmin, tilt, phase):
        a = math.radians(tilt)
        ca, sa = math.cos(a), math.sin(a)
        pts = []
        for i in range(49):
            th = 2 * math.pi * i / 48 + phase
            ex, ey = rmaj * math.cos(th), rmin * math.sin(th)
            pts.append((c + ex * ca - ey * sa, c + ex * sa + ey * ca))
        dr.line(pts, fill=YARN_EDGE, width=w, joint="curve")
        dr.line([(x - w * 0.20, y - w * 0.24) for x, y in pts],
                fill=YARN_LIT, width=max(1, int(w * 0.58)), joint="curve")

    for tilt, mins, phase in ((14, (0.22, 0.55, 0.86), 0.0),
                              (70, (0.30, 0.64), 0.7),
                              (126, (0.18, 0.48, 0.80), 1.4)):
        for j, m in enumerate(mins):
            loop(r * 0.94, r * m, tilt + 5 * math.sin(j * 2.1), phase + j * 0.9)

    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, n - 1, n - 1], fill=255)
    img.putalpha(mask)
    dr.ellipse([1, 1, n - 2, n - 2], outline=YARN_RIM, width=max(3, int(n * 0.050)))
    return img.resize((d_px, d_px), Image.LANCZOS)


def _shading(d_px):
    """Sphere shading, fixed in place while the winding turns beneath it."""
    n = d_px
    shade = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    dr = ImageDraw.Draw(shade)
    for i in range(14):                          # shadow biting in lower-right
        t = i / 13
        k = int(n * 0.5 * (0.30 + 0.70 * t))
        cx, cy = n * (0.60 - 0.10 * t), n * (0.62 - 0.10 * t)
        dr.ellipse([cx - k, cy - k, cx + k, cy + k],
                   outline=(14, 60, 96, 15), width=max(1, int(n * 0.055)))
    for i in range(10):                          # bloom up and left of centre
        t = i / 9
        k = max(1, int(n * 0.30 * (1 - t)))
        cx, cy = n * 0.35, n * 0.33
        dr.ellipse([cx - k, cy - k, cx + k, cy + k], fill=(255, 255, 255, 12))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).ellipse([1, 1, n - 2, n - 2], fill=255)
    shade.putalpha(Image.composite(shade.getchannel("A"),
                                   Image.new("L", (n, n), 0), mask))
    return shade


def _texture(d_px):
    d_px = max(16, int(d_px))
    tex = _tex_cache.get(d_px)
    if tex is None:
        tex = _tex_cache[d_px] = (_winding(d_px), _shading(d_px))
    return tex


class Yarn:
    """A ball of yarn that unravels as it is played with.

    Stateful: every move pays out thread along the path travelled, rolls the
    winding by the distance over the radius (so it turns at the rate a real
    ball would), and shrinks, because thread now on the floor is no longer on
    the ball. Thread once laid never moves again -- each point takes a fixed
    sideways offset when created, so the trail keeps the kinked look of real
    yarn without shimmering frame to frame.
    """

    def __init__(self, x, y, r, full, min_scale=0.42, kink=1.0):
        self.x, self.y = float(x), float(y)
        self.r0 = float(r)
        self.full = float(full)          # path length that unravels it fully
        self.min_scale = min_scale
        self.kink = kink
        self.paid = 0.0
        self.spin = 0.0
        self.trail: list[tuple[float, float]] = []

    @property
    def r(self):
        t = min(1.0, self.paid / self.full)
        return self.r0 * max(self.min_scale, (1.0 - t) ** 0.5)

    def move(self, x, y):
        dist = math.hypot(x - self.x, y - self.y)
        if dist > 0.01:
            self.spin += math.degrees(dist / max(self.r, 1.0))
            self.paid += dist
            if not self.trail or math.hypot(x - self.trail[-1][0],
                                            y - self.trail[-1][1]) > 14 * self.scale:
                self.trail.append((x, y))
        self.x, self.y = float(x), float(y)

    scale = 1.0                          # set by the renderer for non-1080 frames

    def _kinked(self):
        pts = self.trail
        if pts:
            # The live length from the last laid point up to the ball is part
            # of the thread, so the string stays one continuous piece.
            pts = [*pts, (self.x, self.y)]
        if len(pts) < 2:
            return pts
        out = []
        for i, (x, y) in enumerate(pts):
            # A two-point baseline flips hard where the ball changed direction
            # and threw a spike across the thread; a wider window bends.
            ax, ay = pts[max(0, i - 2)]
            bx, by = pts[min(len(pts) - 1, i + 2)]
            tx, ty = bx - ax, by - ay
            n = math.hypot(tx, ty) or 1.0
            px, py = -ty / n, tx / n
            off = self.kink * self.scale * (11.0 * math.sin(i * 0.55)
                                            + 5.5 * math.sin(i * 1.31 + 1.7))
            out.append((x + px * off, y + py * off))
        for _ in range(2):
            out = ([out[0]]
                   + [((a[0] + 2 * b[0] + c[0]) / 4, (a[1] + 2 * b[1] + c[1]) / 4)
                      for a, b, c in zip(out, out[1:], out[2:])]
                   + [out[-1]])
        return out

    def draw_thread(self, d):
        """A shaded tube, twisted like plied yarn. A flat line reads as wire;
        the dark underside gives it round section, and the diagonal ticks are
        the ply -- the twist is what the eye uses to identify yarn."""
        pts = self._kinked()
        if len(pts) < 2:
            return
        w = max(5, int(self.r0 * 0.15))
        d.line(pts, fill=YARN_EDGE, width=w + 4, joint="curve")
        d.line([(x, y - w * 0.22) for x, y in pts], fill=YARN_LIT, width=w,
               joint="curve")
        for i in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            tx, ty = x1 - x0, y1 - y0
            n = math.hypot(tx, ty) or 1.0
            ux, uy = tx / n, ty / n
            px, py = -uy * w * 0.34, ux * w * 0.34
            for f in (0.25, 0.75):
                mx, my = x0 + tx * f, y0 + ty * f
                d.line([(mx - px - ux * w * 0.20, my - py - uy * w * 0.20),
                        (mx + px + ux * w * 0.20, my + py + uy * w * 0.20)],
                       fill=YARN_EDGE, width=max(1, int(w * 0.17)))

    def draw_ball(self, img):
        d_px = max(16, int(self.r * 2))
        wind, shade = _texture(int(self.r0 * 2))
        ball = wind.rotate(-self.spin, resample=Image.BICUBIC)
        ball.alpha_composite(shade)
        if d_px != ball.width:
            ball = ball.resize((d_px, d_px), Image.LANCZOS)
        img.alpha_composite(ball, (int(self.x - d_px / 2), int(self.y - d_px / 2)))


# ==========================================================================
# The sting
# ==========================================================================
def _env(n, sr, attack=0.01, decay=0.25):
    a = int(sr * attack)
    e = np.ones(n)
    e[:a] = np.linspace(0, 1, a)
    e[a:] = np.exp(-np.linspace(0, 1, n - a) / decay)
    return e


def _tone(sr, freq, dur, amp=0.25, decay=0.25, bend=0.0,
          harmonics=(1.0, 0.35, 0.12)):
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    f = freq * (1.0 + bend * t / max(dur, 1e-6))
    phase = 2 * np.pi * np.cumsum(f) / sr
    wave_ = sum(h * np.sin(phase * (i + 1)) for i, h in enumerate(harmonics))
    return amp * wave_ / sum(harmonics) * _env(n, sr, decay=decay)


def _pop(sr, dur=0.06, amp=0.18):
    """Soft paw-pat: filtered noise, very short."""
    n = int(sr * dur)
    x = np.random.default_rng(7).normal(0, 1, n)
    y = np.zeros(n)
    for i in range(1, n):
        y[i] = 0.86 * y[i - 1] + 0.14 * x[i]
    return amp * y / (np.max(np.abs(y)) + 1e-9) * _env(n, sr, attack=0.002, decay=0.10)


def _sting(sr, channels, duration):
    """Original, generated, nothing licensed.

    Deliberately gentle: this plays at the end of somebody else's video, often
    straight after speech, and a harsh sound there is worse than silence -- it
    reads as an ad interrupting rather than a signature. It decays to silence
    under the held wordmark; a note there was one beep too many.
    """
    total = int(sr * duration)
    buf = np.zeros(total)

    def place(sig, at):
        i = int(sr * at)
        end = min(total, i + len(sig))
        if end > i:
            buf[i:end] += sig[: end - i]

    place(_pop(sr, 0.07, 0.16), 0.30)                                  # yarn arrives
    place(_tone(sr, 320, 0.22, amp=0.20, decay=0.16, bend=1.4), 0.46)  # the spring
    place(_pop(sr, 0.09, 0.20), 0.72)                                  # landing
    for k, (f, off) in enumerate(((523.25, 0.0), (659.25, 0.11), (783.99, 0.22))):
        place(_tone(sr, f, 0.55, amp=0.22 - k * 0.02, decay=0.30), 1.18 + off)
    place(_tone(sr, 1046.5, 0.45, amp=0.09, decay=0.26), 1.74)

    fade = int(sr * 0.18)
    buf[-fade:] *= np.linspace(1, 0, fade)
    peak = np.max(np.abs(buf))
    if peak > 0:
        buf = buf / peak * 0.55        # headroom; never louder than the clip
    return np.column_stack([buf] * channels).astype(np.float32)


def _write_wav(path, sr, channels, duration):
    pcm = (np.clip(_sting(sr, channels, duration), -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


# ==========================================================================
# The animation
# ==========================================================================
def _ease_out(t):
    return 1 - (1 - t) ** 3


def _ease_in(t):
    return t * t


def _ease_out_back(t):
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _clamp01(t):
    return max(0.0, min(1.0, t))


def _seg(t, a, b):
    return _clamp01((t - a) / (b - a)) if b > a else 0.0


def _bump(t, a, b):
    """0 -> 1 -> 0 across the window. One beat of a movement."""
    return math.sin(math.pi * _seg(t, a, b))


def _lerp(a, b, t):
    return a + (b - a) * t


def _font(filename, size, expect):
    """The app's own face, asserted by family name.

    ui/src/renderer/src/theme.css, site/styles.css and web/app/globals.css all
    declare 'Segoe UI', and the Dashboard sets the wordmark with Tailwind's
    font-bold -- weight 700. Resolved through %WINDIR% rather than a hardcoded
    C:. Falling back silently to some default face would put the one piece of
    branding carrying the product name in the wrong type, on every clip a user
    posts, with nothing failing.
    """
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    f = ImageFont.truetype(str(fonts / filename), size)
    family, style = f.getname()
    if (family, style) != expect:
        raise RuntimeError(f"{filename} is {family!r}/{style!r}, expected {expect}")
    return f


class _Layout:
    """Where everything sits, for one output size.

    Positions come from measurement, not from nominal numbers:

    * Type is placed by its **ink box**. Putting the wordmark a fixed distance
      below the kicker left a 70px hole, because the bold face carries over
      50px of padding above its caps.
    * Clippy is placed by his **centre of mass** with the tail excluded. His
      bounding box sits 38px right of his real mass and 20px above it, so
      centring the box left him visibly off-centre and sitting low.
    * The type sits **above** him. Below him it has to clear TikTok's 1437
      line, and with his mass centred that arithmetic puts him at 348px -- a
      third of the frame.
    """

    def __init__(self, w, h):
        self.w, self.h = w, h
        # Landscape is NOT the portrait design made smaller. Scaling uniformly
        # by min(w/1080, h/1920) gives 0.5625 at 16:9, which left Clippy and
        # the type in a narrow centre column with two thirds of the frame
        # empty orange. It gets its own arrangement instead: him on the left
        # with his yarn, the branding on the right, both centred on the
        # frame's middle. Portrait is untouched -- it is the signed-off design
        # and its card must stay byte-identical.
        self.landscape = w > h
        self.k = k = (h / REF_H * 1.55) if self.landscape \
            else min(w / REF_W, h / REF_H)
        self.ox, self.oy = (w - REF_W * k) / 2, (h - REF_H * k) / 2

        self.f_kick = _font("seguisb.ttf", max(8, round(KICK_PT * k)),
                            ("Segoe UI", "Semibold"))
        self.f_word_pt = max(8, round(WORD_PT * k))
        self.f_tag = _font("seguisb.ttf", max(8, round(TAG_PT * k)),
                           ("Segoe UI", "Semibold"))
        self.stroke = max(1, round(STROKE_PT * k))
        self._word_fonts: dict[int, ImageFont.FreeTypeFont] = {}

        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        self.ink = lambda text, font, sw=0: probe.textbbox(
            (0, 0), text, font=font, stroke_width=sw)
        self.kb = self.ink(KICKER, self.f_kick)
        self.wb = self.ink(WORDMARK, self.word_font(self.f_word_pt), self.stroke)
        self.tb = self.ink(TAGLINE, self.f_tag)
        self.kh = self.kb[3] - self.kb[1]
        self.wh = self.wb[3] - self.wb[1]
        self.th = self.tb[3] - self.tb[1]
        self.gap_kick = round(GAP_KICK * k)
        self.gap_tag = round(GAP_TAG * k)
        self.text_h = self.kh + self.gap_kick + self.wh + self.gap_tag + self.th

        self.cat = max(16, round(h * LAND_CAT_FRAC) if self.landscape
                       else round(REF_W * CAT_FRAC * k))
        self.cat_bbox = draw_clippy(self.cat).getbbox()
        self.com = centre_of_mass(self.cat)
        self.cat_y = int(h / 2 - self.com[1])       # his mass on the middle

        if self.landscape:
            # Two columns. He sits in the left one, the type in the right, and
            # both are centred on the frame's vertical middle.
            self.cat_x = int(w * LAND_CAT_CX - self.com[0])
            self.text_cx = w * LAND_TEXT_CX
            self.text_top = h / 2 - self.text_h / 2
            self.yarn_x0 = -140 * k                 # enters off the left edge
        else:
            self.cat_x = int(w / 2 - self.com[0])
            self.text_cx = w / 2
            self.text_top = (self.cat_y + self.cat_bbox[1]
                             - round(GAP_CAT * k) - self.text_h)
            self.yarn_x0 = self.ox - 140 * k

        self.floor = self.cat_y + 1000 * self.cat / art.SIZE
        self.roll_y = self.floor - 60 * k
        self.ball_r = max(6, round(self.cat * 0.118))
        self.left_rest = int(self.cat_x + 354 * self.cat / art.SIZE
                             - self.ball_r * 0.55)
        self.right_rest = int(self.cat_x + 806 * self.cat / art.SIZE
                              + self.ball_r * 0.55)
        # How far right the ball may come to rest. Portrait: clear of TikTok's
        # action rail. Landscape: clear of the type column, so it never rolls
        # under the wordmark.
        self.ball_limit = (self.text_cx - (self.wb[2] - self.wb[0]) / 2 - 40 * k
                           if self.landscape else self.ox + RIGHT_RAIL * k)
        self.shoulder_y = self.cat_y + 748 * self.cat / art.SIZE

    def text_x(self, ink_w):
        """Left edge for a centred line of type.

        Portrait keeps the exact integer expression it always had, so its card
        stays byte-identical; landscape centres on its own column.
        """
        if self.landscape:
            return round(self.text_cx - ink_w / 2)
        return (self.w - ink_w) // 2

    def word_font(self, pt):
        f = self._word_fonts.get(pt)
        if f is None:
            f = self._word_fonts[pt] = _font("segoeuib.ttf", pt,
                                             ("Segoe UI", "Bold"))
        return f

    def look_at(self, x, y=None):
        lx = max(-1.0, min(1.0, (x - self.w * 0.5) / (self.w * 0.36)))
        if y is None:
            return (lx, 0.5)
        return (lx, max(-1.0, min(1.0, (y - (self.shoulder_y - 80 * self.k))
                                  / (300 * self.k))))


def _text(img, L, t):
    """'Made with' / Clips Kitty / 'Free & Open Source'.

    The name appears once. "with" stays lowercase: it is a preposition inside
    a fragment running into the name, not a title.

    Timings come from reading time -- the wordmark is legible from 1.40s and
    the frame cuts at 2.90, so it holds for a second and a half.
    """
    p = _seg(t, TEXT_IN, TEXT_IN + 0.30)
    if p <= 0:
        return
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * _clamp01(p * 1.6))

    y = L.text_top
    d.text((L.text_x(L.kb[2] - L.kb[0]), y - L.kb[1]), KICKER,
           font=L.f_kick, fill=(*INK_SOFT, a))
    y += L.kh + L.gap_kick

    # Two-tone, the way the app's sidebar sets it, drawn as two runs positioned
    # by the advance width of "Clips " so the pair keeps its kerning. The
    # settle scales about the wordmark's ink centre, so the line beneath it
    # does not shuffle while it lands.
    fw = L.word_font(max(8, int(L.f_word_pt * (0.88 + 0.12 * _ease_out_back(p)))))
    sw = max(1, round(L.stroke * fw.size / max(1, L.f_word_pt)))
    wb = L.ink(WORDMARK, fw, sw)
    wx = L.text_x(wb[2] - wb[0]) - wb[0]
    wy = y + L.wh / 2 - (wb[3] - wb[1]) / 2 - wb[1]
    for run, fill in ((WORDMARK.split()[0], CLIPS), (WORDMARK.split()[1], KITTY)):
        dx = 0 if fill is CLIPS else d.textlength(WORDMARK.split()[0] + " ", font=fw)
        d.text((wx + dx, wy), run, font=fw, fill=(*fill, a),
               stroke_width=sw, stroke_fill=(*STROKE, a))
    y += L.wh + L.gap_tag

    q = _seg(t, TEXT_IN + 0.20, TEXT_IN + 0.45)
    if q > 0:
        d.text((L.text_x(L.tb[2] - L.tb[0]),
                y - L.tb[1] + round(20 * L.k * (1 - _ease_out(q)))), TAGLINE,
               font=L.f_tag, fill=(*INK_SOFT, int(255 * _clamp01(q * 1.6))))

    img.alpha_composite(layer)


def frames(w, h, fps):
    """The animation, one PIL image per frame.

    Clippy's arms are short stubs -- that is the mascot -- so a paw swing on
    its own is a twitch. The motion comes from the whole animal: he sinks
    before he springs, the spring carries him off the floor and toward the
    ball, and he lands hard. Anticipation and follow-through are what make
    well under a second read as a pounce rather than a wobble.
    """
    L = _Layout(w, h)
    k = L.k
    n = max(1, round(fps * DURATION))
    yarn = Yarn(L.yarn_x0, L.roll_y, L.ball_r, 2400 * k)
    yarn.scale = k
    out = []

    for i in range(n):
        t = i / fps
        run = _seg(t, 0.00, 0.26)
        crouch = _bump(t, 0.20, 0.40)
        leap = _bump(t, 0.38, 0.60)
        land = _bump(t, 0.56, 0.82)
        fly = _seg(t, 0.58, 1.10)
        roll = _seg(t, 1.10, 1.62)

        if fly <= 0:
            x, y = _lerp(L.yarn_x0, L.left_rest, _ease_out(run)), L.roll_y
        else:
            x = _lerp(_lerp(L.left_rest, L.right_rest, _ease_out(fly)),
                      L.right_rest + 34 * k, _ease_out(roll))
            # Keep the ball clear of TikTok's right action rail. Clamped
            # against its CURRENT radius, not its starting one: it unravels to
            # about two thirds of its size by the time it comes to rest.
            x = min(x, L.ball_limit - yarn.r - 8 * k)
            y = L.roll_y - math.sin(math.pi * min(1.0, fly)) * 230 * k
        yarn.move(x, y)

        paw = (SWAT_UP * _seg(t, 0.30, 0.52)
               + (SWAT_DOWN - SWAT_UP) * _seg(t, 0.52, 0.63)
               - SWAT_DOWN * _seg(t, 0.63, 0.88))
        pose = {
            "look": L.look_at(x, y),
            "head_tilt": 15 * L.look_at(x)[0] - 10 * leap,
            "head_dy": (12 * crouch - 26 * leap + 14 * land) * k,
            "paw_l": paw,
            "paw_r": 22 * leap,
            "squash": 0.70 * crouch - 1.0 * leap + 1.0 * land,
            "tail": 20 * math.sin(t * 7.5) + 30 * leap,
            "blink": 1.0 if (0.58 < t < 0.66 or 1.78 < t < 1.88
                          or 2.42 < t < 2.52) else 0.0,
        }
        body = ((-78 * leap - 46 * land) * k,
                (24 * crouch - 92 * leap + 30 * land
                 + 7 * math.sin(t * 3.1) * _seg(t, 1.6, 2.0)) * k)

        img = Image.new("RGBA", (w, h), (*ORANGE, 255))
        img.alpha_composite(draw_clippy(L.cat, **pose), (int(L.cat_x + body[0]), int(L.cat_y + body[1])))
        # The yarn goes AFTER him -- ball and string both -- so it is in front
        # for the whole animation. Drawn before him the string vanished behind
        # his body, including the length he had just knocked out in front.
        yarn.draw_thread(ImageDraw.Draw(img))
        yarn.draw_ball(img)
        _text(img, L, t)
        out.append(img.convert("RGB"))
    return out


# ==========================================================================
# Building, caching and appending
# ==========================================================================
def _ffmpeg():
    from core import binaries
    return binaries.ffmpeg()


def _ffprobe():
    from core import binaries
    return binaries.ffprobe()


def probe(path: Path) -> dict | None:
    """The clip's format, which the outro has to match exactly for `-c copy`."""
    r = subprocess.run(
        [_ffprobe(), "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        streams = json.loads(r.stdout)["streams"]
    except (ValueError, KeyError):
        return None
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        return None
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    num, _, den = (v.get("r_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    return {
        "w": int(v["width"]), "h": int(v["height"]),
        "fps": round(fps, 3),
        "pix_fmt": v.get("pix_fmt", "yuv420p"),
        "sample_rate": int(a["sample_rate"]) if a else 48000,
        "channels": int(a["channels"]) if a else 2,
    }


def _fps_tag(fps) -> str:
    """`30`, not `30.0`.

    probe() reads r_frame_rate and divides, so a plain 30fps clip arrives as
    the float 30.0 and keyed the cache as `..._30.0_...` -- which never matched
    the shipped `..._30_...` card, so every install rendered its own copy of a
    file it already had. Fractional rates (29.97) keep their decimals.
    """
    f = float(fps)
    return str(int(f)) if f.is_integer() else str(round(f, 3))


def _key(fmt: dict) -> str:
    return ("outro_{w}x{h}_{fps}_{pix_fmt}_{sample_rate}_{channels}.mp4"
            .format(**{**fmt, "fps": _fps_tag(fmt["fps"])}))


def _cache_dir(config: dict) -> Path:
    d = resolve_data_dir(config) / "outro"
    d.mkdir(parents=True, exist_ok=True)
    # _render cleans up after itself, but only if the process survives; a kill
    # mid-render leaves its scratch directory behind forever. Sweep anything
    # older than an hour, which cannot be a build still in progress.
    cutoff = time.time() - 3600
    for stale in d.glob(".build_*"):
        try:
            if stale.is_dir() and stale.stat().st_mtime < cutoff:
                shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            pass
    return d


def _bundled(name: str) -> Path | None:
    """The prebuilt card that ships with the app, if one matches this format.

    The common paths — both canvases at 30 and 60fps — then render nothing at
    all, ever. Anything else renders once and is cached.

    Searched the same way core/binaries.py finds bundled tools, because a
    repo-relative path is wrong in a frozen build: PyInstaller one-dir puts
    data under _internal/ beside the exe, not beside the module.
    """
    import sys

    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        roots += [exe_dir / "assets" / "outro",
                  exe_dir / "_internal" / "assets" / "outro"]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass) / "assets" / "outro")
    roots.append(Path(__file__).resolve().parent.parent / "assets" / "outro")
    for root in roots:
        candidate = root / name
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def ensure_outro(fmt: dict, config: dict) -> Path:
    """The outro MP4 for this exact clip format, built once and kept.

    Keyed on width, height, fps, pixel format and audio parameters, because
    those are precisely the things concat's `-c copy` requires to agree. Built
    once per format on a machine -- not once per video, and not once per clip.
    """
    name = _key(fmt)
    cached = _cache_dir(config) / name
    if cached.exists() and cached.stat().st_size > 0:
        return cached

    with _build_lock:
        if cached.exists() and cached.stat().st_size > 0:
            return cached          # another thread built it while we waited
        shipped = _bundled(name)
        if shipped:
            shutil.copyfile(shipped, cached)
            return cached
        _render(fmt, cached)
    return cached


def _render(fmt: dict, dst: Path) -> None:
    """Draw and encode. Written to a temp name and renamed, so a crash or a
    second process never leaves a half-written file in the cache."""
    tmp_dir = dst.parent / f".build_{os.getpid()}_{threading.get_ident()}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        imgs = frames(fmt["w"], fmt["h"], fmt["fps"])
        for i, im in enumerate(imgs):
            im.save(tmp_dir / f"{i:05d}.png")
        wav = tmp_dir / "sting.wav"
        _write_wav(wav, fmt["sample_rate"], fmt["channels"],
                   len(imgs) / fmt["fps"])
        tmp_mp4 = tmp_dir / "outro.mp4"
        r = subprocess.run([
            _ffmpeg(), "-y", "-v", "error",
            "-framerate", str(fmt["fps"]), "-i", str(tmp_dir / "%05d.png"),
            "-i", str(wav),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", fmt["pix_fmt"],
            "-c:a", "aac", "-b:a", "128k",
            "-ar", str(fmt["sample_rate"]), "-ac", str(fmt["channels"]),
            "-shortest", "-movflags", "+faststart", str(tmp_mp4),
        ], capture_output=True, text=True)
        if r.returncode != 0 or not tmp_mp4.exists():
            raise RuntimeError(f"outro encode failed: {r.stderr.strip()[-400:]}")
        tmp_mp4.replace(dst)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def enabled(config: dict) -> bool:
    """Absent means ON, so existing users get it on upgrade without their
    settings.yaml being rewritten, and anyone who turned it off keeps it off."""
    return bool(config.get("clips", {}).get("outro", True))


# How long to keep trying the atomic rename before writing in place instead.
# Short on purpose: a scanner's memory map clears in seconds, and anything
# still holding the file after that is a reader the rename can never beat.
REPLACE_BUDGET = 10.0

# Per-job tally. Until this existed the only way to know how many clips got an
# end card was to probe every file and compare its duration against the window
# in its filename -- which is how "31 of 55" was found, days after the fact.
_tally = {"added": 0, "skipped": 0}


def reset_tally() -> None:
    _tally["added"] = _tally["skipped"] = 0


def summary() -> str:
    """One line for the end of a job, or "" when there is nothing to say."""
    added, skipped = _tally["added"], _tally["skipped"]
    if not (added or skipped):
        return ""
    line = f"End card added to {added} of {added + skipped} clip(s)"
    return line if not skipped else f"{line} ({skipped} skipped — see above)"


def _overwrite_in_place(src: Path, dst: Path) -> bool:
    """Copy `src`'s bytes over `dst`, keeping `dst`'s identity.

    This is the one that beats a reader. Measured, with a file held the way a
    media player holds it (read access, sharing read+write but NOT delete):

        os.replace(src, dst)   -> BLOCKED, WinError 5 Access is denied
        open(dst, "r+b") write -> SUCCEEDS

    MoveFileEx(REPLACE_EXISTING) has to DELETE the destination and the reader's
    share mode forbids exactly that; writing to the existing file needs only
    write access, which it permits. That asymmetry is why the renderer could
    overwrite a clip without complaint while the end card's replace failed on
    the very same file.

    Not atomic, so it is a fallback and never the first move. `src` is left on
    disk until the result is verified, so the bytes are always recoverable.
    """
    payload = src.read_bytes()
    for attempt in (1, 2):
        try:
            with open(dst, "r+b") as f:
                f.seek(0)
                f.write(payload)
                f.truncate()
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            if attempt == 2:
                print(f"  outro skipped: could not write {dst.name} ({exc})")
                return False
            time.sleep(0.5)
            continue
        if dst.stat().st_size == len(payload):
            return True
        # Short write. src still holds the good bytes, so try once more before
        # giving up -- and if it never verifies, say where the copy is rather
        # than deleting the only intact version of the clip.
        if attempt == 2:
            print(f"  outro skipped: {dst.name} did not verify after writing; "
                  f"the joined copy is at {src}")
            return False
    return False


def _replace_with_retry(src: Path, dst: Path, budget: float = REPLACE_BUDGET) -> bool:
    """Put `src` in `dst`'s place, against whatever is holding the file.

    Two strategies, because two different things block this and they need
    opposite answers:

    * **A scanner** memory-maps a just-written clip, which blocks the rename
      AND a write, and clears after a few seconds. Waiting is the answer.
    * **The app itself** holds a clip open whenever the desktop UI previews it
      (`GET /media/<id>` keeps a Chromium media handle). That is held for as
      long as the user leaves it open, so waiting never wins -- one clip sat
      locked for the full 59s budget and still lost its end card. Writing in
      place is the answer, because a reader permits writes but not deletes.

    So: rename first, briefly -- it is atomic and it is right for the scanner
    -- then fall back to the in-place write, which beats the reader. The first
    budget is deliberately short now that a fallback exists; the old 60s bought
    nothing except a minute of stalling per held clip.

    Returns False rather than raising: the caller then leaves the clip exactly
    as it was, which is the right outcome either way.
    """
    started = time.monotonic()
    delay = 0.1
    while True:
        try:
            src.replace(dst)
            waited = time.monotonic() - started
            if waited > 0.5:
                print(f"      End card: {dst.name} was locked for "
                      f"{waited:.1f}s — added anyway")
            return True
        except PermissionError:
            if time.monotonic() - started + delay >= budget:
                break
            time.sleep(delay)
            delay = min(delay * 2, 2.0)      # ceiling, not unbounded doubling

    # Still held after the rename budget: almost certainly a preview open in
    # the app rather than a scan, so stop waiting and write through it.
    if _overwrite_in_place(src, dst):
        print(f"      End card: {dst.name} was open elsewhere — written in "
              f"place instead")
        return True

    # Both refused, which a memory-mapped file will do. Say so: a clip that
    # quietly comes out without an end card and no reason in the log is how
    # this went unexplained for days.
    print(f"  outro skipped: {dst.name} could not be replaced or written "
          f"({time.monotonic() - started:.0f}s)")
    return False


def append(clip: Path, config: dict) -> bool:
    """Append the outro to a finished clip, IN PLACE. Returns whether it ran.

    Never raises. A clip with no outro is worth far more than no clip, so any
    failure here leaves the file exactly as it was and prints why -- the same
    stance as the CPU-encoder fallback added for #84.
    """
    try:
        if not enabled(config):
            return False        # off: not a skip, nothing to report

        # ABSOLUTE, always. The concat demuxer resolves the paths inside its
        # list file, and a relative one does not survive that: `data/clips/X/
        # clip.mp4` written into a list that itself lives in `data/clips/X/`
        # resolves to `data/clips/X/data/clips/X/clip.mp4` and opens nothing.
        #
        # It matters because the database holds both shapes -- settings.yaml's
        # data_dir is relative in a checkout -- and that quietly cost 365 of
        # 1098 clips their end card. Every one of the relative ones failed and
        # every absolute one worked, which is what made it visible at all.
        clip = Path(clip).resolve()
        fmt = probe(clip)
        if not fmt:
            print(f"  outro skipped: could not probe {clip.name}")
            _tally["skipped"] += 1
            return False
        outro = ensure_outro(fmt, config).resolve()

        listing = clip.parent / f".{clip.stem}.outro.txt"
        joined = clip.parent / f".{clip.stem}.outro.mp4"
        listing.write_text(
            f"file '{clip.as_posix()}'\nfile '{outro.as_posix()}'\n",
            encoding="utf-8")
        try:
            r = subprocess.run([
                _ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
                "-i", str(listing), "-c", "copy", "-movflags", "+faststart",
                str(joined),
            ], capture_output=True, text=True)
            if r.returncode != 0 or not joined.exists() or joined.stat().st_size == 0:
                print(f"  outro skipped: {r.stderr.strip()[-200:] or 'concat failed'}")
                _tally["skipped"] += 1
                return False
            if not _replace_with_retry(joined, clip):
                _tally["skipped"] += 1
                return False
            _tally["added"] += 1
            return True
        finally:
            # discard(), not unlink(): this runs in a `finally`, and on Windows
            # a file still held open raises PermissionError, which would
            # REPLACE whatever error was already in flight. That is issue #74,
            # and unlink(missing_ok=True) does not cover it -- missing_ok only
            # suppresses FileNotFoundError.
            discard(listing)
            discard(joined)
    except Exception as exc:  # never lose a clip
        print(f"  outro skipped: {type(exc).__name__}: {exc}")
        _tally["skipped"] += 1
        return False


def verify(size: int = art.SIZE) -> dict:
    """Compose the rest pose and diff it against the shipped mascot.png.

    The guard against this rig drifting away from the artwork. It must stay at
    zero differing pixels: the parts here are the mascot's own drawing
    functions, so anything else means the split has broken.
    """
    with _art_lock:
        return _verify_locked()


def _verify_locked() -> dict:
    old = art.SS
    art.SS = 4
    try:
        raw = art.canvas()
        d = ImageDraw.Draw(raw)
        art.draw_tail(d)
        art.draw_torso(d)
        art.draw_arm(d, -1)
        art.draw_arm(d, 1)
        _head_back(d)
        _pupils(d)
        _head_front(d)
        mine = art.fit(raw)
    finally:
        art.SS = old
    ref_path = Path(__file__).resolve().parent.parent / "docs" / "brand" / "mascot.png"
    ref = Image.open(ref_path).convert("RGBA")
    diff = np.abs(np.asarray(mine, dtype=np.int16)
                  - np.asarray(ref, dtype=np.int16))
    return {"max_channel_diff": int(diff.max()),
            "pixels_differing": int((diff.sum(axis=2) > 12).sum())}


def has_outro(clip: Path, window_seconds: float) -> bool | None:
    """Whether `clip` already carries an end card.

    Compared against the window it was cut from rather than any marker in the
    file: a clip is its window long, plus the card if one was appended. Half
    the card's length is the threshold, which no rounding or keyframe drift can
    cross in either direction.

    None when the file cannot be read, so a probe failure is never mistaken for
    "needs one" and does not get a second card bolted on.
    """
    fmt_dur = subprocess.run(
        [_ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(clip)], capture_output=True, text=True).stdout.strip()
    try:
        actual = float(fmt_dur)
    except ValueError:
        return None
    return (actual - window_seconds) > DURATION / 2


def backfill(db, config: dict, video_id: str | None = None) -> dict:
    """Add the end card to finished clips that never got one.

    Clips do not need re-rendering for this -- append() works on any finished
    file -- which matters because a library can hold hundreds of them and a
    re-render is minutes each.

    Safe to run repeatedly: a clip already longer than its window is skipped,
    so nothing is ever double-carded.
    """
    rows = db.conn.execute(
        "SELECT path, start_s, end_s FROM clips WHERE path IS NOT NULL"
        + (" AND video_id = ?" if video_id else ""),
        (video_id,) if video_id else (),
    ).fetchall()

    reset_tally()
    stats = {"checked": 0, "added": 0, "already": 0, "missing": 0, "failed": 0}
    for row in rows:
        clip = Path(row["path"] if hasattr(row, "keys") else row[0])
        start, end = (row["start_s"], row["end_s"]) if hasattr(row, "keys") else (row[1], row[2])
        if not clip.exists():
            stats["missing"] += 1
            continue
        stats["checked"] += 1
        state = has_outro(clip, float(end) - float(start))
        if state is None:
            stats["failed"] += 1
            continue
        if state:
            stats["already"] += 1
            continue
        if append(clip, config):
            stats["added"] += 1
            print(f"      + {clip.name}")
        else:
            stats["failed"] += 1
    return stats


def finish(src: Path, dst: Path, config: dict) -> bool:
    """Write `dst` as `src` followed by the end card. Returns whether it ran.

    THIS is the one the render path uses, and the difference from append() is
    the whole point: append() modifies a file that already exists, finish()
    PRODUCES the file. CapCut works the same way -- the outro is part of the
    export, not something bolted on afterwards.

    It matters because of a measured asymmetry on Windows. With a clip held the
    way the desktop app holds it while previewing (read access, sharing read
    and write, but not delete):

        os.replace(joined, clip)  -> BLOCKED, WinError 5 Access is denied
        ffmpeg -y ... -> clip     -> SUCCEEDS

    A reader forbids DELETING the file and permits WRITING it. Every earlier
    version replaced, so a clip open in the UI lost its card no matter how long
    we waited -- one run managed only 37 of 49, and spent twelve minutes
    stalling on locks it could never win. Producing the file cannot hit that
    operation at all.

    Never loses the clip: three attempts, each of which WRITES `dst` rather
    than replacing it, so the lock cannot block any of them.
    """
    src = Path(src).resolve()
    dst = Path(dst).resolve()
    try:
        fmt = probe(src)
        if fmt:
            card = ensure_outro(fmt, config).resolve()
            listing = src.with_suffix(".cardlist.txt")
            # Absolute paths: the concat demuxer resolves entries relative to
            # the list file, so a relative one points nowhere. That cost 365
            # clips their card once already.
            listing.write_text(
                f"file '{src.as_posix()}'\nfile '{card.as_posix()}'\n",
                encoding="utf-8")
            try:
                r = subprocess.run([
                    _ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(listing), "-c", "copy", "-movflags", "+faststart",
                    str(dst),
                ], capture_output=True, text=True)
            finally:
                discard(listing)
            if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
                _tally["added"] += 1
                discard(src)
                return True
            print(f"  end card skipped: {r.stderr.strip()[-200:] or 'concat failed'}")
        else:
            print(f"  end card skipped: could not probe {src.name}")
    except Exception as exc:  # never lose a clip
        print(f"  end card skipped: {type(exc).__name__}: {exc}")

    # The card could not be made. The clip itself still has to arrive at dst,
    # and both fallbacks WRITE it, so a held file cannot block them either.
    _tally["skipped"] += 1
    try:
        r = subprocess.run([_ffmpeg(), "-y", "-v", "error", "-i", str(src),
                            "-c", "copy", "-movflags", "+faststart", str(dst)],
                           capture_output=True, text=True)
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            discard(src)
            return False
    except OSError:
        pass
    shutil.copyfile(src, dst)                 # last resort; still a write
    discard(src)
    return False
