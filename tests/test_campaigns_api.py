"""The /campaigns routes (server/campaigns_api.py): the session header on
every route that changes something, the submissions routes declared before
/campaigns/{campaign_id}, campaign CRUD with the rules' own messages, the
preview with a draft caption and a scheduled time, attaching, every action
on a submission, and the Submission and Campaign shapes.

No network and no publisher: rows are written the way the providers write
them (tests/campaign_fake.py).
"""

import json

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from campaign_fake import FakePublisher, clip, link, webhook_destination  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from campaigns import statemachine  # noqa: E402
from core.state import StateDB  # noqa: E402
from server import campaigns_api  # noqa: E402

SECRET = "test-session-secret-0123456789"
HEADERS = {"X-Clips-Kitty-Session": SECRET}


class Worker:
    """The publish worker as the routes see it: asked to run its step."""

    def __init__(self):
        self.nudged = 0

    def notify_campaigns(self):
        self.nudged += 1


@pytest.fixture
def client(tmp_path):
    app = fastapi.FastAPI()
    db_path = tmp_path / "state.db"
    StateDB(db_path).close()
    worker = Worker()
    campaigns_api.install(app, db=lambda: StateDB(db_path), data_dir=tmp_path, publish_worker=worker,
                          session_secret=SECRET)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.worker, c.data_dir = worker, tmp_path
    c.open_db = lambda: StateDB(db_path)
    return c


@pytest.fixture
def db(client):
    d = client.open_db()
    yield d
    d.close()


