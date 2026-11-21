#!/usr/bin/env python3
"""Fail CI when the website's Windows download buttons point at an old release.

The static site is served independently of the Windows installer. A release
version bump must update every download button; relying on GitHub's
/releases/latest/ URL is not safe while releases are marked as prereleases.
This check uses only the standard library and performs no network requests.
"""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_RE = re.compile(r"\bDownload\s+for\s+Windows\b", flags=re.IGNORECASE)
RELEASE_URL = (
    "https://github.com/ColinGPT9/clips-studio/releases/download/"
    "v{version}/ClipsKitty-Web-Setup-{version}.exe"
)


class DownloadLinks(HTMLParser):
    """Collect download anchor labels and hrefs, including nested text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._anchor = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            self._anchor = {"href": href, "line": self.getpos()[0], "text": []}

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            label = " ".join("".join(self._anchor["text"]).split())
            self.links.append((self._anchor["line"], label, self._anchor["href"]))
            self._anchor = None


def check_site(site_dir: Path, version: str) -> tuple[int, list[str]]:
    """Return (count checked, failures) for all download buttons in site HTML."""
    expected = RELEASE_URL.format(version=version)
    issues = []
    checked = 0
    pages = sorted(site_dir.glob("*.html"))
    if not pages:
        return 0, [f"{site_dir}: no HTML pages found"]

    for page in pages:
        parser = DownloadLinks()
        parser.feed(page.read_text(encoding="utf-8"))
        parser.close()
        for line, label, url in parser.links:
            if not DOWNLOAD_RE.search(label):
                continue
            checked += 1
            if url != expected:
                issues.append(
                    f"{page}:{line}: '{label}' points to {url!r}; expected {expected!r}"
                )
    if checked == 0:
        issues.append(f"{site_dir}: no 'Download for Windows' links found")
    return checked, issues


def main() -> int:
    version = json.loads((ROOT / "ui/package.json").read_text(encoding="utf-8"))["version"]
    count, issues = check_site(ROOT / "site", version)
    print(f"Checked {count} Windows download link(s) against ui/package.json ({version})")
    for issue in issues:
        print(f"ERROR: {issue}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
