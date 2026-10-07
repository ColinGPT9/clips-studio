"""Awesome Clips Kitty: the catalog of apps, pipelines, plugins, models,
workflows, integrations and tools around Clips Kitty.

The catalog is a folder (awesome-clips-kitty/ in this repository, meant to
become a repository of its own) with three kinds of files:

    registry/catalog.yaml              catalog-wide settings (the install counter's address)
    registry/sections.yaml             the sections of each kind, in order, and the niches wanted
    registry/<kind>s/<slug>.yaml       one entry per app, model, workflow, integration or tool
    registry/pipelines/<publisher>/<name>.yaml, registry/plugins/...
                                       installable listings (plugins/registry.py checks those)
    stats/metrics.json                 written by a scheduled job: GitHub stars, last commit,
                                       discussions, Hugging Face downloads and likes, installs
    stats/compatibility.json           written by the compatibility check (scripts/check_compatibility.py)

and two built ones: index.json, which the app reads, and README.md, which
people read. Both come from scripts/build_registry_index.py.

Three things are kept apart on purpose:
- the relationship to Clips Kitty: "built-for" (runs inside it; only an
  installable listing), "built-with" (a separate app or tool using its API or
  SDK), "related" (relevant, not integrated);
- the badges: "official" (made by the Clips Kitty project) or "community" (the
  default), "compatible" (an installable version passed the automated checks:
  a technical label, not a review), "featured" (picked by a maintainer);
- the numbers: Clips Kitty installs, GitHub stars, and a model's Hugging Face
  downloads and likes, each its own figure.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from plugins import sources
from plugins._sdk import manifest

# Folder name -> kind. The installable ones are listings (plugins/registry.py).
DIRECTORY_KINDS = {"apps": "app", "models": "model", "workflows": "workflow", "integrations": "integration",
                   "tools": "tool"}
INSTALLABLE_KINDS = {"pipelines": "pipeline", "plugins": "plugin"}
KIND_ORDER = ("app", "pipeline", "plugin", "model", "workflow", "integration", "tool")
KIND_TITLES = {"app": "Apps", "pipeline": "Pipelines", "plugin": "Plugins", "model": "Models",
               "workflow": "Workflows", "integration": "Integrations", "tool": "Tools"}
FOLDER_OF_KIND = {v: k for k, v in {**DIRECTORY_KINDS, **INSTALLABLE_KINDS}.items()}

RELATIONSHIPS = {
    "built-for": "Built for Clips Kitty",
    "built-with": "Built with Clips Kitty",
    "related": "Related",
}
BADGES = {
    "official": "✓ Official",
    "compatible": "✓ Compatible",
    "featured": "★ Featured",
    "community": "Community",
}
BADGE_MEANING = {
    "official": "Made and maintained by the Clips Kitty project.",
    "compatible": "This version passed Clips Kitty's automated compatibility checks: the manifest is valid, "
                  "it installs, its requirements are met, and it runs on a sample video and gives an answer "
                  "Clips Kitty accepts. A technical label, not a security review.",
    "featured": "Picked by hand by a Clips Kitty maintainer as a notable project.",
    "community": "Made by someone outside the Clips Kitty project. Nobody has reviewed its code.",
}

# GitHub owners whose projects are the Clips Kitty project's own.
OFFICIAL_OWNERS = ("colingpt9",)

PLATFORMS = ("windows", "macos", "linux", "web", "android", "ios")
RUNS = ("local", "cloud", "both")
USES = ("api", "sdk", "both")
ENTRY_FIELDS = ("name", "description", "section", "relationship", "license", "license_note", "source", "models",
                "platforms", "runs", "tags", "games", "sports", "adapter", "uses", "featured", "warning", "added",
                "checked")
SOURCE_FIELDS = ("github", "huggingface", "url", "path")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SECTION_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*(/[a-z0-9]+(-[a-z0-9]+)*)?$")
HF_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STALE_DAYS = 365

GENERATED_START = "<!-- generated: everything from here to the end marker comes from registry/ -->"
GENERATED_END = "<!-- generated: end -->"


class CatalogError(ValueError):
    pass


# ---- reading and checking ------------------------------------------------------------------


def _text(problems: list, where: str, value, *, limit: int, required: bool = True) -> bool:
    if value is None and not required:
        return False
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where}: must be text")
        return False
    if len(value) > limit:
        problems.append(f"{where}: at most {limit} characters")
        return False
    return True


def _https(value) -> bool:
    return isinstance(value, str) and value.startswith("https://") and " " not in value and len(value) <= 300


def _short_list(problems: list, where: str, value, *, limit: int = 10, item_limit: int = 40,
                choices: tuple | None = None) -> None:
    if value is None:
        return
    if not isinstance(value, list) or len(value) > limit or not all(
            isinstance(v, str) and 0 < len(v) <= item_limit for v in value):
        problems.append(f"{where}: at most {limit} short words")
        return
    if choices:
        bad = [v for v in value if v not in choices]
        if bad:
            problems.append(f"{where}: {', '.join(bad)} not one of {', '.join(choices)}")


def github_owner(url) -> str | None:
    m = sources.GITHUB_RE.match(url) if isinstance(url, str) else None
    return m.group(1) if m else None


def github_repo(url) -> str | None:
    """"owner/repo" of a GitHub repository URL, or None."""
    m = sources.GITHUB_RE.match(url) if isinstance(url, str) else None
    return f"{m.group(1)}/{m.group(2)}" if m else None


def is_official(github_url) -> bool:
    owner = github_owner(github_url)
    return bool(owner) and owner.lower() in OFFICIAL_OWNERS


def section_ids(sections: dict, kind: str) -> list[str]:
    return [s["id"] for s in (sections.get(kind) or {}).get("sections", [])]


def check_sections(data) -> list[str]:
    """Problems with sections.yaml: per kind, an ordered list of sections, and the niches wanted."""
    if not isinstance(data, dict):
        return ["sections.yaml: a mapping of kind to its sections"]
    problems = []
    for kind, spec in data.items():
        where = f"sections.yaml: {kind}"
        if kind not in KIND_ORDER:
            problems.append(f"{where}: not a kind ({', '.join(KIND_ORDER)})")
            continue
        if not isinstance(spec, dict):
            problems.append(f"{where}: must have sections")
            continue
        for key in spec:
            if key not in ("sections", "wanted"):
                problems.append(f"{where}.{key}: unknown field")
        seen = set()
        for i, s in enumerate(spec.get("sections") or []):
            w = f"{where}.sections[{i}]"
            if not isinstance(s, dict):
                problems.append(f"{w}: must be a mapping")
                continue
            for key in s:
                if key not in ("id", "title", "description", "relationship"):
                    problems.append(f"{w}.{key}: unknown field")
            sid = s.get("id")
            if not isinstance(sid, str) or not SECTION_RE.match(sid):
                problems.append(f"{w}.id: a lowercase name like gaming or gaming/valorant")
            elif sid in seen:
                problems.append(f"{w}.id: {sid} is listed twice")
            else:
                seen.add(sid)
                if "/" in sid and sid.split("/")[0] not in seen:
                    problems.append(f"{w}.id: its parent {sid.split('/')[0]} must come first")
            _text(problems, f"{w}.title", s.get("title"), limit=60)
            _text(problems, f"{w}.description", s.get("description"), limit=300, required=False)
            if "relationship" in s and s["relationship"] not in RELATIONSHIPS:
                problems.append(f"{w}.relationship: one of {', '.join(RELATIONSHIPS)}")
        for i, w_item in enumerate(spec.get("wanted") or []):
            if not isinstance(w_item, dict) or not isinstance(w_item.get("section"), str) or not isinstance(
                    w_item.get("idea"), str) or not w_item["idea"].strip() or len(w_item["idea"]) > 200:
                problems.append(f"{where}.wanted[{i}]: section and idea (one line)")
            elif w_item["section"] not in seen:
                problems.append(f"{where}.wanted[{i}].section: {w_item['section']} is not a section of {kind}")
    return problems


def check_featured(problems: list, where: str, value) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) - {"reason", "date"}:
        problems.append(f"{where}: reason and date")
        return
    _text(problems, f"{where}.reason", value.get("reason"), limit=200)
    if not DATE_RE.match(str(value.get("date", ""))):
        problems.append(f"{where}.date: YYYY-MM-DD")


def check_entry(data, kind: str, slug: str, sections: dict) -> list[str]:
    """Problems with one directory entry (an app, model, workflow, integration or tool)."""
    if not isinstance(data, dict):
        return ["an entry is a mapping of fields"]
    problems: list[str] = []
    for key in data:
        if key not in ENTRY_FIELDS:
            problems.append(f"{key}: unknown field")
    if not SLUG_RE.match(slug) or len(slug) > 60:
        problems.append("the file name must be a lowercase-and-hyphens name, like my-app.yaml")
    _text(problems, "name", data.get("name"), limit=80)
    _text(problems, "description", data.get("description"), limit=300)
    section = data.get("section")
    known = section_ids(sections, kind)
    if not isinstance(section, str) or section not in known:
        problems.append(f"section: one of {', '.join(known) or '(none defined for this kind)'}")
    relationship = data.get("relationship")
    if relationship not in ("built-with", "related"):
        problems.append("relationship: built-with (it uses Clips Kitty's API or SDK) or related (it doesn't yet); "
                        "built-for is for installable plugins, which are listings")
    else:
        wanted = next((s.get("relationship") for s in (sections.get(kind) or {}).get("sections", [])
                       if s["id"] == section), None)
        if wanted and wanted != relationship:
            problems.append(f"relationship: the section {section} is for {wanted} projects")
    if kind == "app" and section == "works-with" and not data.get("adapter"):
        problems.append("adapter: an app in works-with names the listed pipeline or plugin that runs it in Clips Kitty")
    if relationship == "built-with" and data.get("uses") not in USES:
        problems.append("uses: what it uses of Clips Kitty: api, sdk or both")
    if "uses" in data and relationship != "built-with":
        problems.append("uses: only for built-with projects")
    if not isinstance(data.get("license"), str) or not re.match(manifest.LICENSE_PATTERN, data["license"]) \
            or len(data["license"]) > 100 or data["license"] in ("NOASSERTION", "NONE"):
        problems.append("license: the project's licence as an SPDX expression, such as MIT or GPL-3.0-or-later "
                        "(an entry needs an identifiable licence)")
    _text(problems, "license_note", data.get("license_note"), limit=300, required=False)
    source = data.get("source")
    if not isinstance(source, dict) or not any(k in source for k in ("github", "huggingface", "url")):
        problems.append("source: github, huggingface or url")
    else:
        for key in source:
            if key not in SOURCE_FIELDS:
                problems.append(f"source.{key}: unknown field")
        if "github" in source and not github_repo(source["github"]):
            problems.append("source.github: https://github.com/<owner>/<repo>")
        if "huggingface" in source and not (isinstance(source["huggingface"], str)
                                            and HF_ID_RE.match(source["huggingface"])):
            problems.append("source.huggingface: a model id like owner/name")
        if "url" in source and not _https(source["url"]):
            problems.append("source.url: an https address")
        if "path" in source and ("github" not in source or not isinstance(source["path"], str)
                                 or not manifest._relative_inside(source["path"])):
            problems.append("source.path: a folder inside the GitHub repository")
        if kind == "model" and "huggingface" not in source and "url" not in source:
            problems.append("source: a model's home is its Hugging Face repository (huggingface: owner/name)")
    models = data.get("models")
    if models is not None:
        if not isinstance(models, list) or len(models) > 10 or not all(
                isinstance(m, dict) and set(m) == {"huggingface"} and isinstance(m["huggingface"], str)
                and HF_ID_RE.match(m["huggingface"]) for m in models):
            problems.append("models: up to 10 of {huggingface: owner/name}")
    _short_list(problems, "platforms", data.get("platforms"), choices=PLATFORMS)
    if "runs" in data and data["runs"] not in RUNS:
        problems.append(f"runs: one of {', '.join(RUNS)}")
    _short_list(problems, "tags", data.get("tags"))
    _short_list(problems, "games", data.get("games"))
    _short_list(problems, "sports", data.get("sports"))
    if "adapter" in data and not (isinstance(data["adapter"], str) and manifest.ID_RE.match(data["adapter"])):
        problems.append("adapter: the id (publisher/name) of the listed pipeline or plugin that runs it in Clips Kitty")
    check_featured(problems, "featured", data.get("featured"))
    _text(problems, "warning", data.get("warning"), limit=200, required=False)
    for key in ("added", "checked"):
        if key in data and not DATE_RE.match(str(data[key])):
            problems.append(f"{key}: YYYY-MM-DD")
    if "added" not in data:
        problems.append("added: the date it was added, YYYY-MM-DD")
    return problems


def _load_yaml(path: Path):
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def read_settings(catalog_dir: Path) -> tuple[dict, list[str]]:
    """registry/catalog.yaml: {"counter": {"install": "https://...{asset}..."}}."""
    path = Path(catalog_dir) / "registry" / "catalog.yaml"
    if not path.exists():
        return {}, []
    data = _load_yaml(path) or {}
    problems = []
    if not isinstance(data, dict) or set(data) - {"counter"}:
        return {}, ["catalog.yaml: only counter is known"]
    counter = data.get("counter") or {}
    if not isinstance(counter, dict) or set(counter) - {"install"}:
        problems.append("catalog.yaml: counter.install only")
    elif counter.get("install") is not None and not counter_template_ok(counter["install"]):
        problems.append("catalog.yaml: counter.install must be a release download address in one of the "
                        "project's own GitHub repositories, ending in /{asset}")
    return ({"counter": {"install": counter["install"]}} if not problems and counter.get("install") else {}), problems


COUNTER_RE = re.compile(r"^https://github\.com/([A-Za-z0-9-]+)/[A-Za-z0-9._-]+/releases/download/[A-Za-z0-9._-]+/"
                        r"\{asset\}$")


def counter_template_ok(template) -> bool:
    """A counter address must be a release download in one of the project's
    own GitHub repositories: then the count really is GitHub's public
    download count, and no index can send installs anywhere else."""
    m = COUNTER_RE.match(template) if isinstance(template, str) else None
    return bool(m) and m.group(1).lower() in OFFICIAL_OWNERS


def counter_asset(listing_id: str) -> str:
    """The counter's name for one listing: GitHub release assets can't hold "/"."""
    return listing_id.replace("/", "__") + ".count"


def read_sections(catalog_dir: Path) -> tuple[dict, list[str]]:
    path = Path(catalog_dir) / "registry" / "sections.yaml"
    if not path.exists():
        return {}, ["sections.yaml is missing"]
    data = _load_yaml(path) or {}
    problems = check_sections(data)
    return (data if not problems else {}), problems


def read_entries(catalog_dir: Path, sections: dict) -> tuple[list[dict], list[str]]:
    """Every directory entry, checked. An entry with a problem is left out."""
    import yaml

    root = Path(catalog_dir) / "registry"
    out, problems = [], []
    for folder, kind in DIRECTORY_KINDS.items():
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_dir() or path.name.startswith("."):
                continue
            label = f"{folder}/{path.relative_to(base).as_posix()}"
            if path.suffix != ".yaml" or path.parent != base:
                problems.append(f"{label}: an entry is {folder}/<name>.yaml")
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as e:
                problems.append(f"{label}: not valid YAML ({e})")
                continue
            found = check_entry(data, kind, path.stem, sections)
            if found:
                problems += [f"{label}: {p}" for p in found]
                continue
            out.append({"id": f"{folder}/{path.stem}", "kind": kind, "slug": path.stem, **data})
    return out, problems


def read_stats(catalog_dir: Path) -> tuple[dict, dict]:
    """stats/metrics.json and stats/compatibility.json, or empty ones."""
    import json

    out = []
    for name in ("metrics.json", "compatibility.json"):
        path = Path(catalog_dir) / "stats" / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(data if isinstance(data, dict) else {})
        except (OSError, ValueError):
            out.append({})
    return out[0], out[1]


# ---- numbers and badges ---------------------------------------------------------------------


def _days_between(a: str, b: str) -> int | None:
    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except (TypeError, ValueError):
        return None


def entry_metrics(github_url, model_ids: list[str], metrics: dict, *, listing_id: str | None = None) -> dict:
    """The numbers for one entry, each kept apart: its repository's GitHub
    figures, its Clips Kitty installs (listings only), and each model's
    Hugging Face figures."""
    out: dict = {}
    repo = github_repo(github_url)
    gh = ((metrics.get("github") or {}).get(repo) if repo else None) or None
    if isinstance(gh, dict):
        out["github"] = {k: gh[k] for k in ("stars", "pushed_at", "archived", "has_discussions", "discussions")
                         if k in gh}
        days = _days_between(gh.get("pushed_at"), str(metrics.get("generated_at") or ""))
        if gh.get("archived"):
            out["stale"] = "archived"
        elif days is not None and days > STALE_DAYS:
            out["stale"] = f"no commits since {str(gh['pushed_at'])[:10]}"
    if listing_id:
        n = (metrics.get("installs") or {}).get(listing_id)
        if isinstance(n, int) and n >= 0:
            out["installs"] = n
    hf = metrics.get("huggingface") or {}
    models = {m: {k: hf[m][k] for k in ("downloads", "likes", "last_modified") if k in hf[m]}
              for m in model_ids if isinstance(hf.get(m), dict)}
    if models:
        out["models"] = models
    return out


def discussions_url(github_url, gh_metrics: dict | None) -> str | None:
    repo = github_repo(github_url)
    if repo and (gh_metrics or {}).get("has_discussions"):
        return f"https://github.com/{repo}/discussions"
    return None


def compatibility_for(listing_id: str, version: dict, compatibility: dict) -> dict | None:
    """The newest compatibility record for this exact version and commit."""
    records = [r for r in (compatibility.get(listing_id) or []) if isinstance(r, dict)
               and r.get("version") == version.get("version") and r.get("commit") == version.get("commit")]
    records.sort(key=lambda r: str(r.get("checked_at", "")), reverse=True)
    return records[0] if records else None


def badges(*, official: bool, compatible: bool = False, featured: bool = False) -> list[str]:
    out = ["official" if official else "community"]
    if compatible:
        out.append("compatible")
    if featured:
        out.append("featured")
    return out


def finish_entry(entry: dict, metrics: dict) -> dict:
    """A directory entry as the index carries it."""
    source = entry.get("source") or {}
    model_ids = [m["huggingface"] for m in entry.get("models") or []]
    if entry["kind"] == "model" and source.get("huggingface"):
        model_ids = [source["huggingface"], *model_ids]
    nums = entry_metrics(source.get("github"), model_ids, metrics)
    out = {k: v for k, v in entry.items() if k not in ("featured",)}
    for key in ("added", "checked"):
        if key in out:
            out[key] = str(out[key])
    out["badges"] = badges(official=is_official(source.get("github")), featured=bool(entry.get("featured")))
    if entry.get("featured"):
        out["featured"] = {"reason": entry["featured"]["reason"], "date": str(entry["featured"]["date"])}
    out["metrics"] = nums
    url = discussions_url(source.get("github"), nums.get("github"))
    if url:
        out["discussions_url"] = url
    return out


# ---- the README -------------------------------------------------------------------------------


def _anchor(title: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).strip().replace(" ", "-")


def _stars(n) -> str:
    if not isinstance(n, int):
        return ""
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


def _link(entry: dict) -> str:
    source = entry.get("source") or {}
    if source.get("github"):
        url = source["github"]
        if source.get("path"):
            url += "/tree/HEAD/" + source["path"].strip("/")
        elif str(source.get("url") or "").startswith((url + "#", url + "/")):
            url = source["url"]  # a page of the same repository, such as a README section
        return url
    if source.get("huggingface"):
        return f"https://huggingface.co/{source['huggingface']}"
    return source.get("url") or ""


def _line(entry: dict) -> str:
    desc = " ".join(str(entry.get("description", "")).split())
    if desc and desc[-1] not in ".!?":
        desc += "."
    bits = [f"`{entry['license']}`"]
    if "official" in entry.get("badges", []):
        bits.append("✓ Official")
    if "compatible" in entry.get("badges", []):
        bits.append("✓ Compatible")
    if "featured" in entry.get("badges", []):
        bits.append("★ Featured")
    runs = {"local": "runs locally", "cloud": "cloud", "both": "local or cloud"}.get(entry.get("runs") or "")
    if runs:
        bits.append(runs)
    nums = entry.get("metrics") or {}
    if nums.get("installs") is not None:
        bits.append(f"{nums['installs']:,} Clips Kitty installs")
    stars = _stars((nums.get("github") or {}).get("stars"))
    if stars:
        bits.append(f"★ {stars} on GitHub")
    if entry.get("kind") == "model":
        hf = next(iter((nums.get("models") or {}).values()), None)
        if hf and isinstance(hf.get("downloads"), int):
            bits.append(f"{_stars(hf['downloads'])} Hugging Face downloads a month")
    if entry.get("adapter"):
        bits.append(f"Clips Kitty adapter: `{entry['adapter']}`")
    if nums.get("stale"):
        bits.append(f"⚠ {nums['stale']}")
    line = f"- [{entry['name']}]({_link(entry)}) - {desc} " + " · ".join(bits)
    if entry.get("license_note"):
        line += f"  \n  Licence note: {' '.join(entry['license_note'].split())}"
    if entry.get("warning"):
        line += f"  \n  ⚠ {' '.join(entry['warning'].split())}"
    return line


def readme_body(sections: dict, entries: list[dict]) -> str:
    """The generated part of README.md: contents, every section with its
    entries (empty sections left out), unchecked entries, and the niches
    nobody has filled yet."""
    by_kind: dict[str, list[dict]] = {}
    for e in entries:
        by_kind.setdefault(e["kind"], []).append(e)
    toc, body = ["## Contents", ""], []
    for kind in KIND_ORDER:
        mine = by_kind.get(kind, [])
        spec = sections.get(kind) or {}
        checked = [e for e in mine if e.get("checked")]
        unchecked = [e for e in mine if not e.get("checked")]
        if not mine:
            continue
        title = KIND_TITLES[kind]
        toc.append(f"- [{title}](#{_anchor(title)})")
        body += [f"## {title}", ""]
        for s in spec.get("sections", []):
            own = sorted((e for e in checked if e.get("section") == s["id"]), key=lambda e: e["name"].lower())
            children = "/" not in s["id"] and any(str(e.get("section", "")).startswith(s["id"] + "/")
                                                   for e in checked)
            if not own and not children:
                continue
            nested = "/" in s["id"]
            toc.append(("    " if nested else "  ") + f"- [{s['title']}](#{_anchor(s['title'])})")
            body += [f"{'####' if nested else '###'} {s['title']}", ""]
            if s.get("description"):
                body += [f"_{' '.join(s['description'].split())}_", ""]
            if own:
                body += [_line(e) for e in own] + [""]
        if unchecked:
            sub = f"Not yet checked ({title.lower()})"
            toc.append(f"  - [{sub}](#{_anchor(sub)})")
            body += [f"### {sub}", "",
                     "_Added by their authors and not yet checked against the inclusion criteria._", ""]
            body += [_line(e) for e in sorted(unchecked, key=lambda e: e["name"].lower())] + [""]
    wanted = [(kind, w) for kind in KIND_ORDER for w in (sections.get(kind) or {}).get("wanted", [])]
    if wanted:
        toc.append("- [Wanted](#wanted)")
        body += ["## Wanted", "",
                 "Nothing is listed for these yet. Ideas for developers, not projects that exist:", ""]
        titles = {kind: {s["id"]: s["title"] for s in (sections.get(kind) or {}).get("sections", [])}
                  for kind in KIND_ORDER}
        for kind, w in wanted:
            body.append(f"- **{KIND_TITLES[kind]} › {titles[kind].get(w['section'], w['section'])}**: "
                        f"{' '.join(w['idea'].split())}")
        body.append("")
    return "\n".join([*toc, "", *body]).rstrip() + "\n"


def write_readme(readme: str, body: str) -> str:
    """README text with the generated part replaced; the rest is hand-written."""
    if GENERATED_START not in readme or GENERATED_END not in readme:
        raise CatalogError("README.md needs the two generated markers")
    head, rest = readme.split(GENERATED_START, 1)
    _, tail = rest.split(GENERATED_END, 1)
    return f"{head}{GENERATED_START}\n\n{body}\n{GENERATED_END}{tail}"
