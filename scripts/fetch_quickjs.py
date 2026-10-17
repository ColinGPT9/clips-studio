"""Download the QuickJS-ng runtime the installer ships, for the YouTube cookies
fallback (Settings → Advanced settings → YouTube cookies).

Signed in, YouTube's players need their JavaScript run before a video can be
downloaded: yt-dlp does that with Deno, Node 22+, Bun or QuickJS, and a creator
has none of them (the app's own Electron carries Node 20). QuickJS-ng is one
2 MB program. Only the cookies request uses it (sources/ytdlp_common.py); every
other download is made exactly as before. This drops qjs.exe into
vendor/quickjs/, where core.binaries.quickjs looks for it and the packaging
step picks it up.

    python scripts/fetch_quickjs.py            # fetch if missing
    python scripts/fetch_quickjs.py --force    # re-download

The binary is NOT committed: it is a build input, fetched on the machine that
builds the installer, pinned and checked against its published SHA-256.

QuickJS-ng is MIT-licensed; vendor/quickjs/LICENSE-QUICKJS.txt is written next
to the binary and the installer ships it.
"""

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "vendor" / "quickjs"

# Pinned so a build is reproducible. Bump deliberately, with the SHA-256 the
# release page publishes for the asset.
VERSION = "v0.17.0"
BUILD_URL = f"https://github.com/quickjs-ng/quickjs/releases/download/{VERSION}/qjs-windows-x86_64.exe"
SHA256 = "2aeabf0092c3262d6b2609824418f7dd7ed1f1df939f73b2b15645230cac0d77"
SOURCE_URL = "https://github.com/quickjs-ng/quickjs"

NOTICE = f"""QuickJS-ng {VERSION}
------------------
This application bundles an unmodified QuickJS-ng binary (qjs.exe), used only
to run YouTube's player JavaScript when a download is made with the YouTube
cookies set in Settings.

Source: {SOURCE_URL}
Binary: {BUILD_URL}

The MIT License

Copyright (c) 2017-2024 Fabrice Bellard
Copyright (c) 2017-2024 Charlie Gordon
Copyright (c) 2023-2025 Ben Noordhuis and Saúl Ibarra Corretgé

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""


def fetch(force: bool = False) -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    target = DEST / "qjs.exe"
    if target.exists() and not force:
        print(f"  present: {target} ({target.stat().st_size / 1e6:.1f} MB)")
        print("Already vendored — use --force to re-download.")
        return 0
    print(f"Downloading QuickJS-ng {VERSION}")
    print(f"  {BUILD_URL}")
    try:
        with urllib.request.urlopen(BUILD_URL, timeout=120) as r:
            blob = r.read()
    except Exception as e:
        print(f"\nDownload failed: {type(e).__name__}: {e}")
        print(f"Fetch it manually and save it as:\n  {target}")
        return 1
    digest = hashlib.sha256(blob).hexdigest()
    if digest != SHA256:
        print(f"\nSHA-256 mismatch: got {digest}, expected {SHA256}. Not saved.")
        return 1
    target.write_bytes(blob)
    (DEST / "LICENSE-QUICKJS.txt").write_text(NOTICE, encoding="utf-8")
    print(f"  saved {target} ({len(blob) / 1e6:.1f} MB), SHA-256 checked")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    return fetch(ap.parse_args().force)


if __name__ == "__main__":
    sys.exit(main())
