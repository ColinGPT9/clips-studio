"""GET /fonts, GET /fonts/{file} and GET /caption-presets (server/fonts_api.py).

- /fonts lists the bundled families with their files' URLs.
- /fonts/{file} serves the very bytes the render burns, with a long cache
  header, and only a file the manifest names: a path, a traversal, the
  manifest itself, a licence file or a damaged font is 404.
- /caption-presets is config/caption_presets.json.
- The routes are part of the engine (create_app installs them), and the
  renderer's CSP lets the preview load fonts from the engine, and nothing
  else in it changed.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "assets" / "fonts" / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import fonts_api

    app = FastAPI()
    fonts_api.install(app)
    return TestClient(app)


def test_the_list_names_every_bundled_family_and_its_file(client):
    r = client.get("/fonts")
    assert r.status_code == 200
    listed = {f["family"]: f for f in r.json()["fonts"]}
    assert list(listed) == [e["family"] for e in MANIFEST["files"]]
    anton = listed["Anton"]
    assert anton == {"family": "Anton", "weight": 400, "scripts": ["latin", "latin-ext", "vietnamese"],
                     "files": ["Anton-Regular.ttf"], "urls": ["/fonts/Anton-Regular.ttf"], "licence": "OFL-1.1"}


def test_a_listed_file_is_served_as_it_ships_with_a_long_cache(client):
    for e in MANIFEST["files"]:
        name = Path(e["file"]).name
        r = client.get(f"/fonts/{name}")
        assert r.status_code == 200, name
        assert r.content == (ROOT / "assets" / "fonts" / e["file"]).read_bytes()
        assert r.headers["content-type"] == "font/ttf"
        assert "max-age=31536000" in r.headers["cache-control"]
        assert r.headers["access-control-allow-origin"] == "*"      # @font-face fetches in CORS mode


@pytest.mark.parametrize("name", [
    "manifest.json", "OFL.txt", "Papyrus.ttf", "anton%2FAnton-Regular.ttf", "..%2Fmanifest.json",
    "..%2F..%2Fconfig%2Fsettings.yaml", "..%5C..%5Cconfig%5Csettings.yaml", "%2E%2E", "anton-regular.ttf",
])
def test_anything_else_is_refused(client, name):
    r = client.get(f"/fonts/{name}")
    assert r.status_code == 404
    assert b"SIL" not in r.content and b"files" not in r.content


def test_a_path_under_fonts_is_never_a_file(client):
    for path in ("/fonts/anton/Anton-Regular.ttf", "/fonts/../config/settings.yaml", "/fonts/../../NOTICE"):
        assert client.get(path).status_code == 404, path


def test_a_damaged_font_is_not_served(client, monkeypatch):
    from video import fonts

    monkeypatch.setattr(fonts, "_matches", lambda path, sha: False)
    assert client.get("/fonts/Anton-Regular.ttf").status_code == 404


def test_the_presets_are_the_presets_file(client):
    r = client.get("/caption-presets")
    assert r.status_code == 200
    assert r.json() == json.loads((ROOT / "config" / "caption_presets.json").read_text(encoding="utf-8"))


def test_the_engine_installs_the_routes():
    source = (ROOT / "server" / "api.py").read_text(encoding="utf-8")
    assert "fonts_api.install(app)" in source


def test_the_renderer_may_load_fonts_from_the_engine_and_nothing_else_changed():
    html = (ROOT / "ui" / "src" / "renderer" / "index.html").read_text(encoding="utf-8")
    csp = html.split('content="', 2)[2].split('"', 1)[0]
    assert csp == (
        "default-src 'self'; connect-src 'self' http://127.0.0.1:8765 ws://127.0.0.1:8765; "
        "media-src http://127.0.0.1:8765; img-src 'self' data: http://127.0.0.1:8765; "
        "style-src 'self' 'unsafe-inline'; font-src 'self' http://127.0.0.1:8765"
    )
