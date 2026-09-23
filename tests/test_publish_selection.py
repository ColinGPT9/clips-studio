"""Choosing what goes where, and finding out what happened afterwards.

Both came from one real run: 37 clips were accepted in a single batch, five
reached YouTube, and the app could say nothing about the other 32 because
every row sat at "processing" and nothing ever looked again. WoopSocial's
free plan turned out to allow five posts a day, so the batch was a queue
about eight days long rather than anything broken.
"""

import pytest

woop = pytest.importorskip("server.woopsocial_service")


class _Row(dict):
    """A sqlite3.Row stand-in: the code uses both r["k"] and r.keys()."""


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDB:
    def __init__(self, clips=None, publishes=None):
        self._clips = clips or {}
        self._publishes = publishes or []
        self.recorded = []
        self.saved = {}

    def get_clip(self, clip_id):
        return self._clips.get(clip_id)

    def record_clip_publish(self, clip_id, platform, fields):
        self.recorded.append((clip_id, platform, fields))

    def publishes_for_request(self, request_id):
        return [r for r in self._publishes if r["request_id"] == request_id]

    @property
    def conn(self):
        return self

    def execute(self, sql, params=()):
        seen, out = set(), []
        for r in self._publishes:
            if r["state"] in ("queued", "processing") and r["request_id"]:
                if r["request_id"] not in seen:
                    seen.add(r["request_id"])
                    out.append(r)
        # sqlite hands back a cursor, not a list, and the code under test
        # calls .fetchall() on it.
        return _Cursor(out)


def _clip(cid, path):
    return _Row(
        id=cid, path=str(path), title=f"Clip {cid}", hook="", description="d",
        hashtags="", video_id="v", start_s=0, end_s=1,
    )


class _Outcome:
    def __init__(self, platform, state="published"):
        self.platform = platform
        self.state = state
        self.post_id = "p1"
        self.post_url = "https://youtu.be/abc"
        self.error = ""


class _Result:
    def __init__(self, platforms):
        self.request_id = "req-1"
        self.outcomes = [_Outcome(p) for p in platforms]


# ---- holding a clip back from one platform ----------------------------------


