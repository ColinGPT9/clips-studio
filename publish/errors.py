"""What went wrong, in terms a creator can act on.

The Google client raises HttpError for everything from "your daily upload
allowance is gone" to "YouTube limited your channel" to "your sign-in expired",
and the difference matters enormously: one is worth retrying in ten seconds,
one is worth retrying tomorrow, and one needs the user to click Reconnect.
Guessing wrong means either a retry loop that can double-post a video, or a
dead end that looks like a crash.

classify() turns an API failure into one of these, and every one of them
carries a sentence meant to be shown to the user as-is.
"""


class PublishError(Exception):
    """Base class. `message` is safe to show; it never contains a token."""

    retryable = False

    def __init__(self, message: str, *, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


class NotConnected(PublishError):
    """No credentials at all — the user has never connected an account."""


class AuthRequired(PublishError):
    """Had credentials, they no longer work. Reconnect."""


class QuotaExceeded(PublishError):
    """The API project's daily allowance is gone until midnight Pacific."""

    def __init__(self, message: str, *, detail: str = "", resets_at: str = ""):
        super().__init__(message, detail=detail)
        self.resets_at = resets_at


class RateLimited(PublishError):
    """Too fast. Back off and try again shortly."""

    retryable = True


class UploadLimitExceeded(PublishError):
    """A limit on the CHANNEL, not on the API project.

    Worth its own class because switching API keys or waiting for the quota
    reset does nothing for it, which is the first thing people try.
    """


class PublishCancelled(PublishError):
    """The user asked to stop."""


class VideoRejected(PublishError):
    """YouTube accepted the bytes and then refused the video."""


_AUTH_REASONS = {
    "authError",
    "unauthorized",
    "youtubeSignupRequired",
    "forbidden",
}

_MESSAGES = {
    "quotaExceeded": (
        "Your API key's daily upload allowance is used up. It resets at midnight "
        "Pacific time."
    ),
    "dailyLimitExceeded": (
        "Your API key's daily allowance is used up. It resets at midnight Pacific time."
    ),
    "rateLimitExceeded": "YouTube asked us to slow down. Retrying shortly.",
    "userRateLimitExceeded": "YouTube asked us to slow down. Retrying shortly.",
    "uploadLimitExceeded": (
        "YouTube has limited uploads on your channel — this is a limit on the "
        "channel itself, not on Clips Kitty or your API key, so a different key "
        "will not help. It usually clears within a day."
    ),
    "youtubeSignupRequired": (
        "That Google account has no YouTube channel. Create one, then reconnect."
    ),
    "invalidVideoMetadata": "YouTube rejected the title, description or tags.",
    "mediaBodyRequired": "The clip file was empty or unreadable.",
    "failedPrecondition": "The clip file was not a video YouTube could read.",
    "invalidPublishAt": (
        "YouTube would not accept that scheduled time. Pick a time at least a "
        "few minutes from now."
    ),
    "invalidFilename": "YouTube rejected the file name.",
}


def classify(status: int, reason: str = "", message: str = "") -> PublishError:
    """Map an API failure onto one of the classes above.

    `reason` is the machine-readable string from the error payload
    (`error.errors[0].reason`); `message` is whatever text came with it and is
    only used as a fallback, never shown raw without redaction upstream.
    """
    text = _MESSAGES.get(reason, "")

    if reason in ("quotaExceeded", "dailyLimitExceeded"):
        return QuotaExceeded(text, detail=message)
    if reason in ("rateLimitExceeded", "userRateLimitExceeded"):
        return RateLimited(text, detail=message)
    if reason == "uploadLimitExceeded":
        return UploadLimitExceeded(text, detail=message)

    if status == 401 or reason in _AUTH_REASONS:
        return AuthRequired(
            text or "Your YouTube connection has expired. Reconnect it in Settings.",
            detail=message,
        )
    if status == 429:
        return RateLimited(_MESSAGES["rateLimitExceeded"], detail=message)
    if status == 403:
        # 403 with no recognised reason is usually permission, not quota.
        return AuthRequired(
            text or "YouTube refused the request. Reconnect your account in Settings.",
            detail=message,
        )
    if status == 404:
        return PublishError(
            "The upload session expired before it finished. Nothing was published — "
            "check your channel, then try again.",
            detail=message,
        )
    if status >= 500:
        err = PublishError("YouTube had a server error.", detail=message)
        err.retryable = True
        return err

    return PublishError(text or "YouTube rejected the upload.", detail=message)


def from_http_error(exc: Exception) -> PublishError:
    """Classify a googleapiclient HttpError without importing googleapiclient.

    Duck-typed on purpose: this module must stay importable on a CI runner that
    has no Google libraries installed.
    """
    status = getattr(getattr(exc, "resp", None), "status", 0) or 0
    reason = ""
    message = ""
    try:
        import json

        content = getattr(exc, "content", b"") or b"{}"
        if isinstance(content, bytes):
            content = content.decode("utf-8", "replace")
        payload = json.loads(content).get("error", {})
        message = payload.get("message", "") or ""
        errors = payload.get("errors") or []
        if errors:
            reason = errors[0].get("reason", "") or ""
    except Exception:
        # A non-JSON body tells us nothing extra; the status code still does.
        pass
    return classify(int(status), reason, message)
