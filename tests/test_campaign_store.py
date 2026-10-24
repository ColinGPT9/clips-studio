"""The campaign tables: created on every start, written only through column
allowlists, and keyed so a post is submitted to a campaign once.

A submission's identity (campaign, video, window, platform) is its dedupe key:
a retried attach finds the row already there instead of making a second one,
and nothing may move a row onto another post afterwards. A second layer, the
partial unique index on (campaign, link), catches two identities that turn
out to be the same post.
"""

import sqlite3

import pytest

from campaigns import model
from core.state import (
    CAMPAIGN_COLUMNS,
    DESTINATION_COLUMNS,
    SUBMISSION_COLUMNS,
    SUBMISSION_IDENTITY,
    StateDB,
)

URL = "https://www.tiktok.com/@example/video/7300000000000000001"
STAMPS = {"id", "created_at", "updated_at"}


def _columns(db, table):
    return {r["name"] for r in db.conn.execute(f"PRAGMA table_info({table})")}


def _indexes(db, table):
    return {r["name"] for r in db.conn.execute(f"PRAGMA index_list({table})")}


def _campaign(db, name="Example Campaign", **fields):
    return db.insert_campaign(name, **fields)


def _sub(db, campaign_id, platform="tiktok", start=0.0, end=30.0, **fields):
    return db.insert_submission(campaign_id, "vid", start, end, platform, **fields)


# ---- the schema ----------------------------------------------------------------------------

def test_the_tables_and_indexes_exist_on_a_fresh_database(db):
    for table in ("campaigns", "discord_destinations", "campaign_submissions"):
        assert _columns(db, table), table
    assert {"idx_campaign_submissions_url", "idx_campaign_submissions_status",
            "idx_campaign_submissions_clip"} <= _indexes(db, "campaign_submissions")


def test_a_database_from_before_campaigns_gets_them_when_opened_again(tmp_path):
    path = tmp_path / "state.db"
    first = StateDB(path)
    first.upsert_video("vid", title="kept")
    for table in ("campaign_submissions", "discord_destinations", "campaigns"):
        first.conn.execute(f"DROP TABLE {table}")
    first.conn.commit()
    first.close()

    again = StateDB(path)
    try:
        assert _columns(again, "campaigns") and _columns(again, "campaign_submissions")
        assert "idx_campaign_submissions_url" in _indexes(again, "campaign_submissions")
        title = again.conn.execute("SELECT title FROM videos WHERE video_id = 'vid'").fetchone()[0]
        assert title == "kept"
    finally:
        again.close()


def test_the_allowlists_name_every_column_but_the_fixed_ones(db):
    assert _columns(db, "campaigns") - STAMPS == CAMPAIGN_COLUMNS
    assert _columns(db, "discord_destinations") - STAMPS == DESTINATION_COLUMNS
    assert _columns(db, "campaign_submissions") - STAMPS - set(SUBMISSION_IDENTITY) == SUBMISSION_COLUMNS
    # The campaign form edits every column but `archived`, which only delete sets.
    assert set(model.EDITABLE) == CAMPAIGN_COLUMNS - {"archived"}


def test_no_campaign_table_has_a_column_for_a_secret(db):
    for table in ("campaigns", "discord_destinations", "campaign_submissions"):
        assert not {c for c in _columns(db, table) if "token" in c or "secret" in c}, table


def test_the_secrets_names_in_the_credential_store():
    assert model.OAUTH_SECRET == "discord_oauth"
    assert model.webhook_secret_name(3) == "discord_webhook_3"
    assert model.webhook_secret_name("12") == "discord_webhook_12"
    with pytest.raises(ValueError):
        model.webhook_secret_name("../other")


# ---- allowlists -------------------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda db, c, s, d: db.insert_campaign("x", bogus=1),
    lambda db, c, s, d: db.set_campaign(c, bogus=1),
    lambda db, c, s, d: db.insert_destination(bogus=1),
    lambda db, c, s, d: db.set_destination(d, bogus=1),
    lambda db, c, s, d: db.insert_submission(c, "vid", 0, 30, "tiktok", bogus=1),
    lambda db, c, s, d: db.set_submission(s, bogus=1),
])
def test_unknown_columns_are_refused(db, call):
    campaign_id = _campaign(db)
    sub_id = _sub(db, campaign_id)
    dest_id = db.insert_destination(label="Example destination")
    with pytest.raises(ValueError):
        call(db, campaign_id, sub_id, dest_id)


@pytest.mark.parametrize("column", SUBMISSION_IDENTITY)
def test_a_submissions_identity_never_changes(db, column):
    campaign_id = _campaign(db)
    sub_id = _sub(db, campaign_id)
    before = dict(db.get_submission(sub_id))
    with pytest.raises(ValueError, match="identity"):
        db.set_submission(sub_id, **{column: "x"})
    assert dict(db.get_submission(sub_id)) == before


