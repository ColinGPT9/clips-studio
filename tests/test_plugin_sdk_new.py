"""`python -m clipskitty_sdk new` and its six templates (clipskitty_sdk.scaffold).

A template must give a plugin that Clips Kitty accepts, that runs on the
SDK's sample as designed, that the lint has nothing to say about, and whose
own tests and workflow pass, without a real game or a badge in it.

Like every tests/test_plugin_sdk_*.py file, this needs only pytest and PyYAML
and imports nothing from Clips Kitty's engine, so it also runs in CI's SDK
(Windows) job. The runs on the sample video skip without FFmpeg; the
transcript, rater and editor templates need none, and one test clears PATH of
it.
"""

import ast
import builtins
import datetime
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import __main__ as cli  # noqa: E402
from clipskitty_sdk import devrun, samples, scaffold  # noqa: E402
from clipskitty_sdk.host import APP_PYTHON  # noqa: E402
from clipskitty_sdk.lint import NOT_BUNDLED, lint_folder  # noqa: E402
from clipskitty_sdk.manifest import offers, validate_folder, version_satisfies  # noqa: E402

REPO = "https://github.com/ColinGPT9/clips-studio"
SCHEMA_URL = ("https://raw.githubusercontent.com/ColinGPT9/clips-studio/main/sdk/python/clipskitty_sdk/schema/"
              "clipskitty.schema.json")
FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
# The templates that read the video, so their runs on the sample need FFmpeg.
READS_THE_VIDEO = {"blank", "game-events", "understander"}

LIST = """\
blank          finds nothing yet: a start for your own checks
transcript     finds moments where your words are said
game-events    finds moments when a coloured banner shows and the sound gets louder
rater          rates moments others found, by the words said in them
understander   notes what happens in moments others found, from the screen and the words
editor         suggests cuts, mutes and a hook title for clips others found
"""

# What `run --sample` prints for each template, after its "job folder:" line.
ON_THE_SAMPLE = {
    "blank": [
        "  log: The video is 640x360 at 25 frames a second",
        "0 moment(s), as Clips Kitty would take them:",
        "notes: This is the blank template: add your own checks in src/main.py.",
    ],
    "transcript": [
        "1 moment(s), as Clips Kitty would take them:",
        "      13.5s      32.0s  score   -  words_said",
        '       why: the commentary says "quark burst"',
        "notes: The words are said in 1 place(s).",
    ],
    "game-events": [
        "   10%  Looking for the banner",
        "   70%  Listening for the sound getting louder",
        "1 moment(s), as Clips Kitty would take them:",
        "      16.0s      29.8s  score   -  quark_burst",
        "       why: the banner shows from 22 to 26.75 s, and the sound gets 25 dB louder",
        "notes: The banner shows 1 time(s), 1 of them as the sound gets louder.",
    ],
    "rater": [
        "Rate: 2 of 5 moment(s) answered, as Clips Kitty would use them:",
        "m1   6.7s-13.3s  score 60",
        "m2   13.3s-20.0s  score 60",
        'm3   20.0s-26.7s  score 60 -> 75  the commentary says "quark burst"',
        "m4   26.7s-33.3s  score 60",
        'm5   33.3s-40.0s  score 60 -> 10  the commentary says "waiting", which this rater marks as dull',
        "notes: Rated 2 of 5 moment(s) by the words said in them.",
    ],
    "understander": [
        "   10%  Looking for the banner",
        "   80%  Reading what is said",
        "Understand: 2 of 5 moment(s) answered, as Clips Kitty would use them:",
        "m1   6.7s-13.3s  score 60",
        '       note: The commentary says "round one" here',
        "m2   13.3s-20.0s  score 60",
        "m3   20.0s-26.7s  score 60",
        "       note: The quark burst banner shows from 22.0 s",
        "m4   26.7s-33.3s  score 60",
        "m5   33.3s-40.0s  score 60",
        "notes: Noted what happens in 2 of 5 moment(s).",
    ],
    "editor": [
        "Suggest edits: 1 of 5 clip(s) given a suggestion, as Clips Kitty would keep them:",
        "m1   6.7s-13.3s  no suggestion",
        "m2   13.3s-20.0s  no suggestion",
        'm3   20.0s-26.7s  6.7 s  hook title "Quark burst!" (3 s)',
        '     Adds a hook title where the commentary says "quark burst"',
        "m4   26.7s-33.3s  no suggestion",
        "m5   33.3s-40.0s  no suggestion",
        "notes: Suggested edits for 1 of 5 clip(s).",
    ],
}
# What each template can be chosen for (manifest.offers).
OFFERS = {"blank": ("find",), "transcript": ("find",), "game-events": ("find",), "rater": ("rate",),
          "understander": ("understand",), "editor": ("edit",)}
