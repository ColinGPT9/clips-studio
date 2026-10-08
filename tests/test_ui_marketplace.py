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

from plugins import catalog, permissions, sources
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
    assert got[2] == [("I understand nobody at Clips Kitty has read this pipeline’s code, "
                       "and it can do anything I can do on this PC.")]
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
    """The installed app runs Python plugins on its own Python, so a creator
    never sees a Python requirement; only the engine's own report of a missing
    one (a source checkout) shows up."""
    details = permissions.describe(_manifest(run={"command": ["{python}", "main.py"]}))
    assert details["needs_python"] and not any("Python" in line for line in details["requirements"])
    missing = [{"need": "python", "text": "It needs Python 3.10 or newer, and none was found on this PC"},
               {"need": "app", "text": "It needs Clips Kitty >=9.0, and this is 2.0.0"}]
    got = _run(tmp_path, "return [null, [], data[1]].map((p) => m.needLines(undefined, null, data[0], p))",
               [details, missing])
    assert got[0] == [] and got[1] == []
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


# What each role takes in and gives back (docs: the role table), with Quarkbloom
# Arena (a made-up game) plugins as the examples.
ROLES = {
    "example-dev/quarkbloom-finder": (["video", "transcript"], ["ranges"]),
    "example-dev/quarkbloom-captioned-finder": (["video", "transcript"], ["ranges", "context"]),
    "example-dev/quarkbloom-notes": (["moments", "transcript"], ["context"]),
    "example-dev/quarkbloom-rater": (["moments", "transcript"], ["ratings"]),
    "example-dev/quarkbloom-all-in-one": (["video", "transcript", "moments"], ["ranges", "context", "ratings"]),
}


def test_only_pipelines_that_find_are_offered_as_pipeline(tmp_path):
    """A plugin that only rates or understands moments others found is chosen
    under Rate & understand, never as the job's Pipeline: the engine refuses
    it there (store.installed_choice, step find)."""
    base = {"kind": "pipeline", "enabled": True, "problem": None, "flag": None}
    plugins = [{**base, "id": pid, "inputs": inputs, "outputs": outputs} for pid, (inputs, outputs) in ROLES.items()]
    got = _run(tmp_path, "return m.usablePipelines(data).map((p) => p.id)", plugins)
    assert got == ["example-dev/quarkbloom-finder", "example-dev/quarkbloom-captioned-finder",
                   "example-dev/quarkbloom-all-in-one"]
    assert got == [p["id"] for p in plugins if "find" in manifest.offers(p)]
    assert manifest.step_problem(plugins[3], "find").startswith("doesn't find moments")


def test_plugins_without_outputs_count_as_finders(tmp_path):
    """Built-in modes and a plugin summary from before `outputs` was sent say
    nothing about what they give back: they are finders, as every plugin was."""
    base = {"kind": "pipeline", "enabled": True, "problem": None, "flag": None}
    plugins = [{**base, "id": "a/old"}, {**base, "id": "a/finder", "outputs": ["ranges"]},
               {**base, "id": "a/rater", "inputs": ["moments"], "outputs": ["ratings"]}]
    assert _run(tmp_path, "return m.usablePipelines(data).map((p) => p.id)", plugins) == ["a/old", "a/finder"]


def test_update_lines_say_what_a_plugin_now_also_does(tmp_path):
    old = _manifest(version="1.0.0", inputs=["video", "transcript"], outputs=["ranges"],
                    permissions=["video.read", "transcript.read"])
    new = {**old, "version": "1.1.0", "inputs": ["video", "transcript", "moments"],
           "outputs": ["ranges", "context", "ratings"]}
    plan = {"plugin": {"version": "1.1.0"}, "details": permissions.describe(new, tier="listed"),
            "update": {"from": "1.0.0", "direction": "update", **permissions.changes(old, new)}}
    assert plan["update"]["added_steps"] == ["Understands moments", "Rates moments"]
    lines = _run(tmp_path, "return m.updateLines(data)", plan)
    assert [line for line in lines if line["text"].startswith("Now also")] == [
        {"text": "Now also: Understands moments", "tone": "warn"},
        {"text": "Now also: Rates moments", "tone": "warn"}]
    # an update that does what it did before says nothing new about it
    same = {**plan, "update": {"from": "1.0.0", "direction": "update", **permissions.changes(old, old)}}
    assert not any(line["text"].startswith("Now also") for line in _run(tmp_path, "return m.updateLines(data)", same))


