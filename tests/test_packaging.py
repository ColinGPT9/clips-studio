"""What the frozen build must and must not contain.

These read clips-studio.spec as text rather than building anything: a real
build is two and a half hours, so the mistakes worth catching here are the
ones that only surface in an installed copy, hours later, on someone else's
machine.

Both guards below come from bugs that shipped. A development machine has every
Python package lying around, so excluding one from the bundle changes nothing
locally and breaks the release.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "clips-studio.spec"
SDK = ROOT / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))


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


def test_the_spec_bundles_the_plugin_runner_and_the_sdk():
    """process_video imports plugins/ only when a job names a pipeline, and a
    plugin process imports the SDK from files, so both must ship explicitly."""
    text = SPEC.read_text(encoding="utf-8")
    assert 'collect_submodules("plugins")' in text, "the spec no longer bundles the plugins package"
    assert '"sdk" / "python" / "clipskitty_sdk"' in text and '"sdk/python/clipskitty_sdk"' in text, (
        "the spec no longer ships sdk/python/clipskitty_sdk where plugins/_sdk.py looks for it"
    )
    assert '"plugins" / "builtin"' in text and '"plugins/builtin"' in text, (
        "the spec no longer ships the official modes' manifests (plugins/builtin)"
    )
    assert '(str(ROOT / "plugins" / "catalog_index.json"), "plugins")' in text, (
        "the spec no longer ships plugins/catalog_index.json where plugins/registry.py looks for it"
    )
    assert '(str(ROOT / "sdk" / "python" / "LICENSE"), "sdk/python")' in text, (
        "the spec no longer ships the SDK's MIT licence beside it"
    )


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


def test_pipelines_get_the_whole_standard_library():
    """Pipelines run on the engine's own Python (_clipskitty_script_host.py),
    and PyInstaller packs only the modules the engine imports. Without the
    stdlib loop, `import csv` in a pipeline works in a checkout and fails in
    the installed app alone."""
    text = SPEC.read_text(encoding="utf-8")
    assert "sys.stdlib_module_names - STDLIB_SKIP" in text
    skip = re.search(r"^STDLIB_SKIP = \{(.*?)^\}", text, re.S | re.M)
    assert skip, "clips-studio.spec no longer has STDLIB_SKIP"
    skipped = set(re.findall(r'"([^"]+)"', skip.group(1)))
    assert {"tkinter", "_tkinter", "test"} <= skipped
    assert not {"csv", "statistics", "sqlite3", "email", "json", "multiprocessing", "zoneinfo"} & skipped
    assert '"_clipskitty_script_host"' in text


def test_script_mode_is_decided_before_the_engine_loads():
    """main.py hands a pipeline's command line to the script host before its
    heavy imports, so a pipeline starts quickly and loads none of the engine."""
    main = (SPEC.parent / "main.py").read_text(encoding="utf-8")
    assert main.index("CLIPSKITTY_SCRIPT_HOST") < main.index("\nimport yaml")


def _stdlib_skip() -> set[str]:
    text = SPEC.read_text(encoding="utf-8")
    skip = re.search(r"^STDLIB_SKIP = \{(.*?)^\}", text, re.S | re.M)
    assert skip, "clips-studio.spec no longer has STDLIB_SKIP"
    return set(re.findall(r'"([^"]+)"', skip.group(1)))


def test_the_sdk_lint_knows_what_the_app_leaves_out():
    """`python -m clipskitty_sdk validate` warns about a standard library
    module the installed app doesn't have: what the spec leaves out on
    purpose, and the POSIX-only modules the Windows build can't find."""
    from clipskitty_sdk import lint

    skipped = _stdlib_skip()
    assert skipped <= lint.NOT_BUNDLED
    assert lint.NOT_BUNDLED - skipped == lint.NOT_ON_WINDOWS
    assert lint.APP_LEAVES_OUT == skipped


def _build_installer():
    spec = importlib.util.spec_from_file_location("build_installer_under_test", ROOT / "scripts" / "build_installer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_build_refuses_another_python():
    """Plugins run on the Python frozen into the app, and the SDK's lint, its
    CI job and the docs promise them host.APP_PYTHON. A build on another
    minor version is refused before anything is frozen, and the frozen
    engine's script-mode check asserts the version it really has."""
    from clipskitty_sdk.host import APP_PYTHON

    build = _build_installer()
    assert APP_PYTHON == (3, 11)
    assert build.python_problem((3, 12, 0)) == (
        "This build uses Python 3.12, but Clips Kitty's plugins are promised Python 3.11 (APP_PYTHON in "
        "sdk/python/clipskitty_sdk/host.py). Build with 3.11, or change APP_PYTHON and the docs that name it.")
    assert build.python_problem((3, 11, 9)) is None
    assert build.python_problem((3, 11, 0, "final", 0)) is None
    check = build.SCRIPT_MODE_CHECK
    assert "from clipskitty_sdk.host import APP_PYTHON" in check
    assert "assert tuple(sys.version_info[:2]) == tuple(APP_PYTHON)" in check
    main = build.main.__code__.co_names
    assert "python_problem" in main


def test_ci_tests_the_sdk_on_clips_kittys_python():
    """CI's SDK (Windows) job runs the SDK's tests on the app's own Python."""
    yaml = pytest.importorskip("yaml")
    from clipskitty_sdk.host import APP_PYTHON

    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = ci["jobs"]["sdk-windows"]
    versions = [step["with"]["python-version"] for step in job["steps"]
                if str(step.get("uses", "")).startswith("actions/setup-python")]
    assert versions == [".".join(map(str, APP_PYTHON))]
