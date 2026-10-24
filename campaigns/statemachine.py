"""Which status changes a submission may make, and making one safely.

TRANSITIONS is DESIGN §4.2 as data: for each event, the statuses it may start
from, the statuses it may lead to, and who causes it (W the background step,
U the user through a route, A an attach, R startup recovery). apply() checks
the event against the row as it is now and writes the change compare-and-set
(on the status, and on the columns the event's guard read), so a route and
the background step can never both win: the loser gets Conflict and nothing
is written.

The event's target is decided here when the row and its campaign say what it
is (approving a webhook submission makes it ready, an assisted one manual);
otherwise the caller names it with `to=` (an evaluation's verdict, a send that
ran out of retries).
"""

from collections.abc import Callable
from dataclasses import dataclass

from campaigns.model import PRE_SEND, STATUSES, TERMINAL, cell, utc_text
from server import posts

#: The outcomes of evaluating a submission that has a link (DESIGN §5.3).
EVALUATED = ("ready", "review", "manual", "cancelled", "expired")
#: A target meaning "stays in the status it is in".
SAME = "="

_WAITING = ("waiting_publication", "waiting_url")
_EDITABLE = (*PRE_SEND, "failed")
_CANCELLABLE = tuple(s for s in STATUSES if s != "sending" and s not in TERMINAL)


class NotAllowed(Exception):
    """The event can't happen to the submission as it is."""


class Conflict(NotAllowed):
    """The submission changed between reading it and writing the event."""


@dataclass(frozen=True)
class Rule:
    sources: tuple[str, ...]
    targets: tuple[str, ...]
    who: str
    #: Says why the row can't take the event ('' when it can), beyond its status.
    guard: Callable[[dict, float], str] | None = None
    #: The columns the guard reads. The write compares them as well as the
    #: status, so a change to one of them made meanwhile stops the event too.
    watch: tuple[str, ...] = ()


def _no_url(row: dict, now: float) -> str:
    if row["status"] == "manual" and row.get("post_url"):
        return "It already has its link"
    return ""


def _held(row: dict, now: float) -> str:
    return "" if int(row.get("hold") or 0) else "It isn't saved for later"


def _webhook(row: dict, now: float) -> str:
    return "" if row.get("method") == "discord_webhook" else "It isn't sent by a webhook"


def _sendable(row: dict, now: float) -> str:
    if row.get("method") != "discord_webhook":
        return "It isn't sent by a webhook"
    if int(row.get("hold") or 0):
        return "It's saved for later"
    if float(row.get("next_attempt_at") or 0) > now:
        return "It isn't due yet"
    return ""


def _not_by_user(row: dict, now: float) -> str:
    return "You submitted it yourself" if row.get("sent_by") == "user" else ""


def _not_opened(row: dict, now: float) -> str:
    return "Its Discord channel or page was already opened" if row.get("opened_at") else ""


TRANSITIONS: dict[str, Rule] = {
    # Following the post (the background step).
    "published_no_url": Rule(("waiting_publication",), ("waiting_url",), "W"),
    "post_failed": Rule(("waiting_publication",), ("manual", "waiting_publication"), "W"),
    "post_skipped": Rule(("waiting_publication",), ("manual",), "W"),
    "url_found": Rule((*_WAITING, "manual"), EVALUATED, "W", _no_url, ("post_url",)),
    "url_overdue": Rule(("waiting_url",), ("manual",), "W"),
    "stale": Rule(_WAITING, ("manual",), "W"),
    "expire": Rule(PRE_SEND, ("expired",), "W"),
    # Sending by webhook.
    "send_start": Rule(("ready",), ("sending",), "W", _sendable, ("method", "hold", "next_attempt_at")),
    "destination_unusable": Rule(("ready",), ("manual",), "W", _webhook, ("method",)),
    "send_ok": Rule(("sending",), ("sent", "awaiting_confirmation"), "W"),
    "rate_limited": Rule(("sending",), ("ready",), "W"),
    "send_retry": Rule(("sending",), ("ready", "failed"), "W"),
    "send_ambiguous": Rule(("sending",), ("awaiting_confirmation",), "W"),
    "send_gone": Rule(("sending",), ("manual",), "W"),
    "send_denied": Rule(("sending",), ("manual",), "W"),
    "send_forum": Rule(("sending",), ("manual",), "W"),
    "send_refused": Rule(("sending",), ("failed",), "W"),
    "sending_stale": Rule(("sending",), ("awaiting_confirmation",), "W"),
    "restart": Rule(("sending",), ("awaiting_confirmation",), "R"),
    # The user.
    "set_url": Rule((*_WAITING, "review", "manual", "failed"), EVALUATED, "U"),
    "approve": Rule(("review",), ("ready", "manual"), "U"),
    "submit": Rule(("ready",), ("ready", "manual"), "U", _held, ("hold",)),
    "user_submitted": Rule(("manual", "ready", "review", "failed", "awaiting_confirmation"),
                           ("sent", "awaiting_confirmation"), "U"),
    "confirmed": Rule(("sent", "awaiting_confirmation", "manual"), ("confirmed",), "U"),
    "rejected": Rule(("sent", "awaiting_confirmation"), ("rejected",), "U"),
    "retry": Rule(("failed",), ("ready",), "U"),
    "send_again": Rule(("awaiting_confirmation",), ("ready",), "U", _not_by_user, ("sent_by",)),
    "cancel": Rule(_CANCELLABLE, ("cancelled",), "U"),
    "move": Rule(_EDITABLE, ("cancelled",), "U"),
    "edit": Rule(_EDITABLE, (SAME, *EVALUATED), "U"),
    "campaign_changed": Rule(_EDITABLE, (SAME, *EVALUATED), "U", _not_opened, ("opened_at",)),
    # An explicit re-attach by the user (never automation).
    "reopen": Rule(("cancelled", "expired", "failed"), ("waiting_publication",), "A"),
}

