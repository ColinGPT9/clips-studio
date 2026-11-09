"""Make clips again and the clips a person is working with at that moment
(core/in_use.py, #190).

A publish records its post only once the upload is done, and an Original
export marks the clip exported only once the copy is made. Until then the
database says nothing, so each of these requests holds its clips from before
it reads them until what it did is recorded, and Make clips again
(core/remake.py) keeps every clip held. A batch holds all of its clips from
before its first upload, and a YouTube publish plan all of its clips from
before it reads the first. A thumbnail choice, an AI edit, a YouTube publish
and an export queued on the editor lane hold theirs the same way.

The routes run in-process (TestClient); nothing listens on a port. The
uploads are fakes that look at what is held while they "upload".
"""

import base64
import json
import shutil
import threading
import types
from pathlib import Path

import pytest

from core import in_use, remake
from core.state import StateDB

pytest.importorskip("fastapi")  # CI's light install has no web layer
pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
VID = "abcdefghijk"


@pytest.fixture
def lib(tmp_path):
    """A data folder with one processed video; `clip()` adds a rendered clip."""
    data = tmp_path / "data"
    db = StateDB(data / "state.db")
    db.upsert_video(VID, title="A stream")
    db.set_video_status(VID, "done")
    folder = data / "clips"
    folder.mkdir(parents=True)

    def clip(start, end):
        path = folder / f"clip_{int(start):05d}-{int(end):05d}.mp4"
        path.write_bytes(b"x" * 1000)
        return db.add_clip(VID, start, end, 70, "hook", path=str(path), status="queued", title="Model title")

    yield types.SimpleNamespace(db=db, data=data, path=data / "state.db", clip=clip)
    db.conn.close()
    assert in_use.held() == set(), "a request left a clip held"


def _client(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1")


def _published(db, clip_id) -> list[str]:
    return [r[0] for r in db.conn.execute("SELECT platform FROM clip_publishes WHERE clip_id = ?", (clip_id,))]


def _published_now(lib, clip_id) -> list[str]:
    """_published from the thread the route runs on, which needs a
    connection of its own."""
    d = StateDB(lib.path)
    try:
        return _published(d, clip_id)
    finally:
        d.close()


# ---- WoopSocial ---------------------------------------------------------------------


def _woopsocial(lib, monkeypatch, start):
    """The WoopSocial routes, switched on, with `start(path, platforms)` as
    the upload."""
    from publish import uploadpost as up
    from publish import woopsocial as ws
    from server import woopsocial_api
    from server import woopsocial_service as service

    def fake_start(self, path, *, platforms, **_kw):
        start(path, platforms)
        return up.FanOutResult(request_id="req-1", outcomes=[up.PlatformOutcome(platform=p) for p in platforms])

    monkeypatch.setattr(ws.WoopSocialPublisher, "start", fake_start)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: types.SimpleNamespace(projects=lambda: [{"id": "p1"}]))
    d = StateDB(lib.path)
    service.save_settings(d, {"enabled": True, "project_id": "p1"})
    d.close()
    app = FastAPI()
    woopsocial_api.install(app, config={}, db=lambda: StateDB(lib.path), data_dir=lib.data)
    return _client(app)


def test_a_woopsocial_publish_holds_its_clip_until_its_post_is_recorded(lib, monkeypatch):
    clip_id = lib.clip(10.0, 40.0)
    during = []
    client = _woopsocial(lib, monkeypatch, lambda path, platforms: during.append(
        (in_use.held(), _published_now(lib, clip_id))))
    r = client.post(f"/woopsocial/clips/{clip_id}/publish", json={"platforms": ["tiktok"], "title": "Mine"})
    assert r.status_code == 200, r.text
    assert during == [({clip_id}, [])]          # held while nothing yet says it was used
    assert in_use.held() == set() and _published(lib.db, clip_id) == ["tiktok"]


