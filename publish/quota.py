"""YouTube Data API quota, as it actually works in 2026.

Worth stating plainly, because almost every tutorial online is wrong about it
and this repo's own comments were until recently:

* An upload used to cost 1,600 units out of a shared 10,000/day pool, which is
  where the old "about six uploads a day" figure came from. That changed on
  4 December 2025, when the cost dropped to roughly 100 units.
* On 1 June 2026 the API moved to granular quota buckets. `videos.insert` and
  `search.list` each got their OWN daily bucket. A project now gets **100
  uploads per day**, plus 100 search calls, plus 10,000 units shared across
  everything else.

So the limit that matters for publishing is a count of uploads, not a unit
total, and it is per Cloud project. Since every user brings their own project,
nobody shares a bucket with anyone else.

The counter rolls over at midnight America/Los_Angeles. That is genuinely
awkward here: Windows ships no IANA timezone database, `zoneinfo` raises
ZoneInfoNotFoundError, and `tzdata` is not a dependency. Rather than add one
for a single timezone, the US daylight-saving rules are computed directly —
they are simple, fixed since 2007, and unit-tested on both sides of each
boundary.
"""

from datetime import date, datetime, timedelta, timezone

# The dedicated videos.insert bucket. Not configurable: this is Google's
# default allocation, and a project that has been granted more will simply
# never hit the local guard first.
UPLOAD_LIMIT = 100

# Cost in units against the shared 10,000/day pool. videos.insert and
# search.list are absent on purpose — they bill to their own buckets now.
COSTS = {
    "videos.update": 50,
    "videos.list": 1,
    "thumbnails.set": 50,
    "playlistItems.insert": 50,
    "playlists.list": 1,
    "channels.list": 1,
    "videoCategories.list": 1,
    "i18nLanguages.list": 1,
}

_PACIFIC_STANDARD = timedelta(hours=-8)
_PACIFIC_DAYLIGHT = timedelta(hours=-7)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth given weekday of a month. weekday: Monday=0 ... Sunday=6."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _pacific_offset(moment: datetime) -> timedelta:
    """PST or PDT at this instant.

    US daylight saving, unchanged since 2007: forward on the second Sunday in
    March at 02:00 local standard time (10:00 UTC), back on the first Sunday in
    November at 02:00 local daylight time (09:00 UTC).
    """
    moment = moment.astimezone(timezone.utc)
    year = moment.year
    starts = datetime.combine(
        _nth_weekday(year, 3, 6, 2), datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(hours=10)
    ends = datetime.combine(
        _nth_weekday(year, 11, 6, 1), datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(hours=9)
    return _PACIFIC_DAYLIGHT if starts <= moment < ends else _PACIFIC_STANDARD


def pacific_day(moment: datetime | None = None) -> str:
    """The quota day (YYYY-MM-DD in Pacific time) an instant falls in."""
    moment = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return (moment + _pacific_offset(moment)).date().isoformat()


def next_reset(moment: datetime | None = None) -> datetime:
    """When the counter next rolls over, as a UTC instant."""
    moment = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local = moment + _pacific_offset(moment)
    midnight_local = datetime.combine(
        local.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    )
    # Re-resolve the offset at the boundary itself: on a spring-forward day the
    # offset either side of midnight differs, and using the wrong one puts the
    # reset an hour out.
    guess = midnight_local - _pacific_offset(moment)
    return midnight_local - _pacific_offset(guess)


class Ledger:
    """How many uploads this API project has spent today.

    Advisory, not authoritative. A user running two installs against one key
    shares the real bucket, and neither copy can see the other's count — so a
    403 from YouTube is always the truth and this is only here to avoid
    uploading a whole file just to be told no.
    """

    def __init__(self, state: dict | None = None):
        state = state or {}
        self.day: str = state.get("day", "")
        self.uploads: int = int(state.get("uploads", 0))
        self.blocked_until: str = state.get("blocked_until", "")

    def as_dict(self) -> dict:
        return {"day": self.day, "uploads": self.uploads, "blocked_until": self.blocked_until}

    def _roll(self, now: datetime | None = None) -> None:
        today = pacific_day(now)
        if self.day != today:
            self.day = today
            self.uploads = 0
            self.blocked_until = ""

    def remaining(self, now: datetime | None = None) -> int:
        self._roll(now)
        return max(0, UPLOAD_LIMIT - self.uploads)

    def exhausted(self, now: datetime | None = None) -> bool:
        return self.remaining(now) <= 0

    def record_upload(self, now: datetime | None = None) -> None:
        self._roll(now)
        self.uploads += 1

    def record_quota_error(self, now: datetime | None = None) -> None:
        """YouTube said no. Believe it over the local count."""
        self._roll(now)
        self.uploads = max(self.uploads, UPLOAD_LIMIT)
        self.blocked_until = next_reset(now).isoformat().replace("+00:00", "Z")
