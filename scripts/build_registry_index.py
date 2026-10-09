"""Build the Awesome Clips Kitty catalog: index.json for the app, README.md for people.

    python scripts/build_registry_index.py                  # write index.json and README.md
    python scripts/build_registry_index.py --check          # fail if either is out of date or an entry is refused
    python scripts/build_registry_index.py --sources DIR    # read manifests from DIR instead of GitHub

The catalog is its own repository, github.com/ColinGPT9/awesome-clips-kitty
(CONTRIBUTING.md there says what an entry must pass), and its jobs run this
script from a checkout of this repository. --catalog names the catalog's
folder; without it, a checkout beside this repository is used. Each listed version's manifest is fetched at its commit as plain
text from GitHub (raw.githubusercontent.com) and checked with the validator
the app uses. Each commit must also be on a branch or tag of the listed
repository, because GitHub serves a fork's commits under the parent's address
too: the build fetches each repository's commit history (no files) to check.
Nothing is installed or run. With --sources, a manifest is read from
DIR/<owner>/<repo>/<commit>/<path>/clipskitty.yaml instead and the branch
check is skipped, which is how the tests run it without the network. The numbers (stars, downloads,
installs) come from stats/metrics.json, which scripts/update_registry_metrics.py
writes; this build never asks GitHub or Hugging Face for them.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def fixture_reader(base: Path):
    """fetch(url) for --sources: the same path below `base` as below GitHub's raw host."""
    prefix = "https://raw.githubusercontent.com/"

    def fetch(url: str) -> str:
        if not url.startswith(prefix):
            raise ValueError(f"not a raw GitHub address: {url}")
        return (base / url[len(prefix):]).read_text(encoding="utf-8")

    return fetch


def readme_for(index: dict, readme: str) -> str:
    """The README with its generated part rebuilt from the index: the
    directory entries and the installable listings, in their sections."""
    from plugins import catalog

    listings = [{**p, "kind": p.get("kind", "pipeline"),
                 "source": {"github": p["repository"], **({"path": p["path"]} if p.get("path") not in (None, ".") else {})}}
                for p in index["plugins"]]
    return catalog.write_readme(readme, catalog.readme_body(index["sections"], [*index["catalog"], *listings]))


def main(argv=None) -> int:
    from plugins import catalog, registry

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", type=Path, default=registry.catalog_path(), help="the catalog folder")
    ap.add_argument("--out", type=Path, help="where to write the index (default: <catalog>/index.json)")
    ap.add_argument("--check", action="store_true", help="exit 1 if a file is out of date or an entry is refused")
    ap.add_argument("--sources", type=Path, help="read manifests from this folder instead of GitHub")
    args = ap.parse_args(argv)

    out = args.out or args.catalog / "index.json"
    readme_path = args.catalog / "README.md"
    fetch = fixture_reader(args.sources) if args.sources else registry.fetch_raw
    index, problems = registry.build_index(args.catalog, fetch=fetch,
                                           on_branch=None if args.sources else registry.commit_on_branch)
    for problem in problems:
        print(f"refused: {problem}")
    text = registry.index_text(index)
    readme = None
    if readme_path.exists() and "sections" in index:
        try:
            readme = readme_for(index, readme_path.read_text(encoding="utf-8"))
        except catalog.CatalogError as e:
            print(f"refused: README.md: {e}")
            problems.append(str(e))
    counts = f"{len(index['plugins'])} listings, {len(index.get('catalog', []))} catalog entries"
    if args.check:
        stale = [str(path) for path, want in ((out, text), (readme_path, readme)) if want is not None
                 and (path.read_text(encoding="utf-8") if path.exists() else "") != want]
        for path in stale:
            print(f"{path} is out of date: run python scripts/build_registry_index.py")
        if stale or problems:
            return 1
        print(f"{out} and the README are up to date ({counts})")
        return 0
    out.write_text(text, encoding="utf-8")
    if readme is not None:
        readme_path.write_text(readme, encoding="utf-8")
    print(f"wrote {out} ({counts}, {len(problems)} refused)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
