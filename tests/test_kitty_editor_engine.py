"""Editor suite step K2: Clips Kitty Editor's engine side (kitty_editor/),
without the routes and without the editor itself.

- Finding the editor (core/binaries.py): only where it was put on purpose.
- The projects' records (registry.py): one folder and one manifest each.
- A clip's project worked out from the clip's settings (build.plan_clip):
  kept ranges as pieces, mutes, speed, captions at their program times, and
  what is not carried said plainly.
- The project made through the editor's automation (automation.py,
  build.py), against the stand-in in tests/fakes/fake_kitty_editor.py.
- Exports taken back in (intake.py): as the clip's video, kept beside a clip
  that changed, or into the person's folder; told twice, a bad name, a crash.
- Storage's clean-up never takes an export or a project (core/housekeeping.py).

Standard library and the app's light modules only: runs on the CI install.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import date
from pathlib import Path

import pytest

from core import binaries, housekeeping
from core import state as core_state
from core.state import StateDB
from kitty_editor import automation, build, intake, registry

FAKE = Path(__file__).resolve().parent / "fakes" / "fake_kitty_editor.py"
NOW = "2026-11-18T09:00:00"
EXE_NAME = "clips-kitty-editor.exe" if os.name == "nt" else "clips-kitty-editor"


@pytest.fixture
def fake_editor(monkeypatch):
    """The stand-in answers for the editor; lengths are told, not probed."""
    monkeypatch.setattr(automation, "command", lambda exe: [sys.executable, str(FAKE)])
    monkeypatch.setattr(registry, "probe_seconds", lambda path: 12.5)
    monkeypatch.setenv("FAKE_KITTY_EDITOR_SECONDS", "30")
    return "the-editor"


@pytest.fixture
def lib(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = StateDB(data_dir / "state.db")
    db.conn.execute("INSERT INTO videos (video_id, title, status, duration, created_at, updated_at)"
                    " VALUES ('vid1', 'Friday stream', 'done', 3600, ?, ?)", (NOW, NOW))
    db.conn.commit()
    yield Lib(db, data_dir)
    db.conn.close()


class Lib:
    def __init__(self, db, data_dir):
        self.db, self.data_dir = db, data_dir

    def open_db(self):
        return StateDB(self.data_dir / "state.db")

    def clip(self, name="clip_00010-00055.mp4", start=10.0, end=55.0) -> tuple[int, Path]:
        folder = self.data_dir / "clips" / "channel" / "Friday stream [vid1]"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(b"the clip as the AI made it")
        cur = self.db.conn.execute(
            "INSERT INTO clips (video_id, start_s, end_s, score, hook, path, title, render_opts, created_at)"
            " VALUES ('vid1', ?, ?, 80, 'h', ?, 'Clip one', '', ?)", (start, end, str(path), NOW))
        self.db.conn.commit()
        return cur.lastrowid, path

    def row(self, clip_id):
        return self.db.conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()

    def project(self, kind="clip", clip=None, name="My clip") -> dict:
        project_id = registry.new_id()
        home = registry.root(self.data_dir) / project_id
        (home / "out").mkdir(parents=True)
        manifest = {"schema": 1, "id": project_id, "name": name, "kind": kind, "state": "ready", "progress": 1.0,
                    "message": "", "created_at": registry.now(), "last_opened_at": registry.now(),
                    "canvas": {"width": 1080, "height": 1920, "fps_num": 30, "fps_den": 1}, "shape": "9:16",
                    "project_dir": str(home / "p" / name), "export_dir": str(home / "out"), "export_name": name,
                    "clip": None, "video_id": None, "source_file": "", "media": [], "notes": [], "exports": []}
        if clip is not None:
            clip_id, path = clip
            manifest["clip"] = {"id": clip_id, "video_id": "vid1", "start_s": 10.0, "end_s": 55.0,
                                "file": str(path), "file_stamp": registry.stamp(path)}
        return registry.save(self.data_dir, manifest)

    def export(self, manifest, name="My clip.mp4", content=b"exported by the editor") -> Path:
        path = Path(manifest["export_dir"]) / name
        path.write_bytes(content)
        return path

    def take(self, manifest, name="My clip.mp4", **more):
        return intake.take_in(self.data_dir, manifest["id"], name, open_db=self.open_db,
                              clip_lock=lambda clip_id: threading.Lock(), **more)


# ---- finding the editor (E1) -------------------------------------------------------------------


def test_the_editor_is_found_only_where_it_was_put(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPS_KITTY_EDITOR_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert binaries.editor_dir() is None and binaries.editor_exe() is None      # a checkout has none

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("CLIPS_KITTY_EDITOR_DIR", str(empty))
    assert binaries.editor_exe() is None                                        # the folder, but no program in it

    there = tmp_path / "editor"
    there.mkdir()
    (there / EXE_NAME).write_bytes(b"")
    monkeypatch.setenv("CLIPS_KITTY_EDITOR_DIR", str(there))
    assert binaries.editor_dir() == there and binaries.editor_exe() == str(there / EXE_NAME)


def test_the_installed_app_carries_the_editor_in_resources(tmp_path, monkeypatch):
    engine = tmp_path / "resources" / "engine"
    editor = tmp_path / "resources" / "editor"
    engine.mkdir(parents=True)
    editor.mkdir()
    (editor / EXE_NAME).write_bytes(b"")
    monkeypatch.delenv("CLIPS_KITTY_EDITOR_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(engine / "clips-studio-engine.exe"))
    assert binaries.editor_dir() == editor
    # The variable, when set, is the only place looked in.
    monkeypatch.setenv("CLIPS_KITTY_EDITOR_DIR", str(tmp_path / "elsewhere"))
    assert binaries.editor_dir() is None


def test_the_editor_is_never_taken_from_the_path_or_the_vendor_folder():
    import inspect

    source = inspect.getsource(binaries.editor_dir) + inspect.getsource(binaries.editor_exe)
    assert "which(" not in source and "_search_roots" not in source and "vendor" not in source.replace(
        "no repo vendor folder", "")


def test_only_clips_kitty_editor_counts_as_the_editor(tmp_path):
    """--version must start with the editor's own name: another program of
    the same file name is not it, and one that doesn't run is not it."""
    assert automation.version_of(str(tmp_path / "missing.exe")) is None
    script = tmp_path / "says.py"
    for said, expected in (("clips-kitty-editor 0.2.11", "0.2.11"), ("concat 0.2.11", None), ("", None)):
        script.write_text(f"print({said!r})", encoding="utf-8")
        real = automation.subprocess.run

        def run(cmd, *args, real=real, **kwargs):
            return real([sys.executable, str(script)], *args, **kwargs)

        automation.subprocess.run = run
        try:
            assert automation.version_of("anything") == expected
        finally:
            automation.subprocess.run = real


