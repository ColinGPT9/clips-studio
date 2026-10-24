"""The submission queue's steps that need no Discord and no background thread
(DESIGN §5.3): evaluating a submission once its post's link is known, and
reopening one without losing a link the user typed.

The attach routes and the background step (DESIGN §6, a later phase) call
evaluate for a link resolution found and for one the user typed.
"""

import json
import sqlite3

from campaigns import eligibility, formatter, statemachine, urls
from campaigns.model import FAIL, UNKNOWN, cell, json_field, utc_text
from server import posts

#: The reason shown on a row that is now the user's to submit by hand.
BY_HAND = {
    "discord_assisted": "Submit it in Discord",
    "website": "Submit it on the campaign's page",
}


def _duplicate(db, row: dict, url: str):
    return db.active_submission_with_url(int(row["campaign_id"]), url, exclude_id=int(row["id"]))


def evaluate(db, sub_id: int, res, *, now, event: str = "url_found", source: dict | None = None,
             discord_user: str = ""):
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
    campaign routes and none is given. Returns the row after it.

    Only a live resolution is taken (a typed link is built as one). A blocked
    one is the caller's to hand to the user with its reason: its link, if it
    has one, is for the user to check first.
    """
    if res.state != "live" or not res.url:
        raise ValueError("evaluate needs a live post's link")
    found = db.get_submission(sub_id)
    if found is None:
        raise statemachine.NotAllowed(f"Submission #{sub_id} doesn't exist")
    row = dict(found)
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
        return statemachine.apply(db, sub_id, event, now=now, to="cancelled", **saved,
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
    if row["status"] == "failed":
        # Out of failed with a new link or setting: its sends start counting
        # again, as a retry's do, and the last send's error is no longer news.
        extra = {"attempts": 0, "error": "", **extra}
    try:
        return statemachine.apply(db, sub_id, event, now=now, to=target, **saved, **extra,
                                  checks=json.dumps([c.as_dict() for c in checks]), status_reason=reason)
    except sqlite3.IntegrityError:
        # The link index caught a row that took this link meanwhile.
        duplicate = _duplicate(db, row, res.url)
        number = f"#{duplicate['id']}" if duplicate is not None else "another submission"
        return statemachine.apply(db, sub_id, event, now=now, to="cancelled", **saved,
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
