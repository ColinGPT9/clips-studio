"""Internal desktop routes. Credentials only enter through JSON request bodies."""

import re
from typing import Literal

from fastapi import Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from core import secrets
from plugins import session
from server import text_updates as texts
from server.discord_bot import discord_client_id


class SettingsIn(BaseModel):
    enabled: bool | None = None
    away_from_desk: bool | None = None
    show_dmca: bool | None = None
    events: list[Literal['ready', 'scheduled', 'live', 'run_failed', 'publish_failed', 'rejected']] | None = None
    model: str | None = Field(default=None, max_length=128)
    channel_id: str | None = Field(default=None, pattern=r'^\d{0,20}$')
    dm: bool | None = None


def install(app, *, runtime, session_secret, broadcaster):
    def require_session(x_clips_kitty_session: str | None = Header(default=None)):
        if not session.matches(session_secret, x_clips_kitty_session):
            raise HTTPException(403, 'This needs the desktop session header.')

    guarded = [Depends(require_session)]

    def status(d):
        value = texts.settings(d)
        return {**{k: v for k, v in value.items() if k not in ('test_owner',)},
                'owner_connected': bool(texts.owner(d, runtime.data_dir)),
                'has_token': secrets.has(runtime.data_dir, texts.BOT_SECRET),
                'connected': bool(runtime.bot and runtime.bot.connected),
                'typed_enabled': bool(runtime.bot and runtime.bot.typed_enabled),
                'invite_url': f'https://discord.com/oauth2/authorize?client_id={discord_client_id(d)}&scope=bot&permissions=68608'}

    def tell():
        runtime.wake()
        if broadcaster:
            broadcaster.publish({'type': 'automation'})

    runtime.changed = tell

    @app.get('/text-updates/status')
    def get_status():
        d = runtime.db()
        try:
            return status(d)
        finally:
            d.close()

    @app.get('/text-updates/pending')
    def get_pending():
        d = runtime.db()
        try:
            return runtime.pending(d)
        finally:
            d.close()

    @app.patch('/text-updates/settings', dependencies=guarded)
    def patch_settings(body: SettingsIn):
        d = runtime.db()
        try:
            runtime.configure(d, body.model_dump(exclude_none=True))
            result = status(d)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        finally:
            d.close()
        tell()
        return result

    @app.put('/text-updates/discord-bot', dependencies=guarded)
    async def put_bot(request: Request):
        # Framework validation errors echo their input. Validate this secret
        # explicitly so a malformed token never appears in an error response.
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, 'Enter a valid bot token in Settings.') from None
        token = payload.get('token') if isinstance(payload, dict) else None
        if not isinstance(token, str) or not re.fullmatch(r'[\w.-]{16,256}', token):
            raise HTTPException(400, 'Enter a valid bot token in Settings.')
        d = runtime.db()
        try:
            secrets.save(runtime.data_dir, texts.BOT_SECRET, {'token': token})
            texts.store_settings(d, {'test_ok': False, 'test_owner': '', 'last_error': ''})
            result = status(d)
        finally:
            d.close()
        tell()
        return result

    @app.delete('/text-updates/credentials', dependencies=guarded)
    def delete_credentials():
        if not secrets.wipe(runtime.data_dir, texts.BOT_SECRET):
            raise HTTPException(500, 'The stored bot credential could not be removed.')
        d = runtime.db()
        try:
            texts.store_settings(d, {'test_ok': False, 'test_owner': ''})
            result = status(d)
        finally:
            d.close()
        tell()
        return result

    @app.get('/text-updates/channels')
    def channels():
        d = runtime.db()
        try:
            enabled = texts.settings(d)['enabled']
        finally:
            d.close()
        if not enabled or not runtime.bot or not runtime.bot.connected:
            return []
        guilds = runtime.bot.http.request('GET', '/users/@me/guilds', raw=True)
        if not isinstance(guilds, list):
            return []
        out = []
        for guild in guilds:
            from server.discord_bot import snowflake

            listed = runtime.bot.http.request('GET', f"/guilds/{snowflake(guild['id'])}/channels", raw=True)
            if isinstance(listed, list):
                out.extend({'id': str(c['id']), 'name': str(c.get('name') or 'Channel'),
                            'guild': str(guild.get('name') or 'Server')} for c in listed if c.get('type') in (0, 5))
        return out

    @app.post('/text-updates/test', dependencies=guarded)
    def test_message():
        d = runtime.db()
        try:
            value = texts.settings(d)
            uid = texts.owner(d, runtime.data_dir)
            if not uid:
                raise HTTPException(409, 'Sign in under Settings → Discord first.')
            if not value['enabled'] or not runtime.bot or not runtime.bot.connected:
                raise HTTPException(409, 'Enable Clip updates and connect the bot first.')
            channel = value['channel_id']
            if value['dm']:
                opened = runtime.bot.http.request('POST', '/users/@me/channels', {'recipient_id': uid}, raw=True)
                channel = str(opened.get('id') or '') if isinstance(opened, dict) else ''
            if not channel:
                raise HTTPException(400, 'Pick a private channel or allow the bot to open your DM.')
            result = runtime.bot.send(channel, {'content': 'Clips Kitty test message. Approval buttons will appear on new requests.',
                                                'allowed_mentions': {'parse': []}})
            ok = result.kind == 'ok'
            texts.store_settings(d, {'channel_id': channel, 'test_ok': ok, 'test_owner': uid if ok else '',
                'last_error': '' if ok else 'Discord could not deliver the test. Check channel permissions and DM privacy (50007).'})
            answer = {'ok': ok, **status(d)}
        finally:
            d.close()
        tell()
        return answer
