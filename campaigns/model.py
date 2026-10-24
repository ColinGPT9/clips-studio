"""The campaign vocabulary: methods, the submission statuses in the owner's
words and the groups they are shown in, the platforms a campaign can take, a
check's shape, the waiting times, and every rule a saved campaign must keep.

clean_campaign holds the rules rather than a pydantic model in the route, so
CI's main job, which has no FastAPI, tests them.
"""

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from server import posts

METHODS = ("discord_assisted", "discord_webhook", "website")

# ---- where the secrets live ----------------------------------------------------------
#
# In core/secrets (DPAPI on Windows), never in the database, a log line or an
# API answer: the sign-in's tokens under one name, and each webhook
# destination's id and token under a name of its own (DESIGN §3.4).

OAUTH_SECRET = "discord_oauth"


def webhook_secret_name(destination_id: int) -> str:
    """The secret holding one webhook destination's {"webhook_id", "token"}."""
    return f"discord_webhook_{int(destination_id)}"

# ---- statuses ------------------------------------------------------------------

STATUSES = (
    "waiting_publication", "waiting_url", "ready", "review", "manual", "sending", "sent",
    "awaiting_confirmation", "confirmed", "rejected", "failed", "expired", "cancelled",
)

#: The owner's wording for each status (request §11).
LABELS = {
    "waiting_publication": "Waiting for publication",
    "waiting_url": "Waiting for public URL",
    "ready": "Ready to submit",
    "review": "Awaiting review",
    "manual": "Manual action required",
    "sending": "Sending",
    "sent": "Message sent",
    "awaiting_confirmation": "Awaiting confirmation",
    "confirmed": "Confirmed",
    "rejected": "Rejected",
    "failed": "Failed",
    "expired": "Expired",
    "cancelled": "Cancelled",
}

#: Nothing has been sent yet.
PRE_SEND = ("waiting_publication", "waiting_url", "ready", "review", "manual")
#: Nothing moves a row out of these, except an explicit re-attach.
TERMINAL = ("confirmed", "rejected", "expired", "cancelled")

#: How the lists group rows (DESIGN §4.1 "Group shown").
GROUPS = ("to_submit", "outcome_to_record", "waiting", "sent", "done")

#: "Message sent" for a row the user submitted by hand: Clips Kitty sent nothing.
USER_SENT_LABEL = "You submitted it"


def cell(row, key, default=None):
    """A field of a dict or a sqlite3.Row, or `default` when it has none."""
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def to_submit(row) -> bool:
    """Work the user still has to do: review it, submit it by hand, retry or
    cancel a failed one, submit one saved for later, or check Discord after a
    send nobody can vouch for. Drives the badge and the notifications, so a
    webhook message already posted and waiting for the campaign's verdict is
    never counted here."""
    status = cell(row, "status", "")
    if status in ("review", "manual", "failed"):
        return True
    if status == "ready":
        return bool(int(cell(row, "hold", 0) or 0))
    if status == "awaiting_confirmation":
        return not cell(row, "sent_by", "")
    return False


def outcome_to_record(row) -> bool:
    """Sent (by the webhook or by the user), so only the campaign's verdict is
    left to record."""
    return cell(row, "status", "") == "awaiting_confirmation" and bool(cell(row, "sent_by", ""))


def group(row) -> str:
    """Which of GROUPS a row is listed under."""
    if to_submit(row):
        return "to_submit"
    if outcome_to_record(row):
        return "outcome_to_record"
    status = cell(row, "status", "")
    if status == "sent":
        return "sent"
    if status in TERMINAL:
        return "done"
    return "waiting"


def status_label(row) -> str:
    status = cell(row, "status", "")
    if status == "sent" and cell(row, "sent_by", "") == "user":
        return USER_SENT_LABEL
    return LABELS.get(status, status)


# ---- platforms and modes ------------------------------------------------------------

#: Not places a clipping campaign takes a post link from: Upload-Post's
#: Discord and Telegram upload the video file itself, and a Google Business
#: update is a listing post with no public link form Clips Kitty can check or
#: open (DESIGN §5.2 has no rule for it).
NOT_CAMPAIGN_PLATFORMS = ("discord", "telegram", "google_business")