def test_a_woopsocial_publish_that_fails_lets_go_of_its_clip(lib, monkeypatch):
    from publish.errors import PublishError

    clip_id = lib.clip(10.0, 40.0)

    def refused(path, platforms):
        raise PublishError("WoopSocial refused the upload.")

    client = _woopsocial(lib, monkeypatch, refused)
    r = client.post(f"/woopsocial/clips/{clip_id}/publish", json={"platforms": ["tiktok"], "title": "Mine"})
    assert r.status_code == 400 and in_use.held() == set()


def test_a_woopsocial_batch_holds_every_clip_from_its_first_upload(lib, monkeypatch):
    ids = [lib.clip(10.0, 40.0), lib.clip(50.0, 80.0), lib.clip(90.0, 120.0)]
    during = []
    client = _woopsocial(lib, monkeypatch, lambda path, platforms: during.append(in_use.held()))
    r = client.post("/woopsocial/batch", json={"clip_ids": ids, "platforms": ["tiktok"]})
    assert r.status_code == 200 and len(r.json()["started"]) == 3
    assert during == [set(ids)] * 3


# ---- Upload-Post --------------------------------------------------------------------


def _uploadpost(lib, monkeypatch, start):
    from publish import uploadpost as up
    from server import uploadpost_api
    from server import uploadpost_service as service

    def fake_start(self, path, *, platforms, fields, thumbnail=None, idempotency_key=""):
        start(path, platforms)
        return up.FanOutResult(request_id="req-1", outcomes=[up.PlatformOutcome(platform=p) for p in platforms])

    monkeypatch.setattr(up.UploadPostPublisher, "start", fake_start)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())
    d = StateDB(lib.path)
    service.save_settings(d, {"enabled": True, "profile": "me"})
    d.close()
    app = FastAPI()
    uploadpost_api.install(app, config={}, db=lambda: StateDB(lib.path), data_dir=lib.data)
    return _client(app)


def test_an_upload_post_publish_and_batch_hold_their_clips(lib, monkeypatch):
    one = lib.clip(10.0, 40.0)
    batch = [lib.clip(50.0, 80.0), lib.clip(90.0, 120.0)]
    during = []
    client = _uploadpost(lib, monkeypatch, lambda path, platforms: during.append(in_use.held()))
    r = client.post(f"/uploadpost/clips/{one}/publish", json={"platforms": ["tiktok"], "title": "Mine"})
    assert r.status_code == 200, r.text
    r = client.post("/uploadpost/batch", json={"clip_ids": batch, "platforms": ["tiktok"]})
    assert r.status_code == 200 and len(r.json()["started"]) == 2
    assert during == [{one}, set(batch), set(batch)]


# ---- YouTube: a publish job, and a chosen thumbnail ----------------------------------


def _youtube(lib, monkeypatch):
    from server import youtube_api, youtube_service

    monkeypatch.setattr(youtube_service, "is_enabled", lambda _d: True)
    app = FastAPI()
    worker = types.SimpleNamespace(notify=lambda: None)
    youtube_api.install(app, config={}, db=lambda: StateDB(lib.path), data_dir=lib.data,
                        worker=worker, publish_worker=worker)
    return _client(app)


def test_a_youtube_publish_holds_its_clip_until_its_job_is_saved(lib, monkeypatch):
    clip_id = lib.clip(10.0, 40.0)
    client = _youtube(lib, monkeypatch)
    during = []
    real = StateDB.add_publish_job

    def add_publish_job(self, *args, **kwargs):
        during.append(in_use.held())
        return real(self, *args, **kwargs)

    monkeypatch.setattr(StateDB, "add_publish_job", add_publish_job)
    r = client.post(f"/clips/{clip_id}/publish", json={"title": "Mine"})
    assert r.status_code == 200, r.text
    assert during == [{clip_id}] and in_use.held() == set()


