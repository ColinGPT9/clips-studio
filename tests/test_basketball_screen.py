"""The Sports row's Crop for basketball: the picture kept between the apps' top bar and their
caption (the default), or the whole screen. A clip made "Full screen" says so in its saved
options, so a re-render from the editor fills the screen again; the default writes nothing."""

import json
from pathlib import Path

import pytest

from core.models import ClipCandidate, Segment

CLIP = ClipCandidate(start=10.0, end=20.0, score=80)


@pytest.fixture
def render(monkeypatch, tmp_path):
    """One basketball clip through _render_files, with the cut, the framing and the encode
    stood in for. Returns a function: (job sport option, clip opts) -> (the config the
    framing was given, the saved render options)."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import video.cropper as cropper
    from core import modes, pipeline

    seen = {}

    def cut(_source, _candidate, output, ass_path=None, vf_extra="", normalize=True):
        Path(output).write_bytes(b"clip")

    def framing(_clip, config, _name, _hints=None):
        seen["config"] = config
        return {"mode": "track", "path": [(0.0, 0.5)]}

    def encode(_clip, _tracking, output, **_k):
        Path(output).write_bytes(b"vertical")

    monkeypatch.setattr(pipeline, "cut_clip", cut)
    monkeypatch.setattr(pipeline, "_sport_framing", framing)
    monkeypatch.setattr(cropper, "render_vertical", encode)
    monkeypatch.setattr(modes, "probe_size", lambda _path: (1920, 1080))
    source = tmp_path / "downloads" / "v1.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")
    words = [{"start": i * 0.5, "end": i * 0.5 + 0.5, "word": f"w{i}"} for i in range(80)]
    segments = [Segment(start=0.0, end=40.0, text=" ".join(w["word"] for w in words), words=words)]

    def run(sport, opts=None):
        config = {
            "clips": {"captions": False, "outro": False, "vertical": True, "sport": sport},
            "paths": {"data_dir": str(tmp_path)},
            "tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8},
        }
        _final, saved = pipeline._render_files(source, CLIP, segments, tmp_path / "clips", config, opts)
        return seen.pop("config"), json.loads(saved) if saved else {}

    return run


def test_full_screen_reaches_the_framing_and_is_saved_with_the_clip(render):
    given, saved = render({"name": "basketball", "highlights": "best", "period": "full", "screen": "full"})
    assert given["sport_screen"] == "full"
    assert saved["sport"] == "basketball" and saved["sport_screen"] == "full"
    # Re-rendered from the editor, from the clip's saved options in a job without the choice.
    given, again = render({"name": "basketball"}, {"sport": "basketball", "sport_screen": "full"})
    assert given["sport_screen"] == "full" and again["sport_screen"] == "full"


def test_the_default_crop_writes_nothing_new(render):
    given, saved = render({"name": "basketball", "highlights": "best", "period": "full"})
    assert "sport_screen" not in given and "sport_screen" not in saved
    assert saved["sport"] == "basketball"


def test_the_basketball_framing_fills_the_screen_only_when_asked(monkeypatch):
    from sports import basketball
    from sports.basketball import action

    seen = []
    monkeypatch.setattr(action, "compute_offline",
                        lambda _clip, settings, _hints=None: seen.append(dict(settings)) or {"quality": {}})
    monkeypatch.setattr(basketball, "_debug_video", lambda *_a, **_k: None)
    basketball.framing("clip.mp4", {"sport_screen": "full"})
    basketball.framing("clip.mp4", {})
    assert seen[0]["screen"] == "full" and "screen" not in seen[1]
    assert action.picture_share(seen[0]) == 1.0 and action.picture_share(seen[1]) < 0.7
