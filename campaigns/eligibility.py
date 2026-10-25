"""What a campaign asks of a post, checked, and what to do about the answer.

precheck runs before the post exists (the editor's picker: platform, deadline
against the scheduled time, duration, and the caption being typed). check runs
once the post has a link and adds what only then can be known: that it is
public, which account posted it, where the clip came from, and whether the way
of submitting is usable. verdict turns the checks into the next step.

Each check passes, fails, or is unknown (Clips Kitty can't see). An unknown
holds up only an automatic send: doing a step by hand is the review.
"""

import re

from campaigns import urls
from campaigns.model import (
    FAIL,
    PASS,
    UNKNOWN,
    Check,
    cell,
    json_field,
    parse_utc,
    platform_label,
)
from server import posts

_HASHTAG = re.compile(r"#([\w]+)")
_MENTION = re.compile(r"@([\w.]+)")


# ---- where a clip came from ------------------------------------------------------------

def _mode_of_job(payload: dict) -> str:
    if payload.get("sport"):
        return "sports"
    if payload.get("longform"):
        return "longform"
    if payload.get("vertical_live"):
        return "vertical_live"
    if payload.get("podcast"):
        return "podcast"
    if payload.get("gaming") or payload.get("gaming_scoring"):
        return "gaming"
    if payload.get("pipeline"):
        return "pipeline"
    return "regular"


def _mode_of_render(opts: dict) -> str:
    if opts.get("profile"):
        return "longform"
    if opts.get("sport_moment"):
        return "sports"
    if opts.get("gaming"):
        return "gaming"
    return "unknown"


def _pipeline_id(payload: dict) -> str:
    choice = payload.get("pipeline")
    if isinstance(choice, dict):
        return str(choice.get("id") or "")
    return str(choice or "")


def clip_source(db, *, video_id, start_s, end_s, clip_id=0, job_payload=None) -> dict:
    """{"plugin", "mode", "creator_id"} of a clip, for a campaign's routing.

    plugin: the id a pipeline stamped on the clip (clips.scores "plugin"), else
    the job's pipeline id. mode: from the newest processing job of the video,
    else `job_payload` (automation's, for a job row already cleared), else from
    the clip's render options; "unknown" when nothing says. creator_id: the
    video's creator, or None.
    """
    clip = db.get_clip(int(clip_id)) if clip_id else None
    if clip is None and video_id:
        clip = db.conn.execute(
            "SELECT * FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
            (video_id, round(float(start_s), 2), round(float(end_s), 2)),
        ).fetchone()
    video = video_id or (clip["video_id"] if clip is not None else "")
    scores = json_field(cell(clip, "scores", ""), {}) if clip is not None else {}
    plugin = str(scores.get("plugin") or "")

    payload = None
    if video:
        job = db.conn.execute(
            "SELECT payload FROM jobs WHERE type = 'process' AND video_id = ? ORDER BY id DESC LIMIT 1",
            (video,),
        ).fetchone()
        if job is not None:
            payload = json_field(job["payload"], None)
            payload = payload if isinstance(payload, dict) else None
    if payload is None and isinstance(job_payload, dict):
        payload = job_payload
    if payload is not None:
        mode = _mode_of_job(payload)
        plugin = plugin or _pipeline_id(payload)
    else:
        opts = json_field(cell(clip, "render_opts", ""), {}) if clip is not None else {}
        mode = _mode_of_render(opts)

    creator_id = None
    if video:
        row = db.conn.execute("SELECT creator_id FROM videos WHERE video_id = ?", (video,)).fetchone()
        if row is not None and row["creator_id"] is not None:
            creator_id = int(row["creator_id"])
    return {"plugin": plugin, "mode": mode, "creator_id": creator_id}


# ---- the checks ------------------------------------------------------------------------------

