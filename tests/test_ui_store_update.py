"""A Microsoft Store copy says when the Store has a newer version.

The Store updates a Store copy, on its own schedule; the app never downloads
or installs anything there. What it does is ask the Store's public catalog
which version is offered and compare that with the one running, so somebody
whose Store has not updated yet is told, with a button to the Store page.

The two decisions in that (which version the catalog's answer names, and
whether one version is newer than another) are in ui/src/main/storeVersion.ts,
which has no Electron in it and is run here under Node. The rest is checked as
text: the ids match the ones the package and the README use, and a Store copy
still never reaches for the installer's updater.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "ui" / "src" / "main"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _run(tmp_path, cases: list, body: str) -> list:
    """Run `body` (JavaScript: `m` is the module, `c` one case) over the cases."""
    (tmp_path / "storeVersion.ts").write_text((MAIN / "storeVersion.ts").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps((tmp_path / 'storeVersion.ts').as_uri())});"
        "const fs = await import('node:fs');"
        f"const cases = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'cases.json'))}, 'utf8'));"
        f"console.log(JSON.stringify(cases.map((c) => {{ {body} }})));"
    )
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _catalog(*full_names):
    """The catalog's answer, cut down to the part that names packages."""
    return {"Product": {"ProductId": "9NB6XT7DSQZZ", "DisplaySkuAvailabilities": [
        {"Sku": {"Properties": {"Packages": [{"PackageFullName": n, "MaxDownloadSizeInBytes": 1}]}}}
        for n in full_names]}}


def test_the_version_the_store_offers_is_read_from_its_packages(tmp_path):
    ours = "ClipsStudio.ClipsStudio_{}_x64__315g1r74a6w58"
    cases = [
        _catalog(ours.format("2.0.0.0")),
        _catalog(ours.format("2.0.0.0"), ours.format("2.0.0.0")),  # one per SKU, as the Store answers today
        _catalog(ours.format("1.2.0.0"), ours.format("2.1.0.0"), ours.format("2.0.0.0")),  # the highest
        _catalog(ours.format("2.9.0.0"), ours.format("2.10.0.0")),  # by number, not by letter
        _catalog("SomeoneElse.Other_9.0.0.0_x64__abcdefghjkmnp", ours.format("2.0.0.0")),  # only this app's
        _catalog("SomeoneElse.Other_9.0.0.0_x64__abcdefghjkmnp"),
        _catalog(ours.format("2.0.0")),  # not a package version
        _catalog(),
        {"Product": None},
        {"code": "NotFound"},
        None,
        "not json at all",
        [],
    ]
    got = _run(tmp_path, cases, "return m.storeVersionOf(c)")
    assert got == ["2.0.0", "2.0.0", "2.1.0", "2.10.0", "2.0.0", None, None, None, None, None, None, None, None]


def test_newer_means_a_later_version_and_nothing_else(tmp_path):
    cases = [
        ("2.0.0", "1.1.4", True), ("2.0.0", "1.2.0", True), ("2.0.1", "2.0.0", True), ("2.10.0", "2.9.0", True),
        ("2.0.0", "2.0.0", False), ("2.0", "2.0.0", False), ("2.0.0", "2.0", False),
        ("1.2.0", "2.0.0", False),  # the Store behind this copy (a test build): no "update"
        ("2.1.0-beta.1", "2.0.0", False), ("2.1.0", "2.1.0-beta.1", False), ("", "2.0.0", False),
        ("latest", "2.0.0", False),
    ]
    got = _run(tmp_path, [list(c[:2]) for c in cases], "return m.isNewer(c[0], c[1])")
    assert got == [c[2] for c in cases]


def test_the_ids_are_the_ones_the_package_and_the_readme_use():
    source = (MAIN / "storeVersion.ts").read_text(encoding="utf-8")
    product = re.search(r"STORE_PRODUCT_ID = '([A-Z0-9]+)'", source).group(1)
    identity = re.search(r"STORE_IDENTITY = '([\w.]+)'", source).group(1)
    assert f"apps.microsoft.com/detail/{product}" in (ROOT / "README.md").read_text(encoding="utf-8")
    builder = (ROOT / "ui" / "electron-builder.yml").read_text(encoding="utf-8")
    assert re.search(rf"^\s*identityName: {re.escape(identity)}\s*$", builder, re.M)
    # The Store's own page and catalog, by that id, and nowhere else.
    assert "ms-windows-store://pdp/?ProductId=${STORE_PRODUCT_ID}" in source
    assert "https://displaycatalog.mp.microsoft.com/v7.0/products/${STORE_PRODUCT_ID}?" in source


def test_a_store_copy_asks_the_store_and_never_runs_the_installers_updater():
    """Everything a Store copy does about updates is before the `return` that
    ends its branch, and none of it touches electron-updater."""
    source = (MAIN / "updater.ts").read_text(encoding="utf-8")
    start = source.index("if (isMicrosoftStore()) {")
    branch = source[start:source.index("\n    return\n  }\n", start)]
    assert "autoUpdater" not in branch
    assert "net.fetch(STORE_CATALOG" in branch and "storeVersionOf(" in branch and "isNewer(" in branch
    for handler in ("update:check", "update:download", "update:install", "update:skip", "update:prefs"):
        assert f"ipcMain.handle('{handler}'" in branch, handler
    # Download and install stay refused there: the Store does both.
    assert "ipcMain.handle('update:download', async () => ({ ok: false }))" in branch
    assert "ipcMain.handle('update:install', () => ({ ok: false }))" in branch
    # The button opens the Store's page, a fixed address the page can't choose.
    assert "shell.openExternal(STORE_PAGE)" in source


def test_the_screens_say_which_version_and_offer_the_store():
    renderer = ROOT / "ui" / "src" / "renderer" / "src"
    settings = (renderer / "pages" / "Settings.tsx").read_text(encoding="utf-8")
    banner = (renderer / "components" / "UpdateBanner.tsx").read_text(encoding="utf-8")
    assert "Version ${state.version} is in the Microsoft Store. This copy is ${state.current}." in settings
    assert "You're on the latest version (${state.current})." in settings
    assert "Couldn't check the Microsoft Store just now." in settings
    assert "window.studio.update.openStore()" in settings and "window.studio.update.openStore()" in banner
    # The bar shows only when the Store is ahead and that version wasn't skipped.
    assert "if (!s.behind || s.skipped) return null" in banner
    assert "openStore: (): Promise<{ ok: boolean }> => ipcRenderer.invoke('update:openStore')" in (
        ROOT / "ui" / "src" / "preload" / "index.ts").read_text(encoding="utf-8")
