"""The developer launch drafts (docs/platform/developer-launch-drafts.md).

The file holds drafts that wait for Colin: posts, Discussions categories, a
template repository's README, a badge, a licence sentence for CONTRIBUTING.md
and a PyPI release workflow. These tests keep it a draft and keep it true: it
starts with the DRAFT line, links only to pages that were checked or that
this checkout backs, names no action version it can't source, no model and
no real game, says which release runs plugins, and its picture, commands,
output and template README are the ones the SDK and the tutorial have. The
drafted workflow stays out of .github/workflows/.

It reads the names of real games from Clips Kitty's own registry, so it is
an engine test, not one of the SDK's own (tests/test_plugin_sdk_*.py).
"""

import fnmatch
import re
import sys
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
for path in (str(ROOT), str(SDK)):
    if path not in sys.path:
        sys.path.insert(0, path)

from clipskitty_sdk import scaffold  # noqa: E402

from plugins import registry  # noqa: E402

DRAFTS = ROOT / "docs" / "platform" / "developer-launch-drafts.md"
WORKFLOWS = ROOT / ".github" / "workflows"
TUTORIAL = ROOT / "docs" / "developers" / "first-game-pipeline.md"
REPO = "https://github.com/ColinGPT9/clips-studio"
DRAFT_LINE = ("**DRAFT. Nothing here is posted, published or created. Each item needs Colin's approval "
              "(see the SDK plan's approval list).**")
LICENCE_SENTENCE = ("Contributions to `sdk/python/` and the MIT examples are under MIT, and to "
                    "`awesome-clips-kitty/` under CC0-1.0; everything else is AGPL-3.0-or-later.")

# Pages outside this repository that the drafts may link, each fetched (HTTP 200) and read on 2026-10-08.
CONFIRMED = {
    "https://news.ycombinator.com/showhn.html",
    "https://news.ycombinator.com/newsguidelines.html",
    "https://dev.to/t/showdev",
    "https://dev.to/terms",
    "https://dev.to/guidelines-for-ai-assisted-articles-on-dev",
    "https://pycoders.com/submissions",
    "https://lobste.rs/about",
    "https://lobste.rs/tags",
    "https://docs.github.com/en/discussions/managing-discussions-for-your-community/managing-categories-for-discussions",
    "https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-template-repository",
    "https://shields.io/badges/static-badge",
    "https://img.shields.io/badge/built_for-Clips_Kitty-0ea5e9",  # the badge itself: an SVG reading "built for: Clips Kitty"
    "https://docs.pypi.org/trusted-publishers/",
    "https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/",
    "https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/",
}

FENCE = re.compile(r"^(`{3,})\s*([\w-]*)\s*$")
PYPA_NOTE = re.compile(r"\(as the PyPA guide shows on \d{4}-\d{2}-\d{2}\)")


def text() -> str:
    return DRAFTS.read_text(encoding="utf-8")


def flat(value: str) -> str:
    return " ".join(value.split())


def fenced(markdown: str) -> list[tuple[str, str]]:
    """The top-level fenced blocks of some Markdown, as (language, body). A
    block opened with four backticks holds blocks of three, as CommonMark has
    it: only a fence at least as long as the opening one closes it."""
    out = []
    opening, lang, body = None, "", []
    for line in markdown.splitlines():
        m = FENCE.match(line)
        if opening is None:
            if m:
                opening, lang, body = m.group(1), m.group(2), []
        elif m and not m.group(2) and len(m.group(1)) >= len(opening):
            out.append((lang, "\n".join(body) + "\n"))
            opening = None
        else:
            body.append(line)
    assert opening is None, "a fenced block is never closed"
    return out


def outside_blocks(markdown: str) -> str:
    """The Markdown with its fenced blocks left out."""
    kept, opening = [], None
    for line in markdown.splitlines():
        m = FENCE.match(line)
        if opening is None:
            if m:
                opening = m.group(1)
            else:
                kept.append(line)
        elif m and not m.group(2) and len(m.group(1)) >= len(opening):
            opening = None
    return "\n".join(kept)


def section(title: str) -> str:
    """One `## ` section of the drafts, its blocks included. A `## ` line
    inside a block (a drafted post's own headings) doesn't start a section."""
    sections: dict[str, list[str]] = {}
    current, opening = None, None
    for line in text().splitlines():
        m = FENCE.match(line)
        if opening is None and line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
            continue
        if opening is None and m:
            opening = m.group(1)
        elif opening is not None and m and not m.group(2) and len(m.group(1)) >= len(opening):
            opening = None
        if current is not None:
            sections[current].append(line)
    assert title in sections, title
    return "\n".join(sections[title]) + "\n"


