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
     "catalog": [{"id": "apps/example-app", "kind": "app", "name": ..., "relationship": "related",
                  "license": "MIT", "source": {"github": ...}, "badges": [...], "metrics": {...}, ...}],
     "sections": {"app": {"sections": [{"id", "title", "description"}], "wanted": [...]}, ...},
     "metrics_at": "2026-10-07",                       # when the numbers were read, if ever
     "counter": {"install": "https://...{asset}..."},  # optional: where installs are counted
     "blocklist": [{"id": ..., "versions": "*" or [...], "severity": "blocked" | "delisted",
                    "reason": ..., "date": ...}]}

"plugins" are the installable listings (pipelines today); "catalog" is
everything else the directory lists. build_index() makes the index
(scripts/build_registry_index.py runs it in CI). The client reads three kinds
of index, caches the last good copy of each fetched one under
<data_dir>/plugins/cache/, works offline from that cache, and searches
locally:

- the copy bundled with the app (awesome-clips-kitty/index.json);
- Clips Kitty's online list (ONLINE_URL): that same file on the main branch
  of the project's repository, which changes when a change to the catalog is
  merged there (and with the weekly numbers). The app fetches it when the
  Marketplace opens, or the app starts with a pipeline installed, and its copy
  is a day old (a switch in the Marketplace turns that off), or when the
  person presses Check for new pipelines. It only adds: new listings and
  entries, new versions of a bundled listing, new sections, and blocks;
- any addresses in settings (`plugins.registry_urls`, none by default).

Nothing here runs plugin code; the index build clones only the history of
listed repositories (no files), to check where each commit is.

Only the bundled index is trusted to label (D22): its listings were checked
when this project built it (every commit on a branch of its repository), and
it ships with the app. Labels (✓ Official, ✓ Compatible, ★ Featured),
compatibility records and the install counter come from it alone. The online
list brings new listings, newer versions of listed ones, new directory
entries and new blocks between releases; what it adds shows as Community
until a release bundles it, and the project's own listings come only with
the app (as Community they would read as someone else's). Any other index is
a list someone else keeps, so its listings and entries show as Community and
are never counted. Block lists add up across every index ever cached.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import logging
import re
import sys
import threading
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

log = logging.getLogger(__name__)

# Why a list couldn't be fetched, as the Marketplace says it. A fixed sentence,
# never the error's own text: that can carry file paths and library internals,
# so the details go to the engine's log instead.
WHY_OFFLINE = "Couldn't connect. Check this PC's internet connection."
WHY_NOT_FOUND = "Nothing is at that address yet."
WHY_SERVER = "The website that holds the list didn't answer properly."
WHY_TOO_LARGE = "The list is larger than Clips Kitty accepts."
WHY_UNREADABLE = "That address doesn't hold a list Clips Kitty can read."
WHY_NOT_HTTPS = "Only https:// addresses can be checked."
WHY_NOT_SAVED = "The list couldn't be saved on this PC."
WHYS = (WHY_OFFLINE, WHY_NOT_FOUND, WHY_SERVER, WHY_TOO_LARGE, WHY_UNREADABLE, WHY_NOT_HTTPS, WHY_NOT_SAVED)

# One check of the online list at a time (the Marketplace opening and the
# button can ask together; FastAPI runs the routes on several threads).
_ONLINE_LOCK = threading.RLock()

# Clips Kitty's online list: the catalog's index on the project's main branch
# (D29). It answers once the catalog is merged there.
ONLINE_URL = "https://raw.githubusercontent.com/ColinGPT9/clips-studio/main/awesome-clips-kitty/index.json"
CHECKS_FILE = "listing-checks.json"  # the person's switch and the last check, in <data_dir>/plugins/
CHECK_EVERY = 24 * 3600              # an automatic check when the copy is older than this
RETRY_AFTER = 3600                   # and no check was tried for this long (offline: not on every open)
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


def build_index(catalog_dir: Path, *, fetch=fetch_raw, on_branch=None) -> tuple[dict, list[str]]:
    """Build an index from a catalog folder (one with a registry/ folder in
    it). Returns (index, problems); a listing or entry with any problem is
    left out. `fetch(url) -> text` reads a manifest at a commit (fetch_raw,
    or a fixture reader in tests). `on_branch(owner, repo, commit) -> bool`
    says whether a commit is on one of the repository's own branches or tags
    (commit_on_branch; scripts/build_registry_index.py always passes it). When
    it isn't given, that check is not made and listings don't claim it."""
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
            item, found = _build_listing(listing, label, kind, fetch=fetch, on_branch=on_branch,
                                         blocklist=blocklist, metrics=metrics, compatibility=compatibility)
            problems += found
            if item:
                plugins.append(item)

    entries, found = catalog.read_entries(catalog_dir, sections) if has_sections else ([], [])
    problems += found
    listed_ids = {p["id"] for p in plugins}
    finished = []
    for e in entries:
        if e.get("adapter") and e["adapter"] not in listed_ids:
            problems.append(f"{e['id']}: adapter: {e['adapter']} is not a listing here")
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
                   compatibility: dict, on_branch=None) -> tuple[dict | None, list[str]]:
    """One listing as the index carries it, with the manifest of every
    version fetched at its commit and checked."""
    problems: list[str] = []
    owner, repo = _owner_repo(listing["repository"])
    folder = listing.get("path", ".")
    versions, bad = [], False
    for v in listing["versions"]:
        # GitHub serves a fork's commits under the parent repository's address
        # too, so a hash alone doesn't say whose code it is.
        if on_branch is not None:
            try:
                ours = on_branch(owner, repo, v["commit"])
            except Exception as e:  # reported, like a manifest that can't be read
                problems.append(f"{label}: {v['version']}: couldn't check where commit {v['commit'][:7]} comes "
                                f"from ({e})")
                bad = True
                continue
            if not ours:
                problems.append(f"{label}: {v['version']}: commit {v['commit'][:7]} is not on a branch or tag of "
                                f"{owner}/{repo} (it may be from a fork)")
                bad = True
                continue
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
        versions.append((manifest.version_key(v["version"]), entry, data))
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
                       "commit_pinned": True, "public_at_commit": True,
                       **({"commit_on_branch": True} if on_branch is not None else {})}}
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


