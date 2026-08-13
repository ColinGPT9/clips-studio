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

# name -> (width, height). Microsoft's required set for a desktop appx.
SIZES: dict[str, tuple[int, int]] = {
    "Square150x150Logo.png": (150, 150),  # medium Start tile
    "Square44x44Logo.png": (44, 44),  # taskbar and app list
    "StoreLogo.png": (50, 50),  # Store listing and installer
    "Wide310x150Logo.png": (310, 150),  # wide Start tile
    "SplashScreen.png": (620, 300),  # shown while the app starts
}


def main() -> int:
    try:
        from PIL import Image
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