def test_each_step_pill_says_where_it_is_chosen(tmp_path):
    """The pills come from the engine's words (permissions.step_lines); each has
    its title, and the details panel says what a rater's and an understander's
    answers change, then the engine's time limit, word for word."""
    words = [*permissions.STEP_WORDS.values(), permissions.UNDERSTANDS_ITS_OWN]
    got = _run(tmp_path, "return data.map((w) => m.stepBadge(w))", words)
    assert [(b["label"], b["tone"]) for b in got] == [(w, "info") for w in words]
    titles = dict(zip(words, (b["title"] for b in got), strict=True))
    assert titles == {
        "Finds moments": "It picks a video’s moments itself, in place of Clips Kitty’s own scoring. "
                         "Turn on Pipeline when you add a video to use it.",
        "Understands moments": "It says what happens in each moment found by Clips Kitty or a pipeline, and "
                               "Clips Kitty uses that when writing titles. Turn on Rate & understand when you "
                               "add a video.",
        "Rates moments": "It scores each moment found by Clips Kitty or a pipeline. Turn on Rate & understand "
                         "when you add a video.",
        "Suggests edits": "It suggests cuts, fades, a hook title or a layout for each clip. Turn on Suggest edits "
                          "when you add a video.",
        "Understands what it finds": "It says what happens in the moments it finds, for the titles.",
    }
    details = [permissions.describe(_manifest(inputs=inputs, outputs=outputs, permissions=["transcript.read"]))
               for inputs, outputs in ROLES.values()]
    lines = _run(tmp_path, "return data.map((d) => m.stepLines(d))", details)
    rater_line = ("Its scores decide which clips are made and their order, and which are posted when a channel "
                  "posts only the best few.")
    notes_line = "What it says about a moment goes into the request that writes your titles."
    limit = "Clips Kitty stops it after 10 minutes when it rates or understands a video’s moments."
    assert lines == [[], [notes_line], [notes_line, limit], [rater_line, limit], [rater_line, notes_line, limit]]
    assert [d["time_limit"] for d in details] == [None, None, limit, limit, limit]


def test_an_editor_shows_suggests_edits(tmp_path):
    """A plugin that suggests edits for the clips Clips Kitty makes gets its
    own pill, with its title, beside any other step it offers. It is never
    offered as the job's Pipeline."""
    editor = _manifest(inputs=["moments", "transcript"], outputs=["edits"], permissions=["transcript.read"])
    rater_editor = {**editor, "outputs": ["ratings", "edits"]}
    finder_editor = {**editor, "inputs": ["video", "moments"], "outputs": ["ranges", "edits"]}
    assert [permissions.step_lines(m) for m in (editor, rater_editor, finder_editor)] == [
        ["Suggests edits"], ["Rates moments", "Suggests edits"], ["Finds moments", "Suggests edits"]]
    # An edits output without moments in: it offers nothing it can be chosen for.
    assert permissions.step_lines({**editor, "inputs": ["video"]}) == []
    details = permissions.describe(editor)
    assert details["steps"] == ["Suggests edits"]
    got = _run(tmp_path, "return [m.stepBadge(data.steps[0]), m.stepLines(data)]", details)
    assert got[0] == {"label": "Suggests edits", "tone": "info",
                      "title": "It suggests cuts, fades, a hook title or a layout for each clip. Turn on Suggest "
                               "edits when you add a video."}
    assert got[1] == []
    base = {"kind": "pipeline", "enabled": True, "problem": None, "flag": None}
    plugins = [{**base, "id": "example-dev/quarkbloom-trimmer", "inputs": editor["inputs"], "outputs": ["edits"]}]
    assert _run(tmp_path, "return m.usablePipelines(data).map((p) => p.id)", plugins) == []


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
                         "['bundled', m.ONLINE_LIST, 'https://example.com/index.json'].map(m.indexName)]", stamps)
    assert got[0] == ["not fetched yet", "not fetched yet", "updated just now", "updated 10 minutes ago",
                      "updated 3 hours ago", "updated 3 days ago"]
    assert got[1] == ["2.5 GB", "340 MB", "2 KB", ""]
    # the engine writes a model's size the same way in its "press Download (…)" message
    from plugins import models

    sizes = [2.5e9, 2.25e9, 3e9, 340e6, 2_500_000, 52_428_800, 2048, 500, 1]
    assert _run(tmp_path, "return data.map((n) => m.formatBytes(n))", sizes) == [
        models.size_text(int(n)) for n in sizes]
    assert got[2] == ["Team wipe", "Big play"]
    assert got[3] == ["The list that came with Clips Kitty", "Clips Kitty’s online list",
                      "A list from example.com (not Clips Kitty’s)"]


