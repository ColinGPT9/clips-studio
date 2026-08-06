"""Download the Ollama runtime the installer ships.

Ollama runs the language model that scores and titles clips. It used to be the
creator's job: the setup wizard stopped and asked them to go to ollama.com,
download a second installer, run it, and come back. That is the single biggest
thing standing between "downloaded Clips Studio" and "made a clip", and it is
the kind of errand people abandon.

So the app carries its own copy. This fetches the standalone Windows build --
a zip, not the ollama.com installer -- and drops it into vendor/ollama/, where
core.binaries looks for it and the packaging step picks it up. The standalone
build matters: it needs no admin rights, edits no PATH, registers no service,
and leaves any Ollama the creator already installed completely alone.

    python scripts/fetch_ollama.py            # fetch if missing
    python scripts/fetch_ollama.py --force    # re-download

The binaries are NOT committed -- they're build inputs, fetched on the machine
that builds the installer.

Licensing note: Ollama is MIT, so shipping it unmodified is straightforward.
MIT still requires the copyright notice and licence text travel with it, so
vendor/ollama/README-OLLAMA.txt is written next to the binaries and the
installer ships it.
"""

import argparse
import io
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "vendor" / "ollama"

# Pinned so a build is reproducible and a surprise upstream change can't
# silently alter what ships. Bump deliberately -- a runtime upgrade can change
# GPU behaviour and model compatibility, which is not something to discover
# from a build that happened to run on a Tuesday.
OLLAMA_VERSION = "v0.12.6"
RELEASES_API = "https://api.github.com/repos/ollama/ollama/releases/latest"
ASSET = "ollama-windows-amd64.zip"
BUILD_URL = f"https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}/{ASSET}"

# Records which version is sitting in vendor/ollama/, so bumping the constant
# above re-downloads instead of silently keeping the old runtime.
STAMP = "VERSION.txt"

NOTICE = f"""Ollama
------
This application bundles an unmodified Ollama runtime ({OLLAMA_VERSION},
{ASSET}).

Ollama is free software licensed under the MIT License. Clips Studio starts
it as a separate program on a private port and does not link against it.

Source code and releases:
    https://github.com/ollama/ollama
    {BUILD_URL}

---

MIT License

Copyright (c) Ollama

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def _latest_tag() -> str | None:
    """The newest published Ollama tag, for the error message only.

    A pinned URL that 404s means the release was retagged or withdrawn, and
    the useful thing to say is which version to pin instead -- not just that
    a download failed.
    """
    try:
        req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("tag_name")
    except Exception:
        return None


def _installed_version() -> str | None:
    stamp = DEST / STAMP
    try:
        return stamp.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def fetch(force: bool = False) -> int:
    exe = DEST / "ollama.exe"
    if exe.exists() and _installed_version() == OLLAMA_VERSION and not force:
        total = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())
        print(f"  present: {exe} ({OLLAMA_VERSION}, {total / 1e6:.0f} MB total)")
        print("Already vendored — use --force to re-download.")
        return 0

    if exe.exists():
        # A version bump, not a fresh fetch. The old runner libraries under
        # lib/ are version-matched to the executable, so leaving them in place
        # would mix two builds together.
        print(f"Replacing {_installed_version() or 'an unstamped build'} with {OLLAMA_VERSION}")
        shutil.rmtree(DEST, ignore_errors=True)

    DEST.mkdir(parents=True, exist_ok=True)

    print(f"Downloading Ollama {OLLAMA_VERSION}")
    print(f"  {BUILD_URL}")
    try:
        with urllib.request.urlopen(BUILD_URL, timeout=600) as r:
            blob = r.read()
    except Exception as e:
        print(f"\nDownload failed: {type(e).__name__}: {e}")
        latest = _latest_tag()
        if latest and latest != OLLAMA_VERSION:
            print(f"The newest published release is {latest}.")
            print(f"If {OLLAMA_VERSION} was withdrawn, set OLLAMA_VERSION to {latest} in this file.")
        print("Or fetch it by hand and unzip it into:")
        print(f"  {DEST}")
        return 1
    print(f"  got {len(blob) / 1e6:.0f} MB")

    # Extract everything, not a chosen few files: ollama.exe is useless on its
    # own. The GPU runners and the CUDA libraries beside it under lib/ are what
    # make it faster than CPU inference, and they must keep their layout.
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        z.extractall(DEST)

    if not exe.exists():
        print(f"\n{ASSET} did not contain ollama.exe — archive layout changed?")
        return 1

    (DEST / STAMP).write_text(OLLAMA_VERSION, encoding="utf-8")
    (DEST / "README-OLLAMA.txt").write_text(NOTICE, encoding="utf-8")
    total = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())
    print(f"  extracted {total / 1e6:.0f} MB to {DEST}")
    print(f"  wrote {DEST / 'README-OLLAMA.txt'}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    sys.exit(fetch(ap.parse_args().force))
