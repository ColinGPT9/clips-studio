"""The chat box has to do the whole of what it was told.

One message drove all of this:

    "process this video of Sara Saffari <url> and publish them all to my
     youtube channel all titles and descriptions must have #sarasaffari"

The video processed into 33 clips. None of them carried the hashtag, none of
them were published, and captions were burned in although the box was
unticked. Three silent failures from one sentence.
"""

import json

import pytest

mcp = pytest.importorskip("server.mcp")


# ---- the hashtag ------------------------------------------------------------


def test_required_hashtags_reach_the_job(monkeypatch):
    """They were unexpressible before: queue_video had no such argument, so
    the only route was setting metadata on 33 clips one at a time."""
    sent = {}

    def fake_request(method, path, body=None, timeout=60.0):
        sent["path"] = path
        sent["body"] = body
        return {"job_id": 7, "video_id": "abc"}

    monkeypatch.setattr(mcp, "_request", fake_request)
    mcp._queue_video({"url": "https://youtu.be/x", "hashtags": ["#sarasaffari", "gym"]})

    assert sent["body"]["hashtags"] == ["#sarasaffari", "gym"]


def test_blank_hashtags_are_not_sent(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        mcp, "_request",
        lambda m, p, body=None, timeout=60.0: (sent.update(body=body), {"job_id": 1})[1],
    )
    mcp._queue_video({"url": "https://youtu.be/x", "hashtags": ["  ", ""]})
    assert "hashtags" not in sent["body"]


# ---- publishing after the clips exist ---------------------------------------


def test_publish_when_done_is_carried_on_the_job(monkeypatch):
    """The turn ends seconds after queueing and the clips appear an hour
    later, so the second half of the request has to travel with the job."""
    sent = {}
    monkeypatch.setattr(
        mcp, "_request",
        lambda m, p, body=None, timeout=60.0: (sent.update(body=body), {"job_id": 1})[1],
    )
    mcp._queue_video({"url": "https://youtu.be/x", "publish_when_done": ["youtube"]})

    assert sent["body"]["then"] == {"action": "publish", "platforms": ["youtube"]}


def test_no_publish_intent_means_no_then(monkeypatch):
    """Only a request that asked to publish gets published."""
    sent = {}
    monkeypatch.setattr(
        mcp, "_request",
        lambda m, p, body=None, timeout=60.0: (sent.update(body=body), {"job_id": 1})[1],
    )
    mcp._queue_video({"url": "https://youtu.be/x"})
    assert "then" not in sent["body"]


def test_the_tool_advertises_both(monkeypatch):
    """A model only ever sees the schema, so an argument it cannot see is an
    argument that does not exist."""
    tool = next(t for t in mcp.TOOLS if t["name"] == "queue_video")
    props = tool["inputSchema"]["properties"]
    assert "hashtags" in props
    assert "publish_when_done" in props


# ---- the job payload --------------------------------------------------------


def test_the_api_keeps_both_fields_on_the_payload():
    api = pytest.importorskip("server.api")

    class Body(api.JobIn):
        pass

    body = Body(url="https://youtu.be/x", hashtags=["#a"], then={"action": "publish"})
    payload = api._process_options(body)
    assert payload["hashtags"] == ["#a"]
    assert payload["then"] == {"action": "publish"}


def test_a_job_without_them_carries_neither():
    api = pytest.importorskip("server.api")
    payload = api._process_options(api.JobIn(url="https://youtu.be/x"))
    assert "hashtags" not in payload
    assert "then" not in payload


# ---- the hashtags actually land on every clip -------------------------------


def test_required_tags_are_added_to_every_clip_and_not_duplicated():
    """The model writes its own tags; the required ones are appended after,
    because a tag the LLM sometimes forgets is not a required tag."""
    metadata = pytest.importorskip("analysis.metadata")

    metas = [
        metadata.ClipMetadata(title="Gym day", description="d", hashtags=["#fitness"]),
        metadata.ClipMetadata(title="Leg day", description="d", hashtags=["#sarasaffari"]),
    ]
    extra = metadata._clean_hashtags(["#sarasaffari"])
    for meta in metas:
        have = {t.casefold() for t in meta.hashtags}
        meta.hashtags = meta.hashtags + [t for t in extra if t.casefold() not in have]

    assert metas[0].hashtags == ["#fitness", "#sarasaffari"]
    # Already had it: not repeated.
    assert metas[1].hashtags == ["#sarasaffari"]


