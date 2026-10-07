"""The plugin registry: the index build, the app's offline client, search,
installing from a listing, and the block list reaching installed copies.

The registry here is made from a table in a temporary folder: one listing
per plugin and each manifest at a fake commit in a folder laid out like
GitHub's raw file addresses, so the build reads it without the network.
Every id and address is a placeholder.
"""

import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from plugins import catalog, manager, registry, sources, store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APP = "2.0.0"
OWNER = "example-dev"


def _commit(n: int) -> str:
    return f"{n:040x}"


# (name, display name, category, tags, games, events, description)
CATALOGUE = [
    ("marvel-rivals-highlights", "Marvel Rivals Highlights", "gaming", ["team-fights"], ["marvel-rivals"],
     ["team_wipe", "multi_kill"], "Finds team wipes and multi-kills from the kill feed."),
    ("wow-pvp-highlights", "WoW Arena PvP Highlights", "gaming", ["pvp", "arena"], ["world-of-warcraft"],
     ["kill"], "Arena kills and clutch defensives."),
    ("wow-mythic-plus", "Mythic+ Dungeon Moments", "gaming", ["mythic-plus", "pve"], ["world-of-warcraft"],
     ["boss_kill", "wipe"], "Boss kills, wipes and timed keys."),
    ("minecraft-survival", "Minecraft Survival Moments", "gaming", ["survival"], ["minecraft"], ["death"],
     "Deaths, close calls and big finds."),
    ("rocket-league-goals", "Rocket League Goals and Saves", "gaming", ["goals", "saves"], ["rocket-league"],
     ["goal", "save"], "Every goal and save, from the score display."),
    ("soccer-goals", "Soccer Goals", "sports", ["goals"], ["soccer"], ["goal"], "Goals from match broadcasts."),
    ("nhl-goals", "NHL Goals", "sports", ["hockey"], ["nhl"], ["goal"], "Goals from NHL broadcasts."),
    ("sports-highlights", "Sports Highlights", "sports", ["highlights"], ["soccer", "nhl", "basketball"],
     ["goal"], "Big moments from many sports."),
    ("podcast-shorts", "Podcast Shorts", "podcasting", ["shorts", "interviews"], [], [],
     "Self-contained answers from long conversations."),
    ("twitch-stream-highlights", "Stream Highlights", "streaming", ["twitch", "chat-spikes"], [], ["hype"],
     "Moments chat went wild, from a Twitch VOD."),
    ("karaoke-moments", "Caption-Ready Moments", "captions", ["karaoke", "captions"], [], [],
     "Picks moments with clear speech for big captions."),
    ("gaming-highlights", "Gaming Highlights", "gaming", ["highlights"], [], ["kill"],
     "Highlights from any game: World of Warcraft, Minecraft and more."),
]


def _manifest(name, title, category, tags, games, events, description, version="1.0.0", **extra):
    data = {"manifest_version": 1, "id": f"{OWNER}/{name}", "name": title, "version": version, "kind": "pipeline",
            "capability": "highlight_detection", "description": description, "license": "MIT",
            "repository": f"https://github.com/{OWNER}/{name}", "requires": {"clips_kitty": ">=2.0", "plugin_api": 1},
            "run": {"command": ["{python}", "src/main.py"]}, "execution": "local", "inputs": ["video"],
            "outputs": ["ranges"], "permissions": ["video.read"], "category": category, "tags": tags,
            "games": games, "events": events}
    data.update(extra)
    return data


class Registry:
    """A catalog folder laid out like awesome-clips-kitty/, with listings only."""

    def __init__(self, base: Path):
        self.root = base / "catalog"
        self.dir = self.root / "registry"
        self.sources = base / "raw"
        (self.dir / "pipelines").mkdir(parents=True)
        (self.dir / "blocklist.yaml").write_text("[]\n")
        self.n = 0

    def manifest_at(self, name, commit, data, path="."):
        folder = self.sources / OWNER / name / commit
        if path != ".":
            folder = folder / path
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "clipskitty.yaml").write_text(yaml.safe_dump(data))

    def listing(self, name, versions, *, raw=None, file=None, **extra):
        """versions: [(version, manifest or None)] - None writes no manifest."""
        entries = []
        for version, data in versions:
            self.n += 1
            commit = _commit(self.n)
            entries.append({"version": version, "commit": commit})
            if data is not None:
                self.manifest_at(name, commit, data, extra.get("path", "."))
        listing = raw or {"id": f"{OWNER}/{name}", "repository": f"https://github.com/{OWNER}/{name}",
                          "versions": entries, **extra}
        target = self.dir / "pipelines" / (file or f"{OWNER}/{name}.yaml")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(listing))
        return entries

    def block(self, entries):
        (self.dir / "blocklist.yaml").write_text(yaml.safe_dump(entries))

    def build(self, **kw):
        from scripts.build_registry_index import fixture_reader

        return registry.build_index(self.root, fetch=fixture_reader(self.sources), **kw)


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path)