def test_a_youtube_publish_plan_holds_every_clip_from_its_first_read(lib, monkeypatch):
    """POST /publish/plan/execute (the assistant's Confirm button, the MCP
    tool publish_plan_execute) reads every clip of the plan, then publishes
    them one at a time: all of them are held from before that first read
    until the last publish job is saved."""
    ids = [lib.clip(10.0, 40.0), lib.clip(50.0, 80.0), lib.clip(90.0, 120.0)]
    client = _youtube(lib, monkeypatch)
    reads = _hold_seen(monkeypatch, StateDB, "get_clip")
    saved = _hold_seen(monkeypatch, StateDB, "add_publish_job")
    r = client.post("/publish/plan/execute", json={"items": [{"clip_id": c} for c in ids]})
    assert r.status_code == 200 and [s["clip_id"] for s in r.json()["started"]] == ids
    assert len(reads) == 6 and all(held == set(ids) for held in reads)   # the titles, then each publish
    assert saved == [set(ids)] * 3 and in_use.held() == set()


def test_a_chosen_thumbnail_keeps_its_clip(lib, monkeypatch):
    """Chosen while the run works: held while the file is written, and the
    file keeps the clip once it's there."""
    chosen, other = lib.clip(10.0, 40.0), lib.clip(50.0, 80.0)
    client = _youtube(lib, monkeypatch)
    run = remake.Run(lib.db, VID, lib.data)           # a Make clips again starts
    during = []
    real = Path.write_bytes

    def write_bytes(self, data):
        if self.name == f"clip_{chosen}_chosen.jpg":
            during.append(in_use.held())
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 64          # the person's own image
    r = client.post(f"/clips/{chosen}/thumbnail", json={"image": base64.b64encode(png).decode()})
    assert r.status_code == 200, r.text
    assert during == [{chosen}]
    run.db.add_clip(VID, 500.0, 530.0, 80, "new", path="", status="queued")
    out = run.finish()
    assert lib.db.get_clip(chosen) is not None and out["removed"] == [other]
    assert out["kept_because"] == {"edited": 1}


# ---- the app's own routes: Original export, the editor lane, the AI chat --------------


@pytest.fixture
def app(lib, monkeypatch, tmp_path):
    pytest.importorskip("yaml")
    from main import BUNDLED_CONFIG, load_config
    from plugins import session
    from server.api import create_app
    from server.events import broadcaster

    monkeypatch.delenv("CLIPS_KITTY_SESSION_SECRET", raising=False)
    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(lib.data)
    built = create_app(config, settings)
    monkeypatch.setattr(broadcaster, "publish", lambda _e: None)
    client = _client(built)
    client.session = {"X-Clips-Kitty-Session": session.path(lib.data).read_text(encoding="utf-8").strip()}
    client.app_state = built.state
    return client


def test_an_original_export_holds_its_clips_while_copying(lib, app, monkeypatch, tmp_path):
    ids = [lib.clip(10.0, 40.0), lib.clip(50.0, 80.0)]
    during = []
    real = shutil.copy2

    def copy2(src, dst, *a, **k):
        during.append(in_use.held())
        return real(src, dst, *a, **k)

    monkeypatch.setattr(shutil, "copy2", copy2)
    r = app.post(f"/clips/{ids[0]}/export", json={"folder": str(tmp_path / "out")})
    assert r.status_code == 200 and len(r.json()["exported"]) == 1
    r = app.post("/export/batch", json={"clip_ids": ids, "folder": str(tmp_path / "out")})
    assert r.status_code == 200 and len(r.json()["exported"]) == 2
    assert during == [{ids[0]}, set(ids), set(ids)]
    assert in_use.held() == set()


def test_an_export_queued_on_the_lane_holds_its_clips_until_the_lane_has_them(lib, app, monkeypatch, tmp_path):
    ids = [lib.clip(10.0, 40.0), lib.clip(50.0, 80.0)]
    lane = app.app_state.editor_lane
    during = []
    real = lane.submit_export

    def submit_export(**kwargs):
        during.append(in_use.held())
        return real(**kwargs)

    monkeypatch.setattr(lane, "submit_export", submit_export)
    r = app.post("/export/batch", json={"clip_ids": ids, "folder": str(tmp_path / "out"),
                                        "preset": {"kind": "custom", "resolution": "720p"}}, headers=app.session)
    assert r.status_code == 200, r.text
    assert during == [set(ids), set(ids)] and in_use.held() == set()