@pytest.fixture
def two_clips(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    return {1: _clip(1, a), 2: _clip(2, b)}


def _publisher(monkeypatch, sent):
    class FakePublisher:
        def __init__(self, client, project):
            pass

        def start(self, path, *, platforms, title, text, scheduled_for, overrides):
            sent.append({
                "platforms": list(platforms),
                "title": title,
                "scheduled_for": scheduled_for,
            })
            return _Result(platforms)

    monkeypatch.setattr("publish.woopsocial.WoopSocialPublisher", FakePublisher)
    monkeypatch.setattr(woop, "make_client", lambda d: object())
    monkeypatch.setattr(woop, "resolve_project", lambda db, c: "proj")
    monkeypatch.setattr(woop, "load_settings", lambda db: {})
    monkeypatch.setattr(woop, "save_settings", lambda db, patch: None)


def test_a_clip_can_be_held_back_from_one_platform(monkeypatch, tmp_path, two_clips):
    """The point of the whole feature: some platforms are stricter, so one
    clip skips one of them without being dropped everywhere."""
    sent = []
    _publisher(monkeypatch, sent)
    db = _FakeDB(clips=two_clips)

    out = woop.publish_clips(
        db, tmp_path,
        clip_ids=[1, 2],
        platforms=["youtube", "instagram"],
        exclude={1: ["instagram"]},
    )

    assert sent[0]["platforms"] == ["youtube"], "clip 1 skips instagram"
    assert sent[1]["platforms"] == ["youtube", "instagram"], "clip 2 is untouched"
    assert len(out["started"]) == 2


def test_string_keys_work_too(monkeypatch, tmp_path, two_clips):
    """JSON has no integer keys, so the browser sends "1", not 1. Accepting
    only one of those would drop the exclusion in silence."""
    sent = []
    _publisher(monkeypatch, sent)
    woop.publish_clips(
        _FakeDB(clips=two_clips), tmp_path,
        clip_ids=[1], platforms=["youtube", "instagram"],
        exclude={"1": ["instagram"]},
    )
    assert sent[0]["platforms"] == ["youtube"]


def test_a_clip_excluded_everywhere_is_skipped_not_posted_empty(
    monkeypatch, tmp_path, two_clips
):
    sent = []
    _publisher(monkeypatch, sent)
    out = woop.publish_clips(
        _FakeDB(clips=two_clips), tmp_path,
        clip_ids=[1, 2], platforms=["youtube"],
        exclude={1: ["youtube"]},
    )
    assert len(sent) == 1, "only clip 2 is sent"
    assert out["skipped"] == [
        {"clip_id": 1, "reason": "excluded from every platform"}
    ]


def test_no_exclude_behaves_exactly_as_before(monkeypatch, tmp_path, two_clips):
    """The common case must not change: this shipped working and most runs
    send everything everywhere."""
    sent = []
    _publisher(monkeypatch, sent)
    woop.publish_clips(
        _FakeDB(clips=two_clips), tmp_path,
        clip_ids=[1, 2], platforms=["youtube", "instagram"],
    )
    assert [s["platforms"] for s in sent] == [
        ["youtube", "instagram"], ["youtube", "instagram"]
    ]


def test_rubbish_in_exclude_is_ignored_rather_than_fatal(monkeypatch, tmp_path, two_clips):
    sent = []
    _publisher(monkeypatch, sent)
    woop.publish_clips(
        _FakeDB(clips=two_clips), tmp_path,
        clip_ids=[1], platforms=["youtube"],
        exclude={"not-a-number": ["youtube"]},
    )
    assert sent[0]["platforms"] == ["youtube"]


# ---- finding out what happened ----------------------------------------------


def _pub(clip_id, request_id, state):
    return _Row(clip_id=clip_id, request_id=request_id, state=state, platform="youtube")


def test_in_flight_returns_each_request_once():
    """37 rows from one batch are one question to ask, not 37."""
    db = _FakeDB(publishes=[
        _pub(1, "req-a", "processing"),
        _pub(2, "req-a", "processing"),
        _pub(3, "req-b", "queued"),
    ])
    assert woop.in_flight(db) == ["req-a", "req-b"]


def test_nothing_in_flight_asks_woopsocial_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        woop, "make_client", lambda d: pytest.fail("should not have called out")
    )
    out = woop.refresh_in_flight(_FakeDB(publishes=[]), tmp_path)
    assert out == {"checked": 0, "updated": 0, "still_waiting": 0, "failed": 0}


def test_refresh_writes_back_what_it_learns(monkeypatch, tmp_path):
    """The rows said "processing" forever, which is why a five-a-day limit
    was indistinguishable from a failure."""
    db = _FakeDB(
        clips={1: _Row(id=1, video_id="v", start_s=0, end_s=1)},
        publishes=[_pub(1, "req-a", "processing")],
    )

    class FakePublisher:
        def __init__(self, client, project):
            pass

        def check(self, request_id):
            assert request_id == "req-a"
            return _Result(["youtube"])

    monkeypatch.setattr("publish.woopsocial.WoopSocialPublisher", FakePublisher)
    monkeypatch.setattr(woop, "make_client", lambda d: object())
    monkeypatch.setattr(woop, "resolve_project", lambda db_, c: "proj")

    out = woop.refresh_in_flight(db, tmp_path)
    assert out["checked"] == 1
    assert out["updated"] == 1
    assert db.recorded and db.recorded[0][1] == "youtube"
    assert db.recorded[0][2]["post_url"] == "https://youtu.be/abc"


