"""Internal approval settings require the existing desktop session on writes."""

import secrets

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('httpx')

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.state import StateDB
from server import text_updates, text_updates_api


def test_all_settings_writes_are_guarded_and_default_is_off(tmp_path):
    def db():
        return StateDB(tmp_path / 'state.db')
    runtime = text_updates.TextUpdates(db, tmp_path)
    secret = secrets.token_urlsafe(24)
    app = FastAPI()
    text_updates_api.install(app, runtime=runtime, session_secret=secret, broadcaster=None)
    client = TestClient(app)
    assert client.get('/text-updates/status').json()['enabled'] is False
    assert client.get('/text-updates/pending').json() == []
    for method, path, payload in [('PATCH', '/settings', {'enabled': True}),
                                  ('PUT', '/discord-bot', {'token': secrets.token_urlsafe(32)}),
                                  ('POST', '/test', {}), ('DELETE', '/credentials', None)]:
        assert client.request(method, '/text-updates' + path, json=payload).status_code == 403
    headers = {'X-Clips-Kitty-Session': secret}
    assert client.patch('/text-updates/settings', json={'enabled': True}, headers=headers).status_code == 200
    assert client.patch('/text-updates/settings', json={'away_from_desk': True}, headers=headers).status_code == 400
    assert client.get('/text-updates/status').json()['away_from_desk'] is False
    invalid = secrets.token_urlsafe(32) + '/invalid'
    result = client.put('/text-updates/discord-bot', json={'token': invalid}, headers=headers)
    assert result.status_code == 400 and invalid not in result.text


def test_successful_test_is_required_for_away_and_tokens_never_return(tmp_path, monkeypatch):
    from campaigns.discord_webhook import SendResult

    uid, channel = [str(secrets.randbits(62)) for _ in range(2)]
    monkeypatch.setattr(text_updates, 'owner', lambda *_: uid)

    def db():
        return StateDB(tmp_path / 'state.db')

    class HTTP:
        calls = 0

        def request(self, *args, **kwargs):
            self.calls += 1
            return []

    class Bot:
        connected, typed_enabled = True, True

        def __init__(self):
            self.http = HTTP()

        def send(self, channel, payload):
            return SendResult('ok', message_id=str(secrets.randbits(62)))

    bot = Bot()
    runtime = text_updates.TextUpdates(db, tmp_path, bot=bot)
    secret, token = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    app = FastAPI()
    text_updates_api.install(app, runtime=runtime, session_secret=secret, broadcaster=None)
    client = TestClient(app)
    headers = {'X-Clips-Kitty-Session': secret}
    assert client.get('/text-updates/channels').json() == []
    assert bot.http.calls == 0
    response = client.put('/text-updates/discord-bot', json={'token': token}, headers=headers)
    assert response.status_code == 200 and token not in response.text
    client.patch('/text-updates/settings', json={'enabled': True, 'channel_id': channel}, headers=headers)
    assert client.patch('/text-updates/settings', json={'away_from_desk': True}, headers=headers).status_code == 400
    assert client.post('/text-updates/test', headers=headers).json()['ok']
    assert client.patch('/text-updates/settings', json={'away_from_desk': True}, headers=headers).json()['away_from_desk']
    assert client.delete('/text-updates/credentials', headers=headers).json()['has_token'] is False
