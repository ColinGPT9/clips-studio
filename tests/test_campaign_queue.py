"""Evaluating a submission once its post's link is known (DESIGN §5.3).

The link is checked against the campaign's other rows first: one link is
submitted to one campaign once. Then everything known with it is saved, every
check runs, and the verdict decides: expired, stopped for review, done by
hand, or (by webhook only) ready to send.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from campaigns import queue, statemachine
from campaigns.urls import UPLOAD_GONE, Resolution, validate

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
