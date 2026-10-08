"""The developer docs say what the code does: "Your first game pipeline" and
the "Signals cookbook" run as written, on this checkout's SDK, and no page
shows a real game as our example, says a plugin can't post or is sandboxed,
or gives PowerShell a command it would run differently.

The tutorial's install lines are never run (they would fetch the SDK from
GitHub's main branch, not this checkout): every other command runs with
PYTHONPATH set to this checkout's sdk/python. The runs on the sample video
skip without FFmpeg. This file reads the names of real games from Clips
Kitty's own registry, so it is an engine test, not one of the SDK's own
(tests/test_plugin_sdk_*.py) that CI also runs on Windows.
"""

import ast
import http.server
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
for path in (str(ROOT), str(SDK)):
    if path not in sys.path:
        sys.path.insert(0, path)

from clipskitty_sdk import read_job, scaffold, testing  # noqa: E402
from clipskitty_sdk.lint import lint_folder  # noqa: E402
from clipskitty_sdk.manifest import load, validate_folder  # noqa: E402

from plugins import registry  # noqa: E402

DEV_DOCS = ROOT / "docs" / "developers"
TUTORIAL = DEV_DOCS / "first-game-pipeline.md"
COOKBOOK = DEV_DOCS / "signals-cookbook.md"
VERSIONING = DEV_DOCS / "versioning.md"
FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="needs FFmpeg and FFprobe")

# Every page a plugin developer reads, and the files a template writes.
PAGES = sorted([*DEV_DOCS.glob("*.md"), *SDK.rglob("*.md"), *(ROOT / "examples").rglob("*.md"),
                ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "docs" / "API.md",
                ROOT / "docs" / "EXTENDING.md"])
OUR_EXAMPLES = sorted(p for p in [*DEV_DOCS.glob("*.md"), *SDK.rglob("*"), *(ROOT / "examples").rglob("*")]
                      if p.is_file() and p.suffix in {".md", ".py", ".yaml", ".yml", ".txt", ".json"}
                      and "__pycache__" not in p.parts)

PICTURE = """\
Clips Kitty SDK

Input                      a link or a video file
  ↓
Video                      Clips Kitty downloads it and writes down what is said
  ↓
Your plugin                every step is optional
  ├── find                 picks the moments                  built
  ├── understand           says what happens in each one      built
  ├── rate                 scores each moment                 built
  ├── edit                 suggests cuts and framing          coming later
  └── export               posts to a platform                coming later
  ↓
Clips Kitty                does every step no plugin does, then cuts, frames and captions the clips
  ↓
Creator / Social Platform  posts when the creator clicks Publish, or on a schedule or automatic posting the creator switched on
"""
CAPTION = ("posts when the creator clicks Publish, or on a schedule or automatic posting the creator "
           "switched on")


# ---- reading the pages ----------------------------------------------------------------------

FENCE = re.compile(r"^(\s*)```\s*([\w-]*)\s*$")
SKIP = re.compile(r"^<!-- not run by tests/test_plugin_docs\.py: (.+?) -->$")


class Block:
    def __init__(self, lang: str, lines: list[str], line: int, skip: str | None, after_text: str):
        self.lang, self.lines, self.line, self.skip, self.after_text = lang, lines, line, skip, after_text

    @property
    def text(self) -> str:
        return "".join(f"{line}\n" for line in self.lines)


def blocks(path: Path) -> list[Block]:
    """The fenced blocks of a Markdown page, in order. A block's `skip` is the
    reason given by a `<!-- not run by tests/test_plugin_docs.py: ... -->`
    line before it, and `after_text` the prose between it and the block
    before it."""
    out: list[Block] = []
    lang = None
    body: list[str] = []
    start = 0
    skip = None
    between: list[str] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = FENCE.match(line)
        if lang is None:
            if m:
                lang, body, start, indent = m.group(2) or "text", [], n, len(m.group(1))
                continue
            s = SKIP.match(line.strip())
            if s:
                skip = s.group(1)
            elif line.strip():
                between.append(line.strip())
        elif m and not m.group(2):
            out.append(Block(lang, body, start, skip, " ".join(between)))
            lang, skip, between = None, None, []
        else:
            body.append(line[indent:] if line[:indent].strip() == "" else line)
    return out