def test_the_ai_chat_holds_its_clip_while_it_works_out_the_edit(lib, app, monkeypatch):
    pytest.importorskip("numpy")   # core.pipeline, which the route reads its model from
    pytest.importorskip("cv2")
    pytest.importorskip("PIL")
    import analysis.clip_edit
    import core.pipeline
    import llm.registry

    clip_id = lib.clip(10.0, 40.0)
    during = []

    def interpret_edit(message, **_kw):
        during.append(in_use.held())
        return {"reply": "Done.", "needs_render": True, "start": 12.0, "end": None, "render_opts": {}}

    monkeypatch.setattr(analysis.clip_edit, "interpret_edit", interpret_edit)
    monkeypatch.setattr(core.pipeline, "_with_usable_model", lambda cfg: cfg)
    monkeypatch.setattr(llm.registry, "create_backend", lambda cfg: object())
    r = app.post(f"/clips/{clip_id}/ai-edit", json={"message": "start two seconds later"})
    assert r.status_code == 200, r.text
    assert during == [{clip_id}] and in_use.held() == set()
    job = lib.db.get_job(r.json()["job_id"])
    assert json.loads(job["payload"])["clip_id"] == clip_id


# ---- every route that reads a clip and then writes for it holds it --------------------
#
# Each route's write is replaced by one that notes what is held at that
# moment: the clip must be held from before the route read it until then,
# so Make clips again can neither delete it nor make it again in between.


def _note_held(during: list, fake=None):
    """A stand-in for a write that notes what is held, then does the real
    write (`real`, given later) or returns `fake`."""
    box: dict = {}

    def write(*args, **kwargs):
        during.append(in_use.held())
        if "real" in box:
            return box["real"](*args, **kwargs)
        return fake

    return write, box


def _hold_seen(monkeypatch, owner, name: str, fake=None, real: bool = True) -> list:
    during: list = []
    write, box = _note_held(during, fake)
    if real:
        box["real"] = getattr(owner, name)
    monkeypatch.setattr(owner, name, write)
    return during


def test_saving_a_new_title_holds_its_clip(lib, app, monkeypatch):
    clip_id = lib.clip(10.0, 40.0)
    during = _hold_seen(monkeypatch, StateDB, "set_clip")
    r = app.patch(f"/clips/{clip_id}", json={"title": "Mine"})
    assert r.status_code == 200, r.text
    assert during == [{clip_id}] and in_use.held() == set()
    assert r.json()["title"] == "Mine" and r.json()["edited_at"]


@pytest.mark.parametrize("route", ["captions", "render"])
def test_a_caption_fix_and_a_render_hold_their_clip_until_the_job_is_queued(lib, app, monkeypatch, route):
    clip_id = lib.clip(10.0, 40.0)
    during = _hold_seen(monkeypatch, StateDB, "add_job")
    if route == "captions":
        r = app.put(f"/clips/{clip_id}/captions", json={"lines": [{"start": 0.0, "end": 1.0, "text": "hi"}]})
    else:
        r = app.post(f"/clips/{clip_id}/render", json={"start": 11.0})
    assert r.status_code == 200, r.text
    assert during == [{clip_id}] and in_use.held() == set()


def test_saving_a_translation_holds_its_clip(lib, app, monkeypatch):
    clip_id = lib.clip(10.0, 40.0)
    during = _hold_seen(monkeypatch, StateDB, "save_translation")
    r = app.put(f"/clips/{clip_id}/translations/es", json={"lines": [{"start": 0.0, "end": 1.0, "text": "hola"}]})
    assert r.status_code == 200, r.text
    assert during == [{clip_id}] and in_use.held() == set()


def test_queueing_a_translation_holds_its_clips_until_the_job_is_queued(lib, app, monkeypatch):
    ids = [lib.clip(10.0, 40.0), lib.clip(50.0, 80.0)]
    during = _hold_seen(monkeypatch, StateDB, "add_job")
    r = app.post("/translate", json={"clip_ids": ids, "languages": ["es"], "stage": "translate"})
    assert r.status_code == 200, r.text
    assert during == [set(ids)] and in_use.held() == set()