def _clean_entry(e, *, ours: bool) -> dict | None:
    """A catalog entry read from an index, or None when it isn't one. Only
    the fields the Marketplace shows are kept, each only when it has the
    right shape, and links only when they are what they claim to be."""
    if not isinstance(e, dict) or e.get("kind") not in catalog.DIRECTORY_KINDS.values():
        return None
    entry_id = e.get("id")
    folder = catalog.FOLDER_OF_KIND[e["kind"]]
    if not isinstance(entry_id, str) or not entry_id.startswith(folder + "/") \
            or not catalog.SLUG_RE.match(entry_id[len(folder) + 1:]):
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
    if catalog.homepage_problem(source.get("homepage")) is None:
        clean["homepage"] = source["homepage"]
    if catalog.download_problem(source.get("download"), github=clean.get("github"),
                                homepage=clean.get("homepage")) is None:
        clean["download"] = source["download"]
    if not clean:
        return None
    out = {"id": entry_id, "kind": e["kind"], "slug": entry_id[len(folder) + 1:], "name": e["name"][:80],
           "license": e["license"][:100], "source": clean}
    for key in ("description", "section", "relationship", "uses", "license_note", "runs", "warning", "added",
                "checked", "adapter"):
        if isinstance(e.get(key), str):
            out[key] = e[key][:300]
    if out.get("adapter") and not store.ID_RE.match(out["adapter"]):
        out.pop("adapter")
    if e.get("setup") in catalog.SETUPS:
        out["setup"] = e["setup"]
    for key in ("platforms", "tags", "games", "sports"):
        if isinstance(e.get(key), list):
            out[key] = [str(x)[:40] for x in e[key][:10] if isinstance(x, str)]
    models = e.get("models") if isinstance(e.get("models"), list) else []
    out["models"] = [{"huggingface": m["huggingface"]} for m in models[:10] if isinstance(m, dict)
                     and isinstance(m.get("huggingface"), str) and catalog.HF_ID_RE.match(m["huggingface"])]
    out["metrics"] = _clean_metrics(e.get("metrics"), ours=ours)
    if _github_link(e.get("discussions_url")):
        out["discussions_url"] = e["discussions_url"]
    if ours and isinstance(e.get("featured"), dict):
        out["featured"] = {k: str(e["featured"][k])[:200] for k in ("reason", "date") if k in e["featured"]}
    out["badges"] = _labels(e.get("badges"), clean.get("github"), ours=ours, installable=False)
    return out


