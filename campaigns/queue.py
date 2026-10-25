"""The submission queue: attaching posts to campaigns, following each post
until it is live with a public link, evaluating it then (DESIGN §5.3), and
the background step that sends what a webhook may send (DESIGN §6).

Everything lives in campaign_submissions. The background step
(`advance`, run by server/publisher.py's PublishWorker) keeps nothing in
memory but the global rate-limit pause it is handed, so after a restart it
carries on from the rows. Every status change goes through the state
machine, compare-and-set, so a route and the background step never both win.

A webhook send is written `sending` before the request goes out, and a row
left `sending` (a crash, or an exception between the request and its
outcome) is never sent again by itself: it becomes awaiting_confirmation
with a "check Discord" note, because a webhook has no way to ask whether a
message already arrived.
"""

import json
import sqlite3
from datetime import datetime, timedelta

from campaigns import eligibility, formatter, statemachine, urls, views
from campaigns.model import (
    CHECK_DUE_SECONDS,
    CHECK_FAILED_SECONDS,
    CHECK_SCHEDULED_SECONDS,
    FAIL,
    FAILED_POST_WAIT_HOURS,
    LABELS,
    METHODS,
    PLATFORMS,
    PRE_SEND,
    RETRY_DELAYS,
    SEND_SPACING_SECONDS,
    SENDING_STALE_SECONDS,
    SENDS_PER_PASS,
    STALE_DAYS,
    TEMPLATE_MAX,
    UNKNOWN,
    URL_WAIT_HOURS,
    cell,
    json_field,
    parse_utc,
    platform_label,
    utc_text,
)
from server import posts

#: The reason shown on a row that is now the user's to submit by hand.
BY_HAND = {
    "discord_assisted": "Submit it in Discord",
    "website": "Submit it on the campaign's page",
}

#: When an attached post is submitted (DESIGN §7 "Attach modes").
MODES = ("after_publish", "now", "review", "later")
#: Where an attach from a route came from (a watch attaches through attach_for_watch).
ORIGINS = ("editor", "later", "publish_all")
PROVIDERS = ("", "woopsocial", "upload_post", "youtube", "manual")
PROVIDER_LABELS = {"woopsocial": "WoopSocial", "upload_post": "Upload-Post", "youtube": "YouTube"}
#: Statuses an explicit re-attach (or a move) starts again.
REOPENABLE = ("cancelled", "expired", "failed")
#: Statuses a row can be edited (or moved) in.
EDITABLE = (*PRE_SEND, "failed")
#: Statuses the background step follows the post for (manual only without a link).
FOLLOWED = ("waiting_publication", "waiting_url")

GONE_CLIP = "That clip no longer exists"
NO_CAMPAIGN = "That campaign doesn't exist"
ARCHIVED = "That campaign is archived"
SWITCHED_OFF = "That campaign is switched off"
NO_DESTINATION = "The webhook to post to is missing: pick one or submit it by hand"
NO_TOKEN = ("This webhook's link isn't stored on this PC: paste it again in Settings → Discord "
            "(Edit), or submit it by hand")
RATE_LIMITED = "Discord asked Clips Kitty to slow down: it tries again shortly"
REFUSED = "Discord refused the message: change it, then retry"
SENT_IT = "Posted in Discord. This doesn't mean the campaign accepted it."


# ---- evaluation (DESIGN §5.3) -----------------------------------------------------------

def _duplicate(db, row: dict, url: str):
    return db.active_submission_with_url(int(row["campaign_id"]), url, exclude_id=int(row["id"]))


def evaluate(db, sub_id: int, res, *, now, event: str = "url_found", source: dict | None = None,
             discord_user: str = "", changes: dict | None = None):
    """Give submission `sub_id` the link in `res` (a urls.Resolution with a
    URL: the one found, or the one typed) and move it to what its checks say.

    1. Another row of the campaign that still counts already holds the link:
       this one is cancelled, "Already submitted to this campaign as #N".
    2. The link and what is known with it are saved (url, its source, post id,
       account, first seen, provider, due time) with every check.
    3. The verdict: expired; review (or the row's Review first); manual when
       its webhook can't be used; otherwise a webhook row is ready (due now,
       or held when saved for later) and an assisted or website row is the
       user's to submit by hand.

    `event` is the state machine's (url_found for the background step, set_url
    for a typed link, edit or campaign_changed when a change re-evaluates).
    `source` is eligibility.clip_source's answer, looked up here when the
    campaign routes and none is given. `changes` are other fields of the row
    (an edit's method, destination or message) evaluated as if already saved
    and written in the same compare-and-set as the outcome. Returns the row
    after it.

    Only a live resolution is taken (a typed link is built as one). A blocked
    one is the caller's to hand to the user with its reason: its link, if it
    has one, is for the user to check first.
    """
    if res.state != "live" or not res.url:
        raise ValueError("evaluate needs a live post's link")
    found = db.get_submission(sub_id)
    if found is None:
        raise statemachine.NotAllowed(f"Submission #{sub_id} doesn't exist")
    changes = dict(changes or {})
    row = {**dict(found), **changes}
    campaign = db.get_campaign(int(row["campaign_id"]))
    if campaign is None:
        raise statemachine.NotAllowed(f"Submission #{sub_id}'s campaign doesn't exist")
    campaign = dict(campaign)
    moment = posts.as_utc(now)

    saved = {
        "post_url": res.url,
        "url_source": res.source or row.get("url_source") or "",
        "post_id": res.post_id or row.get("post_id") or "",
        "account": res.account or row.get("account") or "",
        "posted_at": row.get("posted_at") or res.posted_at or utc_text(moment),
        "provider": res.provider or row.get("provider") or "",
        "due_at": res.due_at or row.get("due_at") or "",
    }
    duplicate = _duplicate(db, row, res.url)
    if duplicate is not None:
        return statemachine.apply(db, sub_id, event, now=now, to="cancelled", **{**saved, **changes},
                                  status_reason=f"Already submitted to this campaign as #{duplicate['id']}")

    method = str(row.get("method") or campaign.get("method") or "")
    destination = None
    if method == "discord_webhook" and int(row.get("destination_id") or 0):
        destination = db.get_destination(int(row["destination_id"]))
    if source is None and any(json_field(campaign.get("filters"), {}).values()):
        source = eligibility.clip_source(db, video_id=row["video_id"], start_s=row["start_s"],
                                         end_s=row["end_s"], clip_id=int(row.get("clip_id") or 0))

    seen = {**row, **saved}
    checks = eligibility.check(campaign, seen, res, source=source, now=now, destination=destination)
    values = formatter.values_for(db, seen, campaign, discord_user=discord_user)
    checks += formatter.message_checks(campaign, values, method=method,
                                       message_text=str(row.get("message_text") or ""), dest=destination)
    verdict = eligibility.verdict(checks, method=method,
                                  trust_account=bool(int(cell(campaign, "trust_account", 0) or 0)))
    target, reason, extra = _outcome(verdict, checks, row, method, moment.timestamp())
    if found["status"] == "failed":
        # Out of failed with a new link or setting: its sends start counting
        # again, as a retry's do, and the last send's error is no longer news.
        extra = {"attempts": 0, "error": "", **extra}
    fields = {**saved, **extra, **changes}
    try:
        return statemachine.apply(db, sub_id, event, now=now, to=target, **fields,
                                  checks=json.dumps([c.as_dict() for c in checks]), status_reason=reason)
    except sqlite3.IntegrityError:
        # The link index caught a row that took this link meanwhile.
        duplicate = _duplicate(db, row, res.url)
        number = f"#{duplicate['id']}" if duplicate is not None else "another submission"
        return statemachine.apply(db, sub_id, event, now=now, to="cancelled", **{**saved, **changes},
                                  status_reason=f"Already submitted to this campaign as {number}")


