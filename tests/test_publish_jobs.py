"""Publish jobs, the uploads record, and the gate.

The double-upload guard is the important one here. Publishing deliberately does
NOT use the jobs table, because recover_interrupted_jobs() re-queues every
running job on startup — which for an upload would post the same video to
someone's channel a second time. These tests are what stops that being
"simplified" back later.
"""

import json

from core.state import StateDB
from server import youtube_service as service


def _video(db, video_id="v1"):
    db.conn.execute(
        "INSERT INTO videos (video_id, title, status, created_at, updated_at) "
        "VALUES (?, 't', 'done', '2026-01-01', '2026-01-01')",
        (video_id,),
    )
    db.conn.commit()


def _clip(db, video_id="v1", start=1.0, end=5.0, path="/tmp/clip.mp4"):
    db.conn.execute(
        "INSERT INTO clips (video_id, start_s, end_s, score, path, created_at) "
        "VALUES (?, ?, ?, 90, ?, '2026-01-01')",
        (video_id, start, end, path),
    )
    db.conn.commit()
    return db.conn.execute(
        "SELECT id FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
        (video_id, start, end),
    ).fetchone()["id"]


# ---- migration -------------------------------------------------------------


def test_uploads_gains_its_new_columns(db):
    columns = {r["name"] for r in db.conn.execute("PRAGMA table_info(uploads)")}
    for expected in (
        "video_id", "start_s", "end_s", "title", "privacy", "actual_privacy",
        "publish_at", "channel_id", "channel_title", "thumbnail_set",
        "playlist_id", "state", "error", "checked_at",
    ):
        assert expected in columns, f"uploads is missing {expected}"


def test_opening_the_same_database_twice_is_safe(db, tmp_path):
    """_migrate runs on every open; a second one must not fail on duplicates."""
    again = StateDB(tmp_path / "state.db")
    again.close()
    third = StateDB(tmp_path / "state.db")
    assert {r["name"] for r in third.conn.execute("PRAGMA table_info(uploads)")}
    third.close()


# ---- recording an upload ---------------------------------------------------


def test_record_publish_stores_the_full_result(db):
    _video(db)
    clip_id = _clip(db)
    db.record_publish(clip_id, {
        "youtube_id": "abc",
        "privacy": "public",
        "actual_privacy": "private",
        "state": "locked_private",
        "channel_title": "My Channel",
    })
    row = db.get_upload(clip_id)
    assert row["youtube_id"] == "abc"
    assert row["privacy"] == "public"
    assert row["actual_privacy"] == "private"
    assert row["state"] == "locked_private"
    assert db.get_clip(clip_id)["status"] == "uploaded"


def test_publishing_the_same_clip_twice_replaces_rather_than_crashing(db):
    """uploads.clip_id is the primary key, so the old plain INSERT raised on a
    second publish of the same clip."""
    _video(db)
    clip_id = _clip(db)
    db.record_publish(clip_id, {"youtube_id": "first"})
    db.record_publish(clip_id, {"youtube_id": "second"})
    assert db.get_upload(clip_id)["youtube_id"] == "second"


def test_record_upload_still_works_for_the_daemon(db):
    _video(db)
    clip_id = _clip(db)
    db.record_upload(clip_id, "xyz")
    db.record_upload(clip_id, "xyz2")  # would have raised before
    assert db.get_upload(clip_id)["youtube_id"] == "xyz2"


def test_unknown_fields_are_ignored_rather_than_raising(db):
    _video(db)
    clip_id = _clip(db)
    db.record_publish(clip_id, {"youtube_id": "a", "not_a_column": "x"})
    assert db.get_upload(clip_id)["youtube_id"] == "a"


# ---- the job queue ---------------------------------------------------------


def test_a_plain_job_is_claimed_immediately(db):
    _video(db)
    clip_id = _clip(db)
    job_id = db.add_publish_job(clip_id, json.dumps({"title": "t"}), video_id="v1")
    claimed = db.claim_next_publish_job()
    assert claimed is not None and claimed["id"] == job_id
    assert claimed["status"] == "running"
    assert db.claim_next_publish_job() is None, "a running job must not be claimed twice"


def test_a_job_waiting_on_a_render_is_not_claimed_yet(db):
    _video(db)
    clip_id = _clip(db)
    render_id = db.add_job("render", json.dumps({"clip_id": clip_id}))
    db.add_publish_job(clip_id, "{}", video_id="v1", after_job_id=render_id)

    assert db.claim_next_publish_job() is None, "the render has not finished"

    db.finish_job(render_id, "done")
    assert db.claim_next_publish_job() is not None