def test_the_installed_engine_carries_the_package():
    """Everything of kitty_editor is imported inside a function or by a
    module that is: the build names the package, so an installed copy has it."""
    spec = (Path(__file__).resolve().parent.parent / "clips-studio.spec").read_text(encoding="utf-8")
    assert 'collect_submodules("kitty_editor")' in spec


# ---- the records --------------------------------------------------------------------------------


def test_the_three_places_that_name_the_exports_folder_agree(tmp_path):
    assert registry.exports_folder(tmp_path) == tmp_path.joinpath(*core_state.PROJECT_EXPORTS_FOLDER)
    assert registry.exports_folder(tmp_path) == tmp_path.joinpath(*housekeeping.PROJECT_EXPORTS)
    assert registry.root(tmp_path) == tmp_path.joinpath(*housekeeping.EDITOR_PROJECTS)


@pytest.mark.parametrize("given, expected", [
    ("My clip: part 1?", "My clip part 1"), ("  ", "Clip 7"), ("CON", "Clip 7"), ("a/b\\c", "a b c"),
    ("ends with a dot. ", "ends with a dot"), ("x" * 90, "x" * 60), ("tab\there", "tab here"),
    ("Wait... what happened?", "Wait. what happened"), ("...and then", "and then"), ("....", "Clip 7"),
])
def test_a_project_name_can_be_a_windows_folder(given, expected):
    assert registry.safe_name(given, "Clip 7") == expected


