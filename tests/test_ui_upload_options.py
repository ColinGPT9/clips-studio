"""A file added with "Upload video file" is set up like a pasted link.

The Add videos list offers the same switches for a file from this PC as for a
link, and the engine's POST /videos/local takes the same options
(server/api.py LocalVideoIn). The app's request (lib/api.ts addLocalVideo)
names each field it sends, so an option added to the list and the engine but
not to that request was dropped for uploads without a word: the chosen Gaming
detector, Sports and the clip direction ran as Clips Kitty's own scoring.

Read from the source, as tests/test_ui_steps.py reads the job form's: the
request has no harness of its own.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "ui" / "src" / "renderer" / "src" / "lib"


def _model_fields(name: str) -> set[str]:
    tree = ast.parse((ROOT / "server" / "api.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {s.target.id for s in node.body
                    if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)}
    raise AssertionError(f"{name} is not in server/api.py")


def _job_options() -> set[str]:
    src = (LIB / "types.ts").read_text(encoding="utf-8")
    m = re.search(r"export interface JobOptions \{(.*?)\n\}", src, re.S)
    assert m, "JobOptions is not in lib/types.ts"
    return set(re.findall(r"^\s+(\w+)\?:", m.group(1), re.M))


def _upload_body() -> set[str]:
    src = (LIB / "api.ts").read_text(encoding="utf-8")
    m = re.search(r"addLocalVideo: \(.*?body: JSON\.stringify\(\{(.*?)\}\)", src, re.S)
    assert m, "addLocalVideo's request body is not in lib/api.ts"
    return set(re.findall(r"^\s+(\w+):", m.group(1), re.M))


def test_an_upload_sends_every_option_the_engine_takes():
    """Every per-video option the list can set and the engine reads for an
    uploaded file reaches it."""
    offered = _job_options() & _model_fields("LocalVideoIn")
    assert {"pipeline", "sport", "focus", "gaming_scoring", "rate", "understand", "edit"} <= offered
    assert offered - _upload_body() == set()


def test_the_upload_request_names_only_fields_the_engine_reads():
    """A misspelt field would be dropped by the engine just as silently."""
    assert _upload_body() - _model_fields("LocalVideoIn") == set()
