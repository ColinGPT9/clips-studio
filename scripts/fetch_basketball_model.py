"""Fetch the basketball detector (E-BARD's YOLOv8n) into models/.

Not committed, for the same reason the other weights are not (see
fetch_panns.py): a binary in git is paid for by everyone who clones, forever.
The installer build fetches it and bundles it, so an installed copy needs no
network.

    python scripts/fetch_basketball_model.py

The weights are Gabriele Giudici's E-BARD detection models (Hugging Face
GabrieleGiudici/E-BARD-detection-models, CC BY 4.0): a YOLOv8n fine-tuned on
1,800 frames of NBA broadcasts with the classes basketball, hoop, player and
referee. sports/basketball/crop/sources.py uses them to find the ball and the
rim for the 9:16 framing; without them it falls back to the COCO model and
the rim finder. Pinned to one revision and checked against its SHA-256, so
the file can't change under a build. No account is needed to download.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "BODD_yolov8n_0001.pt"
DEST = ROOT / "models" / NAME

REPO = "GabrieleGiudici/E-BARD-detection-models"
REVISION = "3f4789c4431aa73269f60107a4ba0a5f86b7af8b"
URL = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{NAME}"
SHA256 = "dfe3534d51bb21024d1a400c37f0c1fbf0c8b96ea9a56a5f3cb5454813bfd641"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    if DEST.exists() and _sha256(DEST.read_bytes()) == SHA256:
        print(f"Already have {DEST.name} ({DEST.stat().st_size / 1e6:.1f} MB)")
        return 0

    DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"Fetching the basketball detector ({REPO} at {REVISION[:7]}) ...")
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "clips-studio"})
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read()
    except OSError as e:
        print(f"Could not download it: {e}")
        return 1
    got = _sha256(data)
    if got != SHA256:
        print(f"Checksum mismatch: got {got}, expected {SHA256}. Not saved.")
        return 1
    tmp = DEST.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(DEST)
    print(f"Saved {DEST} ({len(data) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
