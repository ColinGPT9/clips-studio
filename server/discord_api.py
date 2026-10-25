"""Settings → Discord: signing in, and the webhooks Clips Kitty may post to.

The routes over campaigns/service.py, which holds every rule (and is tested
in CI's main job, which has no FastAPI). Every route that changes something
needs the desktop app's session secret in the X-Clips-Kitty-Session header,
the same value the plugin routes take (plugins/api.py returns it; this
module never makes its own, which would write a new one over it). A web page
or a stray script can't add a webhook or post through one. The callback is
protected by its one-time PKCE state instead, and parse-link changes nothing.

A webhook URL only ever travels in a request body: the access log prints
every request line with its query, and the log is teed into bug reports.
No route returns a token. Every route is internal (server/api_stability.py).
"""

import time

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from campaigns import discord_http, discord_oauth, links, post_links, service
from campaigns.discord_http import DiscordError
from plugins import session
from server.ai_api import _sign_in_page
from server.events import broadcaster


class SettingsIn(BaseModel):
    client_id: str | None = None


class ParseLinkIn(BaseModel):
    link: str = ""


class SendPublishedIn(BaseModel):
    destination_id: int = Field(ge=1)
    platform: str
    provider: str


class DestinationIn(BaseModel):
    webhook_url: str
    label: str = ""
    is_forum: bool = False
    thread_id: str = ""


class AuthorizeIn(BaseModel):
    label: str = ""


class DestinationPatch(BaseModel):
    label: str | None = None
    is_forum: bool | None = None
    thread_id: str | None = None
    webhook_url: str | None = None


#: The answer for each DiscordError code.
_STATUS = {"not_configured": 400, "not_connected": 409, "expired": 409, "gone": 400, "denied": 400,
           "rate_limited": 429, "network": 502, "bad_response": 502}


def _fail(e: DiscordError) -> HTTPException:
    return HTTPException(_STATUS.get(e.code, 502), e.message)