def reopen(db, sub_id: int, *, now, **fields):
    """Re-attach a cancelled, expired or failed submission (the user's
    explicit attach or a move, never automation) and return the row after it.

    The state machine's reopen clears the row's link, so the link is checked
    against the campaign's other rows again like any new one. A provider's
    link is found again by resolution; a link the user typed is found by
    nothing, so it is given back here, through evaluation and its duplicate
    check, at once."""
    before = db.get_submission(sub_id)
    if before is None:
        raise statemachine.NotAllowed(f"Submission #{sub_id} doesn't exist")
    typed = str(before["post_url"] or "")
    if before["url_source"] != "manual" and before["provider"] != "manual":
        typed = ""
    row = statemachine.apply(db, sub_id, "reopen", now=now, **fields)
    if not typed:
        return row
    url, problem = urls.validate(row["platform"], typed)
    if problem:
        return row
    res = urls.Resolution("live", url=url, source="manual",
                          account=urls.account_from_url(row["platform"], url))
    try:
        return evaluate(db, sub_id, res, now=now, event="set_url")
    except statemachine.NotAllowed:
        # The background step moved it on meanwhile: what it did stands.
        return db.get_submission(sub_id)


def _outcome(verdict: str, checks, row: dict, method: str, seconds: float) -> tuple[str, str, dict]:
    """(status, reason, other fields) for a verdict."""
    def said(results) -> str:
        return "; ".join(c.message for c in checks if c.result in results and c.message)

    if verdict == "expired":
        return "expired", "The campaign's deadline passed", {}
    if verdict == "review":
        held_up = said((FAIL,)) or (said((UNKNOWN,)) if method == "discord_webhook" else "")
        return "review", held_up or "Check it before it is submitted", {}
    if int(row.get("review_first") or 0):
        return "review", "Check it before it is submitted", {}
    if verdict == "manual":
        return "manual", said((FAIL,)) or "Submit it by hand", {}
    if int(row.get("hold") or 0):
        return "ready", "Saved for later", {}
    if method == "discord_webhook":
        return "ready", "", {"next_attempt_at": seconds}
    return "manual", BY_HAND.get(method, "Submit it by hand"), {}


def typed_link(platform: str, url: str) -> urls.Resolution:
    """A link the user typed (already validated), as evaluation takes it."""
    return urls.Resolution("live", url=url, source="manual", account=urls.account_from_url(platform, url))


def current_resolution(db, row, *, now) -> urls.Resolution | None:
    """The row's own link as a live resolution, to evaluate it again after
    a change of method or destination: a link the user typed as typed, a
    provider's as resolution finds it now (its caption and visibility
    included), else as the row holds it. None when it has no link."""
    sub = dict(row)
    url = str(sub.get("post_url") or "")
    if not url:
        return None
    if sub.get("url_source") == "manual":
        return typed_link(sub["platform"], url)
    found = urls.resolve(db, sub, now=now)
    if found.state == "live" and found.url == url:
        return found
    return urls.Resolution("live", url=url, source=str(sub.get("url_source") or "provider"),
                           provider=str(sub.get("provider") or ""), post_id=str(sub.get("post_id") or ""),
                           account=str(sub.get("account") or ""), due_at=str(sub.get("due_at") or ""))


# ---- attaching ------------------------------------------------------------------------------

def mode_fields(mode: str, campaign) -> tuple[int, int]:
    """(hold, review_first) for an attach mode."""
    review_first = int(cell(campaign, "review_first", 0) or 0)
    if mode == "review":
        return 0, 1
    if mode == "later":
        return 1, review_first
    return 0, review_first


def campaign_refusal(campaign, *, platform: str, scheduled_for: str = "", now) -> str:
    """Why a post on `platform` (due at `scheduled_for`, '' for now) can't be
    attached to `campaign`; '' when it can. A deadline that passes before the
    post's time refuses it too, so it never waits for a post that will be late."""
    if campaign is None:
        return NO_CAMPAIGN
    if int(cell(campaign, "archived", 0) or 0):
        return ARCHIVED
    if not int(cell(campaign, "enabled", 1) or 0):
        return SWITCHED_OFF
    for check in eligibility.precheck(campaign, platform=platform, duration_s=0,
                                      scheduled_for=scheduled_for, now=now):
        if check.name in ("platform", "deadline") and check.result == FAIL:
            return check.message
    return ""


def clip_title(clip) -> str:
    return str(cell(clip, "title", "") or cell(clip, "hook", "") or "")


def _identity(db, clip_id: int, platform: str) -> dict | None:
    """The post's lasting identity for a clip: its window, from the clip row,
    else from a post recorded for it (a clip deleted after it went out)."""
    clip = db.get_clip(int(clip_id))
    if clip is not None:
        return {"clip_id": int(clip_id), "video_id": clip["video_id"], "start_s": float(clip["start_s"]),
                "end_s": float(clip["end_s"]), "clip_title": clip_title(clip)}
    for table, extra in (("clip_publishes", " ORDER BY (platform = ?) DESC"), ("uploads", "")):
        args = (int(clip_id), platform) if extra else (int(clip_id),)
        row = db.conn.execute(
            f"SELECT video_id, start_s, end_s FROM {table} WHERE clip_id = ? AND video_id != ''{extra} LIMIT 1",
            args,
        ).fetchone()
        if row is not None:
            return {"clip_id": int(clip_id), "video_id": row["video_id"], "start_s": float(row["start_s"]),
                    "end_s": float(row["end_s"]), "clip_title": ""}
    return None


def _post_time(db, ident: dict, platform: str, provider: str, publish_job_id: int) -> str:
    """When the post is due (UTC text), '' for now or unknown: the scheduled
    time a provider row or a YouTube upload (or its queued job) carries."""
    if platform == "youtube" and provider in ("youtube", "") and publish_job_id:
        job = db.get_publish_job(int(publish_job_id))
        if job is not None:
            try:
                asked = json.loads(job["request"] or "{}")
            except (TypeError, ValueError):
                asked = {}
            if isinstance(asked, dict) and asked.get("publish_at"):
                return str(asked["publish_at"])
    if provider != "youtube":
        row = db.conn.execute(
            "SELECT scheduled_for FROM clip_publishes WHERE platform = ? AND (clip_id = ? OR "
            "(video_id != '' AND video_id = ? AND start_s = ? AND end_s = ?)) "
            "ORDER BY updated_at DESC LIMIT 1",
            (platform, ident["clip_id"], ident["video_id"], round(ident["start_s"], 2), round(ident["end_s"], 2)),
        ).fetchone()
        if row is not None and row["scheduled_for"]:
            return str(row["scheduled_for"])
    if platform == "youtube":
        row = db.conn.execute(
            "SELECT publish_at FROM uploads WHERE clip_id = ? OR (video_id != '' AND video_id = ? AND "
            "start_s = ? AND end_s = ?) ORDER BY (clip_id = ?) DESC LIMIT 1",
            (ident["clip_id"], ident["video_id"], round(ident["start_s"], 2), round(ident["end_s"], 2),
             ident["clip_id"]),
        ).fetchone()
        if row is not None and row["publish_at"]:
            return str(row["publish_at"])
    return ""


