"""The words of a submission: the message, the slash command a person types,
and the payload a webhook posts.

Placeholders are filled by plain replacement in one pass, like the watch
footer (server/automation.py render_footer): any other braces stay exactly as
typed, and a value that happens to contain "{title}" is not filled again.

A webhook can only post a message. It cannot run another bot's slash command,
so the command fields are for the person submitting by hand, and Discord is
never asked to mention anyone.
"""

import re

from campaigns.model import (
    FAIL,
    Check,
    cell,
    json_field,
    parse_utc,
    platform_label,
)

#: The values Clips Kitty fills in itself. An extra field can't take these names.
BUILTINS = ("url", "platform", "platform_id", "title", "campaign", "account", "creator",
            "source_url", "duration", "posted_at", "hashtags", "discord_user")

#: Discord's limits for a message and a forum thread's title.
MAX_CONTENT = 2000
MAX_THREAD_NAME = 100

_PLACEHOLDER = re.compile(r"\{([a-z0-9_]{1,32})\}")
_MORE = "…"

#: The values the campaign form's preview shows when it has no clip to use.
SAMPLE = {
    "url": "https://www.tiktok.com/@example/video/7000000000000000000",
    "platform": "TikTok", "platform_id": "tiktok", "title": "Example clip title",
    "campaign": "Example Campaign", "account": "@example", "creator": "Example Creator",
    "source_url": "https://www.youtube.com/watch?v=xxxxxxxxxxx", "duration": "30",
    "posted_at": "2026-01-31", "hashtags": "#example #clips", "discord_user": "example_user",
}


def _local_date(text) -> str:
    moment = parse_utc(text)
    return moment.astimezone().date().isoformat() if moment is not None else ""


def _clip(db, sub: dict):
    clip = db.get_clip(int(sub.get("clip_id") or 0)) if sub.get("clip_id") else None
    if clip is None:
        clip = db.conn.execute(
            "SELECT * FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
            (sub["video_id"], round(float(sub["start_s"]), 2), round(float(sub["end_s"]), 2)),
        ).fetchone()
    return clip


def _extra_values(campaign: dict, given: dict) -> dict[str, str]:
    out = {}
    for field in json_field(campaign.get("extra_fields"), []):
        name = str(field.get("name") or "") if isinstance(field, dict) else ""
        if not name or name in BUILTINS:
            continue
        value = given.get(name)
        out[name] = str(field.get("default") or "") if value in (None, "") else str(value)
    return out


def values_for(db, sub, campaign, *, discord_user: str = "") -> dict[str, str]:
    """Every placeholder's value for one submission: the builtins, then each
    extra field from the row's extra_values, else the field's default."""
    from server.woopsocial_service import tags_of

    sub, campaign = dict(sub), dict(campaign)
    video = db.conn.execute(
        "SELECT channel_name, source_url FROM videos WHERE video_id = ?", (sub["video_id"],)
    ).fetchone()
    clip = _clip(db, sub)
    values = {
        "url": str(sub.get("post_url") or ""),
        "platform": platform_label(sub["platform"]),
        "platform_id": str(sub["platform"]),
        "title": str(sub.get("clip_title") or ""),
        "campaign": str(campaign.get("name") or ""),
        "account": str(sub.get("account") or ""),
        "creator": str(cell(video, "channel_name", "")) if video is not None else "",
        # Blank when it isn't known: never made up.
        "source_url": str(cell(video, "source_url", "")) if video is not None else "",
        "duration": str(round(float(sub["end_s"]) - float(sub["start_s"]))),
        "posted_at": _local_date(sub.get("posted_at")),
        "hashtags": " ".join(f"#{t}" for t in tags_of(clip)) if clip is not None else "",
        "discord_user": str(discord_user or ""),
    }
    values.update(_extra_values(campaign, json_field(sub.get("extra_values"), {})))
    return values


def render(template: str, values: dict[str, str]) -> str:
    """Fill {name} placeholders from `values`; an unknown one stays as typed."""
    def fill(found: re.Match) -> str:
        name = found.group(1)
        return str(values[name]) if name in values else found.group(0)

    return _PLACEHOLDER.sub(fill, str(template or ""))


def command(campaign, values: dict, *, arg_values: dict | None = None) -> dict:
    """The slash command to type: {"name", "args": [{"name", "value"}],
    "url_arg", "line"}. The link's argument comes first, and its value is the
    link when its template is blank. A per-row `arg_values[name]` replaces that
    argument's value. `line` is the whole command written out, for reference
    only: nobody has checked that Discord runs a pasted line."""
    campaign = dict(campaign)
    name = str(campaign.get("command_name") or "").lstrip("/")
    url_arg = str(campaign.get("url_argument") or "")
    if not name:
        return {"name": "", "args": [], "url_arg": "", "line": ""}
    overrides = arg_values or {}
    args = [a for a in json_field(campaign.get("command_args"), []) if isinstance(a, dict) and a.get("name")]
    args.sort(key=lambda a: a["name"] != url_arg)
    out = []
    for arg in args:
        arg_name = str(arg["name"])
        if overrides.get(arg_name) is not None:
            value = str(overrides[arg_name])
        else:
            template = str(arg.get("value") or "")
            if arg_name == url_arg and not template:
                template = "{url}"
            value = render(template, values)
        out.append({"name": arg_name, "value": value})
    line = " ".join([f"/{name}", *(f"{a['name']}:{a['value']}" for a in out)])
    return {"name": name, "args": out, "url_arg": url_arg, "line": line}