def install(app, *, db, data_dir, session_secret: str, http=None, publish_worker=None) -> None:
    """Add the /discord routes. `session_secret` is plugins_api.install's
    answer; `http` stands in for Discord in tests (default: the real one)."""
    transport = http if http is not None else discord_http.Http()
    pending = discord_oauth.PendingSignIns()

    def require_session(x_clips_kitty_session: str | None = Header(default=None)) -> None:
        if not session.matches(session_secret, x_clips_kitty_session):
            raise HTTPException(403, f"This needs the {session.HEADER} header. The desktop app sends it; a script "
                                     f"finds it in {session.path(data_dir)}.")

    guarded = [Depends(require_session)]

    def port_of(request: Request) -> int:
        return request.url.port or discord_oauth.DEFAULT_PORT

    def status(request: Request) -> dict:
        d = db()
        try:
            return service.status_payload(d, data_dir, redirect_uri=discord_oauth.redirect_uri(port_of(request)))
        finally:
            d.close()

    def tell(event: str) -> None:
        if event:
            broadcaster.publish({"type": "campaign", "discord": event})

    @app.get("/discord/status")
    def discord_status(request: Request):
        return status(request)

    @app.patch("/discord/settings", dependencies=guarded)
    def discord_settings(body: SettingsIn, request: Request):
        if body.client_id is not None:
            d = db()
            try:
                service.set_client_id(d, body.client_id)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            finally:
                d.close()
        return status(request)

    @app.post("/discord/connect", dependencies=guarded)
    def discord_connect(request: Request):
        d = db()
        try:
            url = service.start_sign_in(d, pending, purpose="connect", port=port_of(request), now=time.time())
        except DiscordError as e:
            raise _fail(e) from None
        finally:
            d.close()
        return {"url": url}

    @app.get("/discord/callback", response_class=HTMLResponse)
    def discord_callback(state: str = "", code: str = "", error: str = ""):
        d = db()
        try:
            done = service.finish_sign_in(d, data_dir, transport, pending, state=state, code=code, error=error,
                                          now=time.time())
        finally:
            d.close()
        tell(done.event)
        return _sign_in_page(done.message, ok=done.ok)

    @app.post("/discord/disconnect", dependencies=guarded)
    def discord_disconnect(request: Request):
        d = db()
        try:
            revoked = service.disconnect(d, data_dir, transport)
        finally:
            d.close()
        tell("disconnected")
        return {**status(request), "revoked": revoked}

    @app.get("/discord/guilds")
    def discord_guilds(refresh: bool = False):
        d = db()
        try:
            listed = service.guild_list(d, data_dir, transport, now=time.time(), refresh=refresh)
            fetched_at = float(service.load_settings(d).get("guilds_at") or 0)
        except DiscordError as e:
            if e.code == "expired":
                tell("expired")
            raise _fail(e) from None
        finally:
            d.close()
        return {"guilds": listed, "fetched_at": fetched_at}

    @app.post("/discord/parse-link")
    def discord_parse_link(body: ParseLinkIn):
        # Changes nothing, so no session header. The link comes in the body:
        # a pasted webhook URL in a query string would land in the access log.
        return links.parse_link(body.link).as_dict()

    @app.get("/discord/destinations")
    def discord_destinations():
        d = db()
        try:
            return {"destinations": service.destination_views(d, data_dir)}
        finally:
            d.close()

    @app.post("/discord/clips/{clip_id}/send", dependencies=guarded)
    def discord_send_published(clip_id: int, body: SendPublishedIn):
        d = db()
        try:
            destination = d.get_destination(body.destination_id)
            if destination is None:
                raise HTTPException(404, "No such Discord destination")
            if destination["state"] != "connected" or service.load_webhook(data_dir, body.destination_id) is None:
                raise HTTPException(409, "Reconnect this webhook in Settings before sending links.")
            try:
                result = post_links.send_existing(d, destination_id=body.destination_id, clip_id=clip_id,
                                                  platform=body.platform, provider=body.provider)
            except ValueError as e:
                raise HTTPException(409, str(e)) from e
        finally:
            d.close()
        if publish_worker is not None:
            publish_worker.notify_campaigns()
        return result

    @app.post("/discord/destinations", dependencies=guarded)
    def discord_add_destination(body: DestinationIn):
        d = db()
        try:
            added = service.add_destination(d, data_dir, transport, webhook_url=body.webhook_url, label=body.label,
                                            is_forum=body.is_forum, thread_id=body.thread_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        except DiscordError as e:
            raise _fail(e) from None
        finally:
            d.close()
        return {"destination": added}

    @app.post("/discord/destinations/authorize", dependencies=guarded)
    def discord_authorize_destination(body: AuthorizeIn, request: Request):
        d = db()
        try:
            url = service.start_sign_in(d, pending, purpose="webhook", port=port_of(request), now=time.time(),
                                        label=body.label)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        except DiscordError as e:
            raise _fail(e) from None
        finally:
            d.close()
        return {"url": url}

    @app.patch("/discord/destinations/{destination_id}", dependencies=guarded)
    def discord_update_destination(destination_id: int, body: DestinationPatch):
        d = db()
        try:
            changed = service.update_destination(d, data_dir, transport, destination_id, label=body.label,
                                                 is_forum=body.is_forum, thread_id=body.thread_id,
                                                 webhook_url=body.webhook_url)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        except DiscordError as e:
            raise _fail(e) from None
        finally:
            d.close()
        if changed is None:
            raise HTTPException(404, "No such destination")
        return {"destination": changed}

    @app.delete("/discord/destinations/{destination_id}", dependencies=guarded)
    def discord_remove_destination(destination_id: int):
        d = db()
        try:
            removed = service.remove_destination(d, data_dir, destination_id, now=time.time())
        finally:
            d.close()
        if removed is None:
            raise HTTPException(404, "No such destination")
        return removed

    @app.post("/discord/destinations/{destination_id}/test", dependencies=guarded)
    def discord_test_destination(destination_id: int):
        d = db()
        try:
            result = service.send_test_message(d, data_dir, transport, destination_id)
        finally:
            d.close()
        if result is None:
            raise HTTPException(404, "No such destination")
        return result
