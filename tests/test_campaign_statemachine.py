"""The submission state machine, table-driven from DESIGN §4.2.

Every event is tried from every status: where the table allows it the row
moves to the expected status, everywhere else NotAllowed is raised and the
row is left exactly as it was. Writes are compare-and-set on the status, so of
two changes racing for one row only the first lands.
"""

from datetime import datetime, timezone

import pytest

from campaigns import model, statemachine
from campaigns.statemachine import Conflict, NotAllowed, apply
from core.state import StateDB

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
STAMP = "2026-10-01T12:00:00Z"
URL = "https://www.tiktok.com/@example/video/7300000000000000001"

PRE_SEND = {"waiting_publication", "waiting_url", "ready", "review", "manual"}
EDITABLE = PRE_SEND | {"failed"}

# DESIGN §4.2, written out again here rather than read from the module: event
# -> (the statuses it may start from, where it leads for a webhook row whose
# campaign requires approval, the `to=` the caller gives when it must).
TABLE = {
    "published_no_url": ({"waiting_publication"}, "waiting_url", ""),
    "post_failed": ({"waiting_publication"}, "manual", ""),
    "post_skipped": ({"waiting_publication"}, "manual", ""),
    "url_found": ({"waiting_publication", "waiting_url", "manual"}, "review", "review"),
    "url_overdue": ({"waiting_url"}, "manual", ""),
    "stale": ({"waiting_publication", "waiting_url"}, "manual", ""),
    "set_url": ({"waiting_publication", "waiting_url", "review", "manual", "failed"}, "ready", "ready"),
    "approve": ({"review"}, "ready", ""),
    "submit": ({"ready"}, "ready", ""),
    "send_start": ({"ready"}, "sending", ""),
    "destination_unusable": ({"ready"}, "manual", ""),
    "send_ok": ({"sending"}, "awaiting_confirmation", ""),
    "rate_limited": ({"sending"}, "ready", ""),
    "send_retry": ({"sending"}, "ready", ""),
    "send_ambiguous": ({"sending"}, "awaiting_confirmation", ""),
    "send_gone": ({"sending"}, "manual", ""),
    "send_denied": ({"sending"}, "manual", ""),
    "send_forum": ({"sending"}, "manual", ""),
    "send_refused": ({"sending"}, "failed", ""),
    "sending_stale": ({"sending"}, "awaiting_confirmation", ""),
    "restart": ({"sending"}, "awaiting_confirmation", ""),
    "user_submitted": ({"manual", "ready", "review", "failed", "awaiting_confirmation"},
                       "awaiting_confirmation", ""),
    "confirmed": ({"sent", "awaiting_confirmation", "manual"}, "confirmed", ""),
    "rejected": ({"sent", "awaiting_confirmation"}, "rejected", ""),
    "retry": ({"failed"}, "ready", ""),
    "send_again": ({"awaiting_confirmation"}, "ready", ""),
    "expire": (PRE_SEND, "expired", ""),
    "cancel": (set(model.STATUSES) - {"sending", "confirmed", "rejected", "expired", "cancelled"},
               "cancelled", ""),
    "move": (EDITABLE, "cancelled", ""),
    "edit": (EDITABLE, None, ""),               # None: stays where it is
    "campaign_changed": (EDITABLE, None, ""),
    "reopen": ({"cancelled", "expired", "failed"}, "waiting_publication", ""),
}

# What each event needs of the row besides its status (DESIGN §4.2's brackets).
SETUP = {"submit": {"hold": 1}}


def _row(db, status, *, approval=1, **fields):
    campaign_id = db.insert_campaign("Example Campaign", approval_required=approval,
                                     method="discord_webhook", destination_id=1)
    sub_id = db.insert_submission(campaign_id, "vid", 0.0, 30.0, "tiktok", method="discord_webhook",
                                  sent_by="webhook")
    db.set_submission(sub_id, status=status, **fields)
    return sub_id


def test_the_table_here_and_the_modules_name_the_same_events():
    assert set(TABLE) == set(statemachine.TRANSITIONS)
    for event, rule in statemachine.TRANSITIONS.items():
        assert set(rule.sources) == TABLE[event][0], event
        assert set(rule.sources) <= set(model.STATUSES), event
        assert rule.who in ("W", "U", "A", "R"), event


@pytest.mark.parametrize("event", sorted(TABLE))
@pytest.mark.parametrize("status", model.STATUSES)
def test_every_event_from_every_status(db, event, status):
    sources, target, to = TABLE[event]
    sub_id = _row(db, status, **SETUP.get(event, {}))
    before = dict(db.get_submission(sub_id))
    if status in sources:
        after = apply(db, sub_id, event, now=NOW, to=to)
        assert after["status"] == (target or status)
    else:
        with pytest.raises(NotAllowed):
            apply(db, sub_id, event, now=NOW, to=to)
        assert dict(db.get_submission(sub_id)) == before


