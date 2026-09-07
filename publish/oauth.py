"""The browser half of connecting a YouTube account.

`InstalledAppFlow.run_local_server()` starts a web server, opens a browser, and
blocks until the user finishes consenting — which could be never. That cannot
happen inside an HTTP handler, so it runs on its own thread and the UI polls
for the outcome.

Loopback is the only redirect Google still accepts for desktop apps: the
out-of-band copy-paste flow and custom URI schemes are both retired. The client
secret in a desktop app is not really a secret — Google says as much, since the
binary is on the user's machine — but it is still theirs, so it lives in the
encrypted store rather than in a config file.
"""

import threading

from publish.errors import PublishError

# Long enough for someone to find the right Google account and read the
# unverified-app warning; short enough that an abandoned consent does not leak
# a thread and a listening socket for the life of the process.
CONSENT_TIMEOUT_SECONDS = 300


def client_config(client_id: str, client_secret: str) -> dict:
    """The shape google-auth-oauthlib expects, built from two pasted strings.

    Taking the values rather than the downloaded JSON file means there is no
    path to resolve, nothing to copy into the data directory, and no file for
    the CI secret-scan to trip over.
    """
    return {
        "installed": {
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }


def looks_like_desktop_client(config: dict) -> bool:
    return isinstance(config, dict) and "installed" in config and bool(
        config["installed"].get("client_id")
    )


class ConnectFlow(threading.Thread):
    """One consent attempt. Poll `state` until it leaves 'waiting'."""

    def __init__(self, config: dict, scopes: list[str]):
        super().__init__(daemon=True, name="youtube-oauth")
        self._config = config
        self._scopes = list(scopes)
        self.state = "waiting"  # waiting | done | error
        self.error = ""
        self.credentials = None

    def run(self) -> None:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow

            try:
                flow = InstalledAppFlow.from_client_config(
                    self._config, self._scopes, autogenerate_code_verifier=True
                )
            except TypeError:
                # Older google-auth-oauthlib has no PKCE argument.
                flow = InstalledAppFlow.from_client_config(self._config, self._scopes)

            self.credentials = flow.run_local_server(
                host="127.0.0.1",
                port=0,
                open_browser=True,
                timeout_seconds=CONSENT_TIMEOUT_SECONDS,
                # offline + consent is what actually returns a refresh token.
                # Without them a reconnect can come back with an access token
                # only, and the connection silently dies an hour later.
                access_type="offline",
                prompt="consent",
                # Incremental authorisation: asking for the playlist scope later
                # must ADD to what was granted, not replace it. Without this,
                # turning playlists on would quietly drop the upload permission.
                include_granted_scopes="true",
                success_message=(
                    "Clips Kitty is connected. You can close this tab and go back to the app."
                ),
            )
            self.state = "done"
        except Exception as e:
            self.state = "error"
            self.error = _explain(e)


def _explain(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "access_denied" in lowered:
        return "You declined the permission request, so nothing was connected."
    if "timed out" in lowered or "timeout" in lowered:
        return "The sign-in window timed out. Press Connect and try again."
    if "invalid_client" in lowered:
        return (
            "Google did not recognise that client ID and secret. Check you copied "
            "both from the same OAuth client, and that its type is Desktop app."
        )
    if "redirect_uri_mismatch" in lowered:
        return (
            "Google rejected the redirect. The OAuth client must be of type "
            "Desktop app — a Web application client will not work here."
        )
    if "address already in use" in lowered:
        return "Could not open a local port for the sign-in. Try again."
    return f"Connecting failed: {text[:200]}"


def revoke(token: str) -> bool:
    """Best-effort revoke at Google's endpoint.

    Failure is not worth surfacing: the local token is deleted either way, and
    the user has disconnected as far as this app is concerned.
    """
    if not token:
        return False
    try:
        import requests

        response = requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        return response.status_code == 200
    except Exception:
        return False


def scopes_for(playlists: bool) -> list[str]:
    from publish.youtube_shorts import CONNECT_SCOPES, PLAYLIST_SCOPES

    return list(PLAYLIST_SCOPES if playlists else CONNECT_SCOPES)


def require_client(config: dict | None) -> dict:
    if not looks_like_desktop_client(config or {}):
        raise PublishError(
            "No Google API client is set up yet. Add yours in Settings — "
            "Publish to YouTube."
        )
    return config
