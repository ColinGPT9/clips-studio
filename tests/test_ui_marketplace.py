"""The Marketplace screen's logic (ui/src/renderer/src/lib/marketplace.ts).

The screen shows what the engine says about a plugin; this file decides how
it is grouped, which boxes must be ticked before Install, and how declared
requirements compare with this PC. The TypeScript runs under Node here and is
fed what the engine really produces (plugins/permissions.py,
plugins/sources.py, the manifest validator), so the two can't drift apart
without a test failing.
"""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from plugins import permissions, sources
from plugins._sdk import manifest

LIB = Path(__file__).resolve().parent.parent / "ui" / "src" / "renderer" / "src" / "lib"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _run(tmp_path, body: str, data=None):
    """Run `body` (JavaScript: `m` is the module, `data` the JSON given) and
    return what it prints as JSON."""
    module = tmp_path / "marketplace.ts"
    module.write_text((LIB / "marketplace.ts").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps(data), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps(module.as_uri())});"
        "const fs = await import('node:fs');"
        f"const data = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'data.json'))}, 'utf8'));"
        f"console.log(JSON.stringify((() => {{ {body} }})()));"
    )
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _manifest(**extra) -> dict:
    data = {"id": "example-dev/demo", "name": "Demo", "version": "1.0.0", "execution": "local",
            "permissions": ["video.read"]}
    data.update(extra)
    return data


# ---- in step with the engine ----------------------------------------------------------


def test_categories_and_kinds_match_the_manifest(tmp_path):
    got = _run(tmp_path, "return [Object.keys(m.CATEGORY_LABELS), Object.keys(m.KIND_LABELS)]")
    assert got[0] == list(manifest.CATEGORIES)
    assert got[1] == list(manifest.KINDS) + list(manifest.PLANNED_KINDS)


def test_the_details_type_has_every_field_the_engine_sends():
    text = (LIB / "marketplace.ts").read_text(encoding="utf-8")
    body = re.search(r"export interface PluginDetails \{(.*?)\n\}", text, re.S).group(1)
    fields = set(re.findall(r"^\s+(\w+):", body, re.M))
    assert fields == set(permissions.describe(_manifest()))


def test_every_tier_and_execution_gets_a_badge_in_the_engines_words(tmp_path):
    details = [permissions.describe(_manifest(execution=e), tier=tier)
               for tier in permissions.TIERS for e in (*manifest.EXECUTIONS, None)]
    got = _run(tmp_path, "return data.map((d) => [m.tierBadge(d), m.executionBadge(d.execution, d.execution_text)])",
               details)
    for d, (tier, execution) in zip(details, got, strict=True):
        assert tier["label"] == permissions.TIERS[d["tier"]]
        assert tier["tone"] == {"official": "ok", "listed-official": "ok", "listed": "info", "link": "warn"}[d["tier"]]
        assert execution["title"] == d["execution_text"]
        assert execution["tone"] == {"local": "ok", "remote": "warn", "hybrid": "warn", None: "danger"}[d["execution"]]


# ---- what must be ticked before Install ------------------------------------------------


def test_install_asks_for_what_the_plugin_is_and_does(tmp_path):
    remote = _manifest(execution="remote", network=["api.example.com"],
                       sends=[{"data": "audio", "to": "Example Cloud"}],
                       service={"name": "Example Cloud", "url": "https://example.com", "required": True})
    plans = [{"details": permissions.describe(_manifest(), tier="official")},
             {"details": permissions.describe(_manifest(), tier="link")},
             {"details": permissions.describe(_manifest(), tier="listed")},
             {"details": permissions.describe(remote, tier="listed")}]
    got = _run(tmp_path, "return data.map((p) => m.confirmations(p))", plans)
    assert got[0] == []
    assert len(got[1]) == 1 and "has not checked it" in got[1][0]
    assert len(got[2]) == 1 and "nobody has reviewed" in got[2][0]
    assert len(got[3]) == 3 and "sends data off this PC" in got[3][1] and "account" in got[3][2]


