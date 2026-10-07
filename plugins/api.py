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
    GET    /marketplace?q=&category=&tag=&kind=&section=   listed plugins, searched (no header)
    GET    /marketplace/catalog?q=&kind=&section=      the catalog's apps, models, workflows, integrations
                                                       and tools, searched (no header)
    POST   /marketplace/refresh                        fetch the index addresses in settings (no header)
    GET    /marketplace/counting                       whether installs are counted, and what that sends
    PUT    /marketplace/counting  {"enabled": false}   switch counting off (or on)
    GET    /plugin-models                              every model installed plugins list, and where it is
    POST   /plugin-models/plan      {"plugin", "model"}                  what downloading one would fetch
    POST   /plugin-models/download  {"plugin", "model", "allow_pickle"}  download it into the shared folder
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel

from plugins import catalog, counter, manager, permissions, registry, runner, session, store
from plugins import models as plugin_models


class PlanIn(BaseModel):
    source: dict


class InstallIn(BaseModel):
    plan_id: str


class SecretsIn(BaseModel):
    values: dict[str, str | None]


class ModelIn(BaseModel):
    plugin: str
    model: str
    allow_pickle: bool = False


class CountingIn(BaseModel):
    enabled: bool


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
            git: str | None = None, fetcher=None, bundled_index: Path | None = None,
            model_fetcher=None, model_json=None, count_opener=None) -> str:
    """Add the routes to `app`. Returns the session secret (for tests).
    `blocked` defaults to the registry's block lists, read on each call.
    `model_fetcher`, `model_json` and `count_opener` stand in for the network
    in tests (plugins/models.fetch_https and get_json, plugins/counter._get)."""
    model_fetcher = model_fetcher or plugin_models.fetch_https
    model_json = model_json or plugin_models.get_json
    secret = session.secret_for(data_dir)
    version = app_version if app_version and app_version != "?" else None
    if blocked is None:
        def blocked(plugin_id, plugin_version):
            return registry.blocked_check(data_dir, bundled=bundled_index)(plugin_id, plugin_version)

    def urls() -> list[str]:
        return registry.index_urls(config or {})

    def problems_here(details: dict, app_problem: str | None = None) -> list[dict]:
        """Why a plugin can't run on this PC, as far as the engine can tell
        before installing: the app version, and (in a source checkout only; the
        installed app runs plugins on its own Python) a Python to run it with."""
        out = [{"need": "app", "text": app_problem[:1].upper() + app_problem[1:]}] if app_problem else []
        if details.get("needs_python") and not runner.python_for(None, config):
            out.append({"need": "python", "text": "It needs Python 3.10 or newer, and none was found on this PC"})
        return out

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
        out = manager.listing(data_dir, app_version=version, blocked=blocked)
        for plugin in out["plugins"]:
            plugin["problems_here"] = problems_here(plugin.get("details") or {})
        return out

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
                        blocked=blocked, git=git, fetcher=fetcher, tier=registry.listing_tier(listing),
                        expect={"id": listing["id"], "version": entry["version"]}, listed_in=listing["index"])
        return call(manager.plan, data_dir, source, app_version=version, blocked=blocked, git=git,
                    fetcher=fetcher)

    @app.post("/plugins/install", dependencies=guarded)
    def install_plugin(body: InstallIn):
        before = set(store.load(data_dir)["plugins"])
        view = call(manager.install, data_dir, body.plan_id, app_version=version, blocked=blocked)
        view["counted"] = False
        listed_in = (view.get("source") or {}).get("listed_in")
        if listed_in and view.get("id") not in before and (view.get("details") or {}).get("tier") in \
                permissions.LISTED_TIERS:
            # A first install from a listing; updates and version switches aren't counted.
            template = registry.counter_for(data_dir, urls(), listed_in, bundled=bundled_index)
            view["counted"] = counter.count_install(data_dir, config, template, view["id"],
                                                    opener=count_opener) is not None
        return view

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
    def marketplace(q: str = "", category: str | None = None, tag: str | None = None, kind: str | None = None,
                    section: str | None = None):
        installed = store.load(data_dir)["plugins"]
        found = registry.search(registry.listings(data_dir, urls(), bundled=bundled_index), q,
                                category=category, tag=tag, kind=kind, section=section)
        out = []
        for listing in found:
            entry = installed.get(listing["id"]) or {}
            have = entry.get("active")
            newer = bool(have and manager._version_order(listing["latest"], have) > 0)
            versions = [{**v, "problem_here": store.compatibility_problem(v, version)} for v in listing["versions"]]
            latest = next((v for v in versions if v["version"] == listing["latest"]), versions[0])
            details = permissions.describe(listing, tier=registry.listing_tier(listing))
            out.append({**listing,
                        "versions": versions,
                        "problems_here": problems_here(details, latest["problem_here"]),
                        "details": details,
                        "unofficial": _unofficial(listing),
                        "installed": have,
                        "update_available": newer and not entry.get("pinned"),
                        "pinned": bool(entry.get("pinned"))})
        known = registry.indexes(data_dir, urls(), bundled=bundled_index)
        sections = next((i["index"]["sections"] for i in known if (i["index"] or {}).get("sections")), {})
        return {
            "plugins": out,
            "sections": {k: v for k, v in sections.items() if k in catalog.INSTALLABLE_KINDS.values()},
            "indexes": [{"url": i["url"], "fetched_at": i["fetched_at"], "cached": i["index"] is not None,
                         "plugins": len((i["index"] or {}).get("plugins") or [])}
                        for i in known],
            "categories": list(manifest_vocabulary()["categories"]),
            "kinds": manifest_vocabulary()["kinds"],
        }

    @app.get("/marketplace/catalog")
    def marketplace_catalog(q: str = "", kind: str | None = None, section: str | None = None):
        """The directory's other kinds: apps, models, workflows, integrations
        and tools. They aren't installed from here; each links to its home."""
        entries, sections = registry.catalog_entries(data_dir, urls(), bundled=bundled_index)
        if kind is not None and kind not in catalog.DIRECTORY_KINDS.values():
            raise HTTPException(400, f"kind must be one of {', '.join(catalog.DIRECTORY_KINDS.values())}")
        found = registry.search(entries, q, kind=kind, section=section)
        listed = {p["id"] for p in registry.listings(data_dir, urls(), bundled=bundled_index)}
        out = [{**e, "unofficial": _unofficial(e), "adapter_listed": bool(e.get("adapter") and e["adapter"] in listed)}
               for e in found]
        return {
            "entries": out,
            "sections": {k: v for k, v in sections.items() if k in catalog.DIRECTORY_KINDS.values()},
            "kinds": [{"id": k, "title": catalog.KIND_TITLES[k]} for k in catalog.KIND_ORDER],
            "relationships": catalog.RELATIONSHIPS,
            "badges": {b: {"label": catalog.BADGES[b], "meaning": catalog.BADGE_MEANING[b]} for b in catalog.BADGES},
            "metrics_at": next((i["index"].get("metrics_at") for i in registry.indexes(
                data_dir, urls(), bundled=bundled_index) if (i["index"] or {}).get("metrics_at")), None),
        }

    @app.post("/marketplace/refresh")
    def marketplace_refresh():
        return {"indexes": registry.refresh(data_dir, urls(), fetcher=fetcher)}

    def counting_view() -> dict:
        known = registry.indexes(data_dir, urls(), bundled=bundled_index)
        return {"enabled": counter.enabled(data_dir, config),
                "locked_off": ((config or {}).get("plugins") or {}).get("count_installs") is False,
                "active": any(((i["index"] or {}).get("counter") or {}).get("install") for i in known),
                "text": counter.EXPLAIN}

    @app.get("/marketplace/counting")
    def marketplace_counting():
        return counting_view()

    @app.put("/marketplace/counting", dependencies=guarded)
    def marketplace_set_counting(body: CountingIn):
        counter.set_enabled(data_dir, body.enabled)
        return counting_view()

    def ollama_list() -> list[dict] | None:
        host_url = ((config or {}).get("llm") or {}).get("ollama_host") or "http://localhost:11434"
        return plugin_models.ollama_models(host_url, fetch_json=model_json)

    def model_ref(plugin: str, name: str) -> dict:
        if not store.ID_RE.match(plugin):
            raise HTTPException(404, f"{plugin!r} is not a plugin id")
        found = store.get(data_dir, plugin)
        if found is None:
            raise HTTPException(404, f"{plugin} isn't installed")
        for ref in plugin_models.refs_of(found.manifest):
            if ref["name"] == name:
                return ref
        raise HTTPException(404, f"{found.name} lists no model called {name!r}")

    def model_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except plugin_models.ModelError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/plugin-models")
    def plugin_model_list():
        found = [p for p in (store.get(data_dir, pid) for pid in sorted(store.load(data_dir)["plugins"])) if p]
        listed = [{"id": p.id, "manifest": p.manifest} for p in found]
        asks_ollama = any(r["source"] == "ollama" for p in listed for r in plugin_models.refs_of(p["manifest"]))
        out = model_call(plugin_models.overview, data_dir, listed, ollama=ollama_list() if asks_ollama else None)
        if not asks_ollama:
            out["ollama_reachable"] = None  # not asked: no plugin lists an Ollama model
        return out

    @app.post("/plugin-models/plan", dependencies=guarded)
    def plugin_model_plan(body: ModelIn):
        ref = model_ref(body.plugin, body.model)
        return model_call(plugin_models.plan, data_dir, ref, fetch_json=model_json,
                          ollama=ollama_list() if ref["source"] == "ollama" else None)

    @app.post("/plugin-models/download", dependencies=guarded)
    def plugin_model_download(body: ModelIn):
        ref = model_ref(body.plugin, body.model)
        return model_call(plugin_models.download, data_dir, ref, fetcher=model_fetcher, fetch_json=model_json,
                          allow_pickle=body.allow_pickle)

    return secret


def manifest_vocabulary() -> dict:
    """Categories and kinds for the Marketplace's filters, from the validator."""
    from plugins._sdk import manifest

    return {"categories": manifest.CATEGORIES,
            "kinds": {**dict.fromkeys(manifest.KINDS, "built"), **dict.fromkeys(manifest.PLANNED_KINDS, "planned")}}
