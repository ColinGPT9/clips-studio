"""The submission queue.

Evaluating a submission once its post's link is known (DESIGN §5.3): the link
is checked against the campaign's other rows first, so one link is submitted
to one campaign once. Then everything known with it is saved, every check
runs, and the verdict decides: expired, stopped for review, done by hand, or
(by webhook only) ready to send.

Then the engine (DESIGN §6, §7 Phase 4): attaching posts in each mode,
following each post until it is live with a link, the waiting rules, the
check cadence, sending by webhook with a frozen clock and a scripted Discord,
backoff, rate limits, crash safety, moving a row, and a campaign edit
reaching its rows. No network: Discord is tests/discord_fake.py, the
publisher tests/campaign_fake.py.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
import requests
from campaign_fake import FakePublisher, clip, link, webhook_destination
from discord_fake import CHANNEL_ID, FakeDiscordHTTP

import core.state
from campaigns import queue, service, statemachine
from campaigns.model import RETRY_DELAYS
from campaigns.urls import PRIVATE, UPLOAD_GONE, Resolution, validate
from server import posts

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
URL = "https://www.tiktok.com/@example/video/7300000000000000001"


def _setup(db, *, method="discord_assisted", dest_state="connected", sub=None, **campaign):
    db.upsert_video("vid", title="t", channel_name="Example Creator")
    clip_id = db.add_clip("vid", 0.0, 30.0, 90, "hook")
    dest_id = db.insert_destination(label="Example webhook", state=dest_state) if dest_state else 0
    campaign_id = db.insert_campaign("Example Campaign", method=method, destination_id=dest_id,
                                     channel_id="200000000000000002", **campaign)
    sub_id = db.insert_submission(campaign_id, "vid", 0.0, 30.0, "tiktok", clip_id=clip_id,
                                  clip_title="A great moment", provider="woopsocial", method=method,
                                  destination_id=dest_id, **(sub or {}))
    return campaign_id, sub_id


def _live(url=URL, **fields):
    return Resolution("live", **{"url": url, "source": "provider", "provider": "woopsocial",
                                 "visibility": "default", "account": "@example", "post_id": "p-1",
                                 "due_at": "2026-10-01T11:00:00Z", **fields})


def _checks(row):
    return {c["name"]: c["result"] for c in json.loads(row["checks"])}


@pytest.mark.parametrize("method, reason", [("discord_assisted", "Submit it in Discord"),
                                            ("website", "Submit it on the campaign's page")])
def test_a_good_link_is_the_users_to_submit_by_hand(db, method, reason):
    _, sub_id = _setup(db, method=method, website_url="https://example.com/campaign")
    row = queue.evaluate(db, sub_id, _live(), now=NOW)
    assert (row["status"], row["status_reason"]) == ("manual", reason)
    assert (row["post_url"], row["url_source"], row["post_id"], row["account"], row["provider"]) == (
        URL, "provider", "p-1", "@example", "woopsocial")
    assert (row["due_at"], row["posted_at"]) == ("2026-10-01T11:00:00Z", "2026-10-01T12:00:00Z")
    assert _checks(row)["public"] == "pass"


def test_a_good_link_by_webhook_is_ready_to_send_now(db):
    _, sub_id = _setup(db, method="discord_webhook")
    row = queue.evaluate(db, sub_id, _live(), now=NOW)
    assert (row["status"], row["status_reason"], row["next_attempt_at"]) == ("ready", "", NOW.timestamp())
    assert _checks(row)["destination"] == "pass"


def test_saved_for_later_and_review_first_stop_before_it_goes(db):
    _, held = _setup(db, method="discord_webhook", sub={"hold": 1})
    after = queue.evaluate(db, held, _live(), now=NOW)
    assert after["status_reason"] == "Saved for later"
    assert db.get_submission(held)["status"] == "ready"

    _, review = _setup_other(db, review_first=1)
    row = queue.evaluate(db, review, _live(), now=NOW)
    assert (row["status"], row["status_reason"]) == ("review", "Check it before it is submitted")


def _setup_other(db, **sub):
    campaign_id = db.insert_campaign("Other Campaign", method="discord_assisted")
    sub_id = db.insert_submission(campaign_id, "vid", 0.0, 30.0, "tiktok", **sub)
    return campaign_id, sub_id


def test_an_unknown_stops_only_the_webhook(db):
    _, hook = _setup(db, method="discord_webhook")
    row = queue.evaluate(db, hook, _live(visibility=""), now=NOW)
    assert (row["status"], row["status_reason"]) == ("review", "Clips Kitty didn't record who can see this post")

    _, by_hand = _setup_other(db, method="discord_assisted")
    after = queue.evaluate(db, by_hand, _live(visibility=""), now=NOW)
    assert after["status"] == "manual"


def test_a_failed_check_stops_for_review_with_its_reason(db):
    _, sub_id = _setup(db, required_hashtags=["clips"])
    row = queue.evaluate(db, sub_id, _live(caption="no tags here"), now=NOW)
    assert (row["status"], row["status_reason"]) == ("review", "Missing #clips")
    assert _checks(row)["hashtags"] == "fail"


def test_a_webhook_that_is_gone_leaves_it_to_be_done_by_hand(db):
    _, sub_id = _setup(db, method="discord_webhook", dest_state="gone")
    row = queue.evaluate(db, sub_id, _live(), now=NOW)
    assert row["status"] == "manual" and "gone" in row["status_reason"]


def test_a_campaign_past_its_deadline_expires_it(db):
    deadline = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _, sub_id = _setup(db, deadline=deadline)
    row = queue.evaluate(db, sub_id, _live(), now=NOW)
    assert (row["status"], row["status_reason"]) == ("expired", "The campaign's deadline passed")


def test_a_required_value_left_empty_needs_filling_in(db):
    _, sub_id = _setup(db, extra_fields=[{"name": "goal", "label": "Views goal", "required": True}])
    row = queue.evaluate(db, sub_id, _live(), now=NOW)
    assert (row["status"], row["status_reason"]) == ("review", "Fill in Views goal")
    db.set_submission(sub_id, extra_values={"goal": "1000"})
    after = queue.evaluate(db, sub_id, _live(), now=NOW, event="edit")
    assert after["status"] == "manual"


def test_a_link_already_submitted_to_the_campaign_cancels_this_one(db):
    campaign_id, first = _setup(db)
    second = db.insert_submission(campaign_id, "vid", 0.0, 25.0, "tiktok", method="discord_assisted")
    after = queue.evaluate(db, first, _live(), now=NOW)
    assert after["status"] == "manual"

    row = queue.evaluate(db, second, _live(), now=NOW)
    assert (row["status"], row["status_reason"], row["post_url"]) == (
        "cancelled", f"Already submitted to this campaign as #{first}", URL)
    # Another campaign may take the same link.
    _, elsewhere = _setup_other(db)
    after = queue.evaluate(db, elsewhere, _live(), now=NOW)
    assert after["status"] == "manual"


def test_the_same_post_typed_in_another_spelling_is_a_duplicate(db):
    campaign_id, first = _setup(db)
    second = db.insert_submission(campaign_id, "vid", 0.0, 25.0, "tiktok", method="discord_assisted")
    found, _ = validate("tiktok", URL)
    took = queue.evaluate(db, first, _live(found), now=NOW)
    assert took["status"] == "manual"

    typed, problem = validate("tiktok", "https://m.tiktok.com/@example/video/7300000000000000001/?lang=en")
    assert problem == ""
    row = queue.evaluate(db, second, Resolution("live", url=typed, source="manual"), now=NOW, event="set_url")
    assert (row["status"], row["status_reason"]) == ("cancelled", f"Already submitted to this campaign as #{first}")


def test_the_link_index_catches_a_row_that_took_the_link_meanwhile(db, monkeypatch):
    campaign_id, first = _setup(db)
    second = db.insert_submission(campaign_id, "vid", 0.0, 25.0, "tiktok", method="discord_assisted")
    db.set_submission(first, status="manual", post_url=URL)
    real = db.active_submission_with_url
    calls = []

    def missed_once(*args, **kw):
        calls.append(1)
        return None if len(calls) == 1 else real(*args, **kw)

    monkeypatch.setattr(db, "active_submission_with_url", missed_once)
    row = queue.evaluate(db, second, _live(), now=NOW)
    assert (row["status"], row["status_reason"]) == ("cancelled", f"Already submitted to this campaign as #{first}")


def test_a_typed_link_is_evaluated_the_same_way(db):
    _, sub_id = _setup(db, method="discord_webhook")
    db.set_submission(sub_id, status="review", status_reason="Missing #clips")
    typed = Resolution("live", url=URL, source="manual", account="@example")
    row = queue.evaluate(db, sub_id, typed, now=NOW, event="set_url")
    assert (row["status"], row["url_source"], row["provider"]) == ("ready", "manual", "woopsocial")
    assert _checks(row)["public"] == "pass"


def test_routing_looks_up_where_the_clip_came_from(db):
    _, sub_id = _setup(db, method="discord_webhook", filters={"modes": ["sports"]})
    db.add_job("process", json.dumps({"url": "x", "sport": "soccer"}), video_id="vid")
    after = queue.evaluate(db, sub_id, _live(), now=NOW)
    assert after["status"] == "ready"

    _, other = _setup_other(db, method="discord_assisted")
    db.set_campaign(db.get_submission(other)["campaign_id"], filters={"modes": ["podcast"]})
    row = queue.evaluate(db, other, _live(), now=NOW)
    assert row["status"] == "review" and _checks(row)["routing"] == "fail"


def test_a_resolution_without_a_link_is_not_evaluated(db):
    _, sub_id = _setup(db)
    with pytest.raises(ValueError, match="link"):
        queue.evaluate(db, sub_id, Resolution("published_no_url"), now=NOW)
    assert db.get_submission(sub_id)["status"] == "waiting_publication"


def test_a_blocked_resolution_is_never_taken_as_found(db):
    # A deleted upload's link, offered for the user to check: it carries a
    # URL, and its reason must reach the user rather than be lost here.
    _, sub_id = _setup(db, method="discord_webhook")
    blocked = Resolution("blocked", url="https://www.youtube.com/shorts/abcdefghijk", provider="youtube",
                         source="youtube", reason=UPLOAD_GONE)
    before = dict(db.get_submission(sub_id))
    with pytest.raises(ValueError, match="live"):
        queue.evaluate(db, sub_id, blocked, now=NOW)
    assert dict(db.get_submission(sub_id)) == before


def test_the_first_sight_already_recorded_wins(db):
    # published_no_url stamped when the post was first seen live; the link
    # came later, and the post's own time does not move it.
    _, sub_id = _setup(db)
    db.set_submission(sub_id, status="waiting_url", posted_at="2026-09-30T08:00:00Z")
    row = queue.evaluate(db, sub_id, _live(posted_at="2026-09-29T00:00:00Z"), now=NOW)
    assert row["posted_at"] == "2026-09-30T08:00:00Z"
    # Without one, the resolution's time, and only then now.
    _, other = _setup_other(db)
    row = queue.evaluate(db, other, _live(posted_at="2026-09-29T00:00:00Z"), now=NOW)
    assert row["posted_at"] == "2026-09-29T00:00:00Z"


def test_a_failed_row_given_a_new_link_starts_counting_again(db):
    _, sub_id = _setup(db, method="discord_webhook")
    db.set_submission(sub_id, status="failed", attempts=5, error="500 Server Error")
    typed = Resolution("live", url=URL, source="manual", account="@example")
    row = queue.evaluate(db, sub_id, typed, now=NOW, event="set_url")
    assert (row["status"], row["attempts"], row["error"], row["next_attempt_at"]) == (
        "ready", 0, "", NOW.timestamp())


# ---- reopening -----------------------------------------------------------------------------------

def _typed_then_cancelled(db, sub_id):
    typed = Resolution("live", url=URL, source="manual", account="@example")
    row = queue.evaluate(db, sub_id, typed, now=NOW, event="set_url")
    assert row["status"] == "manual"
    statemachine.apply(db, sub_id, "cancel", now=NOW)


@pytest.mark.parametrize("provider", ["woopsocial", "manual"])
def test_reopening_gives_back_a_link_the_user_typed(db, provider):
    _, sub_id = _setup(db)
    db.set_submission(sub_id, provider=provider)
    _typed_then_cancelled(db, sub_id)

    row = queue.reopen(db, sub_id, now=NOW)
    assert (row["status"], row["status_reason"]) == ("manual", "Submit it in Discord")
    assert (row["post_url"], row["url_source"], row["provider"]) == (URL, "manual", provider)


def test_reopening_a_typed_link_another_row_took_meanwhile_cancels_it_again(db):
    campaign_id, first = _setup(db)
    _typed_then_cancelled(db, first)
    second = db.insert_submission(campaign_id, "vid", 0.0, 25.0, "tiktok", method="discord_assisted")
    took = queue.evaluate(db, second, _live(), now=NOW)
    assert took["status"] == "manual"

    row = queue.reopen(db, first, now=NOW)
    assert (row["status"], row["status_reason"]) == ("cancelled", f"Already submitted to this campaign as #{second}")


def test_reopening_a_link_a_publisher_gave_leaves_it_to_be_found_again(db):
    _, sub_id = _setup(db)
    row = queue.evaluate(db, sub_id, _live(), now=NOW)
    assert row["status"] == "manual"
    statemachine.apply(db, sub_id, "cancel", now=NOW)
    row = queue.reopen(db, sub_id, now=NOW)
    assert (row["status"], row["post_url"]) == ("waiting_publication", "")
    with pytest.raises(statemachine.NotAllowed):
        queue.reopen(db, sub_id, now=NOW)


# ---- attaching (DESIGN §7 "Attach modes", §3.2) ------------------------------------------------
#
# Rows also carry the database's own local stamps (created_at, a provider
# row's updated_at, a send's created_at), which several rules read; they are
# frozen at the tests' clock too.

T0 = NOW


@pytest.fixture(autouse=True)
def _frozen_stamps(monkeypatch):
    local = T0.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    monkeypatch.setattr(core.state, "_now", lambda: local)
    monkeypatch.setattr(posts, "sent_stamp", lambda: local)


def _utc(moment) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _campaign(db, name="Example Campaign", method="discord_assisted", **fields):
    fields.setdefault("channel_id", "200000000000000002")
    fields.setdefault("guild_id", "300000000000000001")
    return db.insert_campaign(name, method=method, **fields)


def _attach(db, clip_id, campaign_ids, platforms=("tiktok",), *, at=T0, **kw):
    return queue.attach(db, items=[{"clip_id": clip_id, "platforms": list(platforms)}],
                        campaign_ids=list(campaign_ids), now=at, **kw)


def _advance(db, data_dir, at, http=None, **kw):
    return queue.advance(db, data_dir, now=at, http=http if http is not None else FakeDiscordHTTP(), **kw)


def _due(db, sub_id):
    """Make a row due now (the cadence tests check the cadence itself)."""
    db.set_submission(sub_id, next_attempt_at=0)


@pytest.mark.parametrize("mode, campaign_review_first, expected", [
    ("after_publish", 0, (0, 0)), ("now", 0, (0, 0)), ("review", 0, (0, 1)), ("later", 0, (1, 0)),
    ("after_publish", 1, (0, 1)), ("now", 1, (0, 1)), ("review", 1, (0, 1)), ("later", 1, (1, 1)),
])
def test_each_attach_mode_sets_hold_and_review_first(db, mode, campaign_review_first, expected):
    clip_id = clip(db)
    cid = _campaign(db, review_first=campaign_review_first)
    row = _attach(db, clip_id, [cid], mode=mode)["created"][0]
    assert (row["hold"], row["review_first"]) == expected
    assert (row["status"], row["origin"], row["method"], row["clip_title"]) == (
        "waiting_publication", "editor", "discord_assisted", "A great moment")


def test_now_on_a_post_not_yet_live_is_after_publish(db):
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"])
    now_row = _attach(db, clip_id, [_campaign(db)], mode="now", provider="woopsocial")["created"][0]
    after_row = _attach(db, clip_id, [_campaign(db, "Other")], mode="after_publish",
                        provider="woopsocial")["created"][0]
    keys = ("status", "hold", "review_first", "next_attempt_at", "provider", "due_at", "post_url")
    assert [now_row[k] for k in keys] == [after_row[k] for k in keys]
    assert (now_row["status"], now_row["next_attempt_at"]) == ("waiting_publication", T0.timestamp() + 60)


def test_a_post_already_live_is_evaluated_in_the_request(db):
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], state="published", url=link("tiktok"))
    cid = _campaign(db, command_name="submit", command_args=[{"name": "link", "value": ""}], url_argument="link")
    row = _attach(db, clip_id, [cid], mode="now")["created"][0]
    assert (row["status"], row["status_reason"], row["post_url"], row["provider"], row["url_source"]) == (
        "manual", "Submit it in Discord", link("tiktok"), "woopsocial", "provider")


def test_each_item_gets_its_own_platforms(db):
    one, two = clip(db), clip(db, start=40.0, end=70.0)
    cid = _campaign(db)
    got = queue.attach(db, items=[{"clip_id": one, "platforms": ["tiktok", "youtube"]},
                                  {"clip_id": two, "platforms": ["instagram"]}],
                       campaign_ids=[cid], now=T0, origin="publish_all")
    made = sorted((r["clip_id"], r["platform"]) for r in got["created"])
    assert made == sorted([(one, "tiktok"), (one, "youtube"), (two, "instagram")])
    assert {r["origin"] for r in got["created"]} == {"publish_all"}
    assert {(r["start_s"], r["end_s"]) for r in got["created"] if r["clip_id"] == two} == {(40.0, 70.0)}


def test_each_refusal_says_why_and_nothing_is_written(db):
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], scheduled_for=_utc(T0 + timedelta(hours=2)))
    passed = _campaign(db, "Passed", deadline=_utc(T0 - timedelta(hours=1)))
    closing = _campaign(db, "Closing", deadline=_utc(T0 + timedelta(hours=1)))
    youtube_only = _campaign(db, "YouTube only", platforms=["youtube"])
    archived = _campaign(db, "Archived", archived=1)
    off = _campaign(db, "Off", enabled=0)
    got = _attach(db, clip_id, [passed, closing, youtube_only, archived, off, 999], provider="woopsocial")
    assert {r["campaign_id"]: r["reason"] for r in got["refused"]} == {
        passed: "The campaign's deadline has passed",
        closing: "Its deadline is before this post's time",
        youtube_only: "This campaign doesn't take TikTok posts",
        archived: queue.ARCHIVED, off: queue.SWITCHED_OFF, 999: queue.NO_CAMPAIGN,
    }
    assert {(r["clip_id"], r["platform"]) for r in got["refused"]} == {(clip_id, "tiktok")}
    assert (got["created"], got["existing"], db.list_submissions()) == ([], [], [])


def test_a_youtube_upload_queued_for_after_the_deadline_is_refused(db):
    clip_id = clip(db)
    job_id = FakePublisher(db).youtube(clip_id, publish_at=_utc(T0 + timedelta(days=2)), done=False)
    cid = _campaign(db, deadline=_utc(T0 + timedelta(days=1)))
    got = _attach(db, clip_id, [cid], platforms=["youtube"], provider="youtube", publish_job_id=job_id)
    assert [r["reason"] for r in got["refused"]] == ["Its deadline is before this post's time"]
    later = _campaign(db, "Later", deadline=_utc(T0 + timedelta(days=3)))
    row = _attach(db, clip_id, [later], platforms=["youtube"], provider="youtube",
                  publish_job_id=job_id)["created"][0]
    assert (row["status"], row["publish_job_id"], row["provider"]) == ("waiting_publication", job_id, "youtube")


def test_a_clip_that_is_gone_is_refused(db):
    got = _attach(db, 4242, [_campaign(db)])
    assert [r["reason"] for r in got["refused"]] == [queue.GONE_CLIP]


def test_a_typed_link_is_checked_for_a_duplicate_before_anything_is_written(db):
    one, two = clip(db), clip(db, start=40.0, end=70.0)
    cid = _campaign(db)
    first = _attach(db, one, [cid], post_url=link("tiktok"))["created"][0]
    assert (first["status"], first["post_url"], first["url_source"], first["provider"]) == (
        "manual", link("tiktok"), "manual", "manual")
    got = _attach(db, two, [cid], post_url=link("tiktok") + "/?lang=en")
    assert got["refused"] == [{"campaign_id": cid, "clip_id": two, "platform": "tiktok",
                               "reason": f"Already submitted to this campaign as #{first['id']}"}]
    assert [r["id"] for r in db.list_submissions()] == [first["id"]]


@pytest.mark.parametrize("kw, message", [
    ({"mode": "soon"}, "when"),
    ({"campaign_ids": []}, "campaign"),
    ({"platforms": []}, "platforms"),
    ({"platforms": ["discord"]}, "can't submit"),
    ({"post_url": "https://example.com/watch/1"}, "platform"),
    ({"post_url": "https://www.youtube.com/shorts/abcdefghijk"}, "YouTube link, not a TikTok"),
    ({"platforms": ["tiktok", "youtube"], "post_url": link("tiktok")}, "one clip on one platform"),
])
def test_a_request_that_cant_be_right_is_refused_whole(db, kw, message):
    clip_id = clip(db)
    cid = _campaign(db)
    platforms = kw.pop("platforms", ["tiktok"])
    ids = kw.pop("campaign_ids", [cid])
    with pytest.raises(ValueError, match=message):
        _attach(db, clip_id, ids, platforms=platforms, **kw)
    assert db.list_submissions() == []


def test_attaching_again_keeps_the_row_and_reopens_only_when_asked(db):
    clip_id = clip(db)
    cid = _campaign(db)
    first = _attach(db, clip_id, [cid])["created"][0]
    assert first["next_attempt_at"] > 0
    again = _attach(db, clip_id, [cid])
    assert (again["created"], [r["id"] for r in again["existing"]]) == ([], [first["id"]])
    assert again["existing"][0]["next_attempt_at"] == 0, "attached again: looked at on the next pass"

    statemachine.apply(db, first["id"], "cancel", now=T0)
    kept = _attach(db, clip_id, [cid])
    assert kept["existing"][0]["status"] == "cancelled", "a cancel stands unless the user reopens it"
    reopened = _attach(db, clip_id, [cid], reopen_rows=True, mode="later")["created"][0]
    assert (reopened["id"], reopened["status"], reopened["hold"]) == (first["id"], "waiting_publication", 1)
    active = _attach(db, clip_id, [cid], reopen_rows=True)
    assert ([r["id"] for r in active["existing"]], active["created"]) == ([first["id"]], [])


# ---- moving and campaign edits -------------------------------------------------------------------

def test_moving_cancels_it_here_and_attaches_it_there_with_its_link_and_words(db):
    clip_id = clip(db)
    here, there = _campaign(db, "Here"), _campaign(db, "There")
    row = _attach(db, clip_id, [here], post_url=link("tiktok"))["created"][0]
    queue.edit(db, row["id"], now=T0, message_text="My clip {url}", arg_values={"link": "x"},
               extra_values={"goal": "1000"})

    cancelled, moved = queue.move(db, row["id"], there, now=T0)
    assert (cancelled["status"], cancelled["status_reason"]) == ("cancelled", "Moved to There")
    assert (moved["campaign_id"], moved["status"], moved["post_url"], moved["url_source"]) == (
        there, "manual", link("tiktok"), "manual")
    assert (moved["message_text"], json.loads(moved["arg_values"]), json.loads(moved["extra_values"])) == (
        "My clip {url}", {"link": "x"}, {"goal": "1000"})
    assert (moved["video_id"], moved["start_s"], moved["end_s"], moved["clip_id"]) == ("vid", 0.0, 30.0, clip_id)

    # And back: the row it left there (cancelled) starts again.
    _, back = queue.move(db, moved["id"], here, now=T0)
    assert (back["id"], back["status"], back["post_url"]) == (row["id"], "manual", link("tiktok"))


def test_a_move_the_other_campaign_refuses_changes_nothing(db):
    clip_id, other_clip = clip(db), clip(db, start=40.0, end=70.0)
    here, youtube_only, taken = _campaign(db, "Here"), _campaign(db, "YouTube only", platforms=["youtube"]), \
        _campaign(db, "Taken")
    row = _attach(db, clip_id, [here], post_url=link("tiktok"))["created"][0]
    _attach(db, other_clip, [taken], post_url=link("tiktok"))
    before = dict(db.get_submission(row["id"]))
    for target, message in ((youtube_only, "doesn't take TikTok"), (taken, "already submitted to Taken")):
        with pytest.raises(statemachine.NotAllowed, match=message):
            queue.move(db, row["id"], target, now=T0)
    with pytest.raises(ValueError, match="already in that campaign"):
        queue.move(db, row["id"], here, now=T0)
    assert dict(db.get_submission(row["id"])) == before
    assert db.list_submissions(campaign_id=youtube_only) == []


def test_a_move_that_breaks_halfway_leaves_both_campaigns_as_they_were(db, monkeypatch):
    clip_id = clip(db)
    here, there = _campaign(db, "Here"), _campaign(db, "There")
    row = _attach(db, clip_id, [here], post_url=link("tiktok"))["created"][0]
    before = dict(db.get_submission(row["id"]))

    def broken(*args, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(queue, "_first_look", broken)
    with pytest.raises(RuntimeError):
        queue.move(db, row["id"], there, now=T0)
    assert dict(db.get_submission(row["id"])) == before
    assert db.list_submissions(campaign_id=there) == []
    assert not db.conn.in_transaction


def test_a_campaign_edit_reaches_only_rows_that_dont_hold_their_own_value(db, tmp_path):
    dest = webhook_destination(db, tmp_path)
    cid = _campaign(db)
    ids = []
    for i in range(4):
        clip_id = clip(db, start=40.0 * i, end=40.0 * i + 30)
        typed = link("tiktok", i + 1) if i < 3 else ""
        ids.append(_attach(db, clip_id, [cid], post_url=typed)["created"][0]["id"])
    plain, own, opened, waiting = ids
    queue.edit(db, own, now=T0, method="website")
    queue.opened(db, opened, now=T0)

    before = db.get_campaign(cid)
    db.set_campaign(cid, method="discord_webhook", destination_id=dest)
    updated = queue.reapply_campaign(db, cid, before, now=T0)
    assert updated == 3

    def seen(sub_id):
        row = db.get_submission(sub_id)
        return row["method"], row["destination_id"], row["status"]

    assert seen(plain) == ("discord_webhook", dest, "ready"), "copied, then evaluated again"
    assert seen(own) == ("website", dest, "manual"), "its own method stays; the destination isn't its own"
    assert seen(opened) == ("discord_assisted", 0, "manual"), "being submitted by hand: left alone"
    assert seen(waiting) == ("discord_webhook", dest, "waiting_publication")
    assert json.loads(db.get_submission(own)["overridden"]) == ["method"]
    again = queue.reapply_campaign(db, cid, db.get_campaign(cid), now=T0)
    assert again == 0


# ---- following a post (DESIGN §4.2, §6.1) -----------------------------------------------------------

def test_a_scheduled_post_is_followed_until_it_is_live_then_sent_once(db, tmp_path):
    clip_id = clip(db)
    publisher = FakePublisher(db)
    dest = webhook_destination(db, tmp_path)
    cid = _campaign(db, method="discord_webhook", destination_id=dest, message_template="New clip: {url}")
    publisher.publish(clip_id, ["tiktok"], scheduled_for=_utc(T0 + timedelta(hours=1)))
    row = _attach(db, clip_id, [cid], provider="woopsocial")["created"][0]
    assert (row["status"], row["due_at"], row["next_attempt_at"]) == (
        "waiting_publication", _utc(T0 + timedelta(hours=1)), T0.timestamp() + 600)

    http, events = FakeDiscordHTTP(), []
    _advance(db, tmp_path, T0 + timedelta(minutes=5), http, emit=events.append)
    assert db.get_submission(row["id"])["status"] == "waiting_publication"

    publisher.refresh(clip_id, "tiktok", "published")  # live, the link not known yet
    live = T0 + timedelta(minutes=70)
    _advance(db, tmp_path, live, http, emit=events.append)
    row = db.get_submission(row["id"])
    assert (row["status"], row["posted_at"]) == ("waiting_url", _utc(live))

    publisher.refresh(clip_id, "tiktok", "published", url=link("tiktok"))
    http.add((200, {"id": "600000000000000001", "channel_id": CHANNEL_ID}))
    out = _advance(db, tmp_path, live + timedelta(seconds=61), http, emit=events.append)
    row = db.get_submission(row["id"])
    assert (row["status"], row["sent_by"], row["discord_message_id"], row["post_url"]) == (
        "awaiting_confirmation", "webhook", "600000000000000001", link("tiktok"))
    assert out["sent"] == 1
    (call,) = http.calls
    assert (call["method"], call["params"]) == ("POST", {"wait": "true"})
    assert call["json"] == {"content": f"New clip: {link('tiktok')}", "allowed_mentions": {"parse": []}}
    assert [(e["previous"], e["status"]) for e in events] == [
        ("waiting_publication", "waiting_url"), ("waiting_url", "ready"), ("sending", "awaiting_confirmation")]
    assert events[-1]["to_submit"] is False, "posted, waiting for the campaign's word: not work to do"

    for hours in (1, 24, 72):
        _advance(db, tmp_path, live + timedelta(hours=hours), http)
    assert len(http.calls) == 1, "sent once"


def test_rows_stuck_behind_failed_or_far_off_posts_never_hold_up_a_live_one(db, tmp_path):
    publisher, cid = FakePublisher(db), _campaign(db)
    for i in range(150):
        clip_id = clip(db, start=40.0 * i, end=40.0 * i + 30)
        if i % 2:
            publisher.publish(clip_id, ["tiktok"], state="failed", error="Rejected")
        else:
            publisher.publish(clip_id, ["tiktok"], scheduled_for=_utc(T0 + timedelta(days=20)))
        db.insert_submission(cid, "vid", 40.0 * i, 40.0 * i + 30, "tiktok", clip_id=clip_id, origin="watch",
                             provider="woopsocial")
    _advance(db, tmp_path, T0)
    _advance(db, tmp_path, T0 + timedelta(seconds=1))
    waits = {r["next_attempt_at"] - T0.timestamp() for r in db.list_submissions(limit=1000)}
    assert waits <= {600, 601, 3600, 3601} and len(db.submissions_due(queue.FOLLOWED, now=T0.timestamp() + 2,
                                                                     no_url_manual=True)) == 0

    live_clip = clip(db, start=9000.0, end=9030.0)
    publisher.publish(live_clip, ["tiktok"], state="published", url=link("tiktok"))
    live = db.insert_submission(cid, "vid", 9000.0, 9030.0, "tiktok", clip_id=live_clip, provider="woopsocial")
    _advance(db, tmp_path, T0 + timedelta(seconds=2))
    assert db.get_submission(live)["status"] == "manual"


@pytest.mark.parametrize("case, wait", [
    ("due", 60), ("unknown_time", 60), ("scheduled", 600), ("failed_watch_post", 3600), ("manual_no_link", 3600),
    ("waiting_url", 60),
])
def test_how_soon_a_row_is_looked_at_again(db, tmp_path, case, wait):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    origin = "watch" if case == "failed_watch_post" else "editor"
    if case == "due":
        publisher.publish(clip_id, ["tiktok"])
    elif case == "waiting_url":
        publisher.publish(clip_id, ["tiktok"], state="published")  # live, its link not known yet
    elif case == "scheduled":
        publisher.publish(clip_id, ["tiktok"], scheduled_for=_utc(T0 + timedelta(days=1)))
    elif case == "failed_watch_post":
        publisher.publish(clip_id, ["tiktok"], state="failed", error="Rejected")
    elif case == "manual_no_link":
        publisher.publish(clip_id, ["tiktok"], state="skipped")
    sub_id = db.insert_submission(cid, "vid", 0.0, 30.0, "tiktok", clip_id=clip_id, origin=origin,
                                  provider="woopsocial" if case != "unknown_time" else "")
    if case == "waiting_url":
        db.set_submission(sub_id, status="waiting_url", posted_at=_utc(T0))
    _advance(db, tmp_path, T0)
    row = db.get_submission(sub_id)
    assert row["next_attempt_at"] == T0.timestamp() + wait
    assert row["status"] == {"manual_no_link": "manual", "waiting_url": "waiting_url"}.get(
        case, "waiting_publication")
    assert db.submissions_due(queue.FOLLOWED, now=T0.timestamp() + wait - 1, no_url_manual=True) == []


def test_a_platform_the_publish_skipped_goes_to_the_user_and_resolves_when_it_appears(db, tmp_path):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    publisher.publish(clip_id, ["instagram"], state="skipped", error="Instagram isn't connected")
    row = _attach(db, clip_id, [cid], platforms=["instagram"], provider="woopsocial")["created"][0]
    assert (row["status"], row["status_reason"], row["post_url"]) == (
        "manual", "Instagram isn't connected on WoopSocial, so nothing was posted there: publish it there "
                  "or enter its link", "")
    assert row["error"] == "Instagram isn't connected"

    publisher.publish(clip_id, ["instagram"], state="published", url=link("instagram"))
    _advance(db, tmp_path, T0 + timedelta(minutes=30))
    assert db.get_submission(row["id"])["status"] == "manual", "a manual row with no link is looked at hourly"
    _advance(db, tmp_path, T0 + timedelta(minutes=61))
    after = db.get_submission(row["id"])
    assert (after["status"], after["status_reason"], after["post_url"]) == (
        "manual", "Submit it in Discord", link("instagram"))


def test_a_failed_editor_post_goes_to_the_user_at_once(db):
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], state="failed", error="Rejected by TikTok")
    row = _attach(db, clip_id, [_campaign(db)], provider="woopsocial")["created"][0]
    assert (row["status"], row["status_reason"], row["error"]) == (
        "manual", "The post failed on TikTok: publish it again or enter its link", "Rejected by TikTok")


def test_a_failed_watch_post_waits_two_days_for_the_watch_to_send_it_again(db, tmp_path):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    publisher.publish(clip_id, ["tiktok"], state="failed", error="Rejected")
    waiting = db.insert_submission(cid, "vid", 0.0, 30.0, "tiktok", clip_id=clip_id, origin="watch",
                                   provider="woopsocial")
    _advance(db, tmp_path, T0)
    row = db.get_submission(waiting)
    assert (row["status"], row["status_reason"]) == (
        "waiting_publication", "The post failed on TikTok: waiting for the watch to send it again")
    _due(db, waiting)
    _advance(db, tmp_path, T0 + timedelta(hours=47))
    assert db.get_submission(waiting)["status"] == "waiting_publication"
    _due(db, waiting)
    _advance(db, tmp_path, T0 + timedelta(hours=49))
    assert db.get_submission(waiting)["status_reason"] == \
        "The post failed on TikTok: publish it again or enter its link"


def test_a_watch_resending_a_failed_post_in_time_resolves_it(db, tmp_path):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    publisher.publish(clip_id, ["tiktok"], state="failed", error="Rejected")
    waiting = db.insert_submission(cid, "vid", 0.0, 30.0, "tiktok", clip_id=clip_id, origin="watch",
                                   provider="woopsocial")
    _advance(db, tmp_path, T0)
    publisher.publish(clip_id, ["tiktok"], state="published", url=link("tiktok"))  # the 6-hour re-send
    _advance(db, tmp_path, T0 + timedelta(hours=6))
    row = db.get_submission(waiting)
    assert (row["status"], row["post_url"]) == ("manual", link("tiktok"))


def test_a_post_with_no_link_a_day_after_it_went_live_goes_to_the_user(db, tmp_path):
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], state="published")
    row = _attach(db, clip_id, [_campaign(db)], provider="woopsocial")["created"][0]
    assert (row["status"], row["posted_at"]) == ("waiting_url", _utc(T0))
    _advance(db, tmp_path, T0 + timedelta(hours=23))
    assert db.get_submission(row["id"])["status"] == "waiting_url"
    _due(db, row["id"])
    _advance(db, tmp_path, T0 + timedelta(hours=24, seconds=1))
    after = db.get_submission(row["id"])
    assert (after["status"], after["status_reason"]) == ("manual", statemachine.REASONS["url_overdue"])


def test_the_fourteen_days_count_from_the_posts_own_time(db, tmp_path):
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], scheduled_for=_utc(T0 + timedelta(days=20)))
    row = _attach(db, clip_id, [_campaign(db)], provider="woopsocial")["created"][0]
    _due(db, row["id"])
    _advance(db, tmp_path, T0 + timedelta(days=15))
    assert db.get_submission(row["id"])["status"] == "waiting_publication", "20 days out: not late on day 15"
    _due(db, row["id"])
    _advance(db, tmp_path, T0 + timedelta(days=34, minutes=1))
    after = db.get_submission(row["id"])
    assert (after["status"], after["status_reason"]) == ("manual", statemachine.REASONS["stale"])


def test_the_same_post_found_for_two_clips_is_submitted_once(db):
    one, two = clip(db), clip(db, start=0.0, end=25.0)  # a trimmed publish of the same post
    publisher, cid = FakePublisher(db), _campaign(db)
    publisher.publish(one, ["tiktok"], state="published", url=link("tiktok"))
    publisher.publish(two, ["tiktok"], state="published", url=link("tiktok"))
    first = _attach(db, one, [cid])["created"][0]
    second = _attach(db, two, [cid])["created"][0]
    assert first["status"] == "manual"
    assert (second["status"], second["status_reason"]) == (
        "cancelled", f"Already submitted to this campaign as #{first['id']}")


def test_a_private_youtube_upload_goes_to_the_user_with_its_reason(db):
    clip_id = clip(db)
    job_id = FakePublisher(db).youtube(clip_id, privacy="private", actual="private")
    row = _attach(db, clip_id, [_campaign(db)], platforms=["youtube"], provider="youtube",
                  publish_job_id=job_id)["created"][0]
    assert (row["status"], row["status_reason"], row["post_url"]) == ("manual", PRIVATE, "")


def test_a_deleted_uploads_link_is_put_in_for_the_user_to_check(db, tmp_path):
    clip_id, cid = clip(db), _campaign(db)
    sub_id = db.insert_submission(cid, "vid", 0.0, 30.0, "youtube", clip_id=clip_id, provider="youtube",
                                  post_id="abcdefghijk")
    _advance(db, tmp_path, T0)
    row = db.get_submission(sub_id)
    assert (row["status"], row["status_reason"], row["post_url"], row["url_source"]) == (
        "manual", UPLOAD_GONE, "https://www.youtube.com/shorts/abcdefghijk", "youtube")


def test_a_direct_youtube_upload_is_followed_through_its_job(db):
    clip_id = clip(db)
    publisher, cid = FakePublisher(db), _campaign(db)
    job_id = publisher.youtube(clip_id, done=False)
    row = _attach(db, clip_id, [cid], platforms=["youtube"], provider="youtube", publish_job_id=job_id)["created"][0]
    assert row["status"] == "waiting_publication"
    db.finish_publish_job(job_id, "done", youtube_id="abcdefghijk")
    db.record_publish(clip_id, {"youtube_id": "abcdefghijk", "video_id": "vid", "start_s": 0.0, "end_s": 30.0,
                                "privacy": "public", "actual_privacy": "public", "state": "uploaded",
                                "channel_title": "Example Channel"})
    after = queue.follow(db, db.get_submission(row["id"]), now=T0)
    assert (after["status"], after["post_url"], after["url_source"]) == (
        "manual", "https://www.youtube.com/shorts/abcdefghijk", "youtube")


def test_submissions_follow_a_rerendered_clip(db):
    clip_id, cid = clip(db), _campaign(db)
    row = _attach(db, clip_id, [cid])["created"][0]
    db.follow_clip_id(clip_id, 777)
    after = db.get_submission(row["id"])
    assert (after["clip_id"], after["video_id"], after["start_s"], after["end_s"]) == (777, "vid", 0.0, 30.0)


# ---- deadlines and crash safety -------------------------------------------------------------------

def _ready(db, data_dir, *, n=1, dest=None, **campaign):
    """n webhook rows ready to send now, on one destination."""
    dest = dest or webhook_destination(db, data_dir)
    cid = _campaign(db, f"Webhook {dest}-{n}-{len(campaign)}", method="discord_webhook", destination_id=dest,
                    **campaign)
    publisher, ids = FakePublisher(db), []
    for i in range(n):
        clip_id = clip(db, video_id=f"vid-{dest}", start=40.0 * i, end=40.0 * i + 30)
        publisher.publish(clip_id, ["tiktok"], state="published", url=link("tiktok", 10 * dest + i))
        row = _attach(db, clip_id, [cid])["created"][0]
        assert row["status"] == "ready", row["status_reason"]
        ids.append(row["id"])
    return ids, dest


def test_a_deadline_expires_what_is_not_yet_sent_and_leaves_the_rest(db, tmp_path):
    (sent, waiting), _ = _ready(db, tmp_path, n=2, deadline=_utc(T0 + timedelta(hours=1)))
    statemachine.apply(db, sent, "send_start", now=T0)
    statemachine.apply(db, sent, "send_ok", now=T0)
    _advance(db, tmp_path, T0 + timedelta(hours=2), FakeDiscordHTTP())
    assert db.get_submission(waiting)["status"] == "expired"
    assert db.get_submission(sent)["status"] == "awaiting_confirmation"


def test_a_send_cut_off_by_a_restart_is_never_sent_again_by_itself(db, tmp_path):
    (sub_id,), _ = _ready(db, tmp_path)
    statemachine.apply(db, sub_id, "send_start", now=T0)
    settled = db.recover_sending_submissions(statemachine.REASONS["restart"])
    assert settled == 1
    row = db.get_submission(sub_id)
    assert (row["status"], row["sent_by"], row["status_reason"]) == (
        "awaiting_confirmation", "", "Clips Kitty closed while sending. Check the channel.")
    http = FakeDiscordHTTP()
    for minutes in (1, 60, 24 * 60):
        _advance(db, tmp_path, T0 + timedelta(minutes=minutes), http)
    assert http.calls == []


def test_a_row_left_sending_is_settled_at_the_start_of_a_pass(db, tmp_path):
    (old, fresh), _ = _ready(db, tmp_path, n=2)
    statemachine.apply(db, old, "send_start", now=T0)
    statemachine.apply(db, fresh, "send_start", now=T0 + timedelta(seconds=100))
    events = []
    _advance(db, tmp_path, T0 + timedelta(seconds=121), FakeDiscordHTTP(), emit=events.append)
    row = db.get_submission(old)
    assert (row["status"], row["sent_by"], row["status_reason"]) == (
        "awaiting_confirmation", "", "Check Discord: it may have been posted")
    assert db.get_submission(fresh)["status"] == "sending"
    assert [(e["submission_id"], e["previous"], e["status"], e["to_submit"]) for e in events] == [
        (old, "sending", "awaiting_confirmation", True)]


# ---- sending by webhook (DESIGN §4.2, §4.3, §5.5) ------------------------------------------------

def test_a_send_that_never_reaches_discord_backs_off_then_fails(db, tmp_path):
    (sub_id,), _ = _ready(db, tmp_path)
    http = FakeDiscordHTTP(requests.ConnectTimeout("no route"))
    at, waits = T0, []
    for attempt in range(1, len(RETRY_DELAYS) + 1):
        if attempt > 1:
            http.add((503, {"message": "Service Unavailable"}))
        _advance(db, tmp_path, at, http)
        row = db.get_submission(sub_id)
        assert (row["status"], row["attempts"]) == ("ready", attempt)
        waits.append(row["next_attempt_at"] - at.timestamp())
        at = at + timedelta(seconds=waits[-1])
    assert waits == list(RETRY_DELAYS)
    http.add((503, {"message": "Service Unavailable"}))
    _advance(db, tmp_path, at, http)
    row = db.get_submission(sub_id)
    assert (row["status"], row["attempts"]) == ("failed", len(RETRY_DELAYS) + 1)
    assert "6 tries" in row["status_reason"] and len(http.calls) == len(RETRY_DELAYS) + 1


def test_a_rate_limit_is_waited_out_and_not_counted(db, tmp_path):
    (sub_id,), _ = _ready(db, tmp_path)
    http = FakeDiscordHTTP((429, {"retry_after": 12.5, "global": False}))
    out = _advance(db, tmp_path, T0, http)
    row = db.get_submission(sub_id)
    assert (row["status"], row["attempts"], row["next_attempt_at"]) == ("ready", 0, T0.timestamp() + 13.5)
    assert out["paused_until"] == 0


def test_a_global_rate_limit_holds_every_send(db, tmp_path):
    (first,), _ = _ready(db, tmp_path)
    (second,), _ = _ready(db, tmp_path)
    http = FakeDiscordHTTP((429, {"retry_after": 30, "global": True}))
    out = _advance(db, tmp_path, T0, http)
    assert out["paused_until"] == T0.timestamp() + 30 and len(http.calls) == 1
    assert {db.get_submission(i)["status"] for i in (first, second)} == {"ready"}
    _advance(db, tmp_path, T0 + timedelta(seconds=10), http, paused_until=out["paused_until"])
    assert len(http.calls) == 1, "nothing goes out while Discord's global limit holds"


def test_a_webhook_that_is_gone_takes_its_other_rows_to_the_user(db, tmp_path):
    (first, second), dest = _ready(db, tmp_path, n=2)
    db.set_submission(second, next_attempt_at=T0.timestamp() + 3600)  # not due: moved on all the same
    http = FakeDiscordHTTP((404, {"message": "Unknown Webhook", "code": 10015}))
    events = []
    _advance(db, tmp_path, T0, http, emit=events.append)
    row = db.get_submission(first)
    assert (row["status"], row["status_reason"]) == ("manual", statemachine.REASONS["send_gone"])
    assert "doesn't exist" in row["error"]
    other = db.get_submission(second)
    assert (other["status"], other["status_reason"]) == ("manual", statemachine.REASONS["send_gone"])
    assert db.get_destination(dest)["state"] == "gone" and service.load_webhook(tmp_path, dest) is None
    assert {(e["submission_id"], e["status"]) for e in events} == {(first, "manual"), (second, "manual")}
    assert len(http.calls) == 1


@pytest.mark.parametrize("answer, status, reason, dest_state", [
    ((403, {"message": "Missing Permissions", "code": 50013}), "manual", statemachine.REASONS["send_denied"], "denied"),
    ((400, {"message": "Forum", "code": 220001}), "manual", statemachine.REASONS["send_forum"], "connected"),
    ((400, {"message": "Invalid Form Body", "code": 50035}), "failed", queue.REFUSED, "connected"),
])
def test_what_each_refusal_from_discord_leads_to(db, tmp_path, answer, status, reason, dest_state):
    (sub_id,), dest = _ready(db, tmp_path)
    _advance(db, tmp_path, T0, FakeDiscordHTTP(answer))
    row = db.get_submission(sub_id)
    assert (row["status"], row["status_reason"]) == (status, reason)
    assert db.get_destination(dest)["state"] == dest_state
    assert row["error"]


def test_a_read_timeout_may_have_posted_so_it_waits_for_the_user(db, tmp_path):
    (sub_id,), _ = _ready(db, tmp_path)
    http = FakeDiscordHTTP(requests.ReadTimeout("read timed out"))
    _advance(db, tmp_path, T0, http)
    row = db.get_submission(sub_id)
    assert (row["status"], row["sent_by"]) == ("awaiting_confirmation", "")
    assert row["status_reason"] == "Check Discord: it may have been posted"
    _advance(db, tmp_path, T0 + timedelta(hours=1), http)
    assert len(http.calls) == 1


def test_one_send_per_destination_every_two_seconds_and_five_a_pass(db, tmp_path):
    (a, b), _ = _ready(db, tmp_path, n=2)
    others = [_ready(db, tmp_path)[0][0] for _ in range(5)]
    ok = [(200, {"id": f"60000000000000{i:04d}", "channel_id": CHANNEL_ID}) for i in range(10)]
    http = FakeDiscordHTTP(*ok)
    out = _advance(db, tmp_path, T0, http)
    assert (out["sent"], out["more"]) == (5, True)
    assert db.get_submission(b)["status"] == "ready", "its destination posted a moment ago"
    _advance(db, tmp_path, T0 + timedelta(seconds=1), http)
    assert db.get_submission(b)["status"] == "ready"
    _advance(db, tmp_path, T0 + timedelta(seconds=2), http)
    statuses = {db.get_submission(i)["status"] for i in (a, b, *others)}
    assert statuses == {"awaiting_confirmation"} and len(http.calls) == 7


def test_rows_saved_for_later_never_fill_the_send_window(db, tmp_path):
    (due,), dest = _ready(db, tmp_path)
    cid = db.get_submission(due)["campaign_id"]
    for i in range(120):
        db.insert_submission(cid, "held", 40.0 * i, 40.0 * i + 30, "tiktok", status="ready", hold=1,
                             method="discord_webhook", destination_id=dest)
    db.set_submission(due, next_attempt_at=1)  # due, and behind every held one
    http = FakeDiscordHTTP((200, {"id": "600000000000000005", "channel_id": CHANNEL_ID}))
    _advance(db, tmp_path, T0, http)
    assert db.get_submission(due)["status"] == "awaiting_confirmation"


def test_a_destination_that_cant_be_used_sends_nothing(db, tmp_path):
    (no_token,), dest = _ready(db, tmp_path)
    service.wipe_webhook(tmp_path, dest)
    (denied,), other = _ready(db, tmp_path)
    db.set_destination(other, state="denied")
    http = FakeDiscordHTTP()
    _advance(db, tmp_path, T0, http)
    assert (db.get_submission(no_token)["status"], db.get_submission(no_token)["status_reason"]) == (
        "manual", queue.NO_TOKEN)
    assert db.get_submission(denied)["status_reason"] == "The webhook is denied: submit it by hand or pick another"
    assert http.calls == []


def test_a_forum_destination_posts_with_a_thread_title(db, tmp_path):
    dest = webhook_destination(db, tmp_path, is_forum=1)
    (sub_id,), _ = _ready(db, tmp_path, dest=dest, thread_name_template="{title} for {campaign}")
    http = FakeDiscordHTTP((200, {"id": "600000000000000009", "channel_id": CHANNEL_ID}))
    _advance(db, tmp_path, T0, http)
    assert http.calls[0]["json"]["thread_name"].startswith("A great moment for Webhook")
    assert db.get_submission(sub_id)["status"] == "awaiting_confirmation"


def test_without_approval_a_sent_message_is_sent(db, tmp_path):
    (sub_id,), _ = _ready(db, tmp_path, approval_required=0)
    _advance(db, tmp_path, T0, FakeDiscordHTTP((200, {"id": "600000000000000003", "channel_id": CHANNEL_ID})))
    row = db.get_submission(sub_id)
    assert (row["status"], row["sent_by"], row["status_reason"]) == ("sent", "webhook", queue.SENT_IT)


# ---- the user's actions -------------------------------------------------------------------------

def test_editing_a_link_or_method_evaluates_it_again_and_marks_it_its_own(db, tmp_path):
    clip_id = clip(db)
    dest = webhook_destination(db, tmp_path)
    cid = _campaign(db, destination_id=dest)
    row = _attach(db, clip_id, [cid])["created"][0]
    typed = queue.edit(db, row["id"], now=T0, post_url=link("tiktok") + "?is_from_webapp=1")
    assert (typed["status"], typed["post_url"], typed["url_source"]) == ("manual", link("tiktok"), "manual")
    hooked = queue.edit(db, row["id"], now=T0, method="discord_webhook")
    assert (hooked["status"], hooked["method"], json.loads(hooked["overridden"])) == (
        "ready", "discord_webhook", ["method"])
    held = queue.edit(db, row["id"], now=T0, hold=True, message_text="{url} please")
    assert (held["status"], held["hold"], held["message_text"]) == ("ready", 1, "{url} please")
    with pytest.raises(ValueError, match="TikTok"):
        queue.edit(db, row["id"], now=T0, post_url="https://www.youtube.com/shorts/abcdefghijk")
    with pytest.raises(ValueError, match="Unknown fields"):
        queue.edit(db, row["id"], now=T0, status="sent")
    statemachine.apply(db, row["id"], "cancel", now=T0)
    with pytest.raises(statemachine.NotAllowed):
        queue.edit(db, row["id"], now=T0, message_text="too late")


def test_a_users_link_that_another_row_holds_cancels_this_one(db):
    one, two = clip(db), clip(db, start=40.0, end=70.0)
    cid = _campaign(db)
    first = _attach(db, one, [cid], post_url=link("tiktok"))["created"][0]
    second = _attach(db, two, [cid])["created"][0]
    after = queue.edit(db, second["id"], now=T0, post_url=link("tiktok"))
    assert (after["status"], after["status_reason"]) == (
        "cancelled", f"Already submitted to this campaign as #{first['id']}")


def test_approve_submit_retry_and_send_again(db, tmp_path):
    clip_id, publisher = clip(db), FakePublisher(db)
    publisher.publish(clip_id, ["tiktok"], state="published", url=link("tiktok"))
    dest = webhook_destination(db, tmp_path)
    hook = _campaign(db, "Hook", method="discord_webhook", destination_id=dest)
    by_hand = _campaign(db, "By hand")

    reviewed = _attach(db, clip_id, [hook], mode="review")["created"][0]
    assert reviewed["status"] == "review"
    approved = queue.approve(db, reviewed["id"], now=T0)
    assert (approved["status"], approved["next_attempt_at"]) == ("ready", T0.timestamp())

    held = _attach(db, clip_id, [by_hand], mode="later")["created"][0]
    assert (held["status"], held["status_reason"]) == ("ready", "Saved for later")
    submitted = queue.submit(db, held["id"], now=T0)
    assert (submitted["status"], submitted["hold"], submitted["status_reason"]) == (
        "manual", 0, "Submit it in Discord")

    db.set_submission(approved["id"], status="failed", attempts=6, error="Discord had a problem")
    retried = queue.retry(db, approved["id"], now=T0)
    assert (retried["status"], retried["attempts"], retried["error"]) == ("ready", 0, "")

    statemachine.apply(db, approved["id"], "send_start", now=T0)
    statemachine.apply(db, approved["id"], "send_ambiguous", now=T0)
    with pytest.raises(statemachine.NotAllowed, match="send_again"):
        queue.retry(db, approved["id"], now=T0)
    again = queue.retry(db, approved["id"], now=T0, send_again=True)
    assert (again["status"], again["sent_by"]) == ("ready", "")


def test_recording_what_happened_and_cancelling(db, tmp_path):
    clip_id = clip(db)
    cid = _campaign(db)
    row = _attach(db, clip_id, [cid], post_url=link("tiktok"))["created"][0]
    first = queue.opened(db, row["id"], now=T0)
    second = queue.opened(db, row["id"], now=T0 + timedelta(hours=1))
    assert second["opened_at"] == first["opened_at"] == _utc(T0)
    message = "https://discord.com/channels/300000000000000001/200000000000000002/600000000000000007"
    done = queue.record(db, row["id"], "submitted", now=T0, message_link=message)
    assert (done["status"], done["sent_by"], done["discord_message_id"], done["discord_channel_id"]) == (
        "awaiting_confirmation", "user", "600000000000000007", "200000000000000002")
    with pytest.raises(ValueError, match="Copy Message Link"):
        queue.record(db, row["id"], "confirmed", now=T0, message_link="https://discord.com/channels/1")
    confirmed = queue.record(db, row["id"], "confirmed", now=T0, note="Paid in the dashboard")
    assert (confirmed["status"], confirmed["confirmation_note"], confirmed["confirmed_at"]) == (
        "confirmed", "Paid in the dashboard", _utc(T0))

    (sending,), _ = _ready(db, tmp_path)
    statemachine.apply(db, sending, "send_start", now=T0)
    with pytest.raises(statemachine.NotAllowed):
        queue.cancel(db, sending, now=T0)
    with pytest.raises(LookupError):
        queue.cancel(db, 99999, now=T0)


# ---- automation's attach (DESIGN §6.4; the watch's tick step is Phase 6) ------------------------

def test_a_watch_attaches_the_posts_that_went_out_once(db):
    publisher = FakePublisher(db)
    clips = [clip(db, start=40.0 * i, end=40.0 * i + 30) for i in range(3)]
    publisher.publish(clips[0], ["tiktok", "youtube"])
    publisher.publish(clips[1], ["tiktok"], state="skipped")
    publisher.publish(clips[2], ["tiktok"], state="published", url=link("tiktok"))
    cid = _campaign(db, review_first=1)
    youtube_only = _campaign(db, "YouTube only", platforms=["youtube"])
    off = _campaign(db, "Off", enabled=0)
    watch, item = {"id": 3}, {"id": 41, "video_id": "vid", "chosen_clips": ""}

    made = queue.attach_for_watch(db, watch=watch, item=item, campaign_ids=[cid, youtube_only, off], now=T0)
    rows = db.list_submissions(limit=50)
    assert made == 4 == len(rows)
    assert sorted((r["campaign_id"], r["clip_id"], r["platform"]) for r in rows) == sorted([
        (cid, clips[0], "tiktok"), (cid, clips[0], "youtube"), (cid, clips[2], "tiktok"),
        (youtube_only, clips[0], "youtube")])
    assert {(r["origin"], r["origin_ref"], r["hold"], r["status"]) for r in rows} == {
        ("watch", "watch:3/item:41", 0, "waiting_publication")}
    assert {r["review_first"] for r in rows if r["campaign_id"] == cid} == {1}

    statemachine.apply(db, rows[0]["id"], "cancel", now=T0)
    retried = queue.attach_for_watch(db, watch=watch, item=item, campaign_ids=[cid], now=T0)
    assert retried == 0
    assert db.get_submission(rows[0]["id"])["status"] == "cancelled", "automation never reopens"


def test_a_watch_attaches_only_its_chosen_clips_and_what_the_routing_takes(db):
    publisher = FakePublisher(db)
    chosen, other = clip(db), clip(db, start=40.0, end=70.0)
    for clip_id in (chosen, other):
        publisher.publish(clip_id, ["tiktok"])
    sports = _campaign(db, "Sports", filters={"modes": ["sports"]})
    anything = _campaign(db, "Anything")
    item = {"id": 1, "video_id": "vid", "chosen_clips": json.dumps([[0.0, 30.0]])}
    made = queue.attach_for_watch(db, watch={"id": 1}, item=item, campaign_ids=[sports, anything],
                                  job_payload={"sport": "soccer"}, now=T0)
    assert made == 2
    assert {(r["campaign_id"], r["clip_id"]) for r in db.list_submissions()} == {(sports, chosen),
                                                                               (anything, chosen)}
    regular = _campaign(db, "Regular only", filters={"modes": ["regular"]})
    wrong_mode = queue.attach_for_watch(db, watch={"id": 1}, item=item, campaign_ids=[regular], now=T0,
                                        job_payload={"sport": "soccer"})
    assert wrong_mode == 0
    unknown = _campaign(db, "Plugin", filters={"plugins": ["example-dev/demo"]})
    unknown_source = queue.attach_for_watch(db, watch={"id": 1}, item=item, campaign_ids=[unknown], now=T0)
    assert unknown_source == 0, "a filter never matches a clip whose source can't be told"


def test_picking_the_same_webhook_again_once_it_is_fixed_sends_it(db, tmp_path):
    (sub_id,), dest = _ready(db, tmp_path)
    _advance(db, tmp_path, T0, FakeDiscordHTTP((404, {"message": "Unknown Webhook", "code": 10015})))
    assert db.get_submission(sub_id)["status"] == "manual"
    # Settings → Discord → Edit: the same destination gets a working link.
    service.save_webhook(tmp_path, dest, "400000000000000001", "a-new-token")
    db.set_destination(dest, state="connected", last_error="")
    again = queue.edit(db, sub_id, now=T0, destination_id=dest)
    assert (again["status"], json.loads(again["overridden"])) == ("ready", []), "picked, not changed"


def test_a_direct_youtube_upload_is_attached_on_youtube_only(db):
    with pytest.raises(ValueError, match="YouTube only"):
        _attach(db, clip(db), [_campaign(db)], platforms=["tiktok"], provider="youtube")


def test_a_row_that_cant_be_looked_at_is_put_off_and_the_rest_go_on(db, tmp_path, monkeypatch, capsys):
    cid = _campaign(db)
    broken = db.insert_submission(cid, "vid", 0.0, 30.0, "tiktok")
    fine_clip = clip(db, start=40.0, end=70.0)
    FakePublisher(db).publish(fine_clip, ["tiktok"], state="published", url=link("tiktok"))
    fine = db.insert_submission(cid, "vid", 40.0, 70.0, "tiktok", clip_id=fine_clip, provider="woopsocial")
    real = queue.follow

    def follow(db_, row, *, now):
        if row["id"] == broken:
            raise RuntimeError("unreadable row")
        return real(db_, row, now=now)

    monkeypatch.setattr(queue, "follow", follow)
    _advance(db, tmp_path, T0)
    assert db.get_submission(broken)["next_attempt_at"] == T0.timestamp() + 3600
    assert db.get_submission(fine)["status"] == "manual"
    assert "Could not check submission" in capsys.readouterr().out


# ---- published again, closed campaigns, words, typed links ---------------------------------------

def _stamp_at(monkeypatch, moment) -> None:
    """The database's own clock (created_at, uploaded_at, a job's updated_at) at `moment`."""
    local = moment.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    monkeypatch.setattr(core.state, "_now", lambda: local)


FAILED_ON_YOUTUBE = "The post failed on YouTube: publish it again or enter its link"


def test_a_failed_youtube_upload_published_again_resolves_without_attaching_again(db, tmp_path, monkeypatch):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    _stamp_at(monkeypatch, T0 - timedelta(days=1))
    publisher.youtube(clip_id, youtube_id="abcdefghi00")  # an upload of the clip from the day before
    _stamp_at(monkeypatch, T0)
    failed = publisher.youtube(clip_id, done=False)
    db.finish_publish_job(failed, "failed", error="The daily upload quota is used up")
    row = _attach(db, clip_id, [cid], platforms=["youtube"], provider="youtube", publish_job_id=failed)["created"][0]
    assert (row["status"], row["status_reason"], row["post_url"]) == ("manual", FAILED_ON_YOUTUBE, ""), \
        "an upload older than the failure isn't this post"

    _stamp_at(monkeypatch, T0 + timedelta(minutes=10))
    publisher.youtube(clip_id, youtube_id="abcdefghi01")  # published again, with no campaign picked
    events = []
    _advance(db, tmp_path, T0 + timedelta(hours=1, seconds=1), emit=events.append)
    after = db.get_submission(row["id"])
    assert (after["status"], after["post_url"], after["status_reason"]) == (
        "manual", link("youtube", 1), "Submit it in Discord")
    assert [(e["previous"], e["status"], e["message"]) for e in events] == [
        ("manual", "manual", "Ready to submit to Example Campaign in Discord")], "its link is news"


def _uploaded(db, clip_id, job_id, youtube_id="abcdefghi01") -> None:
    """A queued direct upload finishing: its job done, its uploads row."""
    found = db.get_clip(clip_id)
    db.finish_publish_job(job_id, "done", youtube_id=youtube_id)
    db.record_publish(clip_id, {"youtube_id": youtube_id, "video_id": found["video_id"], "start_s": found["start_s"],
                                "end_s": found["end_s"], "privacy": "public", "actual_privacy": "public",
                                "state": "uploaded"})


def test_attaching_again_after_a_failed_post_follows_the_new_publish(db, tmp_path):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    publisher.publish(clip_id, ["youtube"], state="failed", error="Rejected")  # through WoopSocial
    row = _attach(db, clip_id, [cid], platforms=["youtube"], provider="woopsocial")["created"][0]
    assert (row["status"], row["status_reason"]) == ("manual", FAILED_ON_YOUTUBE)
    again = publisher.youtube(clip_id, done=False)  # the YouTube panel: the job is queued, then it attaches
    got = _attach(db, clip_id, [cid], platforms=["youtube"], provider="youtube", publish_job_id=again)
    assert [(r["id"], r["provider"], r["publish_job_id"], r["next_attempt_at"]) for r in got["existing"]] == [
        (row["id"], "youtube", again, 0)]
    _uploaded(db, clip_id, again)
    _advance(db, tmp_path, T0 + timedelta(seconds=1))
    assert db.get_submission(row["id"])["post_url"] == link("youtube", 1)


def test_a_row_waiting_on_its_own_new_upload_is_looked_at_each_minute(db, tmp_path, monkeypatch):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    failed = publisher.youtube(clip_id, done=False)
    db.finish_publish_job(failed, "failed", error="The daily upload quota is used up")
    row = _attach(db, clip_id, [cid], platforms=["youtube"], provider="youtube", publish_job_id=failed)["created"][0]
    assert (row["status"], row["status_reason"]) == ("manual", FAILED_ON_YOUTUBE)
    _due(db, row["id"])
    _advance(db, tmp_path, T0 + timedelta(seconds=1))
    assert db.get_submission(row["id"])["next_attempt_at"] == T0.timestamp() + 1 + 3600, "its job ended: hourly"

    at = T0 + timedelta(minutes=1)
    _stamp_at(monkeypatch, at)
    again = publisher.youtube(clip_id, done=False)  # the YouTube panel: queued, then the attach
    _attach(db, clip_id, [cid], platforms=["youtube"], provider="youtube", publish_job_id=again, at=at)
    _advance(db, tmp_path, at + timedelta(seconds=2))  # the pass notify_campaigns asks for
    after = db.get_submission(row["id"])
    assert (after["publish_job_id"], after["next_attempt_at"]) == (again, (at + timedelta(seconds=62)).timestamp())
    _advance(db, tmp_path, at + timedelta(seconds=62))
    assert db.get_submission(row["id"])["post_url"] == "", "still uploading"

    _stamp_at(monkeypatch, T0 + timedelta(minutes=4))
    _uploaded(db, clip_id, again)
    _advance(db, tmp_path, T0 + timedelta(minutes=4, seconds=30))  # due again at +3:04
    after = db.get_submission(row["id"])
    assert (after["status"], after["post_url"], after["status_reason"]) == (
        "manual", link("youtube", 1), "Submit it in Discord"), "within a minute of the upload, not an hour"


@pytest.mark.parametrize("provider", ["manual", ""])
def test_attaching_again_without_a_publisher_keeps_the_post_it_follows(db, tmp_path, provider):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    publisher.publish(clip_id, ["tiktok"])  # queued on WoopSocial
    row = _attach(db, clip_id, [cid], provider="woopsocial")["created"][0]
    again = _attach(db, clip_id, [cid], provider=provider)["existing"][0]  # and no link typed
    assert (again["provider"], again["next_attempt_at"]) == ("woopsocial", 0)
    publisher.refresh(clip_id, "tiktok", "published", url=link("tiktok", 7))
    _advance(db, tmp_path, T0 + timedelta(minutes=2))
    after = db.get_submission(row["id"])
    assert (after["status"], after["provider"], after["post_url"], after["status_reason"]) == (
        "manual", "woopsocial", link("tiktok", 7), "Submit it in Discord")


def test_a_row_waiting_for_a_typed_link_follows_a_publish_attached_later(db, tmp_path):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    row = _attach(db, clip_id, [cid], provider="manual")["created"][0]  # "I'll enter the link", and didn't
    assert (row["status"], row["status_reason"]) == ("manual", "Enter the post's link")
    publisher.publish(clip_id, ["tiktok"])
    again = _attach(db, clip_id, [cid], provider="woopsocial")["existing"][0]
    assert again["provider"] == "woopsocial"
    publisher.refresh(clip_id, "tiktok", "published", url=link("tiktok", 8))
    _advance(db, tmp_path, T0 + timedelta(minutes=2))
    assert db.get_submission(row["id"])["post_url"] == link("tiktok", 8)


def test_a_post_that_went_live_stays_the_one_followed(db, tmp_path):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    publisher.publish(clip_id, ["youtube"], state="published")  # live on WoopSocial, its link not known yet
    row = _attach(db, clip_id, [cid], platforms=["youtube"], provider="woopsocial")["created"][0]
    assert row["status"] == "waiting_url"
    job = publisher.youtube(clip_id, done=False)
    again = _attach(db, clip_id, [cid], platforms=["youtube"], provider="youtube", publish_job_id=job)["existing"][0]
    assert (again["provider"], again["publish_job_id"], again["next_attempt_at"]) == ("woopsocial", 0, 0)
    db.finish_publish_job(job, "failed", error="The daily upload quota is used up")
    _advance(db, tmp_path, T0 + timedelta(hours=1))
    assert db.get_submission(row["id"])["status"] == "waiting_url"
    _advance(db, tmp_path, T0 + timedelta(hours=25))
    after = db.get_submission(row["id"])
    assert (after["status"], after["provider"], after["status_reason"]) == (
        "manual", "woopsocial", statemachine.REASONS["url_overdue"]), "the day's wait for its link still runs"
    job = publisher.youtube(clip_id, done=False)
    again = _attach(db, clip_id, [cid], platforms=["youtube"], provider="youtube", publish_job_id=job)["existing"][0]
    assert (again["provider"], again["publish_job_id"]) == ("woopsocial", 0), "live, only its link is missing"

    # Live with its link before the row's next look: that link, not the new publish.
    other = clip(db, start=200.0, end=230.0)
    publisher.publish(other, ["youtube"])
    waiting = _attach(db, other, [cid], platforms=["youtube"], provider="woopsocial")["created"][0]
    publisher.refresh(other, "youtube", "published", url=link("youtube", 5))
    job = publisher.youtube(other, done=False)
    again = _attach(db, other, [cid], platforms=["youtube"], provider="youtube", publish_job_id=job)["existing"][0]
    assert (again["status"], again["provider"], again["publish_job_id"]) == ("waiting_publication", "woopsocial", 0)
    _advance(db, tmp_path, T0 + timedelta(hours=26))
    assert db.get_submission(waiting["id"])["post_url"] == link("youtube", 5)


@pytest.mark.parametrize("first", ["queued", "skipped"])
def test_a_row_moved_onto_a_direct_upload_that_fails_is_the_users_with_its_reason(db, tmp_path, first):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    publisher.publish(clip_id, ["youtube"], state=first, error="YouTube isn't connected" if first == "skipped" else "")
    row = _attach(db, clip_id, [cid], platforms=["youtube"], provider="woopsocial")["created"][0]
    assert row["status"] == {"queued": "waiting_publication", "skipped": "manual"}[first]
    job = publisher.youtube(clip_id, done=False)
    again = _attach(db, clip_id, [cid], platforms=["youtube"], provider="youtube", publish_job_id=job)["existing"][0]
    assert (again["provider"], again["publish_job_id"]) == ("youtube", job)
    db.finish_publish_job(job, "failed", error="The daily upload quota is used up")
    _advance(db, tmp_path, T0 + timedelta(seconds=1))
    after = db.get_submission(row["id"])
    assert (after["status"], after["status_reason"], after["error"]) == (
        "manual", FAILED_ON_YOUTUBE, "The daily upload quota is used up")


@pytest.mark.parametrize("first, provider, url", [
    ("woopsocial", "youtube", link("youtube", 1)),  # failed on WoopSocial, then uploaded directly
    ("youtube", "woopsocial", link("youtube", 3)),  # a direct upload failed, then posted through WoopSocial
])
def test_a_youtube_post_published_again_through_the_other_route_resolves(db, tmp_path, monkeypatch,
                                                                         first, provider, url):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    _stamp_at(monkeypatch, T0 - timedelta(days=1))  # a post through the other route before the failure
    if first == "woopsocial":
        publisher.youtube(clip_id, youtube_id="abcdefghi00")
    else:
        publisher.publish(clip_id, ["youtube"], state="published", url=link("youtube", 0))
    _stamp_at(monkeypatch, T0)
    if first == "woopsocial":
        publisher.publish(clip_id, ["youtube"], state="failed", error="Rejected")
        row = _attach(db, clip_id, [cid], platforms=["youtube"], provider="woopsocial")["created"][0]
    else:
        job = publisher.youtube(clip_id, done=False)
        db.finish_publish_job(job, "failed", error="The daily upload quota is used up")
        row = _attach(db, clip_id, [cid], platforms=["youtube"], provider="youtube", publish_job_id=job)["created"][0]
    assert (row["status"], row["post_url"], row["status_reason"]) == ("manual", "", FAILED_ON_YOUTUBE), \
        "a post older than the failure isn't this one"

    _stamp_at(monkeypatch, T0 + timedelta(minutes=10))  # published again, no campaign picked
    if first == "woopsocial":
        publisher.youtube(clip_id, youtube_id="abcdefghi01")
    else:
        publisher.publish(clip_id, ["youtube"], state="published", url=link("youtube", 3))
    _advance(db, tmp_path, T0 + timedelta(hours=1, seconds=1))
    after = db.get_submission(row["id"])
    assert (after["status"], after["provider"], after["post_url"], after["status_reason"]) == (
        "manual", provider, url, "Submit it in Discord")


def test_the_sending_write_comes_before_the_request(db, tmp_path):
    (sub_id,), _ = _ready(db, tmp_path)
    dying = FakeDiscordHTTP(KeyboardInterrupt())  # the process dies while the request is out
    with pytest.raises(KeyboardInterrupt):
        _advance(db, tmp_path, T0, dying)
    assert db.get_submission(sub_id)["status"] == "sending"
    db.recover_sending_submissions(statemachine.REASONS["restart"])  # the restart
    later = FakeDiscordHTTP()
    _advance(db, tmp_path, T0 + timedelta(hours=1), later)
    assert (db.get_submission(sub_id)["status"], later.calls) == ("awaiting_confirmation", [])


def test_archiving_a_campaign_cancels_what_it_has_not_sent_and_posts_nothing(db, tmp_path):
    (due, sending, sent), _ = _ready(db, tmp_path, n=3)
    for event in ("send_start", "send_ok"):
        statemachine.apply(db, sent, event, now=T0)
    statemachine.apply(db, sending, "send_start", now=T0)  # out at the moment it is archived
    cid = db.get_submission(due)["campaign_id"]
    waiting_clip = clip(db, start=200.0, end=230.0)
    FakePublisher(db).publish(waiting_clip, ["tiktok"])
    waiting = _attach(db, waiting_clip, [cid], provider="woopsocial")["created"][0]["id"]

    removed = queue.remove_campaign(db, cid, now=T0)
    assert removed == "archived"
    for sub_id in (due, waiting):
        row = db.get_submission(sub_id)
        assert (row["status"], row["status_reason"]) == ("cancelled", queue.CAMPAIGN_ARCHIVED)
    assert db.get_submission(sent)["status"] == "awaiting_confirmation", "already posted: its outcome is to record"
    statemachine.apply(db, sending, "rate_limited", now=T0, next_attempt_at=T0.timestamp())  # Discord: wait
    http, events = FakeDiscordHTTP(), []
    _advance(db, tmp_path, T0 + timedelta(seconds=10), http, emit=events.append)
    back = db.get_submission(sending)
    assert (http.calls, back["status"], back["status_reason"]) == ([], "cancelled", queue.CAMPAIGN_ARCHIVED), \
        "back unsent: nothing goes out for it, and it never waits for good"
    assert [(e["submission_id"], e["previous"], e["status"]) for e in events] == [(sending, "ready", "cancelled")]
    missing = queue.remove_campaign(db, 999, now=T0)
    assert missing == ""


def test_archiving_cancels_all_of_its_rows_or_none(db, tmp_path, monkeypatch):
    (one, two), _ = _ready(db, tmp_path, n=2)
    cid = db.get_submission(one)["campaign_id"]
    real, cancels = statemachine.apply, []

    def breaks_on_the_second(db_, sub_id, event, **kw):
        if event == "cancel":
            cancels.append(sub_id)
            if len(cancels) == 2:
                raise RuntimeError("disk full")
        return real(db_, sub_id, event, **kw)

    monkeypatch.setattr(statemachine, "apply", breaks_on_the_second)
    with pytest.raises(RuntimeError):
        queue.remove_campaign(db, cid, now=T0)
    assert len(cancels) == 2
    assert (db.get_campaign(cid)["archived"], [db.get_submission(i)["status"] for i in (one, two)]) == (
        0, ["ready", "ready"])
    assert not db.conn.in_transaction

    monkeypatch.setattr(statemachine, "apply", real)
    unused = _campaign(db, "Unused")
    deleted = queue.remove_campaign(db, unused, now=T0)
    assert (deleted, db.get_campaign(unused), db.conn.in_transaction) == ("deleted", None, False)


def test_a_campaign_switched_off_follows_and_posts_nothing_until_it_is_on_again(db, tmp_path):
    (due,), _ = _ready(db, tmp_path)
    cid = db.get_submission(due)["campaign_id"]
    later_clip, publisher = clip(db, start=200.0, end=230.0), FakePublisher(db)
    publisher.publish(later_clip, ["tiktok"])
    waiting = _attach(db, later_clip, [cid], provider="woopsocial")["created"][0]["id"]
    db.set_campaign(cid, enabled=0)
    publisher.refresh(later_clip, "tiktok", "published", url=link("tiktok", 99))
    http = FakeDiscordHTTP((200, {"id": "600000000000000001", "channel_id": CHANNEL_ID}))
    _advance(db, tmp_path, T0 + timedelta(minutes=5), http)
    assert (http.calls, db.get_submission(due)["status"], db.get_submission(waiting)["status"]) == (
        [], "ready", "waiting_publication")
    db.set_campaign(cid, enabled=1)
    _advance(db, tmp_path, T0 + timedelta(minutes=6), http)
    assert (len(http.calls), db.get_submission(due)["status"]) == (1, "awaiting_confirmation")
    assert db.get_submission(waiting)["post_url"] == link("tiktok", 99)


def test_an_archived_campaigns_deadline_still_expires_what_it_kept(db, tmp_path):
    (sub_id,), _ = _ready(db, tmp_path, deadline=_utc(T0 + timedelta(hours=1)))
    db.set_campaign(db.get_submission(sub_id)["campaign_id"], archived=1)
    _advance(db, tmp_path, T0 + timedelta(hours=2))
    assert db.get_submission(sub_id)["status"] == "expired"


def test_reopening_takes_the_campaigns_method_as_it_is_now(db, tmp_path):
    dest = webhook_destination(db, tmp_path)
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], state="published", url=link("tiktok"))
    cid = _campaign(db, method="discord_webhook", destination_id=dest)
    row = _attach(db, clip_id, [cid], mode="later")["created"][0]
    assert (row["status"], row["hold"], row["method"]) == ("ready", 1, "discord_webhook")
    queue.cancel(db, row["id"], now=T0)
    before = db.get_campaign(cid)
    db.set_campaign(cid, method="discord_assisted", destination_id=0)
    updated = queue.reapply_campaign(db, cid, before, now=T0)
    assert updated == 0, "a closed row is left as it is"

    again = _attach(db, clip_id, [cid], reopen_rows=True)["created"][0]
    assert (again["id"], again["method"], again["destination_id"], again["hold"]) == (
        row["id"], "discord_assisted", 0, 0), "after publishing: no longer saved for later"
    assert (again["status"], again["status_reason"]) == ("manual", "Submit it in Discord")
    http = FakeDiscordHTTP()
    _advance(db, tmp_path, T0 + timedelta(seconds=30), http)
    assert http.calls == []

    queue.cancel(db, row["id"], now=T0)
    db.set_submission(row["id"], method="website", overridden=json.dumps(["method"]))
    own = _attach(db, clip_id, [cid], reopen_rows=True)["created"][0]
    assert (own["method"], own["destination_id"]) == ("website", 0), "a method of its own stays"


@pytest.mark.parametrize("change, check, reason", [
    ({"extra_values": {"goal": ""}}, "fields", "Fill in Goal"),
    ({"message_text": "{url} " + "x" * 1990}, "length", "longer than Discord's"),
])
def test_words_changed_after_their_check_go_to_review_instead_of_out(db, tmp_path, change, check, reason):
    dest = webhook_destination(db, tmp_path)
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], state="published", url=link("tiktok"))
    goal = [{"name": "goal", "label": "Goal", "default": "", "required": True}]
    cid = _campaign(db, method="discord_webhook", destination_id=dest, message_template="{url} goal {goal}",
                    extra_fields=json.dumps(goal))
    row = _attach(db, clip_id, [cid], mode="later")["created"][0]
    assert (row["status"], row["status_reason"]) == ("review", "Fill in Goal")
    queue.edit(db, row["id"], now=T0, extra_values={"goal": "1000"})
    approved = queue.approve(db, row["id"], now=T0)
    assert (approved["status"], approved["hold"]) == ("ready", 1)

    queue.edit(db, row["id"], now=T0, **change)
    queue.submit(db, row["id"], now=T0)
    http, events = FakeDiscordHTTP((200, {"id": "600000000000000001", "channel_id": CHANNEL_ID})), []
    _advance(db, tmp_path, T0 + timedelta(seconds=5), http, emit=events.append)
    after = db.get_submission(row["id"])
    assert (after["status"], http.calls) == ("review", [])
    assert reason in after["status_reason"] and _checks(after)[check] == "fail"
    assert [(e["previous"], e["status"]) for e in events] == [("ready", "review")]


def test_a_link_typed_for_a_post_already_attached_is_taken_by_its_row(db):
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], state="published")  # live, the publisher has no link
    cid = _campaign(db)
    first = _attach(db, clip_id, [cid], provider="woopsocial")["created"][0]
    assert (first["status"], first["post_url"]) == ("waiting_url", "")

    got = _attach(db, clip_id, [cid], mode="now", post_url=link("tiktok"))
    assert (got["created"], got["refused"], [r["id"] for r in got["existing"]]) == ([], [], [first["id"]])
    row = got["existing"][0]
    assert (row["status"], row["post_url"], row["url_source"], row["status_reason"]) == (
        "manual", link("tiktok"), "manual", "Submit it in Discord")
    same = _attach(db, clip_id, [cid], post_url=link("tiktok"))
    assert (same["refused"], [r["id"] for r in same["existing"]]) == ([], [first["id"]]), \
        "its own link is no duplicate of itself"

    queue.record(db, first["id"], "submitted", now=T0)
    other = _attach(db, clip_id, [cid], post_url=link("tiktok", 2))
    assert [r["reason"] for r in other["refused"]] == [
        f"It is already in this campaign as #{first['id']} (Awaiting confirmation)"]
    assert db.get_submission(first["id"])["post_url"] == link("tiktok")


def test_a_manual_row_that_finds_its_link_is_announced(db, tmp_path):
    clip_id, publisher, cid = clip(db), FakePublisher(db), _campaign(db)
    publisher.publish(clip_id, ["tiktok"], state="failed", error="Rejected by TikTok")
    row = _attach(db, clip_id, [cid], provider="woopsocial")["created"][0]
    assert (row["status"], row["post_url"]) == ("manual", "")
    publisher.refresh(clip_id, "tiktok", "published", url=link("tiktok"))  # published again
    events = []
    _advance(db, tmp_path, T0 + timedelta(hours=1, seconds=1), emit=events.append)
    assert [(e["submission_id"], e["previous"], e["status"], e["to_submit"], e["message"]) for e in events] == [
        (row["id"], "manual", "manual", True, "Ready to submit to Example Campaign in Discord")]
    _advance(db, tmp_path, T0 + timedelta(hours=3), emit=events.append)
    assert len(events) == 1, "said once"


@pytest.mark.parametrize("method, status, reason", [
    ("discord_assisted", "manual", "Submit it in Discord"), ("discord_webhook", "ready", ""),
])
def test_letting_go_of_a_row_saved_for_later_submits_it(db, tmp_path, method, status, reason):
    dest = webhook_destination(db, tmp_path) if method == "discord_webhook" else 0
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], state="published", url=link("tiktok"))
    row = _attach(db, clip_id, [_campaign(db, method=method, destination_id=dest)], mode="later")["created"][0]
    assert (row["status"], row["hold"]) == ("ready", 1)
    after = queue.edit(db, row["id"], now=T0, hold=False)
    assert (after["status"], after["hold"], after["status_reason"]) == (status, 0, reason)
    if method == "discord_webhook":
        assert after["next_attempt_at"] == T0.timestamp(), "due now"


def test_a_webhook_picked_for_one_row_is_its_own(db, tmp_path):
    dest = webhook_destination(db, tmp_path)
    other = db.insert_destination(label="Other hook", state="connected")
    row = _attach(db, clip(db), [_campaign(db, method="discord_webhook", destination_id=dest)])["created"][0]
    after = queue.edit(db, row["id"], now=T0, destination_id=other)
    assert (after["destination_id"], json.loads(after["overridden"])) == (other, ["destination_id"])


def test_a_move_to_a_campaign_that_doesnt_exist_is_not_found(db):
    row = _attach(db, clip(db), [_campaign(db)], post_url=link("tiktok"))["created"][0]
    before = dict(db.get_submission(row["id"]))
    with pytest.raises(LookupError, match="No campaign #999"):
        queue.move(db, row["id"], 999, now=T0)
    assert dict(db.get_submission(row["id"])) == before
