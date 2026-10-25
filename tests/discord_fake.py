"""Stand-ins for Discord, so no test ever reaches the real one.

- FakeDiscordHTTP: an object with the transport's `.request(method, url, **kw)`,
  answering from a scripted queue and logging every call.
- FakeDiscordServer: a small HTTP server on 127.0.0.1 that speaks enough of
  Discord (authorize redirect, token, revoke, users/@me, guilds, webhooks)
  for the engine to be pointed at it through CLIPS_KITTY_DISCORD_API and
  CLIPS_KITTY_DISCORD_AUTHORIZE. Failures are scripted per path.

Ids and names are made up ("example-server").
"""

import base64
import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

USER = {"id": "200000000000000001", "username": "example-user", "global_name": "Example User"}
GUILDS = [
    {"id": "300000000000000001", "name": "example-server", "owner": True, "permissions": "0"},
    {"id": "300000000000000002", "name": "another-server", "owner": False, "permissions": str(1 << 29)},
    {"id": "300000000000000003", "name": "read-only-server", "owner": False, "permissions": "1024"},
]
WEBHOOK_ID = "400000000000000001"
WEBHOOK_TOKEN = "fake-webhook-token-Abc123_xyz"
CHANNEL_ID = "500000000000000001"


class FakeResponse:
    def __init__(self, status: int = 200, body=None, headers: dict | None = None):
        self.status_code = status
        self._body = body
        self.headers = dict(headers or {})

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body

    @property
    def text(self) -> str:
        return "" if self._body is None else json.dumps(self._body)


