"""Captions and text as a layer on the new editor's viewer (editor suite step
9, part B; docs/editor-suite/api/step9-captions.md):

- lib/captionLayer.ts, run under Node (skipped without it): finding the
  captions and text in the engine's own ASS file, moving them while a drag is
  under way exactly as the engine will draw them (checked against
  video/captions.py itself), the five named snap rows, the centre lines and
  the phone apps' screens as snap lines, and when a box runs under an app's
  buttons;
- the viewer's source: JASSUB draws the engine's script live, the page's
  rules gain only what JASSUB needs, JASSUB is pinned and its notices are in
  NOTICE, its GPL-3.0 polyfill is left out of the app, and the app's screen
  is drawn while a caption or text is dragged."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
SRC = UI / "src" / "renderer" / "src"
LIB = SRC / "lib"
EDITOR = SRC / "components" / "editor"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _node(tmp_path, body: str):
    """Run `body` (JavaScript, with `m` the module and `out` what it prints)
    against lib/captionLayer.ts."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    (tmp_path / "captionLayer.ts").write_text(read(LIB / "captionLayer.ts"), encoding="utf-8")
    script = (f"const m = await import({json.dumps((tmp_path / 'captionLayer.ts').as_uri())});"
              f"let out; {body}; console.log(JSON.stringify(out));")
    r = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _engine_ass(style: dict) -> str:
    from core.models import ClipCandidate, Segment
    from video import captions as cap

    spoken = [{"start": 0.1 + i * 0.3, "end": 0.35 + i * 0.3, "word": w}
              for i, w in enumerate("one two three four five six".split())]
    seg = [Segment(start=0.0, end=3.0, text=" ".join(w["word"] for w in spoken), words=spoken)]
    return cap.caption_ass(seg, ClipCandidate(start=0.0, end=3.0, score=1), style={"font": "Anton", **style},
                           canvas=(1080, 1920))


def _texts(ass: str) -> list[str]:
    return [line.split(",", 9)[9] for line in ass.splitlines() if line.startswith("Dialogue:")]


# ---- the live picture while dragging equals what the engine burns --------------------------------


@pytest.mark.parametrize("animation", ["none", "slide", "pop", "fade"])
def test_a_caption_moved_live_is_written_as_the_engine_writes_a_dragged_one(tmp_path, animation):
    """Dragging draws the engine's script with the captions placed at the
    point (placeEvents); the engine, told x and y, writes the same events:
    so the words under the pointer are where they will burn."""
    plain = _engine_ass({"animation": animation})
    dragged = _engine_ass({"animation": animation, "x": 50, "y": 62.5})     # (540, 1200): inside, not clamped
    got = _node(tmp_path, f"const p = m.parseAss({json.dumps(plain)});"
                          "out = m.placeEvents(p, (e) => e.layer === 0, [540, 1200], 1)")
    assert _texts(got) == _texts(dragged)
    assert all("\\pos(540,1200)" in t or "\\move(" in t for t in _texts(got))


def test_a_resize_live_keeps_the_place_and_scales_the_size(tmp_path):
    plain = _engine_ass({})
    got = _node(tmp_path, f"const p = m.parseAss({json.dumps(plain)});"
                          "out = [m.placeEvents(p, (e) => e.layer === 0, null, 1.5),"
                          " m.placeEvents(p, (e) => e.layer === 0, null, 1)]")
    size = int(next(line for line in plain.splitlines() if line.startswith("Style: Default,")).split(",")[2])
    assert all(t.startswith(f"{{\\fs{round(size * 1.5)}}}") for t in _texts(got[0]))
    assert got[1].splitlines() == plain.splitlines()          # nothing to change: as it was