GITIGNORE = ["__pycache__/", ".venv/", "venv/", ".clipskitty/", "node_modules/", "*.mp4", "*.mkv", "*.mov",
             "frame*.png"]


def _cli(*args) -> int:
    return cli.main([str(a) for a in args])


def _new(where: Path, template: str, folder: str | None = None, *options) -> Path:
    out = where / (folder or f"quarkbloom-{template}")
    assert _cli("new", out, "--template", template, *options) == 0
    return out


def _files(folder: Path) -> dict[str, str]:
    return {p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8") for p in sorted(folder.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts}


def _template_sources() -> dict[str, str]:
    return {p.relative_to(scaffold.TEMPLATES_DIR).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(scaffold.TEMPLATES_DIR.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def _manifest(folder: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((folder / "clipskitty.yaml").read_text(encoding="utf-8"))


def _python_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*.py") if "__pycache__" not in p.parts)


def _no_ffmpeg(tmp_path, monkeypatch) -> Path:
    """PATH cleared of FFmpeg (and of everything else)."""
    empty = tmp_path / "empty-path"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))
    assert shutil.which("ffmpeg") is None and shutil.which("ffprobe") is None
    return empty


def _shown_after_the_job_folder(out: str) -> list[str]:
    lines = out.splitlines()
    assert lines[0].startswith("job folder: "), out
    return lines[1:]


def _pytest(folder: Path, tmp_path: Path, env: dict) -> subprocess.CompletedProcess:
    """`python -m pytest -q` in a plugin's folder, as its developer runs it,
    leaving no cache or bytecode there."""
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "--basetemp", str(tmp_path / f"basetemp-{folder.name}")],
                          cwd=folder, env={**env, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True,
                          timeout=600)


def _sdk_env(**changes) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLIPSKITTY_", "PYTHON", "PYTEST_"))}
    return {**env, "PYTHONPATH": str(SDK), **changes}


@pytest.fixture(scope="module")
def made(tmp_path_factory) -> dict[str, Path]:
    """One plugin made from each template, with the defaults."""
    pytest.importorskip("yaml")
    where = tmp_path_factory.mktemp("made")
    return {template: _new(where, template) for template in scaffold.TEMPLATES}


# ---- the list ---------------------------------------------------------------------------


def test_list_names_six_templates(capsys):
    assert _cli("new", "--list") == 0
    assert capsys.readouterr().out == LIST
    assert list(scaffold.TEMPLATES) == ["blank", "transcript", "game-events", "rater", "understander", "editor"]
    folders = sorted(p.name for p in scaffold.TEMPLATES_DIR.iterdir() if p.is_dir() and p.name != "__pycache__")
    assert folders == sorted([*scaffold.TEMPLATES, scaffold.SHARED])


# ---- rendering ---------------------------------------------------------------------------


def test_every_template_renders_with_no_token_left(made, tmp_path):
    for template, folder in made.items():
        for rel, text in _files(folder).items():
            assert "@@" not in text, f"{template}: {rel} keeps a placeholder"

    # Every placeholder in the templates is one `new` fills, and each is used.
    used = set()
    for rel, text in _template_sources().items():
        found = set(re.findall(r"@@([A-Z_]+)@@", text))
        assert found <= set(scaffold.TOKENS), f"{rel}: unknown placeholder {found - set(scaffold.TOKENS)}"
        assert "@@" not in re.sub(r"@@[A-Z_]+@@", "", text), f"{rel}: a stray @@"
        used |= found
    assert used == set(scaffold.TOKENS)

    # One pass of plain replacement: a value is never read again for placeholders.
    assert scaffold.render("@@NAME@@ is @@ID@@", {"NAME": "@@ID@@", "ID": "x/y"}) == "@@ID@@ is x/y"
    with pytest.raises(KeyError):
        scaffold.render("@@NOPE@@", {})

    # Free text goes into clipskitty.yaml escaped, and reads back as typed.
    name = 'Quark "Q" \\ Bursts: #1'
    folder = _new(tmp_path, "transcript", "quoted", "--name", name, "--author", "Ann O'Hare: \"AJ\"")
    assert _manifest(folder)["name"] == name
    assert _manifest(folder)["author"] == {"name": "Ann O'Hare: \"AJ\""}
    assert f"# {name}\n" in (folder / "README.md").read_text(encoding="utf-8")


def test_workflow_schema_and_powershell_dollars_survive(made):
    sources = _template_sources()
    for template, folder in made.items():
        files = _files(folder)
        manifest = files["clipskitty.yaml"]
        assert manifest.startswith(f"# yaml-language-server: $schema={SCHEMA_URL}\n")
        assert "runs-on: ${{ matrix.os }}" in files[".github/workflows/clipskitty-check.yml"]
        assert "name: Check (${{ matrix.os }})" in files[".github/workflows/clipskitty-check.yml"]
        if template in READS_THE_VIDEO:
            assert '"$env:USERPROFILE\\Videos\\match.mp4"' in files["README.md"]
        # Every $ in a template's files is still there, as it was.
        for rel, text in files.items():
            stored = next(s for s in (f"{template}/{rel}", f"{scaffold.SHARED}/{rel}",
                                      f"{scaffold.SHARED}/{rel.lstrip('.')}") if s in sources)
            assert text.count("$") == sources[stored].count("$"), f"{template}: {rel}"
    assert (SDK / "clipskitty_sdk" / "schema" / "clipskitty.schema.json").is_file()


def test_dotfiles_are_written_under_their_real_names(made):
    for template, folder in made.items():
        assert (folder / ".gitignore").is_file() and (folder / ".github" / "workflows" / "clipskitty-check.yml").is_file()
        assert not (folder / "gitignore").exists() and not (folder / "github").exists(), template
    # Stored without the dot, so they ship inside the package: setuptools leaves out names starting with one.
    assert (scaffold.TEMPLATES_DIR / scaffold.SHARED / "gitignore").is_file()
    assert (scaffold.TEMPLATES_DIR / scaffold.SHARED / "github" / "workflows" / "clipskitty-check.yml").is_file()
    assert list(scaffold.TEMPLATES_DIR.rglob(".*")) == []
    assert scaffold.written_name("github/workflows/clipskitty-check.yml") == ".github/workflows/clipskitty-check.yml"
    assert scaffold.written_name("src/github.py") == "src/github.py"


# ---- what the app and the lint say ----------------------------------------------------------


def test_every_template_validates_with_no_warnings_and_no_lint(made, capsys):
    for template, folder in made.items():
        data, report = validate_folder(folder)
        assert report.ok and not report.warnings, (template, report.errors, report.warnings)
        assert lint_folder(folder, data) == [], template
        assert data["id"] == f"{scaffold.DEFAULT_PUBLISHER}/quarkbloom-{template}"
        assert data["version"] == "0.1.0"
        assert data["requires"] == {"clips_kitty": scaffold.TEMPLATE_REQUIRES, "plugin_api": 1}
        assert offers(data) == OFFERS[template]
        assert _cli("validate", folder) == 0
        assert capsys.readouterr().out == f"{data['id']} 0.1.0: valid\n"
    assert _manifest(made["game-events"])["games"] == [scaffold.DEFAULT_GAME]


def _imports(path: Path) -> list[tuple[str, int, bool]]:
    """(module, line, inside a function or class) for each import in a file."""
    found = []

    def visit(node, inside):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                found.extend((alias.name, child.lineno, inside) for alias in child.names)
            elif isinstance(child, ast.ImportFrom):
                assert child.level == 0, f"{path}: a relative import"
                found.append((child.module, child.lineno, inside))
            visit(child, inside or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))

    visit(ast.parse(path.read_text(encoding="utf-8")), False)
    return found