@pytest.fixture
def catalogue(reg, tmp_path):
    for row in CATALOGUE:
        reg.listing(row[0], [("1.0.0", _manifest(*row))])
    index, problems = reg.build()
    assert problems == []
    path = tmp_path / "index.json"
    path.write_text(registry.index_text(index))
    return path


# ---- the build ------------------------------------------------------------------------------


def test_a_commit_must_be_on_a_branch_of_the_listed_repository(reg):
    # GitHub serves a fork's commits under the parent's address too, so a hash alone
    # doesn't say whose code it is: the build asks where each commit is.
    row = CATALOGUE[0]
    (entry,) = reg.listing(row[0], [("1.0.0", _manifest(*row))])
    asked = []

    def on_branch(owner, repo, commit):
        asked.append((owner, repo, commit))
        return False

    index, problems = reg.build(on_branch=on_branch)
    assert index["plugins"] == [] and asked == [(OWNER, row[0], entry["commit"])]
    assert problems == [f"pipelines/{OWNER}/{row[0]}.yaml: 1.0.0: commit {entry['commit'][:7]} is not on a branch "
                        f"or tag of {OWNER}/{row[0]} (it may be from a fork)"]

    def offline(owner, repo, commit):
        raise OSError("no network")

    index, problems = reg.build(on_branch=offline)
    assert index["plugins"] == [] and "couldn't check where commit" in problems[0] and "no network" in problems[0]
    index, problems = reg.build(on_branch=lambda *a: True)
    assert problems == [] and index["plugins"][0]["checks"]["commit_on_branch"] is True
    assert "commit_on_branch" not in reg.build()[0]["plugins"][0]["checks"]  # not checked, not claimed


def test_only_the_bundled_index_installs_as_official():
    official = {"repository": "https://github.com/ColinGPT9/clips-studio"}
    assert registry.listing_tier({**official, "index": "bundled"}) == "listed-official"
    assert registry.listing_tier({**official, "index": "https://example.org/index.json"}) == "listed"
    assert registry.listing_tier({"repository": "https://github.com/example-dev/x", "index": "bundled"}) == "listed"


def test_the_build_lists_each_plugin_with_what_the_marketplace_shows(reg):
    row = CATALOGUE[0]
    reg.listing(row[0], [("1.0.0", _manifest(*row)), ("1.1.0", _manifest(*row, version="1.1.0",
                                                                          requirements={"gpu": "recommended"}))],
                aliases=["rivals"])
    index, problems = reg.build()
    assert problems == []
    (p,) = index["plugins"]
    assert p["id"] == "example-dev/marvel-rivals-highlights" and p["publisher"] == "example-dev"
    assert p["latest"] == "1.1.0" and [v["version"] for v in p["versions"]] == ["1.1.0", "1.0.0"]
    assert p["versions"][0]["commit"] == _commit(2) and p["versions"][0]["requires"]["clips_kitty"] == ">=2.0"
    assert p["requirements"] == {"gpu": "recommended"}  # the latest version's manifest
    assert p["games"] == ["marvel-rivals"] and p["aliases"] == ["rivals"] and p["license"] == "MIT"
    assert p["checks"] == {"manifest_valid": True, "publisher_is_repository_owner": True, "commit_pinned": True,
                           "public_at_commit": True}
    assert registry.index_text(index) == registry.index_text(reg.build()[0])  # the same every time


