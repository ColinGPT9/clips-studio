"""Calling Clips Kitty's local API from a plugin or a script.

    from clipskitty_sdk.client import LocalAPI

    api = LocalAPI()
    print(api.health())                 # {"ok": True, "app_version": ..., "api_version": 1}
    job_id = api.add_job("https://www.youtube.com/watch?v=...", max_clips=3)["job_id"]

The API listens on 127.0.0.1:8765 while the desktop app runs. These helpers
cover routes labelled stable (docs/developers/api-reference.md); `get`,
`post`, `patch` and `delete` reach any other route, which may change without
notice. Standard library only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8765"
SUPPORTED_API_VERSIONS = (1,)


class APIError(RuntimeError):
    """A call the API refused or could not answer. `status` is the HTTP status (0 when unreachable)."""

    def __init__(self, status: int, detail):
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}" if status else str(detail))


class LocalAPI:
    def __init__(self, base_url: str = DEFAULT_URL, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ---- any route -------------------------------------------------------------

    def request(self, method: str, path: str, body=None, params: dict | None = None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read() or b"null")
                detail = detail.get("detail", detail) if isinstance(detail, dict) else detail
            except ValueError:
                detail = e.reason
            raise APIError(e.code, detail) from e
        except (urllib.error.URLError, OSError) as e:
            raise APIError(0, f"Clips Kitty is not answering at {self.base_url} ({e})") from e
        return json.loads(raw) if raw else None

    def get(self, path: str, **params):
        return self.request("GET", path, params=params)

    def post(self, path: str, body=None):
        return self.request("POST", path, body if body is not None else {})

    def patch(self, path: str, body):
        return self.request("PATCH", path, body)

    def delete(self, path: str):
        return self.request("DELETE", path)

    # ---- stable routes ---------------------------------------------------------

    def health(self) -> dict:
        """GET /health. Raises APIError when the app's API version is one this SDK doesn't know."""
        info = self.get("/health")
        version = (info or {}).get("api_version")
        if version is not None and version not in SUPPORTED_API_VERSIONS:
            raise APIError(0, f"Clips Kitty speaks API {version}; this SDK knows {SUPPORTED_API_VERSIONS}")
        return info

    def add_job(self, url: str, **options) -> dict:
        """POST /jobs: queue a video link with the same options the app's form sends."""
        return self.post("/jobs", {"url": url, **options})

    def jobs(self) -> list:
        return self.get("/jobs")

    def job(self, job_id: int) -> dict:
        return self.get(f"/jobs/{int(job_id)}")

    def queue(self) -> dict:
        return self.get("/queue")

    def videos(self) -> list:
        return self.get("/videos")

    def clips(self, video_id: str) -> list:
        return self.get(f"/videos/{urllib.parse.quote(video_id, safe='')}/clips")
