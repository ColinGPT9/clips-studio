"""The quota counter, and the Pacific day it rolls over on.

The DST cases matter more here than they look: getting the boundary wrong rolls
the counter at the wrong hour, which either blocks uploads that should be
allowed or lets a batch run into a wall it could have predicted. Windows has no
timezone database, so these rules are computed rather than looked up — which
makes testing them the only thing standing between the code and a silent
one-hour error twice a year.
"""

from datetime import datetime, timedelta, timezone

from publish.quota import UPLOAD_LIMIT, Ledger, next_reset, pacific_day


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_pacific_is_eight_hours_behind_in_winter():
    # 07:00 UTC on 1 Jan is 23:00 on 31 Dec in PST.
    assert pacific_day(utc(2026, 1, 1, 7, 0)) == "2025-12-31"
    assert pacific_day(utc(2026, 1, 1, 8, 0)) == "2026-01-01"


def test_pacific_is_seven_hours_behind_in_summer():
    assert pacific_day(utc(2026, 7, 1, 6, 59)) == "2026-06-30"
    assert pacific_day(utc(2026, 7, 1, 7, 0)) == "2026-07-01"


def test_spring_forward_boundary():
    """2026: second Sunday in March is the 8th, at 10:00 UTC."""
    assert pacific_day(utc(2026, 3, 8, 9, 59)) == "2026-03-08"  # still PST
    assert pacific_day(utc(2026, 3, 8, 10, 1)) == "2026-03-08"  # now PDT
    # An hour before the change, midnight Pacific is 08:00 UTC.
    assert pacific_day(utc(2026, 3, 8, 7, 59)) == "2026-03-07"


def test_fall_back_boundary():
    """2026: first Sunday in November is the 1st, at 09:00 UTC."""
    assert pacific_day(utc(2026, 11, 1, 8, 59)) == "2026-11-01"  # PDT
    assert pacific_day(utc(2026, 11, 1, 9, 1)) == "2026-11-01"  # PST


def test_reset_is_the_next_pacific_midnight():
    reset = next_reset(utc(2026, 7, 1, 12, 0))
    assert reset == utc(2026, 7, 2, 7, 0), "midnight PDT is 07:00 UTC"
    assert reset > utc(2026, 7, 1, 12, 0)


def test_reset_in_winter():
    assert next_reset(utc(2026, 1, 15, 12, 0)) == utc(2026, 1, 16, 8, 0)


def test_a_fresh_ledger_has_the_full_allowance():
    assert Ledger().remaining(utc(2026, 7, 1, 12, 0)) == UPLOAD_LIMIT
    assert not Ledger().exhausted(utc(2026, 7, 1, 12, 0))


def test_uploads_count_down():
    ledger = Ledger()
    now = utc(2026, 7, 1, 12, 0)
    for _ in range(3):
        ledger.record_upload(now)
    assert ledger.remaining(now) == UPLOAD_LIMIT - 3


def test_the_hundredth_upload_exhausts_it():
    ledger = Ledger()
    now = utc(2026, 7, 1, 12, 0)
    for _ in range(UPLOAD_LIMIT):
        ledger.record_upload(now)
    assert ledger.exhausted(now)
    assert ledger.remaining(now) == 0


def test_the_count_rolls_over_at_pacific_midnight_not_local():
    ledger = Ledger()
    before = utc(2026, 7, 1, 6, 0)  # 23:00 on 30 Jun, Pacific
    ledger.record_upload(before)
    assert ledger.remaining(before) == UPLOAD_LIMIT - 1

    after = utc(2026, 7, 1, 8, 0)  # 01:00 on 1 Jul, Pacific
    assert ledger.remaining(after) == UPLOAD_LIMIT, "a new Pacific day starts fresh"


def test_youtube_saying_no_beats_the_local_count():
    """The local ledger is advisory — two installs sharing one key cannot see
    each other, so a 403 is always the truth."""
    ledger = Ledger()
    now = utc(2026, 7, 1, 12, 0)
    ledger.record_upload(now)
    assert not ledger.exhausted(now)

    ledger.record_quota_error(now)
    assert ledger.exhausted(now)
    assert ledger.blocked_until.endswith("Z")


def test_a_ledger_survives_a_round_trip_through_storage():
    ledger = Ledger()
    now = utc(2026, 7, 1, 12, 0)
    ledger.record_upload(now)
    restored = Ledger(ledger.as_dict())
    assert restored.remaining(now) == UPLOAD_LIMIT - 1


def test_the_quota_day_is_stable_across_a_whole_utc_day():
    """Every hour of one Pacific day must map to the same string."""
    start = utc(2026, 7, 1, 7, 0)  # midnight PDT
    days = {pacific_day(start + timedelta(hours=h)) for h in range(24)}
    assert days == {"2026-07-01"}
