"""Offline narration from the command line, with the built-in Kokoro voice.

Speaks a text file or a list of narrated scenes to WAV files through the
app's voice layer (voice/engines/kokoro.py): on the CPU, with no cloud
account, no plugin access and nothing downloaded while it runs.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.paths import discard  # noqa: E402
from voice import kokoro_files  # noqa: E402
from voice.kokoro_files import MODEL_SHA256, VOICES_SHA256  # noqa: E402

digest = kokoro_files.sha256


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text-file", type=Path)
    source.add_argument("--scenes", type=Path, help="JSON array of id/narration scene objects")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model-dir", type=Path, default=ROOT / "models" / "kokoro",
                        help="Folder with the two voice files (default: models/kokoro, where "
                             "scripts/fetch_kokoro.py puts them)")
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--speed", type=float, default=1.03)
    parser.add_argument("--language", default="en", help="en, es, fr, hi, it or pt")
    parser.add_argument("--seed", type=int, default=None,
                        help="The same seed gives the same WAV bytes on the same PC")
    args = parser.parse_args()
    if not math.isfinite(args.speed) or not 0.5 <= args.speed <= 2:
        parser.error("Speed must be between 0.5 and 2.")
    if args.text_file:
        scenes = [{"id": "narration", "narration": args.text_file.read_text(encoding="utf-8")}]
    else:
        scenes = json.loads(args.scenes.read_text(encoding="utf-8"))
    if not isinstance(scenes, list):
        parser.error("Scenes must be a JSON array.")
    spoken = []
    identifiers = set()
    for scene in scenes:
        if not isinstance(scene, dict):
            parser.error("Every scene must be an object.")
        if not scene.get("narration"):
            continue
        identifier = scene.get("id")
        text = scene["narration"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", identifier):
            parser.error("Scene IDs must be simple filenames.")
        if identifier in identifiers or not isinstance(text, str) or not text.strip():
            parser.error("Use unique IDs and nonempty narration text.")
        identifiers.add(identifier)
        spoken.append((identifier, text))
    if not spoken:
        parser.error("No narration text was supplied.")
    model = args.model_dir / kokoro_files.MODEL_FILE
    voices = args.model_dir / kokoro_files.VOICES_FILE
    if not model.is_file() or not voices.is_file():
        parser.error("The voice files are missing. Run: python scripts/fetch_kokoro.py "
                     "(see docs/LOCAL_NARRATION.md).")
    model_hash, voices_hash = digest(model), digest(voices)
    if model_hash != MODEL_SHA256 or voices_hash != VOICES_SHA256:
        parser.error("Model assets do not match the documented release digests.")
    # The voice layer loads the files from the folder named here, after
    # checking the same digests itself.
    os.environ["CLIPS_STUDIO_KOKORO_DIR"] = str(args.model_dir)
    from voice import VoiceError, wav
    from voice.engines import kokoro

    if not kokoro.installed():
        parser.error("kokoro-onnx is not installed. Run: pip install -r requirements.txt")
    if not kokoro.speaks(args.language):
        parser.error("The built-in voice speaks: " + ", ".join(sorted(kokoro.LANGUAGES)) + ".")
    if f"kokoro:{args.voice}" not in {v["id"] for v in kokoro.voices()}:
        parser.error("The requested voice is not in this local voice pack.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = args.output_dir / "narration-cache.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    results = []
    for identifier, text in spoken:
        output = args.output_dir / (identifier + ".wav")
        request = {"text": text, "voice": args.voice, "speed": args.speed,
                   "model": model_hash, "voices": voices_hash, "runtime": "kokoro-onnx-0.6.1"}
        if args.language != "en":
            request["language"] = args.language
        if args.seed is not None:
            request["seed"] = args.seed
        if cache.get(identifier) != request or not output.is_file():
            fd, name = tempfile.mkstemp(prefix=".narration-", suffix=".wav", dir=args.output_dir)
            os.close(fd)
            temporary = Path(name)
            try:
                try:
                    kokoro.speak(text, args.language, args.voice, temporary, speed=args.speed, seed=args.seed)
                except VoiceError as e:
                    raise RuntimeError(f"Narration failed: {e}") from e
                temporary.replace(output)
            finally:
                discard(temporary)
            cache[identifier] = request
            cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        _rate, duration = wav.info(output)
        results.append({"id": identifier, "path": str(output), "duration_seconds": duration,
                        "sha256": digest(output)})
        print(identifier, round(duration, 2), "seconds", flush=True)
    manifest = {"engine": "Kokoro-82M / kokoro-onnx", "device": "CPU", "voice": args.voice,
                "speed": args.speed, "cloud_requests": 0, "api_key_required": False,
                "model_license": "Apache-2.0", "runtime_license": "MIT", "model_sha256": model_hash,
                "voices_sha256": voices_hash, "scenes": results}
    (args.output_dir / "narration-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