@pytest.mark.parametrize("case, fragment", [
    ("wrong-file", "the file must be pipelines/example-dev/wrong-file.yaml"),
    ("wrong-owner", "the publisher 'example-dev' must be the repository's GitHub owner, 'someone-else'"),
    ("reserved", "the publisher 'clipskitty' is reserved"),
    ("not-github", "repository: must be https://github.com/<owner>/<repo>"),
    ("short-commit", "versions[0].commit: must be the full 40-character commit hash"),
    ("twice", "versions[1].version: 1.0.0 is listed twice"),
    ("unknown-field", "stars: unknown field"),
    ("id-mismatch", "the manifest's id is 'example-dev/something-else', the listing says 'example-dev/id-mismatch'"),
    ("version-mismatch", "the manifest's version is '9.9.9', the listing says '1.0.0'"),
    ("invalid-manifest", "clipskitty.yaml: license: is required"),
    ("no-manifest", "the manifest at commit"),
])
def test_the_build_refuses_a_bad_listing_and_says_why(reg, case, fragment):
    good = ("good", "Good", "utilities", [], [], [], "A good one.")
    reg.listing(good[0], [("1.0.0", _manifest(*good))])
    name = case
    m = _manifest(name, "X", "utilities", [], [], [], "x")
    repo = f"https://github.com/{OWNER}/{name}"
    commit = _commit(99)
    if case == "wrong-file":
        reg.listing(name, [("1.0.0", m)], file=f"{OWNER}/elsewhere.yaml")
    elif case == "wrong-owner":
        reg.listing(name, [], raw={"id": f"{OWNER}/{name}", "repository": f"https://github.com/someone-else/{name}",
                                   "versions": [{"version": "1.0.0", "commit": commit}]})
    elif case == "reserved":
        reg.listing(name, [], raw={"id": f"clipskitty/{name}", "repository": f"https://github.com/clipskitty/{name}",
                                   "versions": [{"version": "1.0.0", "commit": commit}]}, file=f"clipskitty/{name}.yaml")
    elif case == "not-github":
        reg.listing(name, [], raw={"id": f"{OWNER}/{name}", "repository": f"https://example.com/{OWNER}/{name}",
                                   "versions": [{"version": "1.0.0", "commit": commit}]})
    elif case == "short-commit":
        reg.listing(name, [], raw={"id": f"{OWNER}/{name}", "repository": repo,
                                   "versions": [{"version": "1.0.0", "commit": "0123abc"}]})
    elif case == "twice":
        reg.listing(name, [], raw={"id": f"{OWNER}/{name}", "repository": repo,
                                   "versions": [{"version": "1.0.0", "commit": commit}] * 2})
    elif case == "unknown-field":
        reg.listing(name, [], raw={"id": f"{OWNER}/{name}", "repository": repo, "stars": 5,
                                   "versions": [{"version": "1.0.0", "commit": commit}]})
    elif case == "id-mismatch":
        reg.listing(name, [("1.0.0", {**m, "id": f"{OWNER}/something-else"})])
    elif case == "version-mismatch":
        reg.listing(name, [("1.0.0", {**m, "version": "9.9.9"})])
    elif case == "invalid-manifest":
        reg.listing(name, [("1.0.0", {k: v for k, v in m.items() if k != "license"})])
    elif case == "no-manifest":
        reg.listing(name, [("1.0.0", None)])
    index, problems = reg.build()
    assert any(fragment in p for p in problems), problems
    assert [p["id"] for p in index["plugins"]] == ["example-dev/good"]  # the rest still builds


def test_blocked_and_delisted_versions_leave_the_index_and_the_list_goes_in(reg):
    row = CATALOGUE[5]
    reg.listing(row[0], [("1.0.0", _manifest(*row)), ("1.1.0", _manifest(*row, version="1.1.0"))])
    reg.block([{"id": f"{OWNER}/{row[0]}", "versions": ["1.1.0"], "severity": "blocked",
                "reason": "Uploads videos it does not declare", "date": "2026-10-07"}])
    index, problems = reg.build()
    assert problems == []
    (p,) = index["plugins"]
    assert p["latest"] == "1.0.0" and [v["version"] for v in p["versions"]] == ["1.0.0"]
    assert index["blocklist"] == [{"id": f"{OWNER}/{row[0]}", "versions": ["1.1.0"], "severity": "blocked",
                                   "reason": "Uploads videos it does not declare", "date": "2026-10-07"}]
    reg.block([{"id": f"{OWNER}/{row[0]}", "versions": "*", "severity": "delisted", "reason": "Abandoned",
                "date": "2026-10-07"}])
    assert reg.build()[0]["plugins"] == []


def test_a_bad_block_list_is_refused(reg):
    reg.block([{"id": "nope", "versions": [], "severity": "maybe", "reason": "", "date": "yesterday"}])
    problems = reg.build()[1]
    for fragment in ("id: must look like", "versions:", "severity: blocked or delisted", "reason:", "date:"):
        assert any(fragment in p for p in problems), (fragment, problems)


