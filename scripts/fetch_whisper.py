"""Download the Whisper weights the installer ships.

The quietest missing dependency in the app. Everything else announces itself:
no FFmpeg and the preflight says so, no Ollama and the setup wizard stops. But
faster-whisper takes a bare size name to mean "fetch it from Hugging Face",
so an installed copy with no weights looked completely healthy right up until
someone clipped their first video — at which point it sat there pulling 1.6 GB
with no progress bar, no explanation, and no way to tell it apart from a hang.

So the weights ship with the app. This fetches them into vendor/whisper/,
where core.binaries finds them and the packaging step picks them up.

    python scripts/fetch_whisper.py            # fetch if missing
    python scripts/fetch_whisper.py --force    # re-download

Two sizes, because `whisper.model: auto` in settings.yaml picks between them:
large-v3-turbo on a GPU, small on CPU where turbo is far too slow to be worth
its accuracy. Shipping one of the two would leave half of all installs
downloading the other one silently, which is the bug being fixed.

The weights are NOT committed -- they're build inputs, fetched on the machine
that builds the installer.

Licensing note: both are MIT, converted to CTranslate2 format from OpenAI's
Whisper (also MIT). Nothing here needs a click-through, which is exactly why
these can be bundled when a language model cannot.
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "vendor" / "whisper"

# Must match the sizes transcription/transcriber.py can select. Keep in step
# with the `auto` branches there: a size it can pick and this does not fetch
# is a silent runtime download, which is the whole problem.
WANTED = ("small", "large-v3-turbo")

# faster-whisper writes several files per model; this is the big one, and its
# presence is what proves a download finished rather than died halfway.
MARKER = "model.bin"

NOTICE = """Whisper models
--------------
This application bundles CTranslate2 conversions of OpenAI's Whisper models:

    small             Systran/faster-whisper-small
    large-v3-turbo    mobiuslabsgmbh/faster-whisper-large-v3-turbo

Whisper is released by OpenAI under the MIT License, and both conversions are
distributed under the MIT License. The models are used unmodified.

    https://github.com/openai/whisper
    https://huggingface.co/Systran/faster-whisper-small
    https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo
"""


def _size_mb(folder: Path) -> float:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1e6


def fetch(force: bool = False) -> int:
    try:
        # Imported here rather than at the top so --help still works on a
        # machine that has not installed the app's dependencies.
        from faster_whisper.utils import download_model
    except ImportError:
        print("faster-whisper is not installed, so its own downloader is unavailable.")
        print("Run: pip install -r requirements.txt")
        return 1

    DEST.mkdir(parents=True, exist_ok=True)

    for size in WANTED:
        target = DEST / size
        if (target / MARKER).exists() and not force:
            print(f"  present: {size} ({_size_mb(target):.0f} MB)")
            continue

        if target.exists():
            # A partial download from an interrupted run. Left in place it
            # would be picked up as valid the moment model.bin happened to
            # exist, and fail at load time instead of here.
            shutil.rmtree(target, ignore_errors=True)

        print(f"Downloading Whisper '{size}' — this is gigabytes, give it a while")
        try:
            # faster-whisper's own downloader, so the size -> repository
            # mapping cannot drift from what the app will ask for at runtime.
            download_model(size, output_dir=str(target))
        except Exception as e:
            print(f"\nDownload failed for '{size}': {type(e).__name__}: {e}")
            shutil.rmtree(target, ignore_errors=True)
            return 1

        if not (target / MARKER).exists():
            print(f"\n'{size}' downloaded without a {MARKER} — conversion layout changed?")
            return 1
        print(f"  got {size} ({_size_mb(target):.0f} MB)")

    (DEST / "README-WHISPER.txt").write_text(NOTICE, encoding="utf-8")
    print(f"  wrote {DEST / 'README-WHISPER.txt'}")
    print(f"Vendored {_size_mb(DEST):.0f} MB total in {DEST}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    sys.exit(fetch(ap.parse_args().force))
