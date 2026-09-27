"""Moving and resizing a layer on the layout editor's preview
(ui/src/renderer/src/lib/layerDrag.ts): it snaps to the Short's middle and to
other layers the way a design editor does, a facecam keeps its shape whichever
side it's pulled from, and Alt turns snapping off. Run under Node; skipped
without it."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "ui" / "src" / "renderer" / "src" / "lib"
TARGETS = {"xs": [0, 540, 1080], "ys": [0, 960, 1920]}
OPTS = {"aspect": None, "canvas": [1080, 1920], "minW": 130, "minH": 38, "targets": TARGETS, "tol": 22}


def _drag(tmp_path, calls):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    (tmp_path / "layerDrag.ts").write_text((LIB / "layerDrag.ts").read_text(encoding="utf-8"), encoding="utf-8")
    script = (f"const m = await import({json.dumps((tmp_path / 'layerDrag.ts').as_uri())});"
              f"const calls = {json.dumps(calls)};"
              "console.log(JSON.stringify(calls.map(c => m.dragLayer(...c))));")
    r = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_layer_moved_near_the_middle_snaps_to_it_and_shows_the_line(tmp_path):
    (got,) = _drag(tmp_path, [[[100, 300, 400, 400], "move", 250, 0, OPTS]])     # its middle lands at 550
    assert got["box"][0] + got["box"][2] / 2 == 540 and got["guides"]["xs"] == [540]


def test_alt_places_it_freely(tmp_path):
    (got,) = _drag(tmp_path, [[[100, 300, 400, 400], "move", 250, 0, {**OPTS, "targets": None}]])
    assert got["box"][0] == 350 and got["guides"] == {"xs": [], "ys": []}


def test_it_lines_up_with_another_layer(tmp_path):
    opts = {**OPTS, "targets": {"xs": [0, 540, 1080, 700], "ys": [0, 960, 1920, 250]}}
    (got,) = _drag(tmp_path, [[[100, 300, 400, 400], "move", 0, -40, opts]])
    assert got["box"][1] == 250 and got["guides"]["ys"] == [250]


@pytest.mark.parametrize("handle,dx,dy", [("w", -100, 0), ("e", 100, 0), ("n", 0, -100), ("s", 0, 100),
                                          ("nw", -100, -100), ("se", 100, 100), ("ne", 100, -100), ("sw", -100, 100)])
def test_a_round_facecam_pulled_from_any_side_keeps_its_shape(tmp_path, handle, dx, dy):
    start = [300, 400, 400, 400]
    (got,) = _drag(tmp_path, [[start, handle, dx, dy, {**OPTS, "aspect": 1.0, "targets": None}]])
    x, y, w, h = got["box"]
    assert w == h == 500                                                   # grew, still round
    if "w" in handle:
        assert x + w == 700                                                # the right side stayed put
    if "n" in handle:
        assert y + h == 800
    if handle in ("e", "w"):
        assert y + h / 2 == 600                                            # grows about its middle
    if handle in ("n", "s"):
        assert x + w / 2 == 500


def test_a_small_facecam_keeps_its_16_by_10_shape(tmp_path):
    (got,) = _drag(tmp_path, [[[300, 400, 480, 300], "e", 160, 0, {**OPTS, "aspect": 1.6, "targets": None}]])
    assert got["box"][2:] == [640, 400]


def test_a_free_box_snaps_each_edge_it_moves(tmp_path):
    (got,) = _drag(tmp_path, [[[100, 300, 400, 100], "e", 30, 0, OPTS]])
    assert got["box"] == [100, 300, 440, 100] and got["guides"]["xs"] == [540]


def test_a_layer_never_leaves_the_short_or_shrinks_past_the_minimum(tmp_path):
    moved, shrunk = _drag(tmp_path, [[[100, 300, 400, 400], "move", 2000, 3000, {**OPTS, "targets": None}],
                                     [[100, 300, 400, 400], "se", -1000, -1000,
                                      {**OPTS, "aspect": 1.0, "targets": None}]])
    assert moved["box"] == [680, 1520, 400, 400]
    assert shrunk["box"][2] == 130