def test_every_template_imports_only_what_clips_kitty_promises(made):
    promised = (set(sys.stdlib_module_names) - NOT_BUNDLED) | {"clipskitty_sdk"}
    for template, folder in made.items():
        for path in _python_files(folder / "src"):
            for module, line, _ in _imports(path):
                assert module.split(".")[0] in promised, f"{template}: {path.name} line {line} imports {module}"
        # Its tests run only on the developer's PC, with the SDK's test extra.
        for path in _python_files(folder / "tests"):
            for module, line, _ in _imports(path):
                assert module.split(".")[0] in promised | {"pytest"}, f"{template}: tests line {line} {module}"


def test_every_template_imports_the_sdk_at_module_level(made):
    for template, folder in made.items():
        main = folder / "src" / "main.py"
        imports = _imports(main)
        assert [i for i in imports if i[2]] == [], f"{template}: an import inside a function"
        assert any(module.split(".")[0] == "clipskitty_sdk" for module, _, _ in imports), template
        tree = ast.parse(main.read_text(encoding="utf-8"))
        last = tree.body[-1]
        assert ast.unparse(last) == "if __name__ == '__main__':\n    run(main)", template


def test_every_template_opens_text_files_as_utf8(made):
    for template, folder in made.items():
        for path in _python_files(folder):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if name in ("open", "read_text", "write_text"):
                    keywords = {k.arg for k in node.keywords}
                    mode = ast.unparse(node.args[1]) if name == "open" and len(node.args) > 1 else ""
                    assert "encoding" in keywords or "b" in mode, f"{template}: {path.name} line {node.lineno}"
        assert not [w for w in lint_folder(folder, _manifest(folder)) if "encoding" in w]
        # Every file is written as UTF-8 with \n line ends, as the templates are stored.
        for path in folder.rglob("*"):
            if path.is_file():
                assert b"\r\n" not in path.read_bytes(), path


