"""Editor suite step 6: a project pins its sources. A video one of the new
editor's projects names is never deleted by the app's own clean-up paths,
and one no project names still is:

- Storage's clean-up (core/housekeeping.py survey and clean, the Free up
  button);
- Make clips again giving a video's own copy back after its run
  (core/remake.py);
- the delete after publishing (server/automation.py): tests/test_automation.py.

The rule is one helper, core/state.py pinned_video_ids, plain SQL over the
projects table (with a Python reading for a damaged document). Standard
library only: runs on CI's light install.
"""

import sqlite3

import pytest
from media_helpers import add_project, add_video

from core import housekeeping, remake
from core.state import StateDB, _media_of_document, pinned_video_ids, project_media

SHA = "cd" * 32


@pytest.fixture
def lib(tmp_path):
    data = tmp_path / "data"
    db = StateDB(data / "state.db")
    (data / "downloads").mkdir()
    yield db, data
    db.conn.close()


def test_what_the_projects_name(lib):
    db, _data = lib
    assert project_media(db.conn) == {"video": set(), "asset": set(), "library": set(), "file": set()}
    add_project(db.conn, {"m1": ("video", {"type": "video", "video_id": "v1"}),
                          "m2": ("audio", {"type": "asset", "sha256": SHA}),
                          "m3": ("audio", {"type": "library", "id": "music-doodles"}),
                          "m4": ("audio", {"type": "file", "path": "C:/music/song.mp3"})})
    add_project(db.conn, {}, video_id="v2")                     # made from a clip of v2
    assert project_media(db.conn) == {"video": {"v1", "v2"}, "asset": {SHA}, "library": {"music-doodles"},
                                      "file": {"C:/music/song.mp3"}}
    assert pinned_video_ids(db.conn) == {"v1", "v2"} and db.video_pinned("v1") and not db.video_pinned("v3")


def test_a_damaged_document_does_not_hide_the_others(lib):
    db, _data = lib
    add_project(db.conn, {"m1": ("video", {"type": "video", "video_id": "v1"})})
    db.conn.execute("INSERT INTO projects (id, video_id, doc) VALUES ('bad', 'v9', '{not json')")
    db.conn.commit()
    assert pinned_video_ids(db.conn) == {"v1", "v9"}


def test_the_python_reading_matches(lib):
    doc = '{"media":{"m1":{"source":{"type":"video","video_id":"v1"}},"m2":{"source":{"type":"asset","sha256":"x"}}}}'
    assert _media_of_document(doc) == [
        {"type": "video", "video_id": "v1", "sha256": None, "library": None, "path": None},
        {"type": "asset", "video_id": None, "sha256": "x", "library": None, "path": None}]
    assert _media_of_document("[1, 2]") == [] and _media_of_document(None) == []


def test_an_older_database_with_no_projects_pins_nothing(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    assert pinned_video_ids(conn) == set()
    conn.close()


def _download(data, video_id):
    path = data / "downloads" / f"{video_id}.mp4"
    path.write_bytes(b"several gigabytes")
    return path


def test_storage_never_frees_a_download_a_project_uses(lib):
    db, data = lib
    pinned = _download(data, "pinned1")        # its video left the library, a project still uses it
    orphan = _download(data, "orphan1")
    known = _download(data, "known1")
    add_video(db.conn, "known1")
    add_project(db.conn, {"m1": ("video", {"type": "video", "video_id": "pinned1"})})
    found = housekeeping.survey(db, data)
    assert found["_groups"]["orphan_downloads"] == [orphan]
    assert found["sources"]["files"] == 2
    housekeeping.clean(db, data)                # Storage -> Free up
    assert pinned.exists() and known.exists() and not orphan.exists()


def test_a_project_made_from_a_clip_pins_that_clips_video(lib):
    db, data = lib
    pinned = _download(data, "fromclip")
    add_project(db.conn, {}, video_id="fromclip")
    housekeeping.clean(db, data)
    assert pinned.exists()


def test_make_clips_again_gives_a_pinned_videos_copy_back(lib):
    """The run of a video's own copy keeps it under a second name; when the
    video left the library meanwhile, the copy goes back only if a project
    uses it (tests/test_remake_safety.py has the unpinned case)."""
    db, data = lib
    for video_id, named in (("pinnedvid01", True), ("plainvid001", False)):
        add_video(db.conn, video_id)
        db.set_video_status(video_id, "done")
        copy = _download(data, video_id)
        if named:
            add_project(db.conn, {"m1": ("video", {"type": "video", "video_id": video_id})})
        run = remake.Run(db, video_id, data, url=f"local:{video_id}")
        copy.unlink()                                       # the run's first step deleted it
        db.conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
        db.conn.commit()
        run.stopped("was cancelled")
        assert copy.exists() is named, video_id
        assert not copy.with_name(f"{video_id}.remaking.mp4").exists()


def test_a_vertical_live_download_is_its_videos(lib):
    """<id>__vertical.<ext> (sources/vertical.py SUFFIX) is the video <id>'s
    download: kept while the video is in the library or a project names it,
    freed like any other once neither is true."""
    db, data = lib
    in_library = data / "downloads" / "vert1__vertical.mp4"
    pinned = data / "downloads" / "vert2__vertical.mp4"
    orphan = data / "downloads" / "vert3__vertical.mp4"
    for path in (in_library, pinned, orphan):
        path.write_bytes(b"a vertical live")
    add_video(db.conn, "vert1")
    add_project(db.conn, {"m1": ("video", {"type": "video", "video_id": "vert2"})})
    found = housekeeping.survey(db, data)
    assert found["_groups"]["orphan_downloads"] == [orphan]
    assert found["sources"]["files"] == 2
    housekeeping.clean(db, data)
    assert in_library.exists() and pinned.exists() and not orphan.exists()
