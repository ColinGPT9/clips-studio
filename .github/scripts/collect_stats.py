"""Append today's traffic and download numbers to docs/stats/.

Run by .github/workflows/stats.yml. See docs/stats/README.md for what the
columns mean and what they do not.

Keyed by date and idempotent on purpose: the traffic API always returns the
same rolling 14 days, so consecutive runs overlap almost entirely. Re-running
must be a no-op rather than a way to double every number.
"""

import csv
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = os.environ.get("REPO", "ColinGPT9/clips-studio")
TOKEN = os.environ.get("GH_TOKEN", "")
OUT = Path("docs/stats")

TRAFFIC_CSV = OUT / "traffic.csv"
RELEASES_CSV = OUT / "releases.csv"

# (all columns, the ones that identify a row). The key is what makes a re-run
# overwrite instead of append — traffic is one row per day, releases one row
# per asset per day.
TRAFFIC_COLUMNS = ["date", "views", "views_unique", "clones", "clones_unique"]
TRAFFIC_KEY = ["date"]
RELEASE_COLUMNS = ["date", "tag", "asset", "downloads"]
RELEASE_KEY = ["date", "tag", "asset"]


def api(path: str):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/{path}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "clips-kitty-stats",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # A stats job must never fail the repository's checks. Report and stop.
        sys.exit(f"GitHub API {path} returned {e.code}: {e.read()[:200]!r}")


def read_rows(path: Path, key: list[str]) -> dict:
    """Existing rows, keyed so a repeat run overwrites rather than appends."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {tuple(r[k] for k in key): r for r in csv.DictReader(f)}


def write_rows(path: Path, columns: list[str], rows: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for key in sorted(rows):
            w.writerow(rows[key])


def collect_traffic() -> None:
    """One row per day: views and clones, total and unique.

    The two calls are separate endpoints returning the same 14 dates, so they
    are merged on date rather than assumed to line up positionally — a day
    with clones but no views would otherwise shift every later row.
    """
    views = {v["timestamp"][:10]: v for v in api("traffic/views").get("views", [])}
    clones = {c["timestamp"][:10]: c for c in api("traffic/clones").get("clones", [])}

    rows = read_rows(TRAFFIC_CSV, TRAFFIC_KEY)
    for date in sorted(set(views) | set(clones)):
        v, c = views.get(date, {}), clones.get(date, {})
        rows[(date,)] = {
            "date": date,
            "views": v.get("count", 0),
            "views_unique": v.get("uniques", 0),
            "clones": c.get("count", 0),
            "clones_unique": c.get("uniques", 0),
        }
    write_rows(TRAFFIC_CSV, TRAFFIC_COLUMNS, rows)
    print(f"traffic.csv: {len(rows)} days")


def collect_releases(today: str) -> None:
    """Download totals per asset, stamped with the day they were read.

    Cumulative, not per-day: GitHub only ever reports a running total. Storing
    it daily is what makes the curve recoverable — the difference between two
    dates is that period's downloads.
    """
    rows = read_rows(RELEASES_CSV, RELEASE_KEY)
    count = 0
    for rel in api("releases?per_page=100"):
        for asset in rel.get("assets", []):
            rows[(today, rel["tag_name"], asset["name"])] = {
                "date": today,
                "tag": rel["tag_name"],
                "asset": asset["name"],
                "downloads": asset["download_count"],
            }
            count += 1
    write_rows(RELEASES_CSV, RELEASE_COLUMNS, rows)
    print(f"releases.csv: {len(rows)} rows ({count} assets today)")


def main() -> None:
    if not TOKEN:
        sys.exit("GH_TOKEN is not set")
    # The newest traffic date, rather than the clock: it is the day GitHub
    # considers complete, and it keeps both files on the same calendar.
    views = api("traffic/views").get("views", [])
    today = views[-1]["timestamp"][:10] if views else ""
    if not today:
        sys.exit("traffic API returned no days — refusing to write an undated row")
    collect_traffic()
    collect_releases(today)


if __name__ == "__main__":
    main()
