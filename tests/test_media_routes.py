"""Editor suite step 6: the media routes (server/media_api.py), through
FastAPI's TestClient on a temporary data folder. Nothing is started and
nothing listens on a port; the editor lane's tasks are run in the test's
own thread (EditorLane.run_pending).

- GET /media/source/{id}: every form of Range against known bytes, 416,
  HEAD, the ETag with If-None-Match and If-Range, 404 for a missing file and
  for ids that try to leave the data folder.
- Protection: a read route takes the session header or the media's token,
  a changing route only the header, and both refuse a foreign Origin.
- Info and the proxy rule; a proxy, peaks and thumbnails made on the lane by
  real FFmpeg (skipped without it); import for editing; clearing the cache.
- A stream the client stops releases the file at once; on Windows the file
  can be renamed and deleted while a response is half sent.
"""

import os
import struct
import sys

import pytest
from media_helpers import add_project, add_video, make_tone_and_silence, make_video

from core.state import StateDB

DATA = bytes(range(256)) * 4096                 # 1 MiB of known bytes


class Env:
    def __init__(self, client, app, db, session, data_dir):
        self.client, self.app, self.db, self.data_dir = client, app, db, data_dir
        self.lane = app.state.editor_lane
        self.ok = {"X-Clips-Kitty-Session": session}

    def get(self, path, **headers):
        return self.client.get(path, headers={**self.ok, **headers})

    def post(self, path, body=None, **headers):
        return self.client.post(path, json=body if body is not None else {}, headers={**self.ok, **headers})

    def download(self, video_id="vid1", data=DATA, ext=".mp4"):
        path = self.data_dir / "downloads" / f"{video_id}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run(self) -> int:
        return self.lane.run_pending()


