"""What the frozen build must and must not contain.

These read clips-studio.spec as text rather than building anything: a real
build is two and a half hours, so the mistakes worth catching here are the
ones that only surface in an installed copy, hours later, on someone else's
machine.

Both guards below come from bugs that shipped. A development machine has every
Python package lying around, so excluding one from the bundle changes nothing
locally and breaks the release.
"""

import re
from pathlib import Path

SPEC = Path(__file__).resolve().parent.parent / "clips-studio.spec"


def _excludes() -> list[str]:
    text = SPEC.read_text(encoding="utf-8")
    block = re.search(r"^excludes = \[(.*?)^\]", text, re.S | re.M)
    assert block, "clips-studio.spec no longer has an excludes list"
    return re.findall(r'"([^"]+)"', block.group(1))


def test_matplotlib_is_not_excluded():
    """Ultralytics imports matplotlib on the path that loads the YOLO model,
    which video/tracker.py uses and the reactions stage goes through.

    Excluding it produced "No module named 'matplotlib'" on every clip job in
    v0.1.0, while working perfectly in a checkout. If a bundle-size cull ever
    puts it back, this fails instead of the release.
    """
    assert "matplotlib" not in _excludes(), (
        "matplotlib must ship: ultralytics needs it to load a model, so "
        "excluding it breaks every clip job in an installed copy while a "
        "development machine carries on fine"
    )


def test_the_spec_still_bundles_what_the_app_cannot_fetch():
    """FFmpeg, the Ollama runtime and the Whisper weights are the difference
    between a one-click install and a scavenger hunt. Losing a datas block is
    silent until someone installs the result."""
    text = SPEC.read_text(encoding="utf-8")
    for folder in ("ffmpeg", "ollama", "whisper"):
        assert f'"vendor" / "{folder}"' in text or f"vendor_{folder}" in text, (
            f"the spec no longer bundles vendor/{folder}"
        )


def test_the_spec_bundles_cublas_12_for_whisper_on_the_gpu():
    """Issue #111: without cuBLAS 12 in the bundle, every job on an NVIDIA PC
    failed at "Transcribing" (PyTorch's CUDA 13 build has only cuBLAS 13)."""
    text = SPEC.read_text(encoding="utf-8")
    assert '"nvidia.cublas"' in text, "the spec no longer bundles cuBLAS 12 (nvidia.cublas)"


def test_the_voice_model_check_clip_ships():
    """Without it, picking an unchecked voice model in an installed copy fails."""
    text = SPEC.read_text(encoding="utf-8")
    assert '"transcription" / "assets"' in text
    assert (SPEC.parent / "transcription" / "assets" / "probe.mp3").stat().st_size > 1000


def test_the_spec_bundles_the_sports():
    """The registry imports each sport's package by name, which the analyser
    can't see, and every sport reads config/sports.yaml at runtime."""
    text = SPEC.read_text(encoding="utf-8")
    assert 'collect_submodules("sports")' in text, "the spec no longer bundles the sports packages"
    assert '"sports.yaml"' in text, "the spec no longer bundles config/sports.yaml"


def test_the_voice_models_ship_with_the_module_that_reads_them():
    """The second speaker's caption colour (analysis/voice_turns.py) is only
    imported when a clip asks for it, where the analyser can't see it, and
    without both models an installed copy quietly burns one colour."""
    text = SPEC.read_text(encoding="utf-8")
    assert '"analysis.voice_turns"' in text, "the spec no longer names analysis.voice_turns"
    for model in ("pyannote_segmentation_3.onnx", "wespeaker_resnet34_lm.onnx"):
        assert model in text, f"the spec no longer bundles models/{model}"


def test_voice_turns_needs_nothing_the_bundle_leaves_out():
    """scipy.cluster is not in the frozen build and sklearn is excluded from
    it; torchaudio and librosa were never there. Any of them imports fine on
    a development machine and fails in an installed copy."""
    source = (SPEC.parent / "analysis" / "voice_turns.py").read_text(encoding="utf-8")
    for package in ("scipy", "sklearn", "torchaudio", "librosa", "torch"):
        assert not re.search(rf"^\s*(?:import|from)\s+{package}\b", source, re.M), (
            f"analysis/voice_turns.py imports {package}, which an installed copy may not have"
        )