#: The reason shown after an event, when the caller gives none.
REASONS = {
    "post_failed": "The post failed: publish it again or enter its link",
    "post_skipped": "Nothing was posted on this platform: publish it there or enter its link",
    "url_overdue": "No link for this post a day after it went live: enter the post's link",
    "stale": "Not seen live 14 days after its time: enter its link if it went out",
    "expire": "The campaign's deadline passed",
    "destination_unusable": "The webhook can't be used: submit it by hand or pick another",
    "send_ambiguous": "Check Discord: it may have been posted",
    "sending_stale": "Check Discord: it may have been posted",
    "restart": "Clips Kitty closed while sending. Check the channel.",
    "send_gone": "The webhook is gone: submit it by hand or pick another",
    "send_denied": "The webhook isn't allowed to post there any more",
    "send_forum": "This channel is a forum: set Forum on the destination",
}


def refusal(row, event: str, *, now=None) -> str:
    """Why `event` can't happen to the row as it is; '' when it can."""
    rule = TRANSITIONS.get(event)
    if rule is None:
        raise ValueError(f"unknown submission event: {event}")
    row = dict(row)
    status = row.get("status", "")
    if status not in rule.sources:
        return f"Not possible while it is {status.replace('_', ' ')}"
    if rule.guard is not None:
        return rule.guard(row, posts.as_utc(now).timestamp())
    return ""


def allowed(row, event: str, *, now=None) -> bool:
    return not refusal(row, event, now=now)


def _approval_required(db, row: dict) -> bool:
    campaign = db.get_campaign(int(row["campaign_id"]))
    return bool(int(cell(campaign, "approval_required", 1))) if campaign is not None else True


def _target(db, row: dict, event: str, rule: Rule, to: str) -> str:
    status = row["status"]
    if to:
        if to == status and SAME in rule.targets:
            return to
        if to not in rule.targets or to == SAME:
            raise NotAllowed(f"{event} can't make it {to}")
        return to
    if event in ("approve", "submit"):
        return "ready" if row.get("method") == "discord_webhook" else "manual"
    if event in ("send_ok", "user_submitted"):
        return "awaiting_confirmation" if _approval_required(db, row) else "sent"
    if rule.targets[0] == SAME:
        return status
    if event in ("url_found", "set_url"):
        raise ValueError(f"{event} needs to=: the evaluation decides where it goes")
    return rule.targets[0]


def _defaults(event: str, row: dict, target: str, now) -> dict:
    moment = posts.as_utc(now)
    stamp, seconds = utc_text(moment), moment.timestamp()
    out: dict = {}
    if target != row["status"] or event not in ("edit", "campaign_changed"):
        out["status_reason"] = REASONS.get(event, "")
    if event == "published_no_url" and not row.get("posted_at"):
        out["posted_at"] = stamp
    elif event == "submit":
        out["hold"] = 0
        if target == "ready":
            out["next_attempt_at"] = seconds
    elif event == "approve":
        if target == "ready":
            out["next_attempt_at"] = seconds
    elif event == "send_start":
        out["sending_started_at"] = seconds
    elif event == "send_ok":
        out.update(sent_by="webhook", sent_at=stamp, error="")
    elif event in ("send_ambiguous", "sending_stale", "restart"):
        out["sent_by"] = ""
    elif event == "user_submitted":
        out.update(sent_by="user", sent_at=stamp)
    elif event == "confirmed":
        out["confirmed_at"] = stamp
    elif event == "retry":
        out.update(attempts=0, next_attempt_at=seconds, error="")
    elif event == "send_again":
        out.update(next_attempt_at=seconds, sent_by="", error="")
    elif event == "reopen":
        # A fresh start on the same post: what the user set on the row stays
        # (message, values, method), what happened to it goes. Its link goes
        # too and is found again, so it is checked against the campaign's
        # other rows like any new link. Nothing can find a link the user
        # typed, so reopen through queue.reopen, which gives it back.
        out.update(
            attempts=0, error="", next_attempt_at=seconds, post_url="", url_source="",
            checks="[]", sending_started_at=0, sent_at="", sent_by="", discord_message_id="",
            discord_channel_id="", opened_at="", confirmed_at="", confirmation_note="",
        )
    return out


def apply(db, sub_id: int, event: str, *, now, to: str = "", **fields):
    """Make `event` happen to submission `sub_id` and return the row after it.

    Raises NotAllowed when its status (or the row) can't take the event, and
    Conflict when it changed while this ran. `fields` are written with the
    status, over the event's own defaults (a reason, a timestamp, a counter
    reset); the status itself comes only from the event and `to`.
    """
    if "status" in fields:
        raise ValueError("the status comes from the event and to=, not from fields")
    rule = TRANSITIONS.get(event)
    if rule is None:
        raise ValueError(f"unknown submission event: {event}")
    found = db.get_submission(sub_id)
    if found is None:
        raise NotAllowed(f"Submission #{sub_id} doesn't exist")
    row = dict(found)
    problem = refusal(row, event, now=now)
    if problem:
        raise NotAllowed(problem)
    target = _target(db, row, event, rule, to)
    changes = {**_defaults(event, row, target, now), **fields, "status": target}
    match = {column: row[column] for column in rule.watch}
    if not db.set_submission(sub_id, expect=(row["status"],), match=match, **changes):
        raise Conflict(f"Submission #{sub_id} changed meanwhile")
    return db.get_submission(sub_id)
