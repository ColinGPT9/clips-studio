"""Build registry/index.json from the listings in registry/plugins/.

    python scripts/build_registry_index.py                  # write registry/index.json
    python scripts/build_registry_index.py --check          # fail if it is out of date or a listing is refused
    python scripts/build_registry_index.py --sources DIR    # read manifests from DIR instead of GitHub

Each listed version's manifest is fetched at its commit as plain text from
GitHub (raw.githubusercontent.com) and checked with the validator the app
uses; nothing is cloned, installed or run. With --sources, a manifest is read
from DIR/<owner>/<repo>/<commit>/<path>/clipskitty.yaml instead, which is how
the tests run it without the network. What a listing must pass is in
registry/README.md.
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


def main(argv=None) -> int:
    from plugins import registry

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", type=Path, default=ROOT / "registry", help="the registry folder")
    ap.add_argument("--out", type=Path, help="where to write the index (default: <registry>/index.json)")
    ap.add_argument("--check", action="store_true", help="exit 1 if the index is out of date or a listing is refused")
    ap.add_argument("--sources", type=Path, help="read manifests from this folder instead of GitHub")
    args = ap.parse_args(argv)

    out = args.out or args.registry / "index.json"
    fetch = fixture_reader(args.sources) if args.sources else registry.fetch_raw
    index, problems = registry.build_index(args.registry, fetch=fetch)
    for problem in problems:
        print(f"refused: {problem}")
    text = registry.index_text(index)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current != text:
            print(f"{out} is out of date: run python scripts/build_registry_index.py")
            return 1
        if problems:
            return 1
        print(f"{out} is up to date ({len(index['plugins'])} plugins)")
        return 0
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(index['plugins'])} plugins, {len(problems)} refused)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
