"""Clip selection 2 is tried on Shorts first: Longform keeps selection 1
whatever scoring.selection says, unless scoring.longform_selection asks for
2 (longform/process.py pins it on its own copy of the settings).

Download, transcription and the scorer are stubbed; numpy and OpenCV because
the Longform path imports the scorer, so this runs locally like the other
Longform tests.
"""

import pytest


class _Says:
    def generate(self, *_a, **_k):
        return "{}"


def _run(monkeypatch, tmp_path, db, scoring):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import analysis.fusion as fusion
    import core.pipeline as pipeline
    import llm.registry as registry
    import transcription.transcriber as transcriber
    from core.models import DownloadedVideo
    from longform import process as longform

    source = tmp_path / "talk.mp4"
    source.write_bytes(b"not really a video")
    monkeypatch.setattr(pipeline, "_cached_or_download", lambda *_a, **_k: DownloadedVideo(
        video_id="local_talk", title="Talk", path=source, duration=600.0))
    monkeypatch.setattr(pipeline, "convert_slow_source", lambda *_a, **_k: None)
    monkeypatch.setattr(transcriber, "transcribe", lambda *_a, **_k: [])
    monkeypatch.setattr(registry, "create_backend", lambda *_a, **_k: _Says())
    seen = {}

    def find(_path, _segments, _llm, cfg, **_kw):
        seen["scoring"] = dict(cfg.get("scoring") or {})
        return [], []

    monkeypatch.setattr(fusion, "find_clips", find)
    config = {"clips": {"captions": False}, "paths": {"data_dir": str(tmp_path)},
              "whisper": {"model": "tiny", "device": "cpu"}, "llm": {}}
    if scoring is not None:
        config["scoring"] = scoring
    longform.process_longform("local:talk", config, db, {"mode": "short_clips"})
    return seen["scoring"], config


def test_longform_keeps_selection_1_when_shorts_use_2(monkeypatch, tmp_path, db):
    scoring, config = _run(monkeypatch, tmp_path, db, {"selection": 2, "rerank_pool": 8})
    assert scoring == {"selection": 1, "rerank_pool": 8}
    assert config["scoring"] == {"selection": 2, "rerank_pool": 8}  # the Shorts settings are untouched


def test_longform_uses_selection_2_only_when_asked(monkeypatch, tmp_path, db):
    scoring, _config = _run(monkeypatch, tmp_path, db, {"selection": 1, "longform_selection": 2})
    assert scoring["selection"] == 2


def test_longform_without_any_scoring_settings_is_selection_1(monkeypatch, tmp_path, db):
    scoring, config = _run(monkeypatch, tmp_path, db, None)
    assert scoring == {"selection": 1} and "scoring" not in config