def test_an_update_lists_new_permissions_hosts_and_data_leaving(tmp_path):
    old = _manifest(version="1.0.0")
    new = _manifest(version="1.1.0", execution="hybrid", permissions=["video.read", "network"],
                    network=["api.example.com"], sends=[{"data": "frames", "to": "Example Cloud"}])
    plan = {"plugin": {"version": "1.1.0"}, "details": permissions.describe(new, tier="listed"),
            "update": {"from": "1.0.0", "direction": "update", **permissions.changes(old, new)}}
    lines = _run(tmp_path, "return m.updateLines(data)", plan)
    texts = [line["text"] for line in lines]
    assert texts[0].startswith("Updates 1.0.0 to 1.1.0")
    assert "New permission: Connects to: api.example.com" in texts
    assert "Now connects to: api.example.com" in texts
    assert {"text": "New: ⚠ Sends frames from your video to Example Cloud", "tone": "danger"} in lines
    assert any(t.startswith("Where it runs has changed") for t in texts)
    plan["update"]["direction"] = "downgrade"
    assert _run(tmp_path, "return m.updateLines(data)[0].tone", plan) == "warn"
    assert _run(tmp_path, "return m.updateLines({...data, update: null})", plan) == []


# ---- this PC against what a plugin needs ------------------------------------------------


@pytest.mark.parametrize("req, hw, expected", [
    ({"gpu": "required", "vram_gb": 8},
     {"gpu": {"name": "Card", "vram_total": 12e9}, "disk_free_bytes": None, "platform": "win32"},
     ["yes", "yes"]),
    ({"gpu": "required", "vram_gb": 8},
     {"gpu": {"name": "Card", "vram_total": 6e9}, "disk_free_bytes": None, "platform": "win32"},
     ["yes", "no"]),
    # No NVIDIA card seen: another make may be there, so "can't tell", not "no".
    ({"gpu": "required", "vram_gb": 8}, {"gpu": None, "disk_free_bytes": None, "platform": "win32"},
     ["unknown", "unknown"]),
    ({"gpu": "optional", "ram_gb": 16}, None, ["yes", "unknown"]),
    ({"disk_gb": 10}, {"gpu": None, "disk_free_bytes": 50e9, "platform": "win32"}, ["yes"]),
    ({"disk_gb": 10}, {"gpu": None, "disk_free_bytes": 2e9, "platform": "win32"}, ["no"]),
    ({"os": ["windows"]}, {"gpu": None, "disk_free_bytes": None, "platform": "win32"}, ["yes"]),
    ({"os": ["linux", "macos"]}, {"gpu": None, "disk_free_bytes": None, "platform": "win32"}, ["no"]),
    ({"os": ["windows"]}, None, ["unknown"]),
    ({"software": ["Docker"]}, None, ["unknown"]),
])
def test_hardware_fit(tmp_path, req, hw, expected):
    got = _run(tmp_path, "return m.hardwareFit(data.req, data.hw)", {"req": req, "hw": hw})
    assert [line["fit"] for line in got] == expected


def test_python_and_the_engines_problems_join_the_needs(tmp_path):
    details = permissions.describe(_manifest(run={"command": ["{python}", "main.py"]}))
    assert details["needs_python"] and "Python 3 installed on this PC" in details["requirements"]
    missing = [{"need": "python", "text": "It needs Python, and none was found on this PC"},
               {"need": "app", "text": "It needs Clips Kitty >=9.0, and this is 2.0.0"}]
    got = _run(tmp_path, "return [null, [], data[1]].map((p) => m.needLines(undefined, null, data[0], p))",
               [details, missing])
    assert got[0] == [{"text": "Python 3 installed on this PC", "fit": "unknown"}]
    assert got[1] == [{"text": "Python 3 installed on this PC", "fit": "yes"}]
    assert got[2] == [{"text": m["text"], "fit": "no"} for m in missing]
    no_python = permissions.describe(_manifest(run={"command": ["bin/tool.exe"]}))
    assert _run(tmp_path, "return m.needLines(undefined, null, data, [])", no_python) == []


def test_the_card_summary_never_says_it_fits_when_something_is_unknown(tmp_path):
    got = _run(tmp_path, "return data.map((l) => m.fitSummary(l))", [
        [], [{"text": "a", "fit": "yes"}], [{"text": "a", "fit": "yes"}, {"text": "b", "fit": "unknown"}],
        [{"text": "a", "fit": "unknown"}, {"text": "b", "fit": "no"}]])
    assert got[0] is None
    assert [g["tone"] for g in got[1:]] == ["ok", "warn", "danger"]


