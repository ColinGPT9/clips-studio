"""#137: a watermark re-render failed with WinError 5 because the clip was open
in the editor's player, and Windows refuses a rename over an open file. The
watermark now puts its result in place the way the end card does: retry the
rename, then write into the open file."""
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="video.outro draws the end card with numpy, which CI does not install")

from video import outro
from video_editor import watermark


def _locked(target: Path, monkeypatch):
    real = Path.replace

    def replace(self, dst):
        if Path(dst) == target:
            raise PermissionError(5, "Access is denied", str(dst))
        return real(self, dst)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(outro, "REPLACE_BUDGET", 0.2)
    monkeypatch.setattr(outro._replace_with_retry, "__defaults__", (0.2, "End card"))


def test_a_watermark_reaches_a_clip_the_player_holds_open(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"old clip bytes, longer than the new ones")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "logo.png").write_bytes(b"png")

    def ffmpeg_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"watermarked")

        class Done:
            returncode, stderr = 0, ""
        return Done()

    monkeypatch.setattr(watermark.subprocess, "run", ffmpeg_run)
    monkeypatch.setattr(watermark, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(watermark, "video_encoder_args", lambda: [])
    _locked(clip, monkeypatch)

    watermark.apply_image(clip, {"image_asset": "logo.png"}, (1080, 1920), assets)

    assert clip.read_bytes() == b"watermarked"
    assert not clip.with_suffix(".wm.mp4").exists()