def _caption_checks(campaign: dict, caption: str) -> list[Check]:
    hashtags = json_field(campaign.get("required_hashtags"), [])
    mentions = json_field(campaign.get("required_mentions"), [])
    phrase = str(campaign.get("required_text") or "").strip()
    asked = [("hashtags", hashtags), ("mentions", mentions), ("text", phrase)]
    if not any(rule for _, rule in asked):
        return []
    text = str(caption or "")
    if not text.strip():
        return [Check(name, UNKNOWN, "Clips Kitty doesn't know what the caption said")
                for name, rule in asked if rule]

    out = []
    if hashtags:
        have = {t.lower() for t in _HASHTAG.findall(text)}
        missing = [t for t in hashtags if str(t).lower() not in have]
        out.append(Check("hashtags", FAIL, "Missing " + ", ".join(f"#{t}" for t in missing))
                   if missing else Check("hashtags", PASS))
    if mentions:
        have = {m.rstrip(".").lower() for m in _MENTION.findall(text)}
        missing = [m for m in mentions if str(m).lower() not in have]
        out.append(Check("mentions", FAIL, "Missing " + ", ".join(f"@{m}" for m in missing))
                   if missing else Check("mentions", PASS))
    if phrase:
        words = r"\s+".join(re.escape(w) for w in phrase.split())
        found = re.search(rf"(?<!\w){words}(?!\w)", text, re.IGNORECASE)
        out.append(Check("text", PASS) if found else Check("text", FAIL, f"Missing “{phrase}”"))
    return out


def _seconds(value: float) -> str:
    return f"{value:g} s"


def precheck(campaign, *, platform: str, duration_s: float, caption: str = "",
             scheduled_for: str = "", now) -> list[Check]:
    """The checks that can run before the post exists: platform, deadline (against
    the scheduled time when one is given), duration, and hashtags, mentions and
    the phrase on a draft caption."""
    campaign = dict(campaign)
    current = posts.as_utc(now)
    out = []

    platforms = json_field(campaign.get("platforms"), [])
    if not platforms or platform in platforms:
        out.append(Check("platform", PASS))
    else:
        out.append(Check("platform", FAIL, f"This campaign doesn't take {platform_label(platform)} posts"))

    deadline_text = str(campaign.get("deadline") or "")
    deadline = parse_utc(deadline_text)
    scheduled = parse_utc(scheduled_for) if scheduled_for else None
    if not deadline_text:
        out.append(Check("deadline", PASS))
    elif deadline is None:
        out.append(Check("deadline", UNKNOWN, "Clips Kitty can't read the campaign's deadline"))
    elif current >= deadline:
        out.append(Check("deadline", FAIL, "The campaign's deadline has passed"))
    elif scheduled is not None and scheduled > deadline:
        out.append(Check("deadline", FAIL, "Its deadline is before this post's time"))
    else:
        out.append(Check("deadline", PASS))

    low = float(campaign.get("min_seconds") or 0)
    high = float(campaign.get("max_seconds") or 0)
    length = float(duration_s or 0)
    if length < low or (high and length > high):
        wanted = f"{_seconds(low)} to {_seconds(high)}" if high else f"at least {_seconds(low)}"
        out.append(Check("duration", FAIL, f"The clip is {_seconds(round(length, 1))}; this campaign takes {wanted}"))
    else:
        out.append(Check("duration", PASS))

    out += _caption_checks(campaign, caption)
    return out


def _bare_account(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).lstrip("@").lower()


def _public(sub: dict, res) -> Check:
    url = res.url or str(sub.get("post_url") or "")
    _, problem = urls.validate(sub["platform"], url)
    if problem:
        return Check("public", FAIL, problem)
    if res.source == "manual" or sub.get("url_source") == "manual":
        return Check("public", PASS, "You entered this link")
    seen = str(res.visibility or "").lower()
    if seen in ("public", "default"):
        return Check("public", PASS)
    if not seen:
        return Check("public", UNKNOWN, "Clips Kitty didn't record who can see this post")
    shown = {"friends": "only for friends"}.get(seen, seen)
    return Check("public", FAIL, f"This post is {shown}, not public")


def _account(campaign: dict, sub: dict, res) -> list[Check]:
    wanted = json_field(campaign.get("required_accounts"), {}).get(sub["platform"])
    if not wanted:
        return []
    seen = res.account or str(sub.get("account") or "")
    if not seen:
        return [Check("account", UNKNOWN, "Clips Kitty can't see which account posted this")]
    if _bare_account(seen) == _bare_account(wanted):
        return [Check("account", PASS)]
    return [Check("account", FAIL, f"Posted from {seen}, and this campaign wants {wanted}")]


