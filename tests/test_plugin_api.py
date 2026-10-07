"""The plugin manager's routes: the session header, the whole flow through
HTTP, and what a web page can't do.

The app is built as in the other API tests (no `with`, so no worker starts);
plugins come from folders made in a temporary directory.
"""

import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PID = "fixture-dev/manager-test"


@pytest.fixture
def api(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yt_dlp")  # sources.dispatch imports the YouTube source
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    monkeypatch.setenv("CLIPS_KITTY_SESSION_SECRET", "test-session-secret-0123456789")
    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    data_dir = tmp_path / "data"
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, settings)
    return TestClient(app, base_url="http://127.0.0.1"), data_dir


HEADERS = {"X-Clips-Kitty-Session": "test-session-secret-0123456789"}


def test_every_plugin_route_is_labelled_experimental(api):
    from server import api_stability as st

    client, _ = api
    routes = [(m, p) for m, p, _mod in st.app_routes(client.app) if p.startswith(("/plugins", "/marketplace"))]
    assert len(routes) == 12
    assert all(st.label(m, p) == st.EXPERIMENTAL for m, p in routes)


def test_changing_routes_need_the_session_header_and_reading_does_not(api, plugin_source):
    client, data_dir = api
    assert client.get("/plugins").status_code == 200
    body = {"source": {"kind": "folder", "path": str(plugin_source.folder())}}
    for headers in ({}, {"X-Clips-Kitty-Session": "wrong"}, {"X-Clips-Kitty-Session": ""}):
        r = client.post("/plugins/plan", json=body, headers=headers)
        assert r.status_code == 403, r.text
        assert "X-Clips-Kitty-Session" in r.json()["detail"]
    for method, path in (("post", "/plugins/install"), ("post", f"/plugins/{PID}/enable"),
                         ("post", f"/plugins/{PID}/disable"), ("post", f"/plugins/{PID}/rollback"),
                         ("post", f"/plugins/{PID}/pin"), ("post", f"/plugins/{PID}/unpin"),
                         ("delete", f"/plugins/{PID}"), ("put", f"/plugins/{PID}/secrets")):
        r = client.request(method.upper(), path, json={"plan_id": "0" * 16, "values": {"a": "b"}})
        assert r.status_code == 403, (path, r.text)
    assert not (data_dir / "plugins" / "installed.json").exists()


def test_the_secret_is_the_desktop_apps_and_is_written_for_scripts(api):
    _, data_dir = api
    from plugins import session

    assert session.path(data_dir).read_text().strip() == HEADERS["X-Clips-Kitty-Session"]


def test_an_engine_on_its_own_makes_a_secret(tmp_path):
    from plugins import session

    secret = session.secret_for(tmp_path, environ={})
    assert len(secret) >= 32 and session.path(tmp_path).read_text().strip() == secret
    assert session.secret_for(tmp_path, environ={}) != secret  # new at each start
    assert session.secret_for(tmp_path, environ={"CLIPS_KITTY_SESSION_SECRET": "short"}) != "short"
    assert session.matches(secret, f" {secret}\n") and not session.matches(secret, None)


def test_plugins_never_inherit_the_secret():
    from plugins._sdk import host

    env = host.plugin_env({"PATH": "/bin", "CLIPS_KITTY_SESSION_SECRET": "x"}, job_folder=Path("/tmp/job"))
    assert "CLIPS_KITTY_SESSION_SECRET" not in env


