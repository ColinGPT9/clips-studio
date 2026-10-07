"""The plugin manager's routes, mounted on the engine's app by create_app.

Every route here is labelled experimental (server/api_stability.py). The ones
that fetch, install, change or remove plugins need the session secret in an
X-Clips-Kitty-Session header (plugins/session.py); reading what is installed
does not.

    GET    /plugins                                    installed plugins, and the built-in modes
    POST   /plugins/plan       {"source": {...}}       fetch and check; say what installing would do
    POST   /plugins/install    {"plan_id": "..."}      install what a plan staged
    POST   /plugins/{publisher}/{name}/enable | disable | rollback | pin | unpin
    DELETE /plugins/{publisher}/{name}
    PUT    /plugins/{publisher}/{name}/secrets  {"values": {"api_key": "..."}}
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel

from plugins import manager, session, store


class PlanIn(BaseModel):
    source: dict


class InstallIn(BaseModel):
    plan_id: str


class SecretsIn(BaseModel):
    values: dict[str, str | None]


def install(app, *, data_dir: Path, app_version: str | None = None, blocked=None, git: str | None = None,
            fetcher=None) -> str:
    """Add the routes to `app`. Returns the session secret (for tests)."""
    secret = session.secret_for(data_dir)
    version = app_version if app_version and app_version != "?" else None

    def require_session(x_clips_kitty_session: str | None = Header(default=None)) -> None:
        if not session.matches(secret, x_clips_kitty_session):
            raise HTTPException(403, f"This needs the {session.HEADER} header. The desktop app sends it; a script "
                                     f"finds it in {session.path(data_dir)}.")

    def plugin_id(publisher: str, name: str) -> str:
        pid = f"{publisher}/{name}"
        if not store.ID_RE.match(pid):
            raise HTTPException(404, f"{pid!r} is not a plugin id")
        return pid

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except manager.ManagerError as e:
            raise HTTPException(e.status, str(e)) from e

    guarded = [Depends(require_session)]

    @app.get("/plugins")
    def list_plugins():
        return manager.listing(data_dir, app_version=version, blocked=blocked)

    @app.post("/plugins/plan", dependencies=guarded)
    def plan_plugin(body: PlanIn):
        return call(manager.plan, data_dir, body.source, app_version=version, blocked=blocked, git=git,
                    fetcher=fetcher)

    @app.post("/plugins/install", dependencies=guarded)
    def install_plugin(body: InstallIn):
        return call(manager.install, data_dir, body.plan_id, app_version=version, blocked=blocked)

    @app.post("/plugins/{publisher}/{name}/enable", dependencies=guarded)
    def enable_plugin(publisher: str, name: str):
        return call(manager.set_enabled, data_dir, plugin_id(publisher, name), True, blocked=blocked)

    @app.post("/plugins/{publisher}/{name}/disable", dependencies=guarded)
    def disable_plugin(publisher: str, name: str):
        return call(manager.set_enabled, data_dir, plugin_id(publisher, name), False, blocked=blocked)

    @app.post("/plugins/{publisher}/{name}/rollback", dependencies=guarded)
    def rollback_plugin(publisher: str, name: str):
        return call(manager.rollback, data_dir, plugin_id(publisher, name), app_version=version, blocked=blocked)

    @app.post("/plugins/{publisher}/{name}/pin", dependencies=guarded)
    def pin_plugin(publisher: str, name: str):
        return call(manager.set_pinned, data_dir, plugin_id(publisher, name), True, blocked=blocked)

    @app.post("/plugins/{publisher}/{name}/unpin", dependencies=guarded)
    def unpin_plugin(publisher: str, name: str):
        return call(manager.set_pinned, data_dir, plugin_id(publisher, name), False, blocked=blocked)

    @app.delete("/plugins/{publisher}/{name}", dependencies=guarded)
    def remove_plugin(publisher: str, name: str):
        return call(manager.remove, data_dir, plugin_id(publisher, name))

    @app.put("/plugins/{publisher}/{name}/secrets", dependencies=guarded)
    def plugin_secrets(publisher: str, name: str, body: SecretsIn):
        return {"set": call(manager.set_secrets, data_dir, plugin_id(publisher, name), body.values)}

    return secret
