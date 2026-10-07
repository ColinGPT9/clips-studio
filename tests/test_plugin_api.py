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
    assert len(routes) == 18
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
    bundled.write_text(json.dumps({"format": 1, "plugins": [listing], "blocklist": [],
                                   "counter": {"install": "https://example.com/counts/{asset}"}}))

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
    counted = []
    app = FastAPI()
    plugins_api.install(app, data_dir=tmp_path / "data", app_version="2.0.0", fetcher=fetcher, bundled_index=bundled,
                        count_opener=counted.append)
    client = TestClient(app, base_url="http://127.0.0.1")

    found = client.get("/marketplace", params={"q": "NHL"}).json()
    (item,) = found["plugins"]
    assert item["id"] == "example-dev/nhl-goals" and item["installed"] is None
    assert item["details"]["tier_text"] == "Community · not reviewed by a person"
    assert item["badges"] == ["community"]
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
    view = client.post("/plugins/install", json={"plan_id": r.json()["plan_id"]}, headers=HEADERS).json()
    assert client.get("/marketplace", params={"q": "NHL"}).json()["plugins"][0]["installed"] == "1.0.0"
    # One anonymous count for a first install from a listing, named after the listing and nothing else.
    _wait_for(lambda: counted)
    assert view["counted"] is True and counted == ["https://example.com/counts/example-dev__nhl-goals.count"]
    r = client.post("/plugins/plan", json={"source": {"kind": "index", "id": "example-dev/missing"}}, headers=HEADERS)
    assert r.status_code == 404 and "not listed" in r.json()["detail"]
    assert client.post("/marketplace/refresh").json() == {"indexes": []}  # no address in settings


def _wait_for(condition, seconds: float = 5.0) -> None:
    import time

    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        time.sleep(0.01)


