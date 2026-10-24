"""What a campaign asks of a post, checked before it exists (the editor's
picker) and once it is live, and the verdict that picks the next step.

Every check passes, fails or is unknown. A failure stops the submission (the
deadline expires it, a missing webhook sends it to be done by hand, anything
else stops it for review); an unknown stops only a webhook send, since doing a
step by hand is the review.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from campaigns import eligibility
from campaigns.model import FAIL, PASS, UNKNOWN, Check
from campaigns.urls import Resolution

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
TIKTOK = "https://www.tiktok.com/@example/video/7300000000000000001"


def _z(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _campaign(**fields):
    base = {"id": 1, "name": "Example Campaign", "method": "discord_assisted", "platforms": "[]",
            "required_accounts": "{}", "required_hashtags": "[]", "required_mentions": "[]",
            "required_text": "", "min_seconds": 0, "max_seconds": 0, "deadline": "",
            "trust_account": 0, "filters": "{}", "channel_id": "200000000000000002"}
    return {**base, **{k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in fields.items()}}


def _sub(**fields):
    return {"id": 1, "campaign_id": 1, "video_id": "vid", "start_s": 0.0, "end_s": 30.0,
            "platform": "tiktok", "method": "discord_assisted", "post_url": "", "url_source": "",
            "account": "", **fields}


def _live(**fields):
    return Resolution("live", **{"url": TIKTOK, "source": "provider", "visibility": "default",
                                 "account": "@example", **fields})


def _by_name(checks):
    return {c.name: (c.result, c.message) for c in checks}


def _pre(campaign, **kw):
    kw.setdefault("platform", "tiktok")
    kw.setdefault("duration_s", 30.0)
    return _by_name(eligibility.precheck(campaign, now=NOW, **kw))


# ---- before the post exists ----------------------------------------------------------------------

def test_a_campaign_with_no_rules_passes_its_three_standing_checks():
    assert _pre(_campaign()) == {"platform": (PASS, ""), "deadline": (PASS, ""), "duration": (PASS, "")}


def test_the_platform():
    assert _pre(_campaign(platforms=["tiktok", "youtube"]))["platform"][0] == PASS
    result, message = _pre(_campaign(platforms=["youtube"]))["platform"]
    assert result == FAIL and message == "This campaign doesn't take TikTok posts"


def test_the_deadline_and_a_scheduled_time_after_it():
    later = _campaign(deadline=_z(NOW + timedelta(days=3)))
    assert _pre(later)["deadline"] == (PASS, "")
    assert _pre(later, scheduled_for=_z(NOW + timedelta(days=2)))["deadline"] == (PASS, "")
    assert _pre(later, scheduled_for=_z(NOW + timedelta(days=4)))["deadline"] == (
        FAIL, "Its deadline is before this post's time")
    assert _pre(_campaign(deadline=_z(NOW - timedelta(minutes=1))))["deadline"][0] == FAIL
    assert _pre(_campaign(deadline=_z(NOW)))["deadline"][0] == FAIL
    assert _pre(_campaign(deadline="soon"))["deadline"][0] == UNKNOWN


@pytest.mark.parametrize("low, high, length, result", [
    (0, 0, 600.0, PASS), (10, 0, 9.5, FAIL), (10, 60, 60.0, PASS), (10, 60, 60.5, FAIL), (10, 60, 10.0, PASS),
])
def test_the_duration(low, high, length, result):
    found = _pre(_campaign(min_seconds=low, max_seconds=high), duration_s=length)["duration"]
    assert found[0] == result
    if result == FAIL:
        assert "this campaign takes" in found[1]


def test_hashtags_mentions_and_a_phrase_on_a_draft_caption():
    campaign = _campaign(required_hashtags=["clips", "Fun"], required_mentions=["example.co"],
                         required_text="Clips   Kitty")
    good = _pre(campaign, caption="Made with clips kitty #CLIPS #fun via @Example.co.")
    assert {k: good[k][0] for k in ("hashtags", "mentions", "text")} == {
        "hashtags": PASS, "mentions": PASS, "text": PASS}

    bad = _pre(campaign, caption="#clipsKitty #fun @example clipskitty")
    assert bad["hashtags"] == (FAIL, "Missing #clips")
    assert bad["mentions"] == (FAIL, "Missing @example.co")
    assert bad["text"] == (FAIL, "Missing “Clips   Kitty”")

    blank = _pre(campaign, caption="")
    assert {blank[k][0] for k in ("hashtags", "mentions", "text")} == {UNKNOWN}
    assert "hashtags" not in _pre(_campaign(), caption="anything")


# ---- once it is live -----------------------------------------------------------------------------

def _check(campaign=None, sub=None, res=None, source=None, destination=None):
    return _by_name(eligibility.check(campaign or _campaign(), sub or _sub(), res or _live(),
                                      source=source, now=NOW, destination=destination))


@pytest.mark.parametrize("visibility, result", [
    ("public", PASS), ("default", PASS), ("unlisted", FAIL), ("private", FAIL), ("friends", FAIL),
    ("followers", FAIL), ("", UNKNOWN),
])
def test_public(visibility, result):
    assert _check(res=_live(visibility=visibility))["public"][0] == result


def test_a_link_the_user_typed_is_public_on_their_word_and_a_bad_link_fails():
    assert _check(res=_live(source="manual", visibility=""))["public"] == (PASS, "You entered this link")
    assert _check(res=_live(url="https://www.tiktok.com/@example"))["public"][0] == FAIL
    assert _check(res=_live(url=""), sub=_sub(post_url=TIKTOK))["public"][0] == PASS


def test_the_caption_checks_run_on_the_caption_it_went_out_with():
    campaign = _campaign(required_hashtags=["clips"])
    assert _check(campaign, res=_live(caption="#clips"))["hashtags"][0] == PASS
    assert _check(campaign, res=_live(caption="none"))["hashtags"][0] == FAIL
    assert _check(campaign, res=_live(caption=""))["hashtags"][0] == UNKNOWN


def test_the_account():
    wants = _campaign(required_accounts={"tiktok": "Example"})
    assert _check(wants)["account"][0] == PASS
    assert _check(wants, res=_live(account="@someone_else"))["account"][0] == FAIL
    assert _check(wants, res=_live(account=""))["account"][0] == UNKNOWN
    channel = _campaign(required_accounts={"youtube": "examplechannel"})
    yt = _sub(platform="youtube")
    res = _live(url="https://www.youtube.com/shorts/abcdefghijk", account="Example Channel")
    assert _check(channel, sub=yt, res=res)["account"][0] == PASS
    assert "account" not in _check()


def test_routing():
    campaign = _campaign(filters={"plugins": ["example-dev/demo"], "modes": [], "creator_ids": []})
    assert _check(campaign, source={"plugin": "example-dev/demo", "mode": "pipeline"})["routing"][0] == PASS
    assert _check(campaign, source={"plugin": "example-dev/other", "mode": "pipeline"})["routing"][0] == FAIL
    assert _check(campaign, source={"plugin": "", "mode": "regular"})["routing"][0] == FAIL
    assert _check(campaign, source={"plugin": "", "mode": "unknown"})["routing"][0] == UNKNOWN
    assert _check(campaign, source=None)["routing"][0] == UNKNOWN

    modes = _campaign(filters={"modes": ["sports"], "creator_ids": [3]})
    assert _check(modes, source={"plugin": "", "mode": "sports", "creator_id": 3})["routing"][0] == PASS
    assert _check(modes, source={"plugin": "", "mode": "sports", "creator_id": 4})["routing"][0] == FAIL
    assert _check(modes, source={"plugin": "", "mode": "unknown", "creator_id": 3})["routing"][0] == UNKNOWN
    assert _check(modes, source={"plugin": "", "mode": "sports", "creator_id": None})["routing"][0] == UNKNOWN
    # A known mismatch beats an unknown.
    assert _check(modes, source={"plugin": "", "mode": "regular", "creator_id": None})["routing"][0] == FAIL
    assert "routing" not in _check(source=None)


def test_the_way_of_submitting():
    webhook = _sub(method="discord_webhook")
    assert _check(sub=webhook, destination={"state": "connected"})["destination"][0] == PASS
    assert _check(sub=webhook, destination={"state": "gone"})["destination"][0] == FAIL
    assert _check(sub=webhook, destination=None)["destination"][0] == FAIL
    assert "channel" not in _check(sub=webhook, destination={"state": "connected"})

    assert _check()["channel"] == (PASS, "")
    assert _check(_campaign(channel_id=""))["channel"][0] == UNKNOWN
    website = _check(sub=_sub(method="website"))
    assert "channel" not in website and "destination" not in website


def test_a_tiktok_short_link_is_a_warning():
    found = _check(res=_live(url="https://vm.tiktok.com/ZMexample1/"))
    assert found["public"][0] == PASS and found["link"][0] == UNKNOWN
    assert "link" not in _check()


def test_check_has_no_scheduled_time_so_a_live_post_inside_the_deadline_passes():
    campaign = _campaign(deadline=_z(NOW + timedelta(hours=1)))
    assert _check(campaign)["deadline"][0] == PASS
    assert _check(_campaign(deadline=_z(NOW - timedelta(hours=1))))["deadline"][0] == FAIL


# ---- the verdict -----------------------------------------------------------------------------------

def _v(*results, method="discord_webhook", trust=False):
    checks = [Check(name, result) for name, result in results]
    return eligibility.verdict(checks, method=method, trust_account=trust)


def test_the_verdict():
    assert _v(("platform", PASS), ("public", PASS)) == "ok"
    assert _v(("deadline", FAIL), ("destination", FAIL), ("public", FAIL)) == "expired"
    assert _v(("destination", FAIL), ("public", FAIL)) == "manual"
    assert _v(("public", FAIL)) == "review"
    assert _v(("hashtags", FAIL), method="discord_assisted") == "review"


@pytest.mark.parametrize("method, verdict", [
    ("discord_webhook", "review"), ("discord_assisted", "ok"), ("website", "ok"),
])
def test_an_unknown_stops_only_an_automatic_send(method, verdict):
    assert _v(("public", UNKNOWN), method=method) == verdict


def test_an_account_it_cant_see_counts_as_right_when_the_campaign_trusts_it():
    assert _v(("account", UNKNOWN), trust=False) == "review"
    assert _v(("account", UNKNOWN), trust=True) == "ok"
    assert _v(("account", UNKNOWN), ("public", UNKNOWN), trust=True) == "review"
    assert _v(("account", FAIL), trust=True) == "review"


# ---- where a clip came from -------------------------------------------------------------------------

def _source(db, **kw):
    kw.setdefault("video_id", "vid")
    kw.setdefault("start_s", 0.0)
    kw.setdefault("end_s", 30.0)
    return eligibility.clip_source(db, **kw)


def _video(db, creator=None):
    db.upsert_video("vid", title="t")
    if creator is not None:
        db.conn.execute("UPDATE videos SET creator_id = ? WHERE video_id = 'vid'", (creator,))
        db.conn.commit()


@pytest.mark.parametrize("payload, mode", [
    ({"url": "x"}, "regular"),
    ({"sport": "soccer"}, "sports"),
    ({"longform": True}, "longform"),
    ({"vertical_live": True}, "vertical_live"),
    ({"podcast": True}, "podcast"),
    ({"gaming": {"layout": "x"}}, "gaming"),
    ({"gaming_scoring": True}, "gaming"),
    ({"pipeline": {"id": "example-dev/demo"}}, "pipeline"),
])
def test_the_mode_comes_from_the_videos_newest_processing_job(db, payload, mode):
    _video(db)
    db.add_job("process", json.dumps({"url": "x", "podcast": True}), video_id="vid")
    db.add_job("render", json.dumps({"sport": "soccer"}), video_id="vid")
    db.add_job("process", json.dumps(payload), video_id="vid")
    assert _source(db)["mode"] == mode


def test_the_plugin_comes_from_the_clip_then_from_the_job(db):
    _video(db)
    stamped = db.add_clip("vid", 0.0, 30.0, 90, "h", scores=json.dumps({"plugin": "example-dev/demo"}))
    db.add_clip("vid", 40.0, 70.0, 90, "h")
    db.add_job("process", json.dumps({"pipeline": {"id": "example-dev/other"}}), video_id="vid")
    assert _source(db, clip_id=stamped) == {"plugin": "example-dev/demo", "mode": "pipeline", "creator_id": None}
    # Found by its window when the id isn't known.
    assert _source(db)["plugin"] == "example-dev/demo"
    assert _source(db, start_s=40.0, end_s=70.0)["plugin"] == "example-dev/other"


def test_automations_payload_stands_in_for_a_cleared_job(db):
    _video(db, creator=3)
    db.add_clip("vid", 0.0, 30.0, 90, "h")
    assert _source(db, job_payload={"sport": "basketball"}) == {
        "plugin": "", "mode": "sports", "creator_id": 3}
    assert _source(db, job_payload={"pipeline": "example-dev/demo"})["plugin"] == "example-dev/demo"


@pytest.mark.parametrize("opts, mode", [
    ({"profile": "longform"}, "longform"),
    ({"sport_moment": {"kind": "goal"}}, "sports"),
    ({"gaming": {"split": 0.5}}, "gaming"),
    ({}, "unknown"),
])
def test_without_a_job_the_render_options_say_what_they_can(db, opts, mode):
    _video(db)
    db.add_clip("vid", 0.0, 30.0, 90, "h", render_opts=json.dumps(opts))
    assert _source(db)["mode"] == mode


def test_nothing_known_is_unknown(db):
    assert _source(db, video_id="", clip_id=0) == {"plugin": "", "mode": "unknown", "creator_id": None}
