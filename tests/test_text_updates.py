"""Offline approval contracts. All account identifiers and credentials are generated."""

import json
import secrets

import pytest

from core.state import StateDB
from server import text_updates as texts


@pytest.fixture
def db(tmp_path):
    d = StateDB(tmp_path / 'state.db')
    yield d
    d.close()


def test_message_shape_and_joke_switch():
    message = texts.approval_message(42, 'A viewer clip is ready.', joke=True)
    assert message['content'] == '🐱 Clips Kitty · #42\nA viewer clip is ready.'
    assert message['allowed_mentions'] == {'parse': []}
    buttons = message['components'][0]['components']
    assert [(b['style'], b['custom_id']) for b in buttons] == [(3, 'approve:42'), (4, 'deny:42'), (2, 'dmca:42')]
    assert buttons[2]['label'] == 'Damage Control'
    assert len(texts.approval_message(42, 'Ready', joke=False)['components'][0]['components']) == 2


@pytest.mark.parametrize(('reply', 'intent'), [('approve', 'approve'), ('yes #42', 'approve'), ('post 42', 'approve'),
                                            ('deny', 'deny'), ('no #42', 'deny'), ('skip', 'deny'), ('dmca #42', 'dmca'), ('Damage Control #42', 'dmca')])
def test_strict_reply_without_model(reply, intent):
    assert texts.read_intent(reply, 'Viewer clip', 42) == intent


@pytest.mark.parametrize('reply', ['approve #43', 'post it', 'delete everything', 'yes and skip', 'approve #42 #43'])
def test_strict_reply_cannot_choose_another_item_or_action(reply):
    assert texts.read_intent(reply, 'Viewer clip', 42) == 'unknown'


class Model:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def generate(self, prompt, *, json_mode):
        self.calls += 1
        assert json_mode and 'token' not in prompt
        if isinstance(self.result, Exception):
            raise self.result
        return json.dumps(self.result)


def test_model_reader_is_only_a_checked_suggestion():
    assert texts.read_intent('post it', 'Viewer clip', 42, Model({'intent': 'approve', 'item_id': 42})) == 'approve'
    assert texts.read_intent('nah', 'Viewer clip', 42, Model({'intent': 'deny', 'item_id': 42})) == 'deny'
    assert texts.read_intent('dmca it', 'Viewer clip', 42, Model({'intent': 'dmca', 'item_id': 42})) == 'dmca'
    for value in ({'intent': 'approve', 'item_id': 43}, {'intent': 'settings', 'item_id': 42}):
        assert texts.read_intent('post it', 'Viewer clip', 42, Model(value)) == 'unknown'


def test_line_is_saved_once_and_capped(db):
    line = 'Your Viewer clip has 12,000 views and is ready to post. ' + 'Ready ' * 100
    model = Model({'line': line})
    first = texts.ensure_event(db, 42, 'ask', {'title': 'Viewer clip', 'views': 12000}, now=10, model=model)
    again = texts.ensure_event(db, 42, 'ask', {'title': 'Changed', 'views': 15000}, now=20, model=model)
    assert first['line'] == again['line'] == line[:300] and model.calls == 1


def test_model_failure_uses_plain_line(db):
    row = texts.ensure_event(db, 42, 'ask', {'title': 'Viewer clip', 'views': 12000}, now=10,
                             model=Model(TimeoutError()))
    assert row['line'] == 'A clip of "Viewer clip" reached 12,000 views and is ready to post.'


def test_writer_cannot_introduce_new_names_or_view_counts(db):
    row = texts.ensure_event(db, 42, 'ask', {'title': 'Viewer clip', 'views': 12000}, now=10,
                             model=Model({'line': 'Your clip with InventedGuest reached 99999 views.'}))
    assert row['line'] == 'A clip of "Viewer clip" reached 12,000 views and is ready to post.'


def test_writer_does_not_claim_engagement_on_unpublished_destinations(db):
    row = texts.ensure_event(db, 42, 'ask', {'title': 'Viewer clip', 'views': 12000,
        'platforms': ['youtube', 'tiktok']}, now=10,
        model=Model({'line': 'Your clip saw strong engagement across YouTube and TikTok with 12,000 views.'}))
    assert row['line'] == 'A clip of "Viewer clip" reached 12,000 views and is ready to post.'


@pytest.mark.parametrize('line', ['Clip ready to post. Title: Viewer clip. Views: 12000.',
                                 'Your Viewer clip has 12k views and is ready to post.'])