# ---- what the row must be besides its status ---------------------------------------------------

@pytest.mark.parametrize("event, status, fields, problem", [
    ("submit", "ready", {"hold": 0}, "saved for later"),
    ("send_start", "ready", {"method": "discord_assisted"}, "webhook"),
    ("send_start", "ready", {"hold": 1}, "saved for later"),
    ("send_start", "ready", {"next_attempt_at": NOW.timestamp() + 60}, "due"),
    ("destination_unusable", "ready", {"method": "website"}, "webhook"),
    ("url_found", "manual", {"post_url": URL}, "already has its link"),
    ("send_again", "awaiting_confirmation", {"sent_by": "user"}, "yourself"),
    ("campaign_changed", "manual", {"opened_at": STAMP}, "already opened"),
])
def test_guards_beyond_the_status(db, event, status, fields, problem):
    sub_id = _row(db, status, **fields)
    with pytest.raises(NotAllowed, match=problem):
        apply(db, sub_id, event, now=NOW, to="ready" if event == "url_found" else "")
    assert db.get_submission(sub_id)["status"] == status
    assert statemachine.allowed(db.get_submission(sub_id), event, now=NOW) is False


def test_a_due_send_starts_and_is_stamped(db):
    sub_id = _row(db, "ready", next_attempt_at=NOW.timestamp())
    after = apply(db, sub_id, "send_start", now=NOW)
    assert (after["status"], after["sending_started_at"]) == ("sending", NOW.timestamp())


# ---- where an event leads ------------------------------------------------------------------------

@pytest.mark.parametrize("method, approved, submitted", [
    ("discord_webhook", "ready", "ready"),
    ("discord_assisted", "manual", "manual"),
    ("website", "manual", "manual"),
])
def test_approving_and_submitting_follow_the_method(db, method, approved, submitted):
    # Its next check had been pushed an hour out while it waited; approved,
    # a webhook row is due now, as a submitted one is.
    review = _row(db, "review", method=method, next_attempt_at=NOW.timestamp() + 3600)
    after = apply(db, review, "approve", now=NOW)
    assert after["status"] == approved
    if approved == "ready":
        assert after["next_attempt_at"] == NOW.timestamp()
        assert statemachine.allowed(after, "send_start", now=NOW)
    held = _row(db, "ready", method=method, hold=1, next_attempt_at=9e9)
    after = apply(db, held, "submit", now=NOW)
    assert (after["status"], after["hold"]) == (submitted, 0)
    if submitted == "ready":
        assert after["next_attempt_at"] == NOW.timestamp()


@pytest.mark.parametrize("approval, status", [(1, "awaiting_confirmation"), (0, "sent")])
def test_a_sent_message_waits_for_the_campaign_only_when_it_approves_entries(db, approval, status):
    sending = _row(db, "sending", approval=approval)
    after = apply(db, sending, "send_ok", now=NOW, discord_message_id="600000000000000006")
    assert (after["status"], after["sent_by"], after["sent_at"]) == (status, "webhook", STAMP)
    assert after["discord_message_id"] == "600000000000000006"

    manual = _row(db, "manual", approval=approval, sent_by="")
    after = apply(db, manual, "user_submitted", now=NOW)
    assert (after["status"], after["sent_by"], after["sent_at"]) == (status, "user", STAMP)


def test_an_ambiguous_send_or_a_restart_leaves_nobody_as_the_sender(db):
    for event in ("send_ambiguous", "sending_stale", "restart"):
        after = apply(db, _row(db, "sending"), event, now=NOW)
        assert (after["status"], after["sent_by"]) == ("awaiting_confirmation", "")
        assert after["status_reason"] == statemachine.REASONS[event]
        assert model.to_submit(after) and not model.outcome_to_record(after)


def test_an_evaluation_names_its_outcome(db):
    sub_id = _row(db, "waiting_url")
    with pytest.raises(ValueError, match="needs to="):
        apply(db, sub_id, "url_found", now=NOW)
    with pytest.raises(NotAllowed):
        apply(db, sub_id, "url_found", now=NOW, to="sent")
    for outcome in statemachine.EVALUATED:
        sub_id = _row(db, "waiting_url")
        after = apply(db, sub_id, "url_found", now=NOW, to=outcome, post_url=URL)
        assert after["status"] == outcome


