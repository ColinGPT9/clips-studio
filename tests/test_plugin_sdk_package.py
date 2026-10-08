"""The SDK as a package: pyproject.toml, --version, the changelog, the
README's links, what the SDK imports, and the wheel.

Like every tests/test_plugin_sdk_*.py file, this needs only pytest and PyYAML
and imports nothing from Clips Kitty's engine, so it also runs in CI's SDK
(Windows) job. test_sdk_test_files_import_nothing_from_the_engine keeps it so.
"""

import ast
import importlib
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
PACKAGE = SDK / "clipskitty_sdk"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import PLUGIN_API_VERSION, __version__  # noqa: E402
from clipskitty_sdk.__main__ import main, version_line  # noqa: E402

REPO = "https://github.com/ColinGPT9/clips-studio"
# The one sentence that says which Clips Kitty runs plugins. It lives under
# "Which release runs plugins" in versioning.md; other pages repeat it word
# for word.
RELEASE_SENTENCE = ("No Clips Kitty release runs plugins yet. Plugins made with SDK 1.2.0 need the first "
                    "release that includes it; until that is out, run Clips Kitty from source.")
# The newest release in CHANGELOG.md when that sentence was written.
NEWEST_RELEASE_WHEN_WRITTEN = "2.0.0"
CHANGELOG_TOP = ("No version is on PyPI yet. Clips Kitty bundles the SDK from the commit it is built from, "
                 "so a plugin always runs with the SDK of the Clips Kitty it runs in.")
# The SDK imports only the standard library, apart from these lazy imports,
# each inside a function of the one module named.
LAZY_IMPORTS = {"manifest.py": {"yaml"}, "testing.py": {"pytest"}}
# What a tests/test_plugin_sdk_*.py file may import besides the standard
# library: CI's SDK (Windows) job installs only pytest and PyYAML.
SDK_TEST_IMPORTS = {"pytest", "yaml", "clipskitty_sdk"}


def _pyproject() -> dict:
    tomllib = pytest.importorskip("tomllib")  # Python 3.11 and later
    return tomllib.loads((SDK / "pyproject.toml").read_text(encoding="utf-8"))


def _imports(tree: ast.AST) -> list[tuple[str, int, bool]]:
    """(top-level module, line, inside a function) for each absolute import."""
    found = []

    def visit(node, in_function):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                found.extend((alias.name.split(".")[0], child.lineno, in_function) for alias in child.names)
            elif isinstance(child, ast.ImportFrom) and child.level == 0 and child.module:
                found.append((child.module.split(".")[0], child.lineno, in_function))
            visit(child, in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)))

    visit(tree, False)
    return found


def _paragraph_under(text: str, heading: str) -> str:
    """The first paragraph under a Markdown heading, on one line."""
    match = re.search(rf"^#+ {re.escape(heading)}\n\n(.+?)(?:\n\n|\Z)", text, re.M | re.S)
    assert match, f"no '{heading}' heading"
    return " ".join(match.group(1).split())