def test_factual_writer_accepts_sentence_punctuation_and_count_abbreviation(db, line):
    row = texts.ensure_event(db, 42, 'ask', {'title': 'Viewer clip', 'views': 12000}, now=10, model=Model({'line': line}))
    assert row['line'] == line


def test_bot_tokens_are_scrubbed():
    from core.scrub import scrub_secrets

    token = '.'.join((secrets.token_urlsafe(20), secrets.token_urlsafe(5), secrets.token_urlsafe(28)))
    assert token not in scrub_secrets('Bot ' + token)


def test_owner_and_expiry_guard_remote_decisions(db):
    watch = db.insert_watch('twitch_clips', 'channel')
    db.insert_watch_item('tw_clip_pending', watch_id=watch, platform='twitch_clips', publish_state='ask', state='queued')
    item = db.watch_items()[0]
    texts.ensure_event(db, item['id'], 'ask', {'title': 'Viewer clip'}, now=10)
    assert not texts.claim(db, item['id'], 'dmca', 'discord', 11)
    assert not texts.claim(db, item['id'], 'approve', 'discord', 10 + texts.TTL)
    assert texts.claim(db, item['id'], 'deny', 'discord', 11)
    assert not texts.claim(db, item['id'], 'approve', 'app', 12)
    assert db.conn.execute('SELECT COUNT(*) FROM text_update_replies').fetchone()[0] == 1


@pytest.mark.parametrize('intent', ['approve', 'deny'])
def test_bot_interactions_acknowledge_without_model_and_joke_edits_once(db, tmp_path, monkeypatch, intent):
    import time

    from campaigns.discord_webhook import SendResult
    from server.discord_bot import DiscordBot

    uid, channel, message, app_id = [str(secrets.randbelow(10**18) + 10**18) for _ in range(4)]
    monkeypatch.setattr(texts, 'owner', lambda *_: uid)
    runtime = texts.TextUpdates(lambda: StateDB(tmp_path / 'state.db'), tmp_path, clock=lambda: 20)
    texts.store_settings(db, {'enabled': True, 'channel_id': channel})
    watch = db.insert_watch('twitch_clips', 'channel')
    db.insert_watch_item('tw_clip_action', watch_id=watch, platform='twitch_clips', publish_state='ask', state='queued')
    item = db.watch_items()[0]
    texts.ensure_event(db, item['id'], 'ask', {'title': 'Viewer clip'}, now=10)
    db._write("UPDATE text_updates SET message_id=?,channel_id=? WHERE event='ask'", (message, channel))
    calls, timers = [], []

    class HTTP:
        def callback(self, interaction, payload):
            calls.append(('callback', payload))
            return SendResult('ok')

        def original(self, application, token, payload):
            calls.append(('original', payload))
            return SendResult('ok')

    bot = DiscordBot(runtime, http=HTTP(), later=lambda delay, fn: timers.append((delay, fn)))
    interaction = {'id': str(secrets.randbits(62)), 'application_id': app_id, 'token': secrets.token_urlsafe(24),
                   'channel_id': channel, 'user': {'id': uid}, 'message': {'id': message},
                   'data': {'custom_id': f"dmca:{item['id']}"}}
    bot.interaction(interaction)
    assert calls[0][1]['type'] == 4 and 'Ringing' in calls[0][1]['data']['content']
    assert len(timers) == 1 and timers[0][0] == 2
    timers[0][1]()
    assert calls[1][0] == 'original' and 'Delete all VODs and clips from the stream.' in calls[1][1]['content']
    assert db.get_watch_item(item['id'])['publish_state'] == 'ask'
    assert texts.event_row(db, item['id'])['answer'] == ''
    assert db.conn.execute('SELECT COUNT(*) FROM text_update_replies').fetchone()[0] == 0
    interaction['id'] = str(secrets.randbits(62))
    interaction['user']['id'] = str(secrets.randbits(62))
    bot.interaction(interaction)
    assert calls[-1][1]['data']['flags'] == 64 and 'Only the channel owner' in calls[-1][1]['data']['content']
    interaction['id'] = str(secrets.randbits(62))
    interaction['user']['id'] = uid
    interaction['data']['custom_id'] = f"{intent}:{item['id']}"
    runtime.clock = lambda: texts.TTL + 11
    bot.interaction(interaction)
    assert calls[-1][1]['type'] == 7 and 'Expired' in calls[-1][1]['data']['content']
    assert texts.event_row(db, item['id'])['answer'] == ''
    runtime.clock = lambda: 20
    interaction['id'] = str(secrets.randbits(62))
    started = time.perf_counter()
    bot.interaction(interaction)
    assert time.perf_counter() - started < 3
    assert calls[-1][1]['type'] == 7
    assert all(b['disabled'] for b in calls[-1][1]['data']['components'][0]['components'])
    assert texts.event_row(db, item['id'])['answer'] == intent