def test_the_script_writes_and_checks_the_index(reg, tmp_path, capsys):
    from scripts.build_registry_index import main

    row = CATALOGUE[3]
    reg.listing(row[0], [("1.0.0", _manifest(*row))])
    out = tmp_path / "out.json"
    args = ["--catalog", str(reg.root), "--sources", str(reg.sources), "--out", str(out)]
    assert main([*args, "--check"]) == 1  # not written yet
    assert main(args) == 0 and json.loads(out.read_text())["plugins"][0]["id"] == f"{OWNER}/{row[0]}"
    assert main([*args, "--check"]) == 0
    reg.listing("unowned", [], raw={"id": f"{OWNER}/unowned", "repository": "https://github.com/other/unowned",
                                    "versions": [{"version": "1.0.0", "commit": _commit(7)}]})
    assert main([*args, "--check"]) == 1
    assert "refused: pipelines/example-dev/unowned.yaml" in capsys.readouterr().out


def _this_repository(url):
    """The project's own listings point at commits of this repository. Read
    each manifest at its own commit with git, so the test needs no network.
    A shallow clone without that commit skips: CI's build step
    (build_registry_index.py --check) reads the real commits from GitHub."""
    m = re.match(r"https://raw\.githubusercontent\.com/ColinGPT9/clips-studio/([0-9a-f]{40})/(.+)$", url)
    if not m:
        raise AssertionError(f"the committed catalog should need no fetch: {url}")
    commit, path = m.groups()
    shown = subprocess.run(["git", "-C", str(ROOT), "show", f"{commit}:{path}"], capture_output=True,
                           text=True, encoding="utf-8")
    if shown.returncode != 0:
        pytest.skip(f"commit {commit[:7]} is not in this clone; CI's --check step reads it from GitHub")
    return shown.stdout


def _in_this_repository(owner, repo, commit):
    """The branch check, for the same commits: this clone has them (the
    --check step makes the real check, against GitHub's branches)."""
    assert (owner, repo) == ("ColinGPT9", "clips-studio"), f"the committed catalog lists {owner}/{repo}"
    if subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{commit}^{{commit}}"],
                      capture_output=True).returncode != 0:
        pytest.skip(f"commit {commit[:7]} is not in this clone; CI's --check step checks it on GitHub")
    return True


def test_the_committed_catalog_is_up_to_date():
    from scripts.build_registry_index import readme_for

    folder = ROOT / "awesome-clips-kitty"
    index, problems = registry.build_index(folder, fetch=_this_repository, on_branch=_in_this_repository)
    assert problems == []
    hint = "run python scripts/build_registry_index.py"
    assert (folder / "index.json").read_text(encoding="utf-8") == registry.index_text(index), hint
    readme = (folder / "README.md").read_text(encoding="utf-8")
    assert readme_for(index, readme) == readme, hint


# ---- search --------------------------------------------------------------------------------


@pytest.mark.parametrize("query, first", [
    ("Marvel Rivals", "marvel-rivals-highlights"),
    ("World of Warcraft PvP", "wow-pvp-highlights"),
    ("wow pvp", "wow-pvp-highlights"),
    ("Minecraft", "minecraft-survival"),
    ("Rocket League", "rocket-league-goals"),
    ("Soccer goals", "soccer-goals"),
    ("football", "soccer-goals"),
    ("NHL", "nhl-goals"),
    ("podcast", "podcast-shorts"),
    ("Podcast shorts", "podcast-shorts"),
    ("Twitch", "twitch-stream-highlights"),
    ("captions", "karaoke-moments"),
])
def test_specific_searches_find_the_specialised_listing_first(catalogue, tmp_path, query, first):
    found = registry.search(registry.listings(tmp_path / "data", [], bundled=catalogue), query)
    assert found and found[0]["id"] == f"{OWNER}/{first}", [p["id"] for p in found]


def test_wow_finds_both_wow_pipelines_before_anything_broad(catalogue, tmp_path):
    found = [p["id"].split("/")[1] for p in registry.search(registry.listings(tmp_path, [], bundled=catalogue), "WoW")]
    assert set(found[:2]) == {"wow-pvp-highlights", "wow-mythic-plus"}
    assert "soccer-goals" not in found


def test_highlights_and_filters(catalogue, tmp_path):
    every = registry.listings(tmp_path, [], bundled=catalogue)
    names = [p["id"].split("/")[1] for p in registry.search(every, "highlights")]
    assert {"marvel-rivals-highlights", "wow-pvp-highlights", "sports-highlights", "gaming-highlights",
            "twitch-stream-highlights"} <= set(names)
    assert {p["id"].split("/")[1] for p in registry.search(every, category="sports")} == {
        "soccer-goals", "nhl-goals", "sports-highlights"}
    assert [p["id"].split("/")[1] for p in registry.search(every, tag="twitch")] == ["twitch-stream-highlights"]
    assert registry.search(every, kind="caption-style") == []
    assert len(registry.search(every, "")) == len(CATALOGUE)
    assert registry.search(every, "zzz nothing") == []


