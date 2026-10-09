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
                         {"id": "works-with", "title": "Work with Clips Kitty"},
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
       "section": "video-clipping", "relationship": "built-with", "uses": "api", "license": "MIT",
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
        "name": "Example Speech", "description": "Speech to text.", "section": "speech", "relationship": "built-with",
        "uses": "api",
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
    assert app["kind"] == "app" and app["badges"] == ["community"] and app["relationship"] == "built-with"
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
    ({"relationship": "related"}, "relationship: built-with (it uses Clips Kitty's API or SDK)"),
    ({"relationship": "related"}, "A project that doesn't is not an entry"),
    ({"uses": None}, "uses: what it uses of Clips Kitty"),
    ({"section": "nowhere"}, "section: one of built-with, works-with, video-clipping, gaming"),
    ({"section": "works-with"}, "adapter: an app in works-with names the listed pipeline"),
    ({"source": {}}, "source: github, huggingface or url"),
    ({"source": {"github": "https://gitlab.com/example-org/x"}}, "source.github: https://github.com/<owner>/<repo>"),
    ({"source": {"url": "http://example.com"}}, "source.url: an https address"),
    ({"source": {"url": "https://example.com/setup.exe"}}, "source.url: a page, not a file"),
    ({"source": {"github": "https://github.com/example-org/example-clipper",
                 "url": "https://github.com/example-org/example-clipper/releases/download/v1/setup.exe"}},
     "source.url: a page, not a file"),
    ({"source": {"github": "https://github.com/example-org/example-clipper",
                 "url": "https://github.com/example-org/example-clipper/releases/download/v1/tool.hta"}},
     "source.url: a page, not a file (the address ends in .hta)"),
    ({"source": {"github": "https://github.com/example-org/example-clipper",
                 "url": "https://github.com/example-org/example-clipper/releases/download/v1/tool"}},
     "source.url: a page, not a file (it is a GitHub address of a file"),
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


GH = APP["source"]["github"]


@pytest.mark.parametrize("source", [
    {"github": GH, "download": GH + "/releases"},
    {"github": GH, "download": "https://github.com/Example-Org/Example-Clipper/releases/latest/"},
    {"github": GH, "homepage": "https://example.org/", "download": "https://example.org/clipper"},
    {"github": GH, "homepage": "https://example.org/", "download": "https://www.example.org/clipper"},
    {"github": GH, "homepage": "https://www.example.org/", "download": "https://example.org/download"},
    {"github": GH, "homepage": "https://someone.github.io/", "download": "https://someone.github.io/clipper/get"},
    {"github": GH, "url": "https://example.org/clipper/", "homepage": "https://example.org/"},
    {"github": GH, "download": "https://apps.microsoft.com/detail/example-id"},
    {"github": GH, "homepage": "https://example.org/"},
])
def test_a_download_page_and_a_website_go_into_the_index_and_the_app_keeps_them(cat, source):
    cat.write("apps/example-clipper.yaml", {**APP, "source": source, "setup": "installer"})
    index, problems = cat.build()
    assert problems == []
    (entry,) = index["catalog"]
    assert entry["source"] == source and entry["setup"] == "installer"
    (kept,) = registry.check_index(index, ours=True)["catalog"]
    assert kept["source"] == source and kept["setup"] == "installer"


