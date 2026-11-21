"""Regression tests for the static website's release download buttons."""

from scripts.check_site_downloads import RELEASE_URL, check_site


VERSION = "2.1.0"
URL = RELEASE_URL.format(version=VERSION)


def _page(site, name, body):
    site.mkdir(exist_ok=True)
    (site / name).write_text(body, encoding="utf-8")


def test_valid_download_links_across_pages(tmp_path):
    _page(tmp_path, "index.html", f'<a href="{URL}">Download for Windows</a>')
    _page(tmp_path, "gaming.html",
          f'<a href="{URL}"><span>Download</span> for Windows</a>')
    checked, issues = check_site(tmp_path, VERSION)
    assert checked == 2
    assert issues == []


def test_rejects_stale_release_tag_and_asset(tmp_path):
    old = RELEASE_URL.format(version="2.0.0")
    _page(tmp_path, "index.html", f'<a href="{old}">Download for Windows</a>')
    checked, issues = check_site(tmp_path, VERSION)
    assert checked == 1
    assert len(issues) == 1
    assert "index.html" in issues[0]
    assert "2.1.0" in issues[0]


def test_rejects_tag_and_filename_mismatch(tmp_path):
    broken = URL.replace("Setup-2.1.0.exe", "Setup-2.0.0.exe")
    _page(tmp_path, "index.html", f'<a href="{broken}">Download for Windows</a>')
    _, issues = check_site(tmp_path, VERSION)
    assert len(issues) == 1


def test_missing_download_buttons_fails(tmp_path):
    _page(tmp_path, "index.html", '<a href="about.html">About the project</a>')
    checked, issues = check_site(tmp_path, VERSION)
    assert checked == 0
    assert len(issues) == 1
    assert "no 'Download for Windows'" in issues[0]


def test_no_site_pages_fails(tmp_path):
    checked, issues = check_site(tmp_path, VERSION)
    assert checked == 0
    assert len(issues) == 1
    assert "no HTML pages" in issues[0]


def test_ignores_other_release_links(tmp_path):
    _page(tmp_path, "index.html",
          '<a href="https://github.com/other/project/releases/download/v1/app.exe">'
          'Download source archive</a>'
          f'<a href="{URL}">Download for Windows</a>')
    checked, issues = check_site(tmp_path, VERSION)
    assert checked == 1
    assert issues == []