def test_only_the_chosen_events_move_and_the_text_drag_moves_one_text(tmp_path):
    ass = "\n".join([
        "[Script Info]", "PlayResX: 1080", "PlayResY: 1920", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        "Style: Default,Arial,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,440,1",
        "Style: Text2,Arial,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,1,9,3,5,60,60,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        "Dialogue: 0,0:00:00.10,0:00:01.00,Default,,0,0,0,,ONE, TWO",
        "Dialogue: 1,0:00:00.00,0:00:03.00,Text2,,0,0,0,,{\\an5\\pos(270,1536)}Hello",
        "Dialogue: 1,0:00:01.00,0:00:02.00,Text2,,0,0,0,,{\\an5\\pos(540,960)}Second",
        "Dialogue: 2,0:00:00.00,0:00:03.00,Default,,0,0,0,,mark", ""])
    got = _node(tmp_path, f"const p = m.parseAss({json.dumps(ass)});"
                          "const second = p.events.findIndex((e) => e.text.endsWith('Second'));"
                          "out = { moved: m.placeEvents(p, (e, i) => i === second, [100, 200]),"
                          " gone: m.withoutEvents(p, (e) => e.layer === 0),"
                          " at: [m.eventAt(p.events, 1.5)?.text, m.captionNear(p, 2.5)],"
                          " anchors: p.events.map((e) => m.eventAnchor(p, e)) }")
    texts = _texts(got["moved"])
    assert texts == ["ONE, TWO", "{\\an5\\pos(270,1536)}Hello", "{\\an5\\pos(100,200)}Second", "mark"]
    assert _texts(got["gone"]) == ["{\\an5\\pos(270,1536)}Hello", "{\\an5\\pos(540,960)}Second", "mark"]
    assert got["at"][0] == "{\\an5\\pos(270,1536)}Hello"
    assert got["at"][1]["showing"] is False and got["at"][1]["event"]["text"] == "ONE, TWO"   # the nearest caption
    # A bottom caption stands on its margin; a placed text on its point.
    assert got["anchors"][0] == {"x": 540, "y": 1480, "align": 2, "placed": False}
    assert got["anchors"][1] == {"x": 270, "y": 1536, "align": 5, "placed": True}


def test_text_events_are_matched_to_items_in_the_engines_order(tmp_path):
    """The viewer knows which Dialogue: 1 event is which text item by the
    order compose.build_ass writes them (text tracks not muted, items by
    start, blank ones skipped): checked against build_ass itself."""
    from test_compose_plan import apply, manual

    from video_editor import compose

    doc = apply(manual((10, 14, 1.0)), {"op": "add_track", "kind": "text"}, {"op": "add_track", "kind": "text"},
                {"op": "add_text", "track_id": "T1", "content": "Later", "start_us": 2_000_000, "duration_us": 1_000_000},
                {"op": "add_text", "track_id": "T1", "content": "First", "start_us": 0, "duration_us": 1_000_000,
                 "position": {"x": 20, "y": 20}},
                {"op": "add_text", "track_id": "T2", "content": "Other track", "start_us": 500_000,
                 "duration_us": 1_000_000, "size": 60})
    text, _ = compose.build_ass(doc, compose.Inputs(work=Path("unused"), media={}), (1080, 1920))
    drawn = [t.split("}")[-1] for line in text.splitlines() if line.startswith("Dialogue: 1,") for t in [line.split(",", 9)[9]]]
    tracks = [{"kind": t["kind"], "mute": t.get("mute", False),
               "items": [{"id": i["id"], "start_us": i["start_us"], "text": i["text"]} for i in t["items"]]}
              for t in doc["tracks"]]
    ids = _node(tmp_path, f"out = m.textEventItems({json.dumps(tracks)})")
    by_id = {i["id"]: i["text"]["content"] for t in doc["tracks"] if t["kind"] == "text" for i in t["items"]}
    assert [by_id[i] for i in ids] == drawn == ["First", "Later", "Other track"]
    # A muted text track draws nothing, so its items are left out.
    for track, own in zip(tracks, doc["tracks"]):
        track["mute"] = own["id"] == "T1"
    assert _node(tmp_path, f"out = m.textEventItems({json.dumps(tracks)})") == [ids[2]]


# ---- snapping and the apps' screens ----------------------------------------------------------------