def test_a_job_whose_render_failed_is_failed_not_left_queued(db):
    """A row that never moves looks like a hang, and there is no file to upload."""
    _video(db)
    clip_id = _clip(db)
    render_id = db.add_job("render", json.dumps({"clip_id": clip_id}))
    job_id = db.add_publish_job(clip_id, "{}", video_id="v1", after_job_id=render_id)

    db.finish_job(render_id, "failed", "encoder died")
    assert db.claim_next_publish_job() is None

    job = db.get_publish_job(job_id)
    assert job["status"] == "failed"
    assert "render failed" in job["error"]


def test_a_cancelled_render_also_fails_the_publish(db):
    _video(db)
    clip_id = _clip(db)
    render_id = db.add_job("render", json.dumps({"clip_id": clip_id}))
    job_id = db.add_publish_job(clip_id, "{}", after_job_id=render_id)
    db.finish_job(render_id, "cancelled")
    db.claim_next_publish_job()
    assert db.get_publish_job(job_id)["status"] == "failed"


def test_one_waiting_job_does_not_block_a_ready_one(db):
    _video(db)
    waiting_clip = _clip(db, start=1.0, end=5.0)
    ready_clip = _clip(db, start=6.0, end=9.0)
    render_id = db.add_job("render", "{}")
    db.add_publish_job(waiting_clip, "{}", after_job_id=render_id)
    ready = db.add_publish_job(ready_clip, "{}")

    claimed = db.claim_next_publish_job()
    assert claimed["id"] == ready


# ---- crash recovery: the double-upload guard -------------------------------


def test_an_interrupted_upload_is_never_retried(db):
    _video(db)
    clip_id = _clip(db)
    job_id = db.add_publish_job(clip_id, "{}")
    db.claim_next_publish_job()  # now 'running'

    assert db.recover_running_publish_jobs() == 1

    job = db.get_publish_job(job_id)
    assert job["status"] == "interrupted"
    assert "Check your channel" in job["error"]
    assert db.claim_next_publish_job() is None, (
        "re-queueing this would upload the same video twice"
    )


def test_recovery_leaves_queued_jobs_alone(db):
    _video(db)
    clip_id = _clip(db)
    job_id = db.add_publish_job(clip_id, "{}")
    assert db.recover_running_publish_jobs() == 0
    assert db.get_publish_job(job_id)["status"] == "queued"


def test_an_active_job_is_findable_for_a_clip(db):
    _video(db)
    clip_id = _clip(db)
    assert db.active_publish_job_for_clip(clip_id) is None
    job_id = db.add_publish_job(clip_id, "{}")
    assert db.active_publish_job_for_clip(clip_id)["id"] == job_id
    db.finish_publish_job(job_id, "done", youtube_id="abc")
    assert db.active_publish_job_for_clip(clip_id) is None


def test_a_job_can_follow_a_clip_that_a_rerender_re_keyed(db):
    _video(db)
    clip_id = _clip(db)
    job_id = db.add_publish_job(clip_id, "{}", video_id="v1", start_s=1.0, end_s=5.0)
    db.set_publish_job_clip(job_id, 4242)
    assert db.get_publish_job(job_id)["clip_id"] == 4242


# ---- the gate --------------------------------------------------------------


def test_publishing_is_off_on_a_fresh_install(db):
    """The whole point of the Settings gate: a new install shows no YouTube
    anything until someone asks for it."""
    assert service.is_enabled(db) is False
    assert service.load_settings(db)["enabled"] is False


def test_status_says_nothing_at_all_while_disabled(db, tmp_path):
    payload = service.status_payload(db, {"paths": {"data_dir": str(tmp_path)}}, tmp_path)
    assert payload == {"enabled": False}, (
        "a disabled feature must look absent, not merely switched off"
    )


def test_turning_it_on_persists(db):
    service.save_settings(db, {"enabled": True, "privacy": "unlisted"})
    assert service.is_enabled(db) is True
    assert service.load_settings(db)["privacy"] == "unlisted"


def test_unknown_settings_keys_are_dropped(db):
    saved = service.save_settings(db, {"enabled": True, "nonsense": "x"})
    assert "nonsense" not in saved


def test_corrupt_settings_fall_back_to_the_defaults(db):
    db.set_flag(service.SETTINGS_KEY, "{{{not json")
    assert service.load_settings(db)["enabled"] is False


def test_the_quota_ledger_survives_a_round_trip(db):
    ledger = service.load_ledger(db)
    ledger.record_upload()
    service.save_ledger(db, ledger)
    assert service.load_ledger(db).uploads == 1


# ---- multiple channels -----------------------------------------------------


def _channel(cid, title="Chan", handle="@chan"):
    return {"id": cid, "title": title, "handle": handle}


