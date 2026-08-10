"""Regenerate site/sitemap.xml from the pages that actually exist.

    python scripts/build_sitemap.py            # rewrite the sitemap
    python scripts/build_sitemap.py --check    # fail if it is out of date (CI)

Two things a hand-written sitemap gets wrong, both silently. Pages get added
and never listed — roadmap.html and changelog.html were missing the day they
shipped. And `lastmod` rots: a crawler uses it to decide what is worth
re-reading, so a date that never changes is worse than no date, because it
actively says "nothing here has changed".

The dates come from git rather than the filesystem. A fresh clone has today's
mtime on every file, which would tell Google the whole site changed at once.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
SITEMAP = SITE / "sitemap.xml"
BASE = "https://colingpt9.github.io/clips-studio/"

# Home first, then the pages that bring people in from search, then the ones
# they read once they are here.
PRIORITY = {
    "index.html": "1.0",
    "twitch.html": "0.8",
    "kick.html": "0.8",
    "youtube.html": "0.8",
    "local-vs-cloud.html": "0.7",
}
DEFAULT_PRIORITY = "0.6"


def last_changed(path: Path) -> str:
    """The file's last commit date, or today if it is not committed yet."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", str(path.relative_to(ROOT))],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        date = out.stdout.strip()
        if date:
            return date
    except (OSError, subprocess.TimeoutExpired):
        pass
    from datetime import date as _date
    return _date.today().isoformat()


def build() -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    # index.html is the site root: listing it by filename as well would be two
    # urls for one page, which is a duplicate Google has to resolve.
    for page in sorted(SITE.glob("*.html"), key=lambda p: (p.name != "index.html", p.name)):
        loc = BASE if page.name == "index.html" else BASE + page.name
        lines += ["  <url>",
                  f"    <loc>{loc}</loc>",
                  f"    <lastmod>{last_changed(page)}</lastmod>",
                  f"    <priority>{PRIORITY.get(page.name, DEFAULT_PRIORITY)}</priority>",
                  "  </url>"]
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the sitemap is stale, and change nothing")
    args = ap.parse_args()

    wanted = build()
    current = SITEMAP.read_text(encoding="utf-8") if SITEMAP.exists() else ""

    if args.check:
        if wanted != current:
            print("sitemap.xml is out of date — run: python scripts/build_sitemap.py")
            return 1
        print(f"sitemap.xml is current ({len(list(SITE.glob('*.html')))} pages)")
        return 0

    if wanted == current:
        print("sitemap.xml already current")
        return 0
    SITEMAP.write_text(wanted, encoding="utf-8", newline="\n")
    print(f"wrote {SITEMAP.relative_to(ROOT)} ({len(list(SITE.glob('*.html')))} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
