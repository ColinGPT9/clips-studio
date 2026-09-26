"""The editor's split preview and the render make the same crops.

ui/src/renderer/src/lib/gamingLayout.ts mirrors gaming/layout.py by hand, so
the preview beside the boxes you drag shows exactly what the clip will be.
This runs the TypeScript under Node (type-stripping, Node 22.6+) on the same
cases as the Python and compares every number. Skipped without Node.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gaming import layout

ROOT = Path(__file__).resolve().parent.parent
TS = ROOT / "ui" / "src" / "renderer" / "src" / "lib" / "gamingLayout.ts"

CASES = [
    (1920, 1080, None, {}),
    (1920, 1080, None, {"gameAlign": "left"}),
    (1920, 1080, None, {"gameAlign": "right"}),
    (1920, 1080, [0.0, 0.0, 0.3, 0.3], {}),
    (1920, 1080, [0.0, 0.69, 0.179, 0.309], {"camPosition": "bottom"}),
    (1920, 1080, [0.646, 0.743, 0.179, 0.257], {}),
    (1920, 1080, [0.4, 0.7, 0.2, 0.3], {"gameAlign": "right"}),
    (1920, 1080, [0.0, 0.69, 0.17, 0.31], {"gameBox": [0.17, 0.0, 0.83, 0.83]}),
    (1920, 1080, None, {"gameBox": [0.17, 0.0, 0.83, 0.83]}),
    (1280, 720, [0.8, 0.0, 0.2, 0.3], {}),
    (1080, 1080, [0.7, 0.7, 0.3, 0.3], {}),                 # square: the crop is the whole width
    (2560, 1440, [0.0, 0.0, 0.25, 0.25], {"gameBox": [0.25, 0.1, 0.5, 0.9]}),
]
# Every case both ways: the game whole on a blur, and zoomed to fill.
CASES = [(w, h, cam, {**o, "gameFit": fit}) for w, h, cam, o in CASES for fit in ("fit", "fill")]


def _python(w, h, cam, opts):
    p = layout.plan(w, h, tuple(cam) if cam else None, cam_position=opts.get("camPosition", "top"),
                    game_align=opts.get("gameAlign", "center"),
                    game_box=tuple(opts["gameBox"]) if opts.get("gameBox") else None,
                    game_fit=opts.get("gameFit", "fit"))
    return {"kind": p.kind, "game": list(p.game), "cam": list(p.cam) if p.cam else None,
            "camPosition": p.cam_position, "gameFit": p.game_fit}


def test_the_preview_plans_every_case_like_the_render():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    script = (
        f"const m = await import({json.dumps(TS.as_uri())});"
        f"const cases = {json.dumps(CASES)};"
        "console.log(JSON.stringify(cases.map(([w, h, cam, o]) => m.plan(w, h, cam, o))));"
    )
    r = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    ts = json.loads(r.stdout)
    for case, got in zip(CASES, ts, strict=True):
        assert got == _python(*case), case


def test_every_crop_stays_inside_the_frame():
    for w, h, cam, opts in CASES:
        p = _python(w, h, cam, opts)
        for box in (p["game"], p["cam"]):
            if box:
                x, y, bw, bh = box
                assert x >= 0 and y >= 0 and x + bw <= w and y + bh <= h, (w, h, cam, opts, box)