# ---- the client: addresses, cache, offline ------------------------------------------------------


def test_index_addresses_come_from_settings_and_must_be_https():
    assert registry.index_urls({}) == []
    config = {"plugins": {"registry_urls": ["https://example.com/index.json", "http://example.com/x", 5]}}
    assert registry.index_urls(config) == ["https://example.com/index.json"]


def test_refresh_caches_the_last_good_copy_and_works_offline(catalogue, tmp_path):
    data = tmp_path / "data"
    url = "https://example.com/clips-kitty/index.json"
    empty = tmp_path / "bundled.json"
    empty.write_text('{"format": 1, "plugins": [], "blocklist": []}')

    def fetch_ok(u, path):
        Path(path).write_bytes(catalogue.read_bytes())

    assert registry.refresh(data, [url], fetcher=fetch_ok) == [{"url": url, "ok": True, "error": None}]
    assert len(registry.listings(data, [url], bundled=empty)) == len(CATALOGUE)

    def offline(u, path):
        raise sources.SourceError("the download failed (no network)")

    def broken(u, path):
        Path(path).write_text('{"format": 99}')

    for fetcher, fragment in ((offline, "no network"), (broken, "not a Clips Kitty registry index")):
        (status,) = registry.refresh(data, [url], fetcher=fetcher)
        assert status["ok"] is False and fragment in status["error"]
        assert len(registry.listings(data, [url], bundled=empty)) == len(CATALOGUE)  # last good copy
    kinds = [(i["url"], i["index"] is not None) for i in registry.indexes(data, [url, "https://example.com/never"],
                                                                         bundled=empty)]
    assert kinds == [("bundled", True), (registry.ONLINE_URL, False), (url, True), ("https://example.com/never", False)]


def test_a_plugin_in_two_indexes_comes_from_the_first(catalogue, tmp_path):
    data = tmp_path / "data"
    other = json.loads(catalogue.read_text())
    other["plugins"] = [{**other["plugins"][0], "name": "Imposter"}]
    url = "https://example.com/other.json"
    registry.refresh(data, [url], fetcher=lambda u, p: Path(p).write_text(json.dumps(other)))
    found = {p["id"]: p for p in registry.listings(data, [url], bundled=catalogue)}
    assert found[other["plugins"][0]["id"]]["name"] != "Imposter" and found[other["plugins"][0]["id"]]["index"] == "bundled"


# ---- Clips Kitty's online list ---------------------------------------------------------------------


def _listing(name, *versions, owner=OWNER, **extra):
    """An index's listing: versions as (version, commit number), newest first."""
    return {"id": f"{owner.lower()}/{name}", "name": name.replace("-", " ").title(), "publisher": owner.lower(),
            "repository": f"https://github.com/{owner}/{name}", "path": ".",
            "versions": [{"version": v, "commit": _commit(n)} for v, n in versions], **extra}


def _index(plugins=(), *, catalog=(), blocklist=(), metrics_at="2026-10-05", **extra):
    return {"format": 1, "plugins": list(plugins), "catalog": list(catalog), "blocklist": list(blocklist),
            "metrics_at": metrics_at, **extra}


def _online(data, index):
    """Put `index` in the cache as Clips Kitty's online list, the way the app fetches it."""
    status = registry.refresh_online(data, fetcher=lambda u, p: Path(p).write_text(json.dumps(index)))
    assert status == {"url": registry.ONLINE_URL, "ok": True, "error": None}


LABELLED = {"badges": ["community", "compatible", "featured"], "featured": {"reason": "notable"},
            "metrics": {"installs": 120, "github": {"stars": 5}}}