def md_anchors(markdown: str) -> set[str]:
    """GitHub's anchors for the headings of some Markdown, outside its blocks."""
    out = set()
    for line in outside_blocks(markdown).splitlines():
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            slug = re.sub(r"[^\w\- ]", "", m.group(1).strip().lower()).replace(" ", "-")
            n, base = 1, slug
            while slug in out:
                slug, n = f"{base}-{n}", n + 1
            out.add(slug)
    return out


def release_sentence() -> str:
    """The sentence that says which release runs plugins; docs/developers/versioning.md is its source."""
    page = (ROOT / "docs" / "developers" / "versioning.md").read_text(encoding="utf-8")
    return flat(page.split("## Which release runs plugins", 1)[1].strip().split("\n\n", 1)[0])


def dev_post() -> str:
    blocks = [body for lang, body in fenced(section("DEV")) if lang == "markdown"]
    assert len(blocks) == 1
    return blocks[0]


def drafted_workflow() -> str:
    blocks = [body for lang, body in fenced(section("A PyPI release workflow")) if lang == "yaml"]
    assert len(blocks) == 1
    return blocks[0]


def repo_uses() -> set[str]:
    """Every `uses:` value in this repository's workflows, its version comment included."""
    out = set()
    for path in WORKFLOWS.glob("*.yml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*(?:-\s*)?uses:\s*(.+?)\s*$", line)
            if m:
                out.add(flat(m.group(1)))
    return out


# ---- it is a draft ----------------------------------------------------------------------------


def test_it_starts_with_the_draft_line():
    assert text().splitlines()[0] == DRAFT_LINE


def test_the_workflow_is_not_in_github_workflows():
    assert not (WORKFLOWS / "sdk-release.yml").exists()
    for path in WORKFLOWS.glob("*"):
        workflow = path.read_text(encoding="utf-8")
        assert "gh-action-pypi-publish" not in workflow, path.name
        assert "sdk-v" not in workflow, path.name
    assert "gh-action-pypi-publish" in drafted_workflow()


def test_the_drafted_workflow_runs_only_on_an_sdk_tag():
    data = yaml.safe_load(drafted_workflow())
    trigger = data.get("on", data.get(True))  # YAML 1.1 reads a bare `on` as true
    assert trigger == {"push": {"tags": ["sdk-v*"]}}
    assert data["permissions"] == {"contents": "read"}
    assert set(data["jobs"]) == {"build", "publish"}
    publish = data["jobs"]["publish"]
    assert publish["needs"] == ["build"]
    assert publish["environment"] == {"name": "pypi"}
    assert publish["permissions"] == {"id-token": "write"}
    assert "permissions" not in data["jobs"]["build"]
    steps = "\n".join(str(step.get("run", "")) for step in data["jobs"]["build"]["steps"])
    assert '"$GITHUB_REF_NAME" != "sdk-v$version"' in steps
    assert "python -m build sdk/python --outdir dist" in steps
    # The app's release tags start the engine image's workflow; an SDK tag doesn't, and the reverse.
    image = yaml.safe_load((WORKFLOWS / "docker-image.yml").read_text(encoding="utf-8"))
    app_tags = image.get("on", image.get(True))["push"]["tags"]
    assert not any(fnmatch.fnmatch("sdk-v1.2.0", pattern) for pattern in app_tags)
    assert not fnmatch.fnmatch("v2.1.0", "sdk-v*")


# ---- it is true -------------------------------------------------------------------------------


def _repo_url_problem(url: str) -> str | None:
    """Why a link into this repository isn't one this checkout backs, or None when it is."""
    if url in (REPO, f"{REPO}/discussions"):
        return None
    if url.startswith(f"{REPO}#subdirectory="):
        folder = url.split("=", 1)[1]
        return None if (ROOT / folder / "pyproject.toml").is_file() else f"no {folder}/pyproject.toml"
    if url.startswith(f"{REPO}#"):
        anchor = url.split("#", 1)[1]
        return None if anchor in md_anchors((ROOT / "README.md").read_text(encoding="utf-8")) else "no such heading"
    if url.startswith(f"{REPO}/blob/main/"):
        rel, _, anchor = url.removeprefix(f"{REPO}/blob/main/").partition("#")
        path = ROOT / rel
        if not path.is_file():
            return f"no {rel} in this checkout"
        if anchor and anchor not in md_anchors(path.read_text(encoding="utf-8")):
            return f"no #{anchor} in {rel}"
        return None
    return "not an address this checkout backs"


