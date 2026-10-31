"""Optional Discord Gateway client. No server URL, web framework or AI on click ACKs."""

import asyncio
import json
import random
import re
import threading
import time
from urllib.parse import urlparse

from campaigns import discord_http, discord_webhook
from campaigns.discord_webhook import SendResult
from core import secrets
from server import text_updates as texts

BASE_INTENTS = (1 << 0) | (1 << 9) | (1 << 12)
MESSAGE_CONTENT = 1 << 15


def later(delay, fn):
    task = threading.Timer(delay, fn)
    task.daemon = True
    task.start()


def snowflake(value):
    value = str(value or '')
    if not re.fullmatch(r'\d{1,20}', value):
        raise ValueError('Invalid Discord identifier')
    return value


def gateway_url(value):
    parsed = urlparse(value)
    if parsed.scheme != 'wss' or not (parsed.hostname == 'gateway.discord.gg' or str(parsed.hostname).endswith('.discord.gg')) \
            or parsed.username or parsed.password or parsed.port:
        raise ValueError('Invalid Discord Gateway address')
    return value.split('?')[0].rstrip('/') + '/?v=10&encoding=json'


class BotHTTP:
    def __init__(self, token, http=None):
        self.token = token
        self.http = http or discord_http.Http()
        self._limits = {}
        self._global_until = 0
        self._callback_until = 0
        self._lock = threading.Lock()

    def request(self, method, path, payload=None, *, callback=False, raw=False):
        now = time.monotonic()
        bucket = '/'.join(path.split('/')[:3])
        with self._lock:
            until = max(self._callback_until if callback else self._global_until, self._limits.get(bucket, 0))
        if until > now:
            return SendResult('rate_limited', retry_after=until - now)
        try:
            response = self.http.request(method, discord_http.api_base() + path,
                headers={} if callback else {'Authorization': 'Bot ' + self.token()},
                json=payload, timeout=1 if callback else 10, allow_redirects=False)
        except Exception as error:
            return discord_webhook.failed(error)
        result = discord_webhook.classify(response, self.token())
        headers = getattr(response, 'headers', {}) or {}
        if result.kind == 'rate_limited' or headers.get('X-RateLimit-Remaining') == '0':
            try:
                pause = result.retry_after or float(headers.get('X-RateLimit-Reset-After') or 1)
            except (TypeError, ValueError):
                pause = 1
            with self._lock:
                self._limits[bucket] = now + max(1, pause)
                if result.is_global:
                    if callback:
                        self._callback_until = now + max(1, pause)
                    else:
                        self._global_until = now + max(1, pause)
        if raw and result.kind == 'ok':
            return discord_http.body(response)
        return result

    def send(self, channel, payload):
        return self.request('POST', f'/channels/{snowflake(channel)}/messages', payload)

    def edit(self, channel, message, payload):
        return self.request('PATCH', f'/channels/{snowflake(channel)}/messages/{snowflake(message)}', payload)

    def callback(self, interaction, payload):
        token = str(interaction.get('token') or '')
        if not re.fullmatch(r'[\w.-]{1,256}', token):
            return SendResult('bad_request')
        return self.request('POST', f"/interactions/{snowflake(interaction['id'])}/{token}/callback", payload, callback=True)

    def original(self, application, token, payload):
        if not re.fullmatch(r'[\w.-]{1,256}', token):
            return SendResult('bad_request')
        return self.request('PATCH', f'/webhooks/{snowflake(application)}/{token}/messages/@original', payload, callback=True)