def test_the_online_list_brings_new_pipelines_as_community_and_never_counts_them(tmp_path):
    data, bundled = tmp_path / "data", tmp_path / "index.json"
    bundled.write_text(json.dumps(_index([_listing("kept", ("1.0.0", 1), **LABELLED)], counter={
        "install": "https://github.com/ColinGPT9/awesome-clips-kitty/releases/download/installs/{asset}"})))
    _online(data, _index([
        _listing("kept", ("1.0.0", 1)),  # the same version: the bundled listing stays, labels and all
        _listing("brand-new", ("0.1.0", 2), **LABELLED, checks={"manifest_valid": True, "official_repository": True}),
        _listing("clips-studio", ("9.0.0", 3), owner="ColinGPT9"),  # the project's own: only with the app
    ], counter={"install": "https://github.com/ColinGPT9/awesome-clips-kitty/releases/download/x/{asset}"}))

    found = {p["id"]: p for p in registry.listings(data, [], bundled=bundled)}
    assert list(found) == [f"{OWNER}/kept", f"{OWNER}/brand-new"]
    kept, new = found[f"{OWNER}/kept"], found[f"{OWNER}/brand-new"]
    assert kept["index"] == "bundled" and kept["badges"] == ["community", "compatible", "featured"]
    assert kept["metrics"]["installs"] == 120
    assert new["index"] == registry.ONLINE_URL and new["badges"] == ["community"]
    assert "featured" not in new and "installs" not in new["metrics"] and new["metrics"]["github"] == {"stars": 5}
    assert new["checks"] == {"manifest_valid": True}
    assert registry.listing_tier(new) == "listed"
    assert registry.counter_for(data, [], registry.ONLINE_URL, bundled=bundled) is None
    assert registry.counter_for(data, [], "bundled", bundled=bundled)
    # what the Marketplace lists, in the order the lists count
    kinds = [(i["kind"], i["url"], i["index"] is not None) for i in registry.indexes(data, [], bundled=bundled)]
    assert kinds == [("bundled", "bundled", True), ("online", registry.ONLINE_URL, True)]


@pytest.mark.parametrize("online_version, from_online", [("1.1.0", True), ("1.0.0", False), ("0.9.0", False)])
def test_a_newer_version_online_wins_and_shows_as_community_until_a_release(tmp_path, online_version, from_online):
    data, bundled = tmp_path / "data", tmp_path / "index.json"
    bundled.write_text(json.dumps(_index([_listing("tool", ("1.0.0", 1), **LABELLED)])))
    _online(data, _index([_listing("tool", (online_version, 2), ("1.0.0", 1))]))
    (listing,) = registry.listings(data, [], bundled=bundled)
    assert listing["index"] == (registry.ONLINE_URL if from_online else "bundled")
    assert listing["latest"] == (online_version if from_online else "1.0.0")
    assert listing["badges"] == (["community"] if from_online else ["community", "compatible", "featured"])
    assert registry.find(data, [], f"{OWNER}/tool", bundled=bundled)[1]["version"] == listing["latest"]


def test_an_online_block_applies_and_cant_lift_a_bundled_one(tmp_path):
    data, bundled = tmp_path / "data", tmp_path / "index.json"
    block = {"id": f"{OWNER}/a", "versions": ["1.0.0"], "severity": "blocked", "reason": "steals keys", "date": "2026-10-07"}
    bundled.write_text(json.dumps(_index([_listing("a", ("1.0.0", 1)), _listing("b", ("1.0.0", 2))], blocklist=[block])))
    _online(data, _index([_listing("a", ("1.0.0", 1)), _listing("b", ("1.0.0", 2))],
                         blocklist=[{**block, "id": f"{OWNER}/b"}]))
    assert registry.listings(data, [], bundled=bundled) == []
    assert registry.blocked_check(data, bundled=bundled)(f"{OWNER}/b", "1.0.0")["reason"] == "steals keys"


def test_an_online_copy_older_than_this_release_adds_nothing_but_its_blocks(tmp_path):
    data, bundled = tmp_path / "data", tmp_path / "index.json"
    bundled.write_text(json.dumps(_index([_listing("a", ("2.0.0", 1))], metrics_at="2026-10-12")))
    block = {"id": f"{OWNER}/a", "versions": "*", "severity": "delisted", "reason": "gone", "date": "2026-10-01"}
    _online(data, _index([_listing("a", ("3.0.0", 2)), _listing("removed-since", ("1.0.0", 3))],
                         blocklist=[{**block, "id": f"{OWNER}/old"}], metrics_at="2026-10-05"))
    assert [p["id"] for p in registry.listings(data, [], bundled=bundled)] == [f"{OWNER}/a"]
    assert registry.listings(data, [], bundled=bundled)[0]["latest"] == "2.0.0"
    online = registry.indexes(data, [], bundled=bundled)[1]
    assert online["kind"] == "online" and online["index"] is None
    assert registry.blocked_check(data, bundled=bundled)(f"{OWNER}/old", "1.0.0")  # blocks are never set aside


def _entry(slug, *, owner="someone", kind="app", **extra):
    return {"id": f"{catalog.FOLDER_OF_KIND[kind]}/{slug}", "kind": kind, "name": slug.title(), "license": "MIT",
            "source": {"github": f"https://github.com/{owner}/{slug}"}, **extra}