def test_clips_kittys_online_list_is_named_and_described_plainly(tmp_path):
    from plugins import registry

    now = 1_000_000_000
    hours_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3 * 3600))
    minutes_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 20 * 60))
    offline = registry.WHY_OFFLINE
    states = [
        (None, True),
        ({"fetched_at": None, "error": None}, True),
        ({"fetched_at": None, "error": None}, False),  # the interface in a browser: no button to press
        ({"fetched_at": hours_ago, "in_use": True, "error": None}, True),
        ({"fetched_at": hours_ago, "in_use": True, "tried_at": minutes_ago, "error": offline}, True),
        ({"fetched_at": None, "tried_at": minutes_ago, "error": registry.WHY_NOT_FOUND}, True),
        ({"fetched_at": hours_ago, "in_use": False, "error": None}, True),  # set aside: older than this release's
        ({"fetched_at": hours_ago, "in_use": False, "tried_at": minutes_ago, "error": offline}, True),
    ]
    got = _run(tmp_path, "return [m.ONLINE_LIST, ['bundled', m.ONLINE_LIST, 'https://example.com/x.json', undefined]"
                         ".map(m.isOurList), data.map(([o, manage]) => m.onlineText(o ?? undefined, "
                         "1_000_000_000_000, manage))]", states)
    assert got[0] == registry.ONLINE_URL
    assert got[1] == [True, True, False, False]
    assert got[2] == [
        "This shows the list that came with Clips Kitty. Check for new pipelines to get the online list.",
        "This shows the list that came with Clips Kitty. Check for new pipelines to get the online list.",
        "This shows the list that came with Clips Kitty. The Clips Kitty desktop app gets the online list.",
        "Clips Kitty’s online list, updated 3 hours ago.",
        f"The check for new pipelines 20 minutes ago didn’t work. {offline} This shows its copy from 3 hours ago.",
        (f"The check for new pipelines 20 minutes ago didn’t work. {registry.WHY_NOT_FOUND} "
         "This shows the list that came with Clips Kitty."),
        "This shows the list that came with Clips Kitty, which is newer than its online copy.",
        (f"The check for new pipelines 20 minutes ago didn’t work. {offline} "
         "This shows the list that came with Clips Kitty.")]


# ---- Awesome Clips Kitty: labels, numbers and credits ---------------------------------------


def test_the_directory_kinds_and_relationships_match_the_catalog(tmp_path):
    from plugins import catalog

    got = _run(tmp_path, "return [m.DIRECTORY_KINDS, m.RELATIONSHIP_LABELS]")
    # Models are "AI model links" here, so the tab isn't taken for the app's Models page
    assert got[0] == {**{kind: catalog.KIND_TITLES[kind] for kind in catalog.DIRECTORY_KINDS.values()},
                      "model": "AI model links"}
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
    assert _run(tmp_path, "const l = m.entryLink({source: data}); return l && l.url", source) == link