def test_a_glossary_ruling_and_a_remembered_layout_hold_their_clip(lib, app, monkeypatch):
    clip_id = lib.clip(10.0, 40.0)
    during = _hold_seen(monkeypatch, StateDB, "set_term")
    r = app.post(f"/clips/{clip_id}/glossary", json={"term": "GG", "rule": "protect"})
    assert r.status_code == 200, r.text
    monkeypatch.setattr(StateDB, "creator_of_video", lambda self, video_id: 1)
    remembered = _hold_seen(monkeypatch, StateDB, "set_creator_gaming_layout", real=False)
    r = app.put(f"/clips/{clip_id}/creator-gaming-layout", json={"layout": None})
    assert r.status_code == 200, r.text
    assert during == [{clip_id}] and remembered == [{clip_id}] and in_use.held() == set()


def test_a_preview_holds_its_clip_while_it_is_made(lib, app, monkeypatch):
    pytest.importorskip("numpy")   # core.pipeline, which the route renders with
    pytest.importorskip("cv2")
    pytest.importorskip("PIL")
    import core.pipeline

    clip_id = lib.clip(10.0, 40.0)
    (lib.data / "downloads").mkdir()
    (lib.data / "downloads" / f"{VID}.mp4").write_bytes(b"source")
    during: list = []

    def render_files(*_a, **_k):
        during.append(in_use.held())
        raise RuntimeError("no FFmpeg here")

    monkeypatch.setattr(core.pipeline, "_render_files", render_files)
    r = app.post(f"/clips/{clip_id}/preview", json={"edit": None})
    assert r.status_code >= 400
    assert during == [{clip_id}] and in_use.held() == set()


def test_making_thumbnail_candidates_holds_its_clip(lib, monkeypatch):
    import video.thumbnail

    clip_id = lib.clip(10.0, 40.0)
    client = _youtube(lib, monkeypatch)
    during = _hold_seen(monkeypatch, video.thumbnail, "generate", fake=[], real=False)
    r = client.post(f"/clips/{clip_id}/thumbnail/generate")
    assert r.status_code == 200, r.text
    assert during == [{clip_id}] and in_use.held() == set()


def test_sending_a_post_to_discord_and_a_campaign_attach_hold_their_clips(lib, app, monkeypatch):
    from campaigns import post_links, service
    from campaigns import queue as campaign_queue

    ids = [lib.clip(10.0, 40.0), lib.clip(50.0, 80.0)]
    monkeypatch.setattr(StateDB, "get_destination", lambda self, i: {"state": "connected"})
    monkeypatch.setattr(service, "load_webhook", lambda data_dir, i: "https://discord.example/webhook")
    during = _hold_seen(monkeypatch, post_links, "send_existing", fake={"sent": True}, real=False)
    r = app.post(f"/discord/clips/{ids[0]}/send", json={"destination_id": 1, "platform": "tiktok",
                                                         "provider": "woopsocial"}, headers=app.session)
    assert r.status_code == 200, r.text
    attached = _hold_seen(monkeypatch, campaign_queue, "attach", real=False,
                          fake={"created": [], "existing": [], "refused": []})
    r = app.post("/campaigns/submissions", json={"items": [{"clip_id": c, "platforms": ["tiktok"]} for c in ids],
                                                  "campaign_ids": [1]}, headers=app.session)
    assert r.status_code == 200, r.text
    assert during == [{ids[0]}] and attached == [set(ids)] and in_use.held() == set()


def test_a_watched_channels_publish_holds_the_videos_clips_from_before_it_reads_them(lib, monkeypatch):
    from server import automation

    ids = [lib.clip(10.0, 40.0), lib.clip(50.0, 80.0)]
    during: list = []

    def publish(self, d, item, watch):
        during.append(in_use.held())
        return {}

    monkeypatch.setattr(automation.ChannelWatcher, "_publish", publish)
    watcher = automation.ChannelWatcher(lambda: StateDB(lib.path), feed=None, worker=None, broadcaster=None)
    assert watcher.publish(lib.db, {"video_id": VID}, {}) == {}
    assert during == [set(ids)] and in_use.held() == set()


