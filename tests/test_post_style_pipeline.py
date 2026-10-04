"""The highlights post style where it meets the rest of the app: the editor's
hook title over the clip's title card."""

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
