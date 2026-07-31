"""Fetch the TalkNet active-speaker weights into models/.

Not committed, for the same reason FFmpeg is not (see fetch_ffmpeg.py): a
63 MB binary in git is paid for by everyone who clones, forever, including
people who only wanted to fix a typo. The installer build fetches it and
bundles it, so an installed copy needs no network for this.

    python scripts/fetch_asd_model.py

The model is TalkNet-ASD by Tao Ruijie (MIT). The upstream repository serves
its weights from Google Drive, which cannot be scripted reliably, so this
pulls from a public mirror. No account is needed to download.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "models" / "pretrain_TalkSet.model"

MIRRORS = [
    "https://huggingface.co/Kestroll/talknet-asd/resolve/main/pretrain_TalkSet.model",
    "https://huggingface.co/AlekseyKorshuk/talknet-asd/resolve/main/pretrain_TalkSet.model",
]
MIN_BYTES = 50_000_000  # a truncated download is worse than none: it loads and
                        # then fails deep inside a render


def main() -> int:
    if DEST.exists() and DEST.stat().st_size >= MIN_BYTES:
        print(f"Already have {DEST.name} ({DEST.stat().st_size / 1e6:.1f} MB)")
        return 0

    DEST.parent.mkdir(parents=True, exist_ok=True)
    for url in MIRRORS:
        print(f"Fetching {url.split('/')[3]}/{url.split('/')[4]} ...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "clips-studio"})
            with urllib.request.urlopen(req, timeout=600) as r:
                data = r.read()
        except Exception as e:
            print(f"  failed: {e.__class__.__name__}: {str(e)[:80]}")
            continue
        if len(data) < MIN_BYTES:
            print(f"  too small ({len(data) / 1e6:.1f} MB) — treating as a bad download")
            continue
        DEST.write_bytes(data)
        print(f"  wrote {DEST} ({len(data) / 1e6:.1f} MB)")
        print(f"  sha256 {hashlib.sha256(data).hexdigest()[:16]}…")
        return 0

    print(
        "\nCould not fetch the speaker-detection model.\n"
        "Clipping still works without it — the tracker falls back to mouth-motion,\n"
        "which follows the most prominent person when two people are on screen."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
