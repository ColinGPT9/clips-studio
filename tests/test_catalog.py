"""Awesome Clips Kitty: directory entries, sections, labels, numbers, the
README, the install counter's settings, the metrics job and the
compatibility check.

Catalogs here are made in a temporary folder. Every project, id and address
is a placeholder; nothing touches the network.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from plugins import catalog, registry  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TODAY = "2026-10-07"

SECTIONS = {
    "app": {"sections": [{"id": "built-with", "title": "Built with Clips Kitty", "relationship": "built-with"},
                         {"id": "works-with", "title": "Work with Clips Kitty", "relationship": "related"},
                         {"id": "video-clipping", "title": "Video clipping",
                          "description": "Apps that cut long videos into short ones."},
                         {"id": "gaming", "title": "Gaming"}],
            "wanted": []},
    "pipeline": {"sections": [{"id": "general", "title": "General"}, {"id": "gaming", "title": "Gaming"},
                              {"id": "gaming/example-game", "title": "Example Game"}],
                 "wanted": [{"section": "gaming/example-game", "idea": "Wins and close calls."}]},
    "model": {"sections": [{"id": "speech", "title": "Speech"}], "wanted": []},
    "tool": {"sections": [{"id": "developer", "title": "For developers"}], "wanted": []},
}

APP = {"name": "Example Clipper", "description": "Turns long videos into vertical clips.",
       "section": "video-clipping", "relationship": "related", "license": "MIT",
       "source": {"github": "https://github.com/example-org/example-clipper"}, "runs": "local",
       "platforms": ["windows", "linux"], "tags": ["subtitles"], "added": TODAY, "checked": TODAY}


class Catalog:
    def __init__(self, base: Path):
        self.root = base / "catalog"
        self.dir = self.root / "registry"
        self.dir.mkdir(parents=True)
        self.write("sections.yaml", SECTIONS)
        (self.dir / "blocklist.yaml").write_text("[]\n")

    def write(self, rel: str, data) -> Path:
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return path

    def stats(self, name: str, data: dict) -> None:
        path = self.root / "stats" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def build(self):
        def no_fetch(url):
            raise AssertionError(f"no listing here should be fetched: {url}")

        return registry.build_index(self.root, fetch=no_fetch)


@pytest.fixture
def cat(tmp_path):
    return Catalog(tmp_path)


# ---- entries ----------------------------------------------------------------------------------


def test_an_entry_goes_into_the_index_with_its_labels_and_numbers(cat):
    cat.write("apps/example-clipper.yaml", APP)
    cat.write("models/example-speech.yaml", {
        "name": "Example Speech", "description": "Speech to text.", "section": "speech", "relationship": "related",
        "license": "Apache-2.0", "source": {"huggingface": "example-org/example-speech"}, "added": TODAY})
    cat.stats("metrics.json", {
        "generated_at": "2026-10-07T05:00:00Z",
        "github": {"example-org/example-clipper": {"stars": 1234, "pushed_at": "2026-09-30", "archived": False,
                                                   "has_discussions": True, "discussions": 12}},
        "huggingface": {"example-org/example-speech": {"downloads": 52000, "likes": 40}}})
    index, problems = cat.build()
    assert problems == []
    by_id = {e["id"]: e for e in index["catalog"]}
    app = by_id["apps/example-clipper"]
    assert app["kind"] == "app" and app["badges"] == ["community"] and app["relationship"] == "related"
    assert app["metrics"] == {"github": {"stars": 1234, "pushed_at": "2026-09-30", "archived": False,
                                         "has_discussions": True, "discussions": 12}}
    assert app["discussions_url"] == "https://github.com/example-org/example-clipper/discussions"
    model = by_id["models/example-speech"]
    assert model["metrics"] == {"models": {"example-org/example-speech": {"downloads": 52000, "likes": 40}}}
    assert "checked" not in model
    assert index["metrics_at"] == "2026-10-07"
    assert [s["id"] for s in index["sections"]["app"]["sections"]] == ["built-with", "works-with",
                                                                      "video-clipping", "gaming"]


@pytest.mark.parametrize("change, fragment", [
    ({"license": None}, "license: the project's licence"),
    ({"license": "NOASSERTION"}, "license: the project's licence"),
    ({"license": "Proprietary licence"}, "license: the project's licence"),
    ({"relationship": "built-for"}, "built-for is for installable plugins"),
    ({"relationship": "built-with"}, "uses: what it uses of Clips Kitty"),
    ({"uses": "api"}, "uses: only for built-with projects"),
    ({"section": "nowhere"}, "section: one of built-with, works-with, video-clipping, gaming"),
    ({"section": "built-with"}, "relationship: the section built-with is for built-with projects"),
    ({"section": "works-with"}, "adapter: an app in works-with names the listed pipeline"),
    ({"source": {}}, "source: github, huggingface or url"),
    ({"source": {"github": "https://gitlab.com/example-org/x"}}, "source.github: https://github.com/<owner>/<repo>"),
    ({"source": {"url": "http://example.com"}}, "source.url: an https address"),
    ({"platforms": ["amiga"]}, "platforms: amiga not one of"),
    ({"stars": 5}, "stars: unknown field"),
    ({"added": None}, "added: the date it was added"),
    ({"featured": {"reason": "Great"}}, "featured.date: YYYY-MM-DD"),
])
def test_a_bad_entry_is_left_out_and_says_why(cat, change, fragment):
    entry = {**APP, **change}
    entry = {k: v for k, v in entry.items() if v is not None}
    cat.write("apps/example-clipper.yaml", entry)
    cat.write("apps/good-one.yaml", {**APP, "name": "Good One"})
    index, problems = cat.build()
    assert any(fragment in p for p in problems), problems
    assert [e["id"] for e in index["catalog"]] == ["apps/good-one"]


def test_file_names_and_models_are_checked(cat):
    cat.write("apps/Bad_Name.yaml", APP)
    cat.write("apps/nested/deeper.yaml", APP)
    cat.write("models/no-home.yaml", {"name": "No home", "description": "A model.", "section": "speech",
                                      "relationship": "related", "license": "MIT", "added": TODAY,
                                      "source": {"github": "https://github.com/example-org/model"}})
    cat.write("apps/with-models.yaml", {**APP, "models": [{"huggingface": "not a model id"}]})
    problems = cat.build()[1]
    for fragment in ("apps/Bad_Name.yaml: the file name must be", "apps/nested/deeper.yaml: an entry is apps/<name>.yaml",
                     "models/no-home.yaml: source: a model's home is its Hugging Face repository",
                     "apps/with-models.yaml: models: up to 10 of {huggingface: owner/name}"):
        assert any(fragment in p for p in problems), (fragment, problems)


def test_an_adapter_must_be_a_listing_in_the_same_catalog(cat):
    cat.write("apps/example-clipper.yaml", {**APP, "section": "works-with", "adapter": "example-dev/clipper-adapter"})
    index, problems = cat.build()
    assert problems == ["apps/example-clipper: adapter: example-dev/clipper-adapter is not a listing here"]
    assert index["catalog"] == []


def test_official_featured_and_stale(cat):
    cat.write("tools/clips-kitty-sdk.yaml", {
        "name": "Clips Kitty SDK", "description": "Write plugins.", "section": "developer",
        "relationship": "built-with", "uses": "api", "license": "MIT", "added": TODAY,
        "source": {"github": "https://github.com/ColinGPT9/clips-studio", "path": "sdk/python"}})
    cat.write("apps/old-clipper.yaml", {**APP, "name": "Old Clipper",
                                        "source": {"github": "https://github.com/example-org/old"},
                                        "featured": {"reason": "The first of its kind", "date": TODAY}})
    cat.write("apps/archived-clipper.yaml", {**APP, "name": "Archived Clipper",
                                             "source": {"github": "https://github.com/example-org/archived"}})
    cat.stats("metrics.json", {"generated_at": "2026-10-07T05:00:00Z", "github": {
        "example-org/old": {"stars": 99, "pushed_at": "2024-01-02", "archived": False},
        "example-org/archived": {"stars": 5, "pushed_at": "2026-01-01", "archived": True}}})
    index, problems = cat.build()
    assert problems == []
    by_id = {e["id"]: e for e in index["catalog"]}
    assert by_id["tools/clips-kitty-sdk"]["badges"] == ["official"]
    assert by_id["apps/old-clipper"]["badges"] == ["community", "featured"]
    assert by_id["apps/old-clipper"]["featured"] == {"reason": "The first of its kind", "date": TODAY}
    assert by_id["apps/old-clipper"]["metrics"]["stale"] == "no commits since 2024-01-02"
    assert by_id["apps/archived-clipper"]["metrics"]["stale"] == "archived"


def test_sections_are_checked(cat):
    cat.write("sections.yaml", {"app": {"sections": [{"id": "a/b", "title": "Child first"},
                                                     {"id": "Bad Id", "title": "x"}],
                                        "wanted": [{"section": "nowhere", "idea": "x"}]},
                                "gizmo": {"sections": []}})
    problems = cat.build()[1]
    for fragment in ("its parent a must come first", "a lowercase name like gaming", "nowhere is not a section of app",
                     "gizmo: not a kind"):
        assert any(fragment in p for p in problems), (fragment, problems)


# ---- the README --------------------------------------------------------------------------------


def test_the_readme_is_generated_between_the_markers_and_the_rest_is_kept(cat):
    cat.write("apps/example-clipper.yaml", APP)
    cat.write("apps/unchecked-clipper.yaml", {k: v for k, v in {**APP, "name": "Unchecked Clipper"}.items()
                                              if k != "checked"})
    cat.write("apps/risky-clipper.yaml", {**APP, "name": "Risky Clipper", "section": "gaming",
                                          "warning": "Downloads from sites whose terms may not allow it.",
                                          "license_note": "The cloud/ folder has its own licence."})
    cat.stats("metrics.json", {"generated_at": "2026-10-07", "github": {
        "example-org/example-clipper": {"stars": 1234, "pushed_at": "2026-10-01"}}})
    index, problems = cat.build()
    assert problems == []
    body = catalog.readme_body(index["sections"], index["catalog"])
    assert body.startswith("## Contents\n\n- [Apps](#apps)\n  - [Video clipping](#video-clipping)\n")
    assert "### Video clipping\n\n_Apps that cut long videos into short ones._\n\n" in body
    assert ("- [Example Clipper](https://github.com/example-org/example-clipper) - Turns long videos into "
            "vertical clips. `MIT` · runs locally · ★ 1.2k on GitHub") in body
    assert "### Not yet checked (apps)" in body and "[Unchecked Clipper]" in body.split("### Not yet checked")[1]
    assert "  ⚠ Downloads from sites whose terms may not allow it." in body
    assert "Licence note: The cloud/ folder has its own licence." in body
    assert "### Work with Clips Kitty" not in body  # empty sections are left out
    assert "- **Pipelines › Example Game**: Wins and close calls." in body
    readme = f"# Title\n\nIntro.\n\n{catalog.GENERATED_START}\nstale text\n{catalog.GENERATED_END}\n\n## Licence\n"
    out = catalog.write_readme(readme, body)
    assert out.startswith("# Title\n\nIntro.\n\n") and out.endswith(f"{catalog.GENERATED_END}\n\n## Licence\n")
    assert "stale text" not in out and catalog.write_readme(out, body) == out
    with pytest.raises(catalog.CatalogError, match="markers"):
        catalog.write_readme("# No markers\n", body)


def test_the_script_writes_and_checks_the_readme(cat, capsys):
    from scripts.build_registry_index import main

    readme = cat.root / "README.md"
    readme.write_text(f"# Catalog\n\n{catalog.GENERATED_START}\n{catalog.GENERATED_END}\n")
    cat.write("apps/example-clipper.yaml", APP)
    args = ["--catalog", str(cat.root)]
    assert main([*args, "--check"]) == 1
    assert main(args) == 0 and "[Example Clipper]" in readme.read_text()
    assert main([*args, "--check"]) == 0
    readme.write_text(readme.read_text().replace("Example Clipper", "Edited by hand"))
    assert main([*args, "--check"]) == 1
    assert "README.md is out of date" in capsys.readouterr().out


# ---- the install counter's address -----------------------------------------------------------


@pytest.mark.parametrize("value, ok", [
    ("https://github.com/ColinGPT9/awesome-clips-kitty/releases/download/installs/{asset}", True),
    (None, True),
    ("https://github.com/example-org/awesome/releases/download/installs/{asset}", False),  # not the project's
    ("https://tracker.example.net/c/{asset}", False),
    ("https://github.com/ColinGPT9/awesome-clips-kitty/releases/download/installs/{asset}?who=me", False),
    ("http://github.com/ColinGPT9/awesome-clips-kitty/releases/download/installs/{asset}", False),
    ("https://github.com/ColinGPT9/awesome-clips-kitty/releases/download/installs/no-placeholder", False),
])
def test_the_counter_address_must_be_a_release_in_the_projects_own_repository(cat, value, ok):
    cat.write("catalog.yaml", {"counter": {"install": value}})
    index, problems = cat.build()
    assert (problems == []) is ok, problems
    assert index.get("counter") == ({"install": value} if ok and value else None)


def test_the_counter_name_holds_no_slash():
    assert catalog.counter_asset("example-dev/example-plugin") == "example-dev__example-plugin.count"


OWN_COUNTER = "https://github.com/ColinGPT9/awesome-clips-kitty/releases/download/installs/{asset}"


def _remote_index():
    entry = {"id": "apps/x", "kind": "app", "name": "X", "license": "MIT", "featured": {"reason": "Ours", "date": TODAY},
             "source": {"github": "https://github.com/example-org/x"}, "badges": ["official", "compatible", "featured"]}
    own = {**entry, "id": "tools/sdk", "kind": "tool", "source": {"github": "https://github.com/ColinGPT9/clips-studio"},
           "badges": ["community"]}
    del own["featured"]
    return {"format": 1, "plugins": [], "blocklist": [], "catalog": [entry, own], "counter": {"install": OWN_COUNTER}}


def test_only_the_bundled_index_gives_labels_or_counts_installs():
    # Any other index is someone else's list: everything in it is Community, and it can't count.
    data = registry.check_index(_remote_index())
    assert [e["badges"] for e in data["catalog"]] == [["community"], ["community"]]
    assert "featured" not in data["catalog"][0] and "counter" not in data
    # The bundled index was built by this project: its Featured counts, Official follows the
    # repository whatever it claims, and a directory entry is never Compatible.
    data = registry.check_index(_remote_index(), ours=True)
    assert [e["badges"] for e in data["catalog"]] == [["community", "featured"], ["official"]]
    assert data["counter"] == {"install": OWN_COUNTER}
    assert "counter" not in registry.check_index({**_remote_index(), "counter": {"install": "javascript:alert(1)"}},
                                                 ours=True)


def test_an_entry_from_an_index_keeps_only_well_formed_fields():
    bad = {"id": "apps/odd", "kind": "app", "name": "Odd", "license": "MIT", "source": {"github": "https://github.com/a/b"},
           "adapter": ["not", "an", "id"], "games": "Valorant", "platforms": [1, "windows"], "unknown": "x",
           "discussions_url": "javascript:alert(1)",
           "metrics": {"github": {"stars": "lots", "pushed_at": 5}, "models": {"a/b": None, "c/d": {"downloads": 3}},
                       "installs": -1}}
    (entry,) = registry.check_index({"format": 1, "plugins": [], "catalog": [bad]})["catalog"]
    assert "adapter" not in entry and "games" not in entry and "unknown" not in entry
    assert "discussions_url" not in entry and entry["platforms"] == ["windows"]
    assert entry["metrics"] == {"github": {}, "models": {"c/d": {"downloads": 3}}}
    # A listing too: odd checks or word lists are dropped, not a reason to fail.
    listing = {"id": "example-dev/odd", "repository": "https://github.com/ColinGPT9/clips-studio", "checks": ["x"],
               "games": 5, "tags": ["ok"], "versions": [{"version": "1.0.0", "commit": "a" * 40}]}
    (got,) = registry.check_index({"format": 1, "plugins": [listing]}, ours=True)["plugins"]
    assert got["checks"] == {} and "games" not in got and got["tags"] == ["ok"]


# ---- the metrics job --------------------------------------------------------------------------


def test_the_metrics_job_reads_each_number_from_its_own_source_and_keeps_the_last_on_failure():
    from scripts import update_registry_metrics as job

    index = {"plugins": [{"id": "example-dev/plugin", "repository": "https://github.com/example-dev/plugin",
                          "models": [{"source": "huggingface", "id": "example-org/detector"}]}],
             "catalog": [{"source": {"github": "https://github.com/example-org/app"},
                          "models": [{"huggingface": "example-org/speech"}]},
                         {"source": {"huggingface": "example-org/speech"}},
                         {"source": {"github": "https://github.com/example-org/gone"}}],
             "counter": {"install": "https://github.com/example-org/awesome/releases/download/installs/{asset}"}}
    calls = []

    def fetch(url, *, token=None, body=None):
        calls.append((url, token, body is not None))
        if url == "https://api.github.com/repos/example-dev/plugin":
            return {"stargazers_count": 7, "pushed_at": "2026-10-01T00:00:00Z", "archived": False,
                    "has_discussions": True}
        if url == "https://api.github.com/repos/example-org/app":
            return {"stargazers_count": 2048, "pushed_at": "2026-09-01T00:00:00Z", "archived": False,
                    "has_discussions": False}
        if url == "https://api.github.com/graphql":
            return {"data": {"repository": {"discussions": {"totalCount": 3}}}}
        if url.startswith("https://huggingface.co/api/models/"):
            return {"downloads": 1500, "likes": 12, "lastModified": "2026-08-01T10:00:00.000Z"}
        if url == "https://api.github.com/repos/example-org/awesome/releases/tags/installs":
            return {"assets": [{"name": "example-dev__plugin.count", "download_count": 41},
                               {"name": "someone__unlisted.count", "download_count": 9},
                               {"name": "README.md", "download_count": 100}]}
        raise OSError("HTTP Error 404")

    previous = {"github": {"example-org/gone": {"stars": 5, "pushed_at": "2025-01-01"}}, "installs": {}}
    out = job.update(index, previous, fetch=fetch, token="t", now="2026-10-07T05:00:00Z", log=lambda m: None)
    assert out == {
        "generated_at": "2026-10-07T05:00:00Z",
        "github": {"example-dev/plugin": {"stars": 7, "pushed_at": "2026-10-01", "archived": False,
                                          "has_discussions": True, "discussions": 3},
                   "example-org/app": {"stars": 2048, "pushed_at": "2026-09-01", "archived": False,
                                       "has_discussions": False},
                   "example-org/gone": {"stars": 5, "pushed_at": "2025-01-01"}},  # kept
        "huggingface": {"example-org/detector": {"downloads": 1500, "likes": 12, "last_modified": "2026-08-01"},
                        "example-org/speech": {"downloads": 1500, "likes": 12, "last_modified": "2026-08-01"}},
        "installs": {"example-dev/plugin": 41},  # only listings in this catalog
    }
    assert ("https://api.github.com/graphql", "t", True) in calls
    # Without a token, no GraphQL call (and no discussion count).
    calls.clear()
    out = job.update(index, {}, fetch=fetch, token=None, now="x", log=lambda m: None)
    assert "discussions" not in out["github"]["example-dev/plugin"]
    assert not any(u.endswith("/graphql") for u, _, _ in calls)


def test_the_metrics_job_counts_nothing_without_a_release_counter():
    from scripts import update_registry_metrics as job

    assert job.install_counts(None, fetch=None, token=None) is None
    assert job.install_counts("https://counter.example.com/{asset}", fetch=None, token=None) is None


# ---- the compatibility check -------------------------------------------------------------------


needs_tools = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe") and shutil.which("git")),
                                 reason="needs ffmpeg, ffprobe and git")


def _listed_repo(plugin_source, monkeypatch, folder: Path) -> tuple[dict, dict]:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    repo = plugin_source.repo()
    shutil.copytree(folder, repo, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    plugin_source.git(repo, "add", "-A")
    plugin_source.git(repo, "commit", "-q", "-m", "plugin")
    commit = plugin_source.git(repo, "rev-parse", "HEAD")
    data = yaml.safe_load((folder / "clipskitty.yaml").read_text(encoding="utf-8"))
    listing = {"id": data["id"], "repository": repo.resolve().as_uri(), "path": "."}
    return listing, {"version": data["version"], "commit": commit}


@needs_tools
def test_the_compatibility_check_passes_the_example_and_records_why(tmp_path, plugin_source, monkeypatch):
    from scripts.check_compatibility import CHECKS, check

    listing, version = _listed_repo(plugin_source, monkeypatch, ROOT / "examples" / "pipelines" / "scene-cut-highlights")
    record = check(listing, version, app_version="2.0.0", work=tmp_path / "work", git=shutil.which("git"),
                   now="2026-10-07T05:00:00Z", log=lambda m: None)
    assert record["passed"] is True and record["moments"] == 1, record
    assert record["checks"] == dict.fromkeys(CHECKS, True)
    assert record["plugin_api"] == 1 and record["app_version"] == "2.0.0" and record["commit"] == version["commit"]


@needs_tools
@pytest.mark.parametrize("change, failed", [
    ({"src/main.py": "import sys\nsys.exit(3)\n"}, "starts"),
    ({"src/main.py": "import json, os\nopen(os.path.join(os.environ['CLIPSKITTY_JOB'], 'result.json'), 'w')"
                     ".write(json.dumps({'ranges': [{'start': 30, 'end': 10}]}))\n"}, "valid_result"),
    ({"manifest": {"requires": {"clips_kitty": ">=9.0", "plugin_api": 1}}}, "installs"),
    ({"manifest": {"requirements": {"gpu": "required"}}}, "requirements_met"),
    ({"manifest": {"license": None}}, "manifest_valid"),
])
def test_the_compatibility_check_stops_at_the_first_failure(tmp_path, plugin_source, monkeypatch, change, failed):
    from scripts.check_compatibility import CHECKS, check

    manifest = plugin_source.manifest(**{k: v for k, v in (change.get("manifest") or {}).items() if v is not None})
    for k, v in (change.get("manifest") or {}).items():
        if v is None:
            manifest.pop(k)
    files = {k: v for k, v in change.items() if k != "manifest"}
    folder = plugin_source.folder(manifest=manifest, files=files)
    listing, version = _listed_repo(plugin_source, monkeypatch, folder)
    record = check(listing, version, app_version="2.0.0", work=tmp_path / "work", git=shutil.which("git"),
                   timeout=60, log=lambda m: None)
    assert record["passed"] is False and record["checks"][failed] is False, record
    before = CHECKS[: CHECKS.index(failed)]
    assert all(record["checks"][c] is True for c in before) and record["note"].startswith(failed)
    assert not any(c in record["checks"] for c in CHECKS[CHECKS.index(failed) + 1:])


def test_a_passed_record_makes_a_version_compatible_and_only_that_version(cat, tmp_path):
    from scripts.build_registry_index import fixture_reader

    raw = tmp_path / "raw"
    commits = {"1.0.0": "1" * 40, "1.1.0": "2" * 40}
    for v, c in commits.items():
        folder = raw / "example-dev" / "plugin" / c
        folder.mkdir(parents=True)
        (folder / "clipskitty.yaml").write_text(yaml.safe_dump({
            "manifest_version": 1, "id": "example-dev/plugin", "name": "Plugin", "version": v, "kind": "pipeline",
            "capability": "highlight_detection", "description": "x", "license": "MIT",
            "repository": "https://github.com/example-dev/plugin", "requires": {"clips_kitty": ">=2.0", "plugin_api": 1},
            "run": {"command": ["{python}", "src/main.py"]}, "execution": "local", "inputs": ["video"],
            "outputs": ["ranges"], "permissions": ["video.read"], "category": "utilities"}))
    cat.write("pipelines/example-dev/plugin.yaml", {
        "id": "example-dev/plugin", "repository": "https://github.com/example-dev/plugin", "section": "general",
        "added": TODAY, "versions": [{"version": v, "commit": c} for v, c in commits.items()]})
    passed = {"version": "1.0.0", "commit": commits["1.0.0"], "app_version": "2.0.0", "plugin_api": 1,
              "checked_at": "2026-10-07T05:00:00Z", "checks": {}, "passed": True}
    cat.stats("compatibility.json", {"example-dev/plugin": [passed]})
    index, problems = registry.build_index(cat.root, fetch=fixture_reader(raw))
    assert problems == []
    (p,) = index["plugins"]
    assert p["latest"] == "1.1.0" and p["badges"] == ["community"]  # the latest wasn't checked
    assert p["versions"][1]["compatibility"]["passed"] is True and "compatibility" not in p["versions"][0]
    cat.stats("compatibility.json", {"example-dev/plugin": [passed, {**passed, "version": "1.1.0",
                                                                     "commit": commits["1.1.0"]}]})
    assert registry.build_index(cat.root, fetch=fixture_reader(raw))[0]["plugins"][0]["badges"] == [
        "community", "compatible"]
    # A record for another commit of the same version doesn't count.
    cat.stats("compatibility.json", {"example-dev/plugin": [{**passed, "version": "1.1.0", "commit": "3" * 40}]})
    assert registry.build_index(cat.root, fetch=fixture_reader(raw))[0]["plugins"][0]["badges"] == ["community"]


def test_installs_arent_counted_while_the_privacy_policy_says_no_telemetry():
    """The website and the Store answers promise no telemetry. Setting the
    counter's address turns counting on for everyone who installs from the
    catalog, so they must say so first."""
    settings, problems = catalog.read_settings(ROOT / "awesome-clips-kitty")
    assert problems == []
    if not (settings.get("counter") or {}).get("install"):
        return
    for rel in ("site/privacy.html", "docs/msstore-submission-sheet.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "no telemetry" not in text.lower(), f"{rel} still says no telemetry: say that installs are counted first"