def _platforms() -> tuple[str, ...]:
    from publish.uploadpost import VIDEO_PLATFORMS as upload_post
    from publish.woopsocial import PLATFORMS as woopsocial

    out: list[str] = []
    for platform in (*woopsocial, *upload_post):
        if platform not in out and platform not in NOT_CAMPAIGN_PLATFORMS:
            out.append(platform)
    return tuple(out)


#: Every platform the publishers can post a video to that a campaign can take.
PLATFORMS = _platforms()

PLATFORM_LABELS = {
    "youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "facebook": "Facebook",
    "x": "X", "threads": "Threads", "linkedin": "LinkedIn", "pinterest": "Pinterest",
    "bluesky": "Bluesky", "reddit": "Reddit",
}


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, str(platform or "").replace("_", " ").title())


#: What made a clip, for routing (eligibility.clip_source). "unknown" is a
#: source's answer when nothing says, and never a filter value.
MODES = ("regular", "sports", "longform", "vertical_live", "podcast", "gaming", "pipeline")


# ---- checks -------------------------------------------------------------------------

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"


@dataclass(frozen=True)
class Check:
    name: str
    result: str  # pass | fail | unknown
    message: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "result": self.result, "message": self.message}


# ---- times (seconds unless named otherwise) -------------------------------------------

#: Waits before each new try of a webhook send that failed before reaching
#: Discord; after the last one the row is failed.
RETRY_DELAYS = (60, 300, 1800, 7200, 21600)
#: A row still `sending` this long after it started is swept to
#: awaiting_confirmation at the start of a pass.
SENDING_STALE_SECONDS = 120
#: A post published without a link is waited on this long for one.
URL_WAIT_HOURS = 24
#: A row with no progress this long after its post's due time goes to manual.
STALE_DAYS = posts.FOLLOW_DAYS
#: A failed watch post may still be re-sent by the watch until this long after.
FAILED_POST_WAIT_HOURS = 48
#: How often a waiting row is looked at again: its post is due (or its time
#: unknown), its post is scheduled ahead, it waits on a failed post or is a
#: manual row with no link.
CHECK_DUE_SECONDS = 60
CHECK_SCHEDULED_SECONDS = 10 * 60
CHECK_FAILED_SECONDS = 60 * 60
#: At most one send per destination this often, and this many sends a pass.
SEND_SPACING_SECONDS = 2
SENDS_PER_PASS = 5


# ---- JSON columns -------------------------------------------------------------------------

def json_field(value, default):
    """A JSON column's value, decoded; `default` when it is empty, unreadable
    or not the same kind of thing (a list where a dict belongs). A value that
    is already decoded is taken as it is."""
    if isinstance(value, (list, dict)):
        parsed = value
    else:
        try:
            parsed = json.loads(value) if value else default
        except (TypeError, ValueError):
            return default
    if default is not None and not isinstance(parsed, type(default)):
        return default
    return parsed


def utc_text(moment: datetime) -> str:
    """An aware datetime as the UTC text the campaign tables keep."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text) -> datetime | None:
    """RFC 3339 text as an aware UTC datetime; a value without a zone is UTC.
    None when it can't be read."""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# ---- the campaign rules -------------------------------------------------------------------

#: What a campaign form may set: every column but the id, the timestamps and
#: `archived`, which only delete_campaign sets.
EDITABLE = (
    "name", "enabled", "method", "guild_id", "guild_name", "channel_id", "channel_name",
    "channel_link", "destination_id", "website_url", "requirements", "platforms",
    "required_accounts", "trust_account", "required_hashtags", "required_mentions",
    "required_text", "min_seconds", "max_seconds", "deadline", "approval_required",
    "instructions", "command_name", "command_args", "url_argument", "extra_fields",
    "message_template", "thread_name_template", "review_first", "filters", "notes",
)

TEMPLATE_MAX = 2000
NAME_MAX = 100
MAX_ARGS = 25
COMMAND_RE = re.compile(r"^/?([\w-]{1,32})$")
ARG_NAME_RE = re.compile(r"^[\w-]{1,32}$")
EXTRA_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
FILTER_KEYS = ("plugins", "modes", "creator_ids")

