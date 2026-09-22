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

from datetime import date, datetime, timedelta, timezone

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


def daily(
    start: str,
    count: int,
    per_day: int,
    gap_hours: float,
    now: datetime | None = None,
) -> list[str]:
    """`count` publish times, `per_day` of them each day, `gap_hours` apart.

    "Five a day, an hour apart, until they are all out" is the shape people
    actually want, and `spread()` cannot express it: one flat interval either
    bunches everything into today or drags a single post per day out for a
    month. Posting limits are daily, so the budget has to be daily too.

    Day `i // per_day`, slot `i % per_day`, so 37 clips at five a day from
    09:00 go out 09:00-13:00 today, the same five slots tomorrow, and finish
    eight days later.

    A day's posts must fit inside that day. Otherwise the last slot of Monday
    lands on Tuesday, two days' budgets collide, and the platform rejects the
    overflow as spam — which is exactly the failure this replaces.
    """
    if count < 1:
        return []
    if per_day < 1:
        raise PublishError("Post at least one a day, or upload them all now.")
    if gap_hours <= 0:
        raise PublishError("Put some time between the videos, or upload them all now.")
    if gap_hours * (per_day - 1) >= 24:
        raise PublishError(
            f"{per_day} posts {gap_hours} hours apart does not fit in a day. "
            f"Post fewer a day, or put them closer together."
        )
    first = parse(start)
    times = [
        first + timedelta(days=i // per_day, hours=gap_hours * (i % per_day))
        for i in range(count)
    ]
    # The same door a hand-picked time goes through, as in spread().
    return [validate_publish_at(to_rfc3339(moment), now=now) for moment in times]


def daily_after(
    taken: list[str],
    count: int,
    per_day: int,
    gap_hours: float,
    earliest: str,
    now: datetime | None = None,
) -> list[str]:
    """`count` times that fit AROUND what is already scheduled.

    A daily budget is per day, not per batch. `daily()` alone applies it per
    batch, so a second video published while the first is still going out
    puts two runs on the same days: five a day becomes ten a day, and the
    platform rejects the overflow. That is the failure this prevents, and it
    is the same one that lost 32 posts the first time.

    `taken` is every time already committed — posts still queued, and posts
    already made today, which have spent part of today's allowance. A failed
    post holds nothing and should not be in the list.

    With nothing taken, this returns exactly what `daily()` returns, so a
    first run is unchanged.
    """
    if count < 1:
        return []
    if per_day < 1:
        raise PublishError("Post at least one a day, or upload them all now.")
    if gap_hours <= 0:
        raise PublishError("Put some time between the videos, or upload them all now.")
    if gap_hours * (per_day - 1) >= 24:
        raise PublishError(
            f"{per_day} posts {gap_hours} hours apart does not fit in a day. "
            f"Post fewer a day, or put them closer together."
        )

    first = parse(earliest)
    used: dict[date, int] = {}
    for value in taken:
        try:
            moment = parse(value)
        except (PublishError, ValueError):
            continue  # unreadable times are not worth failing a whole run over
        used[moment.date()] = used.get(moment.date(), 0) + 1

    out: list[datetime] = []
    day_offset = 0
    # A guard rather than `while True`: a pathological `used` should not spin
    # forever. 365 days is already past what validate_publish_at accepts.
    while len(out) < count and day_offset < 400:
        when = first + timedelta(days=day_offset)
        room = per_day - used.get(when.date(), 0)
        for slot in range(max(0, room)):
            if len(out) >= count:
                break
            # Slots are counted from the START of that day's run, so a day
            # that is half full continues where it left off rather than
            # restarting at the first hour.
            at = when + timedelta(hours=gap_hours * (used.get(when.date(), 0) + slot))
            if at >= first:
                out.append(at)
        day_offset += 1

    return [validate_publish_at(to_rfc3339(moment), now=now) for moment in out[:count]]


def spread(start: str, count: int, every_hours: float, now: datetime | None = None) -> list[str]:
    """`count` publish times, `every_hours` apart, beginning at `start`.

    "Schedule them an hour apart starting tomorrow at noon" is the whole reason
    this exists. Each time is validated exactly as a hand-picked one is, so a
    batch cannot smuggle past a check a single upload has to pass.

    The interval may be fractional (0.5 is half an hour) but not zero: a batch
    that all publishes at the same instant is a mistake every time, and asking
    for that is better answered with "upload now" than with a schedule.
    """
    if count < 1:
        return []
    if every_hours <= 0:
        raise PublishError("Put some time between the videos, or upload them all now.")
    first = parse(start)
    times = [first + timedelta(hours=every_hours * i) for i in range(count)]
    # Validate through the same door as a single upload: the first must clear
    # the lead time, the last must be inside the far limit.
    return [validate_publish_at(to_rfc3339(moment), now=now) for moment in times]