# ---- a Make clips again finishing while a person publishes ----------------------------


class _Hold:
    """Keeps an upload going until the run has finished."""

    def __init__(self):
        self.inside, self.release = threading.Event(), threading.Event()

    def __call__(self, *_a):
        self.inside.set()
        assert self.release.wait(20), "the run never finished"


def _race(run, hold, request) -> dict:
    """`request` in a thread of its own, held mid-upload while `run` finishes
    on this one (the worker's own thread and connection)."""
    out: dict = {}
    thread = threading.Thread(target=lambda: out.setdefault("response", request()), daemon=True)
    thread.start()
    assert hold.inside.wait(20), "the request never reached its upload"
    out["finish"] = run.finish()
    hold.release.set()
    thread.join(20)
    return out


def test_a_clip_being_published_when_the_run_finishes_is_kept(lib, monkeypatch):
    clip_id = lib.clip(10.0, 40.0)
    unused = lib.clip(50.0, 80.0)
    run = remake.Run(lib.db, VID, lib.data)
    run.db.add_clip(VID, 500.0, 530.0, 80, "new", path="", status="queued")
    hold = _Hold()
    client = _woopsocial(lib, monkeypatch, hold)
    out = _race(run, hold, lambda: client.post(f"/woopsocial/clips/{clip_id}/publish",
                                               json={"platforms": ["tiktok"], "title": "Mine"}))
    assert out["response"].status_code == 200
    assert out["finish"]["removed"] == [unused] and out["finish"]["kept_because"] == {"busy": 1}
    assert lib.db.get_clip(clip_id) is not None and _published(lib.db, clip_id) == ["tiktok"]


def test_the_clips_a_batch_has_still_to_send_are_kept(lib, monkeypatch):
    ids = [lib.clip(10.0, 40.0), lib.clip(50.0, 80.0), lib.clip(90.0, 120.0)]
    unused = lib.clip(130.0, 160.0)
    run = remake.Run(lib.db, VID, lib.data)
    run.db.add_clip(VID, 500.0, 530.0, 80, "new", path="", status="queued")
    hold = _Hold()
    calls = []

    def first_upload_held(path, platforms):
        calls.append(path)
        if len(calls) == 1:
            hold()

    client = _woopsocial(lib, monkeypatch, first_upload_held)
    out = _race(run, hold, lambda: client.post("/woopsocial/batch",
                                               json={"clip_ids": ids, "platforms": ["tiktok"]}))
    assert out["response"].status_code == 200
    assert [s["clip_id"] for s in out["response"].json()["started"]] == ids
    assert out["finish"]["removed"] == [unused]
    assert all(lib.db.get_clip(c) is not None for c in ids)


def test_the_clips_a_youtube_plan_has_still_to_publish_are_kept(lib, monkeypatch):
    """The run finishes while the plan is on its first clip: the second one,
    with no record yet, is kept because the plan holds it."""
    ids = [lib.clip(10.0, 40.0), lib.clip(50.0, 80.0)]
    unused = lib.clip(130.0, 160.0)
    run = remake.Run(lib.db, VID, lib.data)
    run.db.add_clip(VID, 500.0, 530.0, 80, "new", path="", status="queued")
    hold = _Hold()
    client = _youtube(lib, monkeypatch)
    real = StateDB.add_publish_job

    def add_publish_job(self, clip_id, *args, **kwargs):
        out = real(self, clip_id, *args, **kwargs)
        if clip_id == ids[0]:
            hold()
        return out

    monkeypatch.setattr(StateDB, "add_publish_job", add_publish_job)
    out = _race(run, hold, lambda: client.post("/publish/plan/execute",
                                               json={"items": [{"clip_id": c} for c in ids]}))
    assert out["response"].status_code == 200
    assert [s["clip_id"] for s in out["response"].json()["started"]] == ids
    assert out["finish"]["removed"] == [unused]
    assert all(lib.db.get_clip(c) is not None for c in ids)