def test_the_five_named_rows_are_defined_once_and_are_snap_lines(tmp_path):
    got = _node(tmp_path, "out = { rows: m.SNAP_ROWS, t: m.layerTargets(1080, 1920, null, [], []),"
                          " row: m.rowAt(1209.6, 1920) }")
    assert [r["id"] for r in got["rows"]] == ["top", "higher-middle", "middle", "lower-middle", "bottom"]
    assert [r["label"] for r in got["rows"]] == ["Top", "Higher middle", "Middle", "Lower middle", "Bottom"]
    ys = [r["y"] for r in got["rows"]]
    assert ys == sorted(ys) and ys[2] == 50
    # The centre lines and the frame's edges, and every row.
    assert {0, 540, 1080} <= set(got["t"]["xs"])
    assert {0, 960, 1920} | {round(y * 19.2, 2) for y in ys} <= set(got["t"]["ys"])
    assert got["row"]["id"] == "lower-middle"
    # The rows are written nowhere else.
    for f in EDITOR.glob("*.tsx"):
        assert "Higher middle" not in read(f), f


def test_the_apps_screen_edges_are_snap_lines_and_a_box_under_the_buttons_is_found(tmp_path):
    zones = json.loads(read(ROOT / "gaming" / "layouts.json"))["safe_zones"]
    shapes = [{"k": "circle", "x": 961, "y": 875, "r": 56}, {"k": "icon", "x": 961, "y": 1537, "s": 78, "labelY": 1610},
              {"k": "text", "x": 70, "y": 1607}, {"k": "icon", "x": 1008, "y": 150, "s": 56}]
    got = _node(tmp_path, f"const a = m.appAreas({json.dumps(zones['tiktok'])}, {json.dumps(shapes)}, 1080, 1920);"
                          "out = { a, t: m.layerTargets(1080, 1920, " + json.dumps(zones["tiktok"]) + ", a, [[100, 100, 200, 50]]),"
                          " under: m.underApp([700, 1000, 300, 100], a, 1920), clear: m.underApp([200, 900, 500, 100], a, 1920),"
                          " low: m.underApp([200, 1550, 500, 100], a, 1920),"
                          " wide: m.appAreas(" + json.dumps(zones["tiktok"]) + ", " + json.dumps(shapes) + ", 720, 1280) }")
    kinds = {a["kind"]: a["box"] for a in got["a"]}
    assert kinds["buttons"] == [890, 819, 190, 801]            # the column, as tall as its buttons
    assert kinds["bottom"] == [0, 1500, 890, 420] and kinds["top"] == [0, 0, 1080, 180]
    assert got["under"] == ["buttons"] and got["clear"] == [] and got["low"] == ["bottom"]
    assert {890, 60} <= set(got["t"]["xs"]) and {180, 1500, 819, 1620} <= set(got["t"]["ys"])
    assert {100, 200, 300} <= set(got["t"]["xs"])               # another layer's edges and middle
    assert {a["kind"]: a["box"] for a in got["wide"]}["buttons"][0] == pytest.approx(890 * 720 / 1080)


def test_a_dragged_place_and_a_resize_stay_in_the_engines_ranges(tmp_path):
    got = _node(tmp_path, "out = [m.centrePercent([-50, 1800, 200, 300], 1080, 1920), m.centrePercent([440, 1150, 200, 100], 1080, 1920),"
                          " m.resized(84, 100, 300, 20, 200), m.resized(84, 100, 10, 20, 200), m.resized(96, 50, 75, 8, 400)]")
    assert got[0] == {"x": 4.63, "y": 100} and got[1] == {"x": 50, "y": 62.5}
    assert got[2:] == [200, 20, 144]


@pytest.mark.parametrize("canvas", [(1080, 1920), (720, 1280), (1920, 1080)])
@pytest.mark.parametrize("extra", [{}, {"safe_zone": "tiktok"}, {"safe_zone": "reels", "animation": "slide"},
                                   {"safe_zone": "shorts", "font_size": 120}])
