"""Draw the Clips Studio mascot and the app icon.

A cat-monkey. Cat's head and ears; monkey's heart-shaped face patch, long
curling tail and little grabby hands. Dark navy fur from the dashboard
background, sky blue from the dashboard accent on the face, belly and paws.

    python scripts/make_mascot.py

Outputs:
    docs/brand/mascot.png        full body — README, website, merch base
    docs/brand/mascot-head.png   head only — what the icon is cut from
    ui/build/icon.ico            multi-size Windows icon for the installer

This script IS the source of the artwork: edit the numbers and re-run.

Drawn for plush and print, which is a real constraint on the shapes:
  * chibi proportions — head about as wide as the body, which is what makes
    a character read as cute rather than merely animal-shaped;
  * big eyes set low and wide apart, with two highlights each;
  * every silhouette is a circle or a rounded blob, because sharp angles
    are what make a sewn version look wrong;
  * few parts, flat colour, no gradients — a pattern-cutter can follow it
    and it survives being embroidered small.
Everything is drawn at 4x and downsampled: Pillow's shapes are aliased, and
jagged edges make a mascot look broken at any size.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "docs" / "brand"
UI_BUILD = ROOT / "ui" / "build"

SS = 4          # supersampling factor
SIZE = 1024     # final canvas
C = SIZE * SS   # working canvas

# Dashboard palette (ui Tailwind tokens)
NAVY = (19, 36, 61)         # #13243D  bg-surface — the fur
NAVY_DEEP = (10, 22, 40)    # #0A1628  bg-base — outlines, pupils, nose
NAVY_MID = (28, 51, 84)     # #1C3354  bg-raised — soft fur shading
SKY = (56, 189, 248)        # #38BDF8  accent — face, belly, paws
SKY_DEEP = (14, 165, 233)   # #0EA5E9  accent-strong — inner ears, pads
CREAM = (241, 245, 249)     # #F1F5F9  eye whites, highlights


def s(v: float) -> int:
    return int(round(v * SS))


def box(cx, cy, rx, ry):
    return [s(cx - rx), s(cy - ry), s(cx + rx), s(cy + ry)]


def ell(d, cx, cy, rx, ry, fill=None, outline=None, width=0):
    d.ellipse(box(cx, cy, rx, ry), fill=fill, outline=outline,
              width=s(width) if width else 0)


def blob(d, points, fill):
    """A closed shape through `points`, rounded by drawing a fat circle at
    every point and filling the polygon between them. Keeps every edge soft,
    which is the whole trick to a plush-looking character.

    Points must already be in canvas space — callers inside draw_head have to
    map through its transform first, or the shape lands at the wrong scale.

    They must also be in PERIMETER order, walking the outline. Listing them
    in any other order (all the left points, then all the right ones) makes
    the polygon cross itself, and the shape comes out with notches bitten
    out of it.
    """
    d.polygon([(s(x), s(y)) for x, y, _ in points], fill=fill)
    for x, y, r in points:
        ell(d, x, y, r, r, fill)


# ---------------------------------------------------------------- tail
def draw_tail(d):
    """Long monkey curl — the clearest non-cat cue in the silhouette.

    Starts buried in the hip and ends curled up to the right, so the tip is
    OUTSIDE the body. An earlier sweep ended back underneath the torso, which
    hid the sky tip completely and made the tail read as a plain loop.
    """
    pts = []
    for i in range(121):
        t = i / 120
        ang = math.radians(-140 + 200 * t)
        rad = 90 + 210 * t
        cx = 660 + rad * math.cos(ang)
        cy = 740 - rad * math.sin(ang) * 0.80
        pts.append((cx, cy, 42 - 20 * t))
    for cx, cy, r in pts:
        ell(d, cx, cy, r, r, NAVY)
    for cx, cy, r in pts[-20:]:      # sky tip, matching the paws
        ell(d, cx, cy, r * 0.94, r * 0.94, SKY)


# ---------------------------------------------------------------- body
def draw_body(d):
    # Torso: a soft pear, wider at the bottom. Perimeter order — top, right,
    # bottom, left. Kept narrower than the head: a wide torso plus a wide
    # belly patch made the whole lower half read as one fat mass.
    blob(d, [
        (512, 706, 124),
        (556, 786, 106),
        (512, 836, 124),
        (468, 786, 106),
    ], NAVY)

    # Belly patch, held well clear of the bottom so it never runs into the
    # feet.
    ell(d, 512, 792, 92, 96, SKY)
    ell(d, 512, 738, 66, 56, SKY)

    # Back paws LAST, so they sit in front of the body. Drawn before it they
    # were painted over by the torso and the belly appeared to swallow them.
    # Same pad and toe beans as the hands, so all four match.
    # Set a little narrower than the hands, so the stance reads as a sitting
    # animal rather than a star shape.
    for sx in (-1, 1):
        fx = 512 + sx * 100
        ell(d, fx, 906, 54, 44, NAVY)                   # ankle behind the paw
        ell(d, fx, 920, 46, 35, SKY)                    # paw
        ell(d, fx, 929, 22, 14, SKY_DEEP)               # main pad
        for f in (-1, 0, 1):                            # toe beans
            ell(d, fx + f * 21, 904, 10, 9, SKY_DEEP)

    # Arms: rounded stubs ending in proper paws — a big palm pad and three
    # toe beans, same as the feet.
    #
    # The shoulder is anchored WELL INSIDE the torso. Started at the torso
    # edge instead, the first circles sit half outside it and leave a
    # concave notch where the arm meets the body.
    # Thin. At the old thickness the arm was as fat as the torso is deep, so
    # the two merged into one dark mass with paws stuck on the outside and no
    # limb readable at all.
    for sx in (-1, 1):
        shoulder_x = 512 + sx * 88
        hand_x = 512 + sx * 172
        for i in range(21):
            t = i / 20
            cx = shoulder_x + (hand_x - shoulder_x) * t
            cy = 734 + 76 * t
            ell(d, cx, cy, 34 - 7 * t, 34 - 7 * t, NAVY)
        ell(d, hand_x, 814, 40, 37, SKY)                       # paw
        ell(d, hand_x, 825, 19, 13, SKY_DEEP)                  # palm pad
        for f in (-1, 0, 1):                                   # toe beans
            ell(d, hand_x + f * 20, 797, 10, 8, SKY_DEEP)


# ---------------------------------------------------------------- head
def draw_head(d, cx=512.0, cy=430.0, k=1.0):
    """Head, ears, heart-shaped monkey face patch, eyes, nose, mouth."""
    def X(v):
        return cx + (v - 512) * k

    def Y(v):
        return cy + (v - 430) * k

    def R(v):
        return v * k

    def T(pts):
        """Design-space points -> canvas space, for blob()."""
        return [(X(px), Y(py), R(pr)) for px, py, pr in pts]

    # --- ears behind the skull, so they emerge rather than sit on top.
    # Rounded triangles built from three equal-radius corners: an earlier
    # version rounded the tip with a much fatter circle, which gave every ear
    # a knob on a stick.
    # Straight polygons, no blob(): blob puts a circle at every corner, and
    # at this size those read as beads stuck on the ear rather than as
    # rounding.
    def tri(pts, fill):
        d.polygon([(s(X(px)), s(Y(py))) for px, py in pts], fill=fill)

    # --- ears BEHIND the skull, which is then drawn over their bases. Drawn
    # on top instead, the rim outline tracked across the head and the whole
    # ear read as a sticker laid on the face.
    #
    # Cat ears lean OUTWARD — they don't stand straight up like a fox's. The
    # apex sits well outboard of the base centre, so the inner edge slopes
    # while the outer edge stays near vertical.
    for sx in (-1, 1):
        bx = 512 + sx * 116
        ear = [(bx - 98, 380), (bx + 98, 368), (bx + sx * 78, 108)]
        # Rim: stamp the same triangle around a small circle. Widening the
        # base and raising the apex by different amounts (the obvious way)
        # grows a leaning triangle unevenly, and the extra slice shows as a
        # crease down one edge of the ear.
        for a in range(0, 360, 24):
            off = (7 * math.cos(math.radians(a)), 7 * math.sin(math.radians(a)))
            tri([(px + off[0], py + off[1]) for px, py in ear], NAVY_MID)
        tri(ear, NAVY)
        # Inner ear follows the same lean, inset just enough to leave a fur
        # border. Any smaller and it reads as a thin sliver rather than the
        # inside of an ear.
        tri([(bx - 76, 358), (bx + 76, 348), (bx + sx * 74, 146)], SKY_DEEP)

    # --- skull: wide and low, the chibi shape. Deliberately flat colour —
    # a lighter shading ellipse was in here and it made the ears' rounded
    # corners show up as dark balls where navy sat on top of it. Flat also
    # reproduces better in embroidery and print, which is the point.
    #
    # Wider than tall: a taller dome pushed the skull up between the ears and
    # buried them, so they read as nubs on a big round head rather than ears
    # sitting on top of it.
    #
    # The rim is not decoration: navy fur against a dark taskbar is nearly
    # the same colour, and without it the whole silhouette dissolves and only
    # the blue face floats.
    ell(d, X(512), Y(444), R(268), R(212), NAVY_MID)
    ell(d, X(512), Y(444), R(262), R(206), NAVY)

    # --- monkey face patch: a heart — narrow at the brow, wide at the
    # cheeks, tapering to a small chin. This single shape is what stops the
    # character reading as "just a cat".
    blob(d, T([
        (512, 372, 86),          # brow, between the eyes
        (512 + 132, 432, 96),    # right cheek
        (512 + 104, 516, 82),
        (512, 566, 64),          # chin
        (512 - 104, 516, 82),
        (512 - 132, 432, 96),    # left cheek, closing the loop
    ]), SKY)

    # --- eyes: big, low, wide apart. Cuteness lives here.
    for sx in (-1, 1):
        ex = X(512 + sx * 104)
        ell(d, ex, Y(424), R(74), R(78), CREAM)
        ell(d, ex, Y(432), R(50), R(54), NAVY_DEEP)
        ell(d, ex + R(sx * 16), Y(408), R(19), R(19), CREAM)   # main catchlight
        ell(d, ex - R(sx * 14), Y(450), R(9), R(9), CREAM)     # second, smaller

    # --- blush, just inside the cheeks
    for sx in (-1, 1):
        ell(d, X(512 + sx * 186), Y(486), R(40), R(26), SKY_DEEP)

    # --- nose: small rounded cat triangle
    blob(d, T([(512 - 26, 498, 15), (512 + 26, 498, 15), (512, 528, 13)]),
         NAVY_DEEP)

    # --- mouth: a small w, close under the nose
    d.arc(box(X(512 - 26), Y(544), R(28), R(24)), 10, 165, fill=NAVY_DEEP,
          width=s(R(10)))
    d.arc(box(X(512 + 26), Y(544), R(28), R(24)), 15, 170, fill=NAVY_DEEP,
          width=s(R(10)))

    # --- whiskers: short and slightly raised, so they read as cat without
    # spearing out into the silhouette
    for sx in (-1, 1):
        for dy, lift in ((-6, 16), (16, 4)):
            d.line([s(X(512 + sx * 196)), s(Y(508 + dy)),
                    s(X(512 + sx * 286)), s(Y(508 + dy - lift))],
                   fill=NAVY_DEEP, width=s(R(8)))


def fit(img, margin=0.045):
    """Crop to what was actually drawn, then centre it with a margin.

    The feet used to run off the bottom of the canvas, because the drawing
    coordinates were tuned by hand and the composition grew past them.
    Measuring the real bounding box means no part of the character can be
    clipped no matter how the geometry above is edited, and the mascot stays
    optically centred instead of sitting wherever the numbers landed.
    """
    bbox = img.getbbox()
    if bbox is None:
        return img.resize((SIZE, SIZE), Image.LANCZOS)
    art = img.crop(bbox)
    target = int(SIZE * (1 - 2 * margin))
    scale = min(target / art.width, target / art.height)
    art = art.resize((max(1, int(art.width * scale)),
                      max(1, int(art.height * scale))), Image.LANCZOS)
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(art, ((SIZE - art.width) // 2, (SIZE - art.height) // 2), art)
    return out


def render_full():
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    draw_tail(d)
    draw_body(d)
    draw_head(d)
    return fit(img)


def render_head():
    """Head only: a full body turns to mush at 32px, a head keeps its
    silhouette all the way down."""
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    draw_head(d, cx=512, cy=545, k=1.30)
    return fit(img, margin=0.03)


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    UI_BUILD.mkdir(parents=True, exist_ok=True)

    render_full().save(BRAND / "mascot.png")
    print(f"  wrote {BRAND / 'mascot.png'}")

    head = render_head()
    head.save(BRAND / "mascot-head.png")
    print(f"  wrote {BRAND / 'mascot-head.png'}")

    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    head.save(UI_BUILD / "icon.ico", format="ICO", sizes=sizes)
    print(f"  wrote {UI_BUILD / 'icon.ico'}  {[w for w, _ in sizes]}")


if __name__ == "__main__":
    main()
