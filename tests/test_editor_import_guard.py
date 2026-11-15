"""Plain auto-clipping never loads the new editor's project model.

core/pipeline.py is what every auto-clipping render runs through. The new
editor keeps its projects in video_editor/project/ (model, ops, store, the
import of AI clips and the projection back). An editor project can be
rendered without the pipeline knowing it exists, and the pipeline must stay
that way. Nothing core/pipeline.py loads may import video_editor.project or
anything under it, in any form, at the top of a file or inside a function.
That covers core/pipeline.py itself and every repo module it reaches through
its own imports (video_editor.timeline, video_editor.export and so on).

The self-tests prove it catches every way of writing that import, including
one reached through another module.

The package exists since editor suite step 5, and has rules of its own, kept
at the end of this file: it is standard library only (what it asks the rest
of the app it asks inside a function, in env.py and importer.py), loading it
loads no heavy library, and the job worker and the clip database never
import it.

Editor suite step 6 adds the new editor's media code (video_editor/media/:
serving files, proxies, peaks, thumbnails, the editor cache). Nothing
core/pipeline.py loads may import it either, and it has the same kind of
rules, at the very end of this file.
"""

import ast
import subprocess
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "core" / "pipeline.py"
FORBIDDEN = "video_editor.project"
MEDIA = "video_editor.media"
BOTH = (FORBIDDEN, MEDIA)


def _forbidden(name: str, roots=(FORBIDDEN,)) -> bool:
    return any(name == root or name.startswith(root + ".") for root in roots)


def project_imports(source: str, roots=(FORBIDDEN,)) -> list[str]:
    """Every import of video_editor.project (or below it) in `source`; of
    each package in `roots`, when given."""
    leaves = {root.rpartition(".")[2] for root in roots}
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f"line {node.lineno}: import {a.name}" for a in node.names if _forbidden(a.name, roots)]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names = [a.name for a in node.names]
            if _forbidden(node.module, roots) or (
                    node.module == "video_editor" and any(n in leaves | {"*"} for n in names)):
                found.append(f"line {node.lineno}: from {node.module} import {', '.join(names)}")
        elif isinstance(node, ast.Call) and node.args:
            # importlib.import_module("video_editor.project"), __import__("...")
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            first = node.args[0]
            if (called in ("import_module", "__import__") and isinstance(first, ast.Constant)
                    and isinstance(first.value, str) and _forbidden(first.value, roots)):
                found.append(f"line {node.lineno}: {called}({first.value!r})")
    return found