def test_only_a_file_with_a_picture_has_a_length(tmp_path):
    """K-D7: probe_seconds is 0.0 (not a playable video) for an empty file,
    a file of zeros and a sound-only file; a real video has its length."""
    import subprocess

    from core.binaries import ffmpeg, ffprobe

    try:
        subprocess.run([ffprobe(), "-version"], capture_output=True, check=True, timeout=30)
        tool = ffmpeg()
    except (OSError, subprocess.SubprocessError, RuntimeError):
        pytest.skip("FFmpeg is not on this machine")
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    zeros = tmp_path / "zeros.mp4"
    zeros.write_bytes(bytes(4096))
    sound = tmp_path / "sound.m4a"
    video = tmp_path / "video.mp4"
    made = [subprocess.run([tool, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=1", str(sound)],
                           capture_output=True, timeout=60),
            subprocess.run([tool, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=64x64:r=10:d=1",
                            "-pix_fmt", "yuv420p", str(video)], capture_output=True, timeout=60)]
    if any(m.returncode != 0 for m in made):
        pytest.skip("this FFmpeg can't make the test files")
    for path in (empty, zeros, sound, tmp_path / "missing.mp4"):
        assert registry.probe_seconds(path) == 0.0, path.name
    assert registry.probe_seconds(video) == pytest.approx(1.0, abs=0.2)


def test_a_manifest_is_kept_listed_and_deleted(lib):
    first = lib.project(name="First")
    second = lib.project(name="Second")
    registry.update(lib.data_dir, first["id"], lambda m: m.update(last_opened_at="2030-01-01T00:00:00Z"))
    assert [m["name"] for m in registry.list_all(lib.data_dir)] == ["First", "Second"]      # last opened first
    assert registry.load(lib.data_dir, second["id"])["name"] == "Second"
    assert not list((registry.root(lib.data_dir) / first["id"]).glob("*.tmp"))             # written whole
    registry.delete(lib.data_dir, second["id"])
    with pytest.raises(registry.NotFound):
        registry.load(lib.data_dir, second["id"])
    for bad in ("../x", "ABC", "0" * 31, 5):
        with pytest.raises(registry.NotFound):
            registry.load(lib.data_dir, bad)


def test_a_damaged_manifest_is_listed_as_failed_never_hidden(lib):
    manifest = lib.project()
    (registry.root(lib.data_dir) / manifest["id"] / "kitty.json").write_text("{ not json", encoding="utf-8")
    listed = registry.list_all(lib.data_dir)
    assert len(listed) == 1 and listed[0]["state"] == "failed" and listed[0]["id"] == manifest["id"]
    registry.delete(lib.data_dir, manifest["id"])                   # and it can still be removed
    assert registry.list_all(lib.data_dir) == []


def test_a_project_the_editor_holds_is_not_half_deleted(lib, monkeypatch):
    manifest = lib.project()
    home = registry.root(lib.data_dir) / manifest["id"]
    (home / "p" / "My clip").mkdir(parents=True)
    real = registry.shutil.rmtree

    def held(path, *args, **kwargs):
        if Path(path).name == "p":
            raise PermissionError("the editor has it open")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(registry.shutil, "rmtree", held)
    with pytest.raises(registry.InUse):
        registry.delete(lib.data_dir, manifest["id"])
    assert (home / "kitty.json").is_file() and (home / "out").is_dir()      # nothing else went


def test_projects_are_found_by_what_they_are_of_and_follow_a_clips_new_id(lib):
    clip = lib.clip()
    older = lib.project(clip=clip)
    registry.update(lib.data_dir, older["id"], lambda m: m.update(created_at="2020-01-01T00:00:00Z"))
    newer = lib.project(clip=clip)
    assert registry.find_for_clip(lib.data_dir, clip[0])["id"] == newer["id"]
    assert registry.find_for_clip(lib.data_dir, 999) is None
    assert registry.follow_clip_id(lib.data_dir, clip[0], 4242) == 2
    assert registry.find_for_clip(lib.data_dir, 4242)["id"] == newer["id"]
    assert registry.follow_clip_id(lib.data_dir / "nowhere", 1, 2) == 0          # never raises


def test_an_export_file_names_its_project_while_the_project_is_there(lib):
    manifest = lib.project()
    exports = registry.exports_folder(lib.data_dir)
    assert registry.project_of_export(exports / f"{manifest['id']}-1.mp4", lib.data_dir) == manifest["id"]
    assert registry.project_of_export(exports / f"{manifest['id']}-3-2.mov") == manifest["id"]   # folder worked out
    assert registry.project_of_export(exports / f"{'0' * 32}-1.mp4", lib.data_dir) == ""         # a deleted project
    assert registry.project_of_export(exports / "something.mp4", lib.data_dir) == ""


# ---- a clip's project, worked out ---------------------------------------------------------------

LINES = [{"start": 0.5, "end": 3.0, "text": "first line"}, {"start": 3.2, "end": 9.5, "text": "second line"},
         {"start": 11.0, "end": 14.0, "text": "cut away"}, {"start": 16.0, "end": 22.0, "text": "after the cut"},
         {"start": 9.0, "end": 16.5, "text": "across the cut"}]


def plan(opts, lines=LINES, **more):
    return build.plan_clip(opts=opts, window=45.0, base_seconds=45.4, lines=lines, canvas=(1080, 1920), **more)


def test_a_plain_clip_is_one_piece_the_whole_window_with_its_end_pad():
    made = plan({})
    assert made["pieces"] == [{"in": 0.0, "out": 45.4, "muted": False}]
    assert made["length"] == 45.4 and made["speed"] == 1.0 and made["notes"] == ["framing_baked"]
    assert [line["text"] for group in made["captions"] for line in group["lines"]] == [
        "first line", "second line", "across the cut", "cut away", "after the cut"]


def test_kept_ranges_become_pieces_and_captions_move_to_program_time():
    made = plan({"edit": {"keep": [[0.0, 10.0], [15.0, 40.0]]}})
    assert made["pieces"] == [{"in": 0.0, "out": 10.0, "muted": False}, {"in": 15.0, "out": 40.0, "muted": False}]
    assert made["length"] == 35.0
    lines = {line["text"]: (line["start"], line["end"]) for line in made["captions"][0]["lines"]}
    assert "cut away" not in lines                                     # cut away: dropped
    assert lines["first line"] == (0.5, 3.0)
    assert lines["after the cut"] == (11.0, 17.0)                      # five seconds earlier
    assert lines["across the cut"] == (9.0, 11.5)                      # held to what is kept


def test_mutes_split_the_pieces_and_speed_shortens_the_program():
    made = plan({"edit": {"keep": [[0.0, 10.0], [15.0, 40.0]], "mutes": [[20.0, 21.0], [8.0, 16.0]],
                          "speed": 2.0, "volume": 0.5, "fade_in": 0.3, "fade_out": 0.7}})
    assert made["pieces"] == [
        {"in": 0.0, "out": 8.0, "muted": False}, {"in": 8.0, "out": 10.0, "muted": True},
        {"in": 15.0, "out": 16.0, "muted": True}, {"in": 16.0, "out": 20.0, "muted": False},
        {"in": 20.0, "out": 21.0, "muted": True}, {"in": 21.0, "out": 40.0, "muted": False}]
    assert made["speed"] == 2.0 and made["length"] == 17.5
    assert (made["volume"], made["fade_in"], made["fade_out"]) == (0.5, 0.3, 0.7)
    lines = {line["text"]: (line["start"], line["end"]) for line in made["captions"][0]["lines"]}
    assert lines["after the cut"] == (5.5, 8.5)                        # program time at double speed
    assert all(p["muted"] for p in plan({"edit": {"mute_all": True}})["pieces"])


def test_the_caption_style_is_carried_as_far_as_the_editors_text_goes():
    style = {"color": "#FFE600", "font": "Arial", "font_size": 96, "uppercase": True, "position": "top"}
    made = plan({"caption_style": style})
    look = made["captions"][0]["style"]
    assert look["color"] == "#ffe600" and look["textCase"] == "upper" and look["fontFamily"] == "Arial"
    assert look["fontSize"] == round(96 / 1920, 5) and look["strokeWidth"] > 0 and look["strokeColor"] == "#000000"
    assert made["captions"][0]["offsetY"] == -0.35
    assert plan({"caption_style": {"position": "middle"}})["captions"][0]["offsetY"] == 0.0
    assert plan({"caption_style": {"uppercase": False}})["captions"][0]["style"]["textCase"] == "original"
    # A safe zone moves the row clear of the app's own buttons (portrait only).
    zoned = plan({"caption_style": {"position": "bottom", "safe_zone": "tiktok"}})["captions"][0]["offsetY"]
    assert 0.0 < zoned < 0.35


def test_what_is_not_carried_is_said_plainly():
    assert "captions_plain" in plan({"caption_style": {"highlight": True}})["notes"]
    assert "captions_plain" in plan({"caption_style": {"animation": "pop"}})["notes"]
    assert "font_default" in plan({"caption_style": {"font": "A Font Nobody Has"}})["notes"]
    assert "fontFamily" not in plan({"caption_style": {"font": "A Font Nobody Has"}})["captions"][0]["style"]
    assert plan({"caption_style": {"highlight": True}}, lines=[])["notes"] == ["framing_baked"]    # no captions: no note
    off = plan({}, captions=False)
    assert off["captions"] == [] and off["notes"] == ["framing_baked"]


def test_the_other_speakers_lines_get_their_own_colour():
    lines = [{"start": 0, "end": 2, "text": "me"}, {"start": 2, "end": 4, "text": "them", "speaker": 1}]
    made = plan({"caption_style": {"second_speaker": True, "second_speaker_color": "#5CE1FF"}}, lines=lines)
    assert [[line["text"] for line in g["lines"]] for g in made["captions"]] == [["me"], ["them"]]
    assert made["captions"][1]["style"]["color"] == "#5ce1ff" != made["captions"][0]["style"]["color"]
    one = plan({"caption_style": {"second_speaker": False}}, lines=lines)
    assert len(one["captions"]) == 1 and len(one["captions"][0]["lines"]) == 2


def test_hook_music_and_watermark_are_carried_where_the_editor_has_an_equivalent(tmp_path):
    song, logo = tmp_path / "song.mp3", tmp_path / "logo.png"
    made = plan({"edit": {"hook": {"text": "Wait for it", "seconds": 3},
                          "music": {"path": "x.mp3", "volume": 0.4, "duck": True}}},
                music_file=song, watermark={"type": "both", "text": "@me", "position": "top_left", "opacity": 0.5,
                                            "image_asset": "logo.png", "scale": 0.2},
                watermark_image=logo)
    assert made["hook"] == {"text": "Wait for it", "seconds": 3.0}
    assert made["music"] == {"file": str(song), "volume": 0.4} and "music_duck" in made["notes"]
    assert made["watermark_text"]["text"] == "@me" and made["watermark_text"]["offsetX"] < 0 > made[
        "watermark_text"]["offsetY"]
    assert made["watermark_text"]["style"]["opacity"] == 0.5
    assert made["watermark_image"]["file"] == str(logo) and made["watermark_image"]["corner"] == (-1.0, -1.0)
    # Music whose file is gone, and a watermark that moves, are said.
    gone = plan({"edit": {"music": {"path": "x.mp3"}}}, watermark={"type": "text", "text": "@me",
                                                                    "position": "moving"})
    assert gone["music"] is None and {"music_missing", "watermark_still"} <= set(gone["notes"])
    assert "music_duck" not in plan({"edit": {"music": {"path": "x.mp3", "duck": False}}}, music_file=song)["notes"]


# ---- the project made through the editor's automation -------------------------------------------


def saved(project_dir) -> dict:
    return json.loads((Path(project_dir) / "concat.json").read_text(encoding="utf-8"))


def test_a_clips_project_is_built_saved_and_closed(tmp_path, fake_editor, monkeypatch):
    monkeypatch.setenv("FAKE_KITTY_EDITOR_SECONDS", "45.4")
    home = tmp_path / "project"
    (home / "media").mkdir(parents=True)
    base, song, logo = home / "media" / "base.mp4", home / "media" / "music.mp3", home / "media" / "watermark.png"
    for f in (base, song, logo):
        f.write_bytes(b"x")
    made = plan({"edit": {"keep": [[0.0, 10.0], [15.0, 40.0]], "mutes": [[20.0, 21.0]], "volume": 0.8,
                          "fade_out": 0.5, "hook": {"text": "Wait for it", "seconds": 3},
                          "music": {"path": "x", "volume": 0.3}}},
                music_file=song, watermark={"type": "both", "text": "@me", "image_asset": "logo.png"},
                watermark_image=logo)
    canvas = {"width": 1080, "height": 1920, "fps_num": 30, "fps_den": 1}
    with build.open_session(fake_editor, home, [base.parent]) as session:
        project_dir = build.apply_clip_plan(session, home, "My clip", canvas, base, made)
    assert Path(project_dir) == home / "p" / "My clip"
    assert not (Path(project_dir) / "concat.lock").exists()         # closed: the window can open it
    doc = saved(project_dir)
    assert doc["video"] == {"width": 1080, "height": 1920, "rateNum": 30, "rateDen": 1}
    main = [c for c in doc["clips"] if c["kind"] == "video" and c["track"] == "T4"]
    assert [(c["start"], c["duration"], c["sourceStart"]) for c in main] == [
        (0.0, 10.0, 0.0), (10.0, 5.0, 15.0), (15.0, 1.0, 20.0), (16.0, 19.0, 21.0)]     # back to back
    assert [bool(c.get("muted")) for c in main] == [False, False, True, False]
    assert all(c["volume"] == 0.8 and c["framing"] == "fill" for c in main)
    assert main[-1]["fadeOut"] == 0.5 and "fadeIn" not in main[0]
    texts = {c["text"]: c for c in doc["clips"] if c["kind"] == "text"}
    assert texts["after the cut"]["start"] == 11.0 and "cut away" not in texts
    assert texts["Wait for it"]["duration"] == 3.0 and texts["Wait for it"]["offsetY"] < 0
    assert texts["@me"]["duration"] == 35.0 and texts["@me"]["offsetX"] > 0
    music = [c for c in doc["clips"] if c.get("name") == "music.mp3"]
    assert doc["tracks"][0] == music[0]["track"]                       # under every other lane
    assert music[0]["start"] == 0.0 and music[0]["duration"] == 35.0 and music[0]["volume"] == 0.3
    mark = next(c for c in doc["clips"] if c.get("name") == "watermark.png")
    assert doc["tracks"][-1] == mark["track"] and mark["duration"] == 35.0 and mark["opacity"] == 0.85
    assert (home / "build.log").is_file()


def test_speed_is_put_on_every_piece_and_the_lane_closes_up(tmp_path, fake_editor, monkeypatch):
    monkeypatch.setenv("FAKE_KITTY_EDITOR_SECONDS", "45.4")
    home = tmp_path / "project"
    home.mkdir()
    base = home / "base.mp4"
    base.write_bytes(b"x")
    made = plan({"edit": {"keep": [[0.0, 10.0], [15.0, 40.0]], "speed": 2.0}}, lines=[])
    with build.open_session(fake_editor, home, []) as session:
        project_dir = build.apply_clip_plan(session, home, "Fast", {"width": 1080, "height": 1920,
                                                                    "fps_num": 30, "fps_den": 1}, base, made)
    main = [c for c in saved(project_dir)["clips"] if c["kind"] == "video"]
    assert [(c["start"], c["duration"], c["speed"]) for c in main] == [(0.0, 5.0, 2.0), (5.0, 12.5, 2.0)]


def test_short_music_is_repeated_to_the_programs_end(tmp_path, fake_editor, monkeypatch):
    monkeypatch.setenv("FAKE_KITTY_EDITOR_SECONDS", "30")
    home = tmp_path / "project"
    home.mkdir()
    base, song = home / "base.mp4", home / "song.mp3"
    base.write_bytes(b"x")
    song.write_bytes(b"y")
    made = build.plan_clip(opts={"edit": {"music": {"path": "x", "volume": 0.2}}}, window=30.0, base_seconds=30.0,
                           lines=[], canvas=(1080, 1920), music_file=song)
    made["length"] = 75.0           # as if the program were longer than the music
    with build.open_session(fake_editor, home, []) as session:
        path, created = build._create(session, home, "Loop", {"width": 1080, "height": 1920,
                                                              "fps_num": 30, "fps_den": 1})
        build._add_music(session, path, made["music"], 75.0)
        session.tool("project_close", path=path, save=True)
    music = saved(path)["clips"]
    assert [(c["start"], c["duration"]) for c in music] == [(0.0, 30.0), (30.0, 30.0), (60.0, 15.0)]


def test_a_whole_video_is_one_piece_shown_whole(tmp_path, fake_editor):
    home = tmp_path / "project"
    home.mkdir()
    source = tmp_path / "videos" / "talk.mp4"
    source.parent.mkdir()
    source.write_bytes(b"x")
    canvas = {"width": 1920, "height": 1080, "fps_num": 30000, "fps_den": 1001}
    with build.open_session(fake_editor, home, [source.parent]) as session:
        project_dir = build.build_whole(session, home, "Talk", canvas, source)
    doc = saved(project_dir)
    assert doc["video"] == {"width": 1920, "height": 1080, "rateNum": 30000, "rateDen": 1001}
    assert [(c["start"], c["duration"], c["framing"]) for c in doc["clips"]] == [(0.0, 30.0, "fit")]
    assert not (Path(project_dir) / "concat.lock").exists()


def test_the_editor_is_only_let_into_the_projects_own_folders(tmp_path, fake_editor):
    home = tmp_path / "project"
    home.mkdir()
    elsewhere = tmp_path / "elsewhere" / "secret.mp4"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"x")
    with pytest.raises(automation.EditorError, match="outside"):
        with build.open_session(fake_editor, home, []) as session:
            build.build_whole(session, home, "Talk", {"width": 1920, "height": 1080, "fps_num": 30, "fps_den": 1},
                              elsewhere)


