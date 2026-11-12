"""The free music and sound effects that ship with the app (assets/library/,
video/audio_library.py; editor suite step 4).

- The manifest: every file it lists is there with its sha256 and size; ids
  are unique, lowercase letters, digits and dashes; everything is CC0 1.0 and
  names its author, its source and its licence file; every file in the folder
  is listed or is a licence file; NOTICE names the sources; the whole folder
  is at most 25 MB.
- resolve(): a file only for an id the manifest lists whose file is present
  and unchanged; otherwise None and one plain line for the job log.
- A clip's music can name a free track by its id (EditList), and the editor's
  pass plays the bundled file; an unknown id or a damaged file makes the clip
  without music, never fails it. A clip with the person's own file, and one
  with no music, are read exactly as before.
"""

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from video import audio_library
from video_editor import export
from video_editor.timeline import EditList, made_seconds, shift_edit

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "assets" / "library"
MANIFEST = json.loads((LIBRARY / "manifest.json").read_text(encoding="utf-8"))
ITEMS = MANIFEST["items"]
MAX_BYTES = 25 * 1024 * 1024
AUDIO = (".ogg", ".mp3", ".wav")


# ---- the manifest ----------------------------------------------------------------------------


def test_every_listed_file_is_there_with_its_sha256_and_size():
    assert ITEMS
    for e in ITEMS:
        path = LIBRARY / e["file"]
        assert path.is_file(), e["file"]
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == e["sha256"], e["file"]
        assert len(data) == e["bytes"], e["file"]
        assert path.suffix in AUDIO and ".." not in e["file"] and not e["file"].startswith("/"), e["file"]


def test_ids_are_unique_and_well_formed_and_so_are_file_names():
    ids = [e["id"] for e in ITEMS]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", i) for i in ids), ids
    names = [Path(e["file"]).name for e in ITEMS]       # the routes serve a file by its bare name
    assert len(names) == len(set(names))


def test_every_item_says_what_it_is_and_where_it_came_from():
    for e in ITEMS:
        assert e["kind"] in ("music", "sfx"), e["id"]
        assert e["title"].strip() and e["author"].strip(), e["id"]
        assert (e.get("mood") if e["kind"] == "music" else e.get("category")), e["id"]
        assert isinstance(e["seconds"], (int, float)) and e["seconds"] > 0, e["id"]
        assert e["source_url"].startswith("https://"), e["id"]
        assert e["licence"] == "CC0-1.0", e["id"]
        licence = LIBRARY / e["licence_file"]
        assert licence.is_file(), e["id"]
        text = licence.read_text(encoding="utf-8", errors="replace")
        assert "creativecommons.org/publicdomain/zero/1.0" in text, e["licence_file"]
        if e["kind"] == "music":
            # Cut and encoded for the app: the manifest and the licence file both say how.
            assert e.get("changes") and e["changes"] in text and e["source_url"] in text, e["id"]
    assert MANIFEST["licence"] == "CC0-1.0"


def test_there_is_music_in_several_moods_and_a_good_set_of_sound_effects():
    music = [e for e in ITEMS if e["kind"] == "music"]
    sfx = [e for e in ITEMS if e["kind"] == "sfx"]
    assert 6 <= len(music) <= 10 and 40 <= len(sfx) <= 60
    assert {e["mood"] for e in music} >= {"upbeat", "chill", "tense", "funny"}
    assert all(e["seconds"] <= 95 for e in music) and all(e["seconds"] <= 5 for e in sfx)


def test_the_folder_holds_only_listed_files_and_their_licences_and_stays_small():
    found = {p.relative_to(LIBRARY).as_posix() for p in LIBRARY.rglob("*") if p.is_file()}
    listed = {e["file"] for e in ITEMS}
    licences = {e["licence_file"] for e in ITEMS}
    assert found == listed | licences | {"manifest.json"}
    assert sum(p.stat().st_size for p in LIBRARY.rglob("*") if p.is_file()) <= MAX_BYTES


def test_notice_names_the_sources_and_the_packaged_app_gets_the_folder():
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "assets/library/" in notice and "CC0 1.0" in notice
    for author in {e["author"] for e in ITEMS}:
        assert author in notice, author
    for site in {re.match(r"https://([^/]+)/", e["source_url"]).group(1) for e in ITEMS}:
        assert site in notice, site
    spec = (ROOT / "clips-studio.spec").read_text(encoding="utf-8")
    assert '(str(ROOT / "assets" / "library"), "assets/library")' in spec
    assert "*.ogg  binary" in (ROOT / ".gitattributes").read_text(encoding="utf-8")


