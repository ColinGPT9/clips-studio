"""GET /assets/library and GET /assets/library/{file} (server/audio_library_api.py).

- /assets/library lists the bundled music and sound effects: id, kind, title,
  mood, length and the URL of the file.
- /assets/library/{file} serves the very bytes the render mixes in, with a
  long cache header and byte ranges (a player seeks), and only a file the
  manifest names: a path, a traversal, the manifest itself, a licence file or
  a damaged file is 404.
- The routes are part of the engine (create_app installs them), and the
  renderer's CSP already lets the editor play sound from the engine: it did
  not change.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "assets" / "library"
ITEMS = json.loads((LIBRARY / "manifest.json").read_text(encoding="utf-8"))["items"]


@pytest.fixture
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import audio_library_api

    app = FastAPI()
    audio_library_api.install(app)
    return TestClient(app)


def test_the_list_names_every_item_and_its_url(client):
    r = client.get("/assets/library")
    assert r.status_code == 200
    listed = r.json()["items"]
    assert [i["id"] for i in listed] == [e["id"] for e in ITEMS]
    track = next(i for i in listed if i["id"] == "music-roof-tops")
    assert track == {"id": "music-roof-tops", "kind": "music", "title": "Roof Tops", "mood": "chill",
                     "seconds": 75.0, "file": "roof-tops.ogg", "url": "/assets/library/roof-tops.ogg",
                     "licence": "CC0-1.0"}
    assert {i["kind"] for i in listed} == {"music", "sfx"}
    assert all(i["url"] == f"/assets/library/{i['file']}" and i["mood"] and i["seconds"] > 0 for i in listed)
    # Nothing about where the files are on this PC.
    assert "sha256" not in r.text and str(ROOT.name) not in r.text


def test_a_listed_file_is_served_as_it_ships_with_a_long_cache(client):
    for e in ITEMS:
        name = Path(e["file"]).name
        r = client.get(f"/assets/library/{name}")
        assert r.status_code == 200, name
        assert r.content == (LIBRARY / e["file"]).read_bytes()
        assert r.headers["content-type"] == "audio/ogg"
        assert "max-age=31536000" in r.headers["cache-control"]


def test_a_player_can_ask_for_part_of_a_file(client):
    data = (LIBRARY / "music" / "roof-tops.ogg").read_bytes()
    r = client.get("/assets/library/roof-tops.ogg", headers={"Range": "bytes=1000-1999"})
    assert r.status_code == 206 and r.content == data[1000:2000]
    assert r.headers["content-range"] == f"bytes 1000-1999/{len(data)}"


@pytest.mark.parametrize("name", [
    "manifest.json", "LICENSE.txt", "roof-tops.LICENSE.txt", "no-such.ogg", "music%2Froof-tops.ogg",
    "..%2Fmanifest.json", "..%2F..%2F..%2Fconfig%2Fsettings.yaml", "..%5C..%5Cconfig%5Csettings.yaml", "%2E%2E",
    "Roof-Tops.ogg", "roof-tops.ogg%00.txt",
])
def test_anything_else_is_refused(client, name):
    r = client.get(f"/assets/library/{name}")
    assert r.status_code == 404
    assert b"OggS" not in r.content and b"items" not in r.content and b"Creative Commons" not in r.content


def test_a_path_under_the_library_is_never_a_file(client):
    for path in ("/assets/library/music/roof-tops.ogg", "/assets/library/../fonts/manifest.json",
                 "/assets/library/../../NOTICE", "/assets/library/sfx/kenney-ui/LICENSE.txt"):
        assert client.get(path).status_code == 404, path


def test_a_damaged_file_is_not_served(client, monkeypatch):
    from video import audio_library

    monkeypatch.setattr(audio_library, "_matches", lambda path, sha: False)
    assert client.get("/assets/library/roof-tops.ogg").status_code == 404


def test_the_engine_installs_the_routes():
    source = (ROOT / "server" / "api.py").read_text(encoding="utf-8")
    assert "audio_library_api.install(app)" in source


def test_the_renderer_could_already_play_sound_from_the_engine():
    html = (ROOT / "ui" / "src" / "renderer" / "index.html").read_text(encoding="utf-8")
    csp = html.split('content="', 2)[2].split('"', 1)[0]
    assert "media-src http://127.0.0.1:8765;" in csp
