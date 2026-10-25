"""What the renderer is shown of campaigns and their submissions (DESIGN §7
"Submission JSON" and "Campaign JSON"), and the `campaign` event the
background step sends on the existing /ws.

Plain Python with no FastAPI, so CI's main job tests the shapes. Nothing
here holds a token: a destination is shown by its id, label and state only.
"""

from campaigns import formatter, links
from campaigns.model import (
    LABELS,
    cell,
    json_field,
    outcome_to_record,
    platform_label,
    status_label,
    to_submit,
)

#: The campaign columns kept as JSON text, decoded for the renderer.
CAMPAIGN_JSON = {
    "platforms": [], "required_accounts": {}, "required_hashtags": [], "required_mentions": [],
    "command_args": [], "extra_fields": [], "filters": {},
}
#: The campaign columns that are switches (0/1 in the table).
CAMPAIGN_FLAGS = ("enabled", "archived", "trust_account", "approval_required", "review_first")


def discord_user(db) -> str:
    """The connected Discord username for {discord_user}, or ''."""
    from campaigns import service

    user = service.load_settings(db).get("user")
    return str(user.get("username") or "") if isinstance(user, dict) else ""


def counts_of(rows) -> dict:
    """{"counts": {status: n}, "to_submit": n, "outcome_to_record": n} from
    StateDB.submission_counts rows."""
    counts: dict[str, int] = {}
    todo = outcomes = 0
    for row in rows:
        n = int(row["n"])
        counts[row["status"]] = counts.get(row["status"], 0) + n
        sample = {"status": row["status"], "hold": row["hold"], "sent_by": row["sent_by"]}
        todo += n if to_submit(sample) else 0
        outcomes += n if outcome_to_record(sample) else 0
    return {"counts": counts, "to_submit": todo, "outcome_to_record": outcomes}


def campaign_view(db, row, *, counts: dict | None = None) -> dict:
    """A campaign as the renderer gets it: every column, its JSON columns
    decoded and its switches as true/false, plus its webhook destination (id,
    label, state) and how many of its submissions are in each status."""
    out = dict(row)
    for name, default in CAMPAIGN_JSON.items():
        out[name] = json_field(out.get(name), default)
    for name in CAMPAIGN_FLAGS:
        out[name] = bool(int(out.get(name) or 0))
    dest = None
    dest_id = int(out.get("destination_id") or 0)
    if dest_id:
        found = db.get_destination(dest_id)
        dest = {"id": dest_id, "label": found["label"], "state": found["state"]} if found is not None \
            else {"id": dest_id, "label": "", "state": "missing"}
    out["destination"] = dest
    if counts is None:
        counts = counts_of(db.submission_counts(campaign_id=int(out["id"])))["counts"]
    out["counts"] = counts
    return out