# ---- the module ------------------------------------------------------------------------------


@pytest.fixture
def copy(tmp_path, monkeypatch):
    """A copy of the library with one track and one sound effect, to break."""
    root = tmp_path / "library"
    keep = [next(e for e in ITEMS if e["id"] == "music-roof-tops"), next(e for e in ITEMS if e["kind"] == "sfx")]
    for e in keep:
        (root / e["file"]).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(LIBRARY / e["file"], root / e["file"])
    (root / "manifest.json").write_text(json.dumps({"items": keep}), encoding="utf-8")
    monkeypatch.setattr(audio_library, "_roots", lambda: [root])
    audio_library.reload()
    yield root, keep
    monkeypatch.undo()
    audio_library.reload()


def test_the_listing_is_what_the_picker_shows():
    listing = audio_library.listing()
    assert [e["id"] for e in listing] == [e["id"] for e in ITEMS]
    track = next(e for e in listing if e["id"] == "music-roof-tops")
    assert track == {"id": "music-roof-tops", "kind": "music", "title": "Roof Tops", "mood": "chill",
                     "seconds": 75.0, "file": "roof-tops.ogg", "licence": "CC0-1.0"}
    sound = next(e for e in listing if e["kind"] == "sfx")
    assert sound["mood"] == ITEMS[0]["category"] and set(sound) == set(track)
    assert [e["id"] for e in audio_library.items("music")] == [e["id"] for e in ITEMS if e["kind"] == "music"]
    assert audio_library.is_item("music-roof-tops", "music") and not audio_library.is_item("music-roof-tops", "sfx")
    assert not audio_library.is_item("Holizna-Roof-Tops") and not audio_library.is_item(None)


def test_resolve_gives_the_file_for_a_good_id():
    for e in ITEMS:
        said: list = []
        assert audio_library.resolve(e["id"], log=said.append) == LIBRARY / e["file"], e["id"]
        assert said == []


def test_resolve_refuses_an_unknown_id_in_plain_words():
    for unknown in ("no-such-track", "", None, "../manifest", "music/roof-tops.ogg", "HOLIZNA-ROOF-TOPS", 7):
        said: list = []
        assert audio_library.resolve(unknown, log=said.append) is None
        assert len(said) == 1 and said[0].endswith("isn't in this copy of the app"), unknown
    assert audio_library.resolve("no-such-track") is None          # no log given: nothing raised
    assert audio_library.why_not("x" * 500).count("x") == 60        # a long id is cut in the log


def test_resolve_refuses_a_missing_file_and_a_changed_one(copy):
    root, keep = copy
    track = keep[0]
    assert audio_library.resolve(track["id"]) == root / track["file"]
    with (root / track["file"]).open("ab") as f:        # changed: one more byte
        f.write(b"\x00")
    said: list = []
    assert audio_library.resolve(track["id"], log=said.append) is None
    assert said == ['The free music "Roof Tops" is missing or damaged in this copy of the app']
    assert audio_library.file_named(Path(track["file"]).name) is None
    (root / track["file"]).unlink()                     # missing
    said.clear()
    assert audio_library.resolve(track["id"], log=said.append) is None and len(said) == 1
    assert audio_library.resolve(keep[1]["id"]) == root / keep[1]["file"]       # the other one is fine


