"""Counting installs from the catalog, with nothing about the person.

After a plugin is installed from a listing for the first time, the app asks
for one small file named after the listing, at the address the listing's
index gives (`counter.install`, with `{asset}` replaced). The plan is a
GitHub release in the catalog's repository with one such file per listing,
so the count is GitHub's own download count for that file, which
scripts/update_registry_metrics.py reads back into stats/metrics.json.

What the request carries is what any download carries (the address it comes
from, which GitHub sees, as it does when the plugin itself is downloaded) and
nothing else: no account, no identifier, no cookie, no app version, nothing
about videos or projects. An update, a rollback or a version switch is not
counted; installing again after removing is.

It is switched on unless the person switches it off (here, in
<data_dir>/plugins/counting.json, from the Marketplace) or settings say
`plugins.count_installs: false`. Nothing is sent while the index has no
counter address, which is the case until the catalog has a public repository
of its own. Counting never raises and never holds up an install.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from plugins import catalog, store

PREFS_FILE = "counting.json"
DEFAULT_ON = True
TIMEOUT = 10
EXPLAIN = ("Each install from the Marketplace adds one to a public count for that plugin, kept by GitHub. "
           "Clips Kitty sends no account, no ID and nothing about your videos.")


def _prefs_path(data_dir) -> Path:
    return store.root(data_dir) / PREFS_FILE


def chosen(data_dir) -> bool | None:
    """What the person chose in the Marketplace, or None if they haven't."""
    try:
        value = json.loads(_prefs_path(data_dir).read_text(encoding="utf-8")).get("count_installs")
        return value if isinstance(value, bool) else None
    except (OSError, ValueError, AttributeError):
        return None


def enabled(data_dir, config: dict | None = None) -> bool:
    """Whether installs are counted: settings can switch it off for everyone
    on this PC; otherwise the person's choice; otherwise the default."""
    if ((config or {}).get("plugins") or {}).get("count_installs") is False:
        return False
    choice = chosen(data_dir)
    return DEFAULT_ON if choice is None else choice


def set_enabled(data_dir, on: bool) -> None:
    path = _prefs_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"count_installs": bool(on)}), encoding="utf-8")
    tmp.replace(path)


def count_url(template, listing_id) -> str | None:
    """The address that counts one install of a listing, or None."""
    if not catalog.counter_template_ok(template) or not isinstance(listing_id, str) \
            or not store.ID_RE.match(listing_id):
        return None
    return template.replace("{asset}", urllib.parse.quote(catalog.counter_asset(listing_id), safe=""))


def _get(url: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Clips-Kitty"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            response.read(64 * 1024)
    except Exception:  # a count that doesn't arrive is simply not counted
        pass


def count_install(data_dir, config: dict | None, template, listing_id, *, opener=None) -> str | None:
    """Count one install in the background. Returns the address requested,
    or None when nothing is sent (switched off, or no counter address)."""
    if not enabled(data_dir, config):
        return None
    url = count_url(template, listing_id)
    if not url:
        return None
    threading.Thread(target=opener or _get, args=(url,), daemon=True, name="clipskitty-install-count").start()
    return url