def test_a_refusal_is_the_editors_own_sentence_and_nothing_is_left_running(tmp_path, fake_editor, monkeypatch):
    monkeypatch.setenv("FAKE_KITTY_EDITOR_FAIL", "clip_add")
    home = tmp_path / "project"
    home.mkdir()
    source = home / "talk.mp4"
    source.write_bytes(b"x")
    session = build.open_session(fake_editor, home, [])
    with pytest.raises(automation.EditorError, match="clip_add was made to fail"):
        with session:
            build.build_whole(session, home, "Talk", {"width": 1920, "height": 1080, "fps_num": 30, "fps_den": 1},
                              source)
    assert session._proc.poll() is not None                         # stopped and waited for


def test_an_editor_that_never_answers_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(automation, "command", lambda exe: [sys.executable, "-c", "import time; time.sleep(60)"])
    session = automation.Session("x", [tmp_path], tmp_path / "build.log", timeout=1.0)
    with pytest.raises(automation.EditorError, match="too long"):
        session.initialize()
    assert session._proc.poll() is not None


def test_an_editor_that_ends_early_is_an_error_not_a_hang(tmp_path, monkeypatch):
    monkeypatch.setattr(automation, "command", lambda exe: [sys.executable, "-c", "pass"])
    session = automation.Session("x", [tmp_path], tmp_path / "build.log", timeout=20.0)
    with pytest.raises(automation.EditorError):
        session.initialize()
    monkeypatch.setattr(automation, "command", lambda exe: [str(tmp_path / "no-such-program")])
    with pytest.raises(automation.EditorError, match="could not be started"):
        automation.Session("x", [tmp_path], tmp_path / "build.log")