def test_the_online_list_adds_directory_entries_and_sections_but_not_the_projects_own(tmp_path):
    data, bundled = tmp_path / "data", tmp_path / "index.json"
    sections = {"app": {"sections": [{"id": "editors", "title": "Editors"}], "wanted": []}}
    bundled.write_text(json.dumps(_index(catalog=[_entry("cutter", featured={"reason": "x"}, badges=["featured"],
                                                         metrics={"installs": 3})], sections=sections)))
    _online(data, _index(catalog=[_entry("cutter", name="Changed"), _entry("newcomer", section="games",
                                                                         badges=["official", "featured"],
                                                                         metrics={"installs": 9}),
                                  _entry("clips-studio", owner="ColinGPT9")],
                         sections={"app": {"sections": [{"id": "editors", "title": "Renamed"},
                                                        {"id": "games", "title": "Games"}]},
                                   "tool": {"sections": [{"id": "cli", "title": "Command line"}]}}))
    entries, merged = registry.catalog_entries(data, [], bundled=bundled)
    by_id = {e["id"]: e for e in entries}
    assert list(by_id) == ["apps/cutter", "apps/newcomer"]
    assert by_id["apps/cutter"]["name"] == "Cutter" and by_id["apps/cutter"]["badges"] == ["community", "featured"]
    newcomer = by_id["apps/newcomer"]
    assert newcomer["index"] == registry.ONLINE_URL and newcomer["badges"] == ["community"]
    assert "featured" not in newcomer and "installs" not in newcomer["metrics"]
    assert merged["app"]["sections"] == [{"id": "editors", "title": "Editors"}, {"id": "games", "title": "Games"}]
    assert merged["tool"] == {"sections": [{"id": "cli", "title": "Command line"}]}


def test_the_online_list_is_checked_once_a_day_retried_hourly_and_can_be_switched_off(tmp_path):
    data, bundled = tmp_path / "data", tmp_path / "index.json"
    bundled.write_text(json.dumps(_index()))
    now = 1_800_000_000.0
    assert registry.online_checks_on(data) and registry.online_due(data, now=now)  # never fetched

    def offline(u, p):
        raise sources.SourceError("the download failed (no network)")

    status = registry.refresh_online(data, fetcher=offline, now=now)
    assert status["ok"] is False
    view = registry.online_status(data)
    assert view["fetched_at"] is None and "no network" in view["error"] and view["automatic"] is True
    assert not registry.online_due(data, now=now + 30 * 60)  # offline: not again on every open
    assert registry.online_due(data, now=now + 2 * 3600)

    _online(data, _index())  # fetched now (the real clock)
    assert registry.online_status(data)["error"] is None and registry.online_status(data)["fetched_at"]
    real = registry._seconds(registry.online_status(data)["fetched_at"])
    assert not registry.online_due(data, now=real + 23 * 3600)
    assert registry.online_due(data, now=real + 25 * 3600)
    assert registry.online_due(data, now=real - 3600)  # a clock set back doesn't stop the checks

    registry.set_online_checks(data, False)
    assert not registry.online_checks_on(data) and not registry.online_due(data, now=real + 30 * 86400)
    assert registry.online_status(data)["automatic"] is False
    registry.set_online_checks(data, True)
    assert registry.online_due(data, now=real + 30 * 86400)


def test_the_online_list_is_the_catalogs_index_on_the_main_branch():
    """The address is the committed index's own place on the project's main
    branch, so what it serves is exactly what was merged (D29)."""
    assert registry.ONLINE_URL == ("https://raw.githubusercontent.com/ColinGPT9/clips-studio/main/"
                                   + registry.CATALOG_FOLDER + "/index.json")
    assert registry.bundled_path().relative_to(ROOT).as_posix() == registry.CATALOG_FOLDER + "/index.json"
    assert registry.index_urls({"plugins": {"registry_urls": [registry.ONLINE_URL]}}) == []


def test_the_privacy_policy_tells_people_about_the_list_check():
    text = (ROOT / "site" / "privacy.html").read_text(encoding="utf-8")
    assert "Checking for new pipelines" in text and "raw.githubusercontent.com" in text


# ---- installing from a listing, and the block list reaching installed copies --------------------------


