"""The highlights post style where it meets the rest of the app: the editor's
hook title over the clip's title card, and a re-run over clips already
saved."""

import json
from pathlib import Path

import pytest

from core import modes
from video import post_style

HIGHLIGHTS_TOP = {"font": "Arial", "font_size": 80, "post_style": "highlights", "card_position": "top"}


# ---- the hook title and a top card ------------------------------------------------------


@pytest.fixture
def pipeline():
    return pytest.importorskip("core.pipeline")  # imports numpy, which CI does not install


def _render_card_at(pipeline, monkeypatch, tmp_path, edit):
    """Render a Vertical Live highlights clip with its card set at the top,
    ffmpeg faked out, and return where the card was drawn."""
    from core.models import ClipCandidate
    from video_editor import export

    def write(*args, **_kwargs):
        Path(args[2]).write_bytes(b"clip")

    positions = []

    def render_card(headline, subline, size, out_path, position="lower", language="en"):
        positions.append(position)
        return None

    monkeypatch.setattr(pipeline, "cut_clip", write)
    monkeypatch.setattr(export, "apply_edits", write)
    monkeypatch.setattr(modes, "probe_size", lambda _path: (1080, 1920))
    monkeypatch.setattr(post_style, "render_card", render_card)
    config = {
        "clips": {"captions": False, "outro": False, "vertical": True, "vertical_live": True,
                  "caption_style": HIGHLIGHTS_TOP},
        "paths": {"data_dir": str(tmp_path)},
    }
    pipeline._render_files(
        tmp_path / "source.mp4", ClipCandidate(start=10.0, end=40.0, score=80), [],
        tmp_path / "clips", config, {"headline": "STEPBACK FROM THE LOGO", "edit": edit},
    )
    return positions


def test_a_clip_with_a_hook_title_gets_its_card_in_the_lower_third(pipeline, monkeypatch, tmp_path):
    # The hook is burned at the top before the card goes on, so a top card hid it.
    edit = {"hook": {"text": "WAIT FOR THE END", "seconds": 3}}
    assert _render_card_at(pipeline, monkeypatch, tmp_path, edit) == ["lower"]


def test_without_a_hook_the_card_stays_at_the_top(pipeline, monkeypatch, tmp_path):
    assert _render_card_at(pipeline, monkeypatch, tmp_path, {"fade_in": 0.5}) == ["top"]


# ---- a re-run over a saved clip ---------------------------------------------------------


def _rerun(pipeline, db, tmp_path, before: dict, after: dict) -> dict:
    """Register a clip, then the same window again as a force re-run does,
    and return the row's saved options and title."""
    from analysis.metadata import ClipMetadata
    from core.models import ClipCandidate

    db.conn.execute("INSERT INTO videos (video_id, title, status, created_at, updated_at)"
                    " VALUES ('v', 'Game', 'done', 'x', 'x')")
    db.conn.commit()
    clip = ClipCandidate(start=10.0, end=40.0, score=80, hook="h")
    pipeline._register_clip(db, "v", clip, tmp_path / "a.mp4",
                            ClipMetadata(title="Kept title", description="", hashtags=[]),
                            json.dumps(before) if before else "")
    pipeline._register_clip(db, "v", clip, tmp_path / "b.mp4",
                            ClipMetadata(title="New title", description="", hashtags=[]),
                            json.dumps(after) if after else "")
    row = db.conn.execute("SELECT title, path, render_opts FROM clips WHERE video_id = 'v'").fetchone()
    assert row["title"] == "Kept title" and row["path"] == str(tmp_path / "b.mp4")
    return json.loads(row["render_opts"]) if row["render_opts"] else {}


def test_a_rerun_in_highlights_saves_the_card_it_was_rendered_with(pipeline, db, tmp_path):
    before = {"caption_style": {"font": "Georgia", "font_size": 70}, "crop": "center"}
    after = {"caption_style": HIGHLIGHTS_TOP, "headline": "STEPBACK FROM THE LOGO", "subline": "CURRY"}
    opts = _rerun(pipeline, db, tmp_path, before, after)
    assert opts["headline"] == "STEPBACK FROM THE LOGO" and opts["subline"] == "CURRY"
    assert opts["caption_style"] == {"font": "Georgia", "font_size": 70,
                                     "post_style": "highlights", "card_position": "top"}
    assert opts["crop"] == "center"


def test_a_rerun_without_highlights_takes_the_old_card_off_the_row(pipeline, db, tmp_path):
    before = {"caption_style": HIGHLIGHTS_TOP, "headline": "STEPBACK", "subline": "CURRY", "crop": "center"}
    after = {"caption_style": {"font": "Arial", "font_size": 80}}
    opts = _rerun(pipeline, db, tmp_path, before, after)
    assert "headline" not in opts and "subline" not in opts
    assert opts["caption_style"] == {"font": "Arial", "font_size": 80}
    assert post_style.resolve(opts["caption_style"]) == post_style.DEFAULT
    assert opts["crop"] == "center"


def test_a_row_with_no_caption_style_takes_the_whole_one_rendered(pipeline, db, tmp_path):
    after = {"caption_style": HIGHLIGHTS_TOP, "headline": "STEPBACK", "subline": ""}
    opts = _rerun(pipeline, db, tmp_path, {"crop": "center"}, after)
    assert opts == {"crop": "center", "caption_style": HIGHLIGHTS_TOP, "headline": "STEPBACK", "subline": ""}


def test_a_standard_rerun_leaves_the_saved_options_as_they_were(pipeline, db, tmp_path):
    before = {"caption_style": {"font": "Georgia"}, "crop": "center"}
    opts = _rerun(pipeline, db, tmp_path, before, {"caption_style": {"font": "Arial"}})
    assert opts == before


def test_a_gaming_rerun_in_highlights_keeps_both(pipeline, db, tmp_path):
    before = {"gaming": {"cam": [0.0, 0.69, 0.18, 0.31]}, "caption_style": {"font": "Arial"}}
    after = {"gaming": {"cam": [0.0, 0.69, 0.16, 0.31], "layout": "split"},
             "caption_style": HIGHLIGHTS_TOP, "headline": "STEPBACK"}
    opts = _rerun(pipeline, db, tmp_path, before, after)
    assert opts["gaming"] == after["gaming"] and opts["headline"] == "STEPBACK"
    assert post_style.resolve(opts["caption_style"]) == post_style.HIGHLIGHTS