def test_install_update_turn_off_roll_back_and_remove_through_the_api(api, plugin_source):
    client, data_dir = api
    folder = plugin_source.folder(manifest=plugin_source.manifest(settings={"api_key": {"type": "secret"}}))

    plan = client.post("/plugins/plan", json={"source": {"kind": "folder", "path": str(folder)}}, headers=HEADERS)
    assert plan.status_code == 200, plan.text
    plan = plan.json()
    assert plan["ok"] and plan["plugin"]["id"] == PID
    assert plan["details"]["permissions"] == [
        {"id": "video.read", "label": "Reads the video you process", "enforcement": "enforced for the hand-over"}]
    assert client.get("/plugins").json()["plugins"] == []  # planned, not installed

    r = client.post("/plugins/install", json={"plan_id": plan["plan_id"]}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["version"] == "1.0.0"
    (listed,) = client.get("/plugins").json()["plugins"]
    assert listed["id"] == PID and listed["enabled"] and listed["source"]["kind"] == "folder"
    assert len(client.get("/plugins").json()["builtin"]) == 3

    r = client.put(f"/plugins/{PID}/secrets", json={"values": {"api_key": "sk-test-5678"}}, headers=HEADERS)
    assert r.json() == {"set": ["api_key"]}
    assert "sk-test-5678" not in client.get("/plugins").text

    assert client.post(f"/plugins/{PID}/rollback", headers=HEADERS).status_code == 409
    newer = plugin_source.folder("newer", plugin_source.manifest(version="1.1.0"))
    plan = client.post("/plugins/plan", json={"source": {"kind": "folder", "path": str(newer)}}, headers=HEADERS).json()
    assert plan["update"]["from"] == "1.0.0" and plan["update"]["direction"] == "update"
    client.post("/plugins/install", json={"plan_id": plan["plan_id"]}, headers=HEADERS)
    assert client.post(f"/plugins/{PID}/rollback", headers=HEADERS).json()["version"] == "1.0.0"

    assert client.post(f"/plugins/{PID}/disable", headers=HEADERS).json()["enabled"] is False
    r = client.post("/jobs", json={"url": "https://www.youtube.com/watch?v=aB3dEfGhIjK", "pipeline": PID})
    assert r.status_code == 400 and "turned off" in r.json()["detail"]
    assert client.post(f"/plugins/{PID}/enable", headers=HEADERS).json()["enabled"] is True
    assert client.post(f"/plugins/{PID}/pin", headers=HEADERS).json()["pinned"] is True
    assert client.post(f"/plugins/{PID}/unpin", headers=HEADERS).json()["pinned"] is False

    r = client.delete(f"/plugins/{PID}", headers=HEADERS)
    assert r.json() == {"removed": PID, "files_left": False, "keys_removed": True}
    assert client.get("/plugins").json()["plugins"] == []
    assert client.delete(f"/plugins/{PID}", headers=HEADERS).status_code == 404


def test_refusals_come_back_as_messages(api, plugin_source):
    client, _ = api
    r = client.post("/plugins/plan", json={"source": {"kind": "git", "url": "https://example.com/x/y", "commit": "main"}},
                    headers=HEADERS)
    assert r.status_code == 400 and "40-character commit" in r.json()["detail"]
    r = client.post("/plugins/install", json={"plan_id": "0123456789abcdef"}, headers=HEADERS)
    assert r.status_code == 404 and "expired" in r.json()["detail"]
    assert client.post("/plugins/Not_An/Id!/enable", headers=HEADERS).status_code == 404
    bad = plugin_source.folder(manifest=plugin_source.manifest(permissions=[]))
    plan = client.post("/plugins/plan", json={"source": {"kind": "folder", "path": str(bad)}}, headers=HEADERS).json()
    assert plan["ok"] is False and plan["plan_id"] is None
    assert plan["errors"] == ["inputs[0]: the video input needs the video.read permission"]


def test_a_web_page_cannot_send_the_header(api):
    """A cross-site request with a custom header needs a CORS preflight, and
    the engine's CORS allow-list refuses one from any page but the app's own."""
    client, _ = api
    r = client.options("/plugins/install", headers={
        "Origin": "https://attacker.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-clips-kitty-session, content-type"})
    assert r.status_code == 400 and "access-control-allow-origin" not in r.headers
    r = client.post("/plugins/install", content=json.dumps({"plan_id": "0" * 16}),
                    headers={"Origin": "https://attacker.example"})
    assert r.status_code == 403


def test_the_marketplace_searches_the_bundled_index_and_installs_from_it(tmp_path, monkeypatch, plugin_source):
    """GET /marketplace over an index, then plan an install from one of its
    listings. The listing's repository is reached through GitHub's archive,
    handed over by a fake fetcher: nothing touches the network."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import io
    import tarfile

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from plugins import api as plugins_api
    from plugins import registry, sources

    commit = "ab" * 20
    manifest = plugin_source.manifest(id="example-dev/nhl-goals", name="NHL Goals", games=["nhl"], tags=["hockey"],
                                      category="sports", repository="https://github.com/example-dev/nhl-goals")
    listing = {"id": "example-dev/nhl-goals", "publisher": "example-dev", "repository": manifest["repository"],
               "path": ".", "aliases": [], "latest": "1.0.0", "settings": {}, "checks": {},
               "versions": [{"version": "1.0.0", "commit": commit, "requires": manifest["requires"]}],
               **{k: manifest[k] for k in registry.SHOWN if k in manifest}}
    bundled = tmp_path / "index.json"
    bundled.write_text(json.dumps({"format": 1, "plugins": [listing], "blocklist": []}))

    def fetcher(url, path):
        assert url == f"https://github.com/example-dev/nhl-goals/archive/{commit}.tar.gz"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, text in (("clipskitty.yaml", json.dumps(manifest)), ("src/main.py", "print()\n")):
                info = tarfile.TarInfo(f"nhl-goals-{commit}/{name}")
                info.size = len(text.encode())
                tar.addfile(info, io.BytesIO(text.encode()))
        Path(path).write_bytes(buf.getvalue())

    monkeypatch.setattr(sources, "find_git", lambda: None)
    monkeypatch.setenv("CLIPS_KITTY_SESSION_SECRET", HEADERS["X-Clips-Kitty-Session"])
    app = FastAPI()
    plugins_api.install(app, data_dir=tmp_path / "data", app_version="2.0.0", fetcher=fetcher, bundled_index=bundled)
    client = TestClient(app, base_url="http://127.0.0.1")

    found = client.get("/marketplace", params={"q": "NHL"}).json()
    (item,) = found["plugins"]
    assert item["id"] == "example-dev/nhl-goals" and item["installed"] is None
    assert item["details"]["tier_text"] == "Listed · not reviewed by a person"
    assert item["unofficial"] == "Unofficial · not made or endorsed by the makers of NHL"
    assert found["indexes"] == [{"url": "bundled", "fetched_at": None, "cached": True}]
    assert "sports" in found["categories"] and found["kinds"]["pipeline"] == "built"
    assert client.get("/marketplace", params={"q": "soccer"}).json()["plugins"] == []

    r = client.post("/plugins/plan", json={"source": {"kind": "index", "id": "example-dev/nhl-goals"}}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert r.json()["source"] == {"kind": "git", "url": manifest["repository"], "commit": commit, "listed_in": "bundled"}
    client.post("/plugins/install", json={"plan_id": r.json()["plan_id"]}, headers=HEADERS)
    assert client.get("/marketplace", params={"q": "NHL"}).json()["plugins"][0]["installed"] == "1.0.0"
    r = client.post("/plugins/plan", json={"source": {"kind": "index", "id": "example-dev/missing"}}, headers=HEADERS)
    assert r.status_code == 404 and "not listed" in r.json()["detail"]
    assert client.post("/marketplace/refresh").json() == {"indexes": []}  # no address in settings