# A new campaign's values, for the rules that look at two fields at once.
_NEW = {
    "method": "discord_assisted", "destination_id": 0, "website_url": "", "command_name": "",
    "command_args": [], "url_argument": "", "min_seconds": 0.0, "max_seconds": 0.0, "guild_id": "",
    "channel_id": "",
}


def carries_link(args, url_argument: str) -> bool:
    """Whether a command's arguments put the post's link in it: the link's
    argument left blank (it is then filled with the link), or any argument
    whose value has {url} in it."""
    for arg in args:
        if not isinstance(arg, dict):
            continue
        value = str(arg.get("value") or "")
        if "{url}" in value or (url_argument and arg.get("name") == url_argument and not value):
            return True
    return False


def _text(value, what: str, *, limit: int = 0) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{what} must be text")
    value = value.strip()
    if limit and len(value) > limit:
        raise ValueError(f"{what} is too long: at most {limit:,} characters")
    return value


def _flag(value, what: str) -> int:
    if isinstance(value, bool) or value in (0, 1):
        return int(value)
    raise ValueError(f"{what} must be true or false")


def _number(value, what: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a number")
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number") from None
    if not math.isfinite(number):
        raise ValueError(f"{what} must be a number")
    if number < 0:
        raise ValueError(f"{what} can't be negative")
    return number


def _whole(value, what: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a whole number")
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a whole number") from None
    if number < 0 or float(value or 0) != number:
        raise ValueError(f"{what} must be a whole number")
    return number


def _decoded(value, kind, what: str):
    """A JSON field given as JSON text or already decoded."""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else kind()
        except ValueError:
            raise ValueError(f"{what} isn't valid JSON") from None
    if value is None:
        value = kind()
    if not isinstance(value, kind):
        raise ValueError(f"{what} must be a {'list' if kind is list else 'set of named values'}")
    return value


def _https(value, what: str) -> str:
    url = _text(value, what, limit=TEMPLATE_MAX)
    if not url:
        return ""
    if re.search(r"\s", url) or not re.match(r"^https://[^/\s?#@]+\.[^/\s?#@]+(?:[/?#].*)?$", url, re.I):
        raise ValueError(f"{what} must be a full https:// link")
    return url


def _snowflake(value, what: str) -> str:
    from campaigns import links

    text = _text(value, what)
    if text and not links.SNOWFLAKE_RE.match(text):
        raise ValueError(f"{what} must be a Discord ID: 17 to 20 digits")
    return text


def _platform_list(value) -> list[str]:
    out: list[str] = []
    for item in _decoded(value, list, "Platforms"):
        platform = str(item or "").strip().lower()
        if platform not in PLATFORMS:
            raise ValueError(f"Clips Kitty can't submit {platform or 'that'} posts to a campaign")
        if platform not in out:
            out.append(platform)
    return out


def _accounts(value) -> dict[str, str]:
    out: dict[str, str] = {}
    for platform, handle in _decoded(value, dict, "Required accounts").items():
        if platform not in PLATFORMS:
            raise ValueError(f"Required accounts: {platform} isn't a platform a campaign takes")
        name = _text(handle, f"The {platform_label(platform)} account", limit=NAME_MAX).lstrip("@").strip()
        if name:
            out[platform] = name
    return out


def _tags(value, what: str, mark: str) -> list[str]:
    out: list[str] = []
    for item in _decoded(value, list, what):
        tag = _text(item, what, limit=NAME_MAX).lstrip(mark).strip()
        if not tag:
            continue
        if re.search(r"\s", tag):
            raise ValueError(f"{what}: “{tag}” has a space in it; add one per line")
        if tag.lower() not in (t.lower() for t in out):
            out.append(tag)
    return out


def _deadline(value) -> str:
    text = _text(value, "The deadline")
    if not text:
        return ""
    moment = parse_utc(text)
    if moment is None:
        raise ValueError("The deadline isn't a date and time Clips Kitty can read")
    return utc_text(moment)


def _command(value) -> str:
    text = _text(value, "The command")
    if not text:
        return ""
    found = COMMAND_RE.match(text)
    if found is None:
        raise ValueError(
            "The command is its name only, like /submit: letters, digits, - and _, at most 32"
        )
    return found.group(1)


def _args(value) -> list[dict]:
    out, seen = [], set()
    items = _decoded(value, list, "The command's arguments")
    if len(items) > MAX_ARGS:
        raise ValueError(f"A command takes at most {MAX_ARGS} arguments")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each argument needs a name and a value")
        name = _text(item.get("name"), "An argument's name")
        if not ARG_NAME_RE.match(name):
            raise ValueError(
                f"Argument “{name}”: a name is letters, digits, - and _, at most 32"
            )
        if name in seen:
            raise ValueError(f"Argument “{name}” is there twice")
        seen.add(name)
        out.append({"name": name, "value": _text(item.get("value"), f"Argument “{name}”",
                                                 limit=TEMPLATE_MAX)})
    return out


def _extras(value) -> list[dict]:
    from campaigns.formatter import BUILTINS

    out, seen = [], set()
    items = _decoded(value, list, "The extra fields")
    if len(items) > MAX_ARGS:
        raise ValueError(f"At most {MAX_ARGS} extra fields")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each extra field needs a name")
        name = _text(item.get("name"), "An extra field's name")
        if not EXTRA_NAME_RE.match(name):
            raise ValueError(
                f"Extra field “{name}”: a name is lower-case letters, digits and _, at most 32"
            )
        if name in BUILTINS:
            raise ValueError(f"Extra field “{name}” has the name of a value Clips Kitty fills in itself")
        if name in seen:
            raise ValueError(f"Extra field “{name}” is there twice")
        seen.add(name)
        out.append({
            "name": name,
            "label": _text(item.get("label"), f"The label of “{name}”", limit=NAME_MAX) or name,
            "default": _text(item.get("default"), f"The default of “{name}”", limit=TEMPLATE_MAX),
            "required": bool(_flag(item.get("required", False), f"Whether “{name}” is required")),
        })
    return out


def _filters(value) -> dict:
    from plugins import store

    given = _decoded(value, dict, "The routing filters")
    unknown = set(given) - set(FILTER_KEYS)
    if unknown:
        raise ValueError(f"Unknown routing filters: {', '.join(sorted(unknown))}")
    plugins = [str(p) for p in _decoded(given.get("plugins"), list, "Plugins")]
    for plugin in plugins:
        if not store.ID_RE.match(plugin):
            raise ValueError(f"“{plugin}” isn't a plugin id (publisher/name)")
    modes = [str(m) for m in _decoded(given.get("modes"), list, "Modes")]
    for mode in modes:
        if mode not in MODES:
            raise ValueError(f"“{mode}” isn't a kind of clips Clips Kitty makes")
    creators = [_whole(c, "A creator") for c in _decoded(given.get("creator_ids"), list, "Creators")]
    return {"plugins": list(dict.fromkeys(plugins)), "modes": list(dict.fromkeys(modes)),
            "creator_ids": list(dict.fromkeys(creators))}


def _name(value) -> str:
    name = _text(value, "The name", limit=NAME_MAX)
    if not name:
        raise ValueError("Give the campaign a name")
    return name


def _message(value) -> str:
    text = _text(value, "The message", limit=TEMPLATE_MAX)
    if not text:
        raise ValueError("The message can't be empty: {url} on its own sends just the link")
    return text


def _method(value) -> str:
    if value not in METHODS:
        raise ValueError("Pick how this campaign takes submissions")
    return value


_CLEAN = {
    "name": _name,
    "enabled": lambda v: _flag(v, "Enabled"),
    "method": _method,
    "guild_id": lambda v: _snowflake(v, "The server ID"),
    "guild_name": lambda v: _text(v, "The server's name", limit=NAME_MAX),
    "channel_id": lambda v: _snowflake(v, "The channel ID"),
    "channel_name": lambda v: _text(v, "The channel's name", limit=NAME_MAX),
    "channel_link": lambda v: _text(v, "The channel link", limit=TEMPLATE_MAX),
    "destination_id": lambda v: _whole(v, "The webhook destination"),
    "website_url": lambda v: _https(v, "The campaign's page"),
    "requirements": lambda v: _text(v, "The requirements"),
    "platforms": _platform_list,
    "required_accounts": _accounts,
    "trust_account": lambda v: _flag(v, "Counting an account it can't see as right"),
    "required_hashtags": lambda v: _tags(v, "Hashtags", "#"),
    "required_mentions": lambda v: _tags(v, "Mentions", "@"),
    "required_text": lambda v: _text(v, "The required phrase", limit=TEMPLATE_MAX),
    "min_seconds": lambda v: _number(v, "The shortest clip"),
    "max_seconds": lambda v: _number(v, "The longest clip"),
    "deadline": _deadline,
    "approval_required": lambda v: _flag(v, "Approval required"),
    "instructions": lambda v: _text(v, "The instructions"),
    "command_name": _command,
    "command_args": _args,
    "url_argument": lambda v: _text(v, "The link's argument"),
    "extra_fields": _extras,
    "message_template": _message,
    "thread_name_template": lambda v: _text(v, "The thread title", limit=TEMPLATE_MAX),
    "review_first": lambda v: _flag(v, "Review first"),
    "filters": _filters,
    "notes": lambda v: _text(v, "The notes"),
}

_JSON_FIELDS = ("platforms", "required_accounts", "required_hashtags", "required_mentions",
                "command_args", "extra_fields", "filters")


def clean_campaign(fields: dict, *, partial: bool, current=None) -> dict:
    """A campaign's fields checked and normalised, ready for insert_campaign or
    set_campaign (JSON fields as JSON text, flags as 0/1). Raises ValueError
    with the message to show.

    `partial` is an edit: only the fields given are checked, and nothing is
    required. A rule that looks at two fields (a webhook campaign needs its
    destination, the link's argument must be one of the arguments) is then
    checked against `current`, the stored campaign, when it is given, and
    otherwise only when both fields are in the edit.
    """
    if not isinstance(fields, dict):
        raise ValueError("A campaign is a set of named fields")
    unknown = set(fields) - set(EDITABLE)
    if unknown:
        raise ValueError(f"Unknown campaign fields: {', '.join(sorted(unknown))}")
    if not partial and "name" not in fields:
        raise ValueError("Give the campaign a name")

    out = {name: _CLEAN[name](value) for name, value in fields.items()}

    if out.get("channel_link"):
        from campaigns import links

        link = links.parse_channel_link(out["channel_link"])
        for column, found in (("guild_id", link.guild_id), ("channel_id", link.channel_id)):
            if not found:
                continue
            if out.get(column) and out[column] != found:
                raise ValueError("The channel link and the server or channel ID don't match")
            out[column] = found
        if not link.channel_id and "channel_id" not in out:
            # A server's link names no channel: the one stored before belongs
            # to whatever link it came from, maybe another server.
            out["channel_id"] = ""

    base = dict(_NEW)
    if current is not None:
        base.update({k: json_field(current[k], []) if k == "command_args" else current[k]
                     for k in _NEW if cell(current, k) is not None})
    view = {**base, **out}

    def knows(*names: str) -> bool:
        return not partial or current is not None or all(n in out for n in names)

    if knows("method", "destination_id") and view["method"] == "discord_webhook" \
            and not view["destination_id"]:
        raise ValueError("Pick the webhook this campaign posts to (Settings → Discord → Destinations)")
    if knows("method", "website_url") and view["method"] == "website" and not view["website_url"]:
        raise ValueError("A website campaign needs its page's https:// link")
    if knows("url_argument", "command_args") and view["url_argument"]:
        names = [a.get("name") for a in json_field(view["command_args"], [])]
        if view["url_argument"] not in names:
            raise ValueError("The link's argument must be one of the command's arguments")
    if knows("command_name", "command_args", "url_argument") and view["command_name"]:
        # A command with no arguments at all is left alone: its bot may ask
        # for the link itself, and the link has its own copy button.
        args = json_field(view["command_args"], [])
        if args and not carries_link(args, view["url_argument"]):
            raise ValueError("Pick which argument takes the post's link: leave its value blank "
                             "or put {url} in it")
    if knows("min_seconds", "max_seconds") and view["max_seconds"] \
            and float(view["min_seconds"]) > float(view["max_seconds"]):
        raise ValueError("The shortest clip can't be longer than the longest")

    for name in _JSON_FIELDS:
        if name in out:
            out[name] = json.dumps(out[name])
    return out
