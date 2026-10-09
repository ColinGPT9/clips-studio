"""Refresh the copy of Awesome Clips Kitty's index that ships with the app.

    python scripts/sync_catalog_index.py            # fetch it and write plugins/catalog_index.json
    python scripts/sync_catalog_index.py --check    # exit 1 if the shipped copy isn't the catalog's

The catalog is its own repository, github.com/ColinGPT9/awesome-clips-kitty,
which checks and builds itself. The app ships a copy of its index.json: what
the Marketplace shows before the app has been online, and the only copy it
takes the Official, Compatible and Featured labels and the install counter
from. Run this before a release, so the release carries the list as it is
then; between releases the app reads the catalog's own copy (the online
list), which only adds to this one.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def fetched(fetch=None) -> str:
    """The catalog's index as the app would keep it, or RegistryError saying
    why it can't ship: not an index, or one the app would drop part of."""
    from plugins import registry

    text = (fetch or registry.fetch_raw)(registry.ONLINE_URL)
    try:
        data = json.loads(text)
        kept = registry.check_index(data, ours=True)
    except (ValueError, registry.RegistryError) as e:
        raise registry.RegistryError(f"{registry.ONLINE_URL} isn't an index the app reads: {e}") from e
    for part in ("plugins", "catalog"):
        if len(kept.get(part, [])) != len(data.get(part, [])):
            raise registry.RegistryError(f"the app would drop some of the catalog's {part}: this checkout's "
                                         "rules and the catalog's differ")
    return registry.index_text(data)


def main(argv=None) -> int:
    from plugins import registry

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if the shipped copy isn't the catalog's")
    args = ap.parse_args(argv)

    path = registry.bundled_path()
    try:
        text = fetched()
    except (OSError, registry.RegistryError) as e:
        print(f"could not read the catalog: {e}")
        return 1
    same = path.exists() and path.read_text(encoding="utf-8") == text
    if args.check:
        print(f"{path} is the catalog's index" if same else f"{path} is out of date: run python scripts/sync_catalog_index.py")
        return 0 if same else 1
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"{path}: " + ("already the catalog's index" if same else "updated"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
