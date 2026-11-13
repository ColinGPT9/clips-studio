"""Editor suite step 5: what the project operations ask the rest of Clips
Kitty (video_editor/project/env.py AppEnv): the caption fonts, the colour
filters, the Gaming layouts, the export presets and encoders, and which media
a project may name. Against the real modules, which load without numpy, so
this runs on the CI light install; the probe and the encoder are stand-ins.
"""

from types import SimpleNamespace

import pytest

from core import modes
from video import audio_library, captions, export_presets, filters
from video_editor.project import model, ops, store
from video_editor.project.env import AppEnv

S = 1_000_000


def _env(db=None, tmp_path=None, **kwargs) -> AppEnv:
    kwargs.setdefault("encoder_for", lambda codec, pref="auto": "libx264" if codec == "h264" and pref != "gpu" else None)
    kwargs.setdefault("probe", lambda path: SimpleNamespace(width=1280, height=720, duration=61.5, fps=29.97))
    return AppEnv(db=db, data_dir=tmp_path, **kwargs)


def test_the_adjust_ranges_are_the_filters_own():
    assert ops.ADJUST == {key: (lo, hi) for key, (lo, hi, _default) in filters.ADJUST_RANGES.items()}


def test_a_watermarks_keys_are_the_ones_the_render_reads():
    """ops.WATERMARK_KEYS against video_editor/watermark.py as it is written
    (the module needs PIL to load, so its text is read instead): every key
    it takes from a watermark, and no other."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "video_editor" / "watermark.py").read_text(encoding="utf-8")
    read = set(re.findall(r"""cfg(?:\.get\(|\[)["']([a-z_]+)["']""", source))
    assert read and read == set(ops.WATERMARK_KEYS)


def test_the_speed_and_volume_limits_are_the_plans():
    assert model.SPEED_RANGE == (0.25, 4.0) and model.VOLUME_RANGE == (0.0, 2.0) and model.TEXT_MAX == 500


def test_fonts_come_from_the_caption_font_registry():
    env = _env()
    for font in captions.FONTS:
        assert env.font(font.upper()) == font
    for bad in ("", "Comic Sans Nope", None, "../Arial"):
        assert env.font(bad) is None
    doc = ops.apply(model.new_project(), [{"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": S,
                                           "font": captions.BUNDLED_FONTS[0].lower()}], env).document
    assert doc["tracks"][1]["items"][0]["text"]["font"] == captions.BUNDLED_FONTS[0]


def test_filters_and_gaming_layouts_go_through_the_apps_own_checks():
    env = _env()
    assert env.filter_ok("vibrant") == filters.is_valid("vibrant") is True
    assert env.filter_ok("no-such-filter") is False
    layout = {"cam": [0.0, 0.6, 0.3, 0.4], "panels": []}
    assert env.gaming(layout) == modes.clean_gaming(layout)
    with pytest.raises(ValueError):
        env.gaming({"cam": "x"})
    doc = ops.apply(model.new_project(), [{"op": "set_branding", "watermark": {"type": "text", "text": "@Me",
                                                                              "font": "arial"}}], env).document
    assert doc["branding"]["watermark"]["font"] == "Arial"


def test_a_caption_style_takes_only_what_the_captions_read():
    env = _env()
    style = {"font": "impact", "font_size": 96, "position": "top", "animation": captions.ANIMATIONS[1],
             "safe_zone": captions.SAFE_ZONE_CHOICES[1], "uppercase": True, "color": "#FFCC00"}
    assert env.caption_style(style) == {**style, "font": "Impact"}
    for bad in ({"font": "Nope"}, {"font_size": 500}, {"font_size": "big"}, {"position": "left"},
                {"animation": "explode"}, {"safe_zone": "moon"}, {"uppercase": 1}, {"color": "red"},
                {"not_a_key": 1}):
        with pytest.raises(ValueError):
            env.caption_style(bad)
    assert set(captions.DEFAULT_STYLE) >= set(style)


def test_an_export_preset_must_be_one_this_pc_can_encode():
    env = _env()
    preset = env.export_preset({"kind": "custom", "resolution": "720p"})
    assert preset == export_presets.normalize({"kind": "custom", "resolution": "720p"})
    assert env.export_preset(None) is None and env.export_preset("original") is None
    with pytest.raises(ValueError, match="can't encode"):
        env.export_preset({"kind": "custom", "codec": "av1"})
    with pytest.raises(ValueError, match="graphics card"):
        env.export_preset({"kind": "custom", "encoder": "gpu"})
    with pytest.raises(ValueError):
        env.export_preset({"kind": "custom", "resolution": "9000p"})
    with pytest.raises(ops.OpError, match="set_export_preset"):
        ops.apply(model.new_project(), [{"op": "set_export_preset", "preset": {"kind": "custom", "codec": "av1"}}], env)


def test_media_is_named_by_id_and_looked_up_here(db, tmp_path):
    env = _env(db, tmp_path)
    db.upsert_video("vid1", title="Friday stream", duration=3600.0)
    # No file in downloads/: what the library row says.
    assert env.media({"video_id": "vid1"}) == {
        "kind": "video", "source": {"type": "video", "video_id": "vid1"}, "duration_us": 3600 * S, "width": 0,
        "height": 0, "fps": None, "has_audio": True, "name": "Friday stream"}
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "vid1.mp4").write_bytes(b"x")
    probed = env.media({"video_id": "vid1"})
    assert (probed["width"], probed["height"], probed["duration_us"]) == (1280, 720, 61_500_000)
    assert probed["fps"] == {"num": 30000, "den": 1001}
    assert env.video_facts("vid1") == probed and env.video_facts("nope") == {}
    for bad in ("nope", "../vid1", "C:/Users/someone/vid1.mp4", ""):
        with pytest.raises(ValueError):
            env.media({"video_id": bad})
    # A probe that fails leaves what the row says.
    broken = AppEnv(db=db, data_dir=tmp_path, probe=lambda path: 1 / 0)
    assert broken.media({"video_id": "vid1"})["duration_us"] == 3600 * S

    entry = audio_library.items()[0]
    found = env.media({"library": entry["id"]})
    assert found["kind"] == "audio" and found["source"] == {"type": "library", "id": entry["id"]}
    assert found["duration_us"] == model.us(entry["seconds"])
    with pytest.raises(ValueError, match="free tracks"):
        env.media({"library": "no-such-track"})

    sound = tmp_path / "sound.wav"
    sound.write_bytes(b"RIFF" * 50)
    row = store.add_asset(db.conn, tmp_path, sound, kind="audio", name="My sound",
                          facts={"duration_us": 2 * S, "fps": None}, now=0)
    asset = env.media({"asset": row["sha256"]})
    assert asset == {"kind": "audio", "source": {"type": "asset", "sha256": row["sha256"]}, "duration_us": 2 * S,
                     "width": 0, "height": 0, "fps": None, "has_audio": True, "name": "My sound"}
    for bad in ("0" * 64, str(sound), "../" + row["sha256"], row["sha256"].upper()):
        with pytest.raises(ValueError):
            env.media({"asset": bad})
    with pytest.raises(ValueError):
        env.media({"path": str(sound)})
    with pytest.raises(ValueError):
        AppEnv().media({"video_id": "vid1"})

    doc = ops.apply(model.new_project(), [
        {"op": "add_media", "video_id": "vid1"}, {"op": "add_media", "library": entry["id"]},
        {"op": "add_media", "asset": row["sha256"]},
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 0, "source_out_us": 10 * S},
        {"op": "add_audio", "media_id": "m3", "track_id": "A2", "start_us": 0, "role": "voice"}], env).document
    assert [m["source"]["type"] for m in doc["media"].values()] == ["video", "library", "asset"]
    with pytest.raises(ops.OpError, match="past the end"):
        ops.apply(doc, [{"op": "insert_clip", "media_id": "m1", "source_in_us": 0, "source_out_us": 62 * S}], env)


def test_a_clips_free_track_is_the_same_media_as_the_one_add_media_finds():
    """A clip with a free music track opens with that track as media
    (importer.py). Asking for the same track again finds it there rather
    than adding it a second time: both name it the same way."""
    from video_editor.project import importer

    entry = audio_library.items()[0]
    doc = importer.import_clip({"video_id": "vid1", "start": 10.0, "end": 40.0, "title": "A clip",
                                "render_opts": {"edit": {"music": {"library": entry["id"], "volume": 0.3}}}},
                               {"fps": {"num": 30, "den": 1}})
    music = next(m for m in doc["media"].values() if m["source"]["type"] == "library")
    result = ops.apply(doc, [{"op": "add_media", "library": entry["id"]}], _env())
    assert result.created == [{"kind": "media", "id": music["id"]}] and not result.changed
    assert "already in the project" in result.warnings[0]


def test_the_plain_env_refuses_everything():
    env = ops.Env()
    assert env.font("Arial") is None and env.filter_ok("vibrant") is False
    for call in (lambda: env.media({"video_id": "v"}), lambda: env.gaming({}), lambda: env.caption_style({}),
                 lambda: env.export_preset(None)):
        with pytest.raises(ValueError):
            call()