def _first_look(db, sub_id: int, typed: str, *, now):
    """A new (or reopened) row's first evaluation, in the request: a typed
    link is evaluated, anything else is looked up once, so a post that is
    already live comes back with its words to submit."""
    row = db.get_submission(sub_id)
    try:
        if typed:
            event = "set_url" if statemachine.allowed(row, "set_url") else "edit"
            return evaluate(db, sub_id, typed_link(row["platform"], typed), now=now, event=event)
        follow(db, row, now=now)
    except statemachine.NotAllowed:
        pass  # the background step moved it on meanwhile: what it did stands
    return db.get_submission(sub_id)


#: The publishers an attach can name a post of (a "manual" attach with no link names none).
PUBLISHERS = ("woopsocial", "upload_post", "youtube")


def _not_out(db, row, *, now) -> bool:
    """Whether the post a followed row follows hasn't come out: it failed, was
    skipped, is gone, or never came out. One that went live (its link still
    to come, or not yet looked at) has."""
    if row["status"] == "waiting_url":
        return False
    res = urls.resolve(db, row, now=now)
    return res.state == "not_published" or (res.state == "blocked" and res.provider == "manual" and not res.url)


def _due_now(db, row, *, provider: str = "", publish_job_id: int = 0, now) -> None:
    """Attached again: a row still followed is looked at on the next pass.
    It follows the publish this attach comes with (its provider, and a direct
    YouTube upload's new job) only when the attach names a publisher and the
    post the row follows hasn't come out (published again after a failed
    post): a post that went live stays the one it follows."""
    if row["status"] in FOLLOWED or (row["status"] == "manual" and not row["post_url"]):
        fields: dict = {"next_attempt_at": 0}
        if provider in PUBLISHERS and _not_out(db, row, now=now):
            fields["provider"] = provider
            if publish_job_id:
                fields["publish_job_id"] = int(publish_job_id)
        db.set_submission(int(row["id"]), expect=(row["status"],), match={"post_url": row["post_url"]}, **fields)


def _typed_on_existing(db, row, typed: str, *, now) -> tuple[dict | None, str]:
    """A link the user typed for a post the campaign already has a row for:
    (the row after, '') when the row takes it, as an edit of its link would;
    (None, why) when it can't (it was already sent, or it is closed)."""
    if typed == row["post_url"]:
        return db.get_submission(int(row["id"])), ""
    if row["status"] not in EDITABLE:
        return None, f"It is already in this campaign as #{row['id']} ({LABELS[row['status']]})"
    event = "set_url" if statemachine.allowed(row, "set_url") else "edit"
    try:
        return evaluate(db, int(row["id"]), typed_link(row["platform"], typed), now=now, event=event), ""
    except statemachine.NotAllowed:
        return db.get_submission(int(row["id"])), ""  # the background step moved it on meanwhile


def attach(db, *, items, campaign_ids, now, mode: str = "after_publish", provider: str = "",
           publish_job_id: int = 0, post_url: str = "", origin: str = "editor",
           reopen_rows: bool = False) -> dict:
    """Submissions for posts, one per campaign × clip × platform:
    {"created": [rows], "existing": [rows], "refused": [{campaign_id, clip_id,
    platform, reason}]}.

    `items` are {"clip_id", "platforms"}. A row is created at
    waiting_publication with the mode's hold and Review first, then looked at
    once here, so a post already live is evaluated at once. A row the post
    already has in a campaign is returned as `existing` (looked at again on
    the next pass, through this attach's publisher and YouTube job when the
    post it followed hasn't come out); with `reopen_rows`, a cancelled, expired or
    failed one starts again with the campaign's method and webhook (unless
    it holds its own) and is listed as created. A campaign that is missing,
    archived, switched off, doesn't take the platform, or whose deadline has
    passed (or passes before the post's time) refuses the item.

    `post_url` is a link the user typed, for exactly one clip on one
    platform: it is checked for a duplicate in each campaign before anything
    is written, then evaluated after the insert. On an existing row it is
    taken as an edit of the row's link would take it; a row already sent or
    closed (and not reopened) refuses a different link. Raises ValueError
    for a request that can't be right whatever the campaign.
    """
    if mode not in MODES:
        raise ValueError("Pick when to submit it: after publishing, now, review first, or later")
    if origin not in ORIGINS:
        raise ValueError(f"Unknown origin: {origin}")
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    ids = list(dict.fromkeys(int(c) for c in campaign_ids or []))
    if not ids:
        raise ValueError("Pick a campaign")
    pairs: list[tuple[int, str]] = []
    for item in items or []:
        platforms = [str(p or "").strip().lower() for p in (item.get("platforms") or [])]
        if not platforms:
            raise ValueError("Pick the platforms to submit")
        for platform in dict.fromkeys(platforms):
            if platform not in PLATFORMS:
                raise ValueError(f"Clips Kitty can't submit {platform or 'those'} posts to a campaign")
            pairs.append((int(item["clip_id"]), platform))
    if not pairs:
        raise ValueError("Pick a clip to submit")
    if provider == "youtube" and any(platform != "youtube" for _, platform in pairs):
        raise ValueError("A direct YouTube upload is on YouTube only")
    typed = ""
    if post_url:
        if len(pairs) != 1:
            raise ValueError("A link you type goes with one clip on one platform")
        typed, problem = urls.validate(pairs[0][1], post_url)
        if problem:
            raise ValueError(problem)

    moment = posts.as_utc(now)
    campaigns = {cid: db.get_campaign(cid) for cid in ids}
    out: dict = {"created": [], "existing": [], "refused": []}
    for clip_id, platform in pairs:
        ident = _identity(db, clip_id, platform)
        when = _post_time(db, ident, platform, provider, publish_job_id) if ident else ""
        for cid in ids:
            campaign = campaigns[cid]
            problem = GONE_CLIP if ident is None else \
                campaign_refusal(campaign, platform=platform, scheduled_for=when, now=moment)
            key = (cid, ident["video_id"], ident["start_s"], ident["end_s"], platform) if ident else ()
            existing = db.find_submission(*key) if key and not problem else None
            if typed and not problem:
                taken = db.active_submission_with_url(cid, typed,
                                                      exclude_id=int(existing["id"]) if existing else 0)
                if taken is not None:
                    problem = f"Already submitted to this campaign as #{taken['id']}"
            if problem:
                out["refused"].append({"campaign_id": cid, "clip_id": clip_id, "platform": platform,
                                       "reason": problem})
                continue
            hold, review_first = mode_fields(mode, campaign)
            fields = {"clip_id": ident["clip_id"], "clip_title": ident["clip_title"],
                      "provider": provider or ("manual" if typed else ""),
                      "publish_job_id": int(publish_job_id or 0), "origin": origin,
                      "hold": hold, "review_first": review_first}
            if existing is None:
                new_id = db.insert_submission(*key, **fields, method=campaign["method"],
                                              destination_id=int(campaign["destination_id"] or 0))
                if new_id is not None:
                    out["created"].append(_first_look(db, new_id, typed, now=moment))
                    continue
                existing = db.find_submission(*key)  # another request made it meanwhile
            if reopen_rows and existing["status"] in REOPENABLE:
                kept = {k: v for k, v in fields.items() if v or k in ("hold", "review_first")}
                # Its method and webhook are the campaign's as it is now,
                # unless the row was given its own (a campaign edit skipped
                # it while it was closed).
                own = json_field(existing["overridden"], [])
                kept.update({f: v for f, v in (("method", campaign["method"]),
                                               ("destination_id", int(campaign["destination_id"] or 0)))
                             if f not in own})
                try:
                    reopen(db, int(existing["id"]), now=moment, **kept)
                except statemachine.NotAllowed:
                    out["existing"].append(db.get_submission(int(existing["id"])))
                    continue
                out["created"].append(_first_look(db, int(existing["id"]), typed, now=moment))
                continue
            if typed:
                after, why = _typed_on_existing(db, existing, typed, now=moment)
                if why:
                    out["refused"].append({"campaign_id": cid, "clip_id": clip_id, "platform": platform,
                                           "reason": why})
                else:
                    out["existing"].append(after)
                continue
            _due_now(db, existing, provider=provider, publish_job_id=publish_job_id, now=moment)
            out["existing"].append(db.get_submission(int(existing["id"])))
    return out


