"""The layout editor's preview and the render make the same crops.

ui/src/renderer/src/lib/gamingLayout.ts mirrors gaming/layout.py and
gaming/framing.py by hand, and gamingLayouts.ts copies gaming/layouts.json,
so the preview beside the boxes you drag shows exactly what the clip will be.
This runs the TypeScript under Node (type-stripping, Node 22.6+) on many cases
and compares every number with the Python. Skipped without Node.
"""

import itertools
import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from gaming import layout

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "ui" / "src" / "renderer" / "src" / "lib"

BOXES = {
    "corner": [0.0, 0.0, 0.25, 1 / 3],
    "high": [0.0396, 0.270, 0.1417, 0.243],
    "bottom_right": [0.646, 0.743, 0.179, 0.257],
    "middle": [0.4, 0.7, 0.2, 0.3],
}
HEADS = {"corner": [240, 60, 300], "high": [211, 311, 443], "bottom_right": [1410, 830, 1020], "middle": [960, 800, 1000]}


def _cases():
    cases = []
    for (name, cam), preset in itertools.product(BOXES.items(), sorted(layout.PRESETS)):
        for order, fit, safe, divider in itertools.product(("cam_top", "game_top"), ("fit", "fill"),
                                                           ("tiktok", "shorts"), (None, 0.3, 0.9)):
            s = {"preset": preset, "cam": cam, "order": order, "game_fit": fit, "safe": safe,
                 "ui_box": [0.3, 0.0, 0.4, 0.08], "cam2": [0.75, 0.0, 0.25, 0.3]}
            if divider is not None:
                s["divider"] = divider
            cases.append((1920, 1080, s, {"cam": HEADS[name]}))
    # The stream's solid panels: a black chat bar under the game and a splits
    # timer (measured on a speedrun), and a chat column down the right edge.
    panel_sets = {"bar": [[0.0, 0.5852, 0.1667, 0.1019], [0.15, 0.8296, 0.85, 0.1704]],
                  "column": [[0.8, 0.1, 0.2, 0.9]]}
    cams = {"bottom_left": [0.0, 0.6926, 0.1625, 0.3074], "corner": BOXES["corner"]}
    for (pname, panels), (cname, cam), preset, order, fit, align in itertools.product(
            panel_sets.items(), cams.items(), sorted(layout.PRESETS), ("cam_top", "game_top"), ("fit", "fill"),
            ("center", "left", "right")):
        s = {"preset": preset, "cam": cam, "order": order, "game_fit": fit, "game_align": align, "panels": panels,
             "ui_box": [0.3, 0.0, 0.4, 0.08], "cam2": [0.75, 0.0, 0.25, 0.3]}
        cases.append((1920, 1080, s, {}))
    # Layers placed on the Short: facecams (their shape kept, two always the
    # same size) and the Game UI, including ones dragged off the edge.
    places = [{"cam": [0.5, 0.6, 0.3, 0.9]}, {"cam": [0.95, 0.99, 0.5, 0.5], "cam2": [0.1, 0.1, 0.2, 0.2]},
              {"cam": [0.1, 0.1, 0.01, 0.01], "ui": [0.1, 0.8, 0.5, 0.06]}, {"ui": [0.6, 0.0, 0.9, 0.3]}]
    for place, preset, order in itertools.product(places, sorted(layout.PRESETS), ("cam_top", "game_top")):
        s = {"preset": preset, "cam": BOXES["corner"], "order": order, "places": place,
             "ui_box": [0.3, 0.0, 0.4, 0.08], "cam2": [0.75, 0.0, 0.25, 0.3]}
        cases.append((1920, 1080, s, {"cam": HEADS["corner"]}))
    cases += [
        (1920, 1080, {"preset": "split", "cam": None}, {}),
        (1920, 1080, {"preset": "split", "cam": BOXES["corner"], "game_box": [0.17, 0.0, 0.83, 0.83]}, {}),
        (1920, 1080, {"preset": "fullscreen", "cam": None, "game_align": "right"}, {}),
        (1920, 1080, {"cam": [0.0, 0.69, 0.17, 0.31], "cam_position": "bottom", "game_fit": "fit"}, {}),
        (1280, 720, {"preset": "small_cam", "cam": [0.8, 0.0, 0.2, 0.3]}, {}),
        (1080, 1080, {"preset": "half", "cam": [0.7, 0.7, 0.3, 0.3]}, {}),
        (2560, 1440, {"preset": "circle_cam", "cam": [0.0, 0.0, 0.25, 0.25]}, {"cam": [320, 60, 300]}),
    ]
    return cases


def _python(w, h, settings, heads):
    p = layout.plan(w, h, settings, {k: tuple(v) for k, v in heads.items()})
    return {"preset": p.preset, "order": p.order, "safe": p.safe,
            "elements": [{**asdict(e), "src": list(e.src), "dest": list(e.dest)} for e in p.elements]}


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _run_ts(script: str) -> str:
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_the_preview_plans_every_case_like_the_render(tmp_path):
    cases = _cases()
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    # gamingLayout.ts imports './gamingLayouts' without an extension (Vite
    # resolves it); Node needs the file name, so a copy is made beside it.
    for name in ("gamingLayout.ts", "gamingLayouts.ts"):
        text = (LIB / name).read_text(encoding="utf-8").replace("from './gamingLayouts'", "from './gamingLayouts.ts'")
        (tmp_path / name).write_text(text, encoding="utf-8")
    script = (
        f"const m = await import({json.dumps((tmp_path / 'gamingLayout.ts').as_uri())});"
        "const fs = await import('node:fs');"
        f"const cases = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'cases.json'))}, 'utf8'));"
        "console.log(JSON.stringify(cases.map(([w, h, s, heads]) => m.plan(w, h, s, heads))));"
    )
    ts = json.loads(_run_ts(script))
    assert len(ts) == len(cases)
    for case, got in zip(cases, ts, strict=True):
        assert got == _python(*case), case


def test_the_editors_copy_of_the_layout_table_is_the_same(tmp_path):
    ts = (LIB / "gamingLayouts.ts").read_text(encoding="utf-8")
    (tmp_path / "gamingLayouts.ts").write_text(ts, encoding="utf-8")
    script = (f"const m = await import({json.dumps((tmp_path / 'gamingLayouts.ts').as_uri())});"
              "console.log(JSON.stringify(m.LAYOUTS));")
    assert json.loads(_run_ts(script)) == json.loads((ROOT / "gaming" / "layouts.json").read_text(encoding="utf-8"))


def test_every_crop_stays_inside_the_frame():
    for w, h, settings, heads in _cases():
        for e in _python(w, h, settings, heads)["elements"]:
            x, y, bw, bh = e["src"]
            assert x >= 0 and y >= 0 and x + bw <= w and y + bh <= h, (settings, e)