def _campaign(client, **fields) -> dict:
    body = {"name": "Example Campaign", "channel_link": "https://discord.com/channels/300000000000000001/"
                                                        "200000000000000002", **fields}
    r = client.post("/campaigns", json=body, headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()["campaign"]


def _attach(client, clip_id, campaign_ids, platforms=("tiktok",), **body):
    r = client.post("/campaigns/submissions", headers=HEADERS, json={
        "items": [{"clip_id": clip_id, "platforms": list(platforms)}], "campaign_ids": campaign_ids, **body})
    assert r.status_code == 200, r.text
    return r.json()


# ---- the session header and the route order -----------------------------------------------------

MUTATING = {
    ("POST", "/campaigns"), ("PATCH", "/campaigns/{campaign_id}"), ("DELETE", "/campaigns/{campaign_id}"),
    ("POST", "/campaigns/submissions"), ("PATCH", "/campaigns/submissions/{submission_id}"),
    *(("POST", f"/campaigns/submissions/{{submission_id}}/{action}")
      for action in ("move", "approve", "submit", "retry", "opened", "record", "cancel")),
}


def test_every_route_that_changes_something_needs_the_session_header(client):
    from server import api_stability as st

    routes = {(m, p) for m, p, _mod in st.app_routes(client.app) if p.startswith("/campaigns")}
    changing = {(m, p) for m, p in routes if m in ("POST", "PATCH", "PUT", "DELETE")}
    assert changing - MUTATING == {("POST", "/campaigns/preview")}
    assert MUTATING <= changing
    for method, path in sorted(MUTATING):
        concrete = path.replace("{campaign_id}", "1").replace("{submission_id}", "1")
        for headers in ({}, {"X-Clips-Kitty-Session": "wrong"}):
            r = client.request(method, concrete, json={}, headers=headers)
            assert r.status_code == 403, (method, path, r.text)
    assert client.worker.nudged == 0
    assert all(st.label(m, p) == st.INTERNAL for m, p in routes)


def test_the_submissions_routes_come_before_the_campaign_id_route(client):
    paths = [getattr(r, "path", "") for r in client.app.routes]
    assert paths.index("/campaigns/submissions") < paths.index("/campaigns/{campaign_id}")
    r = client.get("/campaigns/submissions")
    assert r.status_code == 200, "not taken for a campaign id (422)"
    assert r.json() == {"submissions": [], "counts": {}, "to_submit": 0, "outcome_to_record": 0}


# ---- campaigns ------------------------------------------------------------------------------------

def test_campaigns_can_be_made_read_changed_and_removed(client, db):
    made = _campaign(client, platforms=["tiktok"], required_hashtags=["#clips"], review_first=True)
    assert (made["guild_id"], made["channel_id"], made["platforms"], made["required_hashtags"]) == (
        "300000000000000001", "200000000000000002", ["tiktok"], ["clips"])
    assert (made["review_first"], made["enabled"], made["destination"], made["counts"]) == (True, True, None, {})

    assert client.get(f"/campaigns/{made['id']}").json()["name"] == "Example Campaign"
    assert [c["id"] for c in client.get("/campaigns").json()["campaigns"]] == [made["id"]]
    r = client.patch(f"/campaigns/{made['id']}", json={"name": "Renamed", "max_seconds": 60}, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert (r.json()["campaign"]["name"], r.json()["campaign"]["max_seconds"], r.json()["submissions_updated"]) == (
        "Renamed", 60.0, 0)

    removed = client.delete(f"/campaigns/{made['id']}", headers=HEADERS)
    assert removed.json() == {"result": "deleted"}
    assert client.get(f"/campaigns/{made['id']}").status_code == 404
    again = client.delete(f"/campaigns/{made['id']}", headers=HEADERS)
    assert again.status_code == 404


def test_a_campaign_with_submissions_is_archived_not_deleted(client, db):
    made = _campaign(client)
    (row,) = _attach(client, clip(db), [made["id"]])["created"]
    removed = client.delete(f"/campaigns/{made['id']}", headers=HEADERS)
    assert removed.json() == {"result": "archived"}
    assert client.get("/campaigns").json()["campaigns"] == []
    archived = client.get("/campaigns", params={"archived": True}).json()["campaigns"]
    assert [(c["id"], c["archived"], c["counts"]) for c in archived] == [(made["id"], True, {"cancelled": 1})]
    after = client.get(f"/campaigns/submissions/{row['id']}").json()
    assert (after["status"], after["status_reason"]) == ("cancelled", "The campaign was archived")


@pytest.mark.parametrize("body, message", [
    ({"name": ""}, "Give the campaign a name"),
    ({"name": "X", "method": "discord_webhook"}, "Pick the webhook"),
    ({"name": "X", "method": "website", "website_url": "http://example.com"}, "https://"),
    ({"name": "X", "channel_link": "https://discord.gg/example"}, "invite link"),
    ({"name": "X", "method": "discord_webhook", "destination_id": 77}, "doesn't exist"),
    ({"name": "X", "platforms": ["myspace"]}, "can't submit"),
])
def test_a_campaign_that_breaks_a_rule_is_refused_with_the_rule(client, body, message):
    r = client.post("/campaigns", json=body, headers=HEADERS)
    assert r.status_code == 400, r.text
    assert message in r.json()["detail"]


def test_an_unknown_field_is_refused(client):
    assert client.post("/campaigns", json={"name": "X", "colour": "red"}, headers=HEADERS).status_code == 422


def test_the_preview_shows_the_words_and_the_early_checks(client, db):
    clip_id = clip(db, hashtags=json.dumps(["gaming"]))
    draft = {"name": "Draft", "command_name": "/submit", "command_args": [{"name": "link", "value": ""},
                                                                          {"name": "platform", "value": "{platform}"}],
             "url_argument": "link", "message_template": "{title} {url}", "required_hashtags": ["clips"],
             "deadline": "2026-01-01T00:00:00Z", "max_seconds": 20}
    r = client.post("/campaigns/preview", json={
        "campaign": draft, "clip_id": clip_id, "platform": "tiktok", "url": link("tiktok") + "?lang=en",
        "caption": "no tags yet", "scheduled_for": "2026-02-01T00:00:00Z"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["message"] == f"A great moment {link('tiktok')}"
    assert got["command"]["line"] == f"/submit link:{link('tiktok')} platform:TikTok"
    checks = {c["name"]: c["result"] for c in got["checks"]}
    assert checks == {"platform": "pass", "deadline": "fail", "duration": "fail", "hashtags": "fail"}
    assert (got["length"], got["problems"]) == (len(got["message"]), [])

    stored = _campaign(client, message_template="{url}")
    only_id = client.post("/campaigns/preview", json={"campaign_id": stored["id"]}).json()
    assert (only_id["checks"], only_id["message"]) == ([], "https://www.tiktok.com/@example/video/7000000000000000000")
    assert client.post("/campaigns/preview", json={}).status_code == 400


# ---- attaching and the actions ----------------------------------------------------------------------

def test_attaching_answers_what_was_made_kept_and_refused(client, db):
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], state="published", url=link("tiktok"))
    open_one = _campaign(client, command_name="submit", command_args=[{"name": "link", "value": ""}],
                         url_argument="link")
    youtube_only = _campaign(client, name="YouTube only", platforms=["youtube"])
    got = _attach(client, clip_id, [open_one["id"], youtube_only["id"]], mode="now", provider="woopsocial")
    (row,) = got["created"]
    assert (row["status"], row["status_label"], row["to_submit"], row["campaign_name"]) == (
        "manual", "Manual action required", True, "Example Campaign")
    assert row["assisted_text"] == f"/submit link:{link('tiktok')}"
    assert row["command"] == {"name": "submit", "line": f"/submit link:{link('tiktok')}",
                              "args": [{"name": "link", "value": link("tiktok")}], "url_arg": "link"}
    assert row["discord_url"] == "https://discord.com/channels/300000000000000001/200000000000000002"
    assert got["refused"] == [{"campaign_id": youtube_only["id"], "clip_id": clip_id, "platform": "tiktok",
                               "reason": "This campaign doesn't take TikTok posts"}]
    assert client.worker.nudged == 1

    again = _attach(client, clip_id, [open_one["id"]])
    assert ([r["id"] for r in again["existing"]], again["created"]) == ([row["id"]], [])
    assert client.get(f"/campaigns/submissions/{row['id']}").json()["id"] == row["id"]
    assert client.get("/campaigns/submissions/999").status_code == 404
    bad = client.post("/campaigns/submissions", headers=HEADERS, json={
        "items": [{"clip_id": clip_id, "platforms": ["tiktok"]}], "campaign_ids": [open_one["id"]],
        "post_url": "https://example.com/1"})
    assert bad.status_code == 400 and "platform" in bad.json()["detail"]


def test_the_submission_shape_lists_every_field(client, db):
    clip_id = clip(db)
    made = _campaign(client)
    (row,) = _attach(client, clip_id, [made["id"]], post_url=link("tiktok"))["created"]
    assert set(row) == {
        "id", "campaign_id", "campaign_name", "clip_id", "clip_title", "video_id", "start_s", "end_s", "platform",
        "provider", "method", "overridden", "status", "status_label", "status_reason", "to_submit",
        "outcome_to_record", "uncertain", "hold", "review_first", "due_at", "post_url", "url_source", "account",
        "checks", "message_text", "arg_values", "extra_values", "assisted_text", "command", "discord_url",
        "message_url", "website_url", "deadline", "attempts", "next_attempt_at", "opened_at", "sent_at", "sent_by",
        "confirmed_at", "confirmation_note", "error", "created_at", "updated_at",
    }
    assert (row["account"], row["url_source"], row["provider"]) == ("@example", "manual", "manual")


def test_the_by_hand_flow_from_opening_discord_to_the_campaigns_answer(client, db):
    made = _campaign(client)
    (row,) = _attach(client, clip(db), [made["id"]], post_url=link("tiktok"))["created"]
    path = f"/campaigns/submissions/{row['id']}"
    assert client.post(f"{path}/opened", headers=HEADERS).json()["opened_at"]
    message = "https://discord.com/channels/300000000000000001/200000000000000002/600000000000000007"
    done = client.post(f"{path}/record", headers=HEADERS, json={"outcome": "submitted", "message_link": message})
    assert done.status_code == 200, done.text
    assert (done.json()["status"], done.json()["sent_by"], done.json()["outcome_to_record"]) == (
        "awaiting_confirmation", "user", True)
    assert done.json()["message_url"] == message
    confirmed = client.post(f"{path}/record", headers=HEADERS, json={"outcome": "confirmed", "note": "Paid"})
    assert (confirmed.json()["status"], confirmed.json()["confirmation_note"]) == ("confirmed", "Paid")
    again = client.post(f"{path}/record", headers=HEADERS, json={"outcome": "rejected"})
    assert again.status_code == 409


def test_editing_approving_submitting_and_retrying(client, db):
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], state="published", url=link("tiktok"))
    dest = webhook_destination(db, client.data_dir)
    hook = _campaign(client, name="Hook", method="discord_webhook", destination_id=dest)
    (row,) = _attach(client, clip_id, [hook["id"]], mode="review")["created"]
    path = f"/campaigns/submissions/{row['id']}"
    assert row["status"] == "review"
    edited = client.patch(path, headers=HEADERS, json={"message_text": "{url} from me", "arg_values": {"a": "b"}})
    assert edited.status_code == 200, edited.text
    assert (edited.json()["message_text"], edited.json()["arg_values"], edited.json()["status"]) == (
        "{url} from me", {"a": "b"}, "review")
    assert client.patch(path, headers=HEADERS, json={"status": "sent"}).status_code == 422
    assert client.patch(path, headers=HEADERS, json={"method": "carrier pigeon"}).status_code == 400

    approved = client.post(f"{path}/approve", headers=HEADERS).json()
    assert (approved["status"], approved["to_submit"]) == ("ready", False)
    assert client.post(f"{path}/submit", headers=HEADERS).status_code == 409, "it isn't saved for later"
    db.set_submission(row["id"], status="failed", attempts=6)
    retried = client.post(f"{path}/retry", headers=HEADERS, json={})
    assert (retried.status_code, retried.json()["status"], retried.json()["attempts"]) == (200, "ready", 0)