def attach_for_watch(db, *, watch, item, campaign_ids, job_payload: dict | None = None, now) -> int:
    """A watched video's posts attached to its watch's campaigns (DESIGN
    §6.4); returns how many submissions were made. Idempotent: a post a
    campaign already has keeps its row, so a retry or a re-send makes none.

    The posts are read from the database, not from a publish call's answer:
    the clip_publishes rows of the item's video still alive (queued,
    processing, published, sending), limited to the item's chosen clips when
    it has them. A platform that was skipped or refused has no such row and
    so gets no submission. Each campaign must be on, not archived, take the
    platform, have a deadline that holds for the post's time, and route the
    clip (a filter that is set never matches a clip whose source is
    unknown). Rows start with no hold and the campaign's Review first.
    Never reopens: a submission the user cancelled stays cancelled.
    """
    from server.woopsocial_service import ALIVE

    video_id = str(cell(item, "video_id", "") or "")
    if not video_id:
        return 0
    moment = posts.as_utc(now)
    rows = db.conn.execute(
        f"SELECT * FROM clip_publishes WHERE video_id = ? AND state IN ({','.join('?' * len(ALIVE))}) "
        "ORDER BY clip_id, platform",
        (video_id, *ALIVE),
    ).fetchall()
    windows = set()
    for window in json_field(cell(item, "chosen_clips", ""), []):
        try:
            windows.add((round(float(window[0]), 2), round(float(window[1]), 2)))
        except (IndexError, KeyError, TypeError, ValueError):
            continue
    picked = []
    for row in rows:
        clip = db.get_clip(int(row["clip_id"]))
        if windows:
            posted = (round(float(row["start_s"]), 2), round(float(row["end_s"]), 2))
            current = (round(float(clip["start_s"]), 2), round(float(clip["end_s"]), 2)) if clip else None
            if posted not in windows and current not in windows:
                continue
        picked.append((row, clip))

    created = 0
    ref = f"watch:{int(cell(watch, 'id', 0))}/item:{int(cell(item, 'id', 0))}"
    for cid in dict.fromkeys(int(c) for c in campaign_ids or []):
        campaign = db.get_campaign(cid)
        routed = any(json_field(cell(campaign, "filters", ""), {}).values()) if campaign else False
        for row, clip in picked:
            if row["platform"] not in PLATFORMS or campaign_refusal(
                    campaign, platform=row["platform"], scheduled_for=str(cell(row, "scheduled_for", "") or ""),
                    now=moment):
                continue
            if routed:
                source = eligibility.clip_source(db, video_id=video_id, start_s=row["start_s"],
                                                 end_s=row["end_s"], clip_id=int(row["clip_id"]),
                                                 job_payload=job_payload)
                if not eligibility.routes(campaign, source):
                    continue
            made = db.insert_submission(
                cid, video_id, row["start_s"], row["end_s"], row["platform"], clip_id=int(row["clip_id"]),
                clip_title=clip_title(clip) if clip is not None else "", provider=row["provider"] or "",
                origin="watch", origin_ref=ref, hold=0, review_first=int(campaign["review_first"] or 0),
                method=campaign["method"], destination_id=int(campaign["destination_id"] or 0),
            )
            created += made is not None
    return created


# ---- following a post ----------------------------------------------------------------------

def _local(text) -> datetime | None:
    """A created_at (local time, no zone) as an aware datetime."""
    try:
        moment = datetime.fromisoformat(str(text or "").strip())
    except ValueError:
        return None
    return moment.astimezone() if moment.tzinfo is None else moment


def _later(db, sub: dict, seconds: float, known: dict) -> None:
    """Nothing changed: look again in `seconds`, keeping what the look
    learned (provider, due time). Only while the row is as it was read."""
    db.set_submission(int(sub["id"]), expect=(sub["status"],), match={"post_url": sub["post_url"]},
                      next_attempt_at=seconds, **known)


def _blocked(db, sub: dict, res, known: dict, moment) -> dict | None:
    """A post found but not usable as it is (private, a link that doesn't
    look like a post's, a deleted upload's): the user's, with the reason. A
    link that would pass is put in for the user to check."""
    seconds = moment.timestamp()
    reason = res.reason or "Check this post, then enter its link"
    fields = {**known, "status_reason": reason, "next_attempt_at": seconds + CHECK_FAILED_SECONDS}
    if res.url:
        url, problem = urls.validate(sub["platform"], res.url)
        if not problem and _duplicate(db, sub, url) is None:
            fields.update(post_url=url, url_source=res.source or "", post_id=res.post_id or sub["post_id"])
    if sub["status"] == "manual" and "post_url" not in fields and sub["status_reason"] == reason:
        _later(db, sub, fields["next_attempt_at"], known)
        return None
    try:
        return statemachine.apply(db, int(sub["id"]), "url_found", now=moment, to="manual", **fields)
    except sqlite3.IntegrityError:
        for name in ("post_url", "url_source", "post_id"):
            fields.pop(name, None)
        return statemachine.apply(db, int(sub["id"]), "url_found", now=moment, to="manual", **fields)