def _github_link(url) -> bool:
    return isinstance(url, str) and url.startswith("https://github.com/") and catalog._https(url)


def _clean_metrics(m, *, ours: bool = True) -> dict:
    """The numbers an index shows for a listing or entry, each only when it
    is a number or a date (the Marketplace prints them as they are). Install
    counts come only from the bundled index, the only one that counts (D22)."""
    m = m if isinstance(m, dict) else {}
    out: dict = {}
    gh = m.get("github") if isinstance(m.get("github"), dict) else None
    if gh:
        out["github"] = {k: gh[k] for k in ("stars", "discussions") if _count(gh.get(k))}
        out["github"].update({k: gh[k] for k in ("archived", "has_discussions") if isinstance(gh.get(k), bool)})
        if isinstance(gh.get("pushed_at"), str):
            out["github"]["pushed_at"] = gh["pushed_at"][:10]
    models = m.get("models") if isinstance(m.get("models"), dict) else {}
    clean_models = {}
    for model_id, numbers in list(models.items())[:10]:
        if isinstance(model_id, str) and catalog.HF_ID_RE.match(model_id) and isinstance(numbers, dict):
            clean_models[model_id] = {k: numbers[k] for k in ("downloads", "likes") if _count(numbers.get(k))}
            if isinstance(numbers.get("last_modified"), str):
                clean_models[model_id]["last_modified"] = numbers["last_modified"][:10]
    if clean_models:
        out["models"] = clean_models
    if ours and _count(m.get("installs")):
        out["installs"] = m["installs"]
    if isinstance(m.get("stale"), str):
        out["stale"] = m["stale"][:100]
    return out


def _count(n) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and n >= 0


def _labels(claimed, repository, *, ours: bool, installable: bool) -> list[str]:
    """The labels a listing or entry shows. Only the bundled index's count:
    it was built by this project, so its Compatible and Featured come from
    real records and its Official repositories had every commit checked.
    Official also needs the repository to be the project's own, whatever an
    index says, and Compatible belongs to installable versions only. Any
    other index's listings are Community, whatever they claim."""
    if not ours:
        return catalog.badges(official=False)
    claimed = claimed if isinstance(claimed, list) else []
    return catalog.badges(official=catalog.is_official(repository),
                          compatible=installable and "compatible" in claimed, featured="featured" in claimed)


def _clean_sections(sections, *, ours: bool) -> dict:
    """An index's sections, as sections_of merges them: for an index this
    project didn't build with the app, only well-formed kinds and sections."""
    if not isinstance(sections, dict):
        return {}
    if ours:
        return sections
    out = {}
    for kind, value in sections.items():
        if not (isinstance(kind, str) and isinstance(value, dict)):
            continue
        out[kind] = {**value, "sections": [x for x in _list(value.get("sections")) if isinstance(x, dict)
                                           and isinstance(x.get("id"), str) and isinstance(x.get("title"), str)]}
        if "wanted" in value:
            out[kind]["wanted"] = [x for x in _list(value["wanted"]) if isinstance(x, str)]
    return out


