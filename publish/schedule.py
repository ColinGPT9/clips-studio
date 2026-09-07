"""Scheduling: converting and checking a publish time.

YouTube owns the schedule. Clips Kitty uploads the video immediately, marks it
private, and hands YouTube a `status.publishAt`. After that the app has no part
to play — the machine can be switched off and the video still goes out. There
is deliberately no local timer anywhere in this feature.

The actual timezone conversion happens in the browser, which has a full ICU
database and knows that 1:30 AM on a fall-back Sunday happens twice. Python on
Windows has no tz database at all (zoneinfo raises ZoneInfoNotFoundError and
tzdata is not a dependency), so this module only ever handles instants that
already carry an offset.
"""

from datetime import datetime, timedelta, timezone

from publish.errors import PublishError

# YouTube needs a moment to process an upload before it can publish it, and a
# user picking "two minutes from now" is really asking for "now".
MIN_LEAD_SECONDS = 900  # 15 minutes

# Past this, a typo is far more likely than an intention.
MAX_LEAD_DAYS = 365 * 2


def parse(value: str) -> datetime:
    """Parse an RFC 3339 timestamp that carries an offset, as UTC.

    Accepts both `...Z` and `...+01:00`. Rejects a naive timestamp outright:
    without an offset there is no way to know which instant is meant, and
    guessing "server local" is how a schedule lands an hour out twice a year.
    """
    text = (value or "").strip()
    if not text:
        raise PublishError("No publish time was given.")
    if text.endswith(("z", "Z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as e:
        raise PublishError(f"'{value}' is not a valid date and time.") from e
    if parsed.tzinfo is None:
        raise PublishError(
            "That time had no timezone attached, so it is ambiguous. "
            "This is a bug — please report it."
        )
    return parsed.astimezone(timezone.utc)


def to_rfc3339(moment: datetime) -> str:
    """Format for the API: UTC, whole seconds, trailing Z."""
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def validate_publish_at(value: str, now: datetime | None = None) -> str:
    """Check a requested schedule and return it normalised to UTC.

    Raises PublishError with something worth showing the user. Called by the
    API route before a job exists, so a bad time fails immediately rather than
    after a render.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    moment = parse(value)

    if moment <= now:
        raise PublishError(
            "That time has already passed. YouTube would publish the video "
            "immediately — pick a future time, or choose Publish now."
        )
    if moment < now + timedelta(seconds=MIN_LEAD_SECONDS):
        minutes = MIN_LEAD_SECONDS // 60
        raise PublishError(
            f"Pick a time at least {minutes} minutes from now, so YouTube has "
            "time to process the video before it goes live."
        )
    if moment > now + timedelta(days=MAX_LEAD_DAYS):
        raise PublishError("That date is more than two years away — check the year.")
    return to_rfc3339(moment)
