"""Editor suite step 6: a media id becomes a file in one place
(video_editor/media/resolver.py), for every media shape a project can name,
and never anything outside the app's folders. Standard library only: runs
on CI's light install.
"""

import os
from pathlib import Path

import pytest
from media_helpers import add_project, add_video

from core.state import StateDB
from video import audio_library
from video_editor.media import resolver
from video_editor.project import store

SHA = "ab" * 32


@pytest.fixture
def lib(tmp_path):
    data = tmp_path / "data"
    db = StateDB(data / "state.db")
    (data / "downloads").mkdir()
    yield db, data
    db.conn.close()


def _resolve(lib, media_id):
    db, data = lib
    return resolver.resolve(db.conn, data, media_id)


def test_a_downloaded_video(lib):
    db, data = lib
    add_video(db.conn, "abcDEF_12-3", "Friday stream")
    (data / "downloads" / "abcDEF_12-3.mp4").write_bytes(b"video")
    found = _resolve(lib, "video:abcDEF_12-3")
    assert found.ref == "video:abcDEF_12-3" and found.kind == "video" and found.name == "Friday stream"
    assert found.path == (data / "downloads" / "abcDEF_12-3.mp4").resolve()


def test_a_video_in_another_container(lib):
    db, data = lib
    add_video(db.conn, "v2")
    (data / "downloads" / "v2.webm").write_bytes(b"video")
    assert _resolve(lib, "video:v2").path.name == "v2.webm"


def test_a_missing_download_says_what_is_missing(lib):
    db, _data = lib
    add_video(db.conn, "v3", "Monday stream")
    with pytest.raises(resolver.NotFound, match="download of \u201cMonday stream\u201d is missing"):
        _resolve(lib, "video:v3")
    with pytest.raises(resolver.NotFound, match="no video 'v4' in the library"):
        _resolve(lib, "video:v4")


def test_a_download_with_no_library_row_is_still_found(lib):
    """A project can pin a download whose video left the library."""
    _db, data = lib
    (data / "downloads" / "gone1.mp4").write_bytes(b"video")
    assert _resolve(lib, "video:gone1").path.name == "gone1.mp4"


def test_a_clips_rendered_file(lib):
    db, data = lib
    add_video(db.conn, "v1")
    clip_file = data / "clips" / "someone" / "A stream [v1]" / "clip_00010-00040.mp4"
    clip_file.parent.mkdir(parents=True)
    clip_file.write_bytes(b"clip")
    clip_id = db.add_clip("v1", 10.0, 40.0, 80, "hook", path=str(clip_file), title="Best moment")
    found = _resolve(lib, f"clip:{clip_id}")
    assert found.path == clip_file.resolve() and found.name == "Best moment" and found.kind == "video"
    with pytest.raises(resolver.NotFound, match="no clip 999"):
        _resolve(lib, "clip:999")
    with pytest.raises(resolver.NotFound):
        _resolve(lib, "clip:99999999999999999999999")


def test_a_clip_file_outside_the_data_folder_is_never_served(lib, tmp_path):
    db, _data = lib
    add_video(db.conn, "v1")
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"not ours")
    clip_id = db.add_clip("v1", 10.0, 40.0, 80, "hook", path=str(outside))
    with pytest.raises(resolver.NotFound, match="outside Clips Kitty's folders"):
        _resolve(lib, f"clip:{clip_id}")


def test_an_imported_file(lib, tmp_path):
    db, data = lib
    sound = tmp_path / "my sound.wav"
    sound.write_bytes(b"RIFF" + bytes(100))
    row = store.add_asset(db.conn, data, sound, kind="audio", name="My sound")
    found = _resolve(lib, f"asset:{row['sha256']}")
    assert found.kind == "audio" and found.sha256 == row["sha256"] and found.name == "My sound"
    assert found.path == store.asset_path(data, row["sha256"], ".wav").resolve()
    found.path.unlink()
    with pytest.raises(resolver.NotFound, match="imported file \u201cMy sound\u201d is missing"):
        _resolve(lib, f"asset:{row['sha256']}")
    with pytest.raises(resolver.NotFound, match="never imported"):
        _resolve(lib, f"asset:{SHA}")


def test_a_library_sound():
    item = audio_library.items()[0]
    found = resolver.resolve(None, Path("."), f"library:{item['id']}")
    assert found.kind == "audio" and found.path == audio_library.resolve(item["id"]).resolve()
    with pytest.raises(resolver.NotFound, match="not one of the app's free"):
        resolver.resolve(None, Path("."), "library:no-such-sound")


