"""Refresh the numbers Awesome Clips Kitty shows: stats/metrics.json in the catalog.

    python scripts/update_registry_metrics.py              # read every number, write metrics.json
    python scripts/update_registry_metrics.py --dry-run    # print what would be written

Each number comes from its own source and is stored apart from the others:

- GitHub, for every repository the catalog names: stars, the last push,
  whether it is archived, whether Discussions are on, and how many
  discussions there are (that one needs a token: GITHUB_TOKEN or GH_TOKEN).
- Hugging Face, for every model an entry or a listing names: downloads in
  the last 30 days (Hugging Face's own figure) and likes.
- Clips Kitty installs, from the install counter: when registry/catalog.yaml
  names a counter address of the form
  https://github.com/<owner>/<repo>/releases/download/<tag>/{asset},
  the download count of each listing's file in that release.

Which repositories and models to read comes from index.json, so run
scripts/build_registry_index.py first when entries have changed, and again
afterwards so the index carries the new numbers. A number that can't be read
keeps its last value; nothing is ever made up. The app never asks GitHub or
Hugging Face for these itself.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GITHUB_API = "https://api.github.com"
HF_API = "https://huggingface.co/api/models/"
RELEASE_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/\{asset\}$")
USER_AGENT = "Awesome-Clips-Kitty-metrics"


def get_json(url: str, *, token: str | None = None, body: dict | None = None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token and url.startswith(GITHUB_API):
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read(8 * 1024 * 1024))


def wanted(index: dict) -> tuple[list[str], list[str]]:
    """The GitHub repositories ("owner/repo") and Hugging Face model ids the
    index names, each once, sorted."""
    from plugins import catalog

    repos, models = set(), set()
    for p in index.get("plugins") or []:
        repo = catalog.github_repo(p.get("repository"))
        if repo:
            repos.add(repo)
        for m in p.get("models") or []:
            if isinstance(m, dict) and m.get("source") == "huggingface" and isinstance(m.get("id"), str):
                models.add(m["id"])
    for e in index.get("catalog") or []:
        source = e.get("source") or {}
        repo = catalog.github_repo(source.get("github"))
        if repo:
            repos.add(repo)
        if isinstance(source.get("huggingface"), str):
            models.add(source["huggingface"])
        for m in e.get("models") or []:
            if isinstance(m, dict) and isinstance(m.get("huggingface"), str):
                models.add(m["huggingface"])
    return sorted(repos), sorted(m for m in models if catalog.HF_ID_RE.match(m))


def github_numbers(repo: str, *, fetch, token: str | None) -> dict:
    data = fetch(f"{GITHUB_API}/repos/{repo}", token=token)
    out = {"stars": int(data["stargazers_count"]), "pushed_at": str(data.get("pushed_at") or "")[:10],
           "archived": bool(data.get("archived")), "has_discussions": bool(data.get("has_discussions"))}
    if out["has_discussions"] and token:
        owner, name = repo.split("/", 1)
        query = ("query($owner: String!, $name: String!) "
                 "{ repository(owner: $owner, name: $name) { discussions { totalCount } } }")
        answer = fetch(f"{GITHUB_API}/graphql", token=token,
                       body={"query": query, "variables": {"owner": owner, "name": name}})
        count = (((answer or {}).get("data") or {}).get("repository") or {}).get("discussions", {}).get("totalCount")
        if isinstance(count, int):
            out["discussions"] = count
    return out


def hf_numbers(model_id: str, *, fetch) -> dict:
    data = fetch(HF_API + urllib.parse.quote(model_id, safe="/"))
    out = {"downloads": int(data.get("downloads") or 0), "likes": int(data.get("likes") or 0)}
    if data.get("lastModified"):
        out["last_modified"] = str(data["lastModified"])[:10]
    return out


def install_counts(template: str | None, *, fetch, token: str | None) -> dict[str, int] | None:
    """{listing id: downloads of its counter file}, or None when the counter
    isn't a GitHub release (or there is none)."""
    m = RELEASE_RE.match(template or "")
    if not m:
        return None
    owner, repo, tag = m.groups()
    release = fetch(f"{GITHUB_API}/repos/{owner}/{repo}/releases/tags/{urllib.parse.quote(tag, safe='')}",
                    token=token)
    out = {}
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.endswith(".count") and "__" in name:
            out[name[: -len(".count")].replace("__", "/", 1)] = int(asset.get("download_count") or 0)
    return out


def update(index: dict, previous: dict, *, fetch=get_json, token: str | None = None, now: str | None = None,
           log=print) -> dict:
    """New metrics, keeping the previous value of anything that can't be read."""
    repos, models = wanted(index)
    old_gh, old_hf = previous.get("github") or {}, previous.get("huggingface") or {}
    out = {"generated_at": now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "github": {}, "huggingface": {}, "installs": dict(previous.get("installs") or {})}
    for repo in repos:
        try:
            out["github"][repo] = github_numbers(repo, fetch=fetch, token=token)
        except Exception as e:  # any failure keeps the last value
            log(f"GitHub {repo}: kept the last value ({e})")
            if repo in old_gh:
                out["github"][repo] = old_gh[repo]
    for model in models:
        try:
            out["huggingface"][model] = hf_numbers(model, fetch=fetch)
        except Exception as e:
            log(f"Hugging Face {model}: kept the last value ({e})")
            if model in old_hf:
                out["huggingface"][model] = old_hf[model]
    template = (index.get("counter") or {}).get("install")
    try:
        counts = install_counts(template, fetch=fetch, token=token)
    except Exception as e:
        log(f"install counter: kept the last values ({e})")
        counts = None
    if counts is not None:
        listed = {p["id"] for p in index.get("plugins") or []}
        out["installs"].update({pid: n for pid, n in counts.items() if pid in listed})
    out["installs"] = dict(sorted(out["installs"].items()))
    return out


def main(argv=None) -> int:
    from plugins import registry

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", type=Path, default=registry.catalog_path(), help="the catalog folder")
    ap.add_argument("--dry-run", action="store_true", help="print the result instead of writing it")
    args = ap.parse_args(argv)

    index = json.loads((args.catalog / "index.json").read_text(encoding="utf-8"))
    path = args.catalog / "stats" / "metrics.json"
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None
    metrics = update(index, previous if isinstance(previous, dict) else {}, token=token)
    text = json.dumps(metrics, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if args.dry_run:
        print(text, end="")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}: {len(metrics['github'])} repositories, {len(metrics['huggingface'])} models, "
          f"{len(metrics['installs'])} install counts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