def test_the_automation_is_started_headless():
    assert automation.command("C:/x/clips-kitty-editor.exe") == ["C:/x/clips-kitty-editor.exe", "--mcp", "headless"]


# ---- exports come back (E4) ---------------------------------------------------------------------


def test_an_export_becomes_the_clips_video(lib, fake_editor):
    clip_id, old = lib.clip()
    manifest = lib.project(clip=(clip_id, old))
    lib.export(manifest)
    events = []
    answer = lib.take(manifest, publish=events.append)
    target = registry.exports_folder(lib.data_dir) / f"{manifest['id']}-1.mp4"
    assert answer == {"result": "replaced_clip", "clip_id": clip_id, "path": str(target), "folder": str(target.parent),
                      "file": target.name, "seconds": 12.5, "again": False}
    row = lib.row(clip_id)
    assert row["path"] == str(target) and target.read_bytes() == b"exported by the editor"
    assert row["edited_at"]                                          # Make clips again keeps it
    assert (row["start_s"], row["end_s"]) == (10.0, 55.0)             # the AI's window is not changed
    assert not old.exists()                                           # the old file went, after it worked
    assert not list(Path(manifest["export_dir"]).iterdir())           # out/ is empty again
    after = registry.load(lib.data_dir, manifest["id"])
    assert after["clip"]["file"] == str(target) and after["clip"]["file_stamp"] == registry.stamp(target)
    assert after["exports"][0]["result"] == "replaced_clip" and after["exports"][0]["seconds"] == 12.5
    assert events == [{"type": "clip_file_replaced", "clip_id": clip_id, "project_id": manifest["id"]},
                      {"type": "queue"}]
    assert lib.db.project_export_of(clip_id) == ""                    # known as an editor export from now on