@pytest.fixture
def env(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yaml")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from plugins import session
    from server.api import create_app

    monkeypatch.delenv("CLIPS_KITTY_SESSION_SECRET", raising=False)
    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    db = StateDB(data_dir / "state.db")
    add_video(db.conn, "vid1", "Friday stream")
    client = TestClient(app, base_url="http://127.0.0.1")
    yield Env(client, app, db, session.path(data_dir).read_text(encoding="utf-8").strip(), data_dir)
    db.conn.close()


# ---- byte ranges --------------------------------------------------------------------------


def test_the_whole_file(env):
    env.download()
    r = env.get("/media/source/video:vid1")
    assert r.status_code == 200 and r.content == DATA
    assert r.headers["accept-ranges"] == "bytes" and r.headers["content-length"] == str(len(DATA))
    assert r.headers["content-type"] == "video/mp4" and r.headers["etag"].startswith('"')
    assert r.headers["cache-control"] == "no-cache" and r.headers["last-modified"].endswith("GMT")


@pytest.mark.parametrize(("header", "first", "last"), [
    ("bytes=0-99", 0, 99), ("bytes=1000-1999", 1000, 1999), ("bytes=524288-", 524288, len(DATA) - 1),
    ("bytes=-100", len(DATA) - 100, len(DATA) - 1), ("bytes=1048000-2000000", 1048000, len(DATA) - 1),
    ("bytes=0-0", 0, 0),
])
def test_one_range(env, header, first, last):
    env.download()
    r = env.get("/media/source/video:vid1", Range=header)
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes {first}-{last}/{len(DATA)}"
    assert r.headers["content-length"] == str(last - first + 1)
    assert r.content == DATA[first:last + 1]


def test_a_range_past_the_end_is_416(env):
    env.download()
    for header in (f"bytes={len(DATA)}-", "bytes=9999999-10000000", "bytes=-0"):
        r = env.get("/media/source/video:vid1", Range=header)
        assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{len(DATA)}"


def test_ranges_that_are_ignored_give_the_whole_file(env):
    env.download()
    for header in ("bytes=0-1,5-6", "items=0-5", "bytes=9-3"):
        r = env.get("/media/source/video:vid1", Range=header)
        assert r.status_code == 200 and r.content == DATA


def test_head(env):
    env.download()
    r = env.client.head("/media/source/video:vid1", headers=env.ok)
    assert r.status_code == 200 and r.content == b"" and r.headers["content-length"] == str(len(DATA))
    r = env.client.head("/media/source/video:vid1", headers={**env.ok, "Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == b"" and r.headers["content-length"] == "10"


def test_etag_if_none_match_and_if_range(env):
    path = env.download()
    tag = env.get("/media/source/video:vid1").headers["etag"]
    r = env.get("/media/source/video:vid1", **{"If-None-Match": tag})
    assert r.status_code == 304 and r.content == b"" and r.headers["etag"] == tag
    r = env.get("/media/source/video:vid1", Range="bytes=0-9", **{"If-Range": tag})
    assert r.status_code == 206 and r.content == DATA[:10]
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))      # the file changed
    r = env.get("/media/source/video:vid1", Range="bytes=0-9", **{"If-Range": tag})
    assert r.status_code == 200 and r.content == DATA                         # so the whole new file
    assert r.headers["etag"] != tag
    assert env.get("/media/source/video:vid1", **{"If-None-Match": tag}).status_code == 200


def test_a_missing_file_and_ids_that_leave_the_data_folder_are_404(env):
    r = env.get("/media/source/video:vid1")
    assert r.status_code == 404 and "download of \u201cFriday stream\u201d is missing" in r.json()["detail"]
    assert "no video 'nope'" in env.get("/media/source/video:nope").json()["detail"]
    for media_id in ("video:..", "video:..%5Cstate.db", "video:..%5C..%5Cwin.ini", "asset:..%2F..%2Fstate.db",
                     "file:C:%5CWindows%5Cwin.ini", "..%5Cstate.db", "project:abc:..", "clip:0x1"):
        r = env.get(f"/media/source/{media_id}")
        assert r.status_code == 404, media_id
        assert b"SQLite" not in r.content


# ---- protection ---------------------------------------------------------------------------


def test_a_read_route_takes_the_header_or_the_token(env):
    source = make_video(env.data_dir / "downloads" / "vid1.mp4", seconds=1)
    assert env.client.get("/media/source/video:vid1").status_code == 403
    assert env.client.get("/media/video:vid1/info").status_code == 403
    info = env.get("/media/video:vid1/info").json()
    for url in info["urls"].values():
        assert "?t=" + info["token"] in url
    assert env.client.get(info["urls"]["source"]).content == source.read_bytes()     # what <video> does


def test_tokens(env):
    from server.media_api import media_token

    env.download()
    env.download("vid2")
    secret = env.ok["X-Clips-Kitty-Session"]
    good = media_token(secret, "video:vid1")
    assert env.client.get(f"/media/source/video:vid1?t={good}").status_code == 200
    assert env.client.get(f"/media/source/video:vid2?t={good}").status_code == 403       # another media's
    assert env.client.get("/media/source/video:vid1?t=" + "0" * 32).status_code == 403
    assert env.client.get(f"/media/source/video:vid1?t={media_token('another secret', 'video:vid1')}"
                          ).status_code == 403
    # A token never stands in for the header on a route that changes something.
    assert env.client.post(f"/media/video:vid1/proxy?t={good}").status_code == 403
    assert env.client.post(f"/media/cache/clear?t={good}").status_code == 403


def test_a_foreign_origin_is_refused_even_with_the_header(env):
    env.download()
    for origin in ("https://example.com", "http://127.0.0.1:9999", "http://localhost:8080"):
        assert env.get("/media/source/video:vid1", Origin=origin).status_code == 403
        assert env.post("/media/cache/clear", Origin=origin).status_code == 403
        assert env.post("/media/import", {"path": "C:/x.mp4"}, Origin=origin).status_code == 403
    for origin in ("null", "file://", "http://localhost:5173", "http://127.0.0.1"):
        assert env.get("/media/source/video:vid1", Origin=origin).status_code == 200, origin


def test_changing_routes_need_the_header(env):
    for path, body in (("/media/video:vid1/proxy", None), ("/media/video:vid1/proxy/cancel", None),
                       ("/media/import", {"path": "C:/x.mp4"}), ("/media/cache/clear", None)):
        assert env.client.post(path, json=body or {}).status_code == 403, path
    assert env.client.get("/media/cache/size").status_code == 403
    assert env.client.get("/media/import/1").status_code == 403


def test_the_old_clip_route_is_unchanged(env):
    clip_file = env.data_dir / "clips" / "someone" / "A [vid1]" / "clip.mp4"
    clip_file.parent.mkdir(parents=True)
    clip_file.write_bytes(b"clip bytes")
    clip_id = env.db.add_clip("vid1", 10.0, 40.0, 80, "hook", path=str(clip_file))
    r = env.client.get(f"/media/{clip_id}")
    assert r.status_code == 200 and r.content == b"clip bytes"
    assert env.get(f"/media/source/clip:{clip_id}").content == b"clip bytes"


# ---- streams ------------------------------------------------------------------------------


def _drive(response, *, after_first=None, stop_after: int | None = None):
    """Send `response` through ASGI by hand: the body chunks it sent. After
    the first chunk `after_first()` runs; after `stop_after` chunks the
    client goes away."""
    import anyio

    chunks, gone = [], anyio.Event()

    async def receive():
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            if len(chunks) == 1 and after_first is not None:
                after_first()
            if stop_after is not None and len(chunks) >= stop_after:
                gone.set()
                await anyio.sleep(0.05)       # the watcher cancels the send loop here

    async def main():
        await response({"type": "http", "method": "GET"}, receive, send)
        gone.set()

    anyio.run(main)
    return chunks


def _stream(env, path, header=None):
    pytest.importorskip("fastapi")
    from starlette.requests import Request

    from server.media_api import serve_file

    headers = [(b"range", header.encode())] if header else []
    return serve_file(Request({"type": "http", "method": "GET", "headers": headers, "query_string": b""}), path)


def test_a_stream_the_client_stops_releases_the_file_at_once(env):
    from video_editor.media import fileio

    big = env.download(data=os.urandom(8 * fileio.CHUNK))
    response = _stream(env, big)
    assert str(big) in fileio.open_paths()
    chunks = _drive(response, stop_after=2)
    assert len(chunks) == 2                       # not the 8 chunks of the whole file
    assert fileio.open_paths() == []


def test_a_stream_sent_to_the_end_releases_the_file(env):
    from video_editor.media import fileio

    big = env.download(data=os.urandom(3 * fileio.CHUNK + 5))
    chunks = _drive(_stream(env, big, "bytes=10-"))
    assert b"".join(chunks) == big.read_bytes()[10:] and fileio.open_paths() == []


@pytest.mark.skipif(sys.platform != "win32", reason="share-delete is about Windows file locking")
def test_on_windows_the_source_can_be_renamed_and_deleted_while_it_streams(env):
    from video_editor.media import fileio

    data = os.urandom(4 * fileio.CHUNK)
    source = env.download(data=data)
    moved = source.with_name("moved.mp4")

    def meanwhile():
        os.rename(source, moved)
        os.remove(moved)
        source.write_bytes(b"a new download")

    chunks = _drive(_stream(env, source), after_first=meanwhile)
    assert b"".join(chunks) == data and source.read_bytes() == b"a new download"
    assert fileio.open_paths() == []


# ---- info, proxy, peaks, thumbnails (FFmpeg) -----------------------------------------------


def test_info_and_the_proxy_rule(env):
    make_video(env.data_dir / "downloads" / "vid1.mp4", seconds=2, size="1280x720")
    r = env.get("/media/video:vid1/info")
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["kind"] == "video" and info["video_codec"] == "h264" and info["has_audio"]
    assert (info["width"], info["height"], info["rotation"]) == (1280, 720, 0)
    assert info["fps"] == {"num": 30, "den": 1} and abs(info["duration_us"] - 2_000_000) < 100_000
    assert info["needs_proxy"] is False and info["proxy_reasons"] == [] and info["proxy"] == {"state": "none"}
    assert len(info["fingerprint"]) == 64 and info["ref"] == "video:vid1" and info["name"] == "Friday stream"
    assert set(info["urls"]) == {"source", "proxy", "peaks", "thumbs"}
    add_video(env.db.conn, "obs1")
    make_video(env.data_dir / "downloads" / "obs1.mkv", seconds=1)       # H.264 + AAC in Matroska
    info = env.get("/media/video:obs1/info").json()
    # Plays as it is (step 8 measured it in Electron 31): no proxy.
    assert info["needs_proxy"] is False and info["proxy_reasons"] == []


def test_a_proxy_asked_for_is_made_on_the_lane(env):
    from video_editor.media import probe

    make_video(env.data_dir / "downloads" / "vid1.mp4", seconds=2, size="1280x720")
    assert env.get("/media/video:vid1/proxy").status_code == 404
    r = env.post("/media/video:vid1/proxy")
    assert r.status_code == 202 and r.json()["state"] == "queued"
    assert env.post("/media/video:vid1/proxy").json()["job_id"] == r.json()["job_id"]    # asked once
    assert env.get("/media/video:vid1/proxy").status_code == 202
    assert env.client.get("/export/jobs").json()["jobs"] == []          # a media task is no export
    assert env.run() == 1
    info = env.get("/media/video:vid1/info").json()
    assert info["proxy"] == {"state": "ready"} and info["needs_proxy"] and info["proxy_reasons"] == [
        "a proxy was asked for"]
    r = env.client.get(info["urls"]["proxy"], headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.headers["content-type"] == "video/mp4" and len(r.content) == 100
    folder = env.data_dir / "media_cache" / info["fingerprint"]
    made = probe.info(folder / "proxy.mp4")
    assert (made["width"], made["height"], made["video_codec"], made["audio_codec"]) == (960, 540, "h264", "aac")
    assert [p.name for p in folder.iterdir() if ".part-" in p.name] == []
    assert env.post("/media/video:vid1/proxy").status_code == 200       # ready: nothing more to do
    assert env.get("/media/video:vid1/proxy?state_only=true").json() == {"state": "ready"}


def test_a_proxy_can_be_cancelled_before_it_starts(env):
    make_video(env.data_dir / "downloads" / "vid1.mp4", seconds=1)
    env.post("/media/video:vid1/proxy")
    r = env.post("/media/video:vid1/proxy/cancel")
    assert r.json()["state"] == "cancelled"
    assert env.run() == 0


def test_peaks_from_an_imported_sound(env, tmp_path):
    sound = make_tone_and_silence(tmp_path / "tone.wav", tone_s=1.0, silence_s=1.0, amplitude=0.5)
    asset = env.post("/media/import", {"path": str(sound)}).json()
    assert asset["kind"] == "audio" and asset["id"].startswith("asset:")
    url = f"/media/{asset['id']}/peaks"
    r = env.get(url)
    assert r.status_code == 202 and r.json()["state"] == "queued"
    env.run()
    r = env.get(f"{url}?per_second=128")
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    data = r.content
    assert data[:4] == b"CKPK"
    _magic, _version, levels, rate, duration_us = struct.unpack_from("<4sHHIQ", data, 0)
    assert levels == 8 and rate == 8192 and abs(duration_us - 2_000_000) < 10_000
    as_json = env.get(f"{url}?per_second=128&format=json&start_us=0&end_us=1000000").json()
    first = as_json["levels"][0]
    assert first["per_second"] == 128 and len(first["max"]) == 128
    assert 60 <= max(first["max"][4:124]) <= 66
    assert env.get(f"{url}?per_second=0").status_code == 400
    assert env.get(f"{url}?per_second=513").status_code == 400
    assert env.get(f"{url}?format=xml").status_code == 400
    make_video(env.data_dir / "downloads" / "vid1.mp4", seconds=1, audio=False)
    assert env.get("/media/video:vid1/peaks").status_code == 404


def test_thumbnails(env):
    make_video(env.data_dir / "downloads" / "vid1.mp4", seconds=5, rate=25, keyint=25)
    assert env.get("/media/video:vid1/thumbs").status_code == 202
    env.run()
    index = env.get("/media/video:vid1/thumbs").json()
    assert index["count"] == 5 and index["times_us"] == [0, 1_000_000, 2_000_000, 3_000_000, 4_000_000]
    sheet = env.client.get(index["sheets"][0]["url"])
    assert sheet.status_code == 200 and sheet.headers["content-type"] == "image/jpeg"
    assert sheet.content[:2] == b"\xff\xd8"
    assert env.get("/media/video:vid1/thumbs/1").status_code == 404
    assert env.get("/media/video:vid1/thumbs/0").status_code == 200


def test_a_failed_product_says_why_and_is_tried_again_only_when_asked(env, monkeypatch):
    from video_editor.media import peaks

    make_video(env.data_dir / "downloads" / "vid1.mp4", seconds=1)

    def broken(*_a, **_k):
        raise RuntimeError("FFmpeg couldn't read the sound: broken stream")

    monkeypatch.setattr(peaks, "compute", broken)
    assert env.get("/media/video:vid1/peaks").status_code == 202
    env.run()
    r = env.get("/media/video:vid1/peaks")
    assert r.status_code == 500 and r.json()["state"] == "failed" and "broken stream" in r.json()["error"]
    assert env.run() == 0                                   # not tried again by itself
    assert env.get("/media/video:vid1/peaks?retry=true").status_code == 202
    assert env.run() == 1


def test_a_replaced_file_gets_new_products(env):
    path = make_video(env.data_dir / "downloads" / "vid1.mp4", seconds=1)
    first = env.get("/media/video:vid1/info").json()["fingerprint"]
    path.unlink()
    make_video(path, seconds=2)
    second = env.get("/media/video:vid1/info").json()
    assert second["fingerprint"] != first and abs(second["duration_us"] - 2_000_000) < 100_000


# ---- import -------------------------------------------------------------------------------


def test_import_a_good_file_twice_gives_one_asset(env, tmp_path):
    video = make_video(tmp_path / "my clip.mp4", seconds=1)
    first = env.post("/media/import", {"path": str(video), "name": "Holiday"})
    assert first.status_code == 200, first.text
    asset = first.json()
    assert asset["kind"] == "video" and asset["name"] == "Holiday" and asset["size"] == video.stat().st_size
    assert asset["width"] == 640 and abs(asset["duration_us"] - 1_000_000) < 100_000 and asset["has_audio"]
    again = env.post("/media/import", {"path": str(video)}).json()
    assert again["sha256"] == asset["sha256"]
    assert env.db.conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 1
    stored = list((env.data_dir / "media" / "assets").rglob("*.mp4"))
    assert len(stored) == 1 and stored[0].read_bytes() == video.read_bytes()
    assert video.exists()                                       # only ever read
    assert env.get(f"/media/source/{asset['id']}").content == video.read_bytes()


@pytest.mark.parametrize(("make", "status", "words"), [
    (lambda p: p, 400, "That is a folder"),
    (lambda p: p / "nope.mp4", 404, "There is no file at that path"),
    (lambda p: (p / "notes.txt").write_text("hello") and p / "notes.txt", 400, "isn't a video, a sound or a picture"),
    (lambda p: (p / "empty.mp4").write_bytes(b"") or p / "empty.mp4", 400, "That file is empty"),
])
def test_import_refusals(env, tmp_path, make, status, words):
    if words.startswith("isn't"):
        from core import binaries

        if binaries.missing():
            pytest.skip("needs FFprobe")
    r = env.post("/media/import", {"path": str(make(tmp_path))})
    assert r.status_code == status and words in r.json()["detail"], r.text
    assert env.db.conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 0


@pytest.mark.parametrize(("path", "words"), [
    ("relative/clip.mp4", "whole path"), ("\\\\server\\share\\clip.mp4", "network path"),
    ("//server/share/clip.mp4", "network path"), ("", "Give the path"), ("C:/a\x00b.mp4", "not a path"),
])
def test_import_refuses_paths_that_are_not_a_file_here(env, path, words):
    r = env.post("/media/import", {"path": path})
    assert r.status_code == 400 and words in r.json()["detail"]


def test_a_large_import_runs_on_the_lane_with_progress(env, tmp_path, monkeypatch):
    from server import media_api

    monkeypatch.setattr(media_api, "INLINE_IMPORT_BYTES", 0)
    video = make_video(tmp_path / "big.mp4", seconds=1)
    r = env.post("/media/import", {"path": str(video)})
    assert r.status_code == 202
    job = r.json()["job"]
    assert job["kind"] == "import" and job["status"] == "queued"
    seen = []
    env.lane._publish = seen.append
    env.run()
    done = env.get(f"/media/import/{job['job_id']}").json()
    assert done["status"] == "done" and done["result"]["kind"] == "video" and done["fraction"] == 1.0
    assert {e["type"] for e in seen} == {"media_job"}
    assert env.get("/media/import/999").status_code == 404


# ---- the cache ----------------------------------------------------------------------------


def test_clearing_the_cache(env):
    make_video(env.data_dir / "downloads" / "vid1.mp4", seconds=2)
    env.get("/media/video:vid1/peaks")
    env.get("/media/video:vid1/thumbs")
    env.run()
    size = env.get("/media/cache/size").json()
    assert size["folders"] == 1 and size["bytes"] > 0 and size["cap_bytes"] == 5 * 1024 ** 3
    out = env.post("/media/cache/clear").json()
    assert out["kept_in_use"] == 0 and out["bytes_freed"] == size["bytes"]
    assert env.get("/media/cache/size").json()["folders"] == 0
    assert (env.data_dir / "downloads" / "vid1.mp4").exists()
    assert env.get("/media/video:vid1/peaks").status_code == 202        # made again when next needed


def test_storage_lists_the_cache_and_free_up_keeps_it(env):
    make_video(env.data_dir / "downloads" / "vid1.mp4", seconds=1)
    env.get("/media/video:vid1/peaks")
    env.run()
    storage = env.client.get("/storage").json()
    assert storage["editor_cache"]["folders"] == 1 and storage["reclaimable"]["editor_cache"]["files"] == 0
    env.client.post("/storage/cleanup")
    assert env.get("/media/cache/size").json()["folders"] == 1


def test_the_project_routes_media_resolve(env):
    make_video(env.data_dir / "downloads" / "vid1.mp4", seconds=1)
    pid = add_project(env.db.conn, {"m1": ("video", {"type": "video", "video_id": "vid1"})})
    info = env.get(f"/media/project:{pid}:m1/info").json()
    assert info["ref"] == "video:vid1"
    assert env.client.get(info["urls"]["source"]).status_code == 200


# ---- fix round 1 --------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [b"bytes=\xb2-", b"bytes=0-\xb9", b"bytes=-\xb3"])
def test_a_range_with_digits_that_are_not_ascii_is_ignored(env, raw):
    """Header bytes are read as Latin-1, so the byte 0xB2 arrives as a
    superscript two, which str.isdigit() takes and int() refuses: the Range is ignored
    (RFC 9110), never a 500."""
    env.download()
    r = env.client.get("/media/source/video:vid1", headers={**env.ok, "Range": raw})
    assert r.status_code == 200 and r.content == DATA


def test_a_file_that_gets_shorter_while_it_streams_drops_the_connection(env):
    """The answer can't reach the Content-Length it announced: the stream
    ends with an error (the server closes the connection, so the <video>
    asks again), never a body cut short as if it were whole."""
    from server.media_api import SourceShrank
    from video_editor.media import fileio

    path = env.download(data=bytes(3 * fileio.CHUNK))
    response = _stream(env, path)

    def cut():
        with open(path, "r+b") as f:
            f.truncate(fileio.CHUNK)

    with pytest.raises(SourceShrank):
        _drive(response, after_first=cut)
    assert fileio.open_paths() == []


def test_the_openapi_schema_has_no_duplicate_operation_ids(env):
    import warnings

    env.app.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = env.app.openapi()
    assert not [w for w in caught if "Duplicate Operation ID" in str(w.message)]
    assert "get" in schema["paths"]["/media/source/{media_id}"]
    r = env.client.head("/media/{}/proxy".format("video:vid1"), headers=env.ok)
    assert r.status_code in (404, 422)          # HEAD still answers, outside the schema


def test_a_playlist_is_not_imported(env, tmp_path):
    clip = make_video(tmp_path / "elsewhere.mp4", seconds=1)
    playlist = tmp_path / "disguised.m3u8"
    playlist.write_text(f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:1.0,\n{clip.name}\n#EXT-X-ENDLIST\n")
    r = env.post("/media/import", {"path": str(playlist)})
    assert r.status_code == 400, r.text
    assert env.db.conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 0


def test_a_project_file_source_never_serves_the_database(env):
    pid = add_project(env.db.conn, {"m1": ("audio", {"type": "file", "path": str(env.data_dir / "state.db")})})
    r = env.get(f"/media/source/project:{pid}:m1")
    assert r.status_code == 404 and "not a sound or a video" in r.json()["detail"]


def test_a_vertical_live_source_is_served(env):
    add_video(env.db.conn, "vert1", "A vertical live")
    env.download("vert1__vertical")
    r = env.get("/media/source/video:vert1", Range="bytes=0-9")
    assert r.status_code == 206 and r.content == DATA[:10]