def test_every_url_is_one_confirmed():
    urls = {u.rstrip(".,;:") for u in re.findall(r"https?://[^\s<>()\"'`\]]+", text())}
    problems = []
    for url in sorted(urls):
        if url.startswith(REPO):
            why = _repo_url_problem(url)
        else:
            why = None if url in CONFIRMED else "not one of the pages checked for these drafts"
        if why:
            problems.append(f"{url}: {why}")
    assert not problems, "\n".join(problems)
    # Each rule quoted comes with the page it is quoted from.
    for page in ("https://news.ycombinator.com/showhn.html", "https://dev.to/t/showdev", "https://lobste.rs/about",
                 "https://pycoders.com/submissions", "https://docs.pypi.org/trusted-publishers/"):
        assert page in urls, page

    # Links within this repository, and to the drafts' own headings.
    own = md_anchors(text())
    for target in re.findall(r"\]\(([^)\s]+)\)", outside_blocks(text())):
        if target.startswith("http"):
            continue
        rel, _, anchor = target.partition("#")
        if not rel:
            assert anchor in own, target
            continue
        path = (DRAFTS.parent / rel).resolve()
        assert path.is_file(), target
        if anchor:
            assert anchor in md_anchors(path.read_text(encoding="utf-8")), target


def test_no_action_version_is_unsourced():
    known = repo_uses()
    lines = [flat(m.group(1)) for m in re.finditer(r"^\s*(?:-\s*)?uses:\s*(.+?)\s*$", drafted_workflow(), re.M)]
    assert len(lines) == 5
    for line in lines:
        assert line in known or PYPA_NOTE.search(line), line
        if line not in known:
            # Not pinned to a commit nobody looked up: the commit is filled in when Colin approves.
            assert re.search(r"@<commit of [^>]+> # ", line), line
    # Every commit the drafts name is one this repository's workflows already pin.
    pinned = {c for value in known for c in re.findall(r"\b[0-9a-f]{40}\b", value)}
    assert set(re.findall(r"\b[0-9a-f]{40}\b", text())) <= pinned


def test_the_licence_sentence_says_or_later():
    assert f"> {LICENCE_SENTENCE}" in text()
    assert "AGPL-3.0-or-later" in LICENCE_SENTENCE
    # It waits for Colin: CONTRIBUTING.md's Licence section isn't changed.
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert LICENCE_SENTENCE not in contributing
    assert "Clips Kitty is **AGPL-3.0**, and contributions are accepted under the same" in contributing
    # What "or-later" rests on.
    assert "or (at your\noption) any later version" in (ROOT / "NOTICE").read_text(encoding="utf-8")
    # The MIT examples it names are MIT, and the one it says isn't, isn't.
    for name, licence in (("scene-cut-highlights", "MIT"), ("keyword-rater", "MIT"),
                          ("transcript-highlights", "AGPL-3.0-or-later")):
        manifest = yaml.safe_load((ROOT / "examples" / "pipelines" / name / "clipskitty.yaml").read_text(encoding="utf-8"))
        assert manifest["license"] == licence, name
        assert f"`examples/pipelines/{name}`" in section("The CONTRIBUTING licence sentence")


MODEL_FAMILY = re.compile(r"\b(?:gemma|llama|llava|qwen|mistral|mixtral|deepseek|moondream|minicpm|granite|"
                          r"whisper|gemini|claude|gpt|phi)(?=[\d.:-]|\b)", re.I)
MODEL_TAG = re.compile(r"\b[a-z][\w.-]*:(?:e?\d+(?:\.\d+)?[bkm]|latest)\b", re.I)


def test_it_names_no_model_identifier():
    found = MODEL_FAMILY.findall(text()) + MODEL_TAG.findall(text())
    assert not found, found
    # The patterns do catch one.
    assert MODEL_FAMILY.search("ask llama3.2 about it") and MODEL_TAG.search("pull some-model:7b")


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " " + re.sub(r"[^a-z0-9]+", " ", value).strip() + " "


