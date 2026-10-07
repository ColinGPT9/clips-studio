"""The registry: the index format, the index build, and the app's client.

The index is one JSON file built from the Awesome Clips Kitty catalog
(awesome-clips-kitty/, see plugins/catalog.py and its CONTRIBUTING.md):

    {"format": 1,
     "plugins": [{"id": "example-dev/example-plugin", "name": ..., "latest": "1.1.0",
                  "versions": [{"version": "1.1.0", "commit": "<40 hex>", ...}, ...],
                  "repository": "https://github.com/example-dev/example-plugin", "path": ".",
                  "section": "gaming/generic", "relationship": "built-for",
                  "badges": ["community", "compatible"], "metrics": {...}, "compatibility": {...},
                  ...the latest version's manifest fields the Marketplace shows...,
                  "checks": {...}}],
     "catalog": [{"key": "apps/example-app", "kind": "app", "name": ..., "relationship": "related",
                  "license": "MIT", "source": {"github": ...}, "badges": [...], "metrics": {...}, ...}],
     "sections": {"app": {"sections": [{"id", "title", "description"}], "wanted": [...]}, ...},
     "metrics_at": "2026-10-07",                       # when the numbers were read, if ever
     "counter": {"install": "https://...{asset}..."},  # optional: where installs are counted
     "blocklist": [{"id": ..., "versions": "*" or [...], "severity": "blocked" | "delisted",
                    "reason": ..., "date": ...}]}

"plugins" are the installable listings (pipelines today); "catalog" is
everything else the directory lists. build_index() makes the index
(scripts/build_registry_index.py runs it in CI). The client reads the copy
bundled with the app (awesome-clips-kitty/index.json) and the indexes at the
addresses in settings (`plugins.registry_urls`, none by default: no address
has been published), caches the last good copy of each under
<data_dir>/plugins/cache/, works offline from that cache, and searches
locally. Nothing here runs plugin code or clones a repository.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

from plugins import catalog, sources, store
from plugins._sdk import manifest

FORMAT = 1
CACHE = "cache"
SEVERITIES = ("blocked", "delisted")
LISTING_FIELDS = ("id", "repository", "path", "aliases", "versions", "section", "featured", "added", "checked")
VERSION_FIELDS = ("version", "commit", "tag", "tested_with", "date")
MAX_INDEX_BYTES = 20 * 1024 * 1024
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Manifest fields copied into the index for the Marketplace.
SHOWN = ("name", "description", "kind", "capability", "license", "category", "tags", "games", "events",
         "execution", "inputs", "outputs", "permissions", "network", "sends", "requirements", "models", "service",
         "links", "examples", "author", "run", "based_on")

# Words people search for and the words listings use. A query word matches
# either side. Grown by pull request; each entry must be true for everyone.
ALIASES = {
    "wow": "world of warcraft",
    "lol": "league of legends",
    "cs2": "counter strike 2",
    "csgo": "counter strike",
    "football": "soccer",
    "soccer": "football",
    "mtg": "magic the gathering",
    "pubg": "playerunknown s battlegrounds",
    "ow": "overwatch",
    "r6": "rainbow six",
    "yt": "youtube",
}
STOPWORDS = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}
WEIGHTS = (("name", 6), ("aliases", 5), ("games", 5), ("tags", 4), ("events", 3), ("category", 2),
           ("capability", 1), ("description", 1))


class RegistryError(ValueError):
    pass


# ---- the index build ------------------------------------------------------------------


def _owner_repo(url) -> tuple[str, str] | None:
    m = sources.GITHUB_RE.match(url) if isinstance(url, str) else None
    return (m.group(1), m.group(2)) if m else None


def raw_manifest_url(owner: str, repo: str, commit: str, path: str = ".") -> str:
    """Where GitHub serves a file of a commit as plain text."""
    inner = "" if path in (".", "") else path.strip("/") + "/"
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{inner}{manifest.MANIFEST_FILE}"


def fetch_raw(url: str) -> str:
    """A small text file over HTTPS (the build's default way to read a manifest)."""
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "Clips-Kitty-registry-build"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise RegistryError("the manifest is larger than 1 MB")
    return data.decode("utf-8")


def check_listing(data, rel_path: str, *, folder: str = "pipelines", sections: dict | None = None) -> list[str]:
    """Problems with one listing file's content (before any manifest is fetched).
    `folder` is pipelines or plugins; `sections` is sections.yaml, when there is one."""
    problems = []
    if not isinstance(data, dict):
        return ["a listing is a mapping of fields"]
    for key in data:
        if key not in LISTING_FIELDS:
            problems.append(f"{key}: unknown field")
    pid = data.get("id")
    if not isinstance(pid, str) or not store.ID_RE.match(pid):
        problems.append("id: must look like publisher/name")
        return problems
    if rel_path != f"{pid}.yaml":
        problems.append(f"the file must be {folder}/{pid}.yaml")
    publisher = pid.split("/", 1)[0]
    if publisher in manifest.RESERVED_PUBLISHERS:
        problems.append(f"id: the publisher {publisher!r} is reserved for plugins that ship with Clips Kitty")
    where = _owner_repo(data.get("repository"))
    if not where:
        problems.append("repository: must be https://github.com/<owner>/<repo>")
    elif where[0].lower() != publisher and where[0].lower() not in catalog.OFFICIAL_OWNERS:
        # The Clips Kitty project's own repositories may hold listings under
        # other publisher names (its examples); nobody else's may.
        problems.append(f"repository: the publisher {publisher!r} must be the repository's GitHub owner, "
                        f"{where[0]!r}")
    if sections is not None:
        kind = catalog.INSTALLABLE_KINDS.get(folder, "pipeline")
        known = catalog.section_ids(sections, kind)
        if data.get("section") not in known:
            problems.append(f"section: one of {', '.join(known) or '(none defined for ' + kind + 's)'}")
    elif "section" in data and not (isinstance(data["section"], str) and catalog.SECTION_RE.match(data["section"])):
        problems.append("section: a section id from sections.yaml")
    catalog.check_featured(problems, "featured", data.get("featured"))
    for key in ("added", "checked"):
        if key in data and not DATE_RE.match(str(data[key])):
            problems.append(f"{key}: YYYY-MM-DD")
    path = data.get("path", ".")
    if not isinstance(path, str) or (path != "." and not manifest._relative_inside(path)):
        problems.append("path: must be a folder inside the repository")
    aliases = data.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > 10 or not all(
            isinstance(a, str) and 0 < len(a) <= 40 for a in aliases):
        problems.append("aliases: at most 10 words or short phrases")
    versions = data.get("versions")
    if not isinstance(versions, list) or not versions:
        problems.append("versions: must list at least one version")
        return problems
    seen = set()
    for i, v in enumerate(versions):
        where_v = f"versions[{i}]"
        if not isinstance(v, dict):
            problems.append(f"{where_v}: must be a mapping")
            continue
        for key in v:
            if key not in VERSION_FIELDS:
                problems.append(f"{where_v}.{key}: unknown field")
        version, commit = v.get("version"), v.get("commit")
        if not isinstance(version, str) or not manifest.VERSION_RE.match(version):
            problems.append(f"{where_v}.version: must be a version like 1.2.0")
        elif version in seen:
            problems.append(f"{where_v}.version: {version} is listed twice")
        seen.add(version)
        if not isinstance(commit, str) or not sources.COMMIT_RE.match(commit):
            problems.append(f"{where_v}.commit: must be the full 40-character commit hash")
        if "date" in v and not DATE_RE.match(str(v["date"])):
            problems.append(f"{where_v}.date: must be YYYY-MM-DD")
    return problems


def check_blocklist(entries) -> list[str]:
    if entries is None:
        return []
    if not isinstance(entries, list):
        return ["the block list is a list of entries"]
    problems = []
    for i, e in enumerate(entries):
        where = f"blocklist[{i}]"
        if not isinstance(e, dict):
            problems.append(f"{where}: must be a mapping")
            continue
        if not isinstance(e.get("id"), str) or not store.ID_RE.match(e["id"]):
            problems.append(f"{where}.id: must look like publisher/name")
        versions = e.get("versions")
        if versions != "*" and not (isinstance(versions, list) and versions and all(
                isinstance(v, str) and manifest.VERSION_RE.match(v) for v in versions)):
            problems.append(f"{where}.versions: \"*\" or a list of versions")
        if e.get("severity") not in SEVERITIES:
            problems.append(f"{where}.severity: blocked or delisted")
        if not isinstance(e.get("reason"), str) or not e["reason"].strip():
            problems.append(f"{where}.reason: say why, in words a user understands")
        if not DATE_RE.match(str(e.get("date", ""))):
            problems.append(f"{where}.date: YYYY-MM-DD")
    return problems


def _clean_block(e: dict) -> dict:
    out = {k: e[k] for k in ("id", "versions", "severity", "reason") if k in e}
    out["date"] = str(e.get("date"))
    if e.get("advisory"):
        out["advisory"] = str(e["advisory"])
    return out


def block_entry(blocklist: list, plugin_id: str, version: str | None) -> dict | None:
    """The block-list entry covering a version, the most severe first."""
    hits = [e for e in blocklist or [] if e.get("id") == plugin_id
            and (e.get("versions") == "*" or (version and version in (e.get("versions") or [])))]
    hits.sort(key=lambda e: SEVERITIES.index(e.get("severity")) if e.get("severity") in SEVERITIES else 9)
    return hits[0] if hits else None


def _settings_shown(settings) -> dict:
    out = {}
    for name, spec in (settings or {}).items():
        if isinstance(spec, dict):
            out[name] = {k: spec[k] for k in ("type", "title", "description") if k in spec}
    return out


def build_index(catalog_dir: Path, *, fetch=fetch_raw) -> tuple[dict, list[str]]:
    """Build an index from a catalog folder (one with a registry/ folder in
    it). Returns (index, problems); a listing or entry with any problem is
    left out. `fetch(url) -> text` reads a manifest at a commit (fetch_raw,
    or a fixture reader in tests)."""
    import yaml

    catalog_dir = Path(catalog_dir)
    registry_dir = catalog_dir / "registry"
    problems: list[str] = []
    block_path = registry_dir / "blocklist.yaml"
    blocklist = yaml.safe_load(block_path.read_text(encoding="utf-8")) if block_path.exists() else []
    for p in check_blocklist(blocklist):
        problems.append(f"blocklist.yaml: {p}")
    blocklist = [_clean_block(e) for e in (blocklist or []) if isinstance(e, dict)] if not problems else []

    has_sections = (registry_dir / "sections.yaml").exists()
    sections, found = catalog.read_sections(catalog_dir) if has_sections else ({}, [])
    problems += found
    settings, found = catalog.read_settings(catalog_dir)
    problems += found
    metrics, compatibility = catalog.read_stats(catalog_dir)

    plugins = []
    for folder, kind in catalog.INSTALLABLE_KINDS.items():
        base = registry_dir / folder
        for path in sorted(base.rglob("*")) if base.is_dir() else ():
            if path.is_dir() or path.name.startswith("."):
                continue
            rel = path.relative_to(base).as_posix()
            label = f"{folder}/{rel}"
            if path.suffix not in (".yaml", ".yml"):
                problems.append(f"{label}: a listing is a .yaml file")
                continue
            try:
                listing = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as e:
                problems.append(f"{label}: not valid YAML ({e})")
                continue
            found = check_listing(listing, rel, folder=folder, sections=sections if has_sections else None)
            if found:
                problems += [f"{label}: {p}" for p in found]
                continue
            item, found = _build_listing(listing, label, kind, fetch=fetch, blocklist=blocklist,
                                         metrics=metrics, compatibility=compatibility)
            problems += found
            if item:
                plugins.append(item)

    entries, found = catalog.read_entries(catalog_dir, sections) if has_sections else ([], [])
    problems += found
    listed_ids = {p["id"] for p in plugins}
    finished = []
    for e in entries:
        if e.get("adapter") and e["adapter"] not in listed_ids:
            problems.append(f"{e['key']}: adapter: {e['adapter']} is not a listing here")
            continue
        finished.append(catalog.finish_entry(e, metrics))
    index = {"format": FORMAT, "plugins": plugins, "blocklist": blocklist}
    if has_sections:
        index["catalog"] = finished
        index["sections"] = {kind: {"sections": [{k: s[k] for k in ("id", "title", "description") if k in s}
                                                 for s in spec.get("sections", [])],
                                    "wanted": spec.get("wanted") or []}
                             for kind, spec in sections.items()}
    if metrics.get("generated_at"):
        index["metrics_at"] = str(metrics["generated_at"])[:10]
    if settings.get("counter"):
        index["counter"] = settings["counter"]
    return index, problems


def _build_listing(listing: dict, label: str, kind: str, *, fetch, blocklist: list, metrics: dict,
                   compatibility: dict) -> tuple[dict | None, list[str]]:
    """One listing as the index carries it, with the manifest of every
    version fetched at its commit and checked."""
    problems: list[str] = []
    owner, repo = _owner_repo(listing["repository"])
    folder = listing.get("path", ".")
    versions, bad = [], False
    for v in listing["versions"]:
        url = raw_manifest_url(owner, repo, v["commit"], folder)
        try:
            data = yaml_load(fetch(url))
        except Exception as e:  # any failure to read is reported, not raised
            problems.append(f"{label}: {v['version']}: the manifest at commit {v['commit'][:7]} could not be "
                            f"read ({e})")
            bad = True
            continue
        report = manifest.validate(data)
        found = [f"clipskitty.yaml: {err}" for err in report.errors]
        if report.ok:
            for key, want in (("id", listing["id"]), ("version", v["version"])):
                if data.get(key) != want:
                    found.append(f"the manifest's {key} is {data.get(key)!r}, the listing says {want!r}")
            if data.get("kind") != kind and kind == "pipeline":
                found.append(f"the manifest's kind is {data.get('kind')!r}: only pipelines go in pipelines/")
            elif data.get("kind") == "pipeline" and kind != "pipeline":
                found.append("a pipeline's listing goes in pipelines/")
        if found:
            problems += [f"{label}: {v['version']}: {f}" for f in found]
            bad = True
            continue
        if block_entry(blocklist, listing["id"], v["version"]):
            continue
        entry = {"version": v["version"], "commit": v["commit"],
                 "requires": data.get("requires"), "permissions": data.get("permissions")}
        for key in ("tag", "tested_with", "date"):
            if key in v:
                entry[key] = str(v[key]) if key == "date" else v[key]
        record = catalog.compatibility_for(listing["id"], entry, compatibility)
        if record:
            entry["compatibility"] = record
        versions.append((manifest._version_tuple(v["version"]), entry, data))
    if bad or not versions:
        return None, problems
    versions.sort(key=lambda t: t[0], reverse=True)
    latest_entry, latest_manifest = versions[0][1], versions[0][2]
    item = {"id": listing["id"], "publisher": listing["id"].split("/")[0], "repository": listing["repository"],
            "path": folder, "aliases": listing.get("aliases", []), "latest": latest_entry["version"],
            "versions": [e for _, e, _ in versions], "settings": _settings_shown(latest_manifest.get("settings")),
            "relationship": "built-for",
            # A listing in one of the project's own repositories may use another publisher name (its
            # examples); it says so instead of claiming the publisher owns the repository.
            "checks": {"manifest_valid": True,
                       ("publisher_is_repository_owner" if owner.lower() == listing["id"].split("/")[0].lower()
                        else "official_repository"): True,
                       "commit_pinned": True, "public_at_commit": True}}
    item.update({k: latest_manifest[k] for k in SHOWN if k in latest_manifest})
    for key in ("section", "added", "checked"):
        if key in listing:
            item[key] = str(listing[key])
    record = latest_entry.get("compatibility")
    compatible = bool(record and record.get("passed")
                      and record.get("plugin_api") in manifest.SUPPORTED_PLUGIN_APIS)
    item["badges"] = catalog.badges(official=catalog.is_official(listing["repository"]), compatible=compatible,
                                    featured=bool(listing.get("featured")))
    if listing.get("featured"):
        item["featured"] = {"reason": listing["featured"]["reason"], "date": str(listing["featured"]["date"])}
    models = [m.get("id") for m in latest_manifest.get("models") or []
              if isinstance(m, dict) and m.get("source") == "huggingface" and m.get("id")]
    item["metrics"] = catalog.entry_metrics(listing["repository"], models, metrics, listing_id=listing["id"])
    url = catalog.discussions_url(listing["repository"], item["metrics"].get("github"))
    if url:
        item["discussions_url"] = url
    return item, problems


def yaml_load(text: str):
    import yaml

    return yaml.safe_load(text)


def index_text(index: dict) -> str:
    return json.dumps(index, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


# ---- the client: reading indexes --------------------------------------------------------


CATALOG_FOLDER = "awesome-clips-kitty"


def catalog_path() -> Path:
    """The Awesome Clips Kitty folder in a source checkout (or the frozen app's bundle)."""
    bundle = getattr(sys, "_MEIPASS", None)
    base = Path(bundle) if bundle else Path(__file__).resolve().parent.parent
    return base / CATALOG_FOLDER


def bundled_path() -> Path:
    return catalog_path() / "index.json"


def _clean_entry(e) -> dict | None:
    """A catalog entry read from an index, or None when it isn't one. Links
    are kept only when they are what they claim to be."""
    if not isinstance(e, dict) or e.get("kind") not in catalog.DIRECTORY_KINDS.values():
        return None
    key = e.get("key")
    folder = catalog.FOLDER_OF_KIND[e["kind"]]
    if not isinstance(key, str) or not key.startswith(folder + "/") or not catalog.SLUG_RE.match(key[len(folder) + 1:]):
        return None
    if not isinstance(e.get("name"), str) or not isinstance(e.get("license"), str):
        return None
    source = e.get("source") if isinstance(e.get("source"), dict) else {}
    clean = {}
    if catalog.github_repo(source.get("github")):
        clean["github"] = source["github"]
        if isinstance(source.get("path"), str) and manifest._relative_inside(source["path"]):
            clean["path"] = source["path"]
    if isinstance(source.get("huggingface"), str) and catalog.HF_ID_RE.match(source["huggingface"]):
        clean["huggingface"] = source["huggingface"]
    if catalog._https(source.get("url")):
        clean["url"] = source["url"]
    if not clean:
        return None
    out = {**e, "source": clean}
    if e.get("discussions_url") and not str(e["discussions_url"]).startswith("https://github.com/"):
        out.pop("discussions_url")
    out["badges"] = _trusted_badges(e.get("badges"), clean.get("github"), installable=False)
    return out


def _trusted_badges(claimed, repository, *, installable: bool) -> list[str]:
    """Badges as an index claims them, except Official, which follows from
    the repository whatever an index says (so an index at another address
    can't make its plugins look like the project's own). Compatible belongs
    to installable versions only."""
    claimed = claimed if isinstance(claimed, list) else []
    return catalog.badges(official=catalog.is_official(repository),
                          compatible=installable and "compatible" in claimed, featured="featured" in claimed)


def check_index(data) -> dict:
    """An index read from anywhere, checked enough to use. Raises RegistryError."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise RegistryError(f"not a Clips Kitty registry index (format {FORMAT})")
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise RegistryError("the index has no plugin list")
    good = []
    for p in plugins:
        if (isinstance(p, dict) and isinstance(p.get("id"), str) and store.ID_RE.match(p["id"])
                and _owner_repo(p.get("repository")) and isinstance(p.get("versions"), list) and p["versions"]
                and all(isinstance(v, dict) and sources.COMMIT_RE.match(str(v.get("commit", "")))
                        and manifest.VERSION_RE.match(str(v.get("version", ""))) for v in p["versions"])):
            checks = {k: v for k, v in (p.get("checks") or {}).items() if k != "official_repository"} \
                if isinstance(p.get("checks"), dict) else {}
            if catalog.is_official(p["repository"]) and (p.get("checks") or {}).get("official_repository") is True:
                checks["official_repository"] = True
            good.append({**p, "checks": checks,
                         "badges": _trusted_badges(p.get("badges"), p["repository"], installable=True)})
    blocklist = data.get("blocklist") if isinstance(data.get("blocklist"), list) else []
    out = {"format": FORMAT, "plugins": good, "blocklist": [b for b in blocklist if isinstance(b, dict)]}
    entries = data.get("catalog") if isinstance(data.get("catalog"), list) else []
    out["catalog"] = [c for c in (_clean_entry(e) for e in entries) if c]
    out["sections"] = data.get("sections") if isinstance(data.get("sections"), dict) else {}
    if isinstance(data.get("metrics_at"), str):
        out["metrics_at"] = data["metrics_at"][:10]
    counter = data.get("counter") if isinstance(data.get("counter"), dict) else {}
    if catalog.counter_template_ok(counter.get("install")):
        out["counter"] = {"install": counter["install"]}
    return out


def index_urls(config: dict) -> list[str]:
    """The index addresses in settings: `plugins.registry_urls`, https only."""
    urls = ((config or {}).get("plugins") or {}).get("registry_urls") or []
    return [u for u in urls if isinstance(u, str) and u.startswith("https://")] if isinstance(urls, list) else []


def _cache_dir(data_dir) -> Path:
    return store.root(data_dir) / CACHE


def _cache_file(data_dir, url: str) -> Path:
    return _cache_dir(data_dir) / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")


def refresh(data_dir, urls: list[str], *, fetcher=None) -> list[dict]:
    """Fetch each index address into the cache. A failure keeps the last good
    copy and is reported, never raised. `fetcher(url, path)` downloads to a
    file (plugins.sources.download when not given)."""
    import tempfile

    out = []
    for url in urls:
        status = {"url": url, "ok": False, "error": None}
        try:
            with tempfile.TemporaryDirectory(prefix="clipskitty-index-") as tmp:
                path = Path(tmp) / "index.json"
                (fetcher or (lambda u, p: sources.download(u, p, limit=MAX_INDEX_BYTES)))(url, path)
                index = check_index(json.loads(path.read_text(encoding="utf-8")))
            target = _cache_file(data_dir, url)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"url": url, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                          "index": index}), encoding="utf-8")
            status["ok"] = True
        except (OSError, ValueError, sources.SourceError) as e:
            status["error"] = str(e) or e.__class__.__name__
        out.append(status)
    return out


def _cached(data_dir, url: str) -> dict | None:
    try:
        data = json.loads(_cache_file(data_dir, url).read_text(encoding="utf-8"))
        return {"url": url, "fetched_at": data.get("fetched_at"), "index": check_index(data.get("index"))}
    except (OSError, ValueError):
        return None


def indexes(data_dir, urls: list[str], *, bundled: Path | None = None) -> list[dict]:
    """Every index the app knows: the bundled copy first, then each address's cached copy."""
    out = []
    path = bundled or bundled_path()
    try:
        out.append({"url": "bundled", "fetched_at": None,
                    "index": check_index(json.loads(path.read_text(encoding="utf-8")))})
    except (OSError, ValueError):
        pass
    for url in urls:
        cached = _cached(data_dir, url)
        out.append(cached or {"url": url, "fetched_at": None, "index": None})
    return out


def blocklist(data_dir, *, bundled: Path | None = None) -> list[dict]:
    """The block lists of the bundled index and of every cached index, so a
    block keeps applying after an address is removed from settings."""
    entries = []
    for item in indexes(data_dir, [], bundled=bundled):
        entries += item["index"]["blocklist"]
    for path in sorted(_cache_dir(data_dir).glob("*.json")) if _cache_dir(data_dir).is_dir() else ():
        try:
            entries += check_index(json.loads(path.read_text(encoding="utf-8")).get("index"))["blocklist"]
        except (OSError, ValueError, AttributeError):
            continue
    return entries


def blocked_check(data_dir, *, bundled: Path | None = None):
    """blocked(id, version) for the plugin manager and the runner."""
    entries = blocklist(data_dir, bundled=bundled)
    return lambda plugin_id, version: block_entry(entries, plugin_id, version)


def listings(data_dir, urls: list[str], *, bundled: Path | None = None) -> list[dict]:
    """Every listed plugin, once: an id in several indexes comes from the first."""
    seen, out = set(), []
    entries = blocklist(data_dir, bundled=bundled)
    for item in indexes(data_dir, urls, bundled=bundled):
        for p in (item["index"] or {}).get("plugins", []):
            if p["id"] in seen:
                continue
            versions = [v for v in p["versions"] if not block_entry(entries, p["id"], v["version"])]
            if not versions:
                continue
            seen.add(p["id"])
            out.append({**p, "versions": versions, "latest": versions[0]["version"], "index": item["url"]})
    return out


def catalog_entries(data_dir, urls: list[str], *, bundled: Path | None = None) -> tuple[list[dict], dict]:
    """Every directory entry, once (a key in several indexes comes from the
    first), and the sections of the first index that has any."""
    seen, out, sections = set(), [], {}
    for item in indexes(data_dir, urls, bundled=bundled):
        index = item["index"] or {}
        if not sections and index.get("sections"):
            sections = index["sections"]
        for e in index.get("catalog", []):
            if e["key"] in seen:
                continue
            seen.add(e["key"])
            out.append({**e, "index": item["url"]})
    return out, sections


def counter_for(data_dir, urls: list[str], index_url: str, *, bundled: Path | None = None) -> str | None:
    """The install-counter address of the index a listing came from, if it has one."""
    for item in indexes(data_dir, urls, bundled=bundled):
        if item["url"] == index_url:
            return ((item["index"] or {}).get("counter") or {}).get("install")
    return None


def find(data_dir, urls: list[str], plugin_id: str, version: str | None = None, *,
         bundled: Path | None = None) -> tuple[dict, dict]:
    """A listing and one of its versions (the latest when none is named)."""
    for p in listings(data_dir, urls, bundled=bundled):
        if p["id"] == plugin_id:
            for v in p["versions"]:
                if version in (None, v["version"]):
                    return p, v
            raise RegistryError(f"{plugin_id} {version} is not listed")
    raise RegistryError(f"{plugin_id} is not listed in any index")


def listing_tier(listing: dict) -> str:
    """The install tier of a listing: official when its repository is one of
    the Clips Kitty project's own (that is where the files come from)."""
    return "listed-official" if catalog.is_official(listing.get("repository")) else "listed"


def source_for(listing: dict, version: dict) -> dict:
    """The plugin manager's source for a listed version: its repository at its commit."""
    source = {"kind": "git", "url": listing["repository"], "commit": version["commit"]}
    if listing.get("path") not in (None, "", "."):
        source["path"] = listing["path"]
    return source


# ---- search -------------------------------------------------------------------------------


def _norm(text) -> str:
    if isinstance(text, (list, tuple)):
        text = " ".join(str(t) for t in text)
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _fields(p: dict) -> dict:
    return {"name": _norm(p.get("name")), "aliases": _norm(p.get("aliases")),
            "games": _norm([*(p.get("games") or []), *(p.get("sports") or [])]),
            "tags": _norm(p.get("tags")), "events": _norm(p.get("events")),
            "category": _norm([p.get("category") or "", str(p.get("section") or "").replace("/", " ")]),
            "capability": _norm(p.get("capability")), "description": _norm(p.get("description"))}


def _word_score(word: str, fields: dict) -> int:
    """How well one query word matches a listing: the weight of the best field."""
    stem = _stem(word)
    alias = ALIASES.get(word)
    best = 0
    for field, weight in WEIGHTS:
        text = fields[field]
        if not text:
            continue
        tokens = text.split()
        hit = any(t == word or _stem(t) == stem or (len(word) >= 3 and t.startswith(word)) for t in tokens)
        if not hit and alias:
            hit = f" {alias} " in f" {text} "
        if hit:
            best = max(best, weight)
    return best


def search(plugins: list[dict], q: str = "", *, category: str | None = None, tag: str | None = None,
           kind: str | None = None, section: str | None = None) -> list[dict]:
    """Listings or catalog entries matching a query, best first. Every word
    must match somewhere; a word in the name counts most, one in the
    description least. `section` matches a section and the sections inside it."""
    out = []
    words = [w for w in _norm(q).split() if w not in STOPWORDS]
    phrase = _norm(q)
    for p in plugins:
        if category and p.get("category") != category:
            continue
        if section and not (p.get("section") == section or str(p.get("section") or "").startswith(section + "/")):
            continue
        if tag and tag not in (p.get("tags") or []):
            continue
        if kind and p.get("kind", "pipeline") != kind:
            continue
        fields = _fields(p)
        scores = [_word_score(w, fields) for w in words]
        if words and not all(scores):
            continue
        score = sum(scores) + (10 if phrase and phrase in fields["name"] else 0)
        out.append((score, p))
    out.sort(key=lambda t: (-t[0], str(t[1].get("name", "")).lower()))
    return [p for _, p in out]