class FakeDiscordHTTP:
    """Answers in order: a FakeResponse, a (status, body[, headers]) tuple,
    an exception to raise, or a function of (method, url, **kw)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def add(self, *answers) -> "FakeDiscordHTTP":
        self.answers.extend(answers)
        return self

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        if not self.answers:
            raise AssertionError(f"unexpected Discord call: {method} {url.split('/webhooks/')[0]}")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer(method, url, **kw)
        if isinstance(answer, tuple):
            return FakeResponse(*answer)
        return answer


def tokens(access="access-1", refresh="refresh-1", expires_in=604800, scope="identify guilds", **extra) -> dict:
    return {"access_token": access, "token_type": "Bearer", "expires_in": expires_in, "refresh_token": refresh,
            "scope": scope, **extra}


def webhook(**extra) -> dict:
    """A webhook object as Discord sends it (GET /webhooks/{id}/{token})."""
    return {"id": WEBHOOK_ID, "type": 1, "guild_id": GUILDS[0]["id"], "channel_id": CHANNEL_ID,
            "name": "Example hook", "token": WEBHOOK_TOKEN, **extra}


def challenge_of(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class FakeDiscordServer:
    """`with FakeDiscordServer() as fake:` then fake.api / fake.authorize."""

    def __init__(self):
        self.codes: dict[str, dict] = {}          # code -> the authorize query it was issued for
        self.access: set[str] = set()
        self.refresh_tokens: set[str] = set()
        self.webhooks = {WEBHOOK_ID: WEBHOOK_TOKEN}
        self.messages: list[dict] = []            # posted through a webhook
        self.forms: list[dict] = []               # token / revoke form bodies
        self.revoked: list[str] = []
        self.requests: list[str] = []             # "METHOD path" (no query)
        self.failures: dict[str, list] = {}       # path prefix -> [(status, body) | "hang"]
        self.give_webhook = True
        self.hang_seconds = 1.0
        self._server = None
        self._thread = None

    # ---- addresses ----
    @property
    def root(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def api(self) -> str:
        return self.root + "/api/v10"

    @property
    def authorize(self) -> str:
        return self.root + "/oauth2/authorize"

    def fail(self, prefix: str, *answers) -> None:
        self.failures.setdefault(prefix, []).extend(answers)

    def __enter__(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _send(self, status, body=None, headers=None):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if body is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _scripted(self, path) -> bool:
                for prefix, queue in fake.failures.items():
                    if path.startswith(prefix) and queue:
                        answer = queue.pop(0)
                        if answer == "hang":
                            time.sleep(fake.hang_seconds)
                            return True
                        status, body = answer[0], answer[1]
                        headers = answer[2] if len(answer) > 2 else None
                        self._send(status, body, headers)
                        return True
                return False

            def _form(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode() if length else ""
                return {k: v[0] for k, v in parse_qs(raw).items()}

            def _json(self):
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"{}")

            def _bearer(self) -> bool:
                auth = self.headers.get("Authorization") or ""
                return auth.startswith("Bearer ") and auth[7:] in fake.access

            def do_GET(self):
                parts = urlsplit(self.path)
                fake.requests.append(f"GET {parts.path}")
                if self._scripted(parts.path):
                    return
                if parts.path == "/oauth2/authorize":
                    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                    code = secrets.token_urlsafe(12)
                    fake.codes[code] = query
                    target = query["redirect_uri"] + "?" + urlencode({"code": code, "state": query["state"]})
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if parts.path == "/api/v10/users/@me":
                    return self._send(200, USER) if self._bearer() else self._send(401, {"message": "401: Unauthorized", "code": 0})
                if parts.path == "/api/v10/users/@me/guilds":
                    return self._send(200, GUILDS) if self._bearer() else self._send(401, {"message": "401: Unauthorized", "code": 0})
                if parts.path.startswith("/api/v10/webhooks/"):
                    hook_id, token = parts.path.split("/")[4:6]
                    if fake.webhooks.get(hook_id) != token:
                        return self._send(404, {"message": "Unknown Webhook", "code": 10015})
                    return self._send(200, webhook(id=hook_id, token=token))
                return self._send(404, {"message": "404: Not Found", "code": 0})

            def do_POST(self):
                parts = urlsplit(self.path)
                fake.requests.append(f"POST {parts.path}")
                if self._scripted(parts.path):
                    return
                if parts.path == "/api/v10/oauth2/token":
                    form = self._form()
                    fake.forms.append(form)
                    if form.get("grant_type") == "authorization_code":
                        issued = fake.codes.pop(form.get("code", ""), None)
                        if issued is None or issued["redirect_uri"] != form.get("redirect_uri") \
                                or issued["code_challenge"] != challenge_of(form.get("code_verifier", "")) \
                                or issued["client_id"] != form.get("client_id"):
                            return self._send(400, {"error": "invalid_grant"})
                        answer = tokens(access=secrets.token_urlsafe(16), refresh=secrets.token_urlsafe(16),
                                        scope=issued["scope"])
                        if "webhook.incoming" in issued["scope"].split() and fake.give_webhook:
                            hook_id = "400000000000000009"
                            fake.webhooks[hook_id] = "made-by-oauth-token"
                            answer["webhook"] = webhook(id=hook_id, token="made-by-oauth-token",
                                                        name="Clips Kitty")
                    elif form.get("grant_type") == "refresh_token":
                        if form.get("refresh_token") not in fake.refresh_tokens:
                            return self._send(400, {"error": "invalid_grant"})
                        answer = tokens(access=secrets.token_urlsafe(16), refresh=secrets.token_urlsafe(16))
                    else:
                        return self._send(400, {"error": "unsupported_grant_type"})
                    fake.access.add(answer["access_token"])
                    fake.refresh_tokens.add(answer["refresh_token"])
                    return self._send(200, answer)
                if parts.path == "/api/v10/oauth2/token/revoke":
                    form = self._form()
                    fake.forms.append(form)
                    fake.revoked.append(form.get("token", ""))
                    fake.access.discard(form.get("token", ""))
                    fake.refresh_tokens.discard(form.get("token", ""))
                    return self._send(200, {})
                if parts.path.startswith("/api/v10/webhooks/"):
                    hook_id, token = parts.path.split("/")[4:6]
                    if fake.webhooks.get(hook_id) != token:
                        return self._send(404, {"message": "Unknown Webhook", "code": 10015})
                    body = self._json()
                    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                    message_id = str(600000000000000000 + len(fake.messages) + 1)
                    fake.messages.append({"webhook_id": hook_id, "query": query, "body": body, "id": message_id})
                    return self._send(200, {"id": message_id, "channel_id": query.get("thread_id") or CHANNEL_ID,
                                            "content": body.get("content", "")})
                return self._send(404, {"message": "404: Not Found", "code": 0})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