@pytest.mark.parametrize("source, fragment", [
    ({"github": GH, "download": GH + "/releases/download/v1.0/clipper-setup.exe"},
     "source.download: a page, not a file"),
    ({"github": GH, "homepage": "https://example.org/", "download": "https://example.org/files/Clipper.MSI"},
     "source.download: a page, not a file"),
    ({"github": GH, "homepage": "https://example.org/", "download": "https://example.org/clipper.tar.gz"},
     "source.download: a page, not a file"),
    ({"github": GH, "homepage": "https://example.org/", "download": "https://downloads.example.net/clipper"},
     "source.download: the GitHub repository's releases page"),
    # a subdomain isn't the homepage's site any more: only the exact name or its www twin
    ({"github": GH, "homepage": "https://example.org/", "download": "https://downloads.example.org/clipper"},
     "source.download: the GitHub repository's releases page"),
    ({"github": GH, "homepage": "https://www.com/", "download": "https://evil-site.com/get"},
     "source.homepage: a full website name"),
    ({"github": GH, "homepage": "https://www.co.uk/", "download": "https://someone-else.co.uk/get"},
     "source.download: the GitHub repository's releases page"),
    ({"github": GH, "homepage": "https://github.io/", "download": "https://someone-else.github.io/get"},
     "source.download: the GitHub repository's releases page"),
    ({"github": GH, "homepage": "https://www.github.io/", "download": "https://someone-else.github.io/get"},
     "source.download: the GitHub repository's releases page"),
    ({"github": GH, "homepage": "https://example.org/", "download": "https://notexample.org/clipper"},
     "source.download: the GitHub repository's releases page"),
    ({"github": GH, "download": "https://github.com/someone-else/example-clipper/releases"},
     "source.download: the GitHub repository's releases page"),
    ({"github": GH, "download": GH + "/releases/tag/v1.0"}, "source.download: the GitHub repository's releases page"),
    ({"github": GH, "download": "http://github.com/example-org/example-clipper/releases"},
     "source.download: a plain https address with no username or password"),
    ({"github": GH, "homepage": "https://example.org/", "download": "https://me:secret@example.org/get"},
     "source.download: a plain https address with no username or password"),
    # a browser reads the backslash as "/" and opens elsewhere.example.net
    ({"github": GH, "homepage": "https://example.org/", "download": "https://elsewhere.example.net\\.example.org/"},
     "source.download: a plain https address"),
    ({"github": GH, "homepage": "https://me@example.org/"}, "source.homepage: a plain https address with no username"),
    ({"github": GH, "homepage": "https://example.org/clipper.zip"}, "source.homepage: a page, not a file"),
    ({"github": GH, "homepage": "https://github.com/someone-else"}, "source.homepage: the project's own website"),
])
def test_a_download_link_must_be_a_page_on_the_projects_own_site(cat, source, fragment):
    cat.write("apps/example-clipper.yaml", {**APP, "source": source})
    index, problems = cat.build()
    assert any(fragment in p for p in problems), problems
    assert index["catalog"] == []


def _fetched(source):
    return {"id": "apps/example-clipper", "kind": "app", "name": "Example Clipper", "license": "MIT",
            "relationship": "built-with", "source": {"github": GH, **source}}


@pytest.mark.parametrize("homepage, download", [
    ("https://www.com/", "https://evil-site.com/get"),  # "com" is everyone's
    ("https://www.co.uk/", "https://someone-else.co.uk/get"),  # every .co.uk site
    ("https://github.io/", "https://someone-else.github.io/get"),  # anyone's GitHub Pages
    ("https://www.github.io/", "https://someone-else.github.io/get"),
    ("https://example.org/", "https://downloads.example.org/get"),  # a subdomain, not the same name
    ("https://www.example.org/", "https://downloads.example.org/get"),
])
def test_a_homepage_cant_vouch_for_a_download_on_another_site(homepage, download):
    """A download on the homepage's site is exactly its name or its www twin.
    The app reads a list the same way, so a fetched entry loses the link."""
    assert catalog.download_problem(download, github=GH, homepage=homepage)
    kept = registry._clean_entry(_fetched({"homepage": homepage, "download": download}), ours=False)
    assert "download" not in kept["source"]


@pytest.mark.parametrize("homepage, download", [
    ("https://example.org/", "https://example.org/get"),
    ("https://example.org/", "https://www.example.org/get"),
    ("https://www.example.org/", "https://example.org/get"),
    ("https://www.example.org/", "https://www.example.org/get"),
])
def test_a_download_on_the_homepages_name_or_its_www_twin_is_kept(homepage, download):
    assert catalog.download_problem(download, github=GH, homepage=homepage) is None
    kept = registry._clean_entry(_fetched({"homepage": homepage, "download": download}), ours=False)
    assert kept["source"]["download"] == download


@pytest.mark.parametrize("homepage, download", [
    ("https://gitlab.com/example-org/clipper", "https://gitlab.com/someone-else/tool/-/releases"),
    ("https://codeberg.org/example-org/clipper", "https://codeberg.org/someone-else/tool/releases"),
    ("https://bitbucket.org/example-org/clipper", "https://bitbucket.org/someone-else/tool/downloads/"),
    ("https://sourceforge.net/projects/example-clipper/", "https://sourceforge.net/projects/someone-else/files/"),
    ("https://sites.google.com/view/example-clipper", "https://sites.google.com/view/someone-else"),
    ("https://drive.google.com/drive/folders/example", "https://drive.google.com/drive/folders/someone-else"),
    ("https://docs.google.com/document/d/example", "https://docs.google.com/document/d/someone-else"),
    ("https://www.dropbox.com/sh/example", "https://www.dropbox.com/sh/someone-else"),
    ("https://hf.co/example-org", "https://hf.co/someone-else"),
    ("https://gist.github.com/example-org", "https://gist.github.com/someone-else"),
])
def test_a_homepage_on_a_site_many_people_share_is_refused_and_vouches_for_no_download(homepage, download):
    """On these sites strangers' pages share the homepage's website name, so
    the same name doesn't make a download the project's own."""
    assert catalog.homepage_problem(homepage) == (
        "source.homepage: the project's own website, not a page on a site where many people have pages, such as "
        "GitHub, GitLab or Google Sites (a GitHub page goes in github, a Hugging Face page in huggingface, any "
        "other in url)")
    assert catalog.download_problem(download, github=GH, homepage=homepage).startswith(
        "source.download: the GitHub repository's releases page")
    kept = registry._clean_entry(_fetched({"homepage": homepage, "download": download}), ours=False)
    assert kept["source"] == {"github": GH}