def assisted_text(campaign, values: dict, *, message_text: str = "",
                  arg_values: dict | None = None) -> str:
    """What the person submits by hand: the row's own text when it has one,
    else the command line when the campaign uses a command, else its message."""
    campaign = dict(campaign)
    if str(message_text or "").strip():
        return render(message_text, values)
    if campaign.get("command_name"):
        return command(campaign, values, arg_values=arg_values)["line"]
    return render(campaign.get("message_template") or "{url}", values)


def fit(template: str, values: dict[str, str], limit: int = MAX_CONTENT) -> str:
    """The rendered template, at most `limit` characters when trimming can get
    it there: the hashtags lose tags from the end, then the title is cut. The
    link is never cut, so a message can still come back too long."""
    text = render(template, values)
    if len(text) <= limit:
        return text
    trimmed = dict(values)
    tags = str(trimmed.get("hashtags") or "").split()
    while tags and "{hashtags}" in template and len(text) > limit:
        tags.pop()
        trimmed["hashtags"] = " ".join(tags)
        text = render(template, trimmed)
    title = str(trimmed.get("title") or "")
    uses = template.count("{title}")
    while title and uses and len(text) > limit:
        over = len(text) - limit
        keep = max(0, len(title.rstrip(_MORE)) - (-(-over // uses)) - 1)
        title = title[:keep].rstrip() + _MORE if keep else ""
        trimmed["title"] = title
        text = render(template, trimmed)
    return text


def _thread_name(campaign: dict, values: dict) -> str:
    name = render(campaign.get("thread_name_template") or "{title}", values).strip()
    name = name or str(values.get("title") or "") or str(values.get("campaign") or "") or "Clip"
    if len(name) > MAX_THREAD_NAME:
        name = name[:MAX_THREAD_NAME - 1].rstrip() + _MORE
    return name


def webhook_payload(campaign, sub, values: dict, dest) -> dict:
    """The JSON a webhook posts: the row's own text or the campaign's message,
    fitted to Discord's 2,000 characters, with every mention switched off; a
    thread title when the destination is a forum and no thread is set."""
    campaign, sub = dict(campaign), dict(sub)
    own = str(sub.get("message_text") or "")
    template = own if own.strip() else str(campaign.get("message_template") or "{url}")
    payload = {
        "content": fit(template, values),
        "allowed_mentions": {"parse": []},
    }
    if dest is not None and int(cell(dest, "is_forum", 0) or 0) and not cell(dest, "thread_id", ""):
        payload["thread_name"] = _thread_name(campaign, values)
    return payload


def message_checks(campaign, values: dict, *, method: str, message_text: str = "",
                   dest=None) -> list[Check]:
    """What stops a submission's words from going out as they are: the link
    or a required extra field left empty ("Fill in ..."), a command typed by
    hand whose arguments leave the link out, and for a webhook a message
    still over 2,000 characters after trimming, or empty. Each one fails, so
    the verdict stops it for review."""
    campaign = dict(campaign)
    out = []
    url = str(values.get("url") or "")
    if not url:
        out.append(Check("fields", FAIL, "Fill in the post's link"))
    elif method != "discord_webhook" and campaign.get("command_name") and not str(message_text or "").strip():
        args = command(campaign, values)["args"]
        if args and not any(url in a["value"] for a in args):
            out.append(Check("fields", FAIL, "The command leaves out the post's link: "
                                             "pick which argument takes it"))
    for field in json_field(campaign.get("extra_fields"), []):
        if isinstance(field, dict) and field.get("required") and not str(values.get(field.get("name"), "")):
            out.append(Check("fields", FAIL, f"Fill in {field.get('label') or field.get('name')}"))
    if method == "discord_webhook":
        content = webhook_payload(campaign, {"message_text": message_text}, values, dest)["content"]
        if not content.strip():
            out.append(Check("length", FAIL, "The message is empty"))
        elif len(content) > MAX_CONTENT:
            out.append(Check("length", FAIL,
                             f"The message is longer than Discord's {MAX_CONTENT:,} characters: shorten it"))
    return out


def preview(campaign, sample: dict | None) -> dict:
    """What a campaign's settings produce, for the form: {"message", "command",
    "thread_name", "length", "problems"}. `sample` gives the values to use
    (a real clip's, or a draft caption's); the rest are SAMPLE's."""
    campaign = dict(campaign)
    values = {**SAMPLE, "campaign": str(campaign.get("name") or SAMPLE["campaign"]), **(sample or {})}
    for name, value in _extra_values(campaign, {}).items():
        values.setdefault(name, value)
    message = fit(campaign.get("message_template") or "{url}", values)
    method = str(campaign.get("method") or "")
    return {
        "message": message,
        "command": command(campaign, values),
        "thread_name": _thread_name(campaign, values),
        "length": len(message),
        "problems": [c.message for c in message_checks(campaign, values, method=method)],
    }

