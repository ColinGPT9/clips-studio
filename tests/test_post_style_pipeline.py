"""The highlights post style where it meets the rest of the app: the editor's
hook title over the clip's title card, a re-run over clips already saved,
and translated subtitles burned onto a clip that has the card."""

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


# ---- translated subtitles over the card -------------------------------------------------

EXPORT_STYLE = {"font": "Arial", "font_size": 84, "color": "#FFFFFF", "position": "bottom"}


def _burn_style(monkeypatch, tmp_path, render_opts: dict, style: dict | None) -> dict | None:
    """Publish one burned language for a clip with these saved options, the
    re-render and the burn faked out, and return the style the burn got."""
    from multilingual import burn
    from multilingual.publish import publish

    burned = []
    base = tmp_path / "base.mp4"
    base.write_bytes(b"base")
    monkeypatch.setattr(burn, "clean_base", lambda *_a, **_k: base)

    def fake_burn(base_video, lines, language, out_path, caption_style, config):
        burned.append(caption_style)
        return None

    monkeypatch.setattr(burn, "burn", fake_burn)
    lines = [{"start": 0.0, "end": 1.0, "text": "no way"}]
    publish(
        lines, ["es"], tmp_path / "out", "clip", llm=None, burn=True,
        clip_row={"render_opts": json.dumps(render_opts)}, config={"clips": {}},
        data_dir=tmp_path, pre_translated={"es": {"lines": [{**lines[0], "text": "no puede ser"}]}},
        style=style,
    )
    assert len(burned) == 1
    return burned[0]


def test_translated_subtitles_on_a_highlights_clip_go_in_the_middle(monkeypatch, tmp_path):
    opts = {"caption_style": {**HIGHLIGHTS_TOP, "card_position": "lower"}, "headline": "STEPBACK"}
    # The card sits in the lower third, where the export's own bottom position would land.
    assert _burn_style(monkeypatch, tmp_path, opts, EXPORT_STYLE) == {**EXPORT_STYLE, "position": "middle"}


def test_with_no_look_chosen_they_take_the_clips_highlights_captions(monkeypatch, tmp_path):
    opts = {"caption_style": HIGHLIGHTS_TOP, "headline": "STEPBACK"}
    style = _burn_style(monkeypatch, tmp_path, opts, None)
    assert style == post_style.caption_style_for(HIGHLIGHTS_TOP) and style["position"] == "middle"


def test_a_standard_clip_burns_in_the_export_style_unchanged(monkeypatch, tmp_path):
    opts = {"caption_style": {"font": "Georgia", "position": "bottom"}}
    assert _burn_style(monkeypatch, tmp_path, opts, EXPORT_STYLE) == EXPORT_STYLE
    assert _burn_style(monkeypatch, tmp_path, opts, None) == opts["caption_style"]


def test_a_landscape_render_has_no_card_and_keeps_the_export_style(monkeypatch, tmp_path):
    opts = {"caption_style": HIGHLIGHTS_TOP, "headline": "STEPBACK", "profile": "short_clips"}
    assert _burn_style(monkeypatch, tmp_path, opts, EXPORT_STYLE) == EXPORT_STYLE


# ---- the editor's preview ---------------------------------------------------------------


def test_the_editors_preview_shows_the_card_words_not_yet_applied(monkeypatch, tmp_path):
    """Update preview sends the card's pending words; the preview must draw
    them, or it shows a card that Apply will not produce."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pipeline = pytest.importorskip("core.pipeline")
    from fastapi.testclient import TestClient

    from core.state import StateDB
    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    db = StateDB(data_dir / "state.db")
    db.conn.execute("INSERT INTO videos (video_id, title, status, created_at, updated_at)"
                    " VALUES ('vid1', 'Game 7', 'done', 'now', 'now')")
    saved = {"caption_style": {"post_style": "highlights"}, "headline": "OLD WORDS", "subline": "KEPT"}
    clip_id = db.conn.execute(
        "INSERT INTO clips (video_id, start_s, end_s, score, hook, path, title, render_opts, created_at)"
        " VALUES ('vid1', 0, 5, 80, 'pass', ?, 'Dime', ?, 'now')",
        (str(tmp_path / "clip.mp4"), json.dumps(saved)),
    ).lastrowid
    db.conn.commit()
    (data_dir / "downloads").mkdir(parents=True)
    (data_dir / "downloads" / "vid1.mp4").write_bytes(b"source")

    seen = []

    def render(source, candidate, segments, out_dir, config, opts, lang):
        seen.append(dict(opts))
        out = Path(out_dir) / "rendered.mp4"
        out.write_bytes(b"preview")
        return out, ""

    monkeypatch.setattr(pipeline, "_render_files", render)
    client = TestClient(app, base_url="http://127.0.0.1")
    try:
        r = client.post(f"/clips/{clip_id}/preview", json={"edit": None, "headline": "NO LOOK DIME👀"})
        assert r.status_code == 200, r.text
        assert seen[-1]["headline"] == "NO LOOK DIME👀" and seen[-1]["subline"] == "KEPT"

        client.post(f"/clips/{clip_id}/preview", json={"edit": None})
        assert seen[-1]["headline"] == "OLD WORDS"  # nothing pending: the saved words
    finally:
        db.conn.close()