def submission_view(db, row, *, campaign=None, user: str | None = None) -> dict:
    """A submission as DESIGN §7 "Submission JSON" lists it: its fields, the
    owner's wording for its status, the words to submit (the message or the
    command, with per-row overrides applied) and the links to open."""
    sub = dict(row)
    if campaign is None:
        campaign = db.get_campaign(int(sub["campaign_id"]))
    campaign = dict(campaign) if campaign is not None else {}
    if user is None:
        user = discord_user(db)
    arg_values = json_field(sub.get("arg_values"), {})
    extra_values = json_field(sub.get("extra_values"), {})
    if campaign:
        values = formatter.values_for(db, sub, campaign, discord_user=user)
        command = formatter.command(campaign, values, arg_values=arg_values)
        assisted = formatter.assisted_text(campaign, values, message_text=str(sub.get("message_text") or ""),
                                           arg_values=arg_values)
    else:
        command = {"name": "", "args": [], "url_arg": "", "line": ""}
        assisted = ""
    message_url = ""
    if sub.get("discord_message_id"):
        guild = str(campaign.get("guild_id") or "")
        dest_id = int(sub.get("destination_id") or 0)
        if dest_id:
            dest = db.get_destination(dest_id)
            guild = str(cell(dest, "guild_id", "")) if dest is not None and cell(dest, "guild_id", "") else guild
        message_url = links.channel_url(guild, str(sub.get("discord_channel_id") or ""),
                                        str(sub["discord_message_id"]))
    return {
        "id": int(sub["id"]),
        "campaign_id": int(sub["campaign_id"]),
        "campaign_name": str(campaign.get("name") or ""),
        "clip_id": int(sub.get("clip_id") or 0),
        "clip_title": str(sub.get("clip_title") or ""),
        "video_id": sub["video_id"],
        "start_s": float(sub["start_s"]),
        "end_s": float(sub["end_s"]),
        "platform": sub["platform"],
        "provider": str(sub.get("provider") or ""),
        "method": str(sub.get("method") or ""),
        "overridden": json_field(sub.get("overridden"), []),
        "status": sub["status"],
        "status_label": status_label(sub),
        "status_reason": str(sub.get("status_reason") or ""),
        "to_submit": to_submit(sub),
        "outcome_to_record": outcome_to_record(sub),
        "uncertain": sub["status"] == "awaiting_confirmation" and not sub.get("sent_by"),
        "hold": bool(int(sub.get("hold") or 0)),
        "review_first": bool(int(sub.get("review_first") or 0)),
        "due_at": str(sub.get("due_at") or ""),
        "post_url": str(sub.get("post_url") or ""),
        "url_source": str(sub.get("url_source") or ""),
        "account": str(sub.get("account") or ""),
        "checks": json_field(sub.get("checks"), []),
        "message_text": str(sub.get("message_text") or ""),
        "arg_values": arg_values,
        "extra_values": extra_values,
        "assisted_text": assisted,
        "command": {"name": command["name"], "line": command["line"], "args": command["args"],
                    "url_arg": command["url_arg"]},
        "discord_url": links.channel_url(str(campaign.get("guild_id") or ""),
                                         str(campaign.get("channel_id") or "")),
        "message_url": message_url,
        "website_url": str(campaign.get("website_url") or ""),
        "deadline": str(campaign.get("deadline") or ""),
        "attempts": int(sub.get("attempts") or 0),
        "next_attempt_at": float(sub.get("next_attempt_at") or 0),
        "opened_at": str(sub.get("opened_at") or ""),
        "sent_at": str(sub.get("sent_at") or ""),
        "sent_by": str(sub.get("sent_by") or ""),
        "confirmed_at": str(sub.get("confirmed_at") or ""),
        "confirmation_note": str(sub.get("confirmation_note") or ""),
        "error": str(sub.get("error") or ""),
        "created_at": sub["created_at"],
        "updated_at": sub["updated_at"],
    }


# ---- the `campaign` event ----------------------------------------------------------------

def event_message(row, campaign_name: str) -> str:
    """One line for a status a submission just reached."""
    sub = dict(row)
    name = campaign_name or "the campaign"
    status = sub["status"]
    method = str(sub.get("method") or "")
    if status == "manual":
        if sub.get("post_url") and method == "discord_assisted":
            return f"Ready to submit to {name} in Discord"
        if sub.get("post_url") and method == "website":
            return f"Ready to submit to {name} on its page"
        return f"{name}: {sub.get('status_reason') or 'it needs you'}"
    if status == "review":
        return f"Check a {platform_label(sub['platform'])} clip for {name} before it is submitted"
    if status == "ready":
        return f"Saved for later for {name}" if int(sub.get("hold") or 0) else f"Ready to post to {name}"
    if status == "sent":
        return f"Posted to {name} in Discord"
    if status == "awaiting_confirmation":
        if not sub.get("sent_by"):
            return f"Check Discord: a clip for {name} may already be posted"
        return f"Posted to {name}: record what the campaign says"
    if status == "failed":
        return f"Couldn't post a clip to {name}: {sub.get('error') or 'retry or submit it by hand'}"
    if status == "expired":
        return f"{name}'s deadline passed"
    return f"{name}: {LABELS.get(status, status)}"


def event(row, previous: str, campaign_name: str) -> dict:
    """The `campaign` WebSocket event for a submission that changed status."""
    sub = dict(row)
    return {
        "type": "campaign",
        "submission_id": int(sub["id"]),
        "campaign_id": int(sub["campaign_id"]),
        "clip_id": int(sub.get("clip_id") or 0),
        "status": sub["status"],
        "previous": previous,
        "to_submit": to_submit(sub),
        "message": event_message(sub, campaign_name),
    }


#: Statuses a list's group asks for (filtered again by the group's own rule).
GROUP_STATUSES = {
    "to_submit": ("ready", "review", "manual", "failed", "awaiting_confirmation"),
    "outcome_to_record": ("awaiting_confirmation",),
    "waiting": ("waiting_publication", "waiting_url", "ready", "sending"),
    "sent": ("sent",),
    "done": ("confirmed", "rejected", "expired", "cancelled"),
}