# ---- running them -------------------------------------------------------------------------


@pytest.mark.parametrize("template", list(scaffold.TEMPLATES))
def test_every_template_runs_on_the_sample(template, made, tmp_path, capsys):
    if template in READS_THE_VIDEO and not FFMPEG:
        pytest.skip("needs FFmpeg and FFprobe")
    assert _cli("run", made[template], "--sample", "--job-dir", tmp_path / "job") == 0
    captured = capsys.readouterr()
    assert _shown_after_the_job_folder(captured.out) == ON_THE_SAMPLE[template]
    # Without FFmpeg, the transcript, rater and editor templates get the sample's transcript and length alone.
    note = samples.SAMPLE_NOTE if shutil.which("ffmpeg") else devrun.SAMPLE_WITHOUT_VIDEO
    assert captured.err.splitlines()[0] == f"note: --sample: {note}"
    assert [line for line in captured.err.splitlines() if line.startswith("warning:")] == []


def test_transcript_rater_and_editor_templates_run_without_ffmpeg(made, tmp_path, monkeypatch, capsys):
    empty = _no_ffmpeg(tmp_path, monkeypatch)
    for template in ("transcript", "rater", "editor"):
        assert _cli("run", made[template], "--sample", "--job-dir", tmp_path / template) == 0
        captured = capsys.readouterr()
        assert _shown_after_the_job_folder(captured.out) == ON_THE_SAMPLE[template]
        assert captured.err.splitlines()[0] == f"note: --sample: {devrun.SAMPLE_WITHOUT_VIDEO}"
        assert "warning:" not in captured.err

        copy = shutil.copytree(made[template], tmp_path / "copies" / template)
        done = _pytest(copy, tmp_path, _sdk_env(PATH=str(empty)))
        assert done.returncode == 0, done.stdout + done.stderr
        assert "skipped" not in done.stdout and " passed" in done.stdout, done.stdout


def test_the_editor_mutes_the_words_its_readme_sets_on_the_sample(made, tmp_path, capsys):
    readme = (made["editor"] / "README.md").read_text(encoding="utf-8")
    assert 'python -m clipskitty_sdk run . --sample --set "mute_words=round one"' in readme
    assert _cli("run", made["editor"], "--sample", "--set", "mute_words=round one", "--job-dir", tmp_path / "job") == 0
    shown = _shown_after_the_job_folder(capsys.readouterr().out)
    # "round one" is said from 8.0 to 9.6 s, in the first clip; the hook title stays on the third.
    assert shown == [
        "Suggest edits: 2 of 5 clip(s) given a suggestion, as Clips Kitty would keep them:",
        "m1   6.7s-13.3s  6.7 s  mute 7.9-9.7",
        "     Mutes the words you listed",
        *ON_THE_SAMPLE["editor"][2:-1],
        "notes: Suggested edits for 2 of 5 clip(s).",
    ]