def test_an_edit_stays_put_unless_its_evaluation_moves_it(db):
    sub_id = _row(db, "review", status_reason="Fill in the views goal")
    after = apply(db, sub_id, "edit", now=NOW, message_text="new words")
    assert (after["status"], after["message_text"], after["status_reason"]) == (
        "review", "new words", "Fill in the views goal")
    after = apply(db, sub_id, "edit", now=NOW, to="ready")
    assert after["status"] == "ready"
    with pytest.raises(NotAllowed):
        apply(db, sub_id, "edit", now=NOW, to=statemachine.SAME)
    with pytest.raises(NotAllowed):
        apply(db, sub_id, "edit", now=NOW, to="sent")


def test_the_status_comes_from_the_event_only(db):
    sub_id = _row(db, "review")
    with pytest.raises(ValueError, match="status"):
        apply(db, sub_id, "approve", now=NOW, status="sent")
    with pytest.raises(ValueError, match="unknown"):
        apply(db, sub_id, "teleport", now=NOW)
    with pytest.raises(NotAllowed, match="doesn't exist"):
        apply(db, 999, "approve", now=NOW)


# ---- what an event writes -----------------------------------------------------------------------

def test_the_first_sight_of_a_published_post_is_stamped_once(db):
    sub_id = _row(db, "waiting_publication")
    after = apply(db, sub_id, "published_no_url", now=NOW)
    assert after["posted_at"] == STAMP
    sub_id = _row(db, "waiting_publication", posted_at="2026-09-30T00:00:00Z")
    after = apply(db, sub_id, "published_no_url", now=NOW)
    assert after["posted_at"] == "2026-09-30T00:00:00Z"


def test_a_reason_given_wins_over_the_events_own(db):
    sub_id = _row(db, "waiting_publication")
    reason = "TikTok isn't connected on WoopSocial, so nothing was posted there"
    after = apply(db, sub_id, "post_skipped", now=NOW, status_reason=reason)
    assert after["status_reason"] == reason
    sub_id = _row(db, "waiting_url", status_reason="old")
    after = apply(db, sub_id, "url_overdue", now=NOW)
    assert after["status_reason"] == statemachine.REASONS["url_overdue"]
    sub_id = _row(db, "manual", status_reason="old")
    after = apply(db, sub_id, "confirmed", now=NOW)
    assert after["status_reason"] == ""


def test_a_retry_starts_counting_again_and_is_due_now(db):
    sub_id = _row(db, "failed", attempts=5, error="500 Server Error", next_attempt_at=9e9)
    after = apply(db, sub_id, "retry", now=NOW)
    assert (after["status"], after["attempts"], after["error"], after["next_attempt_at"]) == (
        "ready", 0, "", NOW.timestamp())


def test_a_429_keeps_the_attempts_and_takes_the_wait_given(db):
    sub_id = _row(db, "sending", attempts=2)
    after = apply(db, sub_id, "rate_limited", now=NOW, next_attempt_at=NOW.timestamp() + 31)
    assert (after["status"], after["attempts"], after["next_attempt_at"]) == ("ready", 2, NOW.timestamp() + 31)
    sub_id = _row(db, "sending", attempts=5)
    after = apply(db, sub_id, "send_retry", now=NOW, to="failed", attempts=6)
    assert after["status"] == "failed"


def test_reopening_starts_over_on_the_same_post_and_keeps_the_users_words(db):
    sub_id = _row(db, "cancelled", post_url=URL, url_source="manual", attempts=3, error="x",
                  sent_at=STAMP, opened_at=STAMP, checks='[{"name": "public"}]',
                  message_text="my words", arg_values='{"link": "x"}', overridden='["method"]')
    after = apply(db, sub_id, "reopen", now=NOW)
    assert after["status"] == "waiting_publication"
    assert (after["post_url"], after["url_source"], after["attempts"], after["error"]) == ("", "", 0, "")
    assert (after["sent_at"], after["opened_at"], after["checks"]) == ("", "", "[]")
    assert (after["message_text"], after["arg_values"], after["overridden"]) == (
        "my words", '{"link": "x"}', '["method"]')
    assert after["next_attempt_at"] == NOW.timestamp()


def test_confirmed_is_stamped(db):
    sub_id = _row(db, "sent")
    after = apply(db, sub_id, "confirmed", now=NOW, confirmation_note="Approved in the dashboard")
    assert (after["status"], after["confirmed_at"], after["confirmation_note"]) == (
        "confirmed", STAMP, "Approved in the dashboard")


# ---- two changes racing for one row ------------------------------------------------------------

def test_a_change_made_meanwhile_wins_and_the_late_one_writes_nothing(db, tmp_path):
    sub_id = _row(db, "ready")
    other = StateDB(tmp_path / "state.db")
    read = db.get_submission

    def read_then_lose_the_race(row_id):
        row = read(row_id)
        other.set_submission(row_id, expect=("ready",), status="cancelled", status_reason="by the user")
        db.get_submission = read
        return row

    db.get_submission = read_then_lose_the_race
    try:
        with pytest.raises(Conflict):
            apply(db, sub_id, "send_start", now=NOW)
    finally:
        other.close()
    row = db.get_submission(sub_id)
    assert (row["status"], row["status_reason"], row["sending_started_at"]) == ("cancelled", "by the user", 0)


