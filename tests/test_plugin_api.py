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
    routes = [(m, p) for m, p, _mod in st.app_routes(client.app)
              if p.startswith(("/plugins", "/marketplace", "/plugin-models"))]
    assert len(routes) == 15
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
               "versions": [{"version": "1.0.0", "commit": commit, "requires": manifest["requires"]},
                            {"version": "0.9.0", "commit": "cd" * 20, "requires": {"clips_kitty": ">=9.0"}}],
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
    assert found["indexes"] == [{"url": "bundled", "fetched_at": None, "cached": True, "plugins": 1}]
    # what would stop it running here, said before installing
    assert item["problems_here"] == [] and item["versions"][0]["problem_here"] is None
    assert item["details"]["needs_python"] and "Python 3 installed on this PC" in item["details"]["requirements"]
    assert item["versions"][1]["problem_here"] == "it needs Clips Kitty >=9.0, and this is 2.0.0"
    with monkeypatch.context() as m:
        m.setattr(plugins_api.host, "find_python", lambda setting=None: None)
        assert client.get("/marketplace", params={"q": "NHL"}).json()["plugins"][0]["problems_here"] == [
            {"need": "python", "text": "It needs Python, and none was found on this PC"}]
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


def test_models_are_listed_looked_at_and_downloaded_through_the_api(tmp_path, monkeypatch, plugin_source,
                                                                    install_plugin):
    """GET /plugin-models, then plan and download one model, with Hugging Face
    played by two functions: nothing touches the network."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import hashlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from plugins import api as plugins_api

    weights, rev = b"example weights", "1" * 40
    sha = hashlib.sha256(weights).hexdigest()
    repo = {"name": "detector", "source": "huggingface", "id": "example-org/example-model", "revision": rev}
    manifest = plugin_source.manifest(models=[{**repo, "files": ["model.onnx"], "license": "apache-2.0"},
                                              {**repo, "name": "old", "files": ["model.bin"]}])
    data_dir = tmp_path / "data"
    install_plugin(data_dir, plugin_source.folder(manifest=manifest))
    fetched = []

    def fetch_json(url):
        if url.endswith(f"/api/models/example-org/example-model/revision/{rev}"):
            return {"cardData": {"license": "apache-2.0"}, "gated": False}
        if url.endswith(f"/api/models/example-org/example-model/tree/{rev}"):
            return [{"path": f, "size": len(weights), "lfs": {"oid": sha}} for f in ("model.onnx", "model.bin")]
        raise AssertionError(url)

    def fetcher(url, dest):
        fetched.append(url)
        Path(dest).write_bytes(weights)

    monkeypatch.setenv("CLIPS_KITTY_SESSION_SECRET", HEADERS["X-Clips-Kitty-Session"])
    app = FastAPI()
    plugins_api.install(app, data_dir=data_dir, app_version="2.0.0", model_fetcher=fetcher, model_json=fetch_json)
    client = TestClient(app, base_url="http://127.0.0.1")

    listed = client.get("/plugin-models").json()
    assert [(m["name"], m["installed"]) for m in listed["models"]] == [("detector", False), ("old", False)]
    assert listed["models"][0]["used_by"] == [{"plugin": PID, "name": "detector"}]
    assert listed["ollama_reachable"] is None  # no plugin lists an Ollama model, so it wasn't asked

    body = {"plugin": PID, "model": "detector"}
    assert client.post("/plugin-models/plan", json=body).status_code == 403
    assert client.post("/plugin-models/download", json=body).status_code == 403
    plan = client.post("/plugin-models/plan", json=body, headers=HEADERS).json()
    assert plan["download_bytes"] == len(weights) and plan["license"] == "apache-2.0" and plan["problem"] is None
    assert plan["files"] == [{"file": "model.onnx", "installed": False, "size": len(weights), "sha256": sha,
                              "already_here_as": None}]
    assert fetched == []  # a plan downloads nothing

    done = client.post("/plugin-models/download", json=body, headers=HEADERS).json()
    assert done["installed"] is True and Path(done["path"]).parts[-2:] == ("snapshots", rev)
    assert fetched == [f"https://huggingface.co/example-org/example-model/resolve/{rev}/model.onnx"]

    old = {"plugin": PID, "model": "old"}
    refused = client.post("/plugin-models/download", json=old, headers=HEADERS)
    assert refused.status_code == 400 and "pickle format" in refused.json()["detail"]
    linked = client.post("/plugin-models/download", json={**old, "allow_pickle": True}, headers=HEADERS).json()
    assert linked["installed"] is True and len(fetched) == 1  # the same bytes: linked, not fetched again
    assert [m["installed"] for m in client.get("/plugin-models").json()["models"]] == [True, True]

    for wrong, status in (({"plugin": PID, "model": "nope"}, 404), ({"plugin": "example-dev/absent", "model": "x"}, 404),
                          ({"plugin": "not an id", "model": "x"}, 404)):
        assert client.post("/plugin-models/plan", json=wrong, headers=HEADERS).status_code == status