def test_generated_tests_pass(made, tmp_path):
    for template, folder in made.items():
        copy = shutil.copytree(folder, tmp_path / "copies" / template)
        done = _pytest(copy, tmp_path, _sdk_env())
        assert done.returncode == 0, f"{template}: {done.stdout}{done.stderr}"
        summary = done.stdout.strip().splitlines()[-1]
        assert " passed" in summary or template in READS_THE_VIDEO, summary
        if FFMPEG:
            assert "skipped" not in summary and "failed" not in summary, f"{template}: {summary}"
        assert not (copy / ".pytest_cache").exists()


def test_the_workflow_uses_clips_kittys_python_and_the_test_extra(made):
    yaml = pytest.importorskip("yaml")
    ours = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    text = (made["transcript"] / ".github" / "workflows" / "clipskitty-check.yml").read_text(encoding="utf-8")
    for template, folder in made.items():  # one workflow for every template
        assert (folder / ".github" / "workflows" / "clipskitty-check.yml").read_text(encoding="utf-8") == text
    flow = yaml.safe_load(text)
    assert set(flow.get("on", flow.get(True))) == {"push", "pull_request", "workflow_dispatch"}
    assert flow["permissions"] == {"contents": "read"}
    job = flow["jobs"]["check"]
    assert job["runs-on"] == "${{ matrix.os }}"
    assert job["strategy"]["matrix"]["os"] == ["ubuntu-latest", "windows-latest"]
    steps = job["steps"]

    # The same action versions as Clips Kitty's own CI, and Clips Kitty's Python.
    uses = [s["uses"] for s in steps if "uses" in s]
    assert uses == ["actions/checkout@v7", "actions/setup-python@v7.0.0"]
    assert all(f"uses: {u}" in ours for u in uses)
    setup = next(s for s in steps if s.get("uses", "").startswith("actions/setup-python"))
    assert setup["with"]["python-version"] == ".".join(map(str, APP_PYTHON)) == "3.11"

    runs = {s["name"]: s for s in steps if "run" in s}
    install = next(s["run"] for s in runs.values() if "pip install" in s["run"])
    assert install == ('python -m pip install "clipskitty-sdk[yaml,test] @ '
                       f'git+{REPO}#subdirectory=sdk/python"')
    assert (ROOT / "sdk" / "python" / "pyproject.toml").is_file()
    for step in runs.values():
        linux_only = step.get("if") == "runner.os == 'Linux'"
        if "ffmpeg" in step["run"] or "--sample" in step["run"]:
            assert linux_only, step
        else:
            assert "if" not in step, step
            assert "&&" not in step["run"], step  # it runs in PowerShell on Windows
    assert [s["run"] for s in runs.values() if "if" not in s and "pip" not in s["run"]] == [
        "python -m clipskitty_sdk validate .", "python -m pytest -q"]


# ---- the command --------------------------------------------------------------------------


def test_new_prints_the_python_that_is_running(tmp_path, capsys, monkeypatch):
    pytest.importorskip("yaml")
    shown = tmp_path / "quarkbloom-bursts"
    assert _cli("new", shown, "--template", "transcript") == 0
    assert capsys.readouterr().out.splitlines() == [
        f"Made {shown} from the transcript template.",
        f"Next: python -m clipskitty_sdk run {shown} --sample",
        "Then change clipskitty.yaml, src/main.py and README.md for your game:",
        scaffold.GUIDE,
    ]
    assert (ROOT / scaffold.GUIDE.removeprefix(f"{REPO}/blob/main/")).is_file()

    # On Windows, when `python` on PATH isn't this Python, the hint is `py`; a folder with a space is quoted.
    monkeypatch.setattr(sys, "platform", "win32")
    other = str(Path(sys.executable).parent / "another" / "python.exe")
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: other if name == "python" else None)
    spaced = tmp_path / "quark bursts"
    assert _cli("new", spaced, "--template", "rater") == 0
    assert f'Next: py -m clipskitty_sdk run "{spaced}" --sample' in capsys.readouterr().out.splitlines()