def test_the_live_drag_keeps_a_caption_where_the_engine_will_place_it(tmp_path, canvas, extra):
    """9-D2: near an edge the engine moves each dragged caption in by its own
    size (_placed_at). The live picture holds each caption the same way
    (captionBounds + heldCentre), so the words don't jump on the drop. The
    sizes here are the engine's own, so this checks the rule, not the
    measuring (the viewer measures in the page, an estimate)."""
    from video import captions as cap

    W, H = canvas
    cases = []
    for x, y in [(98, 50), (2, 50), (50, 99), (50, 1), (66.52, 62), (50, 50), (90, 95)]:
        for text in ("we did it", "a much longer caption line here"):
            opts = {**cap.DEFAULT_STYLE, "font": "Anton", **extra, "x": x, "y": y}
            w, h = cap._caption_box(text, opts, canvas, None)
            cases.append({"style": opts, "at": [round(x * W / 100), round(y * H / 100)], "size": [w, h],
                          "want": list(cap._placed_at(text, opts, canvas, None)),
                          "bounds": list(cap._place_bounds(opts, canvas))})
    got = _node(tmp_path, f"const cases = {json.dumps(cases)};"
                          f"out = cases.map((c) => [m.captionBounds(c.style, {W}, {H}), m.heldCentre(c.at, c.size, m.captionBounds(c.style, {W}, {H}))])")
    for case, (bounds, held) in zip(cases, got):
        assert bounds == case["bounds"], case
        assert held == case["want"], case


def test_the_live_drag_holds_each_caption_by_its_own_size(tmp_path):
    """placeEvents' `hold` gets each caption: two captions of different
    lengths dropped at the right edge sit at different places, as the
    engine puts them."""
    plain = _engine_ass({"words_per_caption": 1})
    got = _node(tmp_path, f"const p = m.parseAss({json.dumps(plain)});"
                          "const hold = (e, at) => [at[0] - e.text.length, at[1]];"
                          "out = m.placeEvents(p, (e) => e.layer === 0, [1000, 960], 1, hold)")
    xs = {int(re.search(r"\\pos\((\d+),", t).group(1)) for t in _texts(got)}
    assert len(xs) > 1


def test_the_caption_safe_zones_are_the_engines():
    from video import captions as cap

    src = read(LIB / "captionLayer.ts")
    block = re.split(r"\n\}\r?\n", src.split("CAPTION_SAFE_ZONES", 1)[1], maxsplit=1)[0]
    for name, zone in cap.SAFE_ZONES.items():
        assert f"{name}: {{ top: {zone['top']}, bottom: {zone['bottom']}, right: {zone['right']} }}" in block, name
    assert f"const SAFE_CLEARANCE = {cap._SAFE_CLEARANCE}" in src and f"const INK_SPREAD = {cap._INK_SPREAD}" in src
    assert f"const SLIDE_PX = {cap._SLIDE_PX}" in src
    layer = read(EDITOR / "CaptionTextLayer.tsx")
    assert "captionBounds(style, W, H)" in layer and "heldCentre(" in layer
    assert "holdCaption(parsed, shown.fs)" in layer


# ---- the viewer's source ---------------------------------------------------------------------------


def test_jassub_draws_the_engines_script_live_with_the_bundled_fonts_and_never_the_internet():
    live = read(EDITOR / "LiveCaptions.tsx")
    assert "import('jassub')" in live and "manualRender(" in live and "setTrack(" in live
    assert "queryFonts: 'local'" in live and "localandremote" not in live
    assert "loadBundledFonts()" in live and "availableFonts" in live
    viewer = read(EDITOR / "Viewer.tsx")
    assert "<LiveCaptions" in viewer and "ass={liveAss ?? layer.ass}" in viewer
    # The still is the truth when paused, but not while a layer moves or is typed into.
    assert "active={showStill && !layerBusy}" in viewer
    assert "Live captions could not start here. Pause to see them as they will look." in viewer
    api = read(LIB / "editorApi.ts")
    assert "captions?rev=${revision}" in api and "captionsAss:" in api and "captions/source_times" in api


def test_the_windows_fonts_a_script_names_are_added_from_this_pc_and_drawn_again(tmp_path):
    # 9-D9: Arial, Arial Black... from the engine's GET /fonts `system`, the
    # burn's own files; 9-D10: a script is drawn again once its fonts are in.
    live = read(EDITOR / "LiveCaptions.tsx")
    assert "editorApi" in live and ".systemFonts()" in live and "assFamilies(script)" in live
    assert "j.renderer.addFonts(urls)" in live
    assert "const REDRAW_MS = [250, 1000]" in live and live.count("drawSoon()") == 2
    assert "systemFonts:" in read(LIB / "editorApi.ts")
    got = _node(tmp_path, "out = m.assFamilies(" + json.dumps(
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, Bold\nStyle: Default,Arial,84,-1\n"
        "Style: T1,Arial Black,96,0\n[Events]\n"
        "Dialogue: 1,0:00:00.00,0:00:01.00,T1,,0,0,0,,{\\fnImpact\\pos(1,2)}Hi") + ")")
    assert sorted(got) == ["arial", "arial black", "impact"]


