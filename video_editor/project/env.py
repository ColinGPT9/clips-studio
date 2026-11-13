"""What the project operations ask the rest of Clips Kitty (ops.Env), for
real: the caption fonts, the colour filters, the Gaming layouts, the export
presets and encoders, and which media a project may name.

Every import of another part of the app is inside the function that needs
it, so loading the project package loads none of them.
"""

from __future__ import annotations

import re
from pathlib import Path

from video_editor.project import model
from video_editor.project.ops import Env

_SHA256 = re.compile(r"[0-9a-f]{64}")
_COLOUR = re.compile(r"#[0-9A-Fa-f]{6}")


class AppEnv(Env):
    """`db` is the app's StateDB (or anything with its `.conn`), `data_dir`
    its data folder. `probe(path)` reads a video file's size, frame rate and
    length (video.export_presets.probe by default) and `encoder_for(codec,
    pref)` says what this PC encodes with (video.encoding.encoder_for); both
    can be replaced, and a test replaces them."""

    def __init__(self, db=None, data_dir=None, probe=None, encoder_for=None):
        self.db = db
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self._probe = probe
        self._encoder_for = encoder_for

    # ---- media ----

    def media(self, ref: dict) -> dict:
        if "library" in ref:
            return self._library(ref["library"])
        if "video_id" in ref:
            return self._video(ref["video_id"])
        if "asset" in ref:
            return self._asset(ref["asset"])
        raise ValueError("names no media")

    def _library(self, item_id: str) -> dict:
        from video import audio_library

        entry = audio_library.entry(item_id)
        if entry is None:
            raise ValueError("is not one of the app's free tracks or sound effects")
        return {"kind": "audio", "source": {"type": "library", "id": entry["id"]},
                "duration_us": model.us(entry.get("seconds") or 0), "width": 0, "height": 0, "fps": None,
                "has_audio": True, "name": str(entry.get("title") or entry["id"])}

    def _facts(self, path: Path | None) -> dict:
        """What a probe says about a video file; {} when there is no file or
        it can't be read (the media is added with what is known)."""
        if path is None or not path.is_file():
            return {}
        try:
            if self._probe is not None:
                source = self._probe(path)
            else:
                from video import export_presets

                source = export_presets.probe(path)
        except Exception:
            return {}
        out = {"width": int(getattr(source, "width", 0) or 0), "height": int(getattr(source, "height", 0) or 0),
               "duration_us": model.us(getattr(source, "duration", 0) or 0)}
        try:
            out["fps"] = model.fps_of(round(float(getattr(source, "fps", 0) or 0), 3))
        except (model.ModelError, TypeError, ValueError):
            out["fps"] = None
        return out

    def _video(self, video_id: str) -> dict:
        if self.db is None:
            raise ValueError("can't be looked up here")
        row = self.db.conn.execute(
            "SELECT video_id, title, duration FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        if row is None:
            raise ValueError("is not a video in the library")
        path = self.data_dir / "downloads" / f"{row['video_id']}.mp4" if self.data_dir is not None else None
        facts = self._facts(path)
        return {"kind": "video", "source": {"type": "video", "video_id": row["video_id"]},
                "duration_us": facts.get("duration_us") or model.us(row["duration"] or 0),
                "width": facts.get("width", 0), "height": facts.get("height", 0), "fps": facts.get("fps"),
                "has_audio": True, "name": str(row["title"] or "")}

    def video_facts(self, video_id: str) -> dict:
        """What is known about a library video, as importer.import_clip takes
        it; {} when it isn't in the library."""
        try:
            return self._video(video_id)
        except ValueError:
            return {}

    def _asset(self, sha256: str) -> dict:
        if self.db is None or not _SHA256.fullmatch(sha256):
            raise ValueError("is not the sha256 of an imported file")
        row = self.db.conn.execute("SELECT * FROM media_assets WHERE sha256 = ?", (sha256,)).fetchone()
        if row is None:
            raise ValueError("is not an imported file")
        fps = {"num": row["fps_num"], "den": row["fps_den"]} if row["fps_num"] and row["fps_den"] else None
        return {"kind": row["kind"], "source": {"type": "asset", "sha256": row["sha256"]},
                "duration_us": int(row["duration_us"] or 0), "width": int(row["width"] or 0),
                "height": int(row["height"] or 0), "fps": fps, "has_audio": bool(row["has_audio"]),
                "name": str(row["name"] or "")}

    # ---- looks ----

    def filter_ok(self, name: str) -> bool:
        from video import filters

        return filters.is_valid(name)

    def gaming(self, settings: dict) -> dict:
        from core import modes

        return modes.clean_gaming(settings)

    def font(self, name: str) -> str | None:
        from video import captions

        wanted = str(name or "").strip().casefold()
        return next((f for f in captions.FONTS if f.casefold() == wanted), None) if wanted else None

    def caption_style(self, style: dict) -> dict:
        """A caption style with only the keys the captions read
        (video/captions.py DEFAULT_STYLE), each checked."""
        from video import captions

        out = {}
        for key, value in style.items():
            if key not in captions.DEFAULT_STYLE:
                raise ValueError(f"has no {str(key)[:40]!r}. It takes {', '.join(captions.DEFAULT_STYLE)}")
            default = captions.DEFAULT_STYLE[key]
            if key == "font":
                found = self.font(value) if isinstance(value, str) else None
                if found is None:
                    raise ValueError(f"font {str(value)[:60]!r} is not one of the caption fonts")
                value = found
            elif key == "position":
                if value not in ("bottom", "middle", "top"):
                    raise ValueError("position must be bottom, middle or top")
            elif key == "animation":
                if value not in captions.ANIMATIONS:
                    raise ValueError(f"animation must be one of {', '.join(captions.ANIMATIONS)}")
            elif key == "safe_zone":
                if value not in captions.SAFE_ZONE_CHOICES:
                    raise ValueError(f"safe_zone must be one of {', '.join(captions.SAFE_ZONE_CHOICES)}")
            elif isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be true or false")
            elif isinstance(default, int):
                lo, hi = (20, 200) if key == "font_size" else (1, 10)
                if type(value) is not int or not lo <= value <= hi:
                    raise ValueError(f"{key} must be a whole number from {lo} to {hi}")
            elif not isinstance(value, str) or not _COLOUR.fullmatch(value):
                raise ValueError(f"{key} must be a colour as #RRGGBB")
            out[key] = value
        return out

    # ---- export ----

    def export_preset(self, raw) -> dict | None:
        from video import export_presets

        try:
            preset = export_presets.normalize(raw)
        except export_presets.PresetError as e:
            raise ValueError(str(e)) from e
        if preset["kind"] == "original":
            return None
        if self._encoder_for is not None:
            encoder_for = self._encoder_for
        else:
            from video import encoding

            encoder_for = encoding.encoder_for
        if encoder_for(preset["codec"], preset["encoder"]) is None:
            codec = export_presets.CODEC_NAMES.get(preset["codec"], preset["codec"])
            how = {"cpu": " on the processor", "gpu": " on the graphics card"}.get(preset["encoder"], "")
            raise ValueError(f"this PC can't encode {codec}{how}. Choose another codec")
        return preset