def _failure(res, sub: dict, platform: str) -> str:
    """The reason for a post that failed or was skipped; '' for any other."""
    if res.outcome == "skipped":
        where = PROVIDER_LABELS.get(res.provider or sub["provider"], "the publisher")
        return (f"{platform} isn't connected on {where}, so nothing was posted there: "
                "publish it there or enter its link")
    if res.outcome == "failed":
        return f"The post failed on {platform}: publish it again or enter its link"
    return ""


def _uploading(db, sub: dict) -> bool:
    """Its own direct YouTube upload is queued or running (the YouTube panel
    attaches while it is): looked at each minute until the job ends."""
    if sub["provider"] != "youtube" or not int(sub["publish_job_id"] or 0):
        return False
    job = db.get_publish_job(int(sub["publish_job_id"]))
    return job is not None and job["status"] in ("queued", "running")


def follow(db, row, *, now):
    """One look at where a followed submission's post stands, and the step
    it leads to (DESIGN §4.2, §6.1): its link found (evaluated), published
    without a link (waits for one a day), failed or skipped (the user's,
    except a watch's failed post the watch may still send again for 48
    hours), not seen live 14 days after its time (the user's). A look that
    changes nothing sets the next one: in a minute while the post is due,
    10 minutes while it is scheduled ahead, an hour while it waits on a
    failed post or the user (a minute while the user's row waits on its own
    direct upload). A row already the user's whose post now reads failed or
    skipped takes that reason. Returns the row after a status change, else
    None.
    """
    sub = dict(row)
    status = sub["status"]
    if status not in FOLLOWED and not (status == "manual" and not sub["post_url"]):
        return None
    moment = posts.as_utc(now)
    seconds = moment.timestamp()
    res = urls.resolve(db, sub, now=moment)
    known = {}
    if res.provider and res.provider != sub["provider"]:
        known["provider"] = res.provider
    if res.due_at and res.due_at != sub["due_at"]:
        known["due_at"] = res.due_at

    def step(event: str, wait: float = CHECK_FAILED_SECONDS, **fields):
        return statemachine.apply(db, int(sub["id"]), event, now=moment, **known, **fields,
                                  next_attempt_at=seconds + wait)

    if res.state == "live":
        return evaluate(db, int(sub["id"]), res, now=moment, event="url_found")
    if res.state == "blocked":
        return _blocked(db, sub, res, known, moment)
    platform = platform_label(sub["platform"])
    failure = _failure(res, sub, platform)
    if status == "manual":
        # A publish it was moved onto that failed too says so (not the
        # first post's reason).
        if failure and failure != sub["status_reason"]:
            known.update(status_reason=failure, error=res.reason[:300])
        wait = CHECK_DUE_SECONDS if _uploading(db, sub) else CHECK_FAILED_SECONDS
        _later(db, sub, seconds + wait, known)
        return None

    if res.state == "published_no_url":
        if status == "waiting_publication":
            return step("published_no_url", CHECK_DUE_SECONDS)
        first = parse_utc(sub["posted_at"]) or moment
        if moment - first >= timedelta(hours=URL_WAIT_HOURS):
            return step("url_overdue")
        _later(db, sub, seconds + CHECK_DUE_SECONDS, known)
        return None
    if status == "waiting_publication" and res.outcome == "skipped":
        return step("post_skipped", error=res.reason[:300], status_reason=failure)
    if status == "waiting_publication" and res.outcome == "failed":
        failed_at = parse_utc(res.failed_at) or moment
        if sub["origin"] == "watch" and moment - failed_at < timedelta(hours=FAILED_POST_WAIT_HOURS):
            reason = f"The post failed on {platform}: waiting for the watch to send it again"
            if sub["status_reason"] == reason:
                _later(db, sub, seconds + CHECK_FAILED_SECONDS, known)
            else:
                step("post_failed", to="waiting_publication", status_reason=reason, error=res.reason[:300])
            return None
        return step("post_failed", to="manual", error=res.reason[:300], status_reason=failure)

    # Still on its way (or scheduled ahead).
    due = parse_utc(res.due_at or sub["due_at"])
    since = due or _local(sub["created_at"]) or moment
    if moment - since >= timedelta(days=STALE_DAYS):
        return step("stale")
    if res.outcome:
        wait = CHECK_FAILED_SECONDS
    elif due is not None and due > moment:
        wait = CHECK_SCHEDULED_SECONDS
    else:
        wait = CHECK_DUE_SECONDS
    _later(db, sub, seconds + wait, known)
    return None


# ---- sending by webhook -----------------------------------------------------------------------