def test_exporting_twice_replaces_the_first_export(lib, fake_editor):
    clip_id, old = lib.clip()
    manifest = lib.project(clip=(clip_id, old))
    lib.export(manifest, content=b"first")
    first = Path(lib.take(manifest)["path"])
    lib.export(manifest, content=b"second")                           # the same name, written again
    second = Path(lib.take(manifest)["path"])
    assert second.name == f"{manifest['id']}-2.mp4" and second.read_bytes() == b"second"
    assert lib.row(clip_id)["path"] == str(second)
    assert not first.exists()                                         # the first was the "old file" this time
    assert len(registry.load(lib.data_dir, manifest["id"])["exports"]) == 2


def test_an_export_told_twice_is_answered_twice_and_taken_once(lib, fake_editor):
    clip_id, old = lib.clip()
    manifest = lib.project(clip=(clip_id, old))
    lib.export(manifest)
    first = lib.take(manifest)
    again = lib.take(manifest)
    assert again == {**first, "again": True}
    assert len(registry.load(lib.data_dir, manifest["id"])["exports"]) == 1


def test_a_clip_changed_since_keeps_its_file_and_the_export_is_kept_beside_it(lib, fake_editor):
    clip_id, old = lib.clip()
    manifest = lib.project(clip=(clip_id, old))
    old.write_bytes(b"Quick edit rendered it again, and it is longer now")
    lib.export(manifest)
    events = []
    answer = lib.take(manifest, publish=events.append)
    assert answer["result"] == "kept_beside" and answer["reason"] == "clip_changed" and answer["clip_id"] == clip_id
    kept = Path(answer["path"])
    assert kept.parent == registry.exports_folder(lib.data_dir) and kept.is_file()
    assert lib.row(clip_id)["path"] == str(old) and old.is_file()     # the clip is left alone
    assert lib.db.project_export_of(clip_id) is None
    assert events[0]["type"] == "editor_export" and events[0]["result"] == "kept_beside"


def test_a_clip_on_another_file_is_changed_too(lib, fake_editor):
    clip_id, old = lib.clip()
    manifest = lib.project(clip=(clip_id, old))
    newer = old.with_name("clip_00012-00050.mp4")
    newer.write_bytes(b"the clip as it was")
    lib.db.set_clip(clip_id, path=str(newer))
    lib.export(manifest)
    assert lib.take(manifest)["result"] == "kept_beside"
    assert lib.row(clip_id)["path"] == str(newer)


def test_a_name_typed_in_the_export_sheet_is_kept(lib, fake_editor, tmp_path):
    """UX round: the file is called what the Export sheet said, copies -2."""
    manifest = lib.project(kind="video", name="Friday stream")
    folder = tmp_path / "mine"
    names = []
    for _ in range(2):
        lib.export(manifest, "Best bits.mp4")
        names.append(lib.take(manifest, "Best bits.mp4", folder=str(folder))["file"])
    assert names == ["Best bits.mp4", "Best bits-2.mp4"]
    today = date.today().isoformat()
    lib.export(manifest, f"Friday stream - {today}.mp4")
    assert lib.take(manifest, f"Friday stream - {today}.mp4", folder=str(folder))["file"] == f"Friday stream - {today}.mp4"


def test_with_no_clip_the_export_goes_to_the_persons_folder(lib, fake_editor, tmp_path):
    manifest = lib.project(kind="video", name="Friday stream")
    folder = tmp_path / "my exports"
    events = []
    names = []
    for _ in range(3):
        lib.export(manifest, "Friday stream.mp4")
        answer = lib.take(manifest, "Friday stream.mp4", folder=str(folder), publish=events.append)
        assert answer["result"] == "saved_to_folder" and answer["folder"] == str(folder)
        names.append(answer["file"])
    today = date.today().isoformat()
    assert names == [f"Friday stream - {today}.mp4", f"Friday stream - {today}-2.mp4",
                     f"Friday stream - {today}-3.mp4"]
    assert all((folder / n).is_file() for n in names)
    assert events[0] == {"type": "editor_export", "project_id": manifest["id"], "result": "saved_to_folder",
                         "path": str(folder / names[0])}