def test_install_counting_can_be_switched_off_and_skips_updates(tmp_path, monkeypatch, plugin_source):
    """The counter: only a first install from a listing whose index names a
    counter address, never an update, and never once switched off."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from plugins import api as plugins_api
    from plugins import counter, registry, sources

    monkeypatch.setattr(sources, "find_git", lambda: None)
    monkeypatch.setenv("CLIPS_KITTY_SESSION_SECRET", HEADERS["X-Clips-Kitty-Session"])
    commits = {"1.0.0": "ab" * 20, "1.1.0": "cd" * 20}
    repo = "https://github.com/example-dev/counted"

    def manifest_of(version):
        return plugin_source.manifest(id="example-dev/counted", version=version, repository=repo)

    listing = {"id": "example-dev/counted", "publisher": "example-dev", "repository": repo, "path": ".",
               "aliases": [], "latest": "1.1.0", "settings": {}, "checks": {},
               "versions": [{"version": v, "commit": c, "requires": manifest_of(v)["requires"]}
                            for v, c in sorted(commits.items(), reverse=True)],
               **{k: manifest_of("1.1.0")[k] for k in registry.SHOWN if k in manifest_of("1.1.0")}}
    bundled = tmp_path / "index.json"

    def publish(counter_address):
        index = {"format": 1, "plugins": [listing], "blocklist": []}
        if counter_address:
            index["counter"] = {"install": counter_address}
        bundled.write_text(json.dumps(index))

    def fetcher(url, path):
        import io
        import tarfile

        version = next(v for v, c in commits.items() if c in url)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, text in (("clipskitty.yaml", json.dumps(manifest_of(version))), ("src/main.py", "print()\n")):
                info = tarfile.TarInfo(f"counted-{commits[version]}/{name}")
                info.size = len(text.encode())
                tar.addfile(info, io.BytesIO(text.encode()))
        Path(path).write_bytes(buf.getvalue())

    counted = []
    data_dir = tmp_path / "data"
    app = FastAPI()
    plugins_api.install(app, data_dir=data_dir, app_version="2.0.0", fetcher=fetcher, bundled_index=bundled,
                        count_opener=counted.append)
    client = TestClient(app, base_url="http://127.0.0.1")

    def install(version):
        r = client.post("/plugins/plan", json={"source": {"kind": "index", "id": listing["id"], "version": version}},
                        headers=HEADERS)
        assert r.status_code == 200 and r.json()["ok"], r.text
        return client.post("/plugins/install", json={"plan_id": r.json()["plan_id"]}, headers=HEADERS).json()

    publish(None)  # no counter address: nothing is sent, whatever the setting
    state = client.get("/marketplace/counting").json()
    assert state["enabled"] is True and state["active"] is False and "no ID" in state["text"]
    assert install("1.0.0")["counted"] is False
    client.delete("/plugins/example-dev/counted", headers=HEADERS)
    publish("https://example.com/counts/{asset}")
    assert install("1.0.0")["counted"] is True
    assert install("1.1.0")["counted"] is False  # an update isn't an install
    _wait_for(lambda: counted)
    assert counted == ["https://example.com/counts/example-dev__counted.count"]
    # Switched off: a session-guarded change, kept in the data folder.
    assert client.put("/marketplace/counting", json={"enabled": False}).status_code == 403
    state = client.put("/marketplace/counting", json={"enabled": False}, headers=HEADERS).json()
    assert state["enabled"] is False and state["active"] is True and counter.chosen(data_dir) is False
    client.delete("/plugins/example-dev/counted", headers=HEADERS)
    assert install("1.1.0")["counted"] is False
    assert counted == ["https://example.com/counts/example-dev__counted.count"]
    # Settings can switch it off for everyone on the PC, whatever was chosen.
    counter.set_enabled(data_dir, True)
    assert counter.enabled(data_dir, {"plugins": {"count_installs": False}}) is False
    assert counter.count_install(data_dir, {"plugins": {"count_installs": False}},
                                 "https://example.com/{asset}", listing["id"], opener=counted.append) is None
    # Only an https address with one {asset}, and only a real listing id.
    assert counter.count_url("http://example.com/{asset}", "a/b") is None
    assert counter.count_url("https://example.com/x", "a/b") is None
    assert counter.count_url("https://example.com/{asset}", "../x") is None


def test_the_catalog_route_lists_the_directory_with_honest_labels(tmp_path, monkeypatch):
    """GET /marketplace/catalog over an index with apps, a model and a tool:
    search, kind and section filters, and an index elsewhere can't make
    someone else's project look official."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from plugins import api as plugins_api
    from plugins import registry

    sections = {"app": {"sections": [{"id": "video-clipping", "title": "Video clipping"},
                                     {"id": "gaming", "title": "Gaming"}], "wanted": []},
                "model": {"sections": [{"id": "speech", "title": "Speech"}], "wanted": []},
                "tool": {"sections": [{"id": "developer", "title": "Developer tools"}], "wanted": []}}
    entries = [
        {"id": "apps/example-clipper", "kind": "app", "slug": "example-clipper", "name": "Example Clipper",
         "description": "Turns long videos into vertical clips.", "section": "video-clipping",
         "relationship": "related", "license": "MIT", "source": {"github": "https://github.com/example-org/clipper"},
         "badges": ["official", "featured"], "added": "2026-10-07"},
        {"id": "apps/example-game-clipper", "kind": "app", "slug": "example-game-clipper", "name": "Game Clipper",
         "description": "Finds kills in an example game.", "section": "gaming", "relationship": "related",
         "license": "GPL-3.0-or-later", "source": {"github": "https://github.com/example-org/game-clipper"},
         "games": ["example-game"], "badges": ["community"], "added": "2026-10-07"},
        {"id": "models/example-speech", "kind": "model", "slug": "example-speech", "name": "Example Speech",
         "description": "Speech to text.", "section": "speech", "relationship": "related", "license": "Apache-2.0",
         "source": {"huggingface": "example-org/example-speech"}, "badges": ["community"], "added": "2026-10-07",
         "metrics": {"models": {"example-org/example-speech": {"downloads": 1200, "likes": 30}}}},
        {"id": "tools/clips-kitty-sdk", "kind": "tool", "slug": "clips-kitty-sdk", "name": "Clips Kitty SDK",
         "description": "Write plugins.", "section": "developer", "relationship": "built-with", "uses": "api",
         "license": "MIT", "source": {"github": "https://github.com/ColinGPT9/clips-studio", "path": "sdk/python"},
         "badges": ["official"], "added": "2026-10-07"},
        {"id": "apps/../evil", "kind": "app", "name": "Bad id", "license": "MIT",
         "source": {"github": "https://github.com/example-org/x"}},
        {"id": "apps/no-source", "kind": "app", "name": "No source", "license": "MIT",
         "source": {"url": "http://insecure.example.com"}},
    ]
    bundled = tmp_path / "index.json"
    bundled.write_text(json.dumps({"format": 1, "plugins": [], "blocklist": [], "catalog": entries,
                                   "sections": sections, "metrics_at": "2026-10-07"}))
    monkeypatch.setenv("CLIPS_KITTY_SESSION_SECRET", HEADERS["X-Clips-Kitty-Session"])
    app = FastAPI()
    plugins_api.install(app, data_dir=tmp_path / "data", app_version="2.0.0", bundled_index=bundled)
    client = TestClient(app, base_url="http://127.0.0.1")

    every = client.get("/marketplace/catalog").json()
    # By name with no query; the two bad entries are dropped.
    assert [e["id"] for e in every["entries"]] == ["tools/clips-kitty-sdk", "apps/example-clipper",
                                                    "models/example-speech", "apps/example-game-clipper"]
    by_id = {e["id"]: e for e in every["entries"]}
    assert by_id["apps/example-clipper"]["badges"] == ["community", "featured"]  # not the project's own
    assert by_id["tools/clips-kitty-sdk"]["badges"] == ["official"]
    assert by_id["apps/example-game-clipper"]["unofficial"].startswith("Unofficial · not made or endorsed")
    assert by_id["apps/example-clipper"]["unofficial"] is None
    assert every["metrics_at"] == "2026-10-07" and every["badges"]["compatible"]["label"] == "✓ Compatible"
    assert "not a security review" in every["badges"]["compatible"]["meaning"]
    assert set(every["sections"]) == {"app", "model", "tool"}
    assert [e["id"] for e in client.get("/marketplace/catalog", params={"kind": "app"}).json()["entries"]] == [
        "apps/example-clipper", "apps/example-game-clipper"]
    assert [e["id"] for e in client.get("/marketplace/catalog", params={"q": "speech"}).json()["entries"]] == [
        "models/example-speech"]
    assert [e["id"] for e in client.get("/marketplace/catalog", params={"section": "gaming"}).json()["entries"]] == [
        "apps/example-game-clipper"]
    assert client.get("/marketplace/catalog", params={"kind": "pipeline"}).status_code == 400
    assert registry.catalog_entries(tmp_path / "data", [], bundled=bundled)[1] == sections


