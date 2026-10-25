"""Desktop-owned Connected Accounts routes; credentials never cross this API.

The same service also backs watched channels and capability-scoped plugin
requests. Registering another provider does not require another token store.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from connected_accounts.models import AccountError
from connected_accounts.service import ConnectedAccounts
from connected_accounts.store import atomic_write
from connected_accounts.twitch import TwitchProvider
from plugins import session


class TwitchConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9]+$")


class ConnectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[str] | None = Field(default=None, max_length=20)


class TwitchOperationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    # The provider permits only named operations, checks their scopes and
    # validates every parameter. No caller supplies a request URL or token.
    params: dict = Field(default_factory=dict)


class EventsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_types: list[str] = Field(default_factory=lambda: ["stream.online", "stream.offline"], max_length=5)


def _config_path(data_dir: Path) -> Path:
    return Path(data_dir) / "connected-account-providers.json"


def make_service(data_dir: Path) -> ConnectedAccounts:
    client_id = ""
    try:
        config = json.loads(_config_path(data_dir).read_text(encoding="utf-8"))
        value = config.get("twitch", {}).get("client_id", "")
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9]{1,128}", value):
            client_id = value
    except (OSError, ValueError, AttributeError):
        # Invalid public configuration cannot authorize anything. Let the
        # user re-enter the public client ID rather than blocking app startup.
        pass
    return ConnectedAccounts(data_dir, {"twitch": TwitchProvider(client_id)})


class AccountRuntime:
    def __init__(self, service, events):
        self.service = service
        self.events = events
        self.gateway = None
        self.workflow = None

    def start(self):
        self.service.start()

    def close(self):
        if self.gateway is not None:
            self.gateway.close()
        self.events.close()
        if self.workflow is not None:
            self.workflow.close()
        self.service.close()


def install(
    app,
    *,
    data_dir: Path,
    db,
    session_secret: str,
    service=None,
    watcher=None,
    broadcaster=None,
    event_manager=None,
) -> AccountRuntime:
    """Mount session-protected routes. Injectable services keep tests offline."""
    from connected_accounts.eventsub import EventSubManager

    service = service if service is not None else make_service(data_dir)
    events = event_manager if event_manager is not None else EventSubManager(service)
    runtime = AccountRuntime(service, events)
    configure_lock = threading.Lock()

    def require_session(given: str | None = Header(default=None, alias=session.HEADER)):
        if not session.matches(session_secret, given):
            raise HTTPException(403, f"{session.HEADER} is required to manage connected accounts.")

    dependencies = [Depends(require_session)]

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AccountError as error:
            raise HTTPException(error.status, {"code": error.code, "message": str(error)}) from None

    def notify():
        if broadcaster is not None:
            broadcaster.publish({"type": "connected_accounts"})

    def reset_consumers(account_id):
        events.stop(account_id)
        if runtime.gateway is not None:
            runtime.gateway.revoke_account(account_id)
        if runtime.workflow is not None:
            runtime.workflow.account_disconnected(account_id)

    def account(account_id):
        for row in service.accounts():
            if row["id"] == account_id:
                return row
        raise HTTPException(404, "This account is no longer connected.")

    def status():
        current = call(service.status)
        storage = current["credential_store"]
        providers = service.providers
        return {
            "providers": [
                {
                    **p,
                    "name": "Twitch" if p["id"] == "twitch" else p["id"],
                    "client_id": providers[p["id"]].client_id,
                    "storage": storage["backend"],
                    "storage_available": storage["available"],
                }
                for p in current["providers"]
            ],
            "accounts": current["accounts"],
        }

    @app.get("/connected-accounts", dependencies=dependencies)
    def get_accounts():
        return status()

    @app.put("/connected-accounts/twitch/config", dependencies=dependencies)
    def configure_twitch(body: TwitchConfigIn):
        with configure_lock:
            # Preserve future providers' public configuration. A corrupt
            # existing file needs recovery instead of silent replacement.
            path = _config_path(data_dir)
            try:
                original = path.read_bytes() if path.exists() else None
                public = json.loads(original) if original is not None else {}
                if not isinstance(public, dict):
                    raise ValueError
            except (OSError, ValueError):
                raise HTTPException(503, "The provider configuration could not be read.") from None
            public["twitch"] = {"client_id": body.client_id}
            replacement = TwitchProvider(body.client_id)
            try:
                atomic_write(path, json.dumps(public).encode("utf-8"))
            except OSError:
                raise HTTPException(503, "The provider configuration could not be saved.") from None
            old = service.providers.get("twitch")
            try:
                service.register_provider(replacement)
            except AccountError as error:
                # The service keeps the previous provider/roster on failure.
                # Keep the next app launch on that same public configuration.
                close = getattr(replacement, "close", None)
                if close:
                    close()
                try:
                    if original is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write(path, original)
                except OSError:
                    raise HTTPException(
                        503,
                        "The provider configuration could not be restored. Restart and check the client ID.",
                    ) from None
                raise HTTPException(error.status, {"code": error.code, "message": str(error)}) from None
            if old is not None and old.client_id != body.client_id:
                for row in service.accounts("twitch"):
                    reset_consumers(row["id"])
        notify()
        return status()

    @app.post("/connected-accounts/twitch/connect", dependencies=dependencies)
    def begin_twitch(body: ConnectIn):
        return call(service.begin, "twitch", body.scopes)

    @app.post("/connected-accounts/flows/{flow_id}/poll", dependencies=dependencies)
    def poll_flow(flow_id: str):
        result = call(service.poll, flow_id)
        if result.get("status") == "connected":
            # New consent is a new collection lifecycle, including reconnect
            # after revocation. Resume monitoring only through explicit opt-in.
            reset_consumers(result["account"]["id"])
            notify()
        return result

    @app.delete("/connected-accounts/flows/{flow_id}", dependencies=dependencies)
    def cancel_flow(flow_id: str):
        return call(service.cancel, flow_id)

    @app.post("/connected-accounts/{account_id}/test", dependencies=dependencies)
    def test_account(account_id: str):
        result = call(service.test, account_id)
        notify()
        return result

    @app.delete("/connected-accounts/{account_id}", dependencies=dependencies)
    def disconnect_account(account_id: str):
        account(account_id)
        reset_consumers(account_id)
        result = call(service.disconnect, account_id)
        notify()
        return result

    @app.post("/connected-accounts/{account_id}/watch", dependencies=dependencies)
    def watch_account(account_id: str):
        row = account(account_id)
        if row["provider"] != "twitch":
            raise HTTPException(400, "This provider does not support watched channels.")
        # Validate the saved identity before deriving a watched channel. A
        # watch grants no additional scopes or rights on Twitch.
        row = call(service.test, account_id)
        if "account" in row:
            row = row["account"]
        if not re.fullmatch(r"[A-Za-z0-9_]{1,25}", row.get("login", "")):
            raise HTTPException(409, "Reconnect Twitch before adding this channel.")
        from server.automation import PublishSettings, view_watch, watch_creator

        d = db()
        try:
            found = d.find_watch("twitch", row["login"].lower())
            created = found is None
            if found is None:
                watch_id = d.insert_watch(
                    "twitch",
                    row["login"].lower(),
                    name=row["display_name"],
                    publish=PublishSettings().model_dump_json(),
                    options=json.dumps({"preset": "standard"}),
                )
                found = d.get_watch(watch_id)
                watch_creator(d, found, create=True)
            result = {"created": created, **view_watch(d, found)}
        finally:
            d.close()
        if watcher is not None:
            watcher.wake()
        if broadcaster is not None:
            broadcaster.publish({"type": "automation"})
        return result

    @app.post("/connected-accounts/{account_id}/twitch", dependencies=dependencies)
    def twitch_operation(account_id: str, body: TwitchOperationIn):
        row = account(account_id)
        if row["provider"] != "twitch" or body.operation not in {
            "stream_context",
            "videos",
            "create_clip",
            "create_vod_clip",
            "get_clip",
            "vod_link",
            "send_chat",
        }:
            raise HTTPException(400, "This account operation is not supported.")
        params = dict(body.params)
        allowed = {
            "stream_context": {"broadcaster_id"},
            "videos": {"broadcaster_id", "first"},
            "create_clip": {"broadcaster_id", "has_delay"},
            "create_vod_clip": {"broadcaster_id", "video_id", "start_seconds", "end_seconds", "title"},
            "get_clip": {"broadcaster_id", "clip_id", "video_id"},
            "vod_link": {"video_id", "seconds"},
            "send_chat": {"broadcaster_id", "message"},
        }
        if set(params) - allowed[body.operation]:
            raise HTTPException(400, "This account operation received unsupported parameters.")
        # Desktop account actions manage the connected broadcaster only.
        # EventSub subscriptions always pass through the host socket below.
        if "broadcaster_id" in params and params["broadcaster_id"] != row["user_id"]:
            raise HTTPException(403, "Choose this connected broadcaster's channel.")
        if body.operation != "vod_link":
            params["broadcaster_id"] = row["user_id"]
        return call(service.perform, account_id, body.operation, **params)

    @app.post("/connected-accounts/{account_id}/events", dependencies=dependencies)
    def start_events(account_id: str, body: EventsIn):
        row = account(account_id)
        if row["provider"] != "twitch":
            raise HTTPException(400, "This provider does not support Twitch events.")
        return call(events.start, account_id, row["user_id"], body.event_types)

    @app.get("/connected-accounts/{account_id}/events", dependencies=dependencies)
    def get_events(account_id: str, after: int = 0, limit: int = 100):
        row = account(account_id)
        return call(events.events, account_id, broadcaster_id=row["user_id"], after=after, limit=limit)

    @app.delete("/connected-accounts/{account_id}/events", dependencies=dependencies)
    def stop_events(account_id: str):
        account(account_id)
        events.stop(account_id)
        return {"status": "stopped"}

    return runtime
