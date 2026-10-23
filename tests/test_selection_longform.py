"""Clip selection 2 is the default for Shorts, not for Longform: Longform keeps
selection 1 whatever scoring.selection says, its default of 2 included,
unless scoring.longform_selection asks for 2. longform/process.py pins it on
its own copy of the settings every time, making a scoring block for the copy
when there is none, so the copy never inherits the Shorts' default; the
settings themselves are left as they are.

Download, transcription and the scorer are stubbed; numpy and OpenCV because
the Longform path imports the scorer, so this runs locally like the other
Longform tests.
"""

import copy

import pytest

ABSENT = "(no scoring block)"


class _Says:
    def generate(self, *_a, **_k):
        return "{}"


def _run(monkeypatch, tmp_path, db, scoring):
    """What Longform hands the scorer: its copy's scoring block (ABSENT when
    there is none) and the selection that copy asks for; and the settings.
    `scoring` is the settings' scoring block, or ABSENT for none."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import analysis.fusion as fusion
    import core.pipeline as pipeline
    import llm.registry as registry
    import transcription.transcriber as transcriber
    from analysis import highlights
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
        seen["scoring"] = copy.deepcopy(cfg.get("scoring", ABSENT))
        seen["selection"] = highlights.selection_version(cfg)
        return [], []

    monkeypatch.setattr(fusion, "find_clips", find)
    config = {"clips": {"captions": False}, "paths": {"data_dir": str(tmp_path)},
              "whisper": {"model": "tiny", "device": "cpu"}, "llm": {}}
    if scoring is not ABSENT:
        config["scoring"] = scoring
    longform.process_longform("local:talk", config, db, {"mode": "short_clips"})
    return seen, config


def test_longform_keeps_selection_1_when_shorts_use_2(monkeypatch, tmp_path, db):
    seen, config = _run(monkeypatch, tmp_path, db, {"selection": 2, "rerank_pool": 8})
    assert seen["scoring"] == {"selection": 1, "rerank_pool": 8} and seen["selection"] == 1
    assert config["scoring"] == {"selection": 2, "rerank_pool": 8}  # the Shorts settings are untouched


@pytest.mark.parametrize("scoring", [
    {"selection": 1, "longform_selection": 2},
    {"selection": 2, "longform_selection": 2},
    {"longform_selection": 2},
])
def test_longform_uses_selection_2_only_when_asked(scoring, monkeypatch, tmp_path, db):
    seen, _config = _run(monkeypatch, tmp_path, db, dict(scoring))
    assert seen["scoring"] == {**scoring, "selection": 2} and seen["selection"] == 2


@pytest.mark.parametrize("scoring, pinned", [
    (ABSENT, {"selection": 1}),                                # no scoring block: one is made
    (None, {"selection": 1}),                                  # `scoring:` with nothing under it
    ({"rerank_pool": 8}, {"rerank_pool": 8, "selection": 1}),  # settings.yaml as it ships
])
def test_with_nothing_set_longform_is_selection_1_and_shorts_are_2(scoring, pinned, monkeypatch, tmp_path, db):
    from analysis import highlights

    seen, config = _run(monkeypatch, tmp_path, db, copy.deepcopy(scoring))
    assert seen["scoring"] == pinned and seen["selection"] == 1
    # The settings are left as they are, and for Shorts they mean selection 2.
    assert config.get("scoring", ABSENT) == scoring
    assert highlights.selection_version(config) == 2