def test_cancel_is_refused_while_sending(client, db):
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], state="published", url=link("tiktok"))
    dest = webhook_destination(db, client.data_dir)
    hook = _campaign(client, name="Hook", method="discord_webhook", destination_id=dest)
    (row,) = _attach(client, clip_id, [hook["id"]])["created"]
    assert row["status"] == "ready"
    statemachine.apply(db, row["id"], "send_start", now=row["next_attempt_at"])
    r = client.post(f"/campaigns/submissions/{row['id']}/cancel", headers=HEADERS)
    assert r.status_code == 409 and "sending" in r.json()["detail"]
    statemachine.apply(db, row["id"], "send_ambiguous", now=row["next_attempt_at"])
    unsure = client.get(f"/campaigns/submissions/{row['id']}").json()
    assert (unsure["uncertain"], unsure["to_submit"], unsure["outcome_to_record"]) == (True, True, False)
    assert client.post(f"/campaigns/submissions/{row['id']}/retry", headers=HEADERS).status_code == 409
    again = client.post(f"/campaigns/submissions/{row['id']}/retry", headers=HEADERS, json={"send_again": True})
    assert again.json()["status"] == "ready"
    cancelled = client.post(f"/campaigns/submissions/{row['id']}/cancel", headers=HEADERS).json()
    assert (cancelled["status"], cancelled["status_reason"]) == ("cancelled", "You cancelled it")
    assert client.post("/campaigns/submissions/999/cancel", headers=HEADERS).status_code == 404


