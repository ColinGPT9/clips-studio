"""The example external pipeline, examples/pipelines/scene-cut-highlights.

It must work the way an outside developer's plugin would: through the app's
runner, importing nothing from Clips Kitty but the SDK, and finding what it
says it finds. The test video is made with FFmpeg: four colours with hard cuts
at 10, 20 and 30 seconds, and a tone that is loud only from 22 to 27 seconds.
"""

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "pipelines" / "scene-cut-highlights"
SDK = ROOT / "sdk" / "python"

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="needs ffmpeg and ffprobe on PATH")


def _make_video(path: Path, loud: tuple[float, float] | None) -> Path:
    volume = f"if(between(t,{loud[0]},{loud[1]}),0.9,0.02)" if loud else "0.02"
    colours = ["red", "blue", "green", "white"]
    cmd = ["ffmpeg", "-loglevel", "error", "-y"]
    for c in colours:
        cmd += ["-f", "lavfi", "-i", f"color=c={c}:s=160x90:d=10:r=10"]
    cmd += ["-f", "lavfi", "-i", f"sine=f=440:d=40,volume='{volume}':eval=frame",
            "-filter_complex", "[0][1][2][3]concat=n=4:v=1:a=0[v]", "-map", "[v]", "-map", "4:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(cmd, check=True, timeout=120)
    return path


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("needs ffmpeg and ffprobe on PATH")
    folder = tmp_path_factory.mktemp("example-videos")
    return {"loud": _make_video(folder / "loud.mp4", (22, 27)), "quiet": _make_video(folder / "quiet.mp4", None)}


# ---- it stays an outside plugin --------------------------------------------------------


def test_it_imports_only_the_standard_library_and_the_sdk():
    tree = ast.parse((EXAMPLE / "src" / "main.py").read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            modules.add(node.module.split(".")[0])
    outside = {m for m in modules if m not in sys.stdlib_module_names and m != "clipskitty_sdk"}
    assert not outside, f"the example imports {outside}; an outside plugin can't"
    assert "__import__" not in (EXAMPLE / "src" / "main.py").read_text(encoding="utf-8")


def test_it_is_laid_out_as_its_own_repository():
    for name in ("clipskitty.yaml", "README.md", "LICENSE", "src/main.py"):
        assert (EXAMPLE / name).is_file(), name


def test_its_manifest_is_valid_with_no_warnings():
    pytest.importorskip("yaml")
    if str(SDK) not in sys.path:
        sys.path.insert(0, str(SDK))
    from clipskitty_sdk.manifest import validate_folder

    data, report = validate_folder(EXAMPLE)
    assert report.ok and not report.warnings, (report.errors, report.warnings)
    assert data["sends"] == [] and data["execution"] == "local" and data["permissions"] == ["video.read", "ffmpeg"]


def test_it_does_not_claim_more_than_it_does():
    """The description says what it cannot do, in the manifest and the README."""
    text = (EXAMPLE / "clipskitty.yaml").read_text(encoding="utf-8") + (EXAMPLE / "README.md").read_text(encoding="utf-8")
    assert "knows nothing about what is happening on screen" in text
    assert "not as a highlight detector" in text


# ---- what it finds ---------------------------------------------------------------------


@needs_ffmpeg
def test_through_the_apps_runner_it_finds_the_loud_stretch_and_starts_on_the_cut(videos, tmp_path, install_plugin):
    pytest.importorskip("yaml")
    from core.models import DownloadedVideo
    from plugins import runner

    data_dir = tmp_path / "data"
    install_plugin(data_dir, EXAMPLE)
    video = DownloadedVideo(video_id="loud", title="Loud", path=videos["loud"], duration=40.0)
    config = {"clips": {"min_duration": 10, "max_duration": 60, "max_clips_per_video": 0}, "llm": {}}
    clips = runner.find_clips({"id": "clips-kitty-examples/scene-cut-highlights"}, video=video, segments=[],
                              language="en", config=config, data_dir=data_dir)
    assert len(clips) == 1
    clip = clips[0]
    assert clip.start == pytest.approx(20.0, abs=0.2)  # the cut before the loud part
    assert clip.start <= 22 and clip.end >= 27  # the loud part is inside
    assert clip.end - clip.start >= 10  # the job's shortest clip
    assert clip.source == "plugin:clips-kitty-examples/scene-cut-highlights@1.0.0"
    assert clip.subscores["plugin_label"] == "loud" and "starting on a scene cut" in clip.reason


@needs_ffmpeg
def test_a_video_with_nothing_loud_gets_no_moments_and_says_why(videos, tmp_path):
    pytest.importorskip("yaml")
    job_dir = tmp_path / "job"
    out = subprocess.run(
        [sys.executable, "-m", "clipskitty_sdk", "run", str(EXAMPLE), "--video", str(videos["quiet"]),
         "--job-dir", str(job_dir)],
        capture_output=True, text=True, timeout=300, cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(SDK)},
    )
    assert out.returncode == 0, out.stderr
    assert "0 moment(s)" in out.stdout
    assert "Nothing was clearly louder than the rest of the video" in out.stdout


@needs_ffmpeg
def test_it_runs_with_only_the_sdk_on_its_path(videos, tmp_path):
    """Started from outside the repository with PYTHONPATH set to the SDK
    alone, as an outside developer would run it."""
    pytest.importorskip("yaml")
    out = subprocess.run(
        [sys.executable, "-m", "clipskitty_sdk", "run", str(EXAMPLE), "--video", str(videos["loud"]),
         "--job-dir", str(tmp_path / "job")],
        capture_output=True, text=True, timeout=300, cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SDK),
             **({"SYSTEMROOT": os.environ["SYSTEMROOT"]} if "SYSTEMROOT" in os.environ else {})},
    )
    assert out.returncode == 0, out.stderr
    assert "1 moment(s)" in out.stdout and "A loud moment" in out.stdout


# ---- its arithmetic ---------------------------------------------------------------------


def _module():
    import importlib.util

    if str(SDK) not in sys.path:
        sys.path.insert(0, str(SDK))
    spec = importlib.util.spec_from_file_location("scene_cut_example", EXAMPLE / "src" / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_loud_seconds_join_into_stretches():
    m = _module()
    levels = [-30.0] * 20
    for s in (5, 6, 8, 15):
        levels[s] = -18.0
    assert m.loud_stretches(levels, 6) == [(5, 9, 12.0), (15, 16, 12.0)]
    assert m.loud_stretches([-30.0] * 20, 6) == []


@pytest.mark.parametrize("stretch, cuts, expected", [
    ((22, 27, 9), [10.0, 20.0, 30.0], (20.0, 30.0, True)),   # starts on the cut, padded to 10 s
    ((22, 27, 9), [5.0], (18.0, 29.0, False)),                 # no cut near: lead-in of 4 s
    ((2, 90, 9), [], (0.0, 60.0, False)),                      # cut to the longest clip
    ((36, 39, 9), [], (30.0, 40.0, False)),                    # kept inside the video
])
def test_a_stretch_becomes_a_clip(stretch, cuts, expected):
    assert _module().to_range(stretch, cuts, 40.0 if stretch[1] < 40 else 100.0, 4.0, 10.0, 60.0) == expected
