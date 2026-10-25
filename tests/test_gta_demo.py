"""Validate actual external-plugin boundaries through a real app video export."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from core.binaries import ffmpeg
from scripts.demo_gta_plugin import probe_media, run_demo

ROOT = Path(__file__).resolve().parent.parent


def test_synthetic_demo_requires_explicit_input_assertion_before_running(tmp_path):
    with pytest.raises(ValueError, match="--synthetic-demo"):
        run_demo(plugin=tmp_path, video=tmp_path / "absent.mp4", transcript=tmp_path / "absent.json",
                 output=tmp_path / "output")


def test_the_demo_finds_this_checkouts_sdk_without_a_pip_install():
    # A fresh, isolated interpreter: only the repository root on the path, as
    # when the script is run from a source checkout.
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); "
             "from scripts.demo_gta_plugin import sdk_testing; print(sdk_testing().__file__)")
    done = subprocess.run([sys.executable, "-I", "-c", probe, str(ROOT)], capture_output=True, text=True,
                          timeout=60, cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()).resolve().is_relative_to(ROOT / "sdk" / "python")


@pytest.mark.slow
def test_actual_gta_plugin_exports_playable_video_with_timed_captions_and_history(tmp_path):
    plugin_setting = os.environ.get("GTA_PLUGIN_DIR")
    if not plugin_setting:
        pytest.skip("Set GTA_PLUGIN_DIR to an external GTA plugin checkout for the real export integration test")
    plugin = Path(plugin_setting).resolve()
    if not (plugin / "clipskitty.yaml").is_file():
        pytest.fail("GTA_PLUGIN_DIR has no clipskitty.yaml")
    source = tmp_path / "synthetic.mp4"
    # Original generated colors and tone, not a downloaded game recording.
    subprocess.run([
        ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "color=c=0x1a2c42:s=640x360:r=24:d=24",
        "-f", "lavfi", "-i", "sine=f=440:sample_rate=44100:d=24",
        "-c:v", "mpeg4", "-q:v", "8", "-c:a", "aac", "-shortest", str(source),
    ], check=True, capture_output=True, timeout=30)
    transcript = tmp_path / "synthetic.transcript.json"
    transcript.write_text(json.dumps({"language": "en", "segments": [
        {"start": 9, "end": 11, "text": "We crashed into a barrier!"},
    ]}), encoding="utf-8")
    output = tmp_path / "export"
    manifest = run_demo(plugin=plugin, video=source, transcript=transcript, output=output,
                        synthetic_demo=True, max_clips=1)
    assert len(manifest["clips"]) == 1
    clip = manifest["clips"][0]
    assert 0 <= clip["source_start"] < 9 < 11 <= clip["source_end"] <= 24
    assert clip["expected_duration"] < 24  # genuinely cuts a selected window
    exported = Path(clip["path"])
    probe = probe_media(exported)
    assert abs(float(probe["format"]["duration"]) - clip["expected_duration"]) <= 0.1
    assert {stream["codec_type"] for stream in probe["streams"]} >= {"video", "audio"}
    assert next(s for s in probe["streams"] if s["codec_type"] == "video")["codec_name"] == "h264"
    # Decoding the result proves this is media, not an empty container.
    subprocess.run([ffmpeg(), "-v", "error", "-i", str(exported), "-t", "1", "-f", "null", "-"],
                   check=True, capture_output=True, timeout=30)
    captions = Path(clip["captions"]).read_text(encoding="utf-8")
    assert "Dialogue:" in captions
    assert "CRASHED" in captions
    assert "PlayResX: 640" in captions
    # The source is a constant dark color: bright pixels at caption time
    # demonstrate that the ASS was actually burned into the exported video.
    def bright_pixels(at):
        frame = subprocess.run([
            ffmpeg(), "-v", "error", "-ss", str(at), "-i", str(exported),
            "-frames:v", "1", "-vf", "crop=iw:ih/2:0:ih/2", "-pix_fmt", "gray",
            "-f", "rawvideo", "-",
        ], check=True, capture_output=True, timeout=30).stdout
        assert len(frame) == 640 * 180
        return sum(value > 180 for value in frame)
    assert bright_pixels(9.2 - clip["source_start"]) > bright_pixels(0.25) + 100
    assert manifest["manual_quality_review"] == "not_performed"
    assert (output / "demo.log").is_file()
    assert json.loads((output / "manifest.json").read_text())["source"]["sha256"]
    with sqlite3.connect(output / "history.sqlite3") as db:
        row = db.execute("SELECT start_s, end_s, path FROM clips WHERE id = ?",
                         (clip["history_clip_id"],)).fetchone()
    assert row is not None
    assert row[0] == pytest.approx(clip["source_start"], abs=0.01)
    assert row[1] == pytest.approx(clip["source_end"], abs=0.01)
    assert row[2] == str(exported)
