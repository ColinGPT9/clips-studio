"""Fetch the two voice models into models/.

Not committed, for the same reason FFmpeg is not (see fetch_ffmpeg.py): a
binary in git is paid for by everyone who clones, forever. The installer
build fetches them and bundles them, so an installed copy needs no network.

    python scripts/fetch_voice_model.py

analysis/voice_turns.py uses the pair to tell a second voice from the main
one, so its captions can take another colour:

  - pyannote's segmentation-3.0 (MIT), as exported to ONNX for sherpa-onnx:
    who is talking when, ten seconds at a time.
  - WeSpeaker's VoxCeleb ResNet34-LM (CC BY 4.0): whose voice a turn is.

No account is needed to download either.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"

# (file, what it is, URL, SHA-256 as Hugging Face publishes it). Each URL is
# pinned to a commit, so the file behind the checksum can't change under it.
WANTED = [
    (
        "wespeaker_resnet34_lm.onnx",
        "WeSpeaker speaker model",
        "https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/"
        "f0c48c298fd835726c27956a5d617bad7115627e/voxceleb_resnet34_LM.onnx",
        "7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068",
    ),
    (
        "pyannote_segmentation_3.onnx",
        "pyannote segmentation model",
        "https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/"
        "9403a6902bb58e3d5ae8c7e77c3422de279db2e0/model.onnx",
        "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
    ),
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch(name: str, label: str, url: str, sha256: str) -> bool:
    dest = MODELS / name
    if dest.exists() and _sha256(dest.read_bytes()) == sha256:
        print(f"Already have {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return True

    print(f"Fetching the {label} from Hugging Face ...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "clips-studio"})
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read()
    except Exception as e:
        print(f"  failed: {e.__class__.__name__}: {str(e)[:80]}")
        return False
    if _sha256(data) != sha256:
        print("  the download doesn't match the published checksum, not keeping it")
        return False
    dest.write_bytes(data)
    print(f"  wrote {dest} ({len(data) / 1e6:.1f} MB)")
    return True


def main() -> int:
    MODELS.mkdir(parents=True, exist_ok=True)
    # Not all(): one failing must not stop the other being fetched.
    got = [_fetch(*wanted) for wanted in WANTED]
    if all(got):
        return 0
    print(
        "\nCould not fetch the voice models.\n"
        "Clipping still works without them: captions stay one colour, whoever\n"
        "is talking."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
