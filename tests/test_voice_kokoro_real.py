"""Editor suite step V1: the built-in voice for real, and what a plain
clipping run must never load.

The first half needs the kokoro-onnx runtime and the two voice files
(python scripts/fetch_kokoro.py), and the dub needs FFmpeg; each test skips
cleanly without them, so CI's light install runs only the second half.
Nothing is downloaded, and no test compares against a recording made on one
machine: the same seed is compared with itself, in this process.
"""

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SENTENCES = {
    "en": "Clips Kitty turns long videos into short clips.",
    "es": "Hola, así va a sonar tu vídeo en español.",
    "fr": "Bonjour, voici comment votre vidéo va sonner.",
    "hi": "नमस्ते, आपका वीडियो ऐसा सुनाई देगा।",
    "it": "Ciao, ecco come suonerà il tuo video.",
    "pt": "Olá, é assim que o seu vídeo vai soar.",
}


@pytest.fixture
def kokoro():
    pytest.importorskip("numpy")
    pytest.importorskip("onnxruntime")
    pytest.importorskip("kokoro_onnx")
    from voice import kokoro_files
    from voice.engines import kokoro as engine

    folder, why = kokoro_files.check()
    if folder is None:
        pytest.skip(f"the built-in voice's files are not here ({why})")
    return engine


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_the_same_seed_gives_the_same_bytes(kokoro, tmp_path):
    from voice import wav

    a, b, c = tmp_path / "a.wav", tmp_path / "b.wav", tmp_path / "c.wav"
    assert kokoro.speak(SENTENCES["en"], "en", "af_heart", a, seed=7) == 24000
    kokoro.speak(SENTENCES["en"], "en", "af_heart", b, seed=7)
    kokoro.speak(SENTENCES["en"], "en", "af_heart", c, seed=8)
    assert _sha(a) == _sha(b), "the same text, voice and seed gave different sound"
    assert _sha(a) != _sha(c), "another seed gave the same sound: the seed is not reaching the model"
    rate, seconds = wav.info(a)
    assert rate == 24000 and 1.5 < seconds < 8.0


@pytest.mark.parametrize("language", sorted(SENTENCES))
def test_every_offered_language_speaks_with_its_own_default_voice(kokoro, tmp_path, language):
    from voice import wav

    assert kokoro.speaks(language)
    offered = kokoro.voices(language)
    assert offered and offered[0]["voice"] == kokoro.default_voice(language)
    assert all(v["language"] == language and v["commercial_ok"] is True for v in offered)
    out = tmp_path / f"{language}.wav"
    kokoro.speak(SENTENCES[language], language, None, out)
    _rate, seconds = wav.info(out)
    assert 1.0 < seconds < 12.0


def test_japanese_and_chinese_are_not_offered(kokoro, tmp_path):
    """Measured: through espeak-ng they are not understandable, so their
    voices in the pack are never listed or spoken."""
    from voice.base import VoiceError

    assert not kokoro.speaks("ja") and not kokoro.speaks("zh")
    assert not kokoro.voices("ja") and not kokoro.voices("zh")
    ids = {v["voice"] for v in kokoro.voices()}
    assert not any(name[:1] in ("j", "z") for name in ids)
    with pytest.raises(VoiceError):
        kokoro.speak("こんにちは", "ja", "jf_alpha", tmp_path / "ja.wav")
    with pytest.raises(VoiceError):
        kokoro.speak("Hello.", "en", "jf_alpha", tmp_path / "en.wav")


def test_a_faster_pace_gives_a_shorter_take(kokoro, tmp_path):
    from voice import wav

    kokoro.speak(SENTENCES["en"], "en", "af_heart", tmp_path / "slow.wav", speed=0.8, seed=3)
    kokoro.speak(SENTENCES["en"], "en", "af_heart", tmp_path / "fast.wav", speed=1.4, seed=3)
    assert wav.info(tmp_path / "fast.wav")[1] < wav.info(tmp_path / "slow.wav")[1]


def test_the_narration_script_writes_what_it_always_did(kokoro, tmp_path):
    """scripts/narrate_local.py through the voice layer: a WAV per scene, the
    cache, and a manifest with the same fields."""
    from voice import kokoro_files

    scenes = tmp_path / "scenes.json"
    scenes.write_text(json.dumps([{"id": "intro", "narration": "Welcome to the show."},
                                  {"id": "skipped"},
                                  {"id": "outro", "narration": "Thanks for watching."}]), encoding="utf-8")
    out = tmp_path / "narration"
    cmd = [sys.executable, str(ROOT / "scripts" / "narrate_local.py"), "--scenes", str(scenes),
           "--output-dir", str(out), "--model-dir", str(kokoro_files.find()), "--seed", "5"]
    first = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(tmp_path))
    assert first.returncode == 0, first.stderr[-2000:]
    manifest = json.loads((out / "narration-manifest.json").read_text(encoding="utf-8"))
    assert {"engine", "device", "voice", "speed", "cloud_requests", "api_key_required", "model_license",
            "runtime_license", "model_sha256"} <= set(manifest)
    assert manifest["voice"] == "af_heart" and manifest["cloud_requests"] == 0
    assert manifest["model_sha256"] == kokoro_files.MODEL_SHA256
    hashes = {name: _sha(out / name) for name in ("intro.wav", "outro.wav")}
    assert (out / "narration-cache.json").is_file() and not (out / "skipped.wav").exists()
    # A second run with nothing changed speaks nothing again.
    stamp = (out / "intro.wav").stat().st_mtime_ns
    again = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(tmp_path))
    assert again.returncode == 0, again.stderr[-2000:]
    assert (out / "intro.wav").stat().st_mtime_ns == stamp
    assert {name: _sha(out / name) for name in hashes} == hashes