# ---- links from the developer -----------------------------------------------------------


def test_only_plain_https_links_are_offered_each_once(tmp_path):
    listing = {
        "repository": "https://github.com/example-dev/example-plugin",
        "links": {"docs": "https://example.com/docs",
                  "funding": ["https://example.com/sponsor", "http://example.com/plain", "javascript:alert(1)"]},
        "author": {"name": "Example Dev", "url": "https://user:pass@example.com/"},
        "service": {"name": "Example Cloud", "url": "https://example.com/docs"},
        "examples": [{"title": "A clip", "url": "https://example.com/clip.mp4"}],
    }
    got = _run(tmp_path, "return m.listingLinks(data)", listing)
    assert [(link["label"], link["url"]) for link in got] == [
        ("Source code", "https://github.com/example-dev/example-plugin"),
        ("Documentation", "https://example.com/docs"),
        ("Example: A clip", "https://example.com/clip.mp4"),
        ("Support the developer", "https://example.com/sponsor"),
    ]


def test_only_the_checks_the_index_build_runs_are_shown_and_a_failed_one_stays(tmp_path):
    # the checks registry.build writes (test_registry pins the same dict)
    built = {"manifest_valid": True, "publisher_is_repository_owner": True, "commit_pinned": True,
             "public_at_commit": True}
    got = _run(tmp_path, "return m.checkLines(data)", built)
    assert len(got) == 4 and all(line["ok"] for line in got)
    # another index can send anything: an unknown claim is not a tick, a failure isn't dropped
    other = {"Security audited by the Clips Kitty team": True, "manifest_valid": False, "commit_pinned": True}
    got = _run(tmp_path, "return m.checkLines(data)", other)
    assert [line["ok"] for line in got] == [False, True]
    assert not any("audit" in line["text"].lower() for line in got)


# ---- the Generate bar's pipeline choice ---------------------------------------------------


def test_only_pipelines_that_can_run_are_offered(tmp_path):
    base = {"kind": "pipeline", "enabled": True, "problem": None, "flag": None}
    plugins = [{**base, "id": "a/ok"}, {**base, "id": "a/off", "enabled": False},
               {**base, "id": "a/broken", "problem": "it needs Clips Kitty >=9"},
               {**base, "id": "a/blocked", "flag": {"severity": "blocked"}},
               {**base, "id": "a/delisted", "flag": {"severity": "delisted"}},
               {**base, "id": "a/caption", "kind": "caption-style"}]
    assert _run(tmp_path, "return m.usablePipelines(data).map((p) => p.id)", plugins) == ["a/ok", "a/delisted"]


SETTING_CASES = [
    ({"type": "integer", "minimum": 1, "maximum": 6}, "3", 3),
    ({"type": "integer", "minimum": 1, "maximum": 6}, "9", 9),
    ({"type": "integer", "minimum": 1, "maximum": 6}, "0", 0),
    ({"type": "integer"}, "2.5", 2.5),
    ({"type": "number", "minimum": 0.5}, "0.75", 0.75),
    ({"type": "number", "minimum": 0.5}, "0.25", 0.25),
    ({"type": "number", "minimum": 0.25}, ".5", 0.5),
    ({"type": "integer", "maximum": 5000}, "1e3", 1000),
    ({"type": "choice", "options": ["fast", "best"]}, "best", "best"),
    ({"type": "choice", "options": ["fast", "best"]}, "other", "other"),
    ({"type": "string", "max_length": 5}, "short", "short"),
    ({"type": "string", "max_length": 5}, "too long", "too long"),
    ({"type": "string", "max_length": 5}, "hello ", "hello "),
    ({"type": "boolean"}, True, True),
]


def test_a_setting_is_refused_exactly_when_the_engine_would_refuse_it(tmp_path):
    got = _run(tmp_path, "return data.map(([spec, raw]) => m.settingValue(spec, raw))",
               [[spec, raw] for spec, raw, _ in SETTING_CASES])
    for (spec, _raw, value), result in zip(SETTING_CASES, got, strict=True):
        engine = manifest.setting_value_problem(spec, value)
        assert (result.get("problem") is None) == (engine is None), (spec, value, result, engine)
        if engine is None:
            assert result["value"] == value