class DiscordBot(threading.Thread):
    def __init__(self, runtime, *, http=None, later=later, connect=None):
        super().__init__(daemon=True, name='clip-updates-discord')
        self.runtime, self.later, self.connect = runtime, later, connect
        self.http = http or BotHTTP(self.token)
        self.connected = False
        self.typed_enabled = True
        self.session_id, self.resume_url, self.sequence = '', '', None
        self._halt = threading.Event()
        self._seen = set()
        self._typed_lock = threading.Lock()
        self._fatal_token = ''
        self._gateway_token = None

    def token(self):
        return str((secrets.load(self.runtime.data_dir, texts.BOT_SECRET) or {}).get('token') or '')

    def stop(self):
        self._halt.set()

    def error(self, message):
        d = self.runtime.db()
        try:
            texts.store_settings(d, {'last_error': message})
        finally:
            d.close()
        self.runtime.changed()

    def send(self, channel, payload):
        return self.http.send(channel, payload)

    def edit(self, channel, message, payload):
        return self.http.edit(channel, message, payload)

    def interaction(self, event):
        identity = str(event.get('id') or '')
        if identity in self._seen:
            return
        self._seen.add(identity)
        if len(self._seen) > 2000:
            self._seen = {identity}
        d = self.runtime.db()
        try:
            value = texts.settings(d)
            if not value['enabled']:
                return
            user = event.get('user') or (event.get('member') or {}).get('user') or {}
            if not texts.owner(d, self.runtime.data_dir) or user.get('id') != texts.owner(d, self.runtime.data_dir):
                self.http.callback(event, {'type': 4, 'data': {'content': 'Only the channel owner can do that.', 'flags': 64,
                                                            'allowed_mentions': {'parse': []}}})
                return
            found = re.fullmatch(r'(approve|deny|dmca):(\d{1,12})', str((event.get('data') or {}).get('custom_id') or ''))
            if not found:
                self.http.callback(event, {'type': 4, 'data': {'content': 'Open Clips Kitty to decide.', 'flags': 64}})
                return
            intent, item_id = found[1], int(found[2])
            request = texts.event_row(d, item_id)
            if not value['enabled'] or str(event.get('channel_id')) != value['channel_id'] or not request \
                    or str((event.get('message') or {}).get('id')) != request['message_id']:
                self.http.callback(event, {'type': 4, 'data': {'content': 'This request is not active. Open Clips Kitty.', 'flags': 64}})
                return
            if not texts.waiting(d, item_id, self.runtime.clock()):
                self.http.callback(event, {'type': 7, 'data': texts.approval_message(item_id, request['line'],
                    joke=value['show_dmca'], content=self.runtime.real_state(d, item_id), disabled=True)})
                return
            if intent == 'dmca':
                if not value['show_dmca']:
                    self.http.callback(event, {'type': 4, 'data': {'content': 'The joke button is switched off.', 'flags': 64}})
                    return
                result = self.http.callback(event, {'type': 4, 'data': {'content': texts.RINGING, 'flags': 64,
                                                                     'allowed_mentions': {'parse': []}}})
                if result.kind == 'ok':
                    self.later(2, lambda: self.http.original(event['application_id'], event['token'],
                        {'content': texts.lawyer(item_id), 'allowed_mentions': {'parse': []}}))
                return
            claimed = texts.claim(d, item_id, intent, 'discord', self.runtime.clock())
            content = (f'✅ Approved. Posting #{item_id}…' if intent == 'approve' else
                       f'⛔ Denied. Skipped #{item_id}. The clips stay in your library.') if claimed else self.runtime.real_state(d, item_id)
            self.http.callback(event, {'type': 7, 'data': texts.approval_message(item_id, request['line'],
                joke=value['show_dmca'], content=content, disabled=True)})
            self.runtime.wake()
        except Exception:
            # Neither exception text nor request content belongs in a bug report.
            self.http.callback(event, {'type': 4, 'data': {'content': 'Open Clips Kitty to check this request.', 'flags': 64}})
        finally:
            d.close()

    def typed(self, event):
        if not self.typed_enabled:
            return
        self._typed_lock.acquire()
        d = self.runtime.db()
        try:
            value = texts.settings(d)
            if not value['enabled'] or (event.get('author') or {}).get('bot') or \
                    (event.get('author') or {}).get('id') != texts.owner(d, self.runtime.data_dir) or \
                    str(event.get('channel_id')) != value['channel_id']:
                return
            content = str(event.get('content') or '')[:2000]
            quoted = str((event.get('message_reference') or {}).get('message_id') or '')
            requests = [dict(r) for r in d.conn.execute("SELECT * FROM text_updates WHERE event='ask' AND state != 'baseline'")
                        if texts.waiting(d, r['item_id'], self.runtime.clock())]
            linked = next((r for r in requests if quoted and r['message_id'] == quoted), None)
            ids = re.findall(r'#(\d{1,12})', content)
            if linked:
                request = linked
            elif len(set(ids)) == 1:
                request = next((r for r in requests if r['item_id'] == int(ids[0])), None)
            else:
                request = requests[0] if len(requests) == 1 and not ids else None
            if request is None:
                # Do not guess among pending items or echo untrusted text.
                if requests and d._write("UPDATE text_updates SET clarified=1 WHERE item_id=? AND event='ask' AND clarified=0",
                                        (requests[0]['item_id'],)).rowcount:
                    self.send(value['channel_id'], {'content': 'Reply Approve, Deny or Damage Control with the #id shown on the request.',
                                                   'allowed_mentions': {'parse': []}})
                return
            item_id = request['item_id']
            item = d.get_watch_item(item_id)
            intent = texts.read_intent(content, item['title'], item_id, self.runtime.model(d))
            if intent == 'dmca' and value['show_dmca']:
                result = self.send(value['channel_id'], {'content': texts.RINGING, 'allowed_mentions': {'parse': []}})
                if result.kind == 'ok':
                    self.later(2, lambda: self.edit(value['channel_id'], result.message_id,
                        {'content': texts.lawyer(item_id), 'allowed_mentions': {'parse': []}}))
            elif intent in ('approve', 'deny'):
                if texts.claim(d, item_id, intent, 'discord', self.runtime.clock(), source='typed'):
                    self.runtime.wake()
            elif d._write("UPDATE text_updates SET clarified=1 WHERE item_id=? AND event='ask' AND clarified=0", (item_id,)).rowcount:
                self.send(value['channel_id'], {'content': f'Reply Approve, Deny or Damage Control for #{item_id}.',
                                               'allowed_mentions': {'parse': []}})
        except Exception:
            self.error('A Discord reply could not be read. Use the in-app approval buttons.')
        finally:
            d.close()
            self._typed_lock.release()

    def run(self):
        asyncio.run(self.gateway())

    def enabled(self):
        d = self.runtime.db()
        try:
            return bool(texts.settings(d)['enabled'] and self.token() and texts.owner(d, self.runtime.data_dir))
        finally:
            d.close()

    async def gateway(self):
        connect = self.connect
        if connect is None:
            from websockets.asyncio.client import connect

        backoff, url = 1, ''
        while not self._halt.is_set():
            try:
                if not self.enabled():
                    self._gateway_token = None
                    await asyncio.sleep(1)
                    continue
                current_token = self.token()
                if current_token != self._gateway_token:
                    self.session_id, self.resume_url, self.sequence = '', '', None
                    self.typed_enabled = True
                    self._gateway_token = current_token
                if self._fatal_token and self.token() == self._fatal_token:
                    await asyncio.sleep(1)
                    continue
                if not url:
                    result = await asyncio.to_thread(self.http.request, 'GET', '/gateway/bot', raw=True)
                    if not isinstance(result, dict):
                        if getattr(result, 'kind', '') == 'denied':
                            self._fatal_token = self.token()
                        raise ConnectionError()
                    limit = result.get('session_start_limit') or {}
                    if limit.get('remaining') == 0:
                        await asyncio.sleep(min(60, max(1, float(limit.get('reset_after', 1000)) / 1000)))
                        continue
                    url = gateway_url(result.get('url', ''))
                async with connect(gateway_url(self.resume_url) if self.session_id and self.resume_url else url,
                                                     open_timeout=10, close_timeout=1, max_size=8 * 1024 * 1024) as socket:
                    await self.connection(socket)
                    backoff = 1
            except Exception as error:
                self.connected = False
                code = getattr(getattr(error, 'rcvd', None), 'code', None) or getattr(error, 'code', None)
                if code == 4014:
                    self.typed_enabled = False
                    self.session_id, self.resume_url, self.sequence = '', '', None
                    self.error('Message Content Intent is unavailable. Buttons work; typed replies are ignored.')
                else:
                    if code in (4004, 4010, 4011):
                        self._fatal_token = self.token()
                    self.error('Discord bot disconnected. Pending clips still wait in the app.')
                for _ in range(backoff):
                    if self._halt.is_set():
                        break
                    await asyncio.sleep(1)
                backoff = min(60, backoff * 2)
            finally:
                was_connected = self.connected
                self.connected = False
                if was_connected:
                    self.runtime.changed()

    async def connection(self, socket):
        credential = self.token()
        interval, next_beat, unacked = None, 0, False
        while not self._halt.is_set() and self.enabled() and self.token() == credential:
            now = time.monotonic()
            if interval and now >= next_beat:
                if unacked:
                    await socket.close(code=4000)
                    raise ConnectionError()
                await socket.send(json.dumps({'op': 1, 'd': self.sequence}))
                unacked, next_beat = True, now + interval
            try:
                packet = json.loads(await asyncio.wait_for(socket.recv(), timeout=min(1, max(.001, next_beat - now)) if interval else 1))
            except asyncio.TimeoutError:
                continue
            op, data = packet.get('op'), packet.get('d')
            if packet.get('s') is not None:
                self.sequence = packet['s']
            if op == 10:
                interval = max(.01, float(data['heartbeat_interval']) / 1000)
                next_beat = time.monotonic() + random.random() * interval
                identify = {'op': 6, 'd': {'token': self.token(), 'session_id': self.session_id, 'seq': self.sequence}} \
                    if self.session_id else {'op': 2, 'd': {'token': self.token(),
                        'intents': BASE_INTENTS | (MESSAGE_CONTENT if self.typed_enabled else 0),
                        'properties': {'os': 'windows', 'browser': 'Clips Kitty', 'device': 'Clips Kitty'}}}
                await socket.send(json.dumps(identify))
            elif op == 11:
                unacked = False
            elif op == 1:
                await socket.send(json.dumps({'op': 1, 'd': self.sequence}))
                unacked, next_beat = True, time.monotonic() + interval
            elif op == 7:
                raise ConnectionError()
            elif op == 9:
                if not data:
                    self.session_id, self.resume_url, self.sequence = '', '', None
                await asyncio.sleep(random.uniform(1, 5))
                raise ConnectionError()
            elif op == 0:
                event = packet.get('t')
                if event == 'READY':
                    d = self.runtime.db()
                    try:
                        if str((data.get('application') or {}).get('id')) != discord_client_id(d):
                            self._fatal_token = self.token()
                            self.error('The bot belongs to another application. Use the Clips Kitty bot token.')
                            return
                    finally:
                        d.close()
                    self.session_id, self.resume_url = data['session_id'], data.get('resume_gateway_url', '')
                    self.connected = True
                    if self.typed_enabled:
                        self.error('')
                    else:
                        self.runtime.changed()
                elif event == 'RESUMED':
                    self.connected = True
                    self.runtime.changed()
                elif event in ('INTERACTION_CREATE', 'MESSAGE_CREATE'):
                    handler = self.interaction if event == 'INTERACTION_CREATE' else self.typed
                    threading.Thread(target=handler, args=(data,), daemon=True, name='clip-update-reply').start()


def discord_client_id(d):
    from campaigns.service import client_id

    return client_id(d)[0]