def test_moving_to_another_campaign(client, db):
    clip_id = clip(db)
    here = _campaign(client, name="Here")
    there = _campaign(client, name="There")
    youtube_only = _campaign(client, name="YouTube only", platforms=["youtube"])
    (row,) = _attach(client, clip_id, [here["id"]], post_url=link("tiktok"))["created"]
    path = f"/campaigns/submissions/{row['id']}/move"
    refused = client.post(path, headers=HEADERS, json={"campaign_id": youtube_only["id"]})
    assert refused.status_code == 409 and "doesn't take TikTok" in refused.json()["detail"]
    missing = client.post(path, headers=HEADERS, json={"campaign_id": 999})
    assert (missing.status_code, missing.json()["detail"]) == (404, "No campaign #999")
    moved = client.post(path, headers=HEADERS, json={"campaign_id": there["id"]})
    assert moved.status_code == 200, moved.text
    got = moved.json()
    assert (got["cancelled"]["status"], got["cancelled"]["status_reason"]) == ("cancelled", "Moved to There")
    assert (got["moved"]["campaign_id"], got["moved"]["post_url"], got["moved"]["status"]) == (
        there["id"], link("tiktok"), "manual")
    assert client.post(path, headers=HEADERS, json={"campaign_id": here["id"]}).status_code == 409


def test_a_campaign_edit_reaches_its_pending_submissions(client, db):
    made = _campaign(client)
    (row,) = _attach(client, clip(db), [made["id"]], post_url=link("tiktok"))["created"]
    dest = webhook_destination(db, client.data_dir)
    r = client.patch(f"/campaigns/{made['id']}", headers=HEADERS,
                     json={"method": "discord_webhook", "destination_id": dest})
    assert r.status_code == 200, r.text
    assert r.json()["submissions_updated"] == 1
    assert r.json()["campaign"]["destination"] == {"id": dest, "label": "Example hook", "state": "connected"}
    after = client.get(f"/campaigns/submissions/{row['id']}").json()
    assert (after["method"], after["status"]) == ("discord_webhook", "ready")


def test_lists_filter_by_status_group_and_clip_and_count_work_to_do(client, db):
    made = _campaign(client)
    one, two = clip(db), clip(db, start=40.0, end=70.0)
    (done,) = _attach(client, one, [made["id"]], post_url=link("tiktok"))["created"]
    (waiting,) = _attach(client, two, [made["id"]])["created"]
    listed = client.get("/campaigns/submissions").json()
    assert [s["id"] for s in listed["submissions"]] == [waiting["id"], done["id"]]
    assert (listed["counts"], listed["to_submit"], listed["outcome_to_record"]) == (
        {"manual": 1, "waiting_publication": 1}, 1, 0)
    assert [s["id"] for s in client.get("/campaigns/submissions", params={"group": "to_submit"}).json()[
        "submissions"]] == [done["id"]]
    assert [s["id"] for s in client.get("/campaigns/submissions", params={"status": "waiting_publication"}).json()[
        "submissions"]] == [waiting["id"]]
    assert client.get("/campaigns/submissions", params={"group": "sent", "status": "manual"}).json()[
        "submissions"] == []
    assert [s["id"] for s in client.get("/campaigns/submissions", params={"clip_id": one}).json()[
        "submissions"]] == [done["id"]]
    assert client.get("/campaigns/submissions", params={"status": "lost"}).status_code == 400
    assert client.get("/campaigns/submissions", params={"group": "later"}).status_code == 400
