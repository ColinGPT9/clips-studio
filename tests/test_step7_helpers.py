"""Editor suite step 7, part A: the helpers the export compiler builds on,
each proven to leave today's renders exactly as they were.

- video/cutter.cut_clip and video_editor/export.apply_edits take `encoder=`,
  the `-c:v ...` block to encode with. Without it the command is today's
  (the step-0 snapshots, tests/test_render_argv_snapshots.py, hold that);
  with it, the command is the same but for that block, and a refusal is
  not retried on the CPU.
- video_editor/watermark.logo_graph() is the image watermark's overlay graph,
  the one apply_image runs (compared here with the step-0 snapshot of it).
- core/framing.frame_intermediate() is core/pipeline.py's framing block,
  moved: with its own defaults it cuts through video/cutter.cut_clip as the
  pipeline does.

Light tier: FFmpeg is faked (tests/render_harness.py), so these run on CI's
light install.
"""

import json
from pathlib import Path

import pytest
from render_harness import ENCODERS, RenderHarness
from test_render_argv_snapshots import LOGO, PADDED, SEGMENTS, SNAPSHOTS, WINDOW

from video import cutter, encoding
from video.captions import build_captions
from video_editor import export, watermark
from video_editor.timeline import EditList

# A mezzanine block of the kind the compiler passes (its own choice is
# documented in docs/editor-suite/api/step7-compiler.md).
MEZZ = ["-c:v", "libx264", "-preset", "medium", "-crf", "12", "-pix_fmt", "yuv420p"]


def _commands(monkeypatch, tmp_path: Path, encoder: str, run) -> list[dict]:
    with monkeypatch.context() as mp:
        h = RenderHarness(mp, tmp_path / encoder, encoder)
        run(h)
        return [c["argv"] for c in h.commands]


def _without_block(argv: list[str], block: list[str]) -> list[str]:
    i = argv.index(block[0])
    assert argv[i:i + len(block)] == block, argv
    return [*argv[:i], "<ENCODER>", *argv[i + len(block):]]


@pytest.mark.parametrize("encoder", ENCODERS)
def test_cut_clip_with_an_encoder_differs_only_in_that_block(encoder, monkeypatch, tmp_path):
    def cut(h, **kw):
        ass = build_captions(SEGMENTS, WINDOW, h.root / "clip.ass", canvas=(1920, 1080))
        cutter.cut_clip(h.source(), PADDED, h.out("clip.mp4"), ass_path=ass, vf_extra="eq=saturation=1.2",
                        normalize=True, final=True, **kw)

    (today,) = _commands(monkeypatch, tmp_path / "a", encoder, cut)
    (unset,) = _commands(monkeypatch, tmp_path / "b", encoder, lambda h: cut(h, encoder=None))
    (mezz,) = _commands(monkeypatch, tmp_path / "c", encoder, lambda h: cut(h, encoder=MEZZ))
    assert unset == today
    selected = encoding.CPU_ARGS if encoder == "cpu" else encoding._CANDIDATES[encoder]
    assert _without_block(mezz, MEZZ) == _without_block(today, list(selected))


def test_cut_clip_with_an_encoder_is_not_retried_on_the_cpu(monkeypatch, tmp_path):
    """Today a refused GPU encode is retried once on the CPU; a block the
    caller chose is its own to handle."""
    def cut(h, **kw):
        h.fail_next("ffmpeg")
        with pytest.raises(RuntimeError, match="ffmpeg cut failed"):
            cutter.cut_clip(h.source(), PADDED, h.out("clip.mp4"), **kw)

    assert len(_commands(monkeypatch, tmp_path / "a", "nvenc", lambda h: cut(h, encoder=MEZZ))) == 1

    def today(h):
        h.fail_next("ffmpeg")
        cutter.cut_clip(h.source(), PADDED, h.out("clip.mp4"))

    assert len(_commands(monkeypatch, tmp_path / "b", "nvenc", today)) == 2


@pytest.mark.parametrize("encoder", ENCODERS)
def test_apply_edits_with_an_encoder_differs_only_in_that_block(encoder, monkeypatch, tmp_path):
    edit = {"keep": [[0.0, 3.2], [4.6, 10.0]], "speed": 1.5, "mutes": [[1.0, 1.4]]}

    def run(h, **kw):
        clip = h.media("clip.source.mp4", duration=PADDED.duration)
        export.apply_edits(clip, EditList.from_dict(edit, duration=WINDOW.duration), h.root / "e.mp4", **kw)

    (today,) = _commands(monkeypatch, tmp_path / "a", encoder, run)
    (mezz,) = _commands(monkeypatch, tmp_path / "b", encoder, lambda h: run(h, encoder=MEZZ))
    selected = encoding.CPU_ARGS if encoder == "cpu" else encoding._CANDIDATES[encoder]
    assert _without_block(mezz, MEZZ) == _without_block(today, list(selected))