def _archive_fetcher(files: dict, commit: str, top: str):
    def fetcher(url, path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit}) as tar:
            for name, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo(f"{top}-{commit}/{name}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        Path(path).write_bytes(buf.getvalue())
    return fetcher


def test_install_from_a_listing_is_listed_and_checked_against_it(reg, tmp_path, monkeypatch):
    row = CATALOGUE[6]
    m = _manifest(*row)
    (entry,) = reg.listing(row[0], [("1.0.0", m)], path="plugin")
    index, _ = reg.build()
    bundled = tmp_path / "index.json"
    bundled.write_text(registry.index_text(index))
    data = tmp_path / "data"
    listing, version = registry.find(data, [], f"{OWNER}/{row[0]}", bundled=bundled)
    source = registry.source_for(listing, version)
    assert source == {"kind": "git", "url": f"https://github.com/{OWNER}/{row[0]}", "commit": entry["commit"],
                      "path": "plugin"}
    monkeypatch.setattr(sources, "find_git", lambda: None)  # GitHub's archive, from a fake fetcher
    files = {"plugin/clipskitty.yaml": yaml.safe_dump(m), "plugin/src/main.py": "print()\n", "README.md": "repo\n"}
    fetcher = _archive_fetcher(files, entry["commit"], row[0])
    plan = manager.plan(data, source, app_version=APP, tier="listed", fetcher=fetcher, listed_in=listing["index"],
                        expect={"id": listing["id"], "version": version["version"]})
    assert plan["ok"], plan["errors"]
    assert plan["details"]["tier_text"] == "Community · not reviewed by a person"
    view = manager.install(data, plan["plan_id"], app_version=APP)
    assert view["details"]["tier"] == "listed" and view["source"]["listed_in"] == "bundled"
    folder = store.get(data, listing["id"]).folder
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == [
        "clipskitty.yaml", "src/main.py"]
    # The files must be the ones listed.
    plan = manager.plan(data, source, app_version=APP, fetcher=fetcher, expect={"id": listing["id"], "version": "2.0.0"})
    assert plan["errors"] == ["the listing says version 2.0.0, the files say 1.0.0"]


def test_a_plugin_in_a_folder_of_its_repository_installs_from_git(tmp_path, plugin_source):
    repo = plugin_source.repo()
    plugin_source.write(repo / "plugins" / "one", plugin_source.manifest(), {"src/main.py": "print(1)\n"})
    plugin_source.write(repo, None, {"README.md": "a repository of several plugins\n"})
    plugin_source.git(repo, "add", "-A")
    plugin_source.git(repo, "commit", "-q", "-m", "two plugins")
    commit = plugin_source.git(repo, "rev-parse", "HEAD")
    data = tmp_path / "data"
    source = {"kind": "git", "url": repo.resolve().as_uri(), "commit": commit, "path": "plugins/one"}
    plan = manager.plan(data, source, app_version=APP)
    assert plan["ok"], plan["errors"]
    assert plan["source_text"].endswith(f" · plugins/one · commit {commit[:7]}")
    manager.install(data, plan["plan_id"], app_version=APP)
    folder = store.get(data, "fixture-dev/manager-test").folder
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == [
        "clipskitty.yaml", "src/main.py"]
    with pytest.raises(manager.ManagerError, match="the commit has no folder plugins/two"):
        manager.plan(data, {**source, "path": "plugins/two"}, app_version=APP)
    with pytest.raises(manager.ManagerError, match="outside the plugin's folder"):
        manager.plan(data, {**source, "path": "../elsewhere"}, app_version=APP)


def test_a_block_reaches_an_installed_copy_without_deleting_it(tmp_path, plugin_source):
    data = tmp_path / "data"
    folder = plugin_source.folder()
    plan = manager.plan(data, {"kind": "folder", "path": str(folder)}, app_version=APP)
    manager.install(data, plan["plan_id"], app_version=APP)
    pid = "fixture-dev/manager-test"
    url = "https://example.com/index.json"

    def publish(severity):
        index = {"format": 1, "plugins": [], "blocklist": [
            {"id": pid, "versions": ["1.0.0"], "severity": severity, "reason": "Sends videos it does not declare",
             "date": "2026-10-07"}]}
        registry.refresh(data, [url], fetcher=lambda u, p: Path(p).write_text(json.dumps(index)))

    publish("delisted")
    assert manager.view(data, pid, app_version=APP, blocked=registry.blocked_check(data))["flag"] == {
        "severity": "delisted", "reason": "Sends videos it does not declare"}
    assert store.installed_choice(data, {"id": pid}).id == pid  # delisted still runs
    publish("blocked")
    view = manager.view(data, pid, app_version=APP, blocked=registry.blocked_check(data))
    assert view["flag"]["severity"] == "blocked"
    with pytest.raises(ValueError, match=r"is blocked: Sends videos it does not declare\. Remove it in Marketplace › Installed\."):
        store.installed_choice(data, {"id": pid})
    assert store.get(data, pid).folder.is_dir()  # flagged and refused, never deleted
    # The block keeps applying after the address is removed from settings.
    assert registry.blocked_check(data)(pid, "1.0.0")["severity"] == "blocked"
