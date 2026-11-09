"""The Make clips again routes (#190): GET and POST /videos/{video_id}/remake.

GET prefills the dialog: the options the video's last 9:16 run used, whether
its source can still be used, and how many of its clips would be replaced
and kept. POST checks the options as POST /jobs does and queues a forced
process job with `replace_untouched`, for the video's own link or file.
Nothing here starts the worker: the app is built without `with`.
"""

import json
import shutil
from pathlib import Path

import pytest

from core.state import StateDB, _now

ROOT = Path(__file__).resolve().parent.parent
VID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VID}"
LOCAL = "local_0123456789ab"


@pytest.fixture
def api(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yaml")
    pytest.importorskip("yt_dlp")  # sources.dispatch imports the YouTube source
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app
    from server.events import broadcaster

    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    app = create_app(config, settings)
    events: list = []
    monkeypatch.setattr(broadcaster, "publish", events.append)
    client = TestClient(app, base_url="http://127.0.0.1")
    db = StateDB(Path(config["paths"]["data_dir"]) / "state.db")
    db.upsert_video(VID, title="A stream")
    db.set_video_source(VID, URL, "youtube")
    db.set_video_status(VID, "done")
    db.set_outcome(VID, {"candidates": 2})
    yield client, db, Path(config["paths"]["data_dir"]), events
    db.close()


def _job(db, payload, status="done", video_id=VID):
    job_id = db.add_job("process", json.dumps(payload), video_id=video_id)
    if status == "running":
        db.set_job(job_id, status="running")
    elif status != "queued":
        db.finish_job(job_id, status)
    return job_id


def _queued(db, job_id):
    return json.loads(db.get_job(job_id)["payload"])


# ---- GET: what the dialog starts from ------------------------------------------


def test_the_last_runs_options_cleaned_to_what_a_person_sets(api):
    client, db, _, _ = api
    job = _job(db, {"url": URL, "force": True, "long_clips": True, "captions": False, "max_clips": 6,
                    "caption_style": {"font": "Arial", "font_size": 80}, "then": {"action": "publish"},
                    "webhook_url": "https://example.com/hook", "webhook_secret": "s", "origin": "watch"})
    out = client.get(f"/videos/{VID}/remake").json()
    assert out["from_job"] == job
    assert out["options"] == {"long_clips": True, "captions": False, "max_clips": 6,
                              "caption_style": {"font": "Arial", "font_size": 80}}
    assert out["source"] == {"kind": "link", "downloaded": False, "link": URL, "usable": True}
    assert out["blocked"] is None and out["queued_job"] is None


def test_with_no_job_left_the_settings_defaults(api):
    client, _, _, _ = api
    out = client.get(f"/videos/{VID}/remake").json()
    assert out["options"] == {} and out["from_job"] is None


def test_how_many_clips_would_be_replaced_and_kept(api):
    client, db, _, _ = api
    unused = db.add_clip(VID, 10.0, 70.0, 70, "h", status="queued")
    edited = db.add_clip(VID, 100.0, 170.0, 70, "h", status="queued")
    db.set_clip(edited, edited_at=_now())
    published = db.add_clip(VID, 200.0, 270.0, 70, "h", status="queued")
    db.record_upload(published, "yt1")
    longform = db.add_clip(VID, 300.0, 420.0, 70, "h", render_opts=json.dumps({"profile": "short_clips"}))
    out = client.get(f"/videos/{VID}/remake").json()
    assert out["counts"] == {"total": 4, "replaced": 1, "kept": 3,
                             "kept_because": {"longform": 1, "edited": 1, "published": 1}}
    assert [(c["id"], c["replace"], c["reasons"]) for c in out["clips"]] == [
        (unused, True, []), (edited, False, ["edited"]), (published, False, ["published"]),
        (longform, False, ["longform"])]


@pytest.mark.parametrize("status", ["queued", "running"])
def test_blocked_while_the_video_waits_or_runs(api, status):
    client, db, _, _ = api
    job = _job(db, {"url": URL}, status=status)
    out = client.get(f"/videos/{VID}/remake").json()
    assert out["blocked"] == status and out["queued_job"] == {"job_id": job, "status": status}


def test_blocked_when_a_files_copy_is_gone(api):
    client, db, data, _ = api
    db.upsert_video(LOCAL, title="My file")
    out = client.get(f"/videos/{LOCAL}/remake").json()
    assert out["blocked"] == "source_gone"
    assert out["source"] == {"kind": "file", "downloaded": False, "link": "", "usable": False}
    (data / "downloads").mkdir(parents=True, exist_ok=True)
    (data / "downloads" / f"{LOCAL}.mp4").write_bytes(b"v")
    assert client.get(f"/videos/{LOCAL}/remake").json()["blocked"] is None


def test_blocked_when_the_queue_is_full(api):
    from core import queue

    client, db, _, _ = api
    for n in range(queue.MAX_ACTIVE):
        _job(db, {"url": f"https://www.youtube.com/watch?v=other{n:06d}"}, status="queued",
             video_id=f"other{n:06d}")
    assert client.get(f"/videos/{VID}/remake").json()["blocked"] == "queue_full"


def test_an_unknown_video_is_404(api):
    client, _, _, _ = api
    assert client.get("/videos/nope/remake").status_code == 404
    assert client.post("/videos/nope/remake", json={}).status_code == 404


# ---- POST: queueing it --------------------------------------------------------------


def test_queued_forced_with_the_flag_for_the_videos_own_link(api):
    client, db, _, events = api
    res = client.post(f"/videos/{VID}/remake", json={"long_clips": False, "max_clips": 99, "captions": True})
    assert res.status_code == 200
    job_id = res.json()["job_id"]
    assert res.json() == {"job_id": job_id, "video_id": VID}
    row = db.get_job(job_id)
    assert (row["type"], row["status"], row["video_id"]) == ("process", "queued", VID)
    # Checked as POST /jobs checks them: max_clips is held to 10.
    assert _queued(db, job_id) == {"url": URL, "force": True, "replace_untouched": True,
                                   "max_clips": 10, "captions": True}
    assert {"type": "queue"} in events


def test_a_file_is_queued_by_its_own_copy(api):
    client, db, data, _ = api
    db.upsert_video(LOCAL, title="My file")
    db.set_video_source(LOCAL, URL, "youtube")       # its original, which is another video
    (data / "downloads").mkdir(parents=True, exist_ok=True)
    (data / "downloads" / f"{LOCAL}.mp4").write_bytes(b"v")
    job_id = client.post(f"/videos/{LOCAL}/remake", json={}).json()["job_id"]
    assert _queued(db, job_id) == {"url": f"local:{LOCAL}", "force": True, "replace_untouched": True}


def test_a_file_whose_copy_is_gone_says_to_add_it_again(api):
    client, db, _, _ = api
    db.upsert_video(LOCAL, title="My file")
    res = client.post(f"/videos/{LOCAL}/remake", json={})
    assert res.status_code == 400 and "Add the file again" in res.json()["detail"]
    assert db.list_jobs() == []


def test_a_linked_video_with_neither_link_nor_download_says_to_add_it_again(api):
    client, db, _, _ = api
    db.upsert_video("zzzzzzzzzzz", title="Gone")
    res = client.post("/videos/zzzzzzzzzzz/remake", json={})
    assert res.status_code == 400 and "Add the video again" in res.json()["detail"]


@pytest.mark.parametrize("status", ["queued", "running"])
def test_409_while_the_video_waits_or_runs(api, status):
    client, db, _, _ = api
    _job(db, {"url": URL}, status=status)
    res = client.post(f"/videos/{VID}/remake", json={})
    assert res.status_code == 409 and "already waiting or being made" in res.json()["detail"]
    assert len(db.list_jobs()) == 1


def test_409_when_the_queue_is_full(api):
    from core import queue

    client, db, _, _ = api
    for n in range(queue.MAX_ACTIVE):
        _job(db, {"url": "u"}, status="queued", video_id=f"other{n:06d}")
    res = client.post(f"/videos/{VID}/remake", json={})
    assert res.status_code == 409 and "queue is full" in res.json()["detail"]


@pytest.mark.parametrize("body, said", [
    ({"filter": "no-such-filter"}, "unknown filter"),
    ({"whole": True, "long_clips": True}, "Whole clips can't be combined"),
    ({"vertical_live": True, "podcast": True}, "Vertical Live can't be combined"),
    ({"longform": {"mode": "short_clips", "shorts": True}}, "Longform can't be on"),
])
def test_options_are_checked_as_post_jobs_checks_them(api, body, said):
    client, db, _, _ = api
    res = client.post(f"/videos/{VID}/remake", json=body)
    assert res.status_code == 400 and said in res.json()["detail"]
    assert db.list_jobs() == []


def test_what_get_prefills_posts_back_as_it_was(api):
    client, db, _, _ = api
    options = {"captions": True, "caption_style": {"font": "Arial", "font_size": 90}, "min_score": 40,
               "focus": "the funny bits", "gaming": True,
               "gaming_layout": {"by": "user", "cam": [0.7, 0.0, 0.3, 0.3], "preset": "split"},
               "hashtags": ["#clips"], "long_clips": True, "max_clips": 5}
    job_res = client.post("/jobs", json={"url": URL, "force": True, **options})
    db.finish_job(job_res.json()["job_id"], "done")
    prefill = client.get(f"/videos/{VID}/remake").json()["options"]
    job_id = client.post(f"/videos/{VID}/remake", json=prefill).json()["job_id"]
    first = _queued(db, job_res.json()["job_id"])
    again = _queued(db, job_id)
    assert again == {**first, "replace_untouched": True}


def test_a_cross_site_form_post_is_refused(api):
    """The body is required JSON: a page elsewhere can post a form or plain
    text to 127.0.0.1 without asking, and must not start one."""
    client, db, _, _ = api
    res = client.post(f"/videos/{VID}/remake", content="x=1",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert res.status_code == 422
    assert client.post(f"/videos/{VID}/remake").status_code == 422
    assert db.list_jobs() == []


def test_a_body_that_doesnt_say_it_is_json_is_refused(api):
    """A page elsewhere can send a body that names no type, or text/plain,
    without the browser asking first. FastAPI (strict about the content type
    since the version requirements.txt asks for) reads neither as JSON, so
    the body is missing: 422, as for POST /jobs. The app's own calls say
    application/json, which a page elsewhere can't send unasked."""
    client, db, _, _ = api
    res = client.post(f"/videos/{VID}/remake", content=b"{}")
    assert "content-type" not in {k.lower() for k in res.request.headers}
    assert res.status_code == 422
    res = client.post(f"/videos/{VID}/remake", content=b"{}", headers={"Content-Type": "text/plain"})
    assert res.status_code == 422
    assert db.list_jobs() == []
    res = client.post(f"/videos/{VID}/remake", content=b"{}",
                      headers={"Content-Type": "application/json; charset=utf-8"})
    assert res.status_code == 200


def test_a_waiting_remake_keeps_its_flag_through_the_queue_settings(api):
    """The Queue page's settings editor can change its options while it waits;
    it stays a Make clips again unless the flag is cleared on purpose."""
    client, db, _, _ = api
    job_id = client.post(f"/videos/{VID}/remake", json={"long_clips": True}).json()["job_id"]
    settings = next(j for j in client.get("/queue").json()["queued"] if j["id"] == job_id)["settings"]
    assert settings == {"force": True, "replace_untouched": True, "long_clips": True}
    assert client.patch(f"/jobs/{job_id}", json={"captions": False, "clear": ["long_clips"]}).status_code == 200
    assert _queued(db, job_id) == {"url": URL, "force": True, "replace_untouched": True, "captions": False}
    client.patch(f"/jobs/{job_id}", json={"clear": ["replace_untouched"]})
    assert _queued(db, job_id) == {"url": URL, "force": True, "captions": False}


def test_post_jobs_forced_is_process_again_as_before(api):
    client, db, _, _ = api
    assert client.post("/jobs", json={"url": URL}).json()["already_processed"] is True
    job_id = client.post("/jobs", json={"url": URL, "force": True}).json()["job_id"]
    assert _queued(db, job_id) == {"url": URL, "force": True}


def test_only_make_clips_again_queues_a_job_that_replaces_clips(api):
    """POST /jobs, POST /jobs/batch, POST /videos/local and PATCH /jobs take
    no `replace_untouched`, and a job queued by itself (a watch's, a
    streaming tool's) has it dropped. PATCH keeps a waiting Make clips
    again's own flag (test_a_waiting_remake_keeps_its_flag_through_the_queue_settings)."""
    from core import queue
    from server.api import BatchItemIn, JobIn, JobPatch, LocalVideoIn

    client, db, _, _ = api
    job_id = client.post("/jobs", json={"url": URL, "force": True, "replace_untouched": True}).json()["job_id"]
    assert _queued(db, job_id) == {"url": URL, "force": True}
    assert client.patch(f"/jobs/{job_id}", json={"replace_untouched": True, "captions": False}).status_code == 200
    assert _queued(db, job_id) == {"url": URL, "force": True, "captions": False}
    other = "https://www.youtube.com/watch?v=zzzzzzzzzzz"
    out = client.post("/jobs/batch", json={"items": [{"url": other, "force": True, "replace_untouched": True}]})
    assert _queued(db, out.json()["created"][0]["job_id"]) == {"url": other, "force": True}
    for model in (JobIn, BatchItemIn, LocalVideoIn, JobPatch):     # extra fields are ignored
        assert "replace_untouched" not in model.model_fields
        assert model.model_config.get("extra") in (None, "ignore"), model.__name__
    outcome, by_itself = queue.enqueue_once(db, "yyyyyyyyyyy", {"url": "local:yyyyyyyyyyy", "origin": "stream",
                                                                 "replace_untouched": True})
    assert outcome == "queued" and _queued(db, by_itself) == {"url": "local:yyyyyyyyyyy", "origin": "stream"}


# ---- the options are JobIn's ------------------------------------------------------


def test_the_carried_options_are_jobins_less_link_force_webhook_then_and_longform():
    pytest.importorskip("fastapi")
    from core.remake import OPTIONS
    from server.api import JobIn, RemakeIn

    left_out = {"url", "force", "webhook_url", "webhook_secret", "then", "longform"}
    assert set(OPTIONS) == set(JobIn.model_fields) - left_out
    assert len(set(OPTIONS)) == len(OPTIONS)
    assert set(RemakeIn.model_fields) == set(OPTIONS) | {"longform"}
    for name in OPTIONS:
        assert RemakeIn.model_fields[name].annotation == JobIn.model_fields[name].annotation, name


# ---- a person's words on a clip ------------------------------------------------


def test_changing_a_clips_words_marks_it_and_saving_the_same_doesnt(api):
    client, db, _, _ = api
    clip_id = db.add_clip(VID, 10.0, 70.0, 70, "h", title="Old", description="d",
                          hashtags=json.dumps(["#a"]))
    for same in ({"title": "Old"}, {"description": "d"}, {"hashtags": ["#a"]}, {"exported": True}):
        assert client.patch(f"/clips/{clip_id}", json=same).json()["edited_at"] == ""
    for changed in ({"title": "New"}, {"description": "e"}, {"hashtags": ["#b"]}):
        db.set_clip(clip_id, edited_at="")
        assert client.patch(f"/clips/{clip_id}", json=changed).json()["edited_at"]


def test_saving_the_same_words_keeps_a_clip_nobody_used_and_clip_json_has_no_fingerprint(api):
    """The fingerprint of what the run made (core/state.py as_made) is Make
    clips again's own: clip JSON leaves it out. The same words saved again
    through PATCH keep the clip one nobody used; new ones don't."""
    from core import remake

    client, db, _, _ = api
    clip_id = db.add_clip(VID, 10.0, 70.0, 70, "h", title="Old", description="d", hashtags=json.dumps(["#a"]))
    got = client.patch(f"/clips/{clip_id}", json={"title": "Old", "hashtags": ["#a"]}).json()
    assert "as_made" not in got and db.get_clip(clip_id)["as_made"]
    assert "as_made" not in client.get(f"/videos/{VID}/clips").json()[0]
    assert remake.classify(db, VID)[0]["replace"] is True
    db.set_clip(clip_id, edited_at="")           # only the fingerprint tells now
    client.patch(f"/clips/{clip_id}", json={"description": "My words"})
    db.set_clip(clip_id, edited_at="")
    assert remake.classify(db, VID)[0]["reasons"] == ["edited"]


# ---- deleting a clip that shares its file ------------------------------------------


def test_deleting_a_clip_leaves_the_file_another_clip_still_plays(api):
    """A run names a clip's file by its whole seconds, so 10.4-20.6 s was
    written over the 10.0-20.0 s clip's file and both rows point at it. The
    Library's Delete on one leaves the other its file; deleting the last one
    takes it."""
    client, db, data, _ = api
    folder = data / "clips" / "someone" / f"A stream [{VID}]"
    folder.mkdir(parents=True)
    shared = folder / "clip_00010-00020.mp4"
    shared.write_bytes(b"x" * 100)
    first = db.add_clip(VID, 10.0, 20.0, 70, "h", path=str(shared), status="queued")
    second = db.add_clip(VID, 10.4, 20.6, 70, "h", path=str(shared), status="queued")
    res = client.delete(f"/clips/{second}")
    assert res.status_code == 200 and res.json() == {"deleted": second, "bytes_freed": 0}
    assert db.get_clip(first) is not None and shared.read_bytes() == b"x" * 100
    res = client.delete(f"/clips/{first}")
    assert res.json() == {"deleted": first, "bytes_freed": 100} and not shared.exists()


# ---- while a Make clips again run works (QC fix round 3) ----------------------------


def _held_back_render(db, data):
    """A run that found an unused clip's moment again: its render is written,
    and no row points at it until the run is over. Plus a new clip with the
    same whole seconds, which shares that file."""
    from core import remake

    folder = data / "clips" / "someone" / f"A stream [{VID}]"
    folder.mkdir(parents=True)
    first = folder / "clip_00010-00070.mp4"
    first.write_bytes(b"own" * 10)
    unused = db.add_clip(VID, 10.2, 70.3, 70, "h", path=str(first), status="queued")
    run = remake.Run(db, VID, data)
    first.write_bytes(b"render" * 10)                       # the run's render of the same moment
    assert run.db.add_clip(VID, 10.2, 70.3, 90, "h", path=str(first), status="queued") is None
    run.db.set_clip(unused, path=str(first), scores="{}")   # held back
    new = run.db.add_clip(VID, 10.4, 70.6, 90, "h", path=str(first), status="queued")
    return run, unused, new, first


def test_deleting_a_clip_leaves_the_file_a_make_clips_again_run_holds(api):
    client, db, data, _ = api
    run, unused, new, render = _held_back_render(db, data)
    res = client.delete(f"/clips/{new}")
    assert res.status_code == 200 and res.json() == {"deleted": new, "bytes_freed": 0}
    assert render.read_bytes() == b"render" * 10
    run.finish()
    assert db.get_clip(unused)["path"] == str(render) and render.is_file()


def test_storage_clean_up_leaves_the_files_a_make_clips_again_run_holds(api):
    client, db, data, _ = api
    run, unused, new, render = _held_back_render(db, data)
    db.delete_clip(new)                                     # so no row points at the render
    stray = render.with_name("clip_00500-00530.mp4")
    stray.write_bytes(b"nobody's")
    shown = client.get("/storage").json()["reclaimable"]["orphan_clips"]
    assert shown["files"] == 1
    assert client.post("/storage/cleanup").status_code == 200
    assert render.is_file() and not stray.exists()
    run.finish()
    assert db.get_clip(unused)["path"] == str(render) and render.read_bytes() == b"render" * 10


# ---- not while one of its clips is being published or exported ---------------------


BUSY = "Clips Kitty is working with one of this video's clips right now"


def _busy(client, db, monkeypatch, clip_id, how):
    from core import in_use

    if how == "a publish waiting":
        db.add_publish_job(clip_id, "{}", video_id=VID, start_s=10.0, end_s=40.0)
        return None
    if how == "an export on the lane":
        monkeypatch.setattr(client.app.state.editor_lane, "jobs",
                            lambda: [{"clip_id": clip_id, "status": "running"}])
        return None
    hold = in_use.holding([clip_id])                        # a request working with it
    hold.__enter__()
    return hold


@pytest.mark.parametrize("how", ["a publish waiting", "an export on the lane", "a request holding it"])
def test_off_and_refused_while_one_of_its_clips_is_in_use(api, monkeypatch, how):
    client, db, _, _ = api
    clip_id = db.add_clip(VID, 10.0, 40.0, 70, "h", status="queued")
    hold = _busy(client, db, monkeypatch, clip_id, how)
    try:
        assert client.get(f"/videos/{VID}/remake").json()["blocked"] == "busy"
        res = client.post(f"/videos/{VID}/remake", json={})
        assert res.status_code == 409 and res.json()["detail"].startswith(BUSY)
        assert db.list_jobs() == []
    finally:
        if hold is not None:
            hold.__exit__(None, None, None)
    if how == "a request holding it":
        assert client.get(f"/videos/{VID}/remake").json()["blocked"] is None
        assert client.post(f"/videos/{VID}/remake", json={}).status_code == 200


def test_another_videos_clip_in_use_doesnt_turn_it_off(api, monkeypatch):
    client, db, _, _ = api
    db.upsert_video("zzzzzzzzzzz", title="Another")
    theirs = db.add_clip("zzzzzzzzzzz", 10.0, 40.0, 70, "h", status="queued")
    db.add_publish_job(theirs, "{}", video_id="zzzzzzzzzzz", start_s=10.0, end_s=40.0)
    db.add_clip(VID, 10.0, 40.0, 70, "h", status="queued")      # the same window, of this video
    assert client.get(f"/videos/{VID}/remake").json()["blocked"] is None


# ---- a clip's video, as it is now ---------------------------------------------------


def test_clip_json_carries_a_media_version_that_changes_with_its_file(api):
    """A clip made again in place keeps its id and its file name: the app
    puts media_version on its /media URL so a player loads the new file."""
    client, db, data, _ = api
    folder = data / "clips"
    folder.mkdir(parents=True)
    path = folder / "clip_00010-00040.mp4"
    path.write_bytes(b"first")
    clip_id = db.add_clip(VID, 10.0, 40.0, 70, "h", path=str(path), status="queued")
    db.add_clip(VID, 50.0, 80.0, 70, "h", path=str(folder / "missing.mp4"), status="queued")
    clips = {c["id"]: c for c in client.get(f"/videos/{VID}/clips").json()}
    first = clips[clip_id]["media_version"]
    assert first and [c["media_version"] for c in clips.values() if c["id"] != clip_id] == [""]
    path.write_bytes(b"the new render, longer")
    again = {c["id"]: c for c in client.get(f"/videos/{VID}/clips").json()}[clip_id]["media_version"]
    assert again and again != first
    assert client.patch(f"/clips/{clip_id}", json={"title": "Mine"}).json()["media_version"] == again
    res = client.get(f"/media/{clip_id}?v={again}")
    assert res.status_code == 200 and res.content == b"the new render, longer"