def _anchors(markdown: Path) -> set[str]:
    """GitHub's anchors for a Markdown file's headings."""
    out = set()
    for heading in re.findall(r"^#{1,6} +(.+?) *#*$", markdown.read_text(encoding="utf-8"), re.M):
        out.add(re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-"))
    return out


def _link_problems(name: str, targets: list[str]) -> list[str]:
    """Why each link would break outside this repository (on PyPI, or in a
    plugin's own repository): a relative link, or a link to this repository's
    `main` that names no file, folder or heading in this checkout."""
    problems = []
    for target in targets:
        if not target.startswith(("https://", "http://", "mailto:")):
            problems.append(f"{name}: {target} is relative, so it breaks outside the repository")
            continue
        match = re.fullmatch(rf"{re.escape(REPO)}(?:/(blob|tree)/main/([^#?]+))?(?:#(.+))?", target)
        if not match:
            continue
        kind, path, anchor = match.groups()
        where = ROOT / path if path else ROOT / "README.md"
        if kind == "tree" and not where.is_dir():
            problems.append(f"{name}: {target} names no folder {path} in this checkout")
        elif kind != "tree" and not where.is_file():
            problems.append(f"{name}: {target} names no file {path} in this checkout")
        elif anchor and where.suffix == ".md" and anchor not in _anchors(where):
            problems.append(f"{name}: {target} names no heading #{anchor} in {where.relative_to(ROOT)}")
    return problems


def _markdown_links(text: str) -> list[str]:
    inline = re.findall(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)", text)
    reference = re.findall(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)", text, re.M)
    html = re.findall(r"\b(?:href|src)=\"([^\"]+)\"", text)
    return inline + reference + html


def _beyond_sdk_tests(module: str) -> bool:
    """Whether a module is more than CI's SDK (Windows) job has: the standard
    library, pytest, PyYAML and the SDK."""
    top = module.split(".")[0]
    return top not in sys.stdlib_module_names and top not in SDK_TEST_IMPORTS


def _imports_beyond_the_stdlib(module: str, seen: set) -> list[str]:
    """For an engine module: what it and its packages import at module level,
    followed through the engine, that isn't the standard library."""
    problems = []
    parts = module.split(".")
    for depth in range(1, len(parts) + 1):
        name = ".".join(parts[:depth])
        if name in seen:
            continue
        seen.add(name)
        base = ROOT.joinpath(*parts[:depth])
        path = base / "__init__.py" if (base / "__init__.py").is_file() else base.with_suffix(".py")
        if not path.is_file():
            continue  # a folder without __init__.py, or a name rather than a module
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
            else:
                continue
            for other in imported:
                top = other.split(".")[0]
                if not _beyond_sdk_tests(other):
                    continue
                if (ROOT / top).is_dir() or (ROOT / f"{top}.py").is_file():
                    problems += _imports_beyond_the_stdlib(other, seen)
                else:
                    problems.append(f"{name} imports {other}")
    return problems


# ---- pyproject.toml ------------------------------------------------------------


def test_pyproject_has_a_console_script_and_no_licence_classifier():
    project = _pyproject()["project"]
    assert project["scripts"] == {"clipskitty-sdk": "clipskitty_sdk.__main__:main"}
    module, _, function = project["scripts"]["clipskitty-sdk"].partition(":")
    assert getattr(importlib.import_module(module), function) is main

    # license is a PEP 639 expression, and setuptools 77 and later refuse one
    # together with a "License ::" classifier.
    assert project["license"] == "MIT"
    assert [c for c in project["classifiers"] if c.startswith("License ::")] == []
    assert set(project["classifiers"]) >= {
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Multimedia :: Video",
        "Typing :: Typed",
    }
    assert project["requires-python"] == ">=3.10"
    assert project["keywords"] == ["clips-kitty", "plugin", "sdk", "video", "highlights"]
    assert project["urls"]["Issues"] == f"{REPO}/issues"
    assert project["urls"]["Changelog"] == f"{REPO}/blob/main/sdk/python/CHANGELOG.md"
    assert "# Not published to PyPI;" in (SDK / "pyproject.toml").read_text(encoding="utf-8")


def test_pyproject_has_yaml_and_test_extras():
    extras = _pyproject()["project"]["optional-dependencies"]
    # The generated tests of a plugin need pytest: pip install "clipskitty-sdk[yaml,test]".
    assert extras == {"yaml": ["PyYAML>=6"], "test": ["pytest"]}


def test_py_typed_ships_as_package_data():
    typed = PACKAGE / "py.typed"
    assert typed.is_file() and typed.read_bytes() == b""
    assert "py.typed" in _pyproject()["tool"]["setuptools"]["package-data"]["clipskitty_sdk"]


def test_byte_code_caches_stay_out_of_the_wheel():
    # templates/**/* would otherwise take the __pycache__ a template's tests
    # leave in the source folder; the wheel test below checks a built wheel.
    setuptools = _pyproject()["tool"]["setuptools"]
    assert "templates/**/*" in setuptools["package-data"]["clipskitty_sdk"]
    assert setuptools["exclude-package-data"]["clipskitty_sdk"] == ["*.pyc"]


# ---- the version -----------------------------------------------------------------


def test_version_prints_the_sdk_and_contract_versions(capsys, tmp_path):
    expected = f"clipskitty-sdk {__version__} (plugin contract {PLUGIN_API_VERSION})"
    assert version_line() == expected
    assert re.fullmatch(r"clipskitty-sdk \d+\.\d+\.\d+ \(plugin contract \d+\)", expected)

    with pytest.raises(SystemExit) as stopped:
        main(["--version"])
    assert stopped.value.code == 0
    assert capsys.readouterr().out == expected + "\n"

    # As a developer runs it: python -m clipskitty_sdk, from anywhere.
    env = {**os.environ, "PYTHONPATH": str(SDK)}
    done = subprocess.run([sys.executable, "-m", "clipskitty_sdk", "--version"], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == expected


def test_changelog_top_version_is_the_sdk_version():
    text = (SDK / "CHANGELOG.md").read_text(encoding="utf-8")
    assert CHANGELOG_TOP in " ".join(text.split())
    versions = re.findall(r"^## (\d+\.\d+\.\d+)\b", text, re.M)
    assert versions, "sdk/python/CHANGELOG.md has no '## X.Y.Z' heading"
    assert versions[0] == __version__, (
        f"sdk/python/CHANGELOG.md's newest version is {versions[0]}, but clipskitty_sdk.__version__ is "
        f"{__version__}: add a heading for it"
    )
    assert versions == sorted(versions, key=lambda v: tuple(int(n) for n in v.split(".")), reverse=True)


# ---- which release runs plugins -----------------------------------------------


def test_versioning_says_which_release_runs_plugins():
    versioning = (ROOT / "docs" / "developers" / "versioning.md").read_text(encoding="utf-8")
    sentence = _paragraph_under(versioning, "Which release runs plugins")
    assert sentence == RELEASE_SENTENCE
    readme = " ".join((SDK / "README.md").read_text(encoding="utf-8").split())
    assert sentence in readme, "sdk/python/README.md doesn't repeat 'Which release runs plugins' word for word"

    # A tripwire: once a release is added, the sentence may no longer be true.
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = re.search(r"^## (\d+\.\d+\.\d+)\b", changelog, re.M).group(1)
    assert newest == NEWEST_RELEASE_WHEN_WRITTEN, (
        "A Clips Kitty release was added to CHANGELOG.md: update 'Which release runs plugins' in "
        "docs/developers/versioning.md and the pages that repeat it"
    )


# ---- links that work on PyPI and in a plugin's own repository ------------------


def test_readme_and_changelog_links_work_outside_the_repository():
    problems = []
    for name in ("README.md", "CHANGELOG.md"):
        text = (SDK / name).read_text(encoding="utf-8")
        links = _markdown_links(text)
        assert links, f"sdk/python/{name} has no links to check"
        problems += _link_problems(f"sdk/python/{name}", links)
        # pip's git+…#subdirectory= must name a folder that can be installed.
        for folder in re.findall(r"#subdirectory=([\w/.-]+)", text):
            if not (ROOT / folder / "pyproject.toml").is_file():
                problems.append(f"sdk/python/{name}: #subdirectory={folder} holds no pyproject.toml")
    problems += _link_problems("sdk/python/pyproject.toml", list(_pyproject()["project"]["urls"].values()))
    assert problems == []


# ---- what the SDK and its tests import -------------------------------------------


def test_every_sdk_module_imports_only_the_standard_library():
    problems = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE)
        if rel.parts[0] == "templates":
            continue  # files `new` writes into a plugin, not SDK modules
        lazy = LAZY_IMPORTS.get(rel.as_posix(), set())
        for name, line, in_function in _imports(ast.parse(path.read_text(encoding="utf-8"))):
            if name in sys.stdlib_module_names or name == "clipskitty_sdk":
                continue
            if name in lazy and in_function:
                continue
            where = "at module level" if name in lazy else ""
            problems.append(f"clipskitty_sdk/{rel.as_posix()} line {line} imports {name} {where}".rstrip())
    assert problems == []


def test_sdk_test_files_import_nothing_from_the_engine():
    files = sorted((ROOT / "tests").glob("test_plugin_sdk_*.py"))
    assert Path(__file__).resolve() in files
    conftest = ast.parse((ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))
    engine_modules, engine_names = [], set()  # what conftest.py imports from the engine at module level
    for node in conftest.body:
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and _beyond_sdk_tests(node.module):
            engine_modules.append(node.module)
            engine_names |= {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _beyond_sdk_tests(alias.name):
                    engine_modules.append(alias.name)
                    engine_names.add(alias.asname or alias.name.split(".")[0])

    # conftest.py's fixtures that use the engine, directly or through another fixture.
    fixtures = {node.name: node for node in conftest.body if isinstance(node, ast.FunctionDef)
                and any("fixture" in ast.unparse(d) for d in node.decorator_list)}
    engine_fixtures: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, node in fixtures.items():
            uses = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            args = {a.arg for a in node.args.args}
            imports_engine = any(_beyond_sdk_tests(module) for module, _, _ in _imports(node))
            if name not in engine_fixtures and (uses & engine_names or args & engine_fixtures or imports_engine):
                engine_fixtures.add(name)
                changed = True

    problems = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, line, _ in _imports(tree):
            if _beyond_sdk_tests(name):
                problems.append(f"{path.name} line {line} imports {name}")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in sorted({a.arg for a in node.args.args} & engine_fixtures):
                    problems.append(f"{path.name} line {node.lineno}: {node.name} uses conftest.py's "
                                    f"{arg} fixture, which needs the engine")

    # pytest loads conftest.py for these files too, so the engine modules it
    # imports at module level must need nothing beyond the standard library.
    seen: set = set()
    for module in engine_modules:
        problems += [f"conftest.py: {p}" for p in _imports_beyond_the_stdlib(module, seen)]
    assert problems == []


# ---- the wheel ------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("CLIPSKITTY_SDK_WHEEL"),
                    reason="needs CLIPSKITTY_SDK_WHEEL, a built wheel (CI's SDK (Windows) job sets it)")
def test_the_wheel_holds_py_typed_schema_and_templates():
    wheel_path = Path(os.environ["CLIPSKITTY_SDK_WHEEL"])
    if not wheel_path.is_absolute():
        wheel_path = ROOT / wheel_path
    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())
        modules = {f"clipskitty_sdk/{p.relative_to(PACKAGE).as_posix()}" for p in PACKAGE.rglob("*.py")
                   if "__pycache__" not in p.parts}
        # Every file `new` writes from, the dotfiles among them under the names they are stored by.
        templates = {f"clipskitty_sdk/{p.relative_to(PACKAGE).as_posix()}" for p in (PACKAGE / "templates").rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts}
        assert {"clipskitty_sdk/templates/_shared/gitignore",
                "clipskitty_sdk/templates/_shared/github/workflows/clipskitty-check.yml"} <= templates
        assert {f"clipskitty_sdk/templates/{name}/clipskitty.yaml" for name in
                ("blank", "transcript", "game-events", "rater", "understander")} <= templates
        expected = modules | templates | {"clipskitty_sdk/py.typed", "clipskitty_sdk/schema/clipskitty.schema.json"}
        assert sorted(expected - names) == []
        assert [n for n in names if n.endswith(".pyc") or "__pycache__" in n] == []

        dist_info = next(n.split("/")[0] for n in names if n.split("/")[0].endswith(".dist-info"))
        # A wheel built on Windows can write its metadata with CRLF line ends.
        entry_points = wheel.read(f"{dist_info}/entry_points.txt").decode("utf-8").replace("\r\n", "\n")
        assert re.search(r"^clipskitty-sdk\s*=\s*clipskitty_sdk\.__main__:main$", entry_points, re.M)
        metadata = wheel.read(f"{dist_info}/METADATA").decode("utf-8").replace("\r\n", "\n")
    assert re.search(rf"^Version: {re.escape(__version__)}$", metadata, re.M)
    assert re.search(r"^License-Expression: MIT$", metadata, re.M)
    assert re.search(r"^Provides-Extra: yaml$", metadata, re.M)
    assert re.search(r"^Provides-Extra: test$", metadata, re.M)
    assert re.search(r"^Classifier: Typing :: Typed$", metadata, re.M)
    assert not re.search(r"^Classifier: License ::", metadata, re.M)