def test_the_page_rules_gain_only_what_jassub_needs():
    html = read(SRC.parent / "index.html")
    csp = re.search(r'content="([^"]+)"', html[html.index("Content-Security-Policy"):]).group(1)
    directives = {d.split()[0]: d.split()[1:] for d in (p.strip() for p in csp.split(";")) if d}
    assert directives["script-src"] == ["'self'", "'wasm-unsafe-eval'"]
    assert "worker-src" not in directives and "'unsafe-eval'" not in csp and "blob:" not in csp
    # Everything else as step 8 had it.
    assert directives["default-src"] == ["'self'"]
    assert directives["connect-src"] == ["'self'", "http://127.0.0.1:8765", "ws://127.0.0.1:8765"]
    assert directives["img-src"] == ["'self'", "data:", "http://127.0.0.1:8765"]
    dev = read(UI / "vite.config.mts")
    assert "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'" in dev


def test_jassub_is_pinned_bundled_not_shipped_as_a_package_and_its_gpl_polyfill_left_out():
    pkg = json.loads(read(UI / "package.json"))
    assert pkg["devDependencies"]["jassub"] == "2.5.23"            # exact, published 2026-10-19
    assert "jassub" not in pkg["dependencies"]
    lock = json.loads(read(UI / "package-lock.json"))["packages"]
    assert lock["node_modules/jassub"]["version"] == "2.5.23"
    for config in ("electron.vite.config.ts", "vite.config.mts"):
        code = read(UI / config)
        assert "'rvfc-polyfill':" in code and "rvfcNone.ts" in code, config
        assert "worker: { format: 'es' }" in code, config
        assert "exclude: ['jassub']" in code, config
    assert read(LIB / "rvfcNone.ts").strip().endswith("export {}")
    notice = read(ROOT / "NOTICE")
    for name in ("JASSUB", "libass", "FreeType", "HarfBuzz", "FriBidi", "Liberation Sans", "rvfc-polyfill"):
        assert name in notice, name


def test_the_apps_screen_is_drawn_while_dragging_and_a_layer_under_it_gets_an_amber_outline():
    layer = read(EDITOR / "CaptionTextLayer.tsx")
    assert "import PlatformOverlay, { PLATFORM_UI } from '../PlatformOverlay'" in layer
    assert "import { SAFE_ZONES } from '../../lib/gamingLayout'" in layer
    assert "import { dragLayer, type Targets } from '../../lib/layerDrag'" in layer
    # The app last chosen, faintly, when the picker says "No overlay".
    assert "lastPlatformApp()" in layer and "<PlatformOverlay platform={dragApp} />" in layer
    assert "dragging && vertical && platform === 'none'" in layer
    # Snapping: Alt held places freely; the centre lines show from the start of a drag.
    assert "targets: e.altKey ? null : layerTargets(" in layer
    assert "x1={W / 2} x2={W / 2}" in layer and "y1={H / 2} y2={H / 2}" in layer
    # The outline names the app, and nothing is moved for you.
    assert "ring-amber-400" in layer and "\"Under TikTok's buttons\"" in layer
    assert "\"Under Instagram Reels' buttons\"" in layer and "\"Under YouTube Shorts' buttons\"" in layer
    # One change per drag, resize or edit; the arrow keys move the selected box.
    assert "op: 'set_captions', style: base" in layer and "op: 'edit_caption_words'" in layer
    assert "ArrowLeft: [-1, 0]" in layer and 'aria-live="polite"' in layer
    editor = read(LIB / "newEditor.ts")
    assert "export function lastPlatformApp()" in editor and "'tiktok'" in editor
