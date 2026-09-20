"""Upload-Post: one request, several platforms.

The reason this provider exists is the `platform[]` form field. Sending it once
per destination makes a single POST fan out server-side, so a clip reaches
YouTube, TikTok, Instagram and the rest from one upload of the bytes. Looping a
per-platform upload would re-send the video every time and lose the partial
results, which is the whole point of the service.

Stdlib only, like the rest of `publish/`. The tests have to import this on a CI
runner with no third-party HTTP library installed.

Two facts about this API that the code below is shaped around:

* **The host is `https://api.upload-post.com` and every path starts `/api/`.**
  The docs also print the base as `.../api`, but that URL is a 404 — the two
  must never be concatenated.
* **A synchronous upload is cut off at 59 seconds**, which no real clip will
  finish inside. So every upload is sent with `async_upload=true` and the
  result is polled. A desktop app cannot receive their webhooks.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from publish.errors import (
    AuthRequired,
    NotConnected,
    PublishError,
    QuotaExceeded,
    RateLimited,
)
from publish.multipart import encode as _encode_multipart

BASE_URL = "https://api.upload-post.com"

# Reading a status page is quick; pushing a video is not.
TIMEOUT = 30
UPLOAD_TIMEOUT = 900

# What Upload-Post documents for *video*. Their account can connect more
# (Slack, Mastodon, Dev.to and so on), but those take text or photos only, and
# offering a clip a destination that cannot hold one is worse than not
# offering it. Reddit is documented and currently returns 503 — it is listed
# so the capability model can mark it unavailable rather than pretend it is
# missing.
VIDEO_PLATFORMS = (
    "youtube",
    "tiktok",
    "instagram",
    "facebook",
    "x",
    "threads",
    "linkedin",
    "pinterest",
    "bluesky",
    "reddit",
    "discord",
    "telegram",
    "google_business",
)

# Terminal states from GET /api/uploadposts/status.
DONE_STATES = ("completed", "failed", "not_found")

# Upload-Post's scheduling horizon.
MAX_SCHEDULE_DAYS = 365


# What each platform actually accepts, taken from Upload-Post's own reference
# rather than assumed. The UI reads this to decide which controls to show, so
# nobody is offered a box that silently does nothing.
#
# Two conventions worth knowing before reading the table. Most per-platform
# fields are `{platform}_{field}`, but a handful are bare names that only
# apply to one platform anyway (`privacy_level` is TikTok's, `visibility` is
# LinkedIn's), and YouTube's are camelCase mirrors of the YouTube API rather
# than snake_case like everything else.
#
# `requires` is the important column. Facebook will not accept a video without
# a page id and Pinterest will not accept one without a board id, so a publish
# that omits them fails on that platform for a reason the user cannot guess.
PLATFORM_FIELDS: dict[str, dict] = {
    "youtube": {
        "title": "youtube_title",
        "description": "youtube_description",
        "first_comment": "youtube_first_comment",
        "ai_disclosure": "containsSyntheticMedia",
        "thumbnail": True,
        "requires": {},
        "extra": {
            "privacy": "privacyStatus",       # public | unlisted | private
            "category": "categoryId",
            "made_for_kids": "selfDeclaredMadeForKids",
        },
    },
    "tiktok": {
        "title": "tiktok_title",
        "description": None,                   # caption only
        "first_comment": "tiktok_first_comment",
        "ai_disclosure": "is_aigc",
        "thumbnail": False,                    # cover_timestamp instead
        "requires": {},
        "extra": {
            "privacy": "privacy_level",
            "disable_comment": "disable_comment",
            "disable_duet": "disable_duet",
            "disable_stitch": "disable_stitch",
        },
    },
    "instagram": {
        "title": "instagram_title",
        "description": None,
        "first_comment": "instagram_first_comment",
        "ai_disclosure": "is_ai_generated",
        "thumbnail": False,                    # cover_image / thumb_offset
        "requires": {},
        "extra": {"media_type": "media_type", "share_to_feed": "share_to_feed"},
    },
    "facebook": {
        "title": "facebook_title",
        "description": "facebook_description",
        "first_comment": "facebook_first_comment",
        "ai_disclosure": "facebook_is_ai_generated",
        "thumbnail": True,                     # VIDEO media type only
        "requires": {"facebook_page_id": "Facebook page ID"},
        "extra": {"media_type": "facebook_media_type"},
    },
    "x": {
        "title": "x_title",
        "description": None,
        "first_comment": "x_first_comment",
        "ai_disclosure": "made_with_ai",
        "thumbnail": False,
        "requires": {},
        "extra": {"reply_settings": "reply_settings"},
    },
    "threads": {
        "title": "threads_title",
        "description": None,
        "first_comment": "threads_first_comment",
        "ai_disclosure": None,
        "thumbnail": False,
        "requires": {},
        "extra": {"topic_tag": "threads_topic_tag", "reply_control": "threads_reply_control"},
    },
    "linkedin": {
        "title": "linkedin_title",
        "description": "linkedin_description",
        "first_comment": "linkedin_first_comment",
        "ai_disclosure": None,
        "thumbnail": True,
        "requires": {},
        "extra": {"visibility": "visibility"},
    },
    "pinterest": {
        "title": "pinterest_title",
        "description": "pinterest_description",
        "first_comment": None,
        "ai_disclosure": "pinterest_ai_disclosures",
        "thumbnail": False,                    # cover image is its own family
        "requires": {"pinterest_board_id": "Pinterest board ID"},
        "extra": {"link": "pinterest_link"},
    },
    "bluesky": {
        "title": "bluesky_title",
        "description": None,
        "first_comment": "bluesky_first_comment",
        "ai_disclosure": None,
        "thumbnail": False,
        "requires": {},
        "extra": {"alt_text": "bluesky_alt_text"},
    },
}


def missing_required(platforms: list[str], given: dict[str, str]) -> list[str]:
    """Human-readable names of required fields the caller has not supplied.

    Checked before the upload rather than after, because Upload-Post reports
    these as a per-platform failure once the video has already been sent —
    the user waits for an upload that was never going to work.
    """
    wanted: list[str] = []
    for platform in platforms:
        needed = (PLATFORM_FIELDS.get(platform, {}).get("requires") or {}).items()
        for name, label in needed:
            if not (given.get(name) or "").strip():
                wanted.append(label)
    return wanted


class UploadPostError(PublishError):
    """An Upload-Post failure that is not one of the shared classes."""


@dataclass
class PlatformOutcome:
    """What happened on ONE destination of a fan-out."""

    platform: str
    state: str = "queued"  # queued | processing | published | failed | skipped
    post_id: str = ""
    post_url: str = ""
    error: str = ""

    @property
    def skipped(self) -> bool:
        return self.state == "skipped"


@dataclass
class FanOutResult:
    """One upload, several destinations.

    Deliberately not `PublishResult`. That type carries a single video_id and
    a single URL because a YouTube upload has exactly one of each, and
    flattening eight destinations into it would throw away the per-platform
    errors that are the main thing a user needs to see. It lives here rather
    than in publish/base.py because Upload-Post is currently the only provider
    that fans out; if a second one appears, move it.
    """

    request_id: str = ""
    outcomes: list[PlatformOutcome] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return all(o.state in ("published", "failed", "skipped") for o in self.outcomes)

    @property
    def published(self) -> list[PlatformOutcome]:
        return [o for o in self.outcomes if o.state == "published"]

    @property
    def failed(self) -> list[PlatformOutcome]:
        return [o for o in self.outcomes if o.state == "failed"]


# Per-platform id fields differ by platform: YouTube returns video_id, Meta
# returns container_id, LinkedIn video_urn, and so on. Checked in order.
_ID_FIELDS = ("post_id", "video_id", "publish_id", "container_id", "video_urn", "id")


def _one_outcome(platform: str, entry: dict) -> PlatformOutcome:
    if not isinstance(entry, dict):
        return PlatformOutcome(platform=platform, state="failed", error=str(entry)[:300])

    # A platform the user ticked but has not connected comes back skipped,
    # not failed. Telling someone their upload failed when they simply have
    # no TikTok account linked sends them looking for a bug.
    if entry.get("skipped"):
        reason = str(entry.get("reason") or entry.get("error") or "")
        return PlatformOutcome(
            platform=platform,
            state="skipped",
            error=(
                "Not connected to Upload-Post yet."
                if "not_configured" in reason
                else reason[:300]
            ),
        )

    post_id = ""
    for key in _ID_FIELDS:
        if entry.get(key):
            post_id = str(entry[key])
            break

    status = str(entry.get("status") or "").lower()
    success = entry.get("success")

    if success is False:
        state = "failed"
    elif success is True or status in ("completed", "publish_success", "success"):
        state = "published"
    elif status in ("queued", "pending"):
        state = "queued"
    elif status in ("processing", "in_progress"):
        state = "processing"
    elif status == "failed":
        state = "failed"
    else:
        state = "processing"

    return PlatformOutcome(
        platform=platform,
        state=state,
        post_id=post_id,
        post_url=str(entry.get("url") or entry.get("post_url") or ""),
        error=str(entry.get("error") or entry.get("message") or "")[:300] if state == "failed" else "",
    )


def parse_results(payload: dict) -> FanOutResult:
    """Normalise a fan-out response into per-platform outcomes.

    Upload-Post returns `results` in two different shapes and both have to be
    handled: the upload call answers with a dict keyed by platform, while the
    status call answers with a list of entries that each name their own
    platform.

    The top-level `success` is deliberately ignored. It is `true` even when a
    platform inside `results` failed, so trusting it reports a partial failure
    as a clean success — which is exactly the bug this function exists to
    prevent.
    """
    result = FanOutResult(request_id=str(payload.get("request_id") or ""))
    raw = payload.get("results")

    if isinstance(raw, dict):
        for platform, entry in raw.items():
            result.outcomes.append(_one_outcome(str(platform), entry))
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                result.outcomes.append(_one_outcome(str(entry.get("platform") or ""), entry))

    result.outcomes.sort(key=lambda o: o.platform)
    return result


def _classify(status: int, payload: dict, body: str) -> PublishError:
    """Map an HTTP failure onto the shared PublishError hierarchy.

    Kept here rather than in publish/errors.py because that module's messages
    are written about YouTube, and a creator reading "reconnect your YouTube
    account" after a TikTok plan error is worse than no message at all.
    """
    detail = str(payload.get("error") or payload.get("message") or body)[:500]

    if status == 401:
        return AuthRequired(
            "Upload-Post did not accept that API key. Check it in Settings.",
            detail=detail,
        )
    if status == 403:
        # Overwhelmingly a plan restriction — TikTok is not on the free plan,
        # and their message names the platform, so it is worth surfacing.
        return UploadPostError(
            f"Upload-Post refused that on your current plan. {detail}",
            detail=detail,
        )
    if status == 404:
        return NotConnected(
            "That Upload-Post profile no longer exists. Reconnect it in Settings.",
            detail=detail,
        )
    if status == 429:
        # Two different things share this code, and they need opposite
        # handling. The monthly upload allowance being gone is not worth
        # retrying until next month; being asked to slow down is worth
        # retrying in seconds. The allowance reply carries a `usage` block.
        monthly = payload.get("usage") is not None or "monthly" in detail.lower()
        if monthly:
            used = (payload.get("usage") or {}).get("count")
            limit = (payload.get("usage") or {}).get("limit")
            counted = f" ({used} of {limit} used)" if used is not None and limit else ""
            return QuotaExceeded(
                f"Your Upload-Post upload allowance for this month is used up{counted}.",
                detail=detail,
            )
        return RateLimited(
            "Upload-Post asked us to slow down. Trying again shortly.", detail=detail
        )
    if status == 400:
        # The one 400 worth naming: none of the chosen platforms is connected.
        if payload.get("invalid_platforms"):
            names = ", ".join(sorted(payload["invalid_platforms"]))
            return NotConnected(
                f"None of those platforms are connected to Upload-Post yet: {names}. "
                "Open Manage Social Accounts to connect them.",
                detail=detail,
            )
        return UploadPostError(f"Upload-Post rejected the request. {detail}", detail=detail)
    if status >= 500:
        err = UploadPostError("Upload-Post had a server error.", detail=detail)
        err.retryable = True
        return err

    return UploadPostError(f"Upload-Post rejected the upload. {detail}", detail=detail)


class UploadPostClient:
    """Everything Clips Kitty needs from the Upload-Post API, and nothing else.

    The key is held here and never returned by any method, so no caller can
    accidentally put it in a response body, a log line or an event.
    """

    def __init__(self, api_key: str, *, base_url: str = BASE_URL):
        self._key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")

    # ---- plumbing --------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        fields: list[tuple[str, str]] | None = None,
        files: list[tuple[str, Path]] | None = None,
        idempotency_key: str = "",
        timeout: int = TIMEOUT,
    ) -> dict:
        if not self._key:
            raise NotConnected(
                "No Upload-Post API key is set. Add one in Settings to publish."
            )

        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v not in (None, "")}
            )

        headers = {"Authorization": f"Apikey {self._key}", "Accept": "application/json"}
        if idempotency_key:
            # Documented, and the reason a timed-out retry cannot double-post.
            headers["Idempotency-Key"] = idempotency_key

        data = None
        if fields is not None or files is not None:
            data, content_type = _encode_multipart(fields or [], files or [])
            headers["Content-Type"] = content_type
        elif json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            payload = {}
            try:
                payload = json.loads(body) if body else {}
            except ValueError:
                pass
            raise _classify(int(e.code), payload if isinstance(payload, dict) else {}, body) from e
        except urllib.error.URLError as e:
            err = UploadPostError(
                "Could not reach Upload-Post. Check your internet connection.",
                detail=str(getattr(e, "reason", e))[:200],
            )
            err.retryable = True
            raise err from e
        except TimeoutError as e:
            err = UploadPostError("Upload-Post did not respond in time.", detail="timeout")
            err.retryable = True
            raise err from e

        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError as e:
            raise UploadPostError(
                "Upload-Post sent a reply Clips Kitty could not read.",
                detail=raw[:200],
            ) from e
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    # ---- account ---------------------------------------------------------

    def validate_key(self) -> dict:
        """Check the key and report the plan. Raises AuthRequired if it is bad."""
        return self._request("GET", "/api/uploadposts/me")

    def list_profiles(self) -> list[dict]:
        """The sub-accounts under this key. The `user` field on every upload."""
        return self._request("GET", "/api/uploadposts/users").get("profiles") or []

    def create_profile(self, username: str) -> dict:
        return self._request(
            "POST", "/api/uploadposts/users", json_body={"username": username}
        )

    def connected_platforms(self, username: str) -> list[str]:
        """Which social accounts this profile has linked.

        Read defensively. Upload-Post's profile object carries the linked
        accounts, but the key it uses is not pinned down in their docs, so
        every plausible shape is accepted rather than assuming one and
        showing a creator "nothing connected" when they plainly have.
        """
        for profile in self.list_profiles():
            if str(profile.get("username") or "") != username:
                continue
            found: set[str] = set()
            for key in ("social_accounts", "accounts", "connections", "platforms"):
                value = profile.get(key)
                if isinstance(value, dict):
                    # {"youtube": {...}} or {"youtube": true}
                    found.update(k for k, v in value.items() if v)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            found.add(item)
                        elif isinstance(item, dict):
                            name = item.get("platform") or item.get("name")
                            if name:
                                found.add(str(name))
            return sorted(found)
        return []

    def connect_url(self, username: str) -> str:
        """A hosted page for linking social accounts, good for 48 hours.

        This is why Clips Kitty needs no social OAuth of its own and never asks
        for a platform password: the user does it on Upload-Post's page, in
        their own browser.
        """
        got = self._request(
            "POST",
            "/api/uploadposts/users/generate-jwt",
            json_body={"username": username},
        )
        return got.get("access_url") or ""

    # ---- publishing ------------------------------------------------------

    def upload_video(
        self,
        video: Path,
        *,
        profile: str,
        platforms: list[str],
        fields: list[tuple[str, str]],
        thumbnail: Path | None = None,
        idempotency_key: str = "",
    ) -> dict:
        """Start one upload that fans out to every platform in `platforms`.

        Returns immediately with a `request_id` to poll — see the note at the
        top about the 59-second cut-off on synchronous uploads.
        """
        form: list[tuple[str, str]] = [("user", profile), ("async_upload", "true")]
        form += [("platform[]", p) for p in platforms]
        form += fields

        files: list[tuple[str, Path]] = [("video", video)]
        if thumbnail is not None:
            files.append(("thumbnail", thumbnail))

        return self._request(
            "POST",
            "/api/upload",
            fields=form,
            files=files,
            idempotency_key=idempotency_key,
            timeout=UPLOAD_TIMEOUT,
        )

    def get_status(self, request_id: str) -> dict:
        return self._request(
            "GET", "/api/uploadposts/status", params={"request_id": request_id}
        )

    def retry(self, request_id: str) -> dict:
        """Re-run only the platforms that failed. The media is not re-sent."""
        return self._request(
            "POST", "/api/uploadposts/posts/retry", json_body={"request_id": request_id}
        )


def platform_overrides(per_platform: dict[str, dict]) -> list[tuple[str, str]]:
    """Turn {'instagram': {'title': '...'}} into the API's flat field names.

    Only fields the platform actually documents are emitted. An Instagram
    caption written here cannot touch the YouTube title, because they are two
    differently-named fields and neither is the generic one.
    """
    fields: list[tuple[str, str]] = []
    for platform, values in (per_platform or {}).items():
        spec = PLATFORM_FIELDS.get(platform)
        if not spec or not isinstance(values, dict):
            continue
        for key, value in values.items():
            if value in (None, "", False):
                continue
            name = spec.get(key) if key in ("title", "description", "first_comment",
                                            "ai_disclosure") else (spec.get("extra") or {}).get(key)
            # A field this platform does not support is dropped rather than
            # guessed at — sending an invented name is how you get a 400 with
            # nothing useful in it.
            if not name:
                continue
            fields.append((name, "true" if value is True else str(value)))
    return fields


def schedule_fields(
    *,
    scheduled_date: str = "",
    timezone: str = "",
    add_to_queue: bool = False,
) -> list[tuple[str, str]]:
    """Scheduling and queueing, which are two different features.

    `scheduled_date` names a time. `add_to_queue` drops the post into the next
    free slot of the user's own queue and lets Upload-Post pick the time.
    **They cannot both be sent**, so this refuses rather than letting the API
    decide which one it felt like honouring.
    """
    if add_to_queue and scheduled_date:
        raise UploadPostError(
            "Pick either a specific time or the queue, not both."
        )
    if add_to_queue:
        return [("add_to_queue", "true")]
    if not scheduled_date:
        return []
    fields = [("scheduled_date", scheduled_date)]
    if timezone:
        # Defaults to UTC at their end, which silently shifts a creator's
        # 7pm by however far they are from Greenwich.
        fields.append(("timezone", timezone))
    return fields


def build_fields(
    *,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    first_comment: str = "",
    overrides: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Turn common metadata into form fields.

    Upload-Post's own fallback chain does most of the per-platform work: a
    platform with no `{platform}_title` set falls back to the generic `title`.
    So writing the metadata once and sending it once is the correct mapping,
    not a shortcut — `platform_overrides()` layers the exceptions on top.

    One quirk worth knowing rather than discovering: LinkedIn's description
    falls back to `title`, not to `description`.
    """
    fields: list[tuple[str, str]] = [("title", title)]
    if description:
        fields.append(("description", description))
    for tag in tags or []:
        # Repeated, like platform[]. A single comma-joined string is a
        # different thing to this API.
        fields.append(("tags[]", tag))
    if first_comment:
        fields.append(("first_comment", first_comment))
    for name, value in (overrides or {}).items():
        if value not in (None, ""):
            fields.append((name, str(value)))
    return fields