def test_no_channels_on_a_fresh_install(db):
    assert service.load_accounts(db) == []
    assert service.default_channel_id(db) is None


def test_the_first_channel_connected_becomes_the_default(db):
    service.add_account(db, _channel("UC_A"), ["upload"])
    accounts = service.load_accounts(db)
    assert len(accounts) == 1
    assert accounts[0]["default"] is True
    assert service.default_channel_id(db) == "UC_A"


def test_a_second_channel_is_added_not_swapped(db):
    """The whole point: a main channel and a clips channel, both connected."""
    service.add_account(db, _channel("UC_A", "Main"), ["upload"])
    service.add_account(db, _channel("UC_B", "Clips"), ["upload"])

    accounts = service.load_accounts(db)
    assert [a["id"] for a in accounts] == ["UC_A", "UC_B"]
    assert service.default_channel_id(db) == "UC_A", "adding must not steal the default"


def test_reconnecting_the_same_channel_updates_it_in_place(db):
    service.add_account(db, _channel("UC_A", "Old name"), ["upload"])
    service.add_account(db, _channel("UC_A", "New name"), ["upload", "readonly"])

    accounts = service.load_accounts(db)
    assert len(accounts) == 1, "reconnecting must not create a duplicate"
    assert accounts[0]["title"] == "New name"
    assert accounts[0]["default"] is True, "it was the default and should stay it"


def test_the_default_can_be_moved(db):
    service.add_account(db, _channel("UC_A"), [])
    service.add_account(db, _channel("UC_B"), [])
    service.set_default_account(db, "UC_B")

    assert service.default_channel_id(db) == "UC_B"
    assert sum(1 for a in service.load_accounts(db) if a.get("default")) == 1


def test_setting_an_unknown_channel_as_default_changes_nothing(db):
    service.add_account(db, _channel("UC_A"), [])
    service.set_default_account(db, "UC_NOPE")
    assert service.default_channel_id(db) == "UC_A"


def test_disconnecting_one_channel_leaves_the_other(db):
    service.add_account(db, _channel("UC_A"), [])
    service.add_account(db, _channel("UC_B"), [])
    service.remove_account(db, "UC_A")

    accounts = service.load_accounts(db)
    assert [a["id"] for a in accounts] == ["UC_B"]
    assert service.default_channel_id(db) == "UC_B", "the survivor must inherit the default"


def test_disconnecting_the_last_channel_leaves_nothing(db):
    service.add_account(db, _channel("UC_A"), [])
    service.remove_account(db, "UC_A")
    assert service.load_accounts(db) == []
    assert service.default_channel_id(db) is None


def test_an_install_that_predates_the_roster_is_adopted(db):
    """Someone already connected before multi-channel existed must not be
    asked to reconnect a working account."""
    service.remember_channel(db, _channel("UC_OLD", "Existing"))
    assert db.get_flag(service.ACCOUNTS_KEY, "") == ""

    accounts = service.load_accounts(db)
    assert [a["id"] for a in accounts] == ["UC_OLD"]
    assert accounts[0]["default"] is True
    assert accounts[0]["legacy_token"] is True, (
        "its token is in the unqualified slot, so it must be findable there"
    )


def test_a_legacy_channel_resolves_to_the_unqualified_token_slot(db):
    service.remember_channel(db, _channel("UC_OLD"))
    service.load_accounts(db)
    assert service.token_name_for_account(db, "UC_OLD") is None
    assert service.token_name_for_account(db, "UC_NEW") == "UC_NEW"


def test_token_names_are_per_channel_and_stable(db):
    from publish.youtube_shorts import TOKEN_SECRET, token_name_for

    assert token_name_for(None) == TOKEN_SECRET
    assert token_name_for("UC_A") == f"{TOKEN_SECRET}__UC_A"
    assert token_name_for("UC_A") != token_name_for("UC_B")


def test_two_channels_tokens_do_not_collide_in_the_store(tmp_path):
    """The bug this guards: filing a second channel's token in the shared slot
    would silently overwrite the first channel's."""
    from core import secrets
    from publish.youtube_shorts import token_name_for

    secrets.save(tmp_path, token_name_for("UC_A"), {"refresh_token": "a"})
    secrets.save(tmp_path, token_name_for("UC_B"), {"refresh_token": "b"})

    assert secrets.load(tmp_path, token_name_for("UC_A"))["refresh_token"] == "a"
    assert secrets.load(tmp_path, token_name_for("UC_B"))["refresh_token"] == "b"

    secrets.wipe(tmp_path, token_name_for("UC_A"))
    assert secrets.load(tmp_path, token_name_for("UC_A")) is None
    assert secrets.load(tmp_path, token_name_for("UC_B")) is not None