def test_text_that_is_not_a_number_is_refused(tmp_path):
    got = _run(tmp_path, "return ['', 'abc', 'Infinity', '1e3', '.5', '3 ']"
                         ".map((r) => m.settingValue({type: 'number'}, r))")
    assert [g.get("value") for g in got] == [None, None, None, 1000, 0.5, 3]


def test_only_settings_changed_from_their_defaults_go_in_the_job(tmp_path):
    settings = {"min_kills": {"type": "integer", "default": 3}, "mode": {"type": "choice", "options": ["a", "b"],
                                                                         "default": "a"},
                "api_key": {"type": "secret"}, "note": {"type": "string"}}
    got = _run(tmp_path, "return [m.jobSettings(data).map(([n]) => n), "
                         "m.chosenSettings(data, {min_kills: 3, mode: 'b', api_key: 'x', note: 'hi'})]", settings)
    assert got[0] == ["min_kills", "mode", "note"]
    assert got[1] == {"mode": "b", "note": "hi"}


def test_a_saved_choice_keeps_only_settings_the_installed_version_accepts(tmp_path):
    # after an update renamed `min_kills`, narrowed `level` and changed `mode`'s options
    settings = {"level": {"type": "integer", "maximum": 5}, "mode": {"type": "choice", "options": ["a", "c"]},
                "loud": {"type": "boolean"}, "note": {"type": "string", "max_length": 4},
                "api_key": {"type": "secret"}}
    saved = {"min_kills": 3, "level": 9, "mode": "b", "loud": "yes", "note": "ok", "api_key": "x"}
    got = _run(tmp_path, "return m.fittingSettings(data[0], data[1])", [saved, settings])
    assert got == {"note": "ok"}
    kept = {"level": 4, "mode": "c", "loud": False, "note": "fine"}
    assert _run(tmp_path, "return m.fittingSettings(data[0], data[1])", [kept, settings]) == kept
    for name, value in kept.items():
        assert manifest.setting_value_problem(settings[name], value) is None


# ---- installing from a link -----------------------------------------------------------------


@pytest.mark.parametrize("link, commit, folder, expected", [
    ("https://github.com/example-dev/example-plugin", COMMIT, "",
     {"kind": "git", "url": "https://github.com/example-dev/example-plugin", "commit": COMMIT}),
    (f"https://github.com/example-dev/monorepo/tree/{COMMIT}/plugins/demo", "", "",
     {"kind": "git", "url": "https://github.com/example-dev/monorepo", "commit": COMMIT, "path": "plugins/demo"}),
    ("https://github.com/example-dev/example-plugin.git", COMMIT.upper(), "/sub/",
     {"kind": "git", "url": "https://github.com/example-dev/example-plugin", "commit": COMMIT, "path": "sub"}),
    ("https://git.example.org/team/plugin", COMMIT, "",
     {"kind": "git", "url": "https://git.example.org/team/plugin", "commit": COMMIT}),
    # the GitHub link pasted into the commit box instead
    ("", f"https://github.com/example-dev/monorepo/tree/{COMMIT}/plugins/demo", "",
     {"kind": "git", "url": "https://github.com/example-dev/monorepo", "commit": COMMIT, "path": "plugins/demo"}),
    ("https://github.com/example-dev/monorepo", f"https://www.github.com/example-dev/monorepo/commit/{COMMIT}", "x",
     {"kind": "git", "url": "https://github.com/example-dev/monorepo", "commit": COMMIT, "path": "x"}),
])
def test_a_pasted_link_becomes_a_source_the_engine_accepts(tmp_path, link, commit, folder, expected):
    got = _run(tmp_path, "return m.gitSource(data[0], data[1], data[2])", [link, commit, folder])
    assert got == {"source": expected}
    assert sources.clean_source(got["source"]) == expected