def check_index(data, *, ours: bool = False) -> dict:
    """An index read from anywhere, checked enough to use. Raises RegistryError.
    `ours` is for the bundled index only, the one this project built (see the module docstring)."""
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
            claimed = p["checks"] if isinstance(p.get("checks"), dict) else {}
            checks = {k: v for k, v in claimed.items()
                      if isinstance(k, str) and v is True and k != "official_repository"}
            if ours and catalog.is_official(p["repository"]) and claimed.get("official_repository") is True:
                checks["official_repository"] = True
            item = {**p, "checks": checks, "metrics": _clean_metrics(p.get("metrics"), ours=ours),
                    "badges": _labels(p.get("badges"), p["repository"], ours=ours, installable=True)}
            for key in ("aliases", "tags", "games", "sports", "events"):  # search reads these as word lists
                if key in item and not (isinstance(item[key], list) and all(isinstance(x, str) for x in item[key])):
                    item.pop(key)
            if not _github_link(item.get("discussions_url")):
                item.pop("discussions_url", None)
            if not ours:
                item.pop("featured", None)
                item["versions"] = [{k: v for k, v in version.items() if k != "compatibility"}
                                    for version in p["versions"]]
            good.append(item)
    blocklist = data.get("blocklist") if isinstance(data.get("blocklist"), list) else []
    out = {"format": FORMAT, "plugins": good, "blocklist": [b for b in blocklist if isinstance(b, dict)]}
    entries = data.get("catalog") if isinstance(data.get("catalog"), list) else []
    out["catalog"] = [c for c in (_clean_entry(e, ours=ours) for e in entries) if c]
    out["sections"] = _clean_sections(data.get("sections"), ours=ours)
    if isinstance(data.get("metrics_at"), str):
        out["metrics_at"] = data["metrics_at"][:10]
    counter = data.get("counter") if isinstance(data.get("counter"), dict) else {}
    if ours and catalog.counter_template_ok(counter.get("install")):
        out["counter"] = {"install": counter["install"]}
    return out


