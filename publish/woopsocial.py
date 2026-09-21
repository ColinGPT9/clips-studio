"""WoopSocial: a second multi-platform publishing provider.

Alongside Upload-Post rather than instead of it. The two do the same job and
a creator should be able to use whichever they already pay for — or, on
WoopSocial's free tier, whichever costs them nothing.

It reaches the same destinations by a different shape, and the differences
are what this module exists to absorb:

* **Two steps, not one.** The clip is uploaded to a media library first
  (`POST /media`), then a post references it by id. Upload-Post takes the
  file and the metadata in a single multipart request.
* **Accounts, not platform names.** A post names `socialAccountId`s, so the
  connected accounts have to be listed and matched to platforms before
  anything can be sent. Upload-Post takes `platform[]=youtube` directly.
* **One parent post, several children.** The fan-out is a `socialAccounts`
  array inside one request, and each child carries its own per-platform
  fields — YouTube's `title` and `privacy` live there, for instance.

Stdlib only, like the rest of `publish/`.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from publish.errors import (
    AuthRequired,
    NotConnected,
    PublishError,
    QuotaExceeded,
    RateLimited,
)
from publish.multipart import encode as _encode_multipart
from publish.uploadpost import FanOutResult, PlatformOutcome

BASE_URL = "https://api.woopsocial.com/v1"

TIMEOUT = 30
UPLOAD_TIMEOUT = 900

# The single-request upload endpoint is documented up to 100 MB; past that
# their chunked upload-session flow is required. Most clips are well under.
SINGLE_UPLOAD_MAX_BYTES = 100 * 1024 * 1024

# Their platform identifiers are upper case. Mapped to the same lower-case
# names Clips Kitty uses everywhere else, so the UI and the database do not
# have to care which provider delivered a post.
PLATFORMS = {
    "youtube": "YOUTUBE",
    "tiktok": "TIKTOK",
    "instagram": "INSTAGRAM",
    "facebook": "FACEBOOK",
    "x": "X",
    "threads": "THREADS",
    "linkedin": "LINKEDIN",
    "pinterest": "PINTEREST",
}
FROM_PLATFORM = {v: k for k, v in PLATFORMS.items()}

VIDEO_PLATFORMS = tuple(PLATFORMS)


class WoopSocialError(PublishError):
    """A WoopSocial failure that is not one of the shared classes."""


def _classify(status: int, payload: dict, body: str) -> PublishError:
    detail = str(
        payload.get("message") or payload.get("error") or payload.get("detail") or body
    )[:500]

    if status == 401 or status == 403:
        return AuthRequired(
            "WoopSocial did not accept that API key. Check it in Settings.",
            detail=detail,
        )
    if status == 404:
        return NotConnected(
            "WoopSocial could not find that project or account. Reconnect in Settings.",
            detail=detail,
        )
    if status == 429:
        # Their plans meter credits rather than uploads, so a 429 here is
        # rate limiting; running out of credits comes back as a 4xx with a
        # message rather than this.
        return RateLimited(
            "WoopSocial asked us to slow down. Trying again shortly.", detail=detail
        )
    if status == 402 or "credit" in detail.lower():
        return QuotaExceeded(
            "Your WoopSocial credits are used up for this month.", detail=detail
        )
    if status >= 500:
        err = WoopSocialError("WoopSocial had a server error.", detail=detail)
        err.retryable = True
        return err
    return WoopSocialError(f"WoopSocial rejected the request. {detail}", detail=detail)


# WoopSocial's own ids are opaque alphanumeric strings. Anything else is either
# a bug in our caller or an attempt to steer the request somewhere it was not
# meant to go, and the difference does not matter here: reject both.
_ID_SHAPE = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _safe_id(value: str) -> str:
    """An id that is safe to interpolate into a URL path.

    quote() defaults to safe="/", so a slash in an id survives into the path
    and walks the request off its endpoint. Validating the shape is what
    actually closes that; quoting with safe="" is the belt to its braces.
    """
    value = (value or "").strip()
    if not _ID_SHAPE.fullmatch(value):
        raise WoopSocialError("That does not look like a WoopSocial post id.")
    return urllib.parse.quote(value, safe="")


class WoopSocialClient:
    """Everything Clips Kitty needs from the WoopSocial API.

    The key is held here and never returned by any method, the same rule the
    Upload-Post client follows.
    """

    def __init__(self, api_key: str, *, base_url: str = BASE_URL):
        self._key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        files: list[tuple[str, Path]] | None = None,
        timeout: int = TIMEOUT,
    ):
        if not self._key:
            raise NotConnected(
                "No WoopSocial API key is set. Add one in Settings to publish."
            )

        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v not in (None, "")}
            )

        headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        data = None
        if files is not None:
            data, content_type = _encode_multipart([], files)
            headers["Content-Type"] = content_type
        elif json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            # Best effort: the real error is the status code, and a body that
            # will not read or will not parse must never mask it.
            try:
                body = e.read().decode("utf-8", "replace")
            except OSError:
                body = ""
            try:
                parsed = json.loads(body) if body else {}
            except ValueError:
                parsed = {}
            raise _classify(
                int(e.code), parsed if isinstance(parsed, dict) else {}, body
            ) from e
        except urllib.error.URLError as e:
            err = WoopSocialError(
                "Could not reach WoopSocial. Check your internet connection.",
                detail=str(getattr(e, "reason", e))[:200],
            )
            err.retryable = True
            raise err from e
        except TimeoutError as e:
            err = WoopSocialError("WoopSocial did not respond in time.", detail="timeout")
            err.retryable = True
            raise err from e

        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError as e:
            raise WoopSocialError(
                "WoopSocial sent a reply Clips Kitty could not read.", detail=raw[:200]
            ) from e

    # ---- account -----------------------------------------------------------

    def validate_key(self) -> dict:
        """Prove the key works.

        Their health endpoint needs no auth, so it would pass with any key at
        all. Listing projects is the cheapest call that actually checks it.
        """
        got = self._request("GET", "/projects")
        projects = got if isinstance(got, list) else (got.get("data") or [])
        return {"projects": projects}

    def projects(self) -> list[dict]:
        got = self._request("GET", "/projects")
        return got if isinstance(got, list) else (got.get("data") or [])

    def create_project(self, name: str) -> dict:
        return self._request("POST", "/projects", json_body={"name": name})

    def social_accounts(self, project_id: str = "") -> list[dict]:
        got = self._request(
            "GET", "/social-accounts", params={"projectId": project_id} if project_id else None
        )
        return got if isinstance(got, list) else (got.get("data") or [])

    def connected_platforms(self, project_id: str = "") -> list[str]:
        """Lower-case platform names for the accounts that can publish now."""
        found = set()
        for account in self.social_accounts(project_id):
            name = FROM_PLATFORM.get(str(account.get("platform") or "").upper())
            if name:
                found.add(name)
        return sorted(found)

    def connect_url(self, project_id: str, platform: str) -> str:
        """A browser page for linking one social account to a project."""
        got = self._request(
            "POST",
            "/social-accounts/authorization-url",
            json_body={
                "projectId": project_id,
                "platform": PLATFORMS.get(platform, platform.upper()),
            },
        )
        return str(got.get("url") or got.get("authorizationUrl") or "")

    # ---- publishing --------------------------------------------------------

    def upload_media(self, video: Path, project_id: str) -> str:
        """Put the clip in the media library and return its id.

        Step one of two: a post references media rather than carrying it.
        """
        size = video.stat().st_size
        if size > SINGLE_UPLOAD_MAX_BYTES:
            raise WoopSocialError(
                f"That clip is {size // (1024 * 1024)} MB. WoopSocial takes up to "
                "100 MB in one piece — shorten the clip or lower the quality."
            )
        got = self._request(
            "POST",
            "/media",
            params={"projectId": project_id},
            files=[("file", video)],
            timeout=UPLOAD_TIMEOUT,
        )
        media_id = str(got.get("id") or got.get("mediaId") or "")
        if not media_id:
            raise WoopSocialError("WoopSocial accepted the clip but returned no media id.")
        return media_id

    def create_post(self, body: dict) -> dict:
        return self._request("POST", "/posts", json_body=body, timeout=UPLOAD_TIMEOUT)

    def get_post(self, post_id: str) -> dict:
        return self._request("GET", f"/posts/{_safe_id(post_id)}")


def build_post(
    *,
    media_id: str,
    text: str,
    accounts: list[dict],
    title: str = "",
    scheduled_for: str = "",
    overrides: dict[str, dict] | None = None,
) -> dict:
    """The create-post body: one parent, one child per destination.

    Per-platform fields live on the child rather than in a flat namespace, so
    a YouTube title and an Instagram caption genuinely cannot collide — they
    are properties of different objects.
    """
    children: list[dict] = []
    for account in accounts:
        name = FROM_PLATFORM.get(str(account.get("platform") or "").upper())
        if not name:
            continue
        child: dict = {
            "platform": account["platform"],
            "socialAccountId": account["id"],
        }
        mine = (overrides or {}).get(name) or {}
        # YouTube is the one that always needs a title of its own; a video
        # with no title is rejected rather than defaulted.
        if name == "youtube":
            child["title"] = (mine.get("title") or title or "")[:100]
            child["privacy"] = mine.get("privacy") or "public"
        elif mine.get("title"):
            child["title"] = mine["title"]
        children.append(child)

    return {
        "content": [{"text": text, "media": [{"type": "MEDIA_LIBRARY", "mediaId": media_id}]}],
        "schedule": (
            {"type": "SCHEDULE_FOR_LATER", "scheduledFor": scheduled_for}
            if scheduled_for
            else {"type": "PUBLISH_NOW"}
        ),
        "socialAccounts": children,
    }


def parse_post(payload: dict) -> FanOutResult:
    """Normalise a post response into the same per-platform shape as Upload-Post.

    Both providers report through `FanOutResult`, so everything above this —
    the database rows, the UI, the status polling — stays provider-agnostic.
    """
    result = FanOutResult(request_id=str(payload.get("id") or payload.get("postId") or ""))
    children = (
        payload.get("socialAccountPosts")
        or payload.get("socialAccounts")
        or payload.get("children")
        or []
    )
    for child in children if isinstance(children, list) else []:
        if not isinstance(child, dict):
            continue
        name = FROM_PLATFORM.get(str(child.get("platform") or "").upper())
        if not name:
            continue
        status = str(child.get("status") or "").upper()
        if status in ("PUBLISHED", "SUCCESS", "COMPLETED"):
            state = "published"
        elif status in ("FAILED", "ERROR"):
            state = "failed"
        elif status in ("SCHEDULED", "PENDING", "DRAFT", "QUEUED"):
            state = "queued"
        else:
            state = "processing"
        result.outcomes.append(
            PlatformOutcome(
                platform=name,
                state=state,
                post_id=str(child.get("externalPostId") or child.get("id") or ""),
                post_url=str(child.get("permalink") or child.get("url") or ""),
                error=str(child.get("error") or child.get("failureReason") or "")[:300]
                if state == "failed"
                else "",
            )
        )
    result.outcomes.sort(key=lambda o: o.platform)
    return result


class WoopSocialPublisher:
    """Publishes one clip to several platforms through WoopSocial."""

    name = "woopsocial"

    def __init__(self, client: WoopSocialClient, project_id: str):
        self.client = client
        self.project_id = project_id

    def start(
        self,
        video: Path,
        *,
        platforms: list[str],
        title: str,
        text: str,
        scheduled_for: str = "",
        overrides: dict[str, dict] | None = None,
    ) -> FanOutResult:
        if not self.project_id:
            raise NotConnected(
                "No WoopSocial project is set up yet. Open Manage Social Accounts "
                "in Settings."
            )
        wanted = {p for p in platforms if p in PLATFORMS}
        if not wanted:
            raise WoopSocialError("Pick at least one platform WoopSocial can post to.")

        accounts = [
            a
            for a in self.client.social_accounts(self.project_id)
            if FROM_PLATFORM.get(str(a.get("platform") or "").upper()) in wanted
        ]
        if not accounts:
            raise NotConnected(
                "None of those platforms are connected to WoopSocial yet. "
                "Open Manage Social Accounts to connect them."
            )

        media_id = self.client.upload_media(video, self.project_id)
        got = self.client.create_post(
            build_post(
                media_id=media_id,
                text=text,
                accounts=accounts,
                title=title,
                scheduled_for=scheduled_for,
                overrides=overrides,
            )
        )
        result = parse_post(got)
        if not result.outcomes:
            linked = {
                FROM_PLATFORM[str(a["platform"]).upper()] for a in accounts
            }
            result.outcomes = [PlatformOutcome(platform=p) for p in sorted(linked)]
        # A platform the user asked for with no connected account is skipped,
        # not failed — the same distinction Upload-Post makes.
        for missing in sorted(wanted - {o.platform for o in result.outcomes}):
            result.outcomes.append(
                PlatformOutcome(
                    platform=missing,
                    state="skipped",
                    error="Not connected to WoopSocial yet.",
                )
            )
        result.outcomes.sort(key=lambda o: o.platform)
        return result

    def check(self, post_id: str) -> FanOutResult:
        result = parse_post(self.client.get_post(post_id))
        if not result.request_id:
            result.request_id = post_id
        return result