@pytest.mark.parametrize("link, commit, folder, fragment", [
    ("https://github.com/example-dev/example-plugin", "main", "", "40-character commit"),
    ("https://github.com/example-dev/example-plugin", COMMIT[:7], "", "40-character commit"),
    (f"https://github.com/example-dev/x/tree/{COMMIT}", "f" * 40, "", "different commits"),
    ("http://github.com/example-dev/example-plugin", COMMIT, "", "https://"),
    ("git@github.com:example-dev/example-plugin.git", COMMIT, "", "https://"),
    ("https://github.com/example-dev/example-plugin", COMMIT, "../outside", "a path inside the repository"),
    ("https://github.com/example-dev/example-plugin/tree/main/plugins/demo", "", "", "a branch or tag"),
    ("https://github.com/example-dev/example-plugin", "https://github.com/example-dev/example-plugin", "",
     "40-character commit"),
    ("https://github.com/example-dev/a", f"https://github.com/example-dev/b/tree/{COMMIT}", "", "different repositories"),
    ("https://github.com/example-dev/example-plugin/pulls", COMMIT, "", "isn’t a repository"),
])
def test_a_link_the_engine_would_refuse_is_explained_first(tmp_path, link, commit, folder, fragment):
    got = _run(tmp_path, "return m.gitSource(data[0], data[1], data[2])", [link, commit, folder])
    assert "source" not in got and fragment in got["problem"]


# ---- small words ---------------------------------------------------------------------------------


def test_index_ages_sizes_and_slugs_read_as_words(tmp_path):
    # fetched_at as registry.refresh writes it
    now = 1_000_000_000
    stamps = [None, "not a time"] + [time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - ago))
                                     for ago in (30, 600, 3 * 3600, 3 * 86400)]
    got = _run(tmp_path, "return ["
                         "data.map((t) => m.fetchedText(t, 1_000_000_000_000)),"
                         "[2.5e9, 340e6, 2048, undefined].map((n) => m.formatBytes(n)),"
                         "['team_wipe', 'big-play'].map(m.slugLabel),"
                         "['bundled', 'https://example.com/index.json'].map(m.indexName)]", stamps)
    assert got[0] == ["not fetched yet", "not fetched yet", "updated just now", "updated 10 minutes ago",
                      "updated 3 hours ago", "updated 3 days ago"]
    assert got[1] == ["2.5 GB", "340 MB", "2 KB", ""]
    assert got[2] == ["Team wipe", "Big play"]
    assert got[3] == ["The list that came with Clips Kitty", "example.com"]


# ---- Awesome Clips Kitty: labels, numbers and credits ---------------------------------------


def test_the_directory_kinds_and_relationships_match_the_catalog(tmp_path):
    from plugins import catalog

    got = _run(tmp_path, "return [m.DIRECTORY_KINDS, m.RELATIONSHIP_LABELS]")
    assert got[0] == {kind: catalog.KIND_TITLES[kind] for kind in catalog.DIRECTORY_KINDS.values()}
    assert got[1] == catalog.RELATIONSHIPS


def test_badges_say_what_they_mean_and_a_listing_shows_only_the_extra_ones(tmp_path):
    from plugins import catalog

    got = _run(tmp_path, "return [m.catalogBadges(data), m.catalogBadges(data, true), m.catalogBadges(['verified'])]",
               ["official", "compatible", "featured"])
    assert [b["label"] for b in got[0]] == [catalog.BADGES[b] for b in ("official", "compatible", "featured")]
    assert [b["label"] for b in got[1]] == ["✓ Compatible", "★ Featured"]
    assert "not a security review" in got[0][1]["title"]
    assert got[2] == []  # an index can't invent a label


def test_an_official_listing_needs_no_trust_tick_and_a_community_one_does(tmp_path):
    plans = [{"details": permissions.describe(_manifest(), tier="listed-official")},
             {"details": permissions.describe(_manifest(), tier="listed")}]
    got = _run(tmp_path, "return data.map((p) => [m.confirmations(p), m.tierBadge(p.details)])", plans)
    assert got[0][0] == [] and got[0][1]["tone"] == "ok" and got[0][1]["label"].startswith("✓ Official")
    assert len(got[1][0]) == 1 and got[1][1]["label"] == "Community · not reviewed by a person"


