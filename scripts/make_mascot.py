"""Draw the Clips Kitty mascot and the app icon.

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
    # bottom, left.
    blob(d, [
        (512, 700, 150),
        (594, 790, 128),
        (512, 862, 158),
        (430, 790, 128),
    ], NAVY)

    # Belly: narrow at the chest, widening toward the bottom. Two concentric
    # ellipses made a near-perfect circle sitting in the middle of the torso
    # like a badge pinned on, rather than a marking that belongs to the body.
    blob(d, [
        (512, 726, 58),
        (554, 772, 70),
        (566, 812, 80),
        (512, 856, 88),
        (458, 812, 80),
        (470, 772, 70),
    ], SKY)

    # Feet AFTER the torso, so the paws sit in front of it. Drawn before it
    # they were painted straight over and the belly appeared to swallow them.
    for sx in (-1, 1):
        fx = 512 + sx * 150
        ell(d, 512 + sx * 132, 926, 82, 66, NAVY)
        ell(d, fx, 944, 66, 50, SKY)                    # paw
        ell(d, fx, 956, 32, 21, SKY_DEEP)               # main pad
        for f in (-1, 0, 1):                            # toe beans
            ell(d, fx + f * 32, 920, 15, 12, SKY_DEEP)

    # Arms: rounded stubs ending in proper paws — a big palm pad and three
    # toe beans, same as the feet. Without the pads the hands were just
    # blue mittens with no read on them at all.
    for sx in (-1, 1):
        shoulder_x = 512 + sx * 150
        hand_x = 512 + sx * 232
        for i in range(19):
            t = i / 18
            cx = shoulder_x + (hand_x - shoulder_x) * t
            cy = 748 + 74 * t
            ell(d, cx, cy, 52 - 10 * t, 52 - 10 * t, NAVY)
        ell(d, hand_x, 828, 60, 56, SKY)                       # paw
        ell(d, hand_x, 846, 30, 22, SKY_DEEP)                  # palm pad
        for f in (-1, 0, 1):                                   # toe beans
            ell(d, hand_x + f * 30, 802, 15, 13, SKY_DEEP)


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
        # Inner ear follows the same lean, inset from each edge.
        tri([(bx - 52, 348), (bx + 52, 340), (bx + sx * 64, 166)], SKY_DEEP)

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