def test_one_unreachable_request_does_not_stop_the_others(monkeypatch, tmp_path):
    db = _FakeDB(
        clips={1: _Row(id=1, video_id="v", start_s=0, end_s=1),
               2: _Row(id=2, video_id="v", start_s=0, end_s=1)},
        publishes=[_pub(1, "req-a", "processing"), _pub(2, "req-b", "processing")],
    )

    class FakePublisher:
        def __init__(self, client, project):
            pass

        def check(self, request_id):
            if request_id == "req-a":
                raise RuntimeError("gone")
            return _Result(["youtube"])

    monkeypatch.setattr("publish.woopsocial.WoopSocialPublisher", FakePublisher)
    monkeypatch.setattr(woop, "make_client", lambda d: object())
    monkeypatch.setattr(woop, "resolve_project", lambda db_, c: "proj")

    out = woop.refresh_in_flight(db, tmp_path)
    assert out["failed"] == 1
    assert out["updated"] == 1, "the reachable one still got updated"


# ---- a daily budget, not a flat interval ------------------------------------


def test_five_a_day_an_hour_apart_spans_the_right_days():
    """The shape that was impossible before: a daily budget. Posting limits
    are daily, so a flat interval either bunches everything into today or
    drags one post a day out for a month."""
    sched = pytest.importorskip("publish.schedule")
    from datetime import datetime, timedelta, timezone

    start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    out = sched.daily(start.isoformat(), 37, 5, 1)

    assert len(out) == 37
    assert len({t[:10] for t in out}) == 8, "37 at five a day is eight days"
    # First day: five slots an hour apart.
    assert [t[11:16] for t in out[:5]] == ["09:00", "10:00", "11:00", "12:00", "13:00"]
    # Day two restarts at the same clock time rather than carrying on.
    assert out[5][11:16] == "09:00"
    assert out[5][:10] != out[4][:10]


def test_a_day_that_cannot_hold_its_own_posts_is_refused():
    """8 hours apart, five a day, would put Monday's last post on Tuesday,
    collide two budgets and get the overflow rejected as spam."""
    sched = pytest.importorskip("publish.schedule")
    from datetime import datetime, timedelta, timezone

    start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(Exception, match="does not fit in a day"):
        sched.daily(start, 10, 5, 8)


def test_one_a_day_is_allowed_because_platforms_ask_for_it():
    sched = pytest.importorskip("publish.schedule")
    from datetime import datetime, timedelta, timezone

    start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    out = sched.daily(start, 3, 1, 1)
    assert len({t[:10] for t in out}) == 3


def test_a_skipped_clip_does_not_burn_a_slot(monkeypatch, tmp_path, two_clips):
    """Five a day means five posts, not five attempts. A clip held back must
    not cost one of the day's slots, or a budget quietly delivers fewer."""
    from datetime import datetime, timedelta, timezone

    start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0
    ).isoformat()

    # Both clips, nothing excluded: slot one and slot two.
    both = []
    _publisher(monkeypatch, both)
    woop.publish_clips(
        _FakeDB(clips=two_clips), tmp_path,
        clip_ids=[1, 2], platforms=["youtube"],
        per_day=5, gap_hours=1, start_at=start,
    )
    assert len(both) == 2
    assert both[0]["scheduled_for"][11:16] == "09:00"
    assert both[1]["scheduled_for"][11:16] == "10:00"

    # Clip 1 held back: clip 2 must take the FIRST slot, not the second.
    one = []
    _publisher(monkeypatch, one)
    woop.publish_clips(
        _FakeDB(clips=two_clips), tmp_path,
        clip_ids=[1, 2], platforms=["youtube"],
        exclude={1: ["youtube"]},
        per_day=5, gap_hours=1, start_at=start,
    )
    assert len(one) == 1
    assert one[0]["scheduled_for"][11:16] == "09:00", "the freed slot is reused"


# ---- a second batch queues behind the first ---------------------------------


def _at(day_offset, hour):
    from datetime import datetime, timedelta, timezone

    base = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    return (base + timedelta(days=day_offset, hours=hour)).isoformat()


def test_nothing_scheduled_behaves_exactly_like_before():
    """A first run must not change. This is the common case and it shipped
    working."""
    sched = pytest.importorskip("publish.schedule")
    start = _at(0, 0)
    assert sched.daily_after([], 7, 5, 1, start) == sched.daily(start, 7, 5, 1)