def flat(text: str) -> str:
    return " ".join(text.split())


def release_sentence() -> str:
    """The one sentence that says which release runs plugins (versioning.md is its source)."""
    text = VERSIONING.read_text(encoding="utf-8")
    section = text.split("## Which release runs plugins", 1)[1]
    return flat(section.strip().split("\n\n", 1)[0])


def pip_line(line: str) -> bool:
    return bool(re.match(r"^\s*(pip|python -m pip|py -m pip)\b", line))


# ---- the tutorial ---------------------------------------------------------------------------


def _shim(folder: Path) -> Path:
    """A folder whose `python` is the Python running these tests, for the tutorial's bash blocks."""
    bin_dir = folder / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    return bin_dir


def _matches(expected: str, got: str) -> bool:
    """Whether output `got` is `expected`, line for line, where … stands for any text."""
    want, have = expected.rstrip("\n").split("\n"), got.rstrip("\n").split("\n")
    if len(want) != len(have):
        return False
    return all(re.fullmatch(".*".join(re.escape(part) for part in w.split("…")), h) for w, h in zip(want, have))


@needs_ffmpeg
def test_the_tutorial_runs_as_written(tmp_path):
    page = blocks(TUTORIAL)
    work = tmp_path / "tutorial"
    work.mkdir()
    env = {**os.environ, "PATH": f"{_shim(tmp_path)}{os.pathsep}{os.environ.get('PATH', '')}",
           "PYTHONPATH": str(SDK), "PYTHONUNBUFFERED": "1", "COLUMNS": "80"}
    for name in ("PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS", "PYTEST_XDIST_WORKER"):
        env.pop(name, None)

    ran, skipped, outputs = [], [], 0
    for i, block in enumerate(page):
        if block.lang != "bash":
            continue
        if block.skip:
            skipped.append(block.skip)
            continue
        commands = [line for line in block.lines if line.strip() and not pip_line(line)]
        for line in block.lines:
            if pip_line(line):  # not run: it would install main's SDK from GitHub, not this checkout's
                assert '#subdirectory=sdk/python"' in line and "[yaml,test]" in line, line
                assert (SDK / "pyproject.toml").is_file()
        if not commands:
            continue
        done = subprocess.run(["bash", "-e", "-c", "\n".join(commands)], cwd=work, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
        shown = page[i + 1] if i + 1 < len(page) else None
        assert done.returncode == 0, f"{TUTORIAL.name} line {block.line}:\n{done.stdout}"
        if shown is not None and shown.lang == "text" and not shown.after_text:
            outputs += 1
            assert _matches(shown.text, done.stdout), (
                f"{TUTORIAL.name} line {shown.line} shows:\n{shown.text}\nbut it printed:\n{done.stdout}")
        ran.append(" ".join(commands))

    joined = "\n".join(ran)
    for command in ("clipskitty_sdk new quarkbloom-bursts --template game-events", "run quarkbloom-bursts --sample",
                    "clipskitty_sdk frame sample.mp4", "--set louder_by_db=30", "python -m pytest -q"):
        assert command in joined, command
    assert outputs >= 5
    assert skipped and all(reason.startswith("it ") for reason in skipped)

    # What the page shows of the plugin is what `new` wrote.
    plugin = work / "quarkbloom-bursts"
    main_py = (plugin / "src" / "main.py").read_text(encoding="utf-8")
    manifest = (plugin / "clipskitty.yaml").read_text(encoding="utf-8")
    for block in page:
        if block.lang == "python":
            assert block.text in main_py, f"{TUTORIAL.name} line {block.line} isn't in src/main.py"
        elif block.lang == "yaml":
            assert block.text in manifest, f"{TUTORIAL.name} line {block.line} isn't in clipskitty.yaml"
    tree = next(b for b in page if b.lang == "text" and b.lines[0] == "quarkbloom-bursts/")
    listed = [line.split()[0] for line in tree.lines[1:]]
    assert sorted(listed) == sorted(p.relative_to(plugin).as_posix() for p in plugin.rglob("*")
                                    if p.is_file() and ".pytest_cache" not in p.parts
                                    and "__pycache__" not in p.parts)


def test_every_tutorial_command_is_shown_for_powershell_and_bash():
    page = blocks(TUTORIAL)
    shells = [b for b in page if b.lang in ("powershell", "bash")]
    assert [b.lang for b in shells] == ["powershell", "bash"] * (len(shells) // 2)
    for ps, sh in zip(shells[::2], shells[1::2]):
        assert len(ps.lines) == len(sh.lines), f"{TUTORIAL.name} line {ps.line}"
        for a, b in zip(ps.lines, sh.lines):
            first_a, first_b = a.split()[:2], b.split()[:2]
            if first_b == ["python", "-m"]:
                assert first_a == ["py", "-m"], a
                assert a.split()[2] == b.split()[2], a  # the same module
            else:
                assert first_a == first_b, a
    # The command `new` prints at the end points at this page.
    assert scaffold.GUIDE.endswith("/docs/developers/first-game-pipeline.md")


# ---- the cookbook ---------------------------------------------------------------------------

MANIFEST = """\
manifest_version: 1
id: your-github-name/{name}
name: Recipe {name}
version: 0.1.0
kind: pipeline
capability: highlight_detection
description: A recipe from the signals cookbook.
license: MIT
requires: {{clips_kitty: ">=2.0", plugin_api: 1}}
run:
  command: ["{{python}}", "src/main.py"]
execution: local
{roles}"""

# What each recipe gives on the sample video, as its "On the sample" line says.
RECIPES = {
    "A region's frames": {"notes": "The middle of the banner's part of the screen is red in 20 frames."},
    "A colour share": {"ranges": [(16.0, 29.75, "banner", "the banner shows from 22 to 26.75 s")]},
    "A brightness change": {"ranges": [(4.0, 14.0, "flash", "the screen's brightness jumps from 53 to 208"),
                                       (14.0, 24.0, "flash", "the screen's brightness jumps from 208 to 90"),
                                       (24.0, 34.0, "flash", "the screen's brightness jumps from 90 to 220")]},
    "An icon by colour at a fixed spot": {"ranges": [(16.0, 29.5, "icon", "the white icon shows from 22 to 26.5 s")]},
    "A loudness spike": {"ranges": [(16.0, 30.0, "loud", "loud from 22 to 27 s")]},
    "Scene cuts": {"ranges": [(20.0, 30.0, "loud", "loud from 22 s; the scene starts at 20 s")]},
    "Words said": {"ranges": [(19.5, 32.0, "words_said", 'the commentary says "quark burst"')]},
    "Asking the local model about a frame": {"context": "A red banner shows at the top of the screen."},
}


def recipes() -> dict[str, tuple[str, str, str]]:
    """Each recipe on the cookbook page: its manifest lines, its code and its text."""
    out = {}
    for part in re.split(r"(?m)^## ", COOKBOOK.read_text(encoding="utf-8"))[1:]:
        title, text = part.split("\n", 1)
        roles = re.findall(r"```yaml\n(.*?)```", text, re.S)
        code = re.findall(r"```python\n(.*?)```", text, re.S)
        assert len(roles) == 1 and len(code) == 1, title
        out[title] = (roles[0], code[0], text)
    return out


class _FakeModel(http.server.BaseHTTPRequestHandler):
    """A stand-in for Ollama on 127.0.0.1 that can see, and says one sentence."""
    asked: ClassVar[list] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeModel.asked.append((self.path, body))
        answer = ({"capabilities": ["completion", "vision"]} if self.path == "/api/show"
                  else {"response": RECIPES["Asking the local model about a frame"]["context"]})
        data = json.dumps(answer).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def test_the_cookbook_has_every_recipe_with_what_it_needs():
    found = recipes()
    assert list(found) == list(RECIPES)
    for title, (_roles, code, text) in found.items():
        for part in ("**Needs:** SDK 1.2.0", "**On the sample", "**Cost:**", "**How it fails:**"):
            assert part in text, (title, part)
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                assert node in tree.body, f"{title}: an import inside a function"
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
                assert all(n.split(".")[0] in sys.stdlib_module_names or n.split(".")[0] == "clipskitty_sdk"
                           for n in names), (title, names)
        assert code.rstrip().endswith('if __name__ == "__main__":\n    run(main)'), title
        for line in code.splitlines():
            assert "open(" not in line or "encoding=" in line, (title, line)


@pytest.mark.parametrize("title", list(RECIPES))
def test_the_cookbook_recipes_run(tmp_path, title):
    roles, code, _text = recipes()[title]
    plugin = tmp_path / "recipe"
    (plugin / "src").mkdir(parents=True)
    (plugin / "clipskitty.yaml").write_text(MANIFEST.format(name="recipe", roles=roles), encoding="utf-8")
    (plugin / "src" / "main.py").write_text(code, encoding="utf-8")
    manifest, report = validate_folder(plugin)
    assert report.ok and not report.warnings, (report.errors, report.warnings)
    assert lint_folder(plugin, manifest) == []

    reads_video = "video.read" in manifest["permissions"]
    if reads_video and not FFMPEG:
        pytest.skip("needs FFmpeg and FFprobe")
    video = testing.sample_video(tmp_path / "media") if reads_video else None
    folder = testing.make_job(tmp_path / "job", plugin, video=video, transcript=testing.sample_transcript(),
                              duration=testing.SAMPLE_VIDEO_SECONDS)

    server = None
    if "ollama" in manifest["permissions"]:
        _FakeModel.asked = []
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeModel)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        job_json = folder / "job.json"
        data = json.loads(job_json.read_text(encoding="utf-8"))
        data["tools"]["ollama"] = {"host": f"http://127.0.0.1:{server.server_address[1]}", "model": "test-model"}
        job_json.write_text(json.dumps(data), encoding="utf-8")
    try:
        namespace = {"__name__": "recipe"}
        exec(compile(code, str(plugin / "src" / "main.py"), "exec"), namespace)
        job = read_job(folder, out=io.StringIO())
        namespace["main"](job)
        if not job._finished:
            job.finish()
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()

    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    want = RECIPES[title]
    ranges = [(r["start"], r["end"], r.get("label"), r.get("reason")) for r in result.get("ranges", [])]
    assert ranges == want.get("ranges", [])
    if "notes" in want:
        assert result.get("notes") == want["notes"]
    if "context" in want:
        handed = json.loads((folder / "job.json").read_text(encoding="utf-8"))["moments"]
        assert result["moments"] == [{"id": m["id"], "context": [want["context"]]} for m in handed]
        generate = [body for path, body in _FakeModel.asked if path == "/api/generate"]
        assert len(generate) == len(handed) and all(body["model"] == "test-model" for body in generate)
        assert all(len(body["images"]) == 1 for body in generate)


def test_the_cookbook_says_what_each_recipe_found():
    for title, want in RECIPES.items():
        text = flat(recipes()[title][2])
        for start, end, _label, reason in want.get("ranges", []):
            assert f"{start:.1f} to {end:g}" in text or f"{start:.1f} to {end:.1f}" in text, (title, start, end)
            assert reason.split(" ")[0] in text, title
        if "notes" in want:
            assert want["notes"] in text, title


# ---- what no page says ------------------------------------------------------------------------


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return " " + re.sub(r"[^a-z0-9]+", " ", text).strip() + " "


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
    not_games = {"football", "soccer", "yt"}
    for short, long in registry.ALIASES.items():
        if short not in not_games:
            names |= {short, long}
    return {_norm(n).strip() for n in names if n.strip()}


# Lines that state catalog facts about real games, not examples of ours.
REAL_NAMES_ALLOWED = (
    "A small alias table maps common short names",
    "`tests/test_registry.py` checks that each of the searches in the platform brief",
)


def test_no_real_game_is_shown_as_our_example():
    games = real_games()
    assert {"marvel rivals", "world of warcraft", "wow", "minecraft"} <= games
    found = []
    for path in OUR_EXAMPLES:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(allowed in line for allowed in REAL_NAMES_ALLOWED):
                continue
            words = _norm(line)
            found += [f"{path.relative_to(ROOT)}:{n}: {game}" for game in sorted(games) if f" {game} " in words]
    assert not found, "\n".join(found)


def test_the_naming_example_is_found_by_its_searches():
    listing = {"id": "example-dev/quarkbloom-burst-highlights", "name": "Quarkbloom Arena Burst Highlights",
               "games": ["quarkbloom-arena"], "tags": ["ranked"], "category": "gaming"}
    text = flat((DEV_DOCS / "marketplace-publishing.md").read_text(encoding="utf-8"))
    example = ('"Quarkbloom Arena Burst Highlights" (for a made-up game) is found by "Quarkbloom", '
               '"Quarkbloom Arena bursts" and "burst"')
    assert example in text
    for query in ("Quarkbloom", "Quarkbloom Arena bursts", "burst"):
        assert registry.search([listing], query) == [listing], query


CLAIMS = (
    re.compile(r"\bplugins?\b[^.;]{0,40}\b(?:can't|cannot|can not|isn't able to|is not able to|is unable to)\b"
               r"[^.;]{0,40}\b(?:post|publish|install|remove|uninstall)", re.I),
    re.compile(r"\b(?:is|are) (?:fully |completely )?sandboxed\b", re.I),
    re.compile(r"\bruns? in(?:side)? a sandbox\b", re.I),
    re.compile(r"\bsandboxed (?:plugin|process)", re.I),
    re.compile(r"\bposts? only when the creator clicks\b", re.I),
)


def test_no_page_says_a_plugin_cannot_post_or_is_sandboxed():
    found = []
    for path in PAGES:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            found += [f"{path.relative_to(ROOT)}:{n}: {m.group(0)}" for claim in CLAIMS for m in claim.finditer(line)]
    assert not found, "\n".join(found)
    # The sentence that replaced the old claim, in local-apis.md.
    local = flat((DEV_DOCS / "local-apis.md").read_text(encoding="utf-8"))
    assert ("It keeps out web pages and scripts that don't know it, not programs running as you: any program "
            "running as the user can read the file that holds it, plugins included. A plugin must not install "
            "or remove plugins; Clips Kitty can't stop one that tries.") in local


# ---- PowerShell ------------------------------------------------------------------------------


def powershell_lines():
    for path in PAGES:
        for block in blocks(path):
            if block.lang in ("powershell", "pwsh", "ps1"):
                for k, line in enumerate(block.lines):
                    yield f"{path.relative_to(ROOT)}:{block.line + 1 + k}", line


def test_no_powershell_block_uses_double_ampersand():
    found = [f"{where}: {line}" for where, line in powershell_lines() if "&&" in line]
    assert not found, "\n".join(found)


def _outside_quotes(line: str) -> str:
    """The line without its quoted parts and without a comment."""
    bare = re.sub(r'"[^"]*"|\'[^\']*\'', "", line)
    return bare.split("#", 1)[0]


def test_no_powershell_block_has_an_unquoted_comma():
    found = [f"{where}: {line}" for where, line in powershell_lines() if "," in _outside_quotes(line)]
    assert not found, "PowerShell passes an unquoted a,b,c as separate arguments:\n" + "\n".join(found)


def test_powershell_blocks_use_py():
    found = [f"{where}: {line}" for where, line in powershell_lines()
             if re.match(r"^\s*(python3?|pip3?)\b", line)]
    assert not found, "PowerShell blocks run py -m, not python or pip:\n" + "\n".join(found)
    assert sum(1 for _ in powershell_lines()) > 20


def test_the_tutorial_and_cookbook_say_which_release_runs_plugins():
    sentence = release_sentence()
    assert sentence.startswith("No Clips Kitty release runs plugins yet.")
    said = sentence.removesuffix(".")  # a page may end it with a link in brackets
    for page in (TUTORIAL, COOKBOOK, DEV_DOCS / "getting-started.md", DEV_DOCS / "sdk.md", DEV_DOCS / "README.md",
                 SDK / "README.md", ROOT / "CONTRIBUTING.md"):
        assert said in flat(page.read_text(encoding="utf-8")), page.name


# ---- settings, the picture, links -------------------------------------------------------------


def _template_for(folder: str, page_text: str) -> str | None:
    m = re.search(rf"clipskitty_sdk new {re.escape(folder)} --template ([\w-]+)", page_text)
    return m.group(1) if m else None


def _settings_of(page: Path, folder: str, tmp_path: Path) -> dict:
    here = page.parent
    template = _template_for(folder, page.read_text(encoding="utf-8"))
    if folder == "." and here.parent == scaffold.TEMPLATES_DIR:
        template = here.name
    if template:
        made = tmp_path / f"{template}-{len(list(tmp_path.iterdir()))}"
        scaffold.make(made, template)
        return load(made).get("settings") or {}
    for base in (here, ROOT):
        if (base / folder / "clipskitty.yaml").is_file():
            return load(base / folder).get("settings") or {}
    raise AssertionError(f"{page.relative_to(ROOT)}: can't tell which plugin `run {folder}` runs")


def test_settings_in_doc_commands_exist(tmp_path):
    checked = 0
    for page in PAGES:
        for block in blocks(page):
            for line in block.lines:
                if "clipskitty_sdk run " not in line or "--set" not in line:
                    continue
                words = shlex.split(line.replace("\\", "/"))
                folder = words[words.index("run") + 1]
                settings = _settings_of(page, folder, tmp_path)
                for i, word in enumerate(words):
                    if word == "--set":
                        name = words[i + 1].split("=", 1)[0]
                        assert name in settings, f"{page.relative_to(ROOT)}: {folder} has no setting {name}"
                        checked += 1
    assert checked >= 5


def test_the_picture_caption_is_exact():
    pictures = []
    for page in PAGES:
        for block in blocks(page):
            if block.lines and block.lines[0] == "Clips Kitty SDK":
                assert block.text == PICTURE, f"{page.relative_to(ROOT)} line {block.line}"
                pictures.append(page)
    assert {SDK / "README.md", DEV_DOCS / "README.md"} <= set(pictures)
    for page in pictures:
        assert CAPTION in page.read_text(encoding="utf-8")


def _anchors(path: Path) -> set[str]:
    """GitHub's anchors for a Markdown page's headings."""
    out = set()
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            slug = re.sub(r"[^\w\- ]", "", m.group(1).strip().lower()).replace(" ", "-")
            n, base = 1, slug
            while slug in out:
                slug, n = f"{base}-{n}", n + 1
            out.add(slug)
    return out


def test_the_developer_pages_links_resolve():
    broken = []
    for page in sorted(DEV_DOCS.glob("*.md")):
        text = re.sub(r"```.*?```", "", page.read_text(encoding="utf-8"), flags=re.S)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if re.match(r"^[a-z]+:", target):
                continue
            file, _, anchor = target.partition("#")
            path = (page.parent / file).resolve() if file else page
            if not path.exists():
                broken.append(f"{page.name}: {target}")
            elif anchor and path.suffix == ".md" and anchor not in _anchors(path):
                broken.append(f"{page.name}: {target} (no such heading)")
    assert not broken, "\n".join(broken)
