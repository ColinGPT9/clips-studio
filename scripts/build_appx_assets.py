"""Generate the Microsoft Store tile images from the app mascot.

    python scripts/build_appx_assets.py

electron-builder's appx target looks for these in ui/build/appx/ and quietly
substitutes generic Electron placeholders for any that are missing, which is
how an app ends up in the Store wearing someone else's icon.

The sizes are Microsoft's, and the two shapes need different treatment:

  * Square tiles get the mascot on a transparent background, because Windows
    draws them on the tile colour from `backgroundColor` in
    electron-builder.yml. Painting our own background there would show as a
    square patch in the wrong shade.
  * Wide310x150Logo and SplashScreen are letterboxed: the mascot is square, and
    stretching it to a 2:1 frame would distort it. It is centred at the frame's
    height instead, on transparency.

Regenerate whenever the mascot changes. The output is deterministic, so
re-running it with no source change produces byte-identical files.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "site" / "assets" / "mascot.png"
DEST = ROOT / "ui" / "build" / "appx"
STORE_ART = ROOT / "docs" / "store-art"

# The app's own background, so letterboxed art does not sit on a grey band.
BRAND_BG = (10, 22, 40)  # #0A1628, matching backgroundColor in electron-builder.yml

# name -> (width, height). Microsoft's required set for a desktop appx.
# These go INSIDE the package, and Windows draws them on the tile colour, so
# they keep a transparent background.
SIZES: dict[str, tuple[int, int]] = {
    "Square150x150Logo.png": (150, 150),  # medium Start tile
    "Square44x44Logo.png": (44, 44),  # taskbar and app list
    "StoreLogo.png": (50, 50),  # Store listing and installer
    "Wide310x150Logo.png": (310, 150),  # wide Start tile
    "SplashScreen.png": (620, 300),  # shown while the app starts
}

# Uploaded to Partner Center by hand, NOT part of the package. These are shown
# on the listing page against Microsoft's own backgrounds, so they get the app's
# background painted in rather than transparency.
#
# 1:1 box art is required. 2:3 poster art is what the Store uses in most of its
# browsing surfaces, so it is the one that decides whether anyone clicks.
STORE_SIZES: dict[str, tuple[int, int]] = {
    "box-art-1x1.png": (1080, 1080),
    "poster-art-2x3.png": (720, 1080),
    "super-hero-art-16x9.png": (1920, 1080),
}


def _font(size: int, bold: bool):
    """Segoe UI at `size`, falling back to whatever Pillow can find.

    Segoe UI because it is what Windows itself uses, so the art sits beside the
    Store's own chrome rather than against it. The fallback keeps the script
    working on a machine without it rather than failing the whole build for a
    typeface.
    """
    from PIL import ImageFont

    names = ["segoeuib.ttf", "seguibl.ttf"] if bold else ["segoeui.ttf"]
    for candidate in names:
        try:
            return ImageFont.truetype(str(Path("C:/Windows/Fonts") / candidate), size)
        except OSError:
            continue
    try:
        return ImageFont.truetype("arialbd.ttf" if bold else "arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _centre(draw, width: int, y: int, text: str, font, fill) -> None:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(((width - (right - left)) // 2 - left, y - top), text, font=font, fill=fill)


def main() -> int:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        sys.exit("Pillow is needed: pip install pillow")

    if not SOURCE.exists():
        sys.exit(f"No source image at {SOURCE.relative_to(ROOT)}")

    src = Image.open(SOURCE).convert("RGBA")
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"source: {SOURCE.relative_to(ROOT)}  {src.width}x{src.height}")

    for name, (w, h) in SIZES.items():
        if w == h:
            out = src.resize((w, h), Image.Resampling.LANCZOS)
        else:
            # Letterbox: scale to the frame height, centre horizontally.
            side = min(w, h)
            scaled = src.resize((side, side), Image.Resampling.LANCZOS)
            out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            out.paste(scaled, ((w - side) // 2, (h - side) // 2), scaled)

        target = DEST / name
        out.save(target, "PNG", optimize=True)
        print(f"  {name:24} {w}x{h}  {target.stat().st_size:>7,} bytes")

    print(f"\nWrote {len(SIZES)} files to {DEST.relative_to(ROOT)}")

    # ---- Store listing art -------------------------------------------------
    STORE_ART.mkdir(parents=True, exist_ok=True)
    print(f"\nStore listing art -> {STORE_ART.relative_to(ROOT)}")

    for name, (w, h) in STORE_SIZES.items():
        out = Image.new("RGBA", (w, h), (*BRAND_BG, 255))

        # Which shapes carry the name, and it is a rule rather than taste:
        #
        #   box art     - no. A tile, read small, with the name shown beside it.
        #   poster art  - yes. A browsing surface with no other text near it.
        #   hero art    - NO, and Partner Center enforces this: "Must not
        #                 include the product's title". It sits behind the
        #                 listing header, which already prints the name over
        #                 the top, so a wordmark here collides with it.
        wordmark = name == "poster-art-2x3.png"

        side = int(min(w, h) * (0.52 if wordmark else 0.72))
        scaled = src.resize((side, side), Image.Resampling.LANCZOS)
        # Sit the mascot above centre when there is text under it.
        top = (h - side) // 2 - (int(h * 0.09) if wordmark else 0)
        out.paste(scaled, ((w - side) // 2, top), scaled)

        if wordmark:
            draw = ImageDraw.Draw(out)
            title_px = int(min(w, h) * 0.105)
            sub_px = int(min(w, h) * 0.042)
            title_font = _font(title_px, bold=True)
            sub_font = _font(sub_px, bold=False)

            y = top + side + int(h * 0.035)
            _centre(draw, out.width, y, "Clips Studio", title_font, (255, 255, 255))
            y += int(title_px * 1.35)
            _centre(
                draw, out.width, y,
                "AI video clipping that runs on your PC",
                sub_font, (125, 175, 235),
            )

        target = STORE_ART / name
        out.convert("RGB").save(target, "PNG", optimize=True)
        print(f"  {name:24} {w}x{h}  {target.stat().st_size:>7,} bytes")

    print(
        f"\nWrote {len(STORE_SIZES)} files. Upload these on the Store listings page\n"
        "in Partner Center — 1:1 box art is required, 2:3 poster art is what the\n"
        "Store shows in most browsing surfaces."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