def test_campaign_crud_stamps_updated_at_and_takes_json_values(db):
    campaign_id = _campaign(db, platforms=["tiktok", "youtube"], filters={"modes": ["sports"]})
    row = db.get_campaign(campaign_id)
    assert row["platforms"] == '["tiktok", "youtube"]'
    assert row["method"] == "discord_assisted" and row["review_first"] == 0
    assert row["approval_required"] == 1 and row["message_template"] == "{url}"
    db.conn.execute("UPDATE campaigns SET updated_at = '2000-01-01T00:00:00' WHERE id = ?", (campaign_id,))
    db.set_campaign(campaign_id, name="Renamed")
    row = db.get_campaign(campaign_id)
    assert row["name"] == "Renamed" and row["updated_at"] != "2000-01-01T00:00:00"


def test_deleting_a_campaign_with_submissions_archives_it(db):
    unused = _campaign(db, "Unused")
    used = _campaign(db, "Used")
    _sub(db, used)

    results = [db.delete_campaign(unused), db.delete_campaign(used), db.delete_campaign(999)]
    assert results == ["deleted", "archived", ""]
    assert db.get_campaign(unused) is None
    assert db.get_campaign(used)["archived"] == 1
    assert [r["id"] for r in db.list_campaigns()] == []
    assert [r["id"] for r in db.list_campaigns(archived=True)] == [used]


def test_destination_crud(db):
    dest_id = db.insert_destination(label="Example webhook", webhook_id="100000000000000001")
    assert db.get_destination(dest_id)["state"] == "connected"
    db.set_destination(dest_id, state="gone", last_error="Unknown Webhook")
    assert db.get_destination(dest_id)["state"] == "gone"
    assert [r["id"] for r in db.list_destinations()] == [dest_id]
    removed = [db.delete_destination(dest_id), db.delete_destination(dest_id)]
    assert removed == [True, False]


# ---- the dedupe key ---------------------------------------------------------------------------

def test_the_same_post_is_attached_to_a_campaign_once(db):
    campaign_id = _campaign(db)
    first = _sub(db, campaign_id, start=12.341, end=40.0)
    assert first is not None
    # A retried attach (rounded to the clip's 2 dp) finds the row already there.
    again = _sub(db, campaign_id, start=12.34, end=40.0)
    assert again is None
    assert db.find_submission(campaign_id, "vid", 12.344, 40.0, "tiktok")["id"] == first
    # Another platform, window or campaign is another submission.
    others = [_sub(db, campaign_id, platform="youtube", start=12.34, end=40.0),
              _sub(db, campaign_id, start=12.34, end=41.0),
              _sub(db, _campaign(db, "Other"), start=12.34, end=40.0)]
    assert None not in others and len(set(others)) == 3


def test_only_an_identity_clash_is_ignored(db):
    campaign_id = _campaign(db)
    with pytest.raises(sqlite3.IntegrityError):
        _sub(db, campaign_id, clip_title=None)


def test_a_new_row_never_carries_a_url(db):
    campaign_id = _campaign(db)
    with pytest.raises(ValueError, match="post_url"):
        _sub(db, campaign_id, post_url=URL)
    assert db.list_submissions() == []


def test_one_link_is_submitted_to_one_campaign_once_until_cancelled(db):
    campaign_id = _campaign(db)
    first = _sub(db, campaign_id, start=0, end=30)
    second = _sub(db, campaign_id, start=0, end=31)
    took = db.set_submission(first, post_url=URL)
    assert took

    with pytest.raises(sqlite3.IntegrityError):
        db.set_submission(second, post_url=URL)
    assert db.active_submission_with_url(campaign_id, URL)["id"] == first
    assert db.active_submission_with_url(campaign_id, URL, exclude_id=first) is None

    # The same link may go to another campaign.
    other = _sub(db, _campaign(db, "Other"))
    took = db.set_submission(other, post_url=URL)
    assert took

    for status in ("cancelled", "expired"):
        db.set_submission(first, status=status)
        assert db.active_submission_with_url(campaign_id, URL) is None
        took = db.set_submission(second, post_url=URL)
        assert took
        db.set_submission(second, post_url="")
    # A failed row still holds its link.
    db.set_submission(first, status="failed")
    with pytest.raises(sqlite3.IntegrityError):
        db.set_submission(second, post_url=URL)


@pytest.mark.parametrize("refused", [
    lambda db, c, first, second: db.set_submission(second, post_url=URL),
    lambda db, c, first, second: _sub(db, c, start=0, end=32, clip_title=None),
    lambda db, c, first, second: db.insert_campaign(None),
    lambda db, c, first, second: db.set_campaign(c, name=None),
])
def test_a_write_the_database_refuses_leaves_no_transaction_open(db, tmp_path, refused):
    campaign_id = _campaign(db)
    first = _sub(db, campaign_id, start=0, end=30)
    second = _sub(db, campaign_id, start=0, end=31)
    db.set_submission(first, post_url=URL)
    with pytest.raises(sqlite3.IntegrityError):
        refused(db, campaign_id, first, second)
    open_after = db.conn.in_transaction
    assert open_after is False
    # Another connection (another thread's StateDB) can still write at once.
    other = StateDB(tmp_path / "state.db")
    try:
        other.conn.execute("PRAGMA busy_timeout = 0")
        took = other.set_submission(first, status="cancelled")
    finally:
        other.close()
    assert took is True