def index_urls(config: dict) -> list[str]:
    """The index addresses in settings: `plugins.registry_urls`, https only
    (Clips Kitty's online list is always read, so it isn't one of these)."""
    urls = ((config or {}).get("plugins") or {}).get("registry_urls") or []
    return [u for u in urls if isinstance(u, str) and u.startswith("https://") and u != ONLINE_URL] \
        if isinstance(urls, list) else []


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
        if not url.startswith("https://"):
            status["error"] = WHY_NOT_HTTPS
            out.append(status)
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="clipskitty-index-") as tmp:
                path = Path(tmp) / "index.json"
                (fetcher or (lambda u, p: sources.download(u, p, limit=MAX_INDEX_BYTES)))(url, path)
                index = check_index(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:  # SourceError is a ValueError
            log.warning("Couldn't fetch the plugin list %s: %s", url, e)
            status["error"] = _why(e)
            out.append(status)
            continue
        target = _cache_file(data_dir, url)
        try:
            _replace(target, json.dumps({"url": url, "fetched_at": _stamp(time.time()), "index": index}))
            status["ok"] = True
        except OSError as e:
            log.warning("Couldn't save the plugin list %s to %s: %s", url, target, e)
            status["error"] = WHY_NOT_SAVED
        out.append(status)
    return out


def _why(error: Exception) -> str:
    """The fixed sentence for a failed fetch (the WHY_ constants)."""
    import urllib.error

    cause = error.__cause__ if isinstance(error.__cause__, OSError) else error
    if isinstance(cause, urllib.error.HTTPError):
        return WHY_NOT_FOUND if cause.code in (404, 410) else WHY_SERVER
    if isinstance(cause, OSError):  # no connection, a timeout, a refused or dropped connection
        return WHY_OFFLINE
    if isinstance(error, sources.TooLarge):
        return WHY_TOO_LARGE
    return WHY_UNREADABLE  # not JSON, or not a Clips Kitty index (check_index says why, in the log)


def _replace(path: Path, text: str) -> None:
    """Write a file whole: a reader never sees half of it, and two writers
    at once each finish (the last one's copy stays)."""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, part = tempfile.mkstemp(dir=path.parent, prefix=path.stem + ".", suffix=".part")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(part, path)
    except BaseException:
        Path(part).unlink(missing_ok=True)
        raise


def _stamp(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _seconds(stamp) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return None


def _checks_path(data_dir) -> Path:
    return store.root(data_dir) / CHECKS_FILE


def _checks(data_dir) -> dict:
    try:
        data = json.loads(_checks_path(data_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_checks(data_dir, data: dict) -> None:
    _replace(_checks_path(data_dir), json.dumps(data))


def online_checks_on(data_dir) -> bool:
    """Whether the Marketplace checks Clips Kitty's online list by itself
    when it opens: on unless the person switched it off."""
    value = _checks(data_dir).get("automatic")
    return value if isinstance(value, bool) else True


def set_online_checks(data_dir, on: bool) -> None:
    with _ONLINE_LOCK:
        _write_checks(data_dir, {**_checks(data_dir), "automatic": bool(on)})


def online_status(data_dir, *, bundled: Path | None = None) -> dict:
    """What the Marketplace says about Clips Kitty's online list: whether it
    is checked automatically, when a copy was last fetched, whether that copy
    is in use (one older than the list that came with the app is set aside),
    and when the last try was and how it went (`error` is None when it worked)."""
    checks = _checks(data_dir)
    cached = _cached(data_dir, ONLINE_URL)
    in_use = bool(cached) and next(i for i in indexes(data_dir, [], bundled=bundled)
                                   if i["kind"] == "online")["index"] is not None
    error = checks.get("error")
    return {"url": ONLINE_URL, "automatic": online_checks_on(data_dir),
            "fetched_at": (cached or {}).get("fetched_at"), "in_use": in_use,
            "tried_at": checks["tried_at"] if isinstance(checks.get("tried_at"), str) else None,
            "error": error if error in WHYS else (WHY_UNREADABLE if isinstance(error, str) else None)}


def _within(stamp, seconds: float, now: float) -> bool:
    at = _seconds(stamp)
    return at is not None and 0 <= now - at < seconds


def online_due(data_dir, *, now: float | None = None) -> bool:
    """Whether opening the Marketplace should check the online list: the
    switch is on, the copy is at least a day old (or there is none), and no
    check was tried in the last hour, so an offline PC isn't asked on every open."""
    if not online_checks_on(data_dir):
        return False
    now = time.time() if now is None else now
    if _within((_cached(data_dir, ONLINE_URL) or {}).get("fetched_at"), CHECK_EVERY, now):
        return False
    checks = _checks(data_dir)
    # Offline: try again in an hour. A list that isn't there or can't be read
    # won't change by itself within the hour, so that waits a day.
    wait = RETRY_AFTER if checks.get("error") in (None, WHY_OFFLINE, WHY_SERVER, WHY_NOT_SAVED) else CHECK_EVERY
    return not _within(checks.get("tried_at"), wait, now)


def refresh_online(data_dir, *, fetcher=None, now: float | None = None) -> dict:
    """Fetch Clips Kitty's online list into the cache now, and note when and
    how it went. Like refresh(), a failure keeps the last good copy."""
    with _ONLINE_LOCK:
        return _refresh_online(data_dir, fetcher=fetcher, now=now)


def refresh_online_if_due(data_dir, *, fetcher=None, now: float | None = None) -> dict | None:
    """refresh_online() when online_due() says so, else None. One check at a
    time: a second caller waits, then finds the first one's answer and doesn't fetch again."""
    with _ONLINE_LOCK:
        if not online_due(data_dir, now=now):
            return None
        return _refresh_online(data_dir, fetcher=fetcher, now=now)


def _refresh_online(data_dir, *, fetcher=None, now: float | None = None) -> dict:
    (status,) = refresh(data_dir, [ONLINE_URL], fetcher=fetcher)
    _write_checks(data_dir, {**_checks(data_dir), "tried_at": _stamp(time.time() if now is None else now),
                             "error": status["error"]})
    return status


def _cached(data_dir, url: str) -> dict | None:
    try:
        data = json.loads(_cache_file(data_dir, url).read_text(encoding="utf-8"))
        return {"url": url, "fetched_at": data.get("fetched_at"), "index": check_index(data.get("index"))}
    except (OSError, ValueError):
        return None


def _older(index: dict, than: dict) -> bool:
    """Whether `index` is an older list than `than`, by the date its numbers
    were read (the weekly job writes it; the bundled list's is at least that new)."""
    a, b = index.get("metrics_at"), than.get("metrics_at")
    return isinstance(a, str) and isinstance(b, str) and a < b


def indexes(data_dir, urls: list[str], *, bundled: Path | None = None) -> list[dict]:
    """Every index the app knows, in the order they count: the bundled copy,
    Clips Kitty's online list, then each settings address's cached copy.
    `kind` is "bundled", "online" or "other"; `index` is None for one never fetched."""
    out = []
    path = bundled or bundled_path()
    ours = None
    try:
        ours = check_index(json.loads(path.read_text(encoding="utf-8")), ours=True)
        out.append({"url": "bundled", "kind": "bundled", "fetched_at": None, "index": ours})
    except (OSError, ValueError):
        pass
    online = _cached(data_dir, ONLINE_URL)
    if online and ours and _older(online["index"], ours):
        online = None  # a copy from before this release's list adds nothing to it
    out.append({**(online or {"url": ONLINE_URL, "fetched_at": None, "index": None}), "kind": "online"})
    for url in urls:
        cached = _cached(data_dir, url)
        out.append({**(cached or {"url": url, "fetched_at": None, "index": None}), "kind": "other"})
    return out


def blocklist(data_dir, *, bundled: Path | None = None) -> list[dict]:
    """The block lists of the bundled index and of every cached index, so a
    block keeps applying after an address is removed from settings."""
    entries = []
    for item in indexes(data_dir, [], bundled=bundled):
        entries += (item["index"] or {}).get("blocklist", [])
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


def _project_own(repository) -> bool:
    return bool(isinstance(repository, str) and catalog.is_official(repository))


def listings(data_dir, urls: list[str], *, bundled: Path | None = None) -> list[dict]:
    """Every listed plugin, once. An id in several indexes comes from the
    first (bundled, online, then settings order); Clips Kitty's online list
    only adds versions to a bundled listing (_add_online). The online list
    never brings the project's own listings: they come with the app, and from
    that list they would show as Community. Each version says which index it
    came from (`index`), which decides its install tier and whether installing
    it is counted."""
    picked: dict[str, dict] = {}
    entries = blocklist(data_dir, bundled=bundled)
    for item in indexes(data_dir, urls, bundled=bundled):
        for p in (item["index"] or {}).get("plugins", []):
            if item["kind"] == "online" and _project_own(p.get("repository")):
                continue
            versions = [{**v, "index": item["url"]} for v in p["versions"]
                        if not block_entry(entries, p["id"], v["version"])]
            if not versions:
                continue
            have = picked.get(p["id"])
            if have is None:
                picked[p["id"]] = {**p, "versions": versions, "latest": versions[0]["version"], "index": item["url"]}
            elif item["kind"] == "online" and have["index"] == "bundled":
                picked[p["id"]] = _add_online(have, p, versions)
    return list(picked.values())


def _same_place(a: dict, b: dict) -> bool:
    def place(p):
        return (str(p.get("repository", "")).lower().rstrip("/").removesuffix(".git"), p.get("path") or ".")
    return place(a) == place(b)


def _add_online(have: dict, online: dict, versions: list[dict]) -> dict:
    """A bundled listing with the versions Clips Kitty's online list adds
    (D29): only versions the bundled list doesn't have, so a version it has
    keeps its commit, its compatibility record and its counting. Nothing is
    added to one of the project's own listings, or from a listing whose code
    is somewhere else. When the newest version is an added one, the listing
    shows what that version is (name, permissions...) and loses ✓ Compatible,
    which belonged to the bundled newest version."""
    if _project_own(have.get("repository")) or not _same_place(have, online):
        return have
    known = {v["version"] for v in have["versions"]}
    added = [v for v in versions if v["version"] not in known]
    if not added:
        return have
    every = sorted(have["versions"] + added, key=lambda v: manifest.version_key(v["version"]), reverse=True)
    out = {**have, "versions": every, "latest": every[0]["version"]}
    if every[0]["index"] != "bundled":
        out.update({k: online[k] for k in (*SHOWN, "settings") if k in online})
        for k in (*SHOWN, "settings"):
            if k not in online:
                out.pop(k, None)
        out["badges"] = [b for b in have.get("badges") or [] if b != "compatible"]
    return out


def _list(value) -> list:
    return value if isinstance(value, list) else []


def sections_of(known: list[dict]) -> dict:
    """The sections to show: the first index's that has any, plus the ones
    Clips Kitty's online list adds (a new game's section, say), by id."""
    out: dict = {}
    for item in known:
        index = item["index"] or {}
        found = index.get("sections") if isinstance(index.get("sections"), dict) else {}
        if not out and found:
            out = {kind: dict(v) for kind, v in found.items() if isinstance(v, dict)}
        elif item["kind"] == "online":
            for kind, v in found.items():
                if not isinstance(v, dict):
                    continue
                if kind not in out:
                    out[kind] = dict(v)
                    continue
                mine = [s for s in _list(out[kind].get("sections")) if isinstance(s, dict)]
                ids = {s.get("id") for s in mine}
                out[kind]["sections"] = mine + [s for s in _list(v.get("sections"))
                                                if isinstance(s, dict) and s.get("id") not in ids]
    return out


def catalog_entries(data_dir, urls: list[str], *, bundled: Path | None = None) -> tuple[list[dict], dict]:
    """Every directory entry, once (an id in several indexes comes from the
    first; the online list never brings the project's own), and the sections."""
    seen, out = set(), []
    known = indexes(data_dir, urls, bundled=bundled)
    for item in known:
        for e in (item["index"] or {}).get("catalog", []):
            if e["id"] in seen or (item["kind"] == "online" and _project_own(e["source"].get("github"))):
                continue
            seen.add(e["id"])
            out.append({**e, "index": item["url"]})
    return out, sections_of(known)


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


def listing_tier(listing: dict, version: dict | None = None) -> str:
    """The install tier of a listing, or of one of its versions: official when
    the bundled index lists it in one of the Clips Kitty project's own
    repositories (the build checked each commit is on that repository's
    branches, so the code is the project's)."""
    index = (version or {}).get("index", listing.get("index"))
    return "listed-official" if index == "bundled" and catalog.is_official(listing.get("repository")) else "listed"


_HISTORY: dict[str, set[str]] = {}


def commit_on_branch(owner: str, repo: str, commit: str, *, git: str | None = None) -> bool:
    """Whether a commit is in the history of the repository's own branches or
    tags. Fetches the commit history only (no files) into a temporary
    folder, once per repository per run."""
    import shutil
    import subprocess
    import tempfile

    key = f"{owner}/{repo}".lower()
    if key not in _HISTORY:
        git = git or shutil.which("git")
        if not git:
            raise RegistryError("git is needed to check where listed commits come from")
        with tempfile.TemporaryDirectory(prefix="clipskitty-history-") as tmp:
            subprocess.run([git, "init", "-q", "--bare", tmp], check=True, capture_output=True)
            subprocess.run([git, "-C", tmp, "fetch", "-q", "--filter=tree:0", "--no-tags",
                            f"https://github.com/{owner}/{repo}.git",
                            "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"],
                           check=True, capture_output=True, timeout=600)
            # rev-list reads commits only, so it never asks GitHub for a missing object.
            listed = subprocess.run([git, "-C", tmp, "rev-list", "--all"], check=True, capture_output=True,
                                    text=True).stdout.split()
        _HISTORY[key] = set(listed)
    return commit.lower() in _HISTORY[key]


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
