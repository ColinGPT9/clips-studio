"""Fetch the game-sound model (PANNs MobileNetV1) into models/.

Not committed, for the same reason FFmpeg is not (see fetch_ffmpeg.py): a
binary in git is paid for by everyone who clones, forever. The installer
build fetches it and bundles it, so an installed copy needs no network.

    python scripts/fetch_panns.py

The weights are Kong et al.'s PANNs (Zenodo record 3987831, CC BY 4.0),
used by analysis/panns.py to hear gunfire, explosions, a crowd and the
like in a gaming stream. No account is needed to download.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "models" / "panns_mobilenetv1.pth"

URL = "https://zenodo.org/records/3987831/files/MobileNetV1_mAP%3D0.389.pth?download=1"
MD5 = "a419303e1c88aa1b9d2ac3811563d371"  # as Zenodo publishes it


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def main() -> int:
    if DEST.exists() and _md5(DEST.read_bytes()) == MD5:
        print(f"Already have {DEST.name} ({DEST.stat().st_size / 1e6:.1f} MB)")
        return 0

    DEST.parent.mkdir(parents=True, exist_ok=True)
    print("Fetching PANNs MobileNetV1 from Zenodo ...")
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "clips-studio"})
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read()
    except Exception as e:
        print(f"  failed: {e.__class__.__name__}: {str(e)[:80]}")
        data = b""
    if data and _md5(data) == MD5:
        DEST.write_bytes(data)
        print(f"  wrote {DEST} ({len(data) / 1e6:.1f} MB)")
        return 0
    if data:
        print("  the download doesn't match the published checksum, not keeping it")

    print(
        "\nCould not fetch the game-sound model.\n"
        "Clipping still works without it: gaming streams are scored on chat and\n"
        "the streamer's voice, without hearing the game's own sound."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