# ---- watermark.logo_graph ---------------------------------------------------------------------


def _snapshot_graphs() -> dict[str, str]:
    snap = json.loads((SNAPSHOTS / "watermark_logo.json").read_text(encoding="utf-8"))
    out = {}
    for command in snap["cpu"]["commands"]:
        argv = command["argv"]
        out[command["step"]] = argv[argv.index("-filter_complex") + 1]
    return out


TURNED = {**LOGO, "position": "custom", "x": 0.25, "y": 0.7, "rotation": 15, "opacity": 0.6, "scale": 0.25}
STEPS = {"in a corner": LOGO, "moving": {**LOGO, "position": "moving"}, "dragged, turned and faded": TURNED}


@pytest.mark.parametrize("step", sorted(STEPS))
def test_logo_graph_is_the_graph_apply_image_has_always_run(step):
    assert watermark.logo_graph(STEPS[step], (1080, 1920)) == _snapshot_graphs()[step]


def test_logo_graph_names_the_pads_it_is_given():
    plain = watermark.logo_graph(TURNED, (1080, 1920))
    named = watermark.logo_graph(TURNED, (1080, 1920), base="vbase", logo="5:v", tag="logo0", out="vlogo")
    assert named == (plain.replace("[1:v]", "[5:v]").replace("[wm]", "[logo0]").replace("[0:v]", "[vbase]")
                     + "[vlogo]")
    # Another canvas scales and places the logo for it.
    assert "scale=346:-1" in watermark.logo_graph(LOGO, (1920, 1080))


# ---- core.framing.frame_intermediate --------------------------------------------------------


@pytest.mark.parametrize("encoder", ENCODERS)
def test_frame_intermediate_cuts_as_the_pipeline_did(encoder, monkeypatch, tmp_path):
    """The one-encode path (landscape): with its own defaults the move cuts
    through video/cutter.cut_clip, exactly as the call it replaced."""
    from core import framing

    config = {"clips": {"vertical": True}}

    def moved(h):
        scratch: list = []
        kept = framing.frame_intermediate(
            h.source(), PADDED, h.out("clip.mp4"), h.root, "clip", config=config, opts={}, edit=None,
            vf_extra="eq=saturation=1.2", normalize=True, landscape=True, scratch=scratch)
        assert kept == (None, None) and scratch == []

    def before(h):
        cutter.cut_clip(h.source(), PADDED, h.out("clip.mp4"), ass_path=None, vf_extra="eq=saturation=1.2",
                        normalize=True, final=True)

    assert _commands(monkeypatch, tmp_path / "a", encoder, moved) == _commands(
        monkeypatch, tmp_path / "b", encoder, before)


def test_frame_intermediate_uses_the_stand_ins_it_is_given(tmp_path):
    """core/pipeline.py passes its own cut_clip and _try_gaming_render (and
    _sport_framing), so tests that stand in for them there still reach it.
    The tracked path with the gaming layout: the cut, then the layout."""
    from core import framing

    calls = []
    scratch: list = []
    layout = {"layout": "split", "used_cam": None}

    def gaming_render(intermediate, render_path, opts, config, ass_path, vf_extra, normalize):
        calls.append(("gaming", intermediate.name, render_path.name, vf_extra, normalize))
        return layout

    kept = framing.frame_intermediate(
        tmp_path / "src.mp4", PADDED, tmp_path / "out.mp4", tmp_path, "clip",
        config={"clips": {}}, opts={}, vf_extra="eq=saturation=1.2", normalize=True, gaming=True,
        scratch=scratch, cut=lambda *a, **k: calls.append(("cut", a[2].name, k)), gaming_render=gaming_render)
    assert kept == (layout, None)
    assert calls == [("cut", "clip.source.mp4", {}),
                     ("gaming", "clip.source.mp4", "out.mp4", "eq=saturation=1.2", True)]
    assert scratch == [tmp_path / "clip.source.mp4"]       # left for the caller's finally