def test_an_entry_has_one_button_and_its_download_page_comes_first(tmp_path):
    """Download page, website, GitHub, Hugging Face, any other page: the same
    order as the README's links (plugins/catalog.py), each with its own label."""
    gh, page = "https://github.com/example-org/app", "https://example.com/about"
    every = {"github": gh, "huggingface": "example-org/speech", "url": page, "homepage": "https://www.example.org/",
             "download": "https://www.example.org/download"}
    drop = ("download", "homepage", "github", "huggingface")
    sources_ = [{k: v for k, v in every.items() if k not in drop[:i]} for i in range(len(drop) + 1)]
    got = _run(tmp_path, "return data.map((s) => m.entryLink({source: s}))", sources_)
    assert got == [
        {"url": "https://www.example.org/download", "label": "Download from", "host": "example.org"},
        {"url": "https://www.example.org/", "label": "Website", "host": None},
        {"url": gh, "label": "Code page on GitHub", "host": None},
        {"url": "https://huggingface.co/example-org/speech", "label": "Model page on Hugging Face", "host": None},
        {"url": page, "label": "Website", "host": None},
    ]
    assert [catalog._link({"source": s}) for s in sources_] == [link["url"] for link in got]
    bad = _run(tmp_path, "return m.entryLink({source: data})", {"github": gh, "download": "javascript:alert(1)",
                                                                  "homepage": "http://example.org/"})
    assert bad == {"url": gh, "label": "Code page on GitHub", "host": None}


def test_an_entry_card_says_it_opens_a_website_and_the_dialog_says_whose_list_it_is_in():
    page = (UI / "pages" / "Marketplace.tsx").read_text(encoding="utf-8")
    card = re.search(r"function EntryCard\(.*?\n\}\n", page, re.S).group(0)
    assert "t(link.label)" in card and "link.host" in card
    assert "t('Not installed by Clips Kitty · opens a website')" in card
    assert "t('Needs technical setup (command line or Python)')" in card
    assert "openPluginLink" not in card and "<OutLink" not in card  # its links go through the catalog's dialog
    main = (UI.parent.parent / "main" / "index.ts").read_text(encoding="utf-8")
    assert "ipcMain.handle('open-catalog-link'" in main
    assert "This link is in Clips Kitty's list. It opens a website outside Clips Kitty." in main
    assert "This link comes from the pipeline's developer, not from Clips Kitty." in main
    preload = (UI.parent.parent / "preload" / "index.ts").read_text(encoding="utf-8")
    assert "ipcRenderer.invoke('open-catalog-link', url, ours)" in preload


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
                       "text": "MIT · this pipeline runs it as a separate program"},
                      {"name": "Old Scorer", "url": None, "text": "GPL-3.0-or-later · this pipeline is a rewrite of it"}]
    assert got[1]["tone"] == "ok" and got[1]["text"].startswith("✓ Compatible: version 1.0.0 passed")
    assert "(2026-10-07)" in got[1]["text"] and "not a security review" in got[1]["text"]
    assert got[2]["tone"] == "warn" and got[2]["text"].endswith("starts: it needs Python.")
    assert got[3] is None


def test_entries_are_grouped_in_the_catalogs_order(tmp_path):
    entries = [{"id": "apps/t", "section": "gaming", "setup": "technical"}, {"id": "apps/b", "section": "gaming"},
               {"id": "apps/a", "section": "video-clipping"}, {"id": "apps/c", "section": "gone"},
               {"id": "apps/i", "section": "gaming", "setup": "installer"}]
    sections = [{"id": "video-clipping", "title": "Video clipping"}, {"id": "video-ai", "title": "Video AI"},
                {"id": "gaming", "title": "Gaming"}]
    got = _run(tmp_path, "return m.groupBySection(data.e, data.s)", {"e": entries, "s": sections})
    assert [(g["title"], [e["id"] for e in g["entries"]]) for g in got] == [
        ("Video clipping", ["apps/a"]), ("Gaming", ["apps/b", "apps/i", "apps/t"]), ("Other", ["apps/c"])]