@pytest.mark.parametrize("event, status, setup, meanwhile", [
    # The user saves a due webhook row for later just as its send starts.
    ("send_start", "ready", {"next_attempt_at": NOW.timestamp()}, {"hold": 1}),
    # The user opens the Discord channel just as the campaign switches to a
    # webhook: the by-hand submission must not also be sent.
    ("campaign_changed", "manual", {"method": "discord_assisted"}, {"opened_at": STAMP}),
    # Another pass found the link first.
    ("url_found", "manual", {}, {"post_url": URL}),
])
def test_a_guarded_column_changed_meanwhile_stops_the_event(db, tmp_path, event, status, setup, meanwhile):
    sub_id = _row(db, status, **setup)
    other = StateDB(tmp_path / "state.db")
    read = db.get_submission

    def read_then_lose_the_race(row_id):
        row = read(row_id)
        other.set_submission(row_id, **meanwhile)
        db.get_submission = read
        return row

    db.get_submission = read_then_lose_the_race
    try:
        with pytest.raises(Conflict):
            apply(db, sub_id, event, now=NOW, to="ready" if event != "send_start" else "",
                  method="discord_webhook")
    finally:
        other.close()
    row = db.get_submission(sub_id)
    assert row["status"] == status
    assert {k: row[k] for k in meanwhile} == meanwhile


# ---- the statuses and how they are shown ----------------------------------------------------------

def test_the_owners_thirteen_statuses_in_his_words():
    assert model.LABELS == {
        "waiting_publication": "Waiting for publication", "waiting_url": "Waiting for public URL",
        "ready": "Ready to submit", "review": "Awaiting review", "manual": "Manual action required",
        "sending": "Sending", "sent": "Message sent", "awaiting_confirmation": "Awaiting confirmation",
        "confirmed": "Confirmed", "rejected": "Rejected", "failed": "Failed", "expired": "Expired",
        "cancelled": "Cancelled",
    }
    assert tuple(model.LABELS) == model.STATUSES
    assert set(model.PRE_SEND) == PRE_SEND
    assert set(model.TERMINAL) == {"confirmed", "rejected", "expired", "cancelled"}


# (status, hold, sent_by) -> (to_submit, outcome_to_record, group, label)
SHOWN = [
    ("waiting_publication", 0, "", False, False, "waiting", "Waiting for publication"),
    ("waiting_url", 0, "", False, False, "waiting", "Waiting for public URL"),
    ("ready", 0, "", False, False, "waiting", "Ready to submit"),
    ("ready", 1, "", True, False, "to_submit", "Ready to submit"),
    ("review", 0, "", True, False, "to_submit", "Awaiting review"),
    ("manual", 0, "", True, False, "to_submit", "Manual action required"),
    ("sending", 0, "", False, False, "waiting", "Sending"),
    ("sent", 0, "webhook", False, False, "sent", "Message sent"),
    ("sent", 0, "user", False, False, "sent", "You submitted it"),
    ("awaiting_confirmation", 0, "", True, False, "to_submit", "Awaiting confirmation"),
    ("awaiting_confirmation", 0, "webhook", False, True, "outcome_to_record", "Awaiting confirmation"),
    ("awaiting_confirmation", 0, "user", False, True, "outcome_to_record", "Awaiting confirmation"),
    ("confirmed", 0, "user", False, False, "done", "Confirmed"),
    ("rejected", 0, "webhook", False, False, "done", "Rejected"),
    ("failed", 0, "", True, False, "to_submit", "Failed"),
    ("expired", 0, "", False, False, "done", "Expired"),
    ("cancelled", 0, "", False, False, "done", "Cancelled"),
]


@pytest.mark.parametrize("status, hold, sent_by, submit, record, group, label", SHOWN)
def test_what_needs_the_user_and_how_a_row_is_shown(db, status, hold, sent_by, submit, record, group, label):
    plain = {"status": status, "hold": hold, "sent_by": sent_by}
    sub_id = _row(db, status, hold=hold, sent_by=sent_by)
    for row in (plain, db.get_submission(sub_id)):
        assert model.to_submit(row) is submit
        assert model.outcome_to_record(row) is record
        assert model.group(row) == group
        assert model.status_label(row) == label
    assert group in model.GROUPS


def test_every_status_is_shown_somewhere():
    assert {s for s, *_ in SHOWN} == set(model.STATUSES)