def real_games() -> set[str]:
    """Names and short names of real games: the catalog's game sections and the search's aliases."""
    sections = yaml.safe_load((ROOT / "awesome-clips-kitty" / "registry" / "sections.yaml").read_text(encoding="utf-8"))
    names = set()

    def walk(node):
        if isinstance(node, dict):
            sid = node.get("id")
            if isinstance(sid, str) and sid.startswith("gaming/") and sid != "gaming/generic":
                names.add(sid.split("/", 1)[1])
                names.add(str(node.get("title") or ""))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(sections)
    for short, long in registry.ALIASES.items():
        if short not in {"football", "soccer", "yt"}:
            names |= {short, long}
    return {_norm(n).strip() for n in names if n.strip()}


def test_it_names_no_real_game():
    games = real_games()
    assert {"marvel rivals", "world of warcraft", "wow", "minecraft"} <= games
    found = [f"line {n}: {game}" for n, line in enumerate(text().splitlines(), 1)
             for game in sorted(games) if f" {game} " in _norm(line)]
    assert not found, "\n".join(found)
    assert "Quarkbloom Arena (a made-up game)" in dev_post()
    assert "Quarkbloom Arena, a made-up game" in section("Show HN")


CLAIMS = (
    re.compile(r"\bplugins?\b[^.;]{0,40}\b(?:can't|cannot|can not|isn't able to|is not able to|is unable to)\b"
               r"[^.;]{0,40}\b(?:post|publish|install|remove|uninstall)", re.I),
    re.compile(r"\b(?:is|are) (?:fully |completely )?sandboxed\b", re.I),
    re.compile(r"\bruns? in(?:side)? a sandbox\b", re.I),
    re.compile(r"\bsandboxed (?:plugin|process)", re.I),
    re.compile(r"\bposts? only when the creator clicks\b", re.I),
    re.compile(r"\bplugins? (?:work|run)s? (?:today|now)\b", re.I),
    re.compile(r"\bnext release\b", re.I),
)
NO_SANDBOX = ("Every plugin runs on the creator's PC with their rights, like any program; Clips Kitty doesn't "
              "sandbox it. The install screen says what it declares.")


def test_it_claims_no_sandbox_and_says_which_release_runs_plugins():
    found = [m.group(0) for claim in CLAIMS for m in claim.finditer(flat(text()))]
    assert not found, found
    sentence = release_sentence()
    for where in (flat(section("Before any of it")), flat(section("Show HN")), flat(dev_post())):
        assert sentence in where
    for where in (flat(section("Show HN")), flat(dev_post())):
        assert NO_SANDBOX in where
        assert "edit and export are coming later" in where.lower()


def test_the_picture_is_the_sdks():
    readme = (SDK / "README.md").read_text(encoding="utf-8")
    picture = [body for lang, body in fenced(readme) if lang == "text" and body.startswith("Clips Kitty SDK\n")]
    assert len(picture) == 1
    drawn = [body for lang, body in fenced(dev_post()) if lang == "text" and body.startswith("Clips Kitty SDK\n")]
    assert drawn == picture


def test_the_commands_and_output_are_the_tutorials():
    """Each command the DEV post gives is a line the tutorial runs (tests/test_plugin_docs.py runs it), in
    the same shell, and the output it shows is the tutorial's."""
    tutorial = fenced(TUTORIAL.read_text(encoding="utf-8"))
    shown = fenced(dev_post())
    for shell in ("powershell", "bash"):
        theirs = {line for lang, body in tutorial if lang == shell for line in body.splitlines()}
        ours = [line for lang, body in shown if lang == shell for line in body.splitlines() if line.strip()]
        assert len(ours) == 3, shell
        assert not [line for line in ours if line not in theirs], shell
    outputs = [body for lang, body in shown if lang == "text" and not body.startswith("Clips Kitty SDK\n")]
    assert len(outputs) == 1
    assert any(outputs[0] in body for lang, body in tutorial if lang == "text")


def test_the_template_readme_is_what_new_writes(tmp_path):
    folder = tmp_path / "quarkbloom-bursts"
    scaffold.make(folder, "game-events", publisher="your-github-name")
    written = (folder / "README.md").read_text(encoding="utf-8")
    drafted = [body for lang, body in fenced(section("The template repository")) if lang == "markdown"]
    assert len(drafted) == 2  # the note for the top, then the README
    assert drafted[1] == written
    assert "Quarkbloom Arena (a made-up game)" in drafted[0] and "Quarkbloom Arena (a made-up game)" in written
    # No badge in it until Colin approves one.
    assert "img.shields.io" not in drafted[0] + written