def test_version_ranges_read_as_words_the_way_the_engine_reads_them(tmp_path):
    ranges = [">=2.0", ">=2.0, <3", ">2.0", "<=2.5", "~=2.1", "~=2.1.0", "~=2", "==2.0", ">=2.0, !=2.3",
              "2.0", "", None, "two", ">=2.0, latest"]
    got = _run(tmp_path, "return data.map((r) => m.rangeText(r))", ranges)
    assert got == ["2.0 and newer", "2.0 up to, not including, 3", "newer than 2.0", "up to 2.5",
                   "2.1 up to, not including, 3", "2.1.0 up to, not including, 2.2", "2 up to, not including, 3",
                   "2.0 only", "2.0 and newer, except 2.3", "2.0 only", None, None, None, None]
    # each is a range the manifest accepts, and the words agree with version_satisfies at their edges
    edges = {"~=2.1": [("2.1.0", True), ("2.9.9", True), ("3.0.0", False), ("2.0.9", False)],
             "~=2.1.0": [("2.1.0", True), ("2.1.9", True), ("2.2.0", False)],
             "~=2": [("2.0.0", True), ("3.0.0", False)]}
    for spec, cases in edges.items():
        assert re.match(manifest.RANGE_PATTERN, spec)
        for version, ok in cases:
            assert manifest.version_satisfies(version, spec) is ok, (spec, version)


def test_where_a_listing_and_an_install_come_from_in_a_few_words(tmp_path):
    now = 1_000_000_000
    hours_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3 * 3600))
    got = _run(tmp_path, "return [['bundled', m.ONLINE_LIST, m.ONLINE_LIST, 'https://lists.example.com/index.json']"
                         ".map((u, i) => m.listedInText(u, i === 1 ? data.online : undefined, 1_000_000_000_000)), "
                         "data.sources.map((s) => m.sourceLine(s))]",
               {"online": {"fetched_at": hours_ago},
                "sources": [{"kind": "git", "url": "https://github.com/example-dev/demo", "commit": COMMIT,
                             "listed_in": "bundled"},
                            {"kind": "git", "url": "https://github.com/example-dev/demo", "commit": COMMIT,
                             "listed_in": "https://lists.example.com/index.json"},
                            {"kind": "git", "url": "https://github.com/example-dev/demo", "commit": COMMIT},
                            {"kind": "folder", "path": "C:/dev/demo"}, None]})
    assert got[0] == ["In the list that came with Clips Kitty.", "In Clips Kitty’s online list (updated 3 hours ago).",
                      "In Clips Kitty’s online list.", "In a list from lists.example.com (not Clips Kitty’s)."]
    assert got[1] == ["From Clips Kitty’s list", "From a list on lists.example.com (not Clips Kitty’s)", "From a link",
                      "From a folder on this PC", ""]


# ---- plain words for creators -----------------------------------------------------------------------

UI = LIB.parent
JARGON = re.compile(r"commit|repositor|manifest|\bforks?\b|registry|checksum|pickle|settings\.yaml|\bgit\b|exit code",
                    re.I)
SHOUTED = re.compile(r"\bPATH\b")  # the environment variable; "path" in a sentence is a plain word
# "Plugin" is the developers' word; a creator reads "pipeline". The one exception is the name of a
# real folder they may have to open, in quotes: Clips Kitty’s “plugins” folder.
PLUGIN_WORD = re.compile(r"(?<!“)\bplug-?ins?\b(?!”)", re.I)
T_CALL = re.compile(r"\bt\(\s*(['\"`])((?:(?!\1)[^\\]|\\.)*)\1")


def _jargon(text: str) -> list[str]:
    return JARGON.findall(text) + SHOUTED.findall(text) + PLUGIN_WORD.findall(text)