def _wait_text(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = round(minutes / 60)
    return f"{hours} hour{'s' if hours != 1 else ''}"


def _send(db, data_dir, http, row, *, moment, user: str, out: dict, tell) -> None:
    """Send one due webhook row: written `sending` first, then the request,
    then what Discord's answer means (DESIGN §4.2)."""
    from campaigns import discord_webhook, service

    sub = dict(row)
    sub_id = int(sub["id"])
    seconds = moment.timestamp()
    dest_id = int(sub["destination_id"] or 0)
    dest = db.get_destination(dest_id) if dest_id else None
    hook = service.load_webhook(data_dir, dest_id) if dest is not None else None
    if dest is None or dest["state"] != "connected" or hook is None:
        reason = NO_DESTINATION if dest is None else NO_TOKEN if dest["state"] == "connected" else \
            f"The webhook is {dest['state']}: submit it by hand or pick another"
        tell(statemachine.apply(db, sub_id, "destination_unusable", now=moment, status_reason=reason), "ready")
        return
    if seconds - float(dest["last_sent_at"] or 0) < SEND_SPACING_SECONDS:
        out["more"] = True  # this destination posted a moment ago: next pass
        return
    campaign = db.get_campaign(int(sub["campaign_id"]))
    if campaign is None:
        return
    values = formatter.values_for(db, sub, campaign, discord_user=user)
    problems = formatter.message_checks(campaign, values, method="discord_webhook",
                                        message_text=str(sub["message_text"] or ""), dest=dest)
    if problems:
        # Its words changed since they were checked (an edit of its values or
        # message, the campaign's fields or template): it waits for the user
        # rather than going out as it is (DESIGN §5.4). The edit's evaluation,
        # made here at the last moment before a send.
        checks = [c for c in json_field(sub["checks"], []) if isinstance(c, dict)
                  and c.get("name") not in ("fields", "length")]
        tell(statemachine.apply(db, sub_id, "edit", now=moment, to="review",
                                status_reason="; ".join(c.message for c in problems),
                                checks=json.dumps(checks + [c.as_dict() for c in problems])), "ready")
        return
    payload = formatter.webhook_payload(campaign, sub, values, dest)

    statemachine.apply(db, sub_id, "send_start", now=moment)  # before the request, always
    db.set_destination(dest_id, last_sent_at=seconds)
    out["sent"] += 1
    result = discord_webhook.send(http, hook["webhook_id"], hook["token"], content=payload["content"],
                                  thread_name=payload.get("thread_name", ""), thread_id=str(dest["thread_id"] or ""))

    kind = result.kind
    if kind == "ok":
        after = statemachine.apply(db, sub_id, "send_ok", now=moment, status_reason=SENT_IT,
                                   discord_message_id=result.message_id,
                                   discord_channel_id=result.channel_id or str(dest["channel_id"] or ""))
        if dest["last_error"]:
            db.set_destination(dest_id, last_error="")
    elif kind == "rate_limited":
        after = statemachine.apply(db, sub_id, "rate_limited", now=moment, status_reason=RATE_LIMITED,
                                   next_attempt_at=seconds + result.retry_after + 1)
        if result.is_global:
            out["paused_until"] = max(out["paused_until"], seconds + result.retry_after)
    elif kind in ("server_error", "unreachable"):
        attempts = int(sub["attempts"] or 0) + 1
        if attempts > len(RETRY_DELAYS):
            after = statemachine.apply(db, sub_id, "send_retry", now=moment, to="failed", attempts=attempts,
                                       error=result.message,
                                       status_reason=f"Couldn't post it after {attempts} tries: retry, "
                                                     "or submit it by hand")
        else:
            wait = RETRY_DELAYS[attempts - 1]
            after = statemachine.apply(db, sub_id, "send_retry", now=moment, to="ready", attempts=attempts,
                                       error=result.message, next_attempt_at=seconds + wait,
                                       status_reason=f"{result.message}: it tries again in {_wait_text(wait)}")
    elif kind == "ambiguous":
        after = statemachine.apply(db, sub_id, "send_ambiguous", now=moment, error=result.message)
    elif kind == "gone":
        after = statemachine.apply(db, sub_id, "send_gone", now=moment, error=result.message)
        db.set_destination(dest_id, state="gone", last_error=result.message)
        service.wipe_webhook(data_dir, dest_id)
        for other in db.list_submissions(statuses=("ready",), limit=1_000_000):
            if int(other["destination_id"] or 0) != dest_id or int(other["id"]) == sub_id:
                continue
            try:
                tell(statemachine.apply(db, int(other["id"]), "destination_unusable", now=moment,
                                        status_reason=statemachine.REASONS["send_gone"]), "ready")
            except statemachine.NotAllowed:
                continue  # not a webhook row, or it changed meanwhile
    elif kind == "denied":
        after = statemachine.apply(db, sub_id, "send_denied", now=moment, error=result.message)
        db.set_destination(dest_id, state="denied", last_error=result.message)
    elif kind == "forum_needs_thread":
        after = statemachine.apply(db, sub_id, "send_forum", now=moment, error=result.message)
        db.set_destination(dest_id, last_error=result.message)
    else:  # bad_request
        after = statemachine.apply(db, sub_id, "send_refused", now=moment, error=result.message,
                                   status_reason=REFUSED)
    tell(after, "sending")


# ---- the background step -------------------------------------------------------------------------

def advance(db, data_dir, *, now, http, emit=None, paused_until: float = 0.0) -> dict:
    """One pass of the background step (DESIGN §6.1), never raising:

    1. rows left `sending` over SENDING_STALE_SECONDS go to
       awaiting_confirmation ("Check Discord"), never sent again by themselves;
    2. rows not yet sent whose campaign's deadline passed expire, and an
       archived campaign's rows not yet sent (out sending when it was
       archived, then back unsent) are cancelled;
    3. up to 100 followed rows that are due get a look (follow);
    4. due webhook rows are sent: at most SENDS_PER_PASS, one per destination
       every SEND_SPACING_SECONDS, none while Discord's global rate limit
       (`paused_until`, unix seconds, held by the caller) lasts, and a row
       whose words no longer pass their checks goes to review instead.

    Steps 3 and 4 skip the rows of a campaign that is switched off or
    archived: nothing is followed or posted for it until it is on again.

    `emit` gets a `campaign` event for each status change. Returns
    {"changed", "sent", "paused_until", "more"}: `more` when sends were left
    for the next pass (the limit, or a destination's spacing).
    """
    moment = posts.as_utc(now)
    seconds = moment.timestamp()
    out = {"changed": 0, "sent": 0, "paused_until": float(paused_until or 0), "more": False}
    names: dict[int, str] = {}
    # Transport exceptions may contain webhook credentials, so diagnostics
    # below describe the failed step without printing exception text.

    def tell(after, previous: str, previous_url: str | None = None) -> None:
        """Announce a status change; with `previous_url`, a row that got its
        link in the same status too (a manual row with no link that found it)."""
        if after is None or (after["status"] == previous
                             and (previous_url is None or after["post_url"] == previous_url)):
            return
        out["changed"] += 1
        if emit is None:
            return
        cid = int(after["campaign_id"])
        if cid not in names:
            found = db.get_campaign(cid)
            names[cid] = str(found["name"]) if found is not None else ""
        try:
            emit(views.event(after, previous, names[cid]))
        except Exception:
            print("  Could not announce a campaign submission.")

    # 1. A send cut off between its write and its outcome.
    try:
        stale = [r for r in db.list_submissions(statuses=("sending",), limit=1_000_000)
                 if float(r["sending_started_at"] or 0) < seconds - SENDING_STALE_SECONDS]
        if stale:
            db.recover_sending_submissions(statemachine.REASONS["sending_stale"],
                                           started_before=seconds - SENDING_STALE_SECONDS)
            for row in stale:
                tell(db.get_submission(int(row["id"])), "sending")
    except Exception:
        print("  Could not settle campaign sends left unfinished.")

    # 2. Deadlines.
    try:
        for campaign in db.list_campaigns(archived=True):
            deadline = parse_utc(campaign["deadline"])
            if deadline is None or deadline > moment:
                continue
            for row in db.list_submissions(statuses=PRE_SEND, campaign_id=int(campaign["id"]), limit=1_000_000):
                try:
                    tell(statemachine.apply(db, int(row["id"]), "expire", now=moment), row["status"])
                except statemachine.NotAllowed:
                    continue
    except Exception:
        print("  Could not expire campaign submissions.")

    # 2b. An archived campaign's row that was out sending when it was archived
    # and came back not sent (a rate limit, a retry, a webhook gone): cancelled
    # as the archive cancelled the rest, so it never waits for good.
    try:
        leftovers = db.conn.execute(
            f"SELECT * FROM campaign_submissions WHERE status IN ({','.join('?' * len(EDITABLE))}) "
            "AND campaign_id IN (SELECT id FROM campaigns WHERE archived = 1) ORDER BY id",
            EDITABLE,
        ).fetchall()
        for row in leftovers:
            try:
                tell(statemachine.apply(db, int(row["id"]), "cancel", now=moment, status_reason=CAMPAIGN_ARCHIVED),
                     row["status"])
            except statemachine.NotAllowed:
                continue
    except Exception:
        print("  Could not cancel an archived campaign's submissions.")

    # 3. Following posts until they are live with a link.
    try:
        due = db.submissions_due(FOLLOWED, now=seconds, limit=100, no_url_manual=True)
    except Exception:
        print("  Could not list campaign submissions to check.")
        due = []
    for row in due:
        try:
            tell(follow(db, row, now=moment), row["status"], row["post_url"])
            continue
        except statemachine.NotAllowed:
            pass  # a route changed it meanwhile (then the push below changes nothing)
        except Exception:
            print(f"  Could not check submission #{row['id']}.")
        try:  # a row that can't be looked at is never at the head of every pass
            db.set_submission(int(row["id"]), expect=(row["status"],), match={"post_url": row["post_url"]},
                              next_attempt_at=seconds + CHECK_FAILED_SECONDS)
        except Exception:
            print(f"  Could not put off submission #{row['id']}.")

    # 4. Sending.
    if seconds < out["paused_until"]:
        return out
    try:
        ready = db.submissions_to_send(now=seconds, limit=100)
        user = views.discord_user(db) if ready else ""
    except Exception:
        print("  Could not list campaign submissions to send.")
        ready, user = [], ""
    for row in ready:
        if out["sent"] >= SENDS_PER_PASS or seconds < out["paused_until"]:
            out["more"] = True
            break
        try:
            _send(db, data_dir, http, row, moment=moment, user=user, out=out, tell=tell)
        except statemachine.NotAllowed:
            continue  # a route changed it before it was sent
        except Exception:
            # Before the `sending` write it simply stays ready; after it, the
            # sweep above settles it as "check Discord" within two minutes.
            print(f"  Could not send submission #{row['id']}.")
    return out


# ---- the user's actions ---------------------------------------------------------------------------

EDIT_FIELDS = ("post_url", "message_text", "arg_values", "extra_values", "destination_id", "method", "hold")


def _strings(value, what: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be a set of named values")
    out = {}
    for name, text in value.items():
        if not isinstance(name, str) or not name or len(name) > 32:
            raise ValueError(f"{what}: “{name}” isn't a name")
        text = "" if text is None else str(text)
        if len(text) > TEMPLATE_MAX:
            raise ValueError(f"{what}: “{name}” is too long: at most {TEMPLATE_MAX:,} characters")
        out[name] = text
    return out


def _get(db, sub_id: int):
    found = db.get_submission(int(sub_id))
    if found is None:
        raise LookupError(f"No submission #{sub_id}")
    return found


def edit(db, sub_id: int, *, now, **patch):
    """Change a submission not yet sent (or failed): its link, its words,
    its webhook, its method, whether it is saved for later. A changed method
    or destination is the row's own from then on (`overridden`), so editing
    the campaign leaves it alone. A new link, method or destination is
    evaluated again (changed words are checked again before a webhook sends
    them). Letting go of a ready row saved for later submits it, as Submit
    does. Raises LookupError, ValueError, NotAllowed."""
    unknown = set(patch) - set(EDIT_FIELDS)
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}")
    row = dict(_get(db, sub_id))
    if row["status"] not in EDITABLE:
        raise statemachine.NotAllowed(f"Not possible while it is {row['status'].replace('_', ' ')}")
    fields: dict = {}
    if patch.get("message_text") is not None:
        text = str(patch["message_text"])
        if len(text) > TEMPLATE_MAX:
            raise ValueError(f"The message is too long: at most {TEMPLATE_MAX:,} characters")
        fields["message_text"] = text
    for name, what in (("arg_values", "The command's values"), ("extra_values", "The extra values")):
        if patch.get(name) is not None:
            fields[name] = _strings(patch[name], what)
    # Saved for later, now let go: submitted, as its Submit button does
    # (a webhook row is due now, any other the user's to do by hand).
    release = row["status"] == "ready" and bool(int(row["hold"] or 0)) and \
        patch.get("hold") is not None and not patch["hold"]
    if patch.get("hold") is not None and not release:
        fields["hold"] = int(bool(patch["hold"]))
    overridden = list(json_field(row["overridden"], []))
    if patch.get("method") is not None and patch["method"] != row["method"]:
        if patch["method"] not in METHODS:
            raise ValueError("Pick how this one is submitted")
        fields["method"] = patch["method"]
        overridden += [] if "method" in overridden else ["method"]
    if patch.get("destination_id") is not None and int(patch["destination_id"]) != int(row["destination_id"]):
        dest_id = int(patch["destination_id"])
        if dest_id and db.get_destination(dest_id) is None:
            raise ValueError("That webhook destination doesn't exist")
        fields["destination_id"] = dest_id
        overridden += [] if "destination_id" in overridden else ["destination_id"]
    if overridden != json_field(row["overridden"], []):
        fields["overridden"] = json.dumps(overridden)

    after = None
    if patch.get("post_url") is not None:
        url, problem = urls.validate(row["platform"], patch["post_url"])
        if problem:
            raise ValueError(problem)
        if url != row["post_url"] or row["url_source"] != "manual":
            event = "set_url" if statemachine.allowed(row, "set_url") else "edit"
            after = evaluate(db, sub_id, typed_link(row["platform"], url), now=now, event=event, changes=fields)
    # A method or webhook picked, even the one it has (a webhook fixed in
    # Settings under the same destination), is a fresh look at how it goes.
    if after is None and row["post_url"] and (patch.get("method") is not None
                                              or patch.get("destination_id") is not None):
        after = evaluate(db, sub_id, current_resolution(db, row, now=now), now=now, event="edit", changes=fields)
    if after is None:
        after = statemachine.apply(db, sub_id, "edit", now=now, to=row["status"], **fields) if fields \
            else _get(db, sub_id)
    if release and after["status"] == "ready" and int(after["hold"] or 0):
        return submit(db, sub_id, now=now)
    return after


def reapply_campaign(db, campaign_id: int, before, *, now) -> int:
    """After a campaign edit: a new method or destination is copied onto its
    rows not yet sent (and failed ones) that don't hold their own value for
    it and whose Discord channel or page wasn't opened yet (a submission by
    hand in progress is never also sent by webhook), and those with a link
    are evaluated again. Returns how many rows changed."""
    after = db.get_campaign(int(campaign_id))
    if after is None or before is None:
        return 0
    changed = {f: after[f] for f in ("method", "destination_id") if before[f] != after[f]}
    if not changed:
        return 0
    count = 0
    for row in db.list_submissions(statuses=EDITABLE, campaign_id=int(campaign_id), limit=1_000_000):
        if row["opened_at"]:
            continue
        own = json_field(row["overridden"], [])
        fields = {f: v for f, v in changed.items() if f not in own and row[f] != v}
        if not fields:
            continue
        try:
            if row["post_url"]:
                evaluate(db, int(row["id"]), current_resolution(db, row, now=now), now=now,
                         event="campaign_changed", changes=fields)
            else:
                statemachine.apply(db, int(row["id"]), "campaign_changed", now=now, to=row["status"], **fields)
        except statemachine.NotAllowed:
            continue  # sent, opened or changed meanwhile
        count += 1
    return count


#: The reason on a row cancelled because its campaign was archived.
CAMPAIGN_ARCHIVED = "The campaign was archived"


def remove_campaign(db, campaign_id: int, *, now) -> str:
    """DELETE /campaigns/{id}: a campaign nothing was submitted to is deleted
    ("deleted"). Otherwise it is archived ("archived"), and its rows not yet
    sent (and failed ones) are cancelled, so nothing is followed, listed as
    work to do or posted by webhook for a campaign the user removed. Rows
    already sent stay, for the user to record what the campaign said. ''
    when there is no such campaign. The archive and its cancels are one
    transaction: all of them, or none."""
    with db.transaction():
        result = db.delete_campaign(int(campaign_id))
        if result != "archived":
            return result
        for row in db.list_submissions(statuses=EDITABLE, campaign_id=int(campaign_id), limit=1_000_000):
            try:
                statemachine.apply(db, int(row["id"]), "cancel", now=now, status_reason=CAMPAIGN_ARCHIVED)
            except statemachine.NotAllowed:
                continue  # out sending: advance cancels it if it comes back not sent
    return result


def move(db, sub_id: int, campaign_id: int, *, now) -> tuple:
    """Move a submission not yet sent (or failed) to another campaign, in one
    transaction: this row is cancelled ("Moved to ..."), and the same post,
    with its clip, provider, link, message and values, is attached to the
    other campaign (a cancelled, expired or failed row it has there starts
    again). Returns (cancelled, moved). Raises LookupError (no such row or
    campaign), ValueError, or NotAllowed when the other campaign refuses it
    (archived, switched off, platform, deadline, the link already submitted
    there) or it can't move as it is."""
    moment = posts.as_utc(now)
    with db.transaction():
        row = dict(_get(db, sub_id))
        if row["status"] not in EDITABLE:
            raise statemachine.NotAllowed(f"Not possible while it is {row['status'].replace('_', ' ')}")
        if int(campaign_id) == int(row["campaign_id"]):
            raise ValueError("It is already in that campaign")
        target = db.get_campaign(int(campaign_id))
        if target is None:
            raise LookupError(f"No campaign #{int(campaign_id)}")
        ident = {"clip_id": int(row["clip_id"] or 0), "video_id": row["video_id"], "start_s": float(row["start_s"]),
                 "end_s": float(row["end_s"])}
        when = _post_time(db, ident, row["platform"], row["provider"], int(row["publish_job_id"] or 0))
        problem = campaign_refusal(target, platform=row["platform"], scheduled_for=when, now=moment)
        if problem:
            raise statemachine.NotAllowed(problem)
        key = (int(campaign_id), row["video_id"], row["start_s"], row["end_s"], row["platform"])
        there = db.find_submission(*key)
        if there is not None and there["status"] not in REOPENABLE:
            raise statemachine.NotAllowed(f"It is already in {target['name']} as #{there['id']}")
        url = str(row["post_url"] or "")
        if url:
            taken = db.active_submission_with_url(int(campaign_id), url,
                                                  exclude_id=int(there["id"]) if there is not None else 0)
            if taken is not None:
                raise statemachine.NotAllowed(f"That link is already submitted to {target['name']} as #{taken['id']}")

        cancelled = statemachine.apply(db, sub_id, "move", now=moment, status_reason=f"Moved to {target['name']}")
        carry = {
            "clip_id": int(row["clip_id"] or 0), "clip_title": row["clip_title"], "provider": row["provider"],
            "publish_job_id": int(row["publish_job_id"] or 0), "origin": row["origin"],
            "origin_ref": row["origin_ref"], "message_text": row["message_text"],
            "arg_values": row["arg_values"], "extra_values": row["extra_values"], "hold": int(row["hold"] or 0),
            "review_first": max(int(row["review_first"] or 0), int(target["review_first"] or 0)),
            "method": target["method"], "destination_id": int(target["destination_id"] or 0), "overridden": "[]",
        }
        if there is None:
            moved_id = db.insert_submission(*key, **carry)
        else:
            moved_id = int(there["id"])
            reopen(db, moved_id, now=moment, **carry)
        typed = url if url and row["url_source"] == "manual" else ""
        moved = _first_look(db, moved_id, typed, now=moment)
    return cancelled, moved


def approve(db, sub_id: int, *, now):
    """Review done: a webhook row is ready to send now (or stays saved for
    later), any other is the user's to submit by hand."""
    row = _get(db, sub_id)
    if row["method"] == "discord_webhook":
        reason = "Saved for later" if int(row["hold"] or 0) else ""
    else:
        reason = BY_HAND.get(row["method"], "Submit it by hand")
    return statemachine.apply(db, sub_id, "approve", now=now, status_reason=reason)


def submit(db, sub_id: int, *, now):
    """Submit one saved for later: a webhook row is sent on the next pass,
    any other is the user's to submit by hand."""
    row = _get(db, sub_id)
    reason = "" if row["method"] == "discord_webhook" else BY_HAND.get(row["method"], "Submit it by hand")
    return statemachine.apply(db, sub_id, "submit", now=now, status_reason=reason)


def retry(db, sub_id: int, *, now, send_again: bool = False):
    """Try a failed send again; or, only with `send_again` (the UI asks
    first, since it may post twice), send again one Clips Kitty can't vouch
    for (awaiting confirmation with nobody recorded as the sender)."""
    row = _get(db, sub_id)
    if row["status"] == "awaiting_confirmation":
        if not send_again:
            raise statemachine.NotAllowed("It may already be posted: sending it again needs send_again")
        return statemachine.apply(db, sub_id, "send_again", now=now, status_reason="Sending it again")
    return statemachine.apply(db, sub_id, "retry", now=now, status_reason="")


def cancel(db, sub_id: int, *, now):
    _get(db, sub_id)
    return statemachine.apply(db, sub_id, "cancel", now=now, status_reason="You cancelled it")


def opened(db, sub_id: int, *, now):
    """The user opened its Discord channel or the campaign's page: from now
    on a campaign edit leaves its method alone. The first time is kept."""
    row = _get(db, sub_id)
    if not row["opened_at"]:
        db.set_submission(int(sub_id), opened_at=utc_text(posts.as_utc(now)))
    return _get(db, sub_id)


#: The user's word on what happened, as the state machine's events.
OUTCOMES = {"submitted": "user_submitted", "confirmed": "confirmed", "rejected": "rejected"}


def record(db, sub_id: int, outcome: str, *, now, note: str = "", message_link: str = ""):
    """The user records what happened: they submitted it (I submitted it),
    or the campaign accepted or rejected it. Only the user ever records an
    outcome; a bot's reply is never read as one. `message_link` is the
    Discord message they posted, kept so the row links to it."""
    from campaigns import links

    if outcome not in OUTCOMES:
        raise ValueError("Say what happened: submitted, confirmed or rejected")
    _get(db, sub_id)
    fields: dict = {}
    if note:
        fields["confirmation_note"] = str(note)[:500]
    if message_link:
        link = links.parse_link(message_link)
        if link.kind != "message":
            raise ValueError("Paste the link of your message in Discord: right-click it → Copy Message Link")
        fields.update(discord_message_id=link.message_id, discord_channel_id=link.channel_id)
    return statemachine.apply(db, sub_id, OUTCOMES[outcome], now=now, **fields)