def test_a_deleted_clips_project_still_exports_to_the_persons_folder(lib, fake_editor, tmp_path):
    clip_id, old = lib.clip()
    manifest = lib.project(clip=(clip_id, old))
    lib.db.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    lib.db.conn.commit()
    lib.export(manifest, "My clip.mov")
    answer = lib.take(manifest, "My clip.mov", folder=str(tmp_path / "out"))
    assert answer["result"] == "saved_to_folder" and answer["file"].endswith(".mov")
    assert old.is_file()                                              # nothing of the clip's was touched


def test_with_no_folder_given_downloads_then_the_apps_own_folder(lib, fake_editor, tmp_path, monkeypatch):
    """K-D22: the Downloads folder the app's own Export buttons use
    (intake.downloads_folder), else the app's own folder."""
    downloads = tmp_path / "home" / "Downloads"
    downloads.mkdir(parents=True)
    monkeypatch.setattr(intake, "downloads_folder", lambda: downloads)
    manifest = lib.project(kind="file", name="Talk")
    lib.export(manifest, "Talk.mp4")
    assert Path(lib.take(manifest, "Talk.mp4")["folder"]) == downloads
    monkeypatch.setattr(intake, "downloads_folder", lambda: tmp_path / "no home" / "Downloads")
    lib.export(manifest, "Talk.mp4")
    assert Path(lib.take(manifest, "Talk.mp4")["folder"]) == registry.exports_folder(lib.data_dir)
    monkeypatch.setattr(intake, "downloads_folder", lambda: None)
    lib.export(manifest, "Talk.mp4")
    assert Path(lib.take(manifest, "Talk.mp4")["folder"]) == registry.exports_folder(lib.data_dir)


def test_the_downloads_folder_is_the_one_windows_knows(monkeypatch, tmp_path):
    """On Windows the Downloads known folder, which a person may have moved
    off C: (what Electron's app.getPath("downloads") gives the Export
    buttons); elsewhere ~/Downloads."""
    import os

    found = intake.downloads_folder()
    assert found is not None and found.is_absolute()
    if os.name != "nt":
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert intake.downloads_folder() == tmp_path / "Downloads"


def test_a_waiting_report_is_had_once_the_lock_is_free(lib, fake_editor):
    """K-D17: take_in(wait=<seconds>) waits for the project's lock."""
    import threading

    manifest = lib.project(kind="video")
    lib.export(manifest)
    lock = intake._project_lock(manifest["id"])
    lock.acquire()
    threading.Timer(0.3, lock.release).start()
    assert lib.take(manifest, folder=str(lib.data_dir / "out"), wait=5.0)["result"] == "saved_to_folder"
    lib.export(manifest, "second.mp4")
    with lock:
        with pytest.raises(intake.Busy):
            lib.take(manifest, "second.mp4", wait=0.1)


@pytest.mark.parametrize("folder", ["\\\\server\\share\\out", "//server/share/out", "relative/out"])
def test_a_network_or_relative_export_folder_is_refused_and_nothing_moves(lib, fake_editor, folder):
    manifest = lib.project(kind="video")
    made = lib.export(manifest)
    with pytest.raises(intake.BadFolder):
        lib.take(manifest, folder=folder)
    assert made.is_file()


@pytest.mark.parametrize("name", ["../kitty.json", "..\\x.mp4", "a/b.mp4", "C:x.mp4", "", ".", "..", "x.exe",
                                  "x.mp4 ", "x" * 260 + ".mp4", "nul\x00.mp4", "a\\b.mp4", "..mp4",
                                  ".x.concat-video.mp4", ".x.concat-audio.m4a", "x?.mp4", 'x".mp4', "x|.mp4"])
def test_only_a_plain_file_name_is_taken(lib, fake_editor, name):
    manifest = lib.project(kind="video")
    with pytest.raises(intake.BadName):
        lib.take(manifest, name)


def test_nothing_outside_the_projects_out_folder_can_be_named(lib, fake_editor, tmp_path):
    manifest = lib.project(kind="video")
    with pytest.raises(intake.NoSuchExport):
        lib.take(manifest, "never made.mp4")
    (Path(manifest["export_dir"]) / "a folder.mp4").mkdir()
    with pytest.raises(intake.NoSuchExport):
        lib.take(manifest, "a folder.mp4")
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"someone else's file")
    link = Path(manifest["export_dir"]) / "link.mp4"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this system makes no symbolic links here")
    with pytest.raises(intake.NoSuchExport):
        lib.take(manifest, "link.mp4")
    assert outside.is_file()


def test_a_second_report_while_one_is_being_taken_in_is_told_busy(lib, fake_editor):
    manifest = lib.project(kind="video")
    lib.export(manifest)
    lock = intake._project_lock(manifest["id"])
    with lock:
        with pytest.raises(intake.Busy):
            lib.take(manifest)
    assert lib.take(manifest, folder=str(lib.data_dir / "out"))["result"] == "saved_to_folder"


def test_a_failure_before_the_clip_is_changed_leaves_everything(lib, fake_editor, monkeypatch):
    clip_id, old = lib.clip()
    manifest = lib.project(clip=(clip_id, old))
    made = lib.export(manifest)

    def broken(self, clip_id, **fields):
        raise RuntimeError("the database is locked")

    monkeypatch.setattr(StateDB, "set_clip", broken)
    with pytest.raises(intake.Failed):
        lib.take(manifest)
    assert lib.row(clip_id)["path"] == str(old) and old.read_bytes() == b"the clip as the AI made it"
    assert made.is_file()                                             # back in out/, to be told again
    assert registry.load(lib.data_dir, manifest["id"])["exports"] == []


