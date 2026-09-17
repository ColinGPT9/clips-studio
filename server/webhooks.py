"""Telling another program that a job finished.

The alternative is polling, and an OBS dock or an n8n flow asking "is it done
yet?" every few seconds for the forty minutes a stream takes is forty minutes
of nothing happening. Pass `webhook_url` when submitting a job and the engine
POSTs once, when that job reaches a terminal state:

    {"event": "job.done",            # or job.failed, job.cancelled
     "job_id": 149, "job_type": "process", "status": "done",
     "video_id": "aB3dEfGhIjK", "title": "Friday stream",
     "clips": 38, "error": ""}

With `webhook_secret`, the body is signed so the listener can tell a real
delivery from anything else that found the URL:

    X-Clips-Kitty-Signature: sha256=<hmac-sha256 of the exact body bytes>

Fire and forget, deliberately:

  **One attempt.** Retrying turns a listener that is down into a pile of
  duplicate deliveries later, and the caller can always ask the API instead.

  **A short timeout.** This runs on the worker thread between two jobs, so a
  listener that accepts the connection and then hangs would stall the queue.

  **It never raises.** The job finished either way. A failed delivery is worth
  one line in the log and nothing more.
"""

import hashlib
import hmac
import json
from urllib.parse import urlparse

import requests

TIMEOUT = 10.0
HEADER = "X-Clips-Kitty-Signature"


def is_deliverable(url: str) -> bool:
    """http(s) with a host, and nothing else.

    The caller is the person running the app, pointing it at their own n8n or
    a script on their network, so this is not a trust boundary — but `file://`
    and friends are never a webhook, and silently trying one would be worse
    than saying no at submission time.
    """
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def signature(secret: str, raw: bytes) -> str:
    """The header value for a body. Over the exact bytes sent, not a
    re-serialisation of them: JSON key order or spacing changing between here
    and the wire would make every signature fail to verify."""
    digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def body_for(event: dict) -> dict:
    """The delivery body for a finished job, built from the event the desktop
    app already receives.

    The same facts, named for an outside reader: `event` carries the terminal
    state so a listener can switch on one field, and every key is always
    present, so nobody integrating has to guard each one.
    """
    status = event.get("status") or ""
    return {
        "event": f"job.{status}" if status else "job.finished",
        "job_id": event.get("job_id"),
        "job_type": event.get("job_type", ""),
        "status": status,
        "video_id": event.get("video_id", ""),
        "title": event.get("title", ""),
        "clips": event.get("clips", 0),
        "error": event.get("error", ""),
    }


def deliver(url: str, body: dict, secret: str = "") -> bool:
    """POST `body` once. True when the listener answered under 400.

    Returns rather than raises, because the only caller is a worker that has
    already finished the job it is reporting.
    """
    if not is_deliverable(url):
        return False
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "clips-kitty"}
    if secret:
        headers[HEADER] = signature(secret, raw)
    try:
        response = requests.post(url, data=raw, headers=headers, timeout=TIMEOUT)
    except Exception as e:  # connection refused, DNS, timeout, anything
        print(f"      webhook: could not reach {url}: {e}")
        return False
    if response.status_code >= 400:
        print(f"      webhook: {url} answered {response.status_code}")
        return False
    return True
