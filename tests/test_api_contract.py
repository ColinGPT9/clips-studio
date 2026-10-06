"""The local API's public boundary: which routes are promised, and what they do.

server/api_stability.py labels every route stable, experimental or internal.
Stable means documented as supported in docs/API.md, which has always said a
supported route changes shape only with a note in CHANGELOG.md and a bump of
API_VERSION. These tests hold that line:

- the label table names only routes that exist, and the generated reference
  (docs/developers/api-reference.md) matches the app;
- each stable route still accepts what it accepted when it was pinned
  (tests/fixtures/api/stable_contract.json): parameters and body fields may be
  added if optional, never removed, retyped or made required;
- the stable calls an integrator starts with behave as docs/API.md shows.

Nothing here starts the worker: the app is built without `with`, so jobs are
queued and never run.
"""

import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "tests" / "fixtures" / "api" / "stable_contract.json"
URL = "https://www.youtube.com/watch?v=aB3dEfGhIjK"


@pytest.fixture
def app(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("yaml")
    pytest.importorskip("numpy")
    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    return create_app(config, settings)


@pytest.fixture
def client(app):
    pytest.importorskip("httpx")
    pytest.importorskip("yt_dlp")  # sources.dispatch imports the YouTube source
    from fastapi.testclient import TestClient

    # No `with`: startup never runs, so no worker thread starts.
    return TestClient(app, base_url="http://127.0.0.1")


# ---- the label table and the reference ---------------------------------------


def test_every_labelled_route_exists(app):
    from server import api_stability as st

    served = {(m, p) for m, p, _ in st.app_routes(app)}
    stale = sorted(k for k in st.ROUTES if k not in served)
    assert not stale, f"server/api_stability.py labels routes the app does not serve: {stale}"


def test_every_route_has_one_of_three_labels(app):
    from server import api_stability as st

    labels = {st.label(m, p) for m, p, _ in st.app_routes(app)}
    assert labels <= {st.STABLE, st.EXPERIMENTAL, st.INTERNAL}
    assert st.label("GET", "/a/route/nobody/listed") == st.INTERNAL


def test_the_table_uses_only_known_labels_and_sections():
    from server import api_stability as st

    for key, (lab, section, purpose) in st.ROUTES.items():
        assert lab in (st.STABLE, st.EXPERIMENTAL), key
        assert section in st.SECTIONS, key
        assert purpose.strip(), key


def test_the_routes_docs_api_calls_supported_are_stable():
    # docs/API.md: "Of these, GET /youtube/status, POST /clips/{id}/publish and
    # GET /clips/{id}/publish are supported; the rest exist to serve the
    # Settings screen and may change with it."
    from server import api_stability as st

    for route in [("GET", "/youtube/status"), ("POST", "/clips/{clip_id}/publish"),
                  ("GET", "/clips/{clip_id}/publish"), ("GET", "/health"), ("POST", "/jobs"), ("WS", "/ws")]:
        assert st.label(*route) == st.STABLE, route
    for route in [("PATCH", "/youtube/settings"), ("PUT", "/youtube/credentials"),
                  ("POST", "/youtube/connect"), ("GET", "/youtube/uploads")]:
        assert st.label(*route) == st.INTERNAL, route


def test_the_reference_is_up_to_date(app):
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_api_reference", ROOT / "scripts" / "gen_api_reference.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    current = gen.REFERENCE.read_text(encoding="utf-8")
    assert current == gen.render(app), (
        "docs/developers/api-reference.md is out of date: run python scripts/gen_api_reference.py"
    )


# ---- stable routes keep accepting what they accepted -------------------------


def _split(text: str, sep: str) -> list[str]:
    """`text` cut at `sep` where it is not inside <>, {} or []."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "<{[":
            depth += 1
        elif ch in ">}]":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _fields(obj: str) -> dict[str, tuple[bool, str]]:
    """`{a:string,b?:integer}` as {name: (required, type)}."""
    out = {}
    for part in _split(obj[1:-1], ","):
        if part:
            name, _, kind = part.partition(":")
            out[name.rstrip("?")] = (not name.endswith("?"), kind)
    return out


def _accepts(old: str, new: str) -> bool:
    """Whether everything a client could send as type `old` is still accepted as `new`
    (both in api_stability._shape's notation)."""
    new_alts = _split(new, "|")
    if "any" in new_alts:
        return True
    return all(any(_accepts_one(o, n) for n in new_alts) for o in _split(old, "|"))


def _accepts_one(old: str, new: str) -> bool:
    if old == new or (old == "integer" and new == "number"):
        return True
    for wrapper in ("array<", "map<"):
        if old.startswith(wrapper) and new.startswith(wrapper):
            return _accepts(old[len(wrapper):-1], new[len(wrapper):-1])
    if old.startswith("enum[") and new.startswith("enum["):
        return set(old[5:-1].split(",")) <= set(new[5:-1].split(","))
    if old.startswith("{") and new.startswith("{"):
        was, now = _fields(old), _fields(new)
        return (all(name in now and _accepts(kind, now[name][1]) and (req or not now[name][0])
                    for name, (req, kind) in was.items())
                and not any(req for name, (req, _) in now.items() if name not in was))
    return False


def _breaks(pinned: dict, now: dict) -> list[str]:
    """What in `now` would break a client written against `pinned`."""
    problems = []
    for route, old in pinned.items():
        new = now.get(route)
        if new is None:
            problems.append(f"{route}: removed")
            continue
        for kind in ("params", "body"):
            old_fields = old[kind] or {}
            new_fields = new[kind] or {}
            if old[kind] is not None and new[kind] is None:
                problems.append(f"{route}: no longer takes a JSON body")
            for name, spec in old_fields.items():
                cur = new_fields.get(name)
                if cur is None:
                    problems.append(f"{route}: {kind[:-1] if kind == 'params' else 'body'} field '{name}' removed")
                    continue
                if not _accepts(spec.get("type", "any"), cur.get("type", "any")):
                    problems.append(f"{route}: '{name}' changed from {spec.get('type')} to {cur.get('type')}")
                if cur.get("in") != spec.get("in"):
                    problems.append(f"{route}: '{name}' moved from {spec.get('in')} to {cur.get('in')}")
                if cur["required"] and not spec["required"]:
                    problems.append(f"{route}: '{name}' is now required")
            for name, cur in new_fields.items():
                if name not in old_fields and cur["required"]:
                    problems.append(f"{route}: new required '{name}'")
    return problems


def test_stable_routes_still_accept_what_they_were_pinned_with(app):
    from server import api_stability as st

    pinned = json.loads(CONTRACT.read_text(encoding="utf-8"))
    problems = _breaks(pinned, st.contract(app))
    assert not problems, (
        "A stable route changed in a way that breaks clients. Make the change additive, or, if it is "
        "meant, bump API_VERSION in server/api.py, note it in CHANGELOG.md and re-pin with "
        "python scripts/gen_api_reference.py --update-contract:\n  " + "\n  ".join(problems)
    )


def test_every_stable_http_route_is_pinned(app):
    from server import api_stability as st

    pinned = json.loads(CONTRACT.read_text(encoding="utf-8"))
    missing = sorted(set(st.contract(app)) - set(pinned))
    assert not missing, (
        f"Stable routes with no pinned shape: {missing}. Pin them with "
        "python scripts/gen_api_reference.py --update-contract"
    )


def test_the_breakage_check_tells_additive_from_breaking():
    pinned = {"POST /x": {"params": {"q": {"in": "query", "required": False, "type": "integer"}},
                          "body": {"url": {"required": True, "type": "string"}}}}
    added = {"POST /x": {"params": {"q": {"in": "query", "required": False, "type": "integer"},
                                    "r": {"in": "query", "required": False, "type": "string"}},
                         "body": {"url": {"required": True, "type": "string"},
                                  "extra": {"required": False, "type": "boolean"}}}}
    assert _breaks(pinned, added) == []
    retyped = json.loads(json.dumps(added))
    retyped["POST /x"]["body"]["url"]["type"] = "integer"
    assert _breaks(pinned, retyped)
    stricter = json.loads(json.dumps(added))
    stricter["POST /x"]["params"]["q"]["required"] = True
    assert _breaks(pinned, stricter)
    new_required = json.loads(json.dumps(added))
    new_required["POST /x"]["body"]["extra"]["required"] = True
    assert _breaks(pinned, new_required)
    assert _breaks(pinned, {}) == ["POST /x: removed"]


@pytest.mark.parametrize("old, new, ok", [
    ("string", "string|null", True),
    ("string|null", "string", False),
    ("integer", "number", True),
    ("array<{url:string}>", "array<{pipeline?:object,url:string}>", True),
    ("array<{url:string}>", "array<{pipeline:object,url:string}>", False),
    ("array<{url:string,x?:integer}>", "array<{url:string}>", False),
    ("{a?:string}", "{a:string}", False),
    ("map<string>", "map<string|integer>", True),
    ("enum[a,b]", "enum[a,b,c]", True),
    ("enum[a,b]", "enum[a]", False),
    ("null|object|string", "null|object|string", True),
    ("object", "any", True),
])
def test_nested_shapes_are_compared_field_by_field(old, new, ok):
    assert _accepts(old, new) is ok


# ---- what the stable calls do ------------------------------------------------


def test_health_reports_the_api_version(client):
    from server.api import API_VERSION

    body = client.get("/health").json()
    assert set(body) == {"ok", "app_version", "api_version"}
    assert body["ok"] is True and body["api_version"] == API_VERSION == 1
    assert isinstance(body["app_version"], str) and body["app_version"]


def test_submitting_a_job(client):
    assert client.post("/jobs", json={}).status_code == 422  # no url
    bad_hook = client.post("/jobs", json={"url": URL, "webhook_url": "ftp://example.com/hook"})
    assert bad_hook.status_code == 400 and "webhook_url" in bad_hook.json()["detail"]

    first = client.post("/jobs", json={"url": URL})
    assert first.status_code == 200
    job_id = first.json()["job_id"]
    assert isinstance(job_id, int)

    again = client.post("/jobs", json={"url": URL}).json()
    assert again == {"job_id": None, "already_queued": True, "video_id": "aB3dEfGhIjK", "queued_job_id": job_id}


def test_an_unknown_sport_is_refused_with_the_choices(client):
    r = client.post("/jobs", json={"url": URL, "sport": {"name": "curling"}})
    assert r.status_code == 400
    assert "soccer" in r.json()["detail"] and "basketball" in r.json()["detail"]


def test_a_batch_queues_the_good_links_and_reports_the_rest(client):
    body = client.post("/jobs/batch", json={"items": [
        {"url": "https://www.twitch.tv/videos/123456789"}, {"url": "not a link"}]}).json()
    assert [set(c) for c in body["created"]] == [{"url", "job_id", "video_id"}]
    assert body["created"][0]["video_id"] == "tw_123456789"
    assert body["skipped"] == [{"url": "not a link", "reason": "unrecognized"}]


def test_jobs_are_listed_and_read_one_at_a_time(client):
    job_id = client.post("/jobs", json={"url": URL}).json()["job_id"]
    listed = client.get("/jobs").json()
    assert isinstance(listed, list) and listed[-1]["id"] == job_id
    documented = {"id", "type", "status", "error", "video_id", "title", "position", "attempts",
                  "interrupted", "created_at", "started_at", "updated_at", "finished_at", "payload"}
    assert documented <= set(listed[-1])
    one = client.get(f"/jobs/{job_id}").json()
    assert documented <= set(one) and one["status"] == "queued"
    assert json.loads(one["payload"])["url"] == URL

    assert client.get("/jobs/999999").json() == {"detail": "no such job"}
    assert client.get("/jobs/999999").status_code == 404
    assert client.get("/jobs/not-a-number").status_code == 422
    assert set(client.get(f"/jobs/{job_id}/log").json()) == {"log", "missing"}
    assert client.delete(f"/jobs/{job_id}").json() == {"deleted": job_id}


def test_cancel_needs_a_video(client):
    assert client.post("/cancel", json={}).status_code == 400
    assert client.post("/cancel", json={"video_id": "aB3dEfGhIjK"}).json() == {"cancelling": "aB3dEfGhIjK"}


def test_the_queue_view_and_pausing(client):
    q = client.get("/queue").json()
    assert {"processing", "queued", "completed", "failed", "paused", "estimate", "capacity", "max_active"} <= set(q)
    assert {"queued_seconds", "per_video_seconds", "samples", "confident"} <= set(q["estimate"])
    assert client.post("/queue/pause").json() == {"paused": True}
    assert client.get("/queue").json()["paused"] is True
    assert client.post("/queue/resume").json() == {"paused": False}


def test_a_local_path_that_is_not_a_video_is_refused(client):
    r = client.post("/videos/local", json={"path": "C:/does/not/exist.mp4"})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("not a video file this app can open")


def test_the_sports_list(client):
    sports = client.get("/sports").json()
    assert {s["id"] for s in sports} >= {"soccer", "basketball"}
    for s in sports:
        assert {"id", "label", "highlights", "periods"} <= set(s)
        assert all(set(h) >= {"id", "label"} for h in s["highlights"])


def test_the_library_reads(client):
    assert client.get("/videos").json() == []
    assert client.get("/videos/not-a-video/clips").json() == []  # documented: 200 [], not 404
    assert client.get("/media/999999").status_code == 404
    assert client.get("/clips/999999/captions").status_code == 404


def test_languages_presets_and_automation(client):
    langs = client.get("/languages").json()
    assert {"languages", "dubbing_available"} <= set(langs)
    assert {"code", "name", "native", "can_dub", "caption_font"} <= set(langs["languages"][0])
    presets = client.get("/integrations/presets").json()
    assert {"standard", "podcast", "long_clips", "highlights"} <= {p["id"] for p in presets}
    assert all({"id", "name", "description", "options"} <= set(p) for p in presets)
    auto = client.get("/automation").json()
    assert {"enabled", "delete_sources", "interval_minutes", "watches", "watching", "presets"} <= set(auto)


def test_youtube_status_answers_when_publishing_is_off(client):
    assert "enabled" in client.get("/youtube/status").json()


def test_system_stats(client):
    pytest.importorskip("psutil")
    stats = client.get("/system/stats").json()
    assert {"cpu_percent", "ram_percent", "data_dir_bytes", "disk_free_bytes", "gpu", "started_at",
            "uptime_seconds"} <= set(stats)