def test_exports_left_behind_by_a_crash_are_taken_in_when_the_project_opens(lib, fake_editor, monkeypatch):
    clip_id, old = lib.clip()
    manifest = lib.project(clip=(clip_id, old))
    first, second = lib.export(manifest, "one.mp4", b"one"), lib.export(manifest, "two.mp4", b"two")
    os.utime(first, (1_000_000, 1_000_000))
    os.utime(second, (2_000_000, 2_000_000))
    still_writing = lib.export(manifest, "three.mp4", b"thr")         # changed just now: left for later
    (Path(manifest["export_dir"]) / "notes.txt").write_text("not an export", encoding="utf-8")
    unfinished = lib.export(manifest, ".four.concat-video.mp4", b"half a render")    # the editor's own, mid-export
    os.utime(unfinished, (500_000, 500_000))
    answers = intake.recover(lib.data_dir, manifest["id"], open_db=lib.open_db,
                             clip_lock=lambda clip_id: threading.Lock())
    assert [a["result"] for a in answers] == ["replaced_clip", "replaced_clip"]      # oldest first
    assert Path(lib.row(clip_id)["path"]).read_bytes() == b"two"
    assert still_writing.is_file() and unfinished.is_file()
    assert intake.recover(lib.data_dir, "0" * 32, open_db=lib.open_db) == []          # never raises


def test_an_exports_length_is_kept_and_probed_only_when_nothing_knows_the_file(lib, fake_editor, monkeypatch):
    clip_id, old = lib.clip()
    manifest = lib.project(clip=(clip_id, old))
    lib.export(manifest)
    target = lib.take(manifest)["path"]
    probes = []
    monkeypatch.setattr(registry, "probe_seconds", lambda path: probes.append(path) or 7.0)
    assert registry.exports_for(lib.data_dir, [target]) == {target: {"seconds": 12.5, "project_id": manifest["id"]}}
    assert probes == []                                               # kept at take-in
    registry.delete(lib.data_dir, manifest["id"])
    assert registry.exports_for(lib.data_dir, [target]) == {target: {"seconds": 7.0, "project_id": ""}}
    registry.exports_for(lib.data_dir, [target])
    assert len(probes) == 1                                           # and then remembered


# ---- nothing is swept (E5) ----------------------------------------------------------------------


def test_storage_clean_up_never_takes_an_export_or_a_project(lib, fake_editor):
    clip_id, old = lib.clip()
    manifest = lib.project(clip=(clip_id, old))
    lib.export(manifest)
    taken = Path(lib.take(manifest)["path"])
    orphan = registry.exports_folder(lib.data_dir) / f"{'0' * 32}-1.mp4"       # an export no row points at
    orphan.write_bytes(b"kept beside a clip")
    plain_orphan = lib.data_dir / "clips" / "channel" / "Friday stream [vid1]" / "clip_00001-00002.mp4"
    plain_orphan.write_bytes(b"a render no row points at")
    base = registry.root(lib.data_dir) / manifest["id"] / "media" / "base.mp4"
    base.parent.mkdir(parents=True)
    base.write_bytes(b"the project's video")

    found = housekeeping.survey(lib.db, lib.data_dir)
    offered = {str(Path(f).resolve()) for files in found["_groups"].values() for f in files}
    assert str(plain_orphan.resolve()) in offered                     # as before for every other file
    assert str(taken.resolve()) not in offered and str(orphan.resolve()) not in offered
    housekeeping.clean(lib.db, lib.data_dir)
    assert taken.is_file() and orphan.is_file() and base.is_file() and not plain_orphan.exists()
    assert (registry.root(lib.data_dir) / manifest["id"] / "kitty.json").is_file()


def test_a_download_a_project_uses_is_not_freed_as_an_orphan(lib):
    downloads = lib.data_dir / "downloads"
    downloads.mkdir()
    used, unused = downloads / "gone1.mp4", downloads / "gone2.mp4"         # neither video is in the library
    used.write_bytes(b"a")
    unused.write_bytes(b"b")
    manifest = lib.project(kind="video")
    registry.update(lib.data_dir, manifest["id"], lambda m: m.update(media=[str(used)]))
    housekeeping.clean(lib.db, lib.data_dir)
    assert used.is_file() and not unused.exists()


def test_a_clip_is_an_editor_export_by_where_its_file_is(lib):
    clip_id, old = lib.clip()
    assert lib.db.project_export_of(clip_id) is None and lib.db.project_export_clips([clip_id]) == {}
    exports = registry.exports_folder(lib.data_dir)
    exports.mkdir(parents=True)
    (exports / "anything.mp4").write_bytes(b"x")
    lib.db.set_clip(clip_id, path=str(exports / "anything.mp4"))
    assert lib.db.project_export_of(clip_id) == ""                    # with no project at all
    assert lib.db.project_export_clips([clip_id, "x", clip_id, 999]) == {clip_id: ""}
    deeper = exports / "sub" / "x.mp4"
    lib.db.set_clip(clip_id, path=str(deeper))
    assert lib.db.project_export_of(clip_id) is None                  # directly in the folder only
    assert lib.db.project_export_of(2**70) is None and lib.db.project_export_of(999) is None
    assert core_state.in_project_exports(str(exports / ".." / "projects" / "a.mp4"), lib.data_dir)
    assert not core_state.in_project_exports(str(exports / "a.mp4"), None)
