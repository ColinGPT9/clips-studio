"""The plugin manager's routes, mounted on the engine's app by create_app.

Every route here is labelled experimental (server/api_stability.py). The ones
that fetch, install, change or remove plugins need the session secret in an
X-Clips-Kitty-Session header (plugins/session.py); reading what is installed
does not.

    GET    /plugins                                    installed plugins, and the built-in modes
    POST   /plugins/plan       {"source": {...}}       fetch and check; say what installing would do
                               (a source can be {"kind": "index", "id": ..., "version": ...}: a listing)
    POST   /plugins/install    {"plan_id": "..."}      install what a plan staged
    POST   /plugins/{publisher}/{name}/enable | disable | rollback | pin | unpin
    DELETE /plugins/{publisher}/{name}
    PUT    /plugins/{publisher}/{name}/secrets  {"values": {"api_key": "..."}}
    GET    /marketplace?q=&category=&tag=&kind=        listed plugins, searched (no header)
    POST   /marketplace/refresh                        fetch the index addresses in settings (no header)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel

from plugins import manager, permissions, registry, session, store


class PlanIn(BaseModel):
    source: dict


class InstallIn(BaseModel):
    plan_id: str


class SecretsIn(BaseModel):
    values: dict[str, str | None]


def _game_name(slug: str) -> str:
    small = {"of", "the", "and", "a"}
    words = slug.split("-")
    return " ".join(w if w in small and i else (w.upper() if len(w) <= 3 and w not in small else w.capitalize())
                    for i, w in enumerate(words))


def _unofficial(listing: dict) -> str | None:
    """The label a listing about someone else's game or league carries."""
    games = [g for g in listing.get("games") or [] if isinstance(g, str)]
    if not games:
        return None
    return "Unofficial · not made or endorsed by the makers of " + ", ".join(_game_name(g) for g in games)


def install(app, *, data_dir: Path, config: dict | None = None, app_version: str | None = None, blocked=None,
            git: str | None = None, fetcher=None, bundled_index: Path | None = None) -> str:
    """Add the routes to `app`. Returns the session secret (for tests).
    `blocked` defaults to the registry's block lists, read on each call."""
    secret = session.secret_for(data_dir)
    version = app_version if app_version and app_version != "?" else None
    if blocked is None:
        def blocked(plugin_id, plugin_version):
            return registry.blocked_check(data_dir, bundled=bundled_index)(plugin_id, plugin_version)

    def urls() -> list[str]:
        return registry.index_urls(config or {})

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
        source = body.source
        if isinstance(source, dict) and source.get("kind") == "index":
            try:
                listing, entry = registry.find(data_dir, urls(), str(source.get("id")), source.get("version"),
                                               bundled=bundled_index)
            except registry.RegistryError as e:
                raise HTTPException(404, str(e)) from e
            return call(manager.plan, data_dir, registry.source_for(listing, entry), app_version=version,
                        blocked=blocked, git=git, fetcher=fetcher, tier="listed",
                        expect={"id": listing["id"], "version": entry["version"]}, listed_in=listing["index"])
        return call(manager.plan, data_dir, source, app_version=version, blocked=blocked, git=git,
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

    @app.get("/marketplace")
    def marketplace(q: str = "", category: str | None = None, tag: str | None = None, kind: str | None = None):
        installed = store.load(data_dir)["plugins"]
        found = registry.search(registry.listings(data_dir, urls(), bundled=bundled_index), q,
                                category=category, tag=tag, kind=kind)
        out = []
        for listing in found:
            entry = installed.get(listing["id"]) or {}
            have = entry.get("active")
            newer = bool(have and manager._version_order(listing["latest"], have) > 0)
            out.append({**listing,
                        "details": permissions.describe(listing, tier="listed"),
                        "unofficial": _unofficial(listing),
                        "installed": have,
                        "update_available": newer and not entry.get("pinned"),
                        "pinned": bool(entry.get("pinned"))})
        known = registry.indexes(data_dir, urls(), bundled=bundled_index)
        return {
            "plugins": out,
            "indexes": [{"url": i["url"], "fetched_at": i["fetched_at"], "cached": i["index"] is not None}
                        for i in known],
            "categories": list(manifest_vocabulary()["categories"]),
            "kinds": manifest_vocabulary()["kinds"],
        }

    @app.post("/marketplace/refresh")
    def marketplace_refresh():
        return {"indexes": registry.refresh(data_dir, urls(), fetcher=fetcher)}

    return secret


def manifest_vocabulary() -> dict:
    """Categories and kinds for the Marketplace's filters, from the validator."""
    from plugins._sdk import manifest

    return {"categories": manifest.CATEGORIES,
            "kinds": {**dict.fromkeys(manifest.KINDS, "built"), **dict.fromkeys(manifest.PLANNED_KINDS, "planned")}}