def test_a_second_batch_never_doubles_up_a_day():
    """The bug this exists for: 37 posts over eight days plus 20 more, both
    at five a day, used to put ten on each of the first four days. WoopSocial
    allows five, so half the run failed."""
    sched = pytest.importorskip("publish.schedule")
    start = _at(0, 0)

    first = sched.daily(start, 37, 5, 1)
    second = sched.daily_after(first, 20, 5, 1, start)

    per_day = {}
    for when in first + second:
        per_day[when[:10]] = per_day.get(when[:10], 0) + 1
    assert max(per_day.values()) <= 5, per_day
    assert len(second) == 20


def test_a_part_used_day_is_filled_then_rolls_over():
    """Two posts already today means three slots left, not five, and the
    fourth goes tomorrow."""
    sched = pytest.importorskip("publish.schedule")
    start = _at(0, 0)
    taken = [_at(0, 0), _at(0, 1)]

    out = sched.daily_after(taken, 4, 5, 1, start)

    assert [w[11:16] for w in out[:3]] == ["11:00", "12:00", "13:00"]
    assert out[3][:10] != out[2][:10], "the fourth rolls to the next day"
    assert out[3][11:16] == "09:00"


def test_a_full_day_is_skipped_entirely():
    sched = pytest.importorskip("publish.schedule")
    start = _at(0, 0)
    taken = [_at(0, h) for h in range(5)]   # today is full

    out = sched.daily_after(taken, 2, 5, 1, start)

    assert all(w[:10] != taken[0][:10] for w in out), "nothing lands on a full day"


def test_unreadable_committed_times_do_not_fail_the_run():
    """One bad row must not cost a whole batch its schedule."""
    sched = pytest.importorskip("publish.schedule")
    start = _at(0, 0)
    out = sched.daily_after(["not a date", ""], 3, 5, 1, start)
    assert len(out) == 3


def test_an_explicit_start_beats_the_queue(monkeypatch, tmp_path, two_clips):
    """Picking a date is an instruction, not a suggestion: it must not be
    pushed back behind whatever else is scheduled."""
    sent = []
    _publisher(monkeypatch, sent)
    db = _FakeDB(clips=two_clips)
    monkeypatch.setattr(
        woop, "committed_times",
        lambda d: pytest.fail("a chosen start must not consult the queue"),
    )
    woop.publish_clips(
        db, tmp_path, clip_ids=[1], platforms=["youtube"],
        per_day=5, gap_hours=1, start_at=_at(0, 0),
    )
    assert sent[0]["scheduled_for"][11:16] == "09:00"


# ---- the caption leads with the hook ----------------------------------------
#
# TikTok has no title field, so the title was simply dropped and the caption
# opened with the description. The hook, the line written to stop someone
# scrolling, never reached the one platform where the caption is the hook.


def _caption(monkeypatch, tmp_path, *, title="", hook="", description=""):
    sent = []
    _publisher(monkeypatch, sent)
    clip = _Row(
        id=1, path=str(tmp_path / "a.mp4"), title=title, hook=hook,
        description=description, hashtags="", video_id="v", start_s=0, end_s=1,
    )
    (tmp_path / "a.mp4").write_bytes(b"x")

    captured = {}

    class FakePublisher:
        def __init__(self, client, project):
            pass

        def start(self, path, *, platforms, title, text, scheduled_for, overrides):
            captured["text"] = text
            captured["title"] = title
            return _Result(platforms)

    monkeypatch.setattr("publish.woopsocial.WoopSocialPublisher", FakePublisher)
    woop.publish_clips(
        _FakeDB(clips={1: clip}), tmp_path, clip_ids=[1], platforms=["tiktok"]
    )
    return captured


def test_the_title_opens_the_caption(monkeypatch, tmp_path):
    got = _caption(
        monkeypatch, tmp_path,
        title="She said WHAT about her brother",
        description="Creator reacts to a message from her brother.",
    )
    assert got["text"].startswith("She said WHAT about her brother")
    assert "Creator reacts" in got["text"], "the description still follows"


