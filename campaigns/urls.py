"""A submission's public link: where it comes from, and checking one.

Nothing is ever invented. A link comes from a provider's row (WoopSocial or
Upload-Post), from a YouTube video Clips Kitty uploaded itself, or from the
user. When none of those has one yet, the answer says why and the submission
keeps waiting.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from campaigns.model import cell, platform_label, utc_text
from server import posts

# Hosts per platform: which platform a link is on.
_HOSTS = (
    ("tiktok", r"(?:www\.|m\.|vm\.|vt\.)?tiktok\.com"),
    ("instagram", r"(?:www\.)?instagram\.com"),
    ("youtube", r"(?:www\.|m\.)?youtube\.com|(?:www\.)?youtu\.be"),
    ("facebook", r"(?:www\.|m\.|web\.)?facebook\.com|fb\.watch"),
    ("x", r"(?:www\.)?(?:x|twitter)\.com"),
    ("threads", r"(?:www\.)?threads\.(?:net|com)"),
    ("linkedin", r"(?:www\.)?linkedin\.com"),
    ("pinterest", r"(?:[a-z]{2}\.|www\.)?pinterest\.[a-z.]{2,6}"),
    ("bluesky", r"(?:www\.)?bsky\.app"),
    ("reddit", r"(?:www\.)?reddit\.com"),
)
_HOST_RES = tuple((platform, re.compile(rf"^(?:{hosts})$")) for platform, hosts in _HOSTS)

# What a post's link looks like (host and path, plus ?v= for a YouTube watch
# page). Threads to Reddit are checked by host only: their paths change shape
# without notice, as ui/src/main/index.ts EXTERNAL_ALLOWED says.
_POSTS = {
    "tiktok": (r"(?:www\.|m\.)?tiktok\.com/@[\w.]+/video/\d+",),
    "instagram": (r"(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/[\w-]+",),
    "youtube": (
        r"(?:www\.|m\.)?youtube\.com/shorts/[\w-]{11}",
        r"(?:www\.|m\.)?youtube\.com/watch\?v=[\w-]{11}",
        r"(?:www\.)?youtu\.be/[\w-]{11}",
    ),
    "facebook": (r"(?:www\.|m\.|web\.)?facebook\.com/.+", r"fb\.watch/.+"),
    "x": (r"(?:www\.)?(?:x|twitter)\.com/\w+/status/\d+",),
    "threads": (r"(?:www\.)?threads\.(?:net|com)/.+",),
    "linkedin": (r"(?:www\.)?linkedin\.com/.+",),
    "pinterest": (r"(?:[a-z]{2}\.|www\.)?pinterest\.[a-z.]{2,6}/.+",),
    "bluesky": (r"(?:www\.)?bsky\.app/.+",),
    "reddit": (r"(?:www\.)?reddit\.com/.+",),
}
_POST_RES = {p: tuple(re.compile(rf"^{r}/?$") for r in rs) for p, rs in _POSTS.items()}
# One spelling per post, so the same post typed or returned two ways is one
# link to the campaign's duplicate check: the host every one of these opens
# on (ui/src/main/index.ts EXTERNAL_ALLOWED), and no trailing slash.
_CANONICAL_HOSTS = {
    "tiktok.com": "www.tiktok.com", "m.tiktok.com": "www.tiktok.com",
    "youtube.com": "www.youtube.com", "m.youtube.com": "www.youtube.com",
    "www.youtu.be": "youtu.be",
    "instagram.com": "www.instagram.com",
    "facebook.com": "www.facebook.com", "m.facebook.com": "www.facebook.com",
    "web.facebook.com": "www.facebook.com",
}
_TIKTOK_SHORT = re.compile(r"^(?:vm|vt)\.tiktok\.com/[\w-]+/?$")
_TIKTOK_HANDLE = re.compile(r"tiktok\.com/(@[\w.]+)/video/", re.IGNORECASE)

SHORT_LINK_WARNING = "This is a short link: some campaigns want the full one"
BLOCKED_PROVIDER = ("The link the publisher returned doesn't look like a public post: "
                    "check it and enter it by hand")
PRIVATE = "This video is private on YouTube"
STILL_PRIVATE = ("Still not public on YouTube a day after its scheduled time: "
                 "check it, then enter its link")
UPLOAD_GONE = ("Clips Kitty no longer has this YouTube upload's record (its clip was deleted): "
               "check the link, then confirm it")


@dataclass
class Resolution:
    state: str              # not_published | published_no_url | live | blocked
    url: str = ""
    post_id: str = ""
    account: str = ""
    caption: str = ""
    posted_at: str = ""     # UTC; known for a direct YouTube upload only
    source: str = ""        # provider | youtube | manual
    provider: str = ""      # of the row found; copied onto the submission
    visibility: str = ""    # clip_publishes.visibility, or the YouTube effective privacy
    due_at: str = ""        # the followed post's due time (UTC)
    outcome: str = ""       # "" | failed | skipped
    failed_at: str = ""     # when the failure was recorded (UTC); a re-send restamps it
    reason: str = ""        # shown as status_reason


# ---- checking a link ---------------------------------------------------------------------

def platform_of(url: str) -> str | None:
    """The platform a link is on, from its host; None when it is on none of them."""
    try:
        host = (urlsplit(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return None
    for platform, pattern in _HOST_RES:
        if pattern.match(host):
            return platform
    return None


def validate(platform: str, url: str) -> tuple[str, str]:
    """(normalised_url, problem). The problem is '' when the link is a public
    post's link on `platform`; it is then given back in one spelling: its
    query and fragment removed (except a YouTube watch page's ?v=), no
    trailing slash, and the www. host for TikTok, YouTube, Instagram and
    Facebook. https only."""
    text = str(url or "").strip()
    if not text:
        return "", "Enter the post's link"
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return "", "That isn't a link"
    if parts.scheme.lower() != "https":
        return "", "Paste the full link, starting with https://"
    if not host or port is not None or "@" in parts.netloc or re.search(r"\s", text):
        return "", "That isn't a link to a post"
    found = platform_of(text)
    if found is None:
        return "", "Clips Kitty can't tell which platform that link is on"
    if found != platform:
        return "", f"That's a {platform_label(found)} link, not a {platform_label(platform)} one"
    host = _CANONICAL_HOSTS.get(host, host)
    path = parts.path.rstrip("/")
    query = ""
    if platform == "youtube" and path == "/watch":
        video = (parse_qs(parts.query).get("v") or [""])[0]
        query = f"?v={video}"
    normalised = f"https://{host}{path}{query}"
    shape = f"{host}{path}{query}"
    if platform == "tiktok" and _TIKTOK_SHORT.match(shape):
        return normalised, ""
    if not any(p.match(shape) for p in _POST_RES.get(platform, ())):
        return "", f"That doesn't look like the link of a {platform_label(platform)} post"
    return normalised, ""


def link_warning(platform: str, url: str) -> str:
    """A link that passes but may not be what a campaign wants: a TikTok short
    link (vm./vt.tiktok.com) instead of the full one."""
    try:
        host = (urlsplit(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""
    if platform == "tiktok" and host in ("vm.tiktok.com", "vt.tiktok.com"):
        return SHORT_LINK_WARNING
    return ""


def account_from_url(platform: str, url: str) -> str:
    """The posting account a link shows: TikTok's "@handle" only."""
    if platform != "tiktok":
        return ""
    found = _TIKTOK_HANDLE.search(str(url or ""))
    return found.group(1) if found else ""


# ---- finding a submission's link -------------------------------------------------------------

def _local_utc(text) -> str:
    """created_at / updated_at / uploaded_at (local time, no zone) as UTC text;
    a value with a zone is read with it."""
    try:
        moment = datetime.fromisoformat(str(text or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return utc_text(moment)


def resolve(db, sub, *, now=None) -> Resolution:
    """Where the post a submission follows stands now, and its link if it has
    one. `sub` is a campaign_submissions row (or its dict).

    A YouTube post that failed (or was skipped) on its route is looked for on
    the other one too: a direct upload made since a WoopSocial or Upload-Post
    post failed, or a publisher's post published since a direct upload
    failed, is the post (the clip published again, with or without the
    campaign picked). Its provider is the one copied onto the submission."""
    sub = dict(sub)
    provider = str(sub.get("provider") or "")
    if provider == "manual":
        return _manual(sub)
    if provider == "youtube":
        res = _youtube(db, sub, now)
    else:
        row = _provider_row(db, sub)
        if row is not None:
            res = _from_provider_row(row, sub, now)
        elif not provider and sub.get("platform") == "youtube":
            res = _youtube(db, sub, now)
        else:
            return Resolution("not_published", provider=provider)
    if res.outcome and sub.get("platform") == "youtube":
        return _other_route(db, sub, res, now) or res
    return res


def _other_route(db, sub: dict, failed: Resolution, now) -> Resolution | None:
    """The YouTube post made through the other route at or after `failed`
    (a failed or skipped one), or None when there is none."""
    since = failed.failed_at
    if not since:
        return None
    if failed.provider == "youtube":
        row = _provider_row(db, sub)
        if row is None or row["state"] != "published" or _local_utc(row["updated_at"]) < since:
            return None
        return _from_provider_row(row, sub, now)
    upload = _upload(db, sub, int(sub.get("clip_id") or 0))
    if upload is None or _local_utc(upload["uploaded_at"]) < since:
        return None
    found = _youtube(db, {**sub, "provider": "youtube", "publish_job_id": 0}, now)
    return None if found.outcome else found


def _manual(sub: dict) -> Resolution:
    url = str(sub.get("post_url") or "")
    if not url:
        # Only the user can give this one its link, so it goes to them now
        # rather than waiting for a publisher that will never answer.
        return Resolution("blocked", provider="manual", source="manual", reason="Enter the post's link")
    normalised, problem = validate(sub["platform"], url)
    if problem:
        return Resolution("blocked", url=url, provider="manual", source="manual", reason=problem)
    return Resolution("live", url=normalised, provider="manual", source="manual",
                      post_id=str(sub.get("post_id") or ""),
                      account=account_from_url(sub["platform"], normalised))


def _provider_row(db, sub: dict):
    """The clip's row for this platform, by id or by the post's window, the
    published one first, then the newest. No provider filter: a clip has one
    row per platform, and publishing it again through the other provider
    overwrites that row's provider."""
    return db.conn.execute(
        "SELECT * FROM clip_publishes WHERE platform = ? AND (clip_id = ? OR "
        "(video_id != '' AND video_id = ? AND start_s = ? AND end_s = ?)) "
        "ORDER BY (state = 'published') DESC, updated_at DESC LIMIT 1",
        (sub["platform"], int(sub.get("clip_id") or 0), sub["video_id"],
         round(float(sub["start_s"]), 2), round(float(sub["end_s"]), 2)),
    ).fetchone()


def _from_provider_row(row, sub: dict, now) -> Resolution:
    base = {
        "provider": str(row["provider"] or sub.get("provider") or ""),
        "post_id": str(row["post_id"] or ""),
        "caption": str(cell(row, "caption", "")),
        "visibility": str(cell(row, "visibility", "")),
        "due_at": utc_text(posts.due_time(row, now=now)),
    }
    state = row["state"]
    if state == "published":
        url = str(row["post_url"] or "")
        if not url:
            return Resolution("published_no_url", **base)
        normalised, problem = validate(sub["platform"], url)
        if problem:
            return Resolution("blocked", url=url, source="provider", reason=BLOCKED_PROVIDER, **base)
        return Resolution("live", url=normalised, source="provider",
                          account=account_from_url(sub["platform"], normalised), **base)
    if state in ("failed", "skipped"):
        return Resolution("not_published", outcome=state, failed_at=_local_utc(row["updated_at"]),
                          reason=str(row["error"] or ""), **base)
    # queued | processing | sending: still on its way.
    return Resolution("not_published", **base)


def _upload(db, sub: dict, clip_id: int):
    return db.conn.execute(
        "SELECT * FROM uploads WHERE youtube_id != '' AND (clip_id = ? OR "
        "(video_id != '' AND video_id = ? AND start_s = ? AND end_s = ?)) "
        "ORDER BY (clip_id = ?) DESC, uploaded_at DESC LIMIT 1",
        (clip_id, sub["video_id"], round(float(sub["start_s"]), 2),
         round(float(sub["end_s"]), 2), clip_id),
    ).fetchone()


def _youtube(db, sub: dict, now) -> Resolution:
    """A direct YouTube upload: through its publish job while that runs, then
    its uploads row read as posts.youtube_view sees it. After a job that
    failed, an upload made since (the clip published again, with or without
    the campaign picked) is the post; without one, the post failed."""
    clip_id = int(sub.get("clip_id") or 0)
    job_id = int(sub.get("publish_job_id") or 0)
    job = db.get_publish_job(job_id) if job_id else None
    upload = None
    if job is not None:
        status = job["status"]
        if status in ("queued", "running"):
            return Resolution("not_published", provider="youtube")
        if status in ("failed", "cancelled", "interrupted"):
            ended = _local_utc(job["updated_at"])
            upload = _upload(db, sub, clip_id)
            if upload is None or not ended or _local_utc(upload["uploaded_at"]) < ended:
                return Resolution("not_published", provider="youtube", outcome="failed", failed_at=ended,
                                  reason=str(job["error"] or "") or f"The upload {status}.")
        else:
            # done: _publish keeps the job's clip_id on the clip it uploaded,
            # which a re-render made just before may have changed.
            clip_id = int(job["clip_id"] or clip_id)

    if upload is None:
        upload = _upload(db, sub, clip_id)
    if upload is None:
        post_id = str(sub.get("post_id") or "")
        if post_id:
            url = posts.youtube_url({"youtube_id": post_id, "start_s": sub["start_s"],
                                     "end_s": sub["end_s"]})
            return Resolution("blocked", url=url, post_id=post_id, provider="youtube",
                              source="youtube", reason=UPLOAD_GONE)
        return Resolution("not_published", provider="youtube")

    clip = db.get_clip(int(upload["clip_id"]))
    seen = posts.youtube_view(upload, clip, now)
    scheduled = str(upload["publish_at"] or "")
    actual = str(upload["actual_privacy"] or "").lower()
    asked = str(upload["privacy"] or "").lower()
    base = {
        "provider": "youtube",
        "post_id": str(upload["youtube_id"]),
        "account": str(upload["channel_title"] or ""),
        "caption": str(cell(upload, "description", "")),
        # Scheduling uploads private whatever was asked, so for a scheduled
        # upload only what YouTube reported counts (as youtube_view reads it).
        "visibility": actual if scheduled else (actual or asked),
        "due_at": utc_text(posts.due_time(upload, now=now)),
    }
    state = seen["state"]
    if state == "failed":
        when = str(upload["checked_at"] or "") or str(upload["uploaded_at"] or "")
        return Resolution("not_published", outcome="failed", failed_at=_local_utc(when),
                          reason=str(upload["error"] or "") or "YouTube rejected the upload.", **base)
    if state == "private":
        return Resolution("blocked", reason=STILL_PRIVATE if scheduled else PRIVATE, **base)
    if state != "published":
        # scheduled, or checking whether it went live at its time.
        return Resolution("not_published", **base)
    url, problem = validate("youtube", seen["post_url"])
    if problem:
        return Resolution("blocked", url=seen["post_url"], source="youtube", reason=problem, **base)
    # Live from its scheduled time, or from the upload when it had none.
    posted_at = utc_text(posts.due_time(upload, now=now))
    return Resolution("live", url=url, source="youtube", posted_at=posted_at, **base)