def test_every_media_shape_of_a_project(lib, tmp_path):
    db, data = lib
    add_video(db.conn, "v1")
    (data / "downloads" / "v1.mp4").write_bytes(b"video")
    sound = tmp_path / "sound.wav"
    sound.write_bytes(b"RIFF" + bytes(50))
    row = store.add_asset(db.conn, data, sound, kind="audio")
    music = data / "music" / "song.mp3"
    music.parent.mkdir()
    music.write_bytes(b"mp3")
    item = audio_library.items()[0]["id"]
    pid = add_project(db.conn, {
        "m1": ("video", {"type": "video", "video_id": "v1"}),
        "m2": ("audio", {"type": "asset", "sha256": row["sha256"]}),
        "m3": ("audio", {"type": "library", "id": item}),
        "m4": ("audio", {"type": "file", "path": str(music)}),
        "m5": ("audio", {"type": "file", "path": str(tmp_path / "outside.mp3")}),
        "m6": ("video", {"type": "something-new"}),
    })
    (tmp_path / "outside.mp3").write_bytes(b"mp3")
    assert _resolve(lib, f"project:{pid}:m1").ref == "video:v1"
    assert _resolve(lib, f"project:{pid}:m2").ref == f"asset:{row['sha256']}"
    assert _resolve(lib, f"project:{pid}:m3").ref == f"library:{item}"
    inside = _resolve(lib, f"project:{pid}:m4")
    assert inside.path == music.resolve() and inside.ref == f"file:{music.resolve()}"
    with pytest.raises(resolver.NotFound, match="outside Clips Kitty's folders"):
        _resolve(lib, f"project:{pid}:m5")
    with pytest.raises(resolver.NotFound, match="can't open"):
        _resolve(lib, f"project:{pid}:m6")
    with pytest.raises(resolver.NotFound, match="no media 'm9'"):
        _resolve(lib, f"project:{pid}:m9")
    with pytest.raises(resolver.NotFound, match="no such project"):
        _resolve(lib, f"project:{'0' * 32}:m1")


@pytest.mark.parametrize("media_id", [
    "video:..", "video:../state.db", "video:..\\state", "video:a/b", "video:-rf", "video:", "clip:-1", "clip:1.5",
    "clip:\uff11", "asset:" + "AB" * 32, "asset:../../x", "library:../x", "library:Sound", "project:abc:m1",
    "project:" + "0" * 32, "file:C:/Windows/win.ini", "file:/etc/passwd", "C:/Windows/win.ini", "", "video",
    "video:" + "a" * 200,
])
def test_ids_that_name_nothing_or_try_to_leave(lib, media_id):
    with pytest.raises(resolver.NotFound):
        _resolve(lib, media_id)
    assert not resolver.is_media_id(media_id)


def test_a_link_out_of_the_data_folder_is_refused(lib, tmp_path):
    db, data = lib
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"not ours")
    link = data / "downloads" / "linked.mp4"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(resolver.NotFound, match="outside Clips Kitty's folders"):
        _resolve(lib, "video:linked")


def test_a_vertical_live_download(lib):
    """A Vertical Live video's download is <id>__vertical.<ext>
    (sources/vertical.py SUFFIX): video:<id> finds it when the video has no
    plain download, and the plain one wins when it has both."""
    db, data = lib
    add_video(db.conn, "vert1", "A vertical live")
    vertical = data / "downloads" / "vert1__vertical.mp4"
    vertical.write_bytes(b"vertical")
    found = _resolve(lib, "video:vert1")
    assert found.path == vertical.resolve() and found.ref == "video:vert1" and found.name == "A vertical live"
    plain = data / "downloads" / "vert1.mp4"
    plain.write_bytes(b"plain")
    assert _resolve(lib, "video:vert1").path == plain.resolve()


def test_a_file_source_serves_only_sounds_and_videos(lib):
    """A project document comes from the client: its "file" source may not
    name the database, a saved sign-in or anything else that isn't a sound
    or a video, even inside the data folder."""
    db, data = lib
    (data / "secrets").mkdir()
    (data / "secrets" / "token.json").write_text("{}")
    pid = add_project(db.conn, {
        "m1": ("audio", {"type": "file", "path": str(data / "state.db")}),
        "m2": ("audio", {"type": "file", "path": str(data / "secrets" / "token.json")}),
        "m3": ("audio", {"type": "file", "path": str(data / "STATE.DB")}),
    })
    for key in ("m1", "m2", "m3"):
        with pytest.raises(resolver.NotFound, match="not a sound or a video"):
            _resolve(lib, f"project:{pid}:{key}")


def test_a_file_source_named_like_a_sound_that_leads_elsewhere_is_refused(lib):
    db, data = lib
    link = data / "music.mp3"
    try:
        os.symlink(data / "state.db", link)
    except (OSError, NotImplementedError):
        pytest.skip("this PC can't make symbolic links")
    pid = add_project(db.conn, {"m1": ("audio", {"type": "file", "path": str(link)})})
    with pytest.raises(resolver.NotFound, match="not a sound or a video"):
        _resolve(lib, f"project:{pid}:m1")
