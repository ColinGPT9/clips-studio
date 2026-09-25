"""The one door every cloud AI request goes through.

Building the headers here, and only here, is what makes two promises
checkable: the user's key travels in a header (never a URL, which would put it
in error messages), and a provider's own headers (OpenRouter's attribution)
are on every request it gets, retries included.

Failures come back as LLMError with a message a creator can act on. A busy or
rate-limited provider is retried a few times, honouring Retry-After; nothing
is ever retried against a different provider.
"""

import time

import requests

from core.scrub import scrub_secrets
from llm.providers.base import LLMError, ProviderSpec

TIMEOUT = 120
ATTEMPTS = 3
MAX_WAIT = 60
RETRYABLE = {408, 429, 500, 502, 503, 504, 529}

# Replaced in tests, so no test ever touches the network or waits.
transport = requests.request
sleep = time.sleep

_NO_CREDIT_CODES = {
    "insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded", "organization_usage_limit_exceeded",
    "payment_required", "billing_error", "insufficient_credits",
}


def auth_headers(spec: ProviderSpec, key: str) -> dict:
    if spec.auth == "x-api-key":
        return {"x-api-key": key}
    if spec.auth == "x-goog-api-key":
        return {"x-goog-api-key": key}
    return {"Authorization": f"Bearer {key}"}


def send(
    spec: ProviderSpec,
    key: str,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    files: dict | None = None,
    data: dict | None = None,
    timeout: float = TIMEOUT,
) -> dict:
    """Make one request (with bounded retries) and return the JSON body."""
    url = spec.base_url.rstrip("/") + path
    headers = {**auth_headers(spec, key), **spec.extra_headers()}
    error: LLMError | None = None
    for attempt in range(1, ATTEMPTS + 1):
        wait = 2.0 * attempt
        try:
            response = transport(
                method, url, headers=headers, json=json_body, params=params,
                files=files, data=data, timeout=timeout,
            )
        except requests.Timeout:
            error = LLMError("provider_down", f"{spec.label} took too long to answer. Try again in a few minutes.")
        except requests.RequestException:
            # The exception text is not used: it names the URL, and keeping
            # every provider message ours is simpler than auditing theirs.
            error = LLMError("network", f"Couldn't reach {spec.label}. Check your internet connection.")
        else:
            body = _body(response)
            status = response.status_code
            if status < 300 and not _error_in(body):
                return body if isinstance(body, dict) else {"data": body}
            if status < 300:
                # OpenRouter can answer 200 with only an error object once a
                # request is under way. That is a failure, not an empty reply.
                status = _int(_error_in(body).get("code")) or 502
            error = error_from(spec, status, body)
            wait = _retry_after(response) or wait
            if status not in RETRYABLE or error.kind == "no_credits":
                raise error
        if attempt < ATTEMPTS:
            sleep(min(wait, MAX_WAIT))
    raise error or LLMError("network", f"Couldn't reach {spec.label}. Check your internet connection.")


def error_from(spec: ProviderSpec, status: int, body) -> LLMError:
    """A provider's error, in the app's words, with their detail scrubbed."""
    err = _error_in(body) or (body if isinstance(body, dict) else {})
    code = str(err.get("code") or err.get("type") or err.get("status") or "").lower()
    detail = scrub_secrets(str(err.get("message") or "")).strip()[:240]
    label = spec.label
    if code in _NO_CREDIT_CODES or status == 402:
        return LLMError("no_credits", f"Your {label} account is out of credit or over its spending limit. "
                                      f"Top it up on {label}, then try again.")
    if status == 401 or "authentication" in code or code == "invalid_api_key":
        return LLMError("invalid_key", f"{label} didn't accept the API key. "
                                       "Check it, or paste a new one in Settings → AI.")
    if status == 429:
        return LLMError("rate_limited", f"{label} is rate limiting your key. Wait a minute and try again.")
    if status == 404:
        return LLMError("model_unavailable", f"{label} doesn't offer that model to your key. "
                                             "Pick another one in Settings → AI." + _said(detail))
    if status in (408, 504) or status >= 500:
        return LLMError("provider_down", f"{label} isn't answering right now. Try again in a few minutes.")
    return LLMError("rejected", f"{label} refused the request." + _said(detail))


def _said(detail: str) -> str:
    return f" It said: {detail}" if detail else ""


def _error_in(body) -> dict:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return err
        if isinstance(err, str) and err:
            return {"message": err}
    return {}


def _body(response):
    try:
        return response.json()
    except ValueError:
        return {"error": {"message": (response.text or "")[:240]}} if response.status_code >= 300 else {}


def _retry_after(response) -> float:
    try:
        return float(response.headers.get("Retry-After") or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