def test_scheduled_line_uses_actual_time_and_failure_line_does_not_claim_ready(db):
    row = texts.ensure_event(db, 1, 'scheduled', {'title': 'Viewer clip', 'views': 12000,
        'platforms': ['youtube'], 'first_slot': '2026-11-01T14:00:00Z'}, now=10)
    assert 'Scheduled' in row['line'] and '2026-11-01T14:00:00Z' in row['line']
    failed = texts.ensure_event(db, 2, 'run_failed', {'title': 'Viewer clip'}, now=10)
    assert 'failed' in failed['line'].lower() and 'ready to post' not in failed['line']


@pytest.mark.parametrize('resume', [False, True])
def test_gateway_hello_heartbeat_ack_and_resume_without_blocking(db, tmp_path, monkeypatch, resume):
    import asyncio

    from server.discord_bot import DiscordBot, discord_client_id

    runtime = texts.TextUpdates(lambda: StateDB(tmp_path / 'state.db'), tmp_path)
    bot = DiscordBot(runtime)
    bot.enabled = lambda: True
    credential = secrets.token_urlsafe(32)
    bot.token = lambda: credential
    session_id = secrets.token_urlsafe(16)
    if resume:
        bot.session_id, bot.sequence = session_id, 10
    packets = [
        {'op': 10, 'd': {'heartbeat_interval': 1000}},
        {'op': 0, 's': 11, 't': 'READY', 'd': {'session_id': session_id, 'application': {'id': discord_client_id(db)}}},
        {'op': 1, 'd': None}, {'op': 11, 'd': None}, {'op': 7, 'd': None}]

    class Socket:
        def __init__(self):
            self.sent = []

        async def recv(self):
            return json.dumps(packets.pop(0))

        async def send(self, value):
            self.sent.append(json.loads(value))

    socket = Socket()
    with pytest.raises(ConnectionError):
        asyncio.run(bot.connection(socket))
    assert socket.sent[0]['op'] == (6 if resume else 2)
    assert any(packet['op'] == 1 for packet in socket.sent)
    assert bot.sequence == 11 and bot.session_id == session_id


def test_gateway_invalid_session_discards_resume_state(db, tmp_path, monkeypatch):
    import asyncio

    from server import discord_bot

    bot = discord_bot.DiscordBot(texts.TextUpdates(lambda: StateDB(tmp_path / 'state.db'), tmp_path))
    bot.enabled = lambda: True
    bot.session_id, bot.sequence = secrets.token_urlsafe(16), 10
    monkeypatch.setattr(discord_bot.random, 'uniform', lambda *_: 0)

    class Socket:
        async def recv(self):
            return json.dumps({'op': 9, 'd': False})

    with pytest.raises(ConnectionError):
        asyncio.run(bot.connection(Socket()))
    assert bot.session_id == '' and bot.sequence is None


def test_gateway_missing_ack_disconnects(db, tmp_path, monkeypatch):
    import asyncio

    from server import discord_bot

    bot = discord_bot.DiscordBot(texts.TextUpdates(lambda: StateDB(tmp_path / 'state.db'), tmp_path))
    bot.enabled = lambda: True
    credential = secrets.token_urlsafe(32)
    bot.token = lambda: credential
    monkeypatch.setattr(discord_bot.random, 'random', lambda: 0)

    class Socket:
        first, closed = True, False

        async def recv(self):
            if self.first:
                self.first = False
                return json.dumps({'op': 10, 'd': {'heartbeat_interval': 10}})
            await asyncio.sleep(1)

        async def send(self, value):
            pass

        async def close(self, **kwargs):
            self.closed = True

    socket = Socket()
    with pytest.raises(ConnectionError):
        asyncio.run(bot.connection(socket))
    assert socket.closed