def test_the_words_a_creator_reads_are_plain(tmp_path):
    """Words like commit, repository or manifest belong to developers. A
    creator's screens say the same thing plainly; the exact facts are under
    Technical details (components/TechnicalDetails.tsx) or in For developers
    (pages/MarketplaceDevelopers.tsx), and neither is checked here."""
    for name in ("pages/Marketplace.tsx", "components/PipelineFields.tsx"):
        text = (UI / name).read_text(encoding="utf-8")
        strings = [m.group(2) for m in T_CALL.finditer(text)]
        assert strings, name
        assert [s for s in strings if _jargon(s)] == [], name
    # marketplace.ts has no t(): what its functions return is what the screen translates and shows
    remote = _manifest(execution="remote", network=["api.example.com"],
                       sends=[{"data": "audio", "to": "Example Cloud"}],
                       service={"name": "Example Cloud", "url": "https://example.com", "required": True})
    details = [permissions.describe(remote, tier=tier) for tier in permissions.TIERS]
    old, new = _manifest(version="1.0.0"), _manifest(version="1.1.0", execution="hybrid", **{
        k: v for k, v in remote.items() if k in ("network", "sends")}, permissions=["video.read", "network"])
    plan = {"plugin": {"version": "1.1.0"}, "details": permissions.describe(new, tier="listed"),
            "update": {"from": "1.0.0", "direction": "update", **permissions.changes(old, new)}}
    record = {"version": "1.0.0", "commit": COMMIT, "app_version": "2.0.0", "plugin_api": 1,
              "checked_at": "2026-10-07T05:00:00Z", "checks": {}, "passed": True}
    shown = _run(tmp_path, """
        const all = []
        const add = (x) => { if (x && typeof x === 'object') Object.values(x).forEach(add); else if (x) all.push(String(x)) }
        add([m.CHECK_LABELS, m.DIRECTORY_KINDS, m.KIND_LABELS, m.RELATIONSHIP_LABELS, m.CATEGORY_LABELS])
        for (const d of data.details) {
          add(m.confirmations({details: d})); add(m.tierBadge(d))
          add(m.executionBadge(d.execution, d.execution_text).label)
        }
        add(m.executionBadge(null).label)
        add(m.updateLines(data.plan))
        add(m.catalogBadges(['official', 'community', 'compatible', 'featured']))
        add([m.compatibilityText(data.record), m.compatibilityText({...data.record, passed: false})])
        add(m.hardwareFit({gpu: 'required', vram_gb: 8, ram_gb: 16, disk_gb: 10, os: ['windows'], software: ['x']},
                          {gpu: null, disk_free_bytes: 1e9, platform: 'win32'}))
        add([m.fitSummary([{text: '', fit: 'no'}]), m.fitSummary([{text: '', fit: 'unknown'}])])
        add([undefined, {fetched_at: null, error: 'x'}, {fetched_at: '2026-10-07T00:00:00Z'}]
          .map((o) => m.onlineText(o, Date.now(), true)))
        add(['bundled', m.ONLINE_LIST, 'https://lists.example.com/i.json'].map((u) => m.listedInText(u, undefined, 0)))
        add([{listed_in: 'bundled'}, {listed_in: 'https://lists.example.com/i.json'}, {kind: 'git'}, {kind: 'folder'}]
          .map(m.sourceLine))
        add(['>=2.0, <3', '~=2.1', '==2.0, !=2.1'].map(m.rangeText))
        add(m.basedOnLines([{name: 'A', url: '', license: 'MIT', how: 'runs'}, {name: 'B', url: '', license: 'MIT',
                                                                                  how: 'includes-code'}]))
        add(m.listingLinks({links: {docs: 'https://example.com/d', funding: ['https://example.com/f']},
                            author: {url: 'https://example.com/a'}}).map((l) => l.label))
        add(m.settingValue({type: 'secret'}, 'x'))
        add([{download: 'https://example.org/d'}, {homepage: 'https://example.org/'}, {github: 'https://github.com/a/b'},
             {huggingface: 'a/b'}].map((s) => m.entryLink({source: s}).label))
        return all
    """, {"details": details, "plan": plan, "record": record})
    assert len(shown) > 50
    assert [s for s in shown if _jargon(s)] == []


def test_creators_get_two_tabs_and_developers_a_section_of_their_own():
    """Browse and Installed are the tabs. Installing from a folder or a link,
    which Clips Kitty hasn't checked, is For developers, opened from a quiet
    link at the bottom of Browse, and nothing on a creator's screen sends
    them there."""
    page = (UI / "pages" / "Marketplace.tsx").read_text(encoding="utf-8")
    tabs = re.search(r"const TABS\b.*?= \[(.*?)\]", page, re.S)
    assert tabs and re.findall(r"id: '(\w+)'", tabs.group(1)) == ["browse", "installed"]
    assert "t('For developers: install a pipeline you’re writing')" in page
    assert "<MarketplaceDevelopers " in page and "function AddFromLink(" not in page
    assert "MarketplaceTab = 'browse' | 'installed' | 'developers'" in (LIB / "plugins.ts").read_text(encoding="utf-8")
    developers = (UI / "pages" / "MarketplaceDevelopers.tsx").read_text(encoding="utf-8")
    assert "t('For developers')" in developers
    assert "t('Clips Kitty hasn’t checked pipelines installed here. To find pipelines, use Browse.')" in developers
    for name in ("pages/Marketplace.tsx", "components/PipelineFields.tsx"):
        strings = [m.group(2) for m in T_CALL.finditer((UI / name).read_text(encoding="utf-8"))]
        assert [s for s in strings if re.search(r"folder or a (GitHub )?link|Add from", s)] == [], name