class UploadPostPublisher:
    """Publishes one clip to several platforms in a single upload.

    Not a `publish.base.Publisher` subclass. That interface returns one
    PublishResult with one video id, which is the right contract for YouTube
    and the wrong one here — the whole value of this provider is the
    per-platform breakdown, and satisfying the ABC would mean discarding it.
    The existing YouTube path keeps that interface untouched.
    """

    name = "upload_post"

    def __init__(self, client: UploadPostClient, profile: str):
        self.client = client
        self.profile = profile

    def start(
        self,
        video: Path,
        *,
        platforms: list[str],
        fields: list[tuple[str, str]],
        thumbnail: Path | None = None,
        idempotency_key: str = "",
    ) -> FanOutResult:
        """Send the clip once, to every platform. Returns straight away.

        The upload is asynchronous because their synchronous mode is cut off
        at 59 seconds, which no real clip survives. Poll `check()` with the
        returned request_id.
        """
        chosen = [p for p in platforms if p in VIDEO_PLATFORMS]
        if not chosen:
            raise UploadPostError(
                "Pick at least one platform that can take a video."
            )
        if not self.profile:
            raise NotConnected(
                "No Upload-Post profile is set up yet. Open Manage Social Accounts "
                "in Settings to connect your accounts."
            )

        payload = self.client.upload_video(
            video,
            profile=self.profile,
            platforms=chosen,
            fields=fields,
            thumbnail=thumbnail,
            idempotency_key=idempotency_key,
        )
        result = parse_results(payload)

        # An async start answers with a request_id and no per-platform detail
        # yet. Seed a queued row for each destination so the UI has something
        # truthful to show before the first poll lands.
        if not result.outcomes:
            result.outcomes = [PlatformOutcome(platform=p) for p in sorted(chosen)]
        return result

    def check(self, request_id: str) -> FanOutResult:
        got = self.client.get_status(request_id)
        result = parse_results(got)
        if not result.request_id:
            result.request_id = request_id
        return result

    def retry(self, request_id: str) -> FanOutResult:
        """Re-run the platforms that failed, through Upload-Post's own retry.

        Not a fresh upload: their retry reuses the media already stored
        against the request, so the clip is not sent twice and the platforms
        that already succeeded are left alone. Re-publishing from scratch
        would risk a duplicate post on every platform that worked.
        """
        got = self.client.retry(request_id)
        result = parse_results(got)
        if not result.request_id:
            result.request_id = request_id
        return result


def validate_schedule(when: str, *, now=None) -> str:
    """Check a scheduled time before it is sent.

    Upload-Post accepts up to a year ahead. A time in the past is the more
    common mistake — a user picks today's date and a time that has already
    gone — and it is worth catching here rather than as an opaque 400.
    """
    from datetime import datetime, timedelta
    from datetime import timezone as tz

    text = (when or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as e:
        raise UploadPostError(
            "That scheduled time was not a date Clips Kitty could read."
        ) from e

    current = now or datetime.now(tz.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz.utc)
    if parsed <= current:
        raise UploadPostError("That time has already passed. Pick a time in the future.")
    if parsed > current + timedelta(days=MAX_SCHEDULE_DAYS):
        raise UploadPostError(
            f"Upload-Post only schedules up to {MAX_SCHEDULE_DAYS} days ahead."
        )
    return text