def test_a_listing_from_the_projects_own_repository_is_official(tmp_path, monkeypatch, plugin_source):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from plugins import api as plugins_api
    from plugins import registry

    def listing(pid, repository):
        m = plugin_source.manifest(id=pid, repository=repository)
        return {"id": pid, "publisher": pid.split("/")[0], "repository": repository, "path": ".", "aliases": [],
                "latest": "1.0.0", "settings": {}, "checks": {}, "badges": ["official"],
                "versions": [{"version": "1.0.0", "commit": "ab" * 20, "requires": m["requires"]}],
                **{k: m[k] for k in registry.SHOWN if k in m}}

    bundled = tmp_path / "index.json"
    bundled.write_text(json.dumps({"format": 1, "blocklist": [], "plugins": [
        listing("clips-kitty-examples/demo", "https://github.com/ColinGPT9/clips-studio"),
        listing("example-dev/claims-official", "https://github.com/example-dev/claims-official")]}))
    monkeypatch.setenv("CLIPS_KITTY_SESSION_SECRET", HEADERS["X-Clips-Kitty-Session"])
    app = FastAPI()
    plugins_api.install(app, data_dir=tmp_path / "data", app_version="2.0.0", bundled_index=bundled)
    client = TestClient(app, base_url="http://127.0.0.1")
    found = {p["id"]: p for p in client.get("/marketplace").json()["plugins"]}
    official, other = found["clips-kitty-examples/demo"], found["example-dev/claims-official"]
    assert official["badges"] == ["official"] and official["details"]["tier"] == "listed-official"
    assert official["details"]["tier_text"] == "✓ Official · made by the Clips Kitty project"
    assert other["badges"] == ["community"] and other["details"]["tier"] == "listed"


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