def _module_file(root: Path, name: str) -> Path | None:
    """The repo file for a dotted module name, or None when it isn't one of the repo's."""
    base = root.joinpath(*name.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def _module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_names(root: Path, path: Path) -> set[str]:
    """Every module name `path` imports, anywhere in the file, with relative imports resolved.

    `from pkg import name` adds both pkg and pkg.name, because name may be a
    submodule. Names that turn out not to be repo modules are dropped later.
    """
    module = _module_name(root, path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[:len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if base:
                names.add(base)
            names.update(f"{base}.{a.name}" if base else a.name for a in node.names if a.name != "*")
    return names


def reachable_modules(root: Path, start: Path, roots=(FORBIDDEN,)) -> dict[Path, list[str]]:
    """Every repo module `start` loads through imports, with the chain that reaches it.

    Importing a.b.c also runs a/__init__.py and a/b/__init__.py, so those are
    followed too. Imports of the project model itself are not followed; they
    are what reachable_offenders reports.
    """
    chain = {start: [_module_name(root, start)]}
    queue = deque([start])
    while queue:
        path = queue.popleft()
        for name in sorted(_imported_names(root, path)):
            if _forbidden(name, roots):
                continue
            parts = name.split(".")
            for depth in range(1, len(parts) + 1):
                target = _module_file(root, ".".join(parts[:depth]))
                if target is not None and target not in chain:
                    chain[target] = chain[path] + [_module_name(root, target)]
                    queue.append(target)
    return chain


def reachable_offenders(root: Path, start: Path, roots=(FORBIDDEN,)) -> list[str]:
    """Each import of the project model (of each package in `roots`) in a
    module `start` loads, with its chain."""
    offenders = []
    for path, via in reachable_modules(root, start, roots).items():
        for line in project_imports(path.read_text(encoding="utf-8"), roots):
            offenders.append(f"{' -> '.join(via)}: {line}")
    return offenders


def test_the_pipeline_never_imports_the_editor_project_model():
    found = project_imports(PIPELINE.read_text(encoding="utf-8"))
    assert not found, "core/pipeline.py imports the editor's project model:\n" + "\n".join(found)


def test_nothing_the_pipeline_loads_imports_the_editor_project_model():
    offenders = reachable_offenders(ROOT, PIPELINE)
    assert not offenders, (
        "Plain auto-clipping would load the editor's project model:\n" + "\n".join(offenders))


def test_the_walk_reaches_the_editor_modules_the_pipeline_uses():
    """The walk must really follow imports, or the test above passes vacuously."""
    names = {_module_name(ROOT, path) for path in reachable_modules(ROOT, PIPELINE)}
    for expected in ("video_editor.timeline", "video_editor.export", "video.cropper", "video.encoding"):
        assert expected in names, f"the walk never reached {expected}"
    assert len(names) > 20


def test_the_guard_catches_every_way_of_importing_it():
    imports = [
        "import video_editor.project",
        "import video_editor.project.model as model",
        "from video_editor.project import ops",
        "from video_editor.project.store import load",
        "from video_editor import project",
        "from video_editor import timeline, project as p",
        "from video_editor import *",
        "def render():\n    from video_editor.project import projection\n",
        "import importlib\nstore = importlib.import_module('video_editor.project.store')",
        "model = __import__('video_editor.project.model')",
    ]
    for source in imports:
        assert project_imports(source), f"not caught: {source}"


def test_the_guard_lets_the_rest_of_video_editor_through():
    fine = [
        "from video_editor.timeline import EditList",
        "from video_editor import watermark as _wm",
        "import video_editor.export",
        "from video_editor.projection import remap",        # a name that only starts the same
        "from . import project",                             # relative: not the editor package
        "importlib.import_module(name)",
    ]
    for source in fine:
        assert not project_imports(source), f"caught by mistake: {source}"


def _mini_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def test_the_guard_follows_imports_through_other_modules(tmp_path):
    root = _mini_repo(tmp_path, {
        "core/__init__.py": "",
        "core/pipeline.py": "import numpy\nfrom video_editor.export import apply_edits\n",
        "video_editor/__init__.py": "",
        "video_editor/export.py": "from .timeline import EditList\n",
        "video_editor/timeline.py": (
            "class EditList:\n    pass\n\n"
            "def _later():\n    from video_editor.project import model\n"),
        "video_editor/project/__init__.py": "",
        "video_editor/project/model.py": "",
    })
    offenders = reachable_offenders(root, root / "core" / "pipeline.py")
    assert len(offenders) == 1
    assert "core.pipeline -> video_editor.export -> video_editor.timeline" in offenders[0]


def test_the_guard_follows_package_inits_and_relative_imports(tmp_path):
    root = _mini_repo(tmp_path, {
        "core/__init__.py": "",
        "core/pipeline.py": "from video.cutter import cut_clip\n",
        "video/__init__.py": "from . import helpers\n",
        "video/cutter.py": "def cut_clip():\n    pass\n",
        "video/helpers.py": "import video_editor.project.store\n",
        "video_editor/__init__.py": "",
        "video_editor/project/__init__.py": "",
        "video_editor/project/store.py": "",
    })
    offenders = reachable_offenders(root, root / "core" / "pipeline.py")
    assert offenders and "video.helpers" in offenders[0]


def test_a_clean_chain_passes(tmp_path):
    root = _mini_repo(tmp_path, {
        "core/__init__.py": "",
        "core/pipeline.py": "from video_editor.export import apply_edits\n",
        "video_editor/__init__.py": "",
        "video_editor/export.py": "from .timeline import EditList\nimport json\n",
        "video_editor/timeline.py": "class EditList:\n    pass\n",
    })
    assert reachable_offenders(root, root / "core" / "pipeline.py") == []


# ---- the package itself (editor suite step 5) ----------------------------------------------------

PACKAGE = ROOT / "video_editor" / "project"
PACKAGE_FILES = ("__init__.py", "env.py", "importer.py", "migrate.py", "model.py", "ops.py", "patch.py",
                 "projection.py", "store.py")
# The only imports from outside the package and the standard library, each
# inside the function that needs it: what the operations ask the app
# (env.py), and the edit as the render reads it (importer.py).
ASKED_INSIDE_A_FUNCTION = {
    "env.py": {"video.audio_library", "video.export_presets", "video.filters", "core.modes", "video.captions",
               "video.encoding"},
    "importer.py": {"video_editor.timeline"},
}


def _imports(path: Path) -> tuple[set[str], set[str]]:
    """(the modules a file imports at its top, the ones it imports inside a function)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top, inner = set(), set()

    def names(node) -> set[str]:
        if isinstance(node, ast.Import):
            return {a.name for a in node.names}
        if isinstance(node, ast.ImportFrom):
            assert not node.level, f"{path.name}: a relative import"
            return {f"{node.module}.{a.name}" if node.module in ("video", "core", "video_editor") else node.module
                    for a in node.names}
        return set()

    for node in tree.body:
        top |= names(node)
    for node in ast.walk(tree):
        if node not in tree.body:
            inner |= names(node)
    return top, inner


def _outside(names: set[str]) -> set[str]:
    return {n for n in names if n.split(".")[0] not in sys.stdlib_module_names and n != "__future__"
            and not _forbidden(n)}


def test_the_package_is_these_files():
    assert sorted(p.name for p in PACKAGE.glob("*.py")) == sorted(PACKAGE_FILES)
    assert not project_imports((ROOT / "video_editor" / "__init__.py").read_text(encoding="utf-8"))


def test_the_project_package_is_standard_library_only():
    for name in PACKAGE_FILES:
        top, inner = _imports(PACKAGE / name)
        assert _outside(top) == set(), f"{name} imports {_outside(top)} at its top"
        assert _outside(inner) == ASKED_INSIDE_A_FUNCTION.get(name, set()), name


def test_loading_the_project_package_loads_nothing_heavy():
    """In a fresh Python: every module of the package, then the heavy
    libraries and the pipeline looked for among what got loaded."""
    code = "; ".join((
        "import sys",
        f"sys.path.insert(0, {str(ROOT)!r})",
        "from video_editor.project import env, importer, migrate, model, ops, patch, projection, store",
        "importer.import_clip({'video_id': 'v', 'start': 0, 'end': 9, 'render_opts': {'edit': {'speed': 2}}})",
        "heavy = [m for m in ('numpy', 'cv2', 'torch', 'PIL', 'fastapi', 'pydantic', 'core.pipeline', 'core.state',"
        " 'server.jobs') if m in sys.modules]",
        "print('loaded:', heavy)",
    ))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "loaded: []", out.stdout


def test_the_worker_and_the_database_never_import_the_package():
    """Nothing of step 5 runs unless a project route is called: only the
    routes' own module imports the package."""
    for rel in ("server/jobs.py", "core/state.py", "core/remake.py", "server/editor_lane.py", "core/housekeeping.py"):
        assert not project_imports((ROOT / rel).read_text(encoding="utf-8")), rel
    importers = sorted(
        path.relative_to(ROOT).as_posix()
        for folder in ("core", "server", "video", "video_editor", "analysis", "plugins", "publish", "campaigns")
        for path in (ROOT / folder).rglob("*.py")
        if PACKAGE not in path.parents and project_imports(path.read_text(encoding="utf-8")))
    # Since step 6, importing a file for editing copies it in through the
    # store (inside the function that does it). Since step 7, the export
    # compiler reads the document's time helpers (model) and its routes the
    # store and the projection.
    assert importers == ["server/compose_api.py", "server/projects_api.py", "video_editor/compose.py",
                         "video_editor/media/importing.py"]


# ---- the media code (editor suite step 6) --------------------------------------------------------

MEDIA_PACKAGE = ROOT / "video_editor" / "media"
MEDIA_FILES = ("__init__.py", "cache.py", "fileio.py", "importing.py", "peaks.py", "probe.py", "proxy.py",
               "resolver.py", "thumbs.py")
# What the media code asks the rest of the app, each inside the function
# that needs it.
MEDIA_ASKS = {
    "importing.py": {"video_editor.project"},
    "peaks.py": {"core.binaries", "video.export_presets", "numpy"},
    "probe.py": {"core.binaries"},
    "resolver.py": {"core.paths", "video.audio_library"},
    "thumbs.py": {"core.binaries", "video.export_presets"},
}


def test_the_pipeline_never_imports_the_media_code():
    found = project_imports(PIPELINE.read_text(encoding="utf-8"), BOTH)
    assert not found, "core/pipeline.py imports the editor's media code:\n" + "\n".join(found)


def test_nothing_the_pipeline_loads_imports_the_media_code():
    offenders = reachable_offenders(ROOT, PIPELINE, BOTH)
    assert not offenders, "Plain auto-clipping would load the editor's media code:\n" + "\n".join(offenders)


def test_the_pipeline_never_reaches_what_deletes_or_serves_editor_media():
    names = {_module_name(ROOT, path) for path in reachable_modules(ROOT, PIPELINE, BOTH)}
    for module in ("core.housekeeping", "server.media_api", "server.editor_lane", "server.api", "core.remake"):
        assert module not in names, f"core/pipeline.py loads {module}"


def test_the_guard_catches_the_media_code_too():
    for source in ("import video_editor.media", "from video_editor.media import cache",
                   "from video_editor import media", "def f():\n    from video_editor.media.peaks import compute\n",
                   "importlib.import_module('video_editor.media.resolver')"):
        assert project_imports(source, BOTH), source
        assert not project_imports(source), f"counted as the project model: {source}"


def test_the_media_package_is_these_files():
    assert sorted(p.name for p in MEDIA_PACKAGE.glob("*.py")) == sorted(MEDIA_FILES)


def test_the_media_package_is_standard_library_only():
    def asked(names: set[str]) -> set[str]:
        return {n for n in _outside(names) | {n for n in names if _forbidden(n)} if not _forbidden(n, (MEDIA,))}

    for name in MEDIA_FILES:
        top, inner = _imports(MEDIA_PACKAGE / name)
        assert asked(top) == set(), f"{name} imports {asked(top)} at its top"
        assert asked(inner) == MEDIA_ASKS.get(name, set()), name


def test_loading_the_media_package_loads_nothing_heavy():
    code = "; ".join((
        "import sys",
        f"sys.path.insert(0, {str(ROOT)!r})",
        "from video_editor.media import cache, fileio, importing, peaks, probe, proxy, resolver, thumbs",
        "heavy = [m for m in ('numpy', 'cv2', 'torch', 'PIL', 'fastapi', 'pydantic', 'core.pipeline',"
        " 'server.jobs', 'video_editor.project') if m in sys.modules]",
        "print('loaded:', heavy)",
    ))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "loaded: []", out.stdout


def test_only_the_media_routes_and_storage_import_the_media_code():
    importers = sorted(
        path.relative_to(ROOT).as_posix()
        for folder in ("core", "server", "video", "video_editor", "analysis", "plugins", "publish", "campaigns",
                       "remote_render", "sources", "transcription", "creator", "voice")
        if (ROOT / folder).is_dir()
        for path in (ROOT / folder).rglob("*.py")
        if MEDIA_PACKAGE not in path.parents and project_imports(path.read_text(encoding="utf-8"), (MEDIA,)))
    # Since step 7 the export resolves its media and keeps its tracking in
    # the cache (server/compose_api.py; video_editor/compose.py inside a function).
    assert importers == ["core/housekeeping.py", "server/api.py", "server/compose_api.py", "server/media_api.py",
                         "video_editor/compose.py"]
    for rel in ("core/housekeeping.py", "server/api.py", "video_editor/compose.py"):
        top, _inner = _imports_any(ROOT / rel)
        assert not {n for n in top if _forbidden(n, (MEDIA,))}, f"{rel} imports the media code at its top"


def _imports_any(path: Path) -> tuple[set[str], set[str]]:
    """(the modules a file imports at its top, the ones it imports inside a
    function), for a file that may use relative imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def names(node) -> set[str]:
        if isinstance(node, ast.Import):
            return {a.name for a in node.names}
        if isinstance(node, ast.ImportFrom) and node.module:
            return {node.module} | {f"{node.module}.{a.name}" for a in node.names}
        return set()

    top = set().union(*(names(n) for n in tree.body)) if tree.body else set()
    inner = set().union(*(names(n) for n in ast.walk(tree) if n not in tree.body))
    return top, inner


# ---- the framing move and the export compiler (editor suite step 7) ------------------------------

FRAMING = ROOT / "core" / "framing.py"
COMPOSE = "video_editor.compose"
EDITOR = (FORBIDDEN, MEDIA, COMPOSE)


def test_the_pipeline_and_its_framing_never_import_the_editor():
    """core/framing.py (core/pipeline.py's framing block, moved so the
    export compiler can frame an AI item the same way) imports none of the
    project model, the media code or the compiler, and neither does the
    pipeline, directly or through anything they load."""
    for start in (PIPELINE, FRAMING):
        found = project_imports(start.read_text(encoding="utf-8"), EDITOR)
        assert not found, f"{start.name} imports the new editor:\n" + "\n".join(found)
        offenders = reachable_offenders(ROOT, start, EDITOR)
        assert not offenders, f"{start.name} would load the new editor:\n" + "\n".join(offenders)


def test_the_walk_reaches_the_framing_and_what_it_frames_with():
    """The pipeline reaches core.framing, and core.framing the renderers it
    calls (inside its functions), or the test above passes vacuously."""
    names = {_module_name(ROOT, path) for path in reachable_modules(ROOT, PIPELINE, EDITOR)}
    assert "core.framing" in names
    framed = {_module_name(ROOT, path) for path in reachable_modules(ROOT, FRAMING, EDITOR)}
    for expected in ("video.cutter", "video.cropper", "video_editor.export", "video.podcast", "gaming.run"):
        assert expected in framed, f"the walk from core.framing never reached {expected}"


def test_the_guard_catches_the_compiler_too():
    for source in ("import video_editor.compose", "from video_editor.compose import compile",
                   "from video_editor import compose", "def f():\n    from video_editor import compose\n",
                   "importlib.import_module('video_editor.compose')"):
        assert project_imports(source, EDITOR), source
        assert not project_imports(source, BOTH), f"counted as the project model or media: {source}"


def test_only_the_export_routes_import_the_compiler():
    """Nothing runs the compiler unless an export route is called: only
    server/compose_api.py imports it (server/api.py installs those routes
    inside create_app, as it does the media routes)."""
    importers = sorted(
        path.relative_to(ROOT).as_posix()
        for folder in ("core", "server", "video", "video_editor", "analysis", "plugins", "publish", "campaigns",
                       "remote_render", "sources", "transcription", "creator", "voice", "gaming", "sports")
        if (ROOT / folder).is_dir()
        for path in (ROOT / folder).rglob("*.py")
        if path != ROOT / "video_editor" / "compose.py"
        and project_imports(path.read_text(encoding="utf-8"), (COMPOSE,)))
    assert importers == ["server/compose_api.py"]
    top, _inner = _imports_any(ROOT / "server" / "api.py")
    assert "server.compose_api" not in top, "server/api.py imports the export routes at its top"


# What the compiler asks the rest of the app, each inside the function that
# needs it; at its top it imports the standard library and the document's
# time helpers only.
COMPOSE_ASKS = {"core.framing", "core.models", "video", "video.encoding", "video.export_presets", "video.filters",
                "video.outro", "video.tracker", "video_editor.media", "video_editor.media.cache",
                # Part C: the captions, fonts, hook and watermark of step 3 and
                # before, and step 4's duck settings.
                "video.captions", "video.fonts", "video_editor", "video_editor.overlay", "video_editor.watermark",
                "video_editor.export"}


def test_the_compiler_imports_the_rest_of_the_app_only_inside_functions():
    top, inner = _imports_any(ROOT / "video_editor" / "compose.py")
    outside = {n for n in top if n.split(".")[0] not in sys.stdlib_module_names and n != "__future__"}
    assert outside == {"video_editor.project", "video_editor.project.model"}, outside
    asked = {n for n in inner if n.split(".")[0] not in sys.stdlib_module_names}
    assert asked <= COMPOSE_ASKS | {f"{a}.{b}" for a in COMPOSE_ASKS for b in ("concat", "concat_entry")} | {
        n for n in asked if n.rsplit(".", 1)[0] in COMPOSE_ASKS}, asked


def test_loading_the_compiler_loads_nothing_heavy():
    code = "; ".join((
        "import sys",
        f"sys.path.insert(0, {str(ROOT)!r})",
        "from video_editor import compose",
        "heavy = [m for m in ('numpy', 'cv2', 'torch', 'PIL', 'fastapi', 'pydantic', 'core.pipeline', 'core.framing',"
        " 'core.state', 'server.jobs', 'video.tracker', 'video.cropper') if m in sys.modules]",
        "print('loaded:', heavy)",
    ))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "loaded: []", out.stdout