def test_a_trailing_dot_cant_take_a_homepage_or_download_past_the_shared_sites():
    """github.com. opens github.com, but the name wouldn't match the list."""
    assert catalog.homepage_problem("https://gitlab.com./someone").startswith("source.homepage: a plain https address")
    assert catalog.download_problem("https://gitlab.com./someone-else", github=GH,
                                    homepage="https://gitlab.com./someone").startswith(
        "source.download: a plain https address")
    assert catalog.url_problem("https://github.com./someone/clipper/raw/main/tool").startswith(
        "source.url: a page, not a file (it is a GitHub address of a file")


@pytest.mark.parametrize("path, ending", [
    *((f"app{e}", e) for e in (
        ".exe", ".msi", ".msp", ".msu", ".msix", ".msixbundle", ".appx", ".appxbundle", ".appinstaller",
        ".application", ".appref-ms", ".bat", ".cmd", ".com", ".pif", ".scr", ".cpl", ".reg", ".hta", ".ps1", ".vbs",
        ".vbe", ".js", ".jse", ".wsf", ".jar", ".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".tar", ".gz", ".xz", ".bz2",
        ".zip", ".7z", ".rar", ".cab", ".iso", ".img", ".vhd", ".vhdx", ".dmg", ".pkg", ".appimage", ".deb", ".rpm",
        ".apk", ".whl")),
    ("Setup.EXE", ".exe"), ("app.exe;", ".exe"), ("app.exe%20", ".exe"), ("app.exe.", ".exe"),
    ("app.exe.%20;", ".exe"), ("app.exe/", ".exe"), ("app.exe;jsessionid=1", ".exe"), ("app%2Eexe", ".exe"),
])
def test_an_address_that_ends_in_a_program_installer_or_archive_is_refused(path, ending):
    address = "https://example.com/" + path
    assert catalog.download_problem(address, homepage="https://example.com/") == \
        f"source.download: a page, not a file (the address ends in {ending}; the project's own page says which file to get)"
    assert catalog.homepage_problem(address) == f"source.homepage: a page, not a file (the address ends in {ending})"
    assert catalog.url_problem(address) == f"source.url: a page, not a file (the address ends in {ending})"


@pytest.mark.parametrize("address", [
    GH + "/releases/download/v1/tool", GH + "/releases/latest/download/tool", GH + "/raw/main/tool",
    GH + "/archive/refs/tags/v1", GH + "/zipball/main", GH + "/tarball/v1", GH + "/blob/main/tool?raw=true",
    "https://gist.github.com/someone/0123abc/raw/tool",
])
def test_a_github_address_of_a_file_is_refused_whatever_it_ends_in(address):
    reason = "it is a GitHub address of a file: a release download, a raw file or a source archive"
    assert catalog.download_problem(address, github=GH) == \
        f"source.download: a page, not a file ({reason}; the project's own page says which file to get)"
    assert catalog.url_problem(address) == f"source.url: a page, not a file ({reason})"
    kept = registry._clean_entry(_fetched({"url": address, "download": address}), ours=False)
    assert kept["source"] == {"github": GH}


@pytest.mark.parametrize("address, host", [
    ("https://raw.githubusercontent.com/example-org/example-clipper/main/tool", "raw.githubusercontent.com"),
    ("https://codeload.github.com/example-org/example-clipper/zip/main", "codeload.github.com"),
])
def test_an_address_on_a_github_file_host_is_refused(address, host):
    assert catalog.url_problem(address) == f"source.url: a page, not a file ({host} only serves files)"
    assert catalog.homepage_problem(address) == f"source.homepage: a page, not a file ({host} only serves files)"


