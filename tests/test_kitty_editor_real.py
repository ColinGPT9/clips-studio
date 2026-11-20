"""Editor suite step K2: a project built with the real Clips Kitty Editor.

Runs only where the editor is (CLIPS_KITTY_EDITOR_DIR, or the installed
app's resources/editor: core.binaries.editor_exe) and a test video is named:

    CLIPS_KITTY_EDITOR_DIR=<folder with clips-kitty-editor.exe>
    CLIPS_KITTY_EDITOR_TEST_VIDEO=<a video file of 40 s or more, with sound>

Skipped everywhere else (Linux CI, a checkout with no editor). No window is
opened: the editor runs headless, as it does when the app builds a project.

A clip's project is made from the video as its base (the framed render is
not part of this test), then opened again and read back with the editor's
own timeline_read: the kept ranges are pieces back to back, the muted
stretch is its own piece, the captions sit at their program times, and the
folder's lock is let go so the window can open it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core import binaries
from kitty_editor import build

VIDEO = os.environ.get("CLIPS_KITTY_EDITOR_TEST_VIDEO", "")

pytestmark = pytest.mark.skipif(
    os.name != "nt" or binaries.editor_exe() is None or not VIDEO or not Path(VIDEO).is_file(),
    reason="needs Windows, Clips Kitty Editor (CLIPS_KITTY_EDITOR_DIR) and CLIPS_KITTY_EDITOR_TEST_VIDEO")


def close_to(a: float, b: float, by: float = 0.05) -> bool:
    return abs(a - b) <= by


def test_a_clips_project_is_built_and_read_back(tmp_path):
    exe = binaries.editor_exe()
    video = Path(VIDEO)
    info = build.probe(video)
    if not info["video"] or info["seconds"] < 40:
        pytest.skip("the test video can't be read, or is shorter than 40 s")
    home = tmp_path / "project"
    home.mkdir()
    opts = {"edit": {"keep": [[0.0, 10.0], [15.0, 35.0]], "mutes": [[20.0, 21.0]], "volume": 0.8, "fade_out": 0.5,
                     "hook": {"text": "Wait for it", "seconds": 3}},
            "caption_style": {"color": "#FFE600", "position": "bottom"}}
    lines = [{"start": 0.5, "end": 3.0, "text": "first line"}, {"start": 11.0, "end": 14.0, "text": "cut away"},
             {"start": 16.0, "end": 22.0, "text": "after the cut"}]
    plan = build.plan_clip(opts=opts, window=40.0, base_seconds=info["seconds"], lines=lines,
                           canvas=(info["width"], info["height"]),
                           watermark={"type": "text", "text": "@test", "position": "top_right"})
    canvas = {"width": info["width"], "height": info["height"], "fps_num": info["fps_num"],
              "fps_den": info["fps_den"]}
    with build.open_session(exe, home, [video.parent]) as session:
        project_dir = build.apply_clip_plan(session, home, "Real clip", canvas, video, plan)
    assert Path(project_dir, "concat.json").is_file()
    assert not Path(project_dir, "concat.lock").exists()            # let go: the window can open it

    with build.open_session(exe, home, [video.parent]) as session:
        session.tool("project_open", path=project_dir)
        view = session.tool("timeline_read", path=project_dir)
        session.tool("project_close", path=project_dir, save=False)
    assert (view["project"]["width"], view["project"]["height"]) == (info["width"], info["height"])
    clips = view["timeline"]["clips"]
    pieces = sorted((c for c in clips if c["kind"] == "video"), key=lambda c: c["start"])
    assert [(round(c["start"], 2), round(c["duration"], 2), round(c["sourceStart"], 2)) for c in pieces] == [
        (0.0, 10.0, 0.0), (10.0, 5.0, 15.0), (15.0, 1.0, 20.0), (16.0, 14.0, 21.0)]
    assert [bool(c.get("muted")) for c in pieces] == [False, False, True, False]
    assert all(close_to(c.get("volume", 1.0), 0.8) for c in pieces) and close_to(pieces[-1]["fadeOut"], 0.5)
    texts = {c["text"]: c for c in clips if c["kind"] == "text"}
    assert set(texts) == {"first line", "after the cut", "Wait for it", "@test"}
    assert close_to(texts["after the cut"]["start"], 11.0) and close_to(texts["after the cut"]["end"], 17.0)
    assert close_to(texts["Wait for it"]["duration"], 3.0) and texts["Wait for it"]["offsetY"] < 0
    assert close_to(texts["@test"]["duration"], 30.0) and texts["@test"]["offsetX"] > 0
    assert close_to(view["timeline"]["duration"], 30.0)


def test_a_whole_video_project_is_built_and_read_back(tmp_path):
    exe = binaries.editor_exe()
    video = Path(VIDEO)
    info = build.probe(video)
    if not info["video"]:
        pytest.skip("the test video can't be read")
    home = tmp_path / "project"
    home.mkdir()
    canvas = {"width": 1920, "height": 1080, "fps_num": info["fps_num"], "fps_den": info["fps_den"]}
    with build.open_session(exe, home, [video.parent]) as session:
        project_dir = build.build_whole(session, home, "Real video", canvas, video)
    with build.open_session(exe, home, [video.parent]) as session:
        session.tool("project_open", path=project_dir)
        view = session.tool("timeline_read", path=project_dir)
        session.tool("project_close", path=project_dir, save=False)
    assert (view["project"]["width"], view["project"]["height"]) == (1920, 1080)
    assert len(view["timeline"]["clips"]) == 1
    only = view["timeline"]["clips"][0]
    assert only["start"] == 0.0 and close_to(only["duration"], info["seconds"], 0.2)
    assert Path(view["media"][0]["path"]) == video                   # used in place