def test_new_refuses_a_full_folder_and_the_reserved_publisher(tmp_path, capsys):
    pytest.importorskip("yaml")
    full = tmp_path / "full"
    full.mkdir()
    (full / "notes.txt").write_text("mine", encoding="utf-8")
    refusals = [
        (["new", full, "--template", "blank"], f"error: {full} isn't empty; new only writes into a new or empty folder"),
        (["new", full / "notes.txt", "--template", "blank"],
         f"error: {full / 'notes.txt'} isn't empty; new only writes into a new or empty folder"),
        (["new", tmp_path / "a", "--template", "blank", "--publisher", "clipskitty"],
         "error: the publisher clipskitty is reserved for plugins that ship with Clips Kitty"),
        (["new", tmp_path / "b", "--template", "blank", "--publisher", "Your Name"],
         "error: --publisher must be your GitHub name in lower case: letters, digits and hyphens"),
        (["new", tmp_path / "c", "--template", "highlights"],
         ("error: --template: there is no template called 'highlights'; choose one of blank, transcript, "
          "game-events, rater, understander, editor (new --list says what each does)")),
        (["new", tmp_path / "d", "--template", "blank", "--name", "Q" * 61],
         "error: Clips Kitty would refuse this plugin, so new wrote nothing: name: is longer than 60 characters"),
        (["new", tmp_path / "e", "--template", "blank", "--license", "my own licence"],
         ("error: Clips Kitty would refuse this plugin, so new wrote nothing: license: must be an SPDX licence "
          "identifier such as MIT or Apache-2.0")),
        (["new", tmp_path / "f", "--template", "game-events", "--game", "Quarkbloom Arena"],
         ("error: --game must be the game's short name in lower case: letters, digits and hyphens, such as "
          "quarkbloom-arena")),
        (["new", tmp_path / "!!!", "--template", "blank"],
         ("error: the folder's name, '!!!', gives the plugin no id: name the folder with letters, digits and "
          "hyphens, such as quarkbloom-bursts")),
        (["new", "--template", "blank"],
         ("error: new needs a folder and a template: python -m clipskitty_sdk new FOLDER --template NAME "
          "(templates: blank, transcript, game-events, rater, understander, editor; new --list says what each "
          "does)")),
    ]
    for args, message in refusals:
        assert _cli(*args) == 2, args
        captured = capsys.readouterr()
        assert captured.err == message + "\n" and captured.out == "", args
    assert [p.name for p in full.iterdir()] == ["notes.txt"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["full"]  # nothing else was written

    # An empty folder is fine.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _cli("new", empty, "--template", "blank") == 0
    assert (empty / "clipskitty.yaml").is_file()


def test_new_asks_only_on_a_terminal(tmp_path, capsys, monkeypatch):
    pytest.importorskip("yaml")
    asked = []
    answers = []

    def answer(question=""):
        asked.append(question)
        return answers.pop(0)

    monkeypatch.setattr(builtins, "input", answer)
    monkeypatch.setattr(cli, "_is_terminal", lambda stream: True)

    answers[:] = ["quark-dev", "Quarkbloom Bursts!"]
    assert _cli("new", tmp_path / "one", "--template", "transcript") == 0
    assert asked == [scaffold.ASK_PUBLISHER, scaffold.ASK_NAME]
    data = _manifest(tmp_path / "one")
    assert data["id"] == "quark-dev/one" and data["name"] == "Quarkbloom Bursts!"
    assert data["author"] == {"name": "quark-dev"}

    asked.clear()
    answers[:] = [""]  # Enter: the name comes from the folder's
    assert _cli("new", tmp_path / "quarkbloom-bursts", "--template", "rater", "--publisher", "quark-dev") == 0
    assert asked == [scaffold.ASK_NAME]
    assert _manifest(tmp_path / "quarkbloom-bursts")["name"] == "Quarkbloom Bursts"

    asked.clear()
    assert _cli("new", tmp_path / "three", "--template", "blank", "--publisher", "quark-dev", "--name", "Three") == 0
    assert asked == []

    answers[:] = ["Quark Dev"]
    assert _cli("new", tmp_path / "four", "--template", "blank") == 2
    assert capsys.readouterr().err == ("error: --publisher must be your GitHub name in lower case: letters, digits "
                                       "and hyphens\n")
    assert not (tmp_path / "four").exists()

    # Not a terminal: nothing is asked, and the defaults are used.
    monkeypatch.setattr(cli, "_is_terminal", lambda stream: False)
    asked.clear()
    assert _cli("new", tmp_path / "quiet", "--template", "blank") == 0
    assert asked == []
    data = _manifest(tmp_path / "quiet")
    assert data["id"] == "your-github-name/quiet" and data["name"] == "Quiet"


def test_template_licence_and_gitignore_are_written(tmp_path, capsys):
    pytest.importorskip("yaml")
    sdk_licence = (SDK / "LICENSE").read_text(encoding="utf-8")
    year = datetime.date.today().year
    mit = _new(tmp_path, "game-events", "mit", "--publisher", "quark-dev", "--author", "Quark Dev")
    assert "note:" not in capsys.readouterr().err
    licence = (mit / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith(f"MIT License\n\nCopyright (c) {year} Quark Dev\n\nPermission is hereby granted")
    assert licence.split("\n", 3)[3] == sdk_licence.split("\n", 3)[3]  # the same MIT text
    assert _manifest(mit)["license"] == "MIT" and _manifest(mit)["author"] == {"name": "Quark Dev"}
    template_licence = (mit / "TEMPLATE-LICENSE.txt").read_text(encoding="utf-8")
    assert sdk_licence in template_licence and "the game-events template of the Clips Kitty SDK" in template_licence

    other = _new(tmp_path, "rater", "apache", "--license", "Apache-2.0")
    assert capsys.readouterr().err == "note: add the text of Apache-2.0 as LICENSE\n"
    assert not (other / "LICENSE").exists() and (other / "TEMPLATE-LICENSE.txt").is_file()
    assert _manifest(other)["license"] == "Apache-2.0"
    assert _manifest(other)["author"] == {"name": scaffold.DEFAULT_PUBLISHER}

    lines = (mit / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert set(GITIGNORE) <= set(lines)


def test_no_template_carries_a_badge_or_a_real_game():
    yaml = pytest.importorskip("yaml")
    sections = yaml.safe_load((ROOT / "awesome-clips-kitty" / "registry" / "sections.yaml").read_text(encoding="utf-8"))
    games = [s for s in sections["pipeline"]["sections"] if s["id"].startswith("gaming/") and s["id"] != "gaming/generic"]
    assert games, "sections.yaml lists no games to check against"
    real = {s["title"].lower() for s in games} | {s["id"].split("/", 1)[1] for s in games}
    for rel, text in _template_sources().items():
        lower = text.lower()
        assert not [name for name in real if name in lower], f"{rel} names a real game"
        assert "shields.io" not in lower and "badge" not in lower and "![" not in text and "<img" not in lower, rel
        assert "built for clips kitty" not in lower, rel
    for template in scaffold.TEMPLATES:
        readme = (scaffold.TEMPLATES_DIR / template / "README.md").read_text(encoding="utf-8")
        assert "Quarkbloom Arena (a made-up game): replace it with yours" in readme, template
    assert scaffold.DEFAULT_GAME == "quarkbloom-arena" and scaffold.DEFAULT_PUBLISHER == "your-github-name"


def test_template_links_name_pages_in_this_checkout():
    problems = []
    for rel, text in _template_sources().items():
        for link in re.findall(r"https?://[^\s)\"'>`]+", text):
            match = re.fullmatch(rf"{re.escape(REPO)}/blob/main/([^#]+)(?:#(.+))?", link)
            if match:
                path, anchor = match.groups()
                if not (ROOT / path).is_file():
                    problems.append(f"{rel}: {link} names no file in this checkout")
                elif anchor and f"## {anchor.replace('-', ' ')}" not in (ROOT / path).read_text(
                        encoding="utf-8").lower():
                    problems.append(f"{rel}: {link} names no heading")
            elif link not in (f"{REPO}#subdirectory=sdk/python", SCHEMA_URL):
                problems.append(f"{rel}: {link} is a link this test doesn't know")
    assert problems == []


def test_template_floor_was_reviewed_for_the_latest_release(made):
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = re.search(r"^## (\d+\.\d+\.\d+)\b", changelog, re.M).group(1)
    assert newest == scaffold.FLOOR_REVIEWED_AT, (
        "A Clips Kitty release was added to CHANGELOG.md: check it bundles every SDK module the templates "
        "import, then set FLOOR_REVIEWED_AT (and raise TEMPLATE_REQUIRES if it doesn't)"
    )
    assert version_satisfies(scaffold.FLOOR_REVIEWED_AT, scaffold.TEMPLATE_REQUIRES)
    for template, folder in made.items():
        assert _manifest(folder)["requires"]["clips_kitty"] == scaffold.TEMPLATE_REQUIRES, template