def test_the_screen_and_the_engine_use_the_same_words_for_what_a_pipeline_may_do():
    """The explanation under "What it may do" is the engine's (plugins/permissions.py),
    word for word, and names the two labels it puts after each permission."""
    text = (UI / "pages" / "Marketplace.tsx").read_text(encoding="utf-8")
    note = permissions.ENFORCEMENT_NOTE
    assert f"'{note}'" in text
    for label in (permissions.ENFORCED, permissions.DECLARED):
        assert f"“{label[0].upper()}{label[1:]}”:" in note


def test_the_consent_and_safety_lines_are_in_every_language(tmp_path):
    """What a creator ticks before Install, and the engine's words for what a
    pipeline may do, are translated in every locale file. A line whose English
    changes falls back to English on screen, and fails here until it is
    translated again."""
    remote = _manifest(execution="remote", network=["api.example.com"],
                       sends=[{"data": "audio", "to": "Example Cloud"}],
                       service={"name": "Example Cloud", "url": "https://example.com", "required": True})
    plans = [{"details": permissions.describe(remote, tier=tier)} for tier in ("link", "listed")]
    ticks = _run(tmp_path, "return data.flatMap((p) => m.confirmations(p))", plans)
    model_tick = "I trust this model. I understand its files can run programs on this PC when the pipeline opens them."
    assert f"'{model_tick}'" in (UI / "pages" / "Marketplace.tsx").read_text(encoding="utf-8")
    lines = {*ticks, model_tick, permissions.NOTICE, permissions.ENFORCED, permissions.DECLARED,
             permissions.ENFORCEMENT_NOTE}
    assert len(lines) == 9
    locales = sorted((UI / "locales").glob("*.json"))
    assert len(locales) >= 18
    for path in locales:
        have = json.loads(path.read_text(encoding="utf-8"))
        assert sorted(lines - set(have)) == [], path.name
        assert all(have[line].strip() for line in lines), path.name


def test_the_engines_words_a_creator_reads_say_pipeline():
    """Text the engine sends that the screen shows (through t(), so it is a key
    in the locale files) says "pipeline", not "plugin": counter.EXPLAIN sits
    next to the counting switch, the models texts under Installed. Any such
    constant in plugins/ that a locale file translates is checked too."""
    import importlib
    import pkgutil

    from plugins import counter, models
    assert PLUGIN_WORD.search("a public count for that plugin") and not PLUGIN_WORD.search("Clips Kitty’s “plugins” folder")
    keys = set()
    for path in (UI / "locales").glob("*.json"):
        keys |= set(json.loads(path.read_text(encoding="utf-8")))
    shown = {counter.EXPLAIN, models.GATED, models.OLLAMA_MODELS, permissions.NOTICE, permissions.ENFORCED,
             permissions.DECLARED, permissions.ENFORCEMENT_NOTE}
    for info in pkgutil.iter_modules([str(Path(counter.__file__).parent)]):
        try:
            module = importlib.import_module(f"plugins.{info.name}")
        except ImportError:  # an optional dependency this test run doesn't have
            continue
        shown |= {v for k, v in vars(module).items() if k.isupper() and isinstance(v, str) and v in keys}
    assert counter.EXPLAIN in keys
    assert [s for s in shown if PLUGIN_WORD.search(s)] == []