# ---- compare-and-set, due rows, recovery ------------------------------------------------------

def test_set_submission_compares_the_status_before_it_sets(db):
    sub_id = _sub(db, _campaign(db))
    lost = db.set_submission(sub_id, expect=("ready",), status="sending")
    assert lost is False
    assert db.get_submission(sub_id)["status"] == "waiting_publication"
    won = db.set_submission(sub_id, expect=("waiting_publication", "waiting_url"), status="waiting_url")
    assert won is True
    assert db.get_submission(sub_id)["status"] == "waiting_url"
    results = [db.set_submission(sub_id, status_reason="no expectation"), db.set_submission(sub_id),
               db.set_submission(999, status="ready")]
    assert results == [True, False, False]


def test_set_submission_can_compare_other_columns_too(db):
    sub_id = _sub(db, _campaign(db), hold=1)
    lost = db.set_submission(sub_id, expect=("waiting_publication",), match={"hold": 0}, status="ready")
    assert lost is False and db.get_submission(sub_id)["status"] == "waiting_publication"
    won = db.set_submission(sub_id, expect=("waiting_publication",), match={"hold": 1, "opened_at": ""},
                            status="ready")
    assert won is True and db.get_submission(sub_id)["status"] == "ready"
    with pytest.raises(ValueError):
        db.set_submission(sub_id, match={"bogus": 1}, status="manual")


def test_submissions_due_are_the_longest_waiting_first(db):
    campaign_id = _campaign(db)
    ids = {}
    for name, start, status, at, url in (
        ("late", 1, "waiting_url", 30, ""),
        ("early", 2, "waiting_publication", 10, ""),
        ("middle", 3, "waiting_url", 20, ""),
        ("future", 4, "waiting_url", 100, ""),
        ("manual_no_url", 5, "manual", 15, ""),
        ("manual_with_url", 6, "manual", 5, URL),
        ("ready", 7, "ready", 1, ""),
    ):
        ids[name] = _sub(db, campaign_id, start=start, end=start + 30)
        db.set_submission(ids[name], status=status, next_attempt_at=at, post_url=url)

    def names(rows):
        found = {v: k for k, v in ids.items()}
        return [found[r["id"]] for r in rows]

    waiting = ("waiting_publication", "waiting_url")
    assert names(db.submissions_due(waiting, now=50)) == ["early", "middle", "late"]
    assert names(db.submissions_due(waiting, now=50, limit=2)) == ["early", "middle"]
    assert names(db.submissions_due(waiting, now=50, no_url_manual=True)) == [
        "early", "manual_no_url", "middle", "late"]
    assert names(db.submissions_due((), now=50, no_url_manual=True)) == ["manual_no_url"]
    assert db.submissions_due((), now=50) == []
    assert names(db.submissions_due(("ready",), now=50)) == ["ready"]


def test_recover_sending_never_leaves_a_row_sending(db):
    campaign_id = _campaign(db)
    old = _sub(db, campaign_id, start=0, end=30)
    new = _sub(db, campaign_id, start=1, end=30)
    for sub_id, started in ((old, 100.0), (new, 500.0)):
        db.set_submission(sub_id, status="sending", sending_started_at=started, sent_by="webhook")

    swept = db.recover_sending_submissions("Check Discord: it may have been posted", started_before=300)
    assert swept == 1
    row = db.get_submission(old)
    assert (row["status"], row["sent_by"]) == ("awaiting_confirmation", "")
    assert row["status_reason"] == "Check Discord: it may have been posted"
    assert db.get_submission(new)["status"] == "sending"

    recovered = db.recover_sending_submissions("Clips Kitty closed while sending. Check the channel.")
    assert recovered == 1
    assert db.get_submission(new)["status"] == "awaiting_confirmation"


def test_list_submissions_filters_and_puts_the_newest_first(db):
    first, second = _campaign(db, "First"), _campaign(db, "Second")
    a = _sub(db, first, clip_id=7)
    b = _sub(db, second, clip_id=7)
    c = _sub(db, first, platform="youtube", clip_id=8)
    db.set_submission(c, status="manual")
    assert [r["id"] for r in db.list_submissions()] == [c, b, a]
    assert [r["id"] for r in db.list_submissions(campaign_id=first)] == [c, a]
    assert [r["id"] for r in db.list_submissions(clip_id=7)] == [b, a]
    assert [r["id"] for r in db.list_submissions(statuses=("manual",))] == [c]
    assert [r["id"] for r in db.list_submissions(video_id="other")] == []
    assert len(db.list_submissions(limit=1)) == 1