@pytest.mark.parametrize("path", ["app.exe%00", "get/app%0A", "get%E2%80%AE/page", "page?x=%E2%80%8B", "page#%0D"])
def test_an_address_with_a_hidden_character_is_refused_and_says_so(path):
    """It is refused for the hidden character, not taken for a file."""
    address = "https://example.com/" + path
    hidden = ("an address with no hidden or control characters in it, such as a line break or a right-to-left "
              "mark, also when written with % (like %0A)")
    assert catalog.download_problem(address, homepage="https://example.com/") == f"source.download: {hidden}"
    assert catalog.homepage_problem(address) == f"source.homepage: {hidden}"
    assert catalog.url_problem(address) == f"source.url: {hidden}"
    assert catalog.url_problem("https://example.com/get‮/page") == f"source.url: {hidden}"


def test_an_address_that_cant_be_read_says_so():
    assert catalog.url_problem("https://[x/page") == \
        "source.url: a correctly written https address, such as https://example.org/page"


@pytest.mark.parametrize("path", ["", "download", "app.exe-guide", "get?file=app.exe", "releases/", "app.exe.html"])
def test_an_address_whose_path_doesnt_end_in_a_listed_ending_passes(path):
    """Only the end of the path is checked: get?file=app.exe passes though
    it may well be a file, and so would a page that sends the browser on to one."""
    address = "https://example.com/" + path
    assert catalog.download_problem(address, homepage="https://example.com/") is None
    assert catalog.url_problem(address) is None


@pytest.mark.parametrize("address", [GH, GH + "#readme", GH + "/releases", GH + "/releases/latest",
                                     GH + "/blob/main/README.md", GH + "/wiki"])
def test_a_github_page_isnt_taken_for_a_file(address):
    assert catalog.url_problem(address) is None


def test_setup_is_installer_or_technical(cat):
    cat.write("apps/example-clipper.yaml", {**APP, "setup": "easy"})
    assert any("setup: installer" in p for p in cat.build()[1])


def test_file_names_and_models_are_checked(cat):
    cat.write("apps/Bad_Name.yaml", APP)
    cat.write("apps/nested/deeper.yaml", APP)
    cat.write("models/no-home.yaml", {"name": "No home", "description": "A model.", "section": "speech",
                                      "relationship": "built-with", "uses": "api", "license": "MIT", "added": TODAY,
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


def test_the_readme_links_the_download_page_first_and_lists_technical_setup_last(cat):
    cat.write("apps/a-tool.yaml", {**APP, "name": "A Tool", "setup": "technical"})
    cat.write("apps/b-app.yaml", {**APP, "name": "B App", "setup": "installer", "source": {
        "github": GH, "homepage": "https://example.org/", "download": "https://example.org/download"}})
    cat.write("apps/c-app.yaml", {**APP, "name": "C App", "source": {"github": GH, "homepage": "https://example.org/"}})
    index, problems = cat.build()
    assert problems == []
    body = catalog.readme_body(index["sections"], index["catalog"])
    lines = [line for line in body.splitlines() if line.startswith("- [") and "](https://" in line]
    assert lines[0].startswith("- [B App](https://example.org/download) - ")
    assert lines[1].startswith("- [C App](https://example.org/) - ")
    assert lines[2].startswith(f"- [A Tool]({GH}) - ") and "needs technical setup (command line or Python)" in lines[2]


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
             "relationship": "built-with",
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


def test_a_link_to_another_project_is_not_an_entry_whatever_a_list_says():
    """Only what is built with Clips Kitty is shown. The build refuses any
    other entry (the parametrized cases above); the app drops one that
    arrives in a fetched list, and one that says nothing of itself."""
    for said in ({"relationship": "related"}, {"relationship": "built-for"}, {}):
        index = _remote_index()
        index["catalog"][0] = {**index["catalog"][0], **said}
        if not said:
            del index["catalog"][0]["relationship"]
        for ours in (False, True):
            assert [e["id"] for e in registry.check_index(index, ours=ours)["catalog"]] == ["tools/sdk"]


def test_an_entry_from_an_index_keeps_only_well_formed_fields():
    bad = {"id": "apps/odd", "kind": "app", "name": "Odd", "license": "MIT", "source": {"github": "https://github.com/a/b"},
           "relationship": "built-with",
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
    counter = json.loads(registry.bundled_path().read_text(encoding="utf-8")).get("counter") or {}
    if not counter.get("install"):
        return
    for rel in ("site/privacy.html", "docs/msstore-submission-sheet.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "no telemetry" not in text.lower(), f"{rel} still says no telemetry: say that installs are counted first"