def test_the_hook_is_used_when_there_is_no_title(monkeypatch, tmp_path):
    got = _caption(monkeypatch, tmp_path, hook="The bit that goes viral", description="d")
    assert got["text"].startswith("The bit that goes viral")


def test_a_title_the_description_already_opens_with_is_not_repeated(monkeypatch, tmp_path):
    got = _caption(
        monkeypatch, tmp_path,
        title="Leg day",
        description="Leg day, and it did not go to plan.",
    )
    assert got["text"].count("Leg day") == 1


def test_a_clip_with_only_a_description_still_posts(monkeypatch, tmp_path):
    got = _caption(monkeypatch, tmp_path, description="Just a description.")
    assert got["text"] == "Just a description."


def test_a_clip_with_only_a_title_still_posts(monkeypatch, tmp_path):
    got = _caption(monkeypatch, tmp_path, title="Only a title")
    assert got["text"] == "Only a title"


# ---- "sending" has to stop saying sending -----------------------------------
#
# Every row sat at "processing" for good, because the only thing that asked
# the provider was a button in a view nobody had open. Sixty posts read as
# "sending" long after some had published and others had failed.


class _Worker:
    """The publish worker's refresh, without starting its thread."""

    def __init__(self, tmp_path):
        from server.publisher import PublishWorker

        self.w = PublishWorker.__new__(PublishWorker)
        self.w.data_dir = tmp_path
        self.w._next_provider_check = 0.0


def test_the_worker_asks_the_provider_on_its_own(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(woop, "is_enabled", lambda db: True)
    monkeypatch.setattr(woop, "has_key", lambda d: True)
    monkeypatch.setattr(woop, "in_flight", lambda db: ["req-a"])
    monkeypatch.setattr(
        woop, "refresh_in_flight",
        lambda db, d: called.setdefault("ran", True)
        or {"checked": 1, "updated": 1, "still_waiting": 0, "failed": 0},
    )
    _Worker(tmp_path).w._refresh_provider_posts(object())
    assert called.get("ran"), "nothing asked the provider"


def test_it_does_not_ask_again_immediately(monkeypatch, tmp_path):
    """Delivery takes hours. A tight loop would spend its life asking about
    posts due on Thursday."""
    runs = []
    monkeypatch.setattr(woop, "is_enabled", lambda db: True)
    monkeypatch.setattr(woop, "has_key", lambda d: True)
    monkeypatch.setattr(woop, "in_flight", lambda db: ["req-a"])
    monkeypatch.setattr(
        woop, "refresh_in_flight",
        lambda db, d: runs.append(1) or {"checked": 1, "updated": 0, "still_waiting": 1, "failed": 0},
    )
    worker = _Worker(tmp_path).w
    worker._refresh_provider_posts(object())
    worker._refresh_provider_posts(object())
    worker._refresh_provider_posts(object())
    assert len(runs) == 1, "asked more than once inside the interval"


def test_nothing_in_flight_asks_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(woop, "is_enabled", lambda db: True)
    monkeypatch.setattr(woop, "has_key", lambda d: True)
    monkeypatch.setattr(woop, "in_flight", lambda db: [])
    monkeypatch.setattr(
        woop, "refresh_in_flight", lambda db, d: pytest.fail("nothing to ask about")
    )
    _Worker(tmp_path).w._refresh_provider_posts(object())


def test_an_unreachable_provider_does_not_break_the_worker(monkeypatch, tmp_path, capsys):
    """This worker exists to run uploads. A status check failing must never
    disturb that."""
    monkeypatch.setattr(woop, "is_enabled", lambda db: True)
    monkeypatch.setattr(woop, "has_key", lambda d: True)
    monkeypatch.setattr(woop, "in_flight", lambda db: ["req-a"])

    def boom(db, d):
        raise RuntimeError("woopsocial is down")

    monkeypatch.setattr(woop, "refresh_in_flight", boom)
    _Worker(tmp_path).w._refresh_provider_posts(object())  # must not raise
    assert "woopsocial is down" in capsys.readouterr().out