def test_each_number_is_its_own_line(tmp_path):
    metrics = {"installs": 12482, "github": {"stars": 1234, "discussions": 3, "pushed_at": "2026-09-30"},
               "models": {"example-org/speech": {"downloads": 52000, "likes": 40}}, "stale": "archived"}
    got = _run(tmp_path, "return [m.metricLines(data), m.metricLines({installs: 1}), m.metricLines(undefined), "
                         "[0, 999, 1000, 1250, 1999999].map(m.shortCount)]", metrics)
    assert [line["text"] for line in got[0]] == [
        "12,482 Clips Kitty installs", "★ 1.2k on GitHub", "3 discussions on GitHub",
        "example-org/speech on Hugging Face: 52k downloads a month, 40 likes", "⚠ Archived by its authors"]
    assert got[0][-1]["tone"] == "warn"
    assert [line["text"] for line in got[1]] == ["1 Clips Kitty install"] and got[2] == []
    assert got[3] == ["0", "999", "1k", "1.3k", "2M"]


@pytest.mark.parametrize("source, link", [
    ({"github": "https://github.com/example-org/app"}, "https://github.com/example-org/app"),
    ({"github": "https://github.com/example-org/app", "path": "tools/cli"},
     "https://github.com/example-org/app/tree/HEAD/tools/cli"),
    ({"github": "https://github.com/example-org/app", "url": "https://github.com/example-org/app#readme"},
     "https://github.com/example-org/app#readme"),
    ({"github": "https://github.com/example-org/app", "url": "https://elsewhere.example.com"},
     "https://github.com/example-org/app"),
    ({"huggingface": "example-org/speech"}, "https://huggingface.co/example-org/speech"),
    ({"huggingface": "../../evil"}, None),
    ({"url": "https://example.org"}, "https://example.org/"),
    ({"url": "javascript:alert(1)"}, None),
])
def test_an_entry_links_to_its_home(tmp_path, source, link):
    assert _run(tmp_path, "return m.entryLink({source: data})", source) == link


def test_credits_and_compatibility_in_words(tmp_path):
    based_on = [{"name": "Example Clipper", "url": "https://github.com/example-org/clipper", "license": "MIT",
                 "how": "runs"},
                {"name": "Old Scorer", "url": "http://insecure.example.com", "license": "GPL-3.0-or-later",
                 "how": "port"}]
    record = {"version": "1.0.0", "commit": COMMIT, "app_version": "2.0.0", "plugin_api": 1,
              "checked_at": "2026-10-07T05:00:00Z", "checks": {}, "passed": True}
    got = _run(tmp_path, "return [m.basedOnLines(data.b), m.compatibilityText(data.r), "
                         "m.compatibilityText({...data.r, passed: false, note: 'starts: it needs Python'}), "
                         "m.compatibilityText(undefined)]", {"b": based_on, "r": record})
    assert got[0] == [{"name": "Example Clipper", "url": "https://github.com/example-org/clipper",
                       "text": "MIT · this plugin runs it as a separate program"},
                      {"name": "Old Scorer", "url": None, "text": "GPL-3.0-or-later · this plugin is a rewrite of it"}]
    assert got[1]["tone"] == "ok" and got[1]["text"].startswith("✓ Compatible: version 1.0.0 passed")
    assert "(2026-10-07)" in got[1]["text"] and "not a security review" in got[1]["text"]
    assert got[2]["tone"] == "warn" and got[2]["text"].endswith("starts: it needs Python.")
    assert got[3] is None


def test_entries_are_grouped_in_the_catalogs_order(tmp_path):
    entries = [{"id": "apps/b", "section": "gaming"}, {"id": "apps/a", "section": "video-clipping"},
               {"id": "apps/c", "section": "gone"}]
    sections = [{"id": "video-clipping", "title": "Video clipping"}, {"id": "video-ai", "title": "Video AI"},
                {"id": "gaming", "title": "Gaming"}]
    got = _run(tmp_path, "return m.groupBySection(data.e, data.s)", {"e": entries, "s": sections})
    assert [(g["title"], [e["id"] for e in g["entries"]]) for g in got] == [
        ("Video clipping", ["apps/a"]), ("Gaming", ["apps/b"]), ("Other", ["apps/c"])]