def test_a_manifest_can_never_point_outside_the_folder(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    secret = tmp_path / "secret.ogg"
    secret.write_bytes(b"not a track")
    sha = hashlib.sha256(b"not a track").hexdigest()
    items = [{"id": "escape", "kind": "music", "file": "../secret.ogg", "sha256": sha},
             {"id": "Bad Id", "kind": "music", "file": "a.ogg", "sha256": sha},
             {"id": "other-kind", "kind": "video", "file": "a.ogg", "sha256": sha}]
    (root / "manifest.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    monkeypatch.setattr(audio_library, "_roots", lambda: [root])
    audio_library.reload()
    try:
        assert [e["id"] for e in audio_library.items()] == ["escape"]
        assert audio_library.resolve("escape") is None and audio_library.file_named("secret.ogg") is None
    finally:
        monkeypatch.undo()
        audio_library.reload()


def test_a_copy_of_the_app_with_no_library_has_none(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_library, "_roots", lambda: [tmp_path / "nowhere"])
    audio_library.reload()
    try:
        assert audio_library.folder() is None and audio_library.listing() == []
        assert audio_library.resolve("music-roof-tops") is None
    finally:
        monkeypatch.undo()
        audio_library.reload()


# ---- a clip's music --------------------------------------------------------------------------


def test_the_music_is_all_about_as_loud():
    """Every track was set to one loudness when it was encoded, so the volume
    slider means the same for each: the manifest says by how much."""
    for e in ITEMS:
        if e["kind"] == "music":
            assert re.search(r"turned (down|up) by [0-9.]+ dB so every track is about as loud \(-18 LUFS\)",
                             e["changes"]), e["id"]
            assert e["changes"] in (LIBRARY / e["licence_file"]).read_text(encoding="utf-8"), e["id"]


def test_a_clips_music_can_be_a_free_track():
    edit = EditList.from_dict({"music": {"library": " music-roof-tops ", "volume": 0.4, "duck": False}}, duration=20)
    assert edit is not None and edit.music == {"library": "music-roof-tops", "volume": 0.4, "duck": False}
    assert not edit.is_noop()
    # The same defaults and limits as the person's own file.
    assert EditList.from_dict({"music": {"library": "x"}}, 20).music == {"library": "x", "volume": 0.25, "duck": True}
    assert EditList.from_dict({"music": {"library": "x", "volume": 9}}, 20).music["volume"] == 1.0
    assert EditList.from_dict({"music": {"library": "x", "volume": "loud"}}, 20).music["volume"] == 0.25
    for nothing in ({"library": ""}, {"library": "  "}, {"library": None}, {"library": 5}, {"volume": 0.3}):
        assert EditList.from_dict({"music": nothing}, 20) is None, nothing


def test_the_persons_own_file_is_read_exactly_as_before():
    own = {"music": {"path": " C:/songs/mine.mp3 ", "volume": 0.3, "duck": False}}
    assert EditList.from_dict(own, 20).music == {"path": "C:/songs/mine.mp3", "volume": 0.3, "duck": False}
    # Their own file wins when both are named: the saved music keeps one or the other.
    both = {"music": {"path": "mine.mp3", "library": "music-roof-tops"}}
    assert EditList.from_dict(both, 20).music == {"path": "mine.mp3", "volume": 0.25, "duck": True}
    assert EditList.from_dict({"volume": 1.0}, 20) is None          # no music, nothing to do


def test_a_saved_edit_keeps_its_free_track_through_a_moved_window():
    saved = {"keep": [[0, 5], [10, 20]], "music": {"library": "music-doodles", "volume": 0.3, "duck": True}}
    moved = shift_edit(saved, 100, 120, 98, 120)
    assert moved["music"] == saved["music"]
    assert json.loads(json.dumps(saved))["music"] == saved["music"]
    assert made_seconds(saved, 20) == 15


def test_the_render_plays_the_bundled_file_or_goes_without(capsys):
    track = EditList.from_dict({"music": {"library": "music-roof-tops"}}, 20).music
    assert export._music_file(track) == LIBRARY / "music" / "roof-tops.ogg"
    assert capsys.readouterr().out == ""
    assert export._music_file({"library": "no-such-track", "volume": 0.3, "duck": True}) is None
    assert capsys.readouterr().out == ('      The free music "no-such-track" isn\'t in this copy of the app, '
                                       "so this clip has no music\n")
    # A sound effect is not music: it would loop under the whole clip.
    effect = next(e["id"] for e in ITEMS if e["kind"] == "sfx")
    assert audio_library.resolve(effect) is not None and audio_library.resolve(effect, kind="sfx") is not None
    assert export._music_file({"library": effect, "volume": 0.3, "duck": True}) is None
    assert capsys.readouterr().out == (f'      The free music "{effect}" isn\'t in this copy of the app, '
                                       "so this clip has no music\n")
    assert export._music_file(None) is None
    assert export._music_file({"path": str(ROOT / "no" / "such" / "song.mp3")}) is None       # as before: silently
    assert export._music_file({"path": str(LIBRARY / "manifest.json")}) == LIBRARY / "manifest.json"
    assert capsys.readouterr().out == ""
