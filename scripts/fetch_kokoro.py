"""Fetch the built-in voice's two files into models/kokoro/.

Not committed, for the same reason FFmpeg is not (see fetch_ffmpeg.py): a
binary in git is paid for by everyone who clones, forever. The installer
build fetches them and bundles them, so an installed copy speaks with no
network and nothing is ever downloaded while the app runs.

    python scripts/fetch_kokoro.py

The files are the Kokoro-82M voice model (Apache-2.0) as the kokoro-onnx
project released it for ONNX Runtime, and its voice pack: about 354 MB
together. Each is pinned to a release and checked against its SHA-256
(voice/kokoro_files.py holds the pins); a download that does not match is
not kept. No account is needed.
"""

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voice.kokoro_files import FILES, sha256  # noqa: E402

DEST = ROOT / "models" / "kokoro"


def _fetch(name: str, label: str, url: str, want: str, size: int) -> bool:
    dest = DEST / name
    if dest.exists() and sha256(dest) == want:
        print(f"Already have {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return True

    print(f"Fetching the {label} ({size / 1e6:.0f} MB) ...")
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    got = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "clips-studio"})
        with urllib.request.urlopen(req, timeout=600) as r, part.open("wb") as f:
            # In blocks: the model is 325 MB, too much to hold in memory twice.
            for block in iter(lambda: r.read(1024 * 1024), b""):
                f.write(block)
                h.update(block)
                got += len(block)
    except Exception as e:
        print(f"  failed: {e.__class__.__name__}: {str(e)[:80]}")
        part.unlink(missing_ok=True)
        return False
    if h.hexdigest() != want:
        print("  the download doesn't match the pinned checksum, not keeping it")
        part.unlink(missing_ok=True)
        return False
    os.replace(part, dest)
    print(f"  wrote {dest} ({got / 1e6:.1f} MB)")
    return True


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    # Not all(): one failing must not stop the other being fetched.
    got = [_fetch(*wanted) for wanted in FILES]
    if all(got):
        return 0
    print(
        "\nCould not fetch the built-in voice.\n"
        "Clipping still works without it. Voice features say the voice files\n"
        "are missing, and dubbing keeps using its other voices."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
