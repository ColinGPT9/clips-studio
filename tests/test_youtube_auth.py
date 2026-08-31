from pathlib import Path

from publish.youtube_shorts import (
    ANALYTICS_SCOPE,
    AUTH_SCOPES,
    UPLOAD_SCOPES,
    YouTubeShortsPublisher,
)


class FakeCredentials:
    def __init__(self, scopes):
        self.scopes = list(scopes)
        self.expired = False
        self.refresh_token = "refresh"
        self.valid = True
        self.refreshed = False

    def has_scopes(self, scopes):
        return set(scopes).issubset(self.scopes)

    def refresh(self, request):
        self.refreshed = True

    def to_json(self):
        return '{"token":"fake"}'


def test_noninteractive_upload_accepts_existing_upload_only_token(
    monkeypatch,
    tmp_path,
):
    from google.oauth2 import credentials as credentials_module

    token = tmp_path / "youtube_token.json"
    token.write_text("{}", encoding="utf-8")
    fake = FakeCredentials(UPLOAD_SCOPES)

    monkeypatch.setattr(
        credentials_module.Credentials,
        "from_authorized_user_file",
        staticmethod(lambda path: fake),
    )

    publisher = YouTubeShortsPublisher(
        client_secret=tmp_path / "client_secret.json",
        token_path=token,
    )

    assert publisher.authenticate(interactive=False) is fake


def test_interactive_auth_upgrades_upload_only_token_to_analytics(
    monkeypatch,
    tmp_path,
):
    from google.oauth2 import credentials as credentials_module
    from google_auth_oauthlib import flow as flow_module

    token = tmp_path / "youtube_token.json"
    token.write_text("{}", encoding="utf-8")
    client_secret = tmp_path / "client_secret.json"
    client_secret.write_text("{}", encoding="utf-8")

    old = FakeCredentials(UPLOAD_SCOPES)
    upgraded = FakeCredentials(AUTH_SCOPES)
    seen = {}

    monkeypatch.setattr(
        credentials_module.Credentials,
        "from_authorized_user_file",
        staticmethod(lambda path: old),
    )

    class FakeFlow:
        def run_local_server(self, port=0):
            seen["port"] = port
            return upgraded

    def fake_from_client_secrets_file(path, scopes):
        seen["path"] = Path(path)
        seen["scopes"] = list(scopes)
        return FakeFlow()

    monkeypatch.setattr(
        flow_module.InstalledAppFlow,
        "from_client_secrets_file",
        staticmethod(fake_from_client_secrets_file),
    )

    publisher = YouTubeShortsPublisher(
        client_secret=client_secret,
        token_path=token,
    )

    result = publisher.authenticate(interactive=True)

    assert result is upgraded
    assert ANALYTICS_SCOPE in seen["scopes"]
    assert set(seen["scopes"]) == set(AUTH_SCOPES)
    assert seen["path"] == client_secret
    assert seen["port"] == 0
    assert token.read_text(encoding="utf-8") == '{"token":"fake"}'
