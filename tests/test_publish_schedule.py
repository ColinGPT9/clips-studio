"""Scheduling times.

The rule these enforce: Clips Kitty never holds a schedule. It uploads the
video now, private, with a publishAt, and YouTube publishes it later whether or
not this computer is switched on. So the only thing to get right here is the
instant itself.
"""

from datetime import datetime, timedelta, timezone

import pytest

from publish.errors import PublishError
from publish.schedule import MIN_LEAD_SECONDS, parse, to_rfc3339, validate_publish_at

NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)


def test_z_and_offset_forms_are_the_same_instant():
    assert parse("2026-09-07T12:00:00Z") == parse("2026-09-07T13:00:00+01:00")


def test_lowercase_z_is_accepted():
    assert parse("2026-09-07T12:00:00z").hour == 12


def test_a_time_without_a_zone_is_refused():
    """Without an offset there is no way to know which instant is meant, and
    guessing is how a schedule lands an hour out twice a year."""
    with pytest.raises(PublishError):
        parse("2026-09-07T12:00:00")


def test_nonsense_is_refused():
    with pytest.raises(PublishError):
        parse("next tuesday")
    with pytest.raises(PublishError):
        parse("")


def test_output_is_utc_with_a_z_and_no_microseconds():
    formatted = to_rfc3339(datetime(2026, 9, 7, 13, 30, 5, 123456,
                                    tzinfo=timezone(timedelta(hours=2))))
    assert formatted == "2026-09-07T11:30:05Z"


def test_a_valid_future_time_is_normalised_to_utc():
    assert validate_publish_at("2026-09-07T14:00:00+01:00", now=NOW) == "2026-09-07T13:00:00Z"


def test_a_past_time_is_refused_with_a_useful_reason():
    with pytest.raises(PublishError) as e:
        validate_publish_at("2026-09-06T12:00:00Z", now=NOW)
    assert "passed" in str(e.value)


def test_too_soon_is_refused():
    soon = NOW + timedelta(seconds=MIN_LEAD_SECONDS - 60)
    with pytest.raises(PublishError):
        validate_publish_at(to_rfc3339(soon), now=NOW)


def test_exactly_the_lead_time_is_allowed():
    ok = NOW + timedelta(seconds=MIN_LEAD_SECONDS)
    assert validate_publish_at(to_rfc3339(ok), now=NOW) == to_rfc3339(ok)


def test_absurdly_far_ahead_is_refused_as_a_typo():
    with pytest.raises(PublishError) as e:
        validate_publish_at("2050-01-01T00:00:00Z", now=NOW)
    assert "year" in str(e.value)


def test_daylight_saving_is_the_browsers_job_not_ours():
    """The renderer converts local wall-clock to an instant, because it has a
    full timezone database and Python on Windows does not. By the time a time
    reaches here it already carries an offset, so both sides of a DST change
    are just two different offsets and nothing special happens."""
    before = validate_publish_at("2026-10-25T01:30:00+01:00", now=NOW)  # BST
    after = validate_publish_at("2026-10-25T01:30:00+00:00", now=NOW)  # GMT
    assert before == "2026-10-25T00:30:00Z"
    assert after == "2026-10-25T01:30:00Z"
    assert before != after, "the same wall clock either side of the change is a different instant"