def _routing(campaign: dict, source: dict | None) -> list[Check]:
    filters = json_field(campaign.get("filters"), {})
    plugins = filters.get("plugins") or []
    modes = filters.get("modes") or []
    creators = filters.get("creator_ids") or []
    if not (plugins or modes or creators):
        return []
    if source is None:
        return [Check("routing", UNKNOWN, "Clips Kitty can't tell where this clip came from")]
    mode = source.get("mode") or "unknown"
    results = []
    if plugins:
        plugin = source.get("plugin") or ""
        if plugin:
            results.append(plugin in plugins)
        else:
            # No plugin stamp: a clip a plugin made always has one, so it is
            # not from a plugin, unless nothing at all is known about it.
            results.append(None if mode == "unknown" else False)
    if modes:
        results.append(None if mode == "unknown" else mode in modes)
    if creators:
        creator = source.get("creator_id")
        results.append(None if creator is None else int(creator) in [int(c) for c in creators])
    if False in results:
        return [Check("routing", FAIL, "This clip isn't one the campaign's routing takes")]
    if None in results:
        return [Check("routing", UNKNOWN, "Clips Kitty can't tell where this clip came from")]
    return [Check("routing", PASS)]


def routes(campaign, source: dict | None) -> bool:
    """Whether a campaign's routing takes a clip from `source`: always when
    it has no filters, and never when a filter is set and the source can't
    be told (automation attaches only what is known to match)."""
    found = _routing(dict(campaign), source)
    return not found or found[0].result == PASS


def check(campaign, sub, res, *, source: dict | None, now, destination=None) -> list[Check]:
    """Every check once the post has a link: precheck on the caption it went
    out with (no scheduled time: it is live by now), then public, account,
    routing, and the way of submitting: a webhook's destination exists and is
    connected (`destination`, its discord_destinations row), an assisted
    campaign has a channel saved."""
    campaign, sub = dict(campaign), dict(sub)
    out = precheck(campaign, platform=sub["platform"],
                   duration_s=float(sub["end_s"]) - float(sub["start_s"]),
                   caption=res.caption, now=now)
    out.append(_public(sub, res))
    out += _account(campaign, sub, res)
    out += _routing(campaign, source)
    warning = urls.link_warning(sub["platform"], res.url or str(sub.get("post_url") or ""))
    if warning:
        out.append(Check("link", UNKNOWN, warning))

    method = str(sub.get("method") or campaign.get("method") or "")
    if method == "discord_webhook":
        state = str(cell(destination, "state", "")) if destination is not None else ""
        if destination is None:
            out.append(Check("destination", FAIL, "The webhook to post to is missing: pick one"))
        elif state != "connected":
            out.append(Check("destination", FAIL, f"The webhook is {state or 'not usable'}: submit it by hand or pick another"))
        else:
            out.append(Check("destination", PASS))
    elif method == "discord_assisted":
        if str(campaign.get("channel_id") or ""):
            out.append(Check("channel", PASS))
        else:
            out.append(Check("channel", UNKNOWN, "No channel saved: you'll find the channel yourself"))
    return out


def verdict(checks: list[Check], *, method: str, trust_account: bool) -> str:
    """What to do next: "ok", "review", "manual" or "expired".

    A failed deadline expires it; a failed destination sends it to be done by
    hand; any other failure stops for review. An unknown stops for review only
    when the next step would be automatic (a webhook); an account Clips Kitty
    can't see counts as right with `trust_account`.
    """
    failed = {c.name for c in checks if c.result == FAIL}
    if "deadline" in failed:
        return "expired"
    if "destination" in failed:
        return "manual"
    if failed:
        return "review"
    unknown = [c for c in checks if c.result == UNKNOWN
               and not (c.name == "account" and trust_account)]
    if unknown and method == "discord_webhook":
        return "review"
    return "ok"