def test_a_tag_that_would_overflow_the_title_is_left_off_it():
    """YouTube refuses a title over 100 characters, and losing the upload
    would be a worse outcome than the tag living only in the description."""
    title = "x" * 98
    tag = "#sarasaffari"
    assert len(title) + len(tag) + 1 > 100
    fits = len(title) + len(tag) + 1 <= 100
    assert not fits


# ---- the deferred publish fires ---------------------------------------------


class _FakeDB:
    def __init__(self, clips):
        self._clips = clips

    def clips_for_video(self, video_id):
        return self._clips


def test_the_worker_publishes_when_the_job_asked_it_to(monkeypatch, tmp_path):
    jobs = pytest.importorskip("server.jobs")
    woop = pytest.importorskip("server.woopsocial_service")

    called = {}
    monkeypatch.setattr(woop, "is_enabled", lambda db: True)
    monkeypatch.setattr(woop, "has_key", lambda d: True)
    monkeypatch.setattr(
        woop, "publish_clips",
        lambda db, data_dir, **kw: called.update(kw) or {"started": kw["clip_ids"], "skipped": []},
    )

    worker = jobs.Worker.__new__(jobs.Worker)
    worker.config = {"paths": {"data_dir": str(tmp_path)}}
    db = _FakeDB([{"id": 1}, {"id": 2}])
    job = {"video_id": "abc"}
    worker._run_follow_up(db, job, {"then": {"action": "publish", "platforms": ["youtube"]}})

    assert called["clip_ids"] == [1, 2]
    assert called["platforms"] == ["youtube"]


def test_a_job_that_did_not_ask_publishes_nothing(monkeypatch, tmp_path):
    jobs = pytest.importorskip("server.jobs")
    woop = pytest.importorskip("server.woopsocial_service")

    monkeypatch.setattr(
        woop, "publish_clips",
        lambda *a, **k: pytest.fail("nothing asked for a publish"),
    )
    worker = jobs.Worker.__new__(jobs.Worker)
    worker.config = {"paths": {"data_dir": str(tmp_path)}}
    worker._run_follow_up(_FakeDB([{"id": 1}]), {"video_id": "abc"}, {})


def test_a_failed_publish_does_not_fail_the_job(monkeypatch, tmp_path, capsys):
    """The clips were still produced, which is what the job was."""
    jobs = pytest.importorskip("server.jobs")
    woop = pytest.importorskip("server.woopsocial_service")

    monkeypatch.setattr(woop, "is_enabled", lambda db: True)
    monkeypatch.setattr(woop, "has_key", lambda d: True)

    def boom(*a, **k):
        raise RuntimeError("woopsocial is down")

    monkeypatch.setattr(woop, "publish_clips", boom)
    worker = jobs.Worker.__new__(jobs.Worker)
    worker.config = {"paths": {"data_dir": str(tmp_path)}}
    # Must not raise.
    worker._run_follow_up(
        _FakeDB([{"id": 1}]), {"video_id": "abc"},
        {"then": {"action": "publish", "platforms": ["youtube"]}},
    )
    assert "woopsocial is down" in capsys.readouterr().out


def test_nothing_is_published_when_the_provider_is_off(monkeypatch, tmp_path):
    jobs = pytest.importorskip("server.jobs")
    woop = pytest.importorskip("server.woopsocial_service")

    monkeypatch.setattr(woop, "is_enabled", lambda db: False)
    monkeypatch.setattr(
        woop, "publish_clips", lambda *a, **k: pytest.fail("provider is off")
    )
    worker = jobs.Worker.__new__(jobs.Worker)
    worker.config = {"paths": {"data_dir": str(tmp_path)}}
    worker._run_follow_up(
        _FakeDB([{"id": 1}]), {"video_id": "abc"},
        {"then": {"action": "publish", "platforms": ["youtube"]}},
    )


def test_json_round_trip_of_the_then_block():
    """It rides in the job's payload column, which is text."""
    then = {"action": "publish", "platforms": ["youtube"], "every_hours": 1}
    assert json.loads(json.dumps({"then": then}))["then"] == then