def test_the_marketplace_says_only_what_is_true_and_says_it_in_every_language():
    """The counting switch, a pipeline installed switched off, its models,
    the Community label and where a listing's code is: each line claims only
    what Clips Kitty knows, and is translated in every locale file."""
    from plugins import counter
    page = (UI / "pages" / "Marketplace.tsx").read_text(encoding="utf-8")
    strings = {m.group(2) for m in T_CALL.finditer(page)}
    # plugins.count_installs is in this Windows account's own settings.yaml (core/paths.py), not the PC's
    switched_off = "Switched off in your Clips Kitty settings file."
    # a gated model is one Clips Kitty can't download (plugins/models.py GATED)
    models_line = ("Some of its AI models may not be on this PC yet. Installed shows which ones, and whether "
                   "Clips Kitty can download them.")
    # an update or reinstall keeps the creator's Off switch, and the Generate bar offers only pipelines that are on
    installed_off = "It is switched off, so the Generate bar doesn’t offer it. Switch it on under Installed."
    # another list's checks are what that list reports; Clips Kitty only knows nobody there read the code
    community = next(s for s in strings if s.startswith("Community: "))
    assert "Its page says which automatic checks its list reports. Nobody at Clips Kitty has read its code." \
        in community
    # the address of the code is what the list build checked; the author's name is the developer's own words
    code, name = "Its code is on GitHub at", "Its developer gives their name as"
    # Clips Kitty can only know nobody *at Clips Kitty* read the code, and an Official pipeline's code is its own
    automatic = "These checks are automatic. Nobody at Clips Kitty has read this pipeline’s code."
    # the footer sends people to the listing page for its checks, so a list that reports none says so there
    no_checks = ("This list doesn’t report any automatic checks that Clips Kitty knows about. "
                 "Nobody at Clips Kitty has read this pipeline’s code.")
    lines = {switched_off, models_line, installed_off, community, code, name, automatic, no_checks}
    assert lines <= strings
    assert not [s for s in strings if re.search(r"everyone on this PC|how to get them|checked its listing", s)]
    assert not [s for s in strings if "Nobody has read" in s or "nobody has read" in s]

    dialog = re.search(r"function InstallDialog\(.*?\n\}\n", page, re.S).group(0)
    assert "const switchedOff = done?.enabled === false" in dialog  # a missing field reads as on, like the engine
    assert "missing.length === 0 && !switchedOff && (" in dialog  # "Ready" only when it is on
    assert re.search(r"\{switchedOff && \(?\s*<p[^>]*>\{t\('" + re.escape(installed_off) + r"'\)\}</p>", dialog)
    where = re.search(r"\{t\('Where it comes from'\)\}(.*?)<TechnicalDetails(.*?)/>", page, re.S)
    assert "t('Made by')" not in page and "listing.author?.name ?? listing.publisher}." not in where.group(1)
    assert where.group(1).index(f"t('{code}')") < where.group(1).index(f"t('{name}')")
    assert "['Repository', listing.repository]" in where.group(2)  # the full address stays in Technical details
    # "owner/name" is read with the engine's own pattern for a GitHub address
    assert sources.GITHUB_RE.pattern.replace("/", "\\/") in page
    # the developer's own name appears only as theirs: not in the header under the pipeline's name, nowhere else
    listing_page = re.search(r"function ListingPage\(.*?\n\}\n", page, re.S).group(0)
    header = re.search(r"<h3[^>]*>\{listing\.name\}</h3>(.*?)</p>", listing_page, re.S).group(1)
    assert "author" not in re.sub(r"\{/\*.*?\*/\}", "", header, flags=re.S) and "{listing.id}" in header
    assert page.count("listing.author") == listing_page.count("listing.author") == 2  # the guard and the one line
    # the "nobody at Clips Kitty has read it" lines are left out for an Official listing (code that is ours)
    assert "const official = listing.details?.tier === 'listed-official'" in listing_page
    assert re.search(r"\{!official && \(\s*<li>\{t\('" + re.escape(automatic) + r"'\)\}</li>", listing_page)
    assert re.search(r"\{checks\.length === 0 && !official && \(\s*<p[^>]*>\s*\{t\(\s*'" + re.escape(no_checks),
                     listing_page)

    for path in sorted((UI / "locales").glob("*.json")):
        have = json.loads(path.read_text(encoding="utf-8"))
        assert sorted((lines | {counter.EXPLAIN}) - set(have)) == [], path.name
        assert all(have[line].strip() for line in lines | {counter.EXPLAIN}), path.name
        assert not [k for k in have if "everyone on this PC" in k or "for that plugin" in k], path.name
        assert not [k for k in have if "Nobody has read this pipeline" in k], path.name
