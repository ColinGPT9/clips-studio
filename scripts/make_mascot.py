"""Write the Clips Kitty mascot PNGs and the Windows icon.

    python scripts/make_mascot.py

Outputs:
    docs/brand/mascot.png        full body — README, website, merch base
    docs/brand/mascot-head.png   head only — what the icon is cut from
    ui/build/icon.ico            multi-size Windows icon for the installer

The artwork itself lives in `video/mascot_art.py` — edit the numbers there and
re-run this. It moved out of here because `scripts/` is not packaged into the
frozen build, and `video/outro.py` needs the same geometry at runtime to
animate Clippy for the end card. One source, three consumers.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video.mascot_art import render_full, render_head  # noqa: E402

BRAND = ROOT / "docs" / "brand"
UI_BUILD = ROOT / "ui" / "build"


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
