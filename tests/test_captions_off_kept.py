"""#128: a clip made with captions off got them back at its first edit. Off
lived only in the job's settings, and an editor re-render uses the app's, so
the clip now keeps the job's choice (`job_captions`), and the editor's
Captions tab has a switch of its own (`captions`), which wins."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EDITOR = ROOT / "ui" / "src" / "renderer" / "src" / "components" / "TimelineEditor.tsx"
API_TS = ROOT / "ui" / "src" / "renderer" / "src" / "lib" / "api.ts"


def _cfg(captions=True):
    return {"clips": {"captions": captions}}


@pytest.fixture
def pipeline():
    pytest.importorskip("numpy", reason="core.pipeline needs numpy, which CI does not install")
    from core import pipeline

    return pipeline


@pytest.mark.parametrize("captions, opts, expected", [
    (True, {}, True),
    (False, {}, False),
    (True, {"job_captions": False}, False),          # an editor re-render of an off clip
    (True, {"job_captions": False, "captions": True}, True),   # the switch turned back on
    (True, {"captions": False}, False),               # the switch turned off
])
def test_whether_a_clip_burns_captions(pipeline, captions, opts, expected):
    assert pipeline.captions_on(_cfg(captions), opts) is expected


def test_the_jobs_choice_is_not_an_edit_by_hand(pipeline):
    # "Make it again with my edits" must not take every clip of a captions-off
    # job for one a person edited, nor carry the old job's choice to a new run.
    assert not pipeline._edited({"job_captions": False})
    assert "job_captions" in pipeline._JOB_DECIDES


@pytest.fixture
def render(pipeline, monkeypatch, tmp_path):
    pytest.importorskip("cv2")
    from core import modes
    from core.models import ClipCandidate

    burned = []

    def fake_cut(source, candidate, output, ass_path=None, vf_extra="", normalize=True):
        burned.append(ass_path)
        Path(output).write_bytes(b"clip")

    monkeypatch.setattr(pipeline, "cut_clip", fake_cut)
    monkeypatch.setattr(pipeline, "build_captions", lambda *a, **k: tmp_path / "clip.ass")
    monkeypatch.setattr(modes, "probe_size", lambda _path: (1080, 1920))

    def run(captions, opts=None):
        config = {
            "clips": {"captions": captions, "outro": False, "vertical": True, "vertical_live": True},
            "paths": {"data_dir": str(tmp_path)},
            "tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8},
        }
        burned.clear()
        _final, opts_json = pipeline._render_files(
            tmp_path / "source.mp4", ClipCandidate(start=10.0, end=40.0, score=80), [],
            tmp_path / "clips", config, opts,
        )
        return json.loads(opts_json or "{}"), burned[0]

    return run


def test_a_captions_off_clip_stays_off_when_edited(render):
    saved, ass = render(captions=False)
    assert saved["job_captions"] is False and ass is None
    # The editor re-renders with the app's settings, where captions are on.
    resaved, ass = render(captions=True, opts=saved)
    assert ass is None and resaved["job_captions"] is False


def test_the_editors_switch_turns_them_back_on(render):
    saved, _ = render(captions=False)
    _resaved, ass = render(captions=True, opts={**saved, "captions": True})
    assert ass is not None


def test_a_captions_on_job_saves_nothing_new(render):
    saved, ass = render(captions=True)
    assert "job_captions" not in saved and ass is not None


def test_the_editor_sends_its_switch_on_apply_and_preview():
    editor = EDITOR.read_text(encoding="utf-8")
    assert "if (captionsDirty) renderOpts.captions = captionsOn" in editor
    assert "captionsDirty ? captionsOn : undefined" in editor
    assert "clip.render_opts?.captions ?? clip.render_opts?.job_captions ?? true" in editor
    assert "captions: captions ?? null" in API_TS.read_text(encoding="utf-8")


def test_a_clips_own_switch_from_an_earlier_run_is_not_saved_over_a_captions_off_run(render):
    # "Make it again with my edits" with captions off carries a clip's
    # captions:true; the file burns none, so neither may what is saved.
    saved, ass = render(captions=False, opts={"captions": True})
    assert ass is None and "captions" not in saved and saved["job_captions"] is False
    _resaved, ass = render(captions=True, opts=saved)
    assert ass is None


def test_reset_and_the_translation_preview_read_the_same_switch():
    editor = EDITOR.read_text(encoding="utf-8")
    assert "setCaptionsOn(storedCaptionsOn)" in editor            # Reset puts it back
    modal = (EDITOR.parent / "EditorModal.tsx").read_text(encoding="utf-8")
    assert "clip.render_opts?.captions ?? clip.render_opts?.job_captions ?? true" in modal