def test_ambiguous_bot_send_is_not_retried_and_429_is_deferred():
    import requests

    from server.discord_bot import BotHTTP

    class HTTP:
        calls = 0

        def request(self, *args, **kwargs):
            self.calls += 1
            raise requests.ReadTimeout()

    http = HTTP()
    bot = BotHTTP(lambda: secrets.token_urlsafe(32), http=http)
    assert bot.send(str(secrets.randbits(62)), {'content': 'Fixture'}).kind == 'ambiguous'
    assert http.calls == 1

    class Reply:
        status_code = 429

        def __init__(self):
            self.headers = {}

        def json(self):
            return {'retry_after': 60}

    http.request = lambda *args, **kwargs: Reply()
    assert bot.send(str(secrets.randbits(62)), {'content': 'Fixture'}).kind == 'rate_limited'


def test_typed_replies_use_quote_then_checked_model_and_unknown_asks_once(db, tmp_path, monkeypatch):
    from campaigns.discord_webhook import SendResult
    from server.discord_bot import DiscordBot

    uid, channel, message = [str(secrets.randbits(62)) for _ in range(3)]
    monkeypatch.setattr(texts, 'owner', lambda *_: uid)
    runtime = texts.TextUpdates(lambda: StateDB(tmp_path / 'state.db'), tmp_path, clock=lambda: 20,
                               model_factory=lambda *_: Model({'intent': 'unknown', 'item_id': None}))
    watch = db.insert_watch('twitch_clips', 'channel')
    db.insert_watch_item('tw_clip_typed', watch_id=watch, platform='twitch_clips', state='queued', publish_state='ask')
    item = db.watch_items()[0]
    texts.store_settings(db, {'enabled': True, 'channel_id': channel})
    texts.ensure_event(db, item['id'], 'ask', {'title': 'Viewer clip'}, now=10)
    db._write("UPDATE text_updates SET message_id=? WHERE event='ask'", (message,))

    class HTTP:
        def __init__(self):
            self.sends = []

        def send(self, channel, payload):
            self.sends.append(payload)
            return SendResult('ok', message_id=str(secrets.randbits(62)))

    http = HTTP()
    bot = DiscordBot(runtime, http=http)
    event = {'author': {'id': uid}, 'channel_id': channel, 'content': 'unclear reply',
             'message_reference': {'message_id': message}}
    bot.typed(event)
    bot.typed(event)
    assert len(http.sends) == 1 and 'Reply Approve' in http.sends[0]['content']
    runtime._model = Model({'intent': 'approve', 'item_id': item['id']})
    event['content'] = 'post it'
    bot.typed(event)
    assert texts.event_row(db, item['id'])['answer'] == 'approve'
    assert db.conn.execute('SELECT source FROM text_update_replies').fetchone()[0] == 'typed'


def test_bot_global_rate_limit_does_not_suppress_interaction_ack():
    import time

    from server.discord_bot import BotHTTP

    class Reply:
        status_code = 204
        headers = None

        def json(self):
            return {}

    class HTTP:
        calls = 0

        def request(self, *args, **kwargs):
            self.calls += 1
            return Reply()

    http = HTTP()
    bot = BotHTTP(lambda: secrets.token_urlsafe(32), http=http)
    bot._global_until = time.monotonic() + 60
    result = bot.callback({'id': str(secrets.randbits(62)), 'token': secrets.token_urlsafe(32)}, {'type': 4})
    assert result.kind == 'ok' and http.calls == 1


def test_gateway_privileged_intent_refusal_reconnects_with_buttons_only(db, tmp_path):
    import asyncio
    from contextlib import asynccontextmanager

    from server.discord_bot import DiscordBot

    class Refused(Exception):
        code = 4014

    calls = []

    @asynccontextmanager
    async def connect(*args, **kwargs):
        calls.append('connect')
        if len(calls) == 1:
            raise Refused()
        yield object()

    class HTTP:
        def request(self, *args, **kwargs):
            return {'url': 'wss://gateway.discord.gg', 'session_start_limit': {'remaining': 1}}

    runtime = texts.TextUpdates(lambda: StateDB(tmp_path / 'state.db'), tmp_path)
    bot = DiscordBot(runtime, connect=connect, http=HTTP())
    bot.enabled = lambda: True

    async def connection(socket):
        assert not bot.typed_enabled
        bot.stop()

    bot.connection = connection
    asyncio.run(bot.gateway())
    assert len(calls) == 2 and not bot.typed_enabled
