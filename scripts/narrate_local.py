"""Optional host-side, offline Kokoro narration. No cloud account or plugin access."""
from __future__ import annotations

import argparse
import hashlib
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

MODEL_SHA256 = "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"
VOICES_SHA256 = "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text-file", type=Path)
    source.add_argument("--scenes", type=Path, help="JSON array of id/narration scene objects")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--speed", type=float, default=1.03)
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
    model = args.model_dir / "kokoro-v1.0.onnx"
    voices = args.model_dir / "voices-v1.0.bin"
    if not model.is_file() or not voices.is_file():
        parser.error("Download the model-files-v1.1 assets first; see docs/LOCAL_NARRATION.md.")
    model_hash, voices_hash = digest(model), digest(voices)
    if model_hash != MODEL_SHA256 or voices_hash != VOICES_SHA256:
        parser.error("Model assets do not match the documented release digests.")
    # Optional dependencies are imported only for an explicit narration request.
    import numpy as np
    import onnxruntime as runtime
    import soundfile as sf
    from kokoro_onnx import Kokoro

    options = runtime.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    session = runtime.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
    engine = Kokoro.from_session(session, str(voices))
    if args.voice not in engine.voices:
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
        if cache.get(identifier) != request or not output.is_file():
            samples, rate = engine.create(text, voice=args.voice, speed=args.speed, lang="en-us")
            if not len(samples) or not np.isfinite(samples).all() or np.max(np.abs(samples)) < 0.001:
                raise RuntimeError("Narration produced invalid or silent audio.")
            fd, name = tempfile.mkstemp(prefix=".narration-", suffix=".wav", dir=args.output_dir)
            os.close(fd)
            temporary = Path(name)
            try:
                sf.write(temporary, samples, rate, format="WAV", subtype="PCM_16")
                temporary.replace(output)
            finally:
                discard(temporary)
            cache[identifier] = request
            cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        info = sf.info(output)
        results.append({"id": identifier, "path": str(output), "duration_seconds": info.duration,
                        "sha256": digest(output)})
        print(identifier, round(info.duration, 2), "seconds", flush=True)
    manifest = {"engine": "Kokoro-82M / kokoro-onnx", "device": "CPU", "voice": args.voice,
                "speed": args.speed, "cloud_requests": 0, "api_key_required": False,
                "model_license": "Apache-2.0", "runtime_license": "MIT", "model_sha256": model_hash,
                "voices_sha256": voices_hash, "scenes": results}
    (args.output_dir / "narration-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