def test_a_real_clip_is_dubbed_with_the_built_in_voice(kokoro, tmp_path, monkeypatch):
    """Speech is in the dub, in each sentence's slot, and the clip keeps its
    length. Needs FFmpeg with libx264."""
    import shutil

    from core.binaries import ffmpeg, ffprobe
    from multilingual import dub

    exe = ffmpeg()
    if not (Path(exe).is_file() or shutil.which(exe)):
        pytest.skip("needs FFmpeg")
    clip = tmp_path / "clip.mp4"
    made = subprocess.run(
        [exe, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30:duration=10",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "10", "-c:v", "libx264", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("this FFmpeg could not make the test clip")
    lines = [
        {"start": 0.5, "end": 3.0, "text": "Hola a todos, bienvenidos al canal."},
        {"start": 3.5, "end": 6.0, "text": "Hoy vamos a ver algo completamente nuevo."},
        {"start": 6.5, "end": 9.5, "text": "¿Listos? Empecemos ahora mismo."},
    ]
    data = tmp_path / "data"
    out = dub.dub(lines, "es", clip, tmp_path / "clip.es.dubbed.mp4", tmp_path / "voices", tmp_path / "work",
                  workers=1, data_dir=data)
    assert out is not None and out.is_file(), "the built-in voice made no dub"
    probe = subprocess.run([ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(out)],
                           capture_output=True, text=True)
    assert abs(float(json.loads(probe.stdout)["format"]["duration"]) - 10.0) < 0.3

    def loudness(start: float, end: float) -> float:
        r = subprocess.run([exe, "-v", "info", "-nostats", "-ss", str(start), "-t", str(end - start), "-i",
                            str(out), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True)
        found = [ln for ln in r.stderr.splitlines() if "mean_volume" in ln]
        assert found, r.stderr[-500:]
        return float(found[-1].split("mean_volume:")[1].split("dB")[0])

    # Each sentence is heard inside its slot; before the first one there is
    # only the clip's own silence.
    for line in lines:
        assert loudness(line["start"] + 0.2, line["end"] - 0.4) > -45.0, line
    assert loudness(0.0, 0.4) < -60.0


# ---- what never loads --------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.add(node.module)
    return names


def test_a_plain_clipping_run_never_loads_the_voice_layer():
    """core/pipeline.py and everything it reaches (the walk of
    tests/test_editor_import_guard.py) import nothing from voice/: clipping
    behaves exactly as before whether the voice is there or not."""
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        from test_editor_import_guard import PIPELINE, reachable_modules
    finally:
        sys.path.pop(0)
    offenders = []
    for path, via in reachable_modules(ROOT, PIPELINE).items():
        if any(name == "voice" or name.startswith("voice.") for name in _imports(path)):
            offenders.append(" -> ".join(via))
    assert not offenders, "plain auto-clipping would load the voice layer:\n" + "\n".join(offenders)
    # core/binaries.py names the two files itself for that reason.
    from core import binaries
    from voice import kokoro_files

    assert binaries.KOKORO_FILES == (kokoro_files.MODEL_FILE, kokoro_files.VOICES_FILE)


def test_loading_the_voice_layer_loads_no_heavy_library():
    """`import voice` (and its settings, its address rules and its pins) is
    the standard library only: numpy, onnxruntime and kokoro_onnx are loaded
    when something speaks, never when the app starts or lists its engines."""
    code = ("import sys; import voice, voice.settings, voice.net, voice.kokoro_files, voice.align, voice.wav; "
            "import voice.engines.kokoro, voice.engines.voicebox, voice.engines.openai_local, voice.engines.piper; "
            "heavy = [m for m in ('numpy', 'onnxruntime', 'kokoro_onnx', 'torch', 'cv2', 'piper', 'fastapi', "
            "'faster_whisper', 'requests') if m in sys.modules]; print(','.join(heavy))")
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert got.returncode == 0, got.stderr[-2000:]
    assert got.stdout.strip() == "", f"importing the voice layer loaded: {got.stdout.strip()}"


def test_nothing_in_the_voice_layer_downloads_anything():
    """No Voicebox, no VoiceStudio, no model is fetched while the app runs:
    the layer has no downloader in it. (Piper's own on-demand voices are
    Piper's, in multilingual/dub.py, as before.) Its only network code is
    voice/net.py, which refuses every address that is not this PC."""
    for path in sorted((ROOT / "voice").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        names = _imports(path)
        assert "requests" not in names and "httpx" not in names and "huggingface_hub" not in names, path.name
        if path.name != "net.py":
            assert not any(n.startswith("urllib.request") or n in ("socket", "http.client") for n in names), (
                f"{path.relative_to(ROOT)} opens connections itself: only voice/net.py may")
        assert "urlretrieve" not in source and "fetch_https" not in source, path.name
