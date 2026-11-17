"""Editor suite step 9, part A: the routes of the captions layer, through
FastAPI's TestClient on a temporary data folder (the fixtures of
tests/test_step8_routes.py: nothing listens on a port, the renders are
stood in for).

- GET /projects/{id}/captions.ass: the ASS the export burns, for the
  viewer to draw live; the header or the project's token, an Origin of
  this PC's own; follows a dragged caption (x, y).
- GET /projects/{id}/captions: the caption lines in program time for the
  lane, the same remap the export burns.
- POST /projects/{id}/captions/source_times: lane blocks to source times.
- POST /projects/{id}/export with captions "none" and srt: no captions in
  the picture (text still), "<name>.srt" beside the file, on both branches.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import test_step8_routes as step8
from test_step8_routes import branch_a_export, changed, clip_project

# Step 8's fixtures: the app on a temporary data folder, renders stood in for, today's re-render stood in for.
env, no_render, stub_rerender = step8.env, step8.no_render, step8.stub_rerender

pytest.importorskip("fastapi")

WORDS = [{"start": 1.2 + i * 0.4, "end": 1.45 + i * 0.4, "word": w}
         for i, w in enumerate("we did it again and the whole chat went wild".split())]


def transcript(env) -> None:
    folder = env.data_dir / "transcripts"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "vid1.json").write_text(json.dumps({"language": "en", "segments": [
        {"start": 1.2, "end": 5.0, "text": " ".join(w["word"] for w in WORDS), "words": WORDS}]}), encoding="utf-8")


def captioned_project(env) -> tuple[int, dict]:
    transcript(env)
    return clip_project(env)


def lane(env, project_id: str, revision=None) -> dict:
    r = env.get(f"/projects/{project_id}/captions" + (f"?rev={revision}" if revision is not None else ""))
    assert r.status_code == 200, r.text
    return r.json()


def dialogue(text: str, layer: int) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(f"Dialogue: {layer},")]


# ---- protection ---------------------------------------------------------------------------------


def test_the_captions_routes_need_the_app(env):
    _clip_id, project = captioned_project(env)
    pid = project["id"]
    for path in (f"/projects/{pid}/captions.ass", f"/projects/{pid}/captions"):
        assert env.client.get(path).status_code == 403                                   # no header, no token
        assert env.client.get(path + "?t=nope").status_code == 403
        assert env.get(path, origin="https://example.com").status_code == 403
        assert env.get(path).status_code == 200
    r = env.client.post(f"/projects/{pid}/captions/source_times", json={"ranges": [{"start_us": 0, "end_us": 1}]})
    assert r.status_code == 403
    # The signed address (urls.captions_ass) works with no header, as JASSUB's worker fetches it.
    signed = lane(env, pid)["urls"]["captions_ass"]
    assert "t=" in signed and env.client.get(signed).status_code == 200
    assert env.client.get(signed, headers={"origin": "https://example.com"}).status_code == 403
    assert env.get("/projects/nope/captions").status_code == 404


def test_an_old_revision_is_refused(env):
    _clip_id, project = captioned_project(env)
    newer = changed(env, project)
    r = env.get(f"/projects/{project['id']}/captions.ass?rev={project['revision']}")
    assert r.status_code == 409 and r.json()["detail"]["current_revision"] == newer
    assert env.get(f"/projects/{project['id']}/captions.ass?rev={newer}").status_code == 200


# ---- the ASS the viewer draws ----------------------------------------------------------------------


def test_the_viewers_ass_is_the_one_the_export_burns(env, no_render):
    _clip_id, project = captioned_project(env)
    pid = project["id"]
    out = env.ops(pid, project["revision"], {"op": "add_track", "kind": "text"},
                  {"op": "add_text", "content": "Watch this", "start_us": 0, "duration_us": 1_000_000,
                   "position": {"x": 50, "y": 20}})
    r = env.get(f"/projects/{pid}/captions.ass?rev={out['revision']}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/x-ssa")
    viewer = r.text
    assert dialogue(viewer, 0) and "{\\an5\\pos(540,384)}Watch this" in viewer
    assert r.headers["x-clips-kitty-revision"] == str(out["revision"])
    export = env.post(f"/projects/{pid}/export", {})
    assert export.status_code == 200, export.text
    env.lane.run_pending()
    burned = next(v for plan in no_render if plan.purpose == "export" for stage in plan.stages
                  for k, v in stage.scripts.items() if k.endswith("export.ass"))
    assert viewer == burned


def test_a_dragged_caption_moves_in_the_viewers_ass(env):
    _clip_id, project = captioned_project(env)
    pid = project["id"]
    before = env.get(f"/projects/{pid}/captions.ass").text
    assert "\\pos(" not in before
    out = env.ops(pid, project["revision"], {"op": "set_captions", "style": {"x": 50, "y": 70, "font": "Anton"}})
    after = env.get(f"/projects/{pid}/captions.ass?rev={out['revision']}").text
    events = dialogue(after, 0)
    assert events and all("\\an5\\pos(540,1344)" in e for e in events)
    # Reset position: the style without x and y draws as before.
    back = env.ops(pid, out["revision"], {"op": "set_captions", "style": {"font": "Anton"}})
    reset = env.get(f"/projects/{pid}/captions.ass?rev={back['revision']}").text
    assert "\\pos(" not in reset


def test_a_drag_then_a_resize_are_two_undo_steps(env):
    # 9-D7: both send the whole style, a moment apart; one Ctrl+Z takes back the resize only.
    _clip_id, project = captioned_project(env)
    pid = project["id"]
    style = dict(lane(env, pid)["style"])
    a = env.ops(pid, project["revision"], {"op": "set_captions", "style": {**style, "x": 50, "y": 30}})
    b = env.ops(pid, a["revision"], {"op": "set_captions", "style": {**style, "x": 50, "y": 30, "font_size": 120}})
    r = env.post(f"/projects/{pid}/undo", {"expected_revision": b["revision"]})
    assert r.status_code == 200, r.text
    after = r.json()["document"]["captions"]["style"]
    assert (after["x"], after["y"], after["font_size"]) == (50, 30, style["font_size"])
    # Two moves of one drag a moment apart stay one step, as every small tweak.
    c = env.ops(pid, r.json()["revision"], {"op": "set_captions", "style": {**style, "x": 40, "y": 20}})
    d = env.ops(pid, c["revision"], {"op": "set_captions", "style": {**style, "x": 45, "y": 25}})
    r = env.post(f"/projects/{pid}/undo", {"expected_revision": d["revision"]})
    assert (r.json()["document"]["captions"]["style"]["x"], r.json()["document"]["captions"]["style"]["y"]) == (50, 30)


def test_with_nothing_to_burn_the_viewer_gets_an_empty_script(env):
    _clip_id, project = captioned_project(env)
    out = env.ops(project["id"], project["revision"], {"op": "set_captions", "enabled": False})
    r = env.get(f"/projects/{project['id']}/captions.ass?rev={out['revision']}")
    assert r.status_code == 200 and "[Events]" in r.text and not dialogue(r.text, 0) and not dialogue(r.text, 1)


def test_a_bad_style_is_refused(env):
    _clip_id, project = captioned_project(env)
    r = env.post(f"/projects/{project['id']}/ops", {"expected_revision": project["revision"],
                                                    "ops": [{"op": "set_captions", "style": {"x": 50}}]})
    assert r.status_code == 400 and "together" in r.text
    r = env.post(f"/projects/{project['id']}/ops", {"expected_revision": project["revision"],
                                                    "ops": [{"op": "set_captions", "style": {"x": 50, "y": 101}}]})
    assert r.status_code == 400


# ---- the lane --------------------------------------------------------------------------------------


def test_the_lane_lists_the_lines_in_program_time_and_follows_a_cut(env):
    _clip_id, project = captioned_project(env)
    pid = project["id"]
    first = lane(env, pid)
    assert first["saved"] is False and first["burned"] is True and first["revision"] == project["revision"]
    assert first["lines"] and all(line["id"].startswith("tx") for line in first["lines"])
    assert first["urls"]["captions_ass"].startswith(f"/projects/{pid}/captions.ass?rev=")
    pieces = [p for line in first["lines"] for p in line["pieces"]]
    # The clip starts at 1.0 s in the video: "we" is said 0.2 s into the program.
    assert pieces[0]["start_us"] == 200_000 and pieces[0]["text"].split()[0] == "we"
    # Lines made from the transcript follow what the main track plays, so a cut only hides a
    # saved line: save them (with the lane's ids, as the lane's first edit does).
    saved = env.ops(pid, project["revision"], {"op": "set_captions", "lines": [
        {"id": line["id"], "in_us": line["in_us"], "out_us": line["out_us"], "text": line["text"]}
        for line in first["lines"]]})
    assert lane(env, pid, saved["revision"])["saved"] is True
    # Cut the program's first 1.3 s out: the first line's words are in it, so it is hidden; the
    # rest move up 1.3 s.
    item = next(t for t in project["document"]["tracks"] if t["id"] == "V1")["items"][0]
    out = env.ops(pid, saved["revision"], {"op": "split_item", "item_id": item["id"], "at_us": 1_300_000})
    head = next(t for t in out["document"]["tracks"] if t["id"] == "V1")["items"][0]["id"]
    out = env.ops(pid, out["revision"], {"op": "delete_items", "item_ids": [head]})
    cut = lane(env, pid, out["revision"])
    assert cut["lines"][0]["hidden"] and cut["lines"][0]["pieces"] == []
    assert not any(line["hidden"] for line in cut["lines"][1:])
    kept = {line["id"]: line for line in cut["lines"] if not line["hidden"]}
    for line in first["lines"]:
        if line["id"] in kept and line["pieces"][0]["start_us"] >= 1_300_000:
            assert kept[line["id"]]["pieces"][0]["start_us"] == line["pieces"][0]["start_us"] - 1_300_000


def test_a_block_retimed_on_the_lane_moves_by_the_same_amount(env):
    _clip_id, project = captioned_project(env)
    pid = project["id"]
    first = lane(env, pid)
    # Save the transcript's lines with the lane's ids, then move the second
    # line 0.3 s later through the source times the engine gives.
    lines = [{"id": line["id"], "in_us": line["in_us"], "out_us": line["out_us"], "text": line["text"]}
             for line in first["lines"]]
    out = env.ops(pid, project["revision"], {"op": "set_captions", "lines": lines})
    target = first["lines"][1]
    piece = target["pieces"][0]
    r = env.post(f"/projects/{pid}/captions/source_times",
                 {"revision": out["revision"], "ranges": [{"start_us": piece["start_us"] + 300_000,
                                                           "end_us": piece["end_us"] + 300_000},
                                                          {"start_us": 99_000_000, "end_us": 99_500_000}]})
    assert r.status_code == 200, r.text
    mapped, past_the_end = r.json()["ranges"]
    assert past_the_end is None
    assert mapped == {"in_us": target["in_us"] + 300_000, "out_us": target["out_us"] + 300_000}
    out = env.ops(pid, out["revision"], {"op": "edit_caption_words",
                                         "edits": [{"line_id": target["id"], **mapped}]})
    moved = {line["id"]: line for line in lane(env, pid, out["revision"])["lines"]}[target["id"]]
    assert moved["pieces"][0]["start_us"] == piece["start_us"] + 300_000


@pytest.mark.parametrize("body", [{"ranges": []}, {"ranges": [{"start_us": -1, "end_us": 5}]},
                                  {"ranges": [{"start_us": 0}]}, {"ranges": [{"start_us": 0.5, "end_us": 1}]},
                                  {"ranges": [{"start_us": 0, "end_us": 1}], "extra": 1}])
def test_source_times_refuse_a_bad_body(env, body):
    _clip_id, project = captioned_project(env)
    assert env.post(f"/projects/{project['id']}/captions/source_times", body).status_code == 422


# ---- the export's caption choices ------------------------------------------------------------------


def _ass_of(plan) -> str | None:
    return next((v for stage in plan.stages for k, v in stage.scripts.items() if k.endswith("export.ass")), None)


def _srt_times(text: str) -> list[tuple[str, str]]:
    return re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", text)


def test_an_export_with_captions_burned_and_no_srt_is_as_before(env, no_render):
    _clip_id, project = captioned_project(env)
    changed(env, project)
    r = env.post(f"/projects/{project['id']}/export", {})
    assert r.status_code == 200 and r.json()["export"]["captions"] == "burn" and r.json()["export"]["srt"] is False
    env.lane.run_pending()
    job = env.lane.get(r.json()["export"]["export_id"])
    assert job["status"] == "done" and "srt" not in job["result"]
    assert dialogue(_ass_of(no_render[-1]), 0)
    assert not list(Path(job["result"]["path"]).parent.glob("*.srt"))


def test_branch_b_without_burned_captions_and_with_an_srt(env, no_render):
    _clip_id, project = captioned_project(env)
    pid = project["id"]
    revision = changed(env, project)
    out = env.ops(pid, revision, {"op": "add_track", "kind": "text"},
                  {"op": "add_text", "content": "Watch this", "start_us": 0, "duration_us": 1_000_000})
    want = lane(env, pid, out["revision"])
    r = env.post(f"/projects/{pid}/export", {"captions": "none", "srt": True})
    assert r.status_code == 200, r.text
    assert r.json()["branch"] == "B"
    env.lane.run_pending()
    job = env.lane.get(r.json()["export"]["export_id"])
    assert job["status"] == "done", job
    script = _ass_of(no_render[-1])
    assert not dialogue(script, 0) and "Watch this" in script           # the text still burns
    video = Path(job["result"]["path"])
    srt = video.with_suffix(".srt")
    assert job["result"]["srt"] == str(srt) and job["result"]["srt_file"] == srt.name and srt.is_file()
    times = _srt_times(srt.read_text(encoding="utf-8"))
    pieces = sorted(p["start_us"] for line in want["lines"] for p in line["pieces"])
    assert len(times) == len(pieces)
    for (start, _end), us in zip(times, pieces):
        h, m, rest = start.split(":")
        s, ms = rest.split(",")
        assert abs((int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms) - us / 1000) <= 1


def test_branch_a_without_burned_captions_and_with_an_srt(env, no_render, stub_rerender):
    _clip_id, project = captioned_project(env)
    out, _ = branch_a_export(env, project)           # a plain export first: captions on, no srt
    env.lane.run_pending()
    assert stub_rerender[-1]["render_opts"].get("captions") is not False
    r = env.post(f"/projects/{project['id']}/export", {"revision": out["revision"], "preset": "original",
                                                       "captions": "none", "srt": True})
    assert r.status_code == 200 and r.json()["branch"] == "A", r.text
    env.lane.run_pending()
    job = env.lane.get(r.json()["export"]["export_id"])
    assert job["status"] == "done", job
    assert stub_rerender[-1]["render_opts"]["captions"] is False
    srt = Path(job["result"]["path"]).with_suffix(".srt")
    assert job["result"]["srt_file"] == srt.name and _srt_times(srt.read_text(encoding="utf-8"))


def test_an_srt_goes_with_the_video_it_describes(env, no_render):
    # 9-D1: a later export that replaces the clip's file takes the old .srt
    # with it; Storage lists a .srt whose video is gone; deleting the clip
    # deletes its .srt.
    clip_id, project = captioned_project(env)
    pid = project["id"]
    changed(env, project)
    first = env.post(f"/projects/{pid}/export", {"srt": True})
    env.lane.run_pending()
    one = env.lane.get(first.json()["export"]["export_id"])["result"]
    old_video, old_srt = Path(one["path"]), Path(one["srt"])
    assert old_video.is_file() and old_srt.is_file()
    second = env.post(f"/projects/{pid}/export", {"captions": "none", "srt": True})
    env.lane.run_pending()
    two = env.lane.get(second.json()["export"]["export_id"])["result"]
    assert two["replaced"] == str(old_video) and not old_video.exists()
    assert not old_srt.exists() and Path(two["srt"]).is_file()
    # A .srt left beside nothing (an older build, a video deleted by hand) is listed and freed.
    before = env.get("/storage").json()["reclaimable"]["orphan_clips"]["files"]
    stray = Path(two["path"]).with_name(f"{pid}-r999.srt")
    stray.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    translated = Path(two["path"]).with_name(Path(two["path"]).stem + ".es.srt")
    translated.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n", encoding="utf-8")
    assert env.get("/storage").json()["reclaimable"]["orphan_clips"]["files"] == before + 1
    env.post("/storage/cleanup", {})
    assert not stray.exists() and translated.exists() and Path(two["srt"]).is_file()
    # Deleting the clip deletes its file and the .srt beside it.
    assert env.client.delete(f"/clips/{clip_id}", headers=env.ok).status_code == 200
    assert not Path(two["path"]).exists() and not Path(two["srt"]).exists()


def _cut_out(env, pid: str, revision: int, doc: dict, a_us: int, b_us: int) -> dict:
    """Split V1 at program a and b and delete the middle (ripple), as the timeline does."""
    def v1(document):
        return next(t for t in document["tracks"] if t["id"] == "V1")["items"]

    item = next(i for i in v1(doc) if i["start_us"] <= a_us < i["start_us"] + i["duration_us"])
    out = env.ops(pid, revision, {"op": "split_item", "item_id": item["id"], "at_us": a_us})
    item = next(i for i in v1(out["document"]) if i["start_us"] <= b_us < i["start_us"] + i["duration_us"])
    out = env.ops(pid, out["revision"], {"op": "split_item", "item_id": item["id"], "at_us": b_us})
    middle = min(v1(out["document"]), key=lambda i: abs(i["start_us"] - a_us))     # splits land on frames
    return env.ops(pid, out["revision"], {"op": "delete_items", "item_ids": [middle["id"]]})


@pytest.mark.parametrize("body", [{}, {"srt": True}, {"captions": "none", "srt": True}])
def test_a_cut_through_a_caption_line_is_exported_by_the_compiler(env, no_render, stub_rerender, body):
    """9-D12: today's re-render keeps a line cut through by an edit whole,
    every word over its whole span; the lane, the live viewer and the .srt
    split it word by word. So when the captions show (burned or in a .srt),
    such an export goes through the compiler, which burns what they show."""
    _clip_id, project = captioned_project(env)
    pid = project["id"]
    # "we did it" plays 0.2-1.25 s: cut 0.4-0.8 s ("did") out of the middle of it.
    out = _cut_out(env, pid, project["revision"], project["document"], 400_000, 800_000)
    shown = [p for line in lane(env, pid, out["revision"])["lines"] for p in line["pieces"]]
    assert [p["text"] for p in shown[:2]] == ["we", "it"]
    r = env.post(f"/projects/{pid}/export", {"revision": out["revision"], "preset": "original", **body})
    assert r.status_code == 200, r.text
    assert r.json()["branch"] == "B" and r.json()["reasons"] == ["A caption line is cut through by an edit."]
    env.lane.run_pending()
    job = env.lane.get(r.json()["export"]["export_id"])
    assert job["status"] == "done", job
    assert not stub_rerender
    script = _ass_of(no_render[-1])
    burned = [line.split(",", 9)[9] for line in dialogue(script, 0)] if script else []
    if body.get("captions") == "none":
        assert burned == []
    else:
        assert burned[:2] == ["WE", "IT"] or [t.lower() for t in burned[:2]] == ["we", "it"], burned


def test_a_cut_between_caption_lines_or_with_no_captions_shown_stays_todays(env, no_render, stub_rerender):
    _clip_id, project = captioned_project(env)
    pid = project["id"]
    # Between "we did it" (ends 1.25 s) and "again and the" (starts 1.4 s): no line is cut.
    out = _cut_out(env, pid, project["revision"], project["document"], 1_270_000, 1_380_000)
    r = env.post(f"/projects/{pid}/export", {"revision": out["revision"], "preset": "original", "srt": True})
    assert r.status_code == 200 and r.json()["branch"] == "A", r.text
    env.lane.run_pending()
    # A cut through a line with the captions neither burned nor saved: nothing shows them.
    out = _cut_out(env, pid, out["revision"], out["document"], 400_000, 800_000)
    r = env.post(f"/projects/{pid}/export", {"revision": out["revision"], "preset": "original", "captions": "none"})
    assert r.status_code == 200 and r.json()["branch"] == "A", r.text


@pytest.mark.parametrize("look", [{"highlight": True}, {"animation": "karaoke"}])
def test_lit_up_words_on_a_cut_clip_are_exported_by_the_compiler(env, no_render, stub_rerender, look):
    """9-D17: today's re-render times highlighted or karaoke words by length
    once a clip is cut or sped up; the compiler times them as the live viewer
    shows them. A cut between lines, which stays today's without lit-up
    words (the test above), goes through the compiler with them."""
    _clip_id, project = captioned_project(env)
    pid = project["id"]
    styled = env.ops(pid, project["revision"], {"op": "set_captions", "style": {"font": "Anton", **look}})
    out = _cut_out(env, pid, styled["revision"], styled["document"], 1_270_000, 1_380_000)
    r = env.post(f"/projects/{pid}/export", {"revision": out["revision"], "preset": "original"})
    assert r.status_code == 200, r.text
    assert r.json()["branch"] == "B" and r.json()["reasons"] == ["Words light up on a clip that is cut or sped up."]
    env.lane.run_pending()
    assert env.lane.get(r.json()["export"]["export_id"])["status"] == "done" and not stub_rerender
    # Not burned: nothing lights up in the picture, so today's re-render stays.
    r = env.post(f"/projects/{pid}/export", {"revision": out["revision"], "preset": "original", "captions": "none"})
    assert r.status_code == 200 and r.json()["branch"] == "A", r.text


def test_an_srt_with_no_captions_says_so_and_the_export_stands(env, no_render):
    _clip_id, project = clip_project(env)            # no transcript: no caption lines
    changed(env, project)
    r = env.post(f"/projects/{project['id']}/export", {"srt": True})
    env.lane.run_pending()
    job = env.lane.get(r.json()["export"]["export_id"])
    assert job["status"] == "done" and job["result"]["srt"] is None and "no captions" in job["result"]["srt_error"]


@pytest.mark.parametrize("body", [{"captions": "maybe"}, {"captions": None}, {"srt": "yes"}, {"srt": 1}])
def test_the_export_refuses_bad_caption_choices(env, body):
    _clip_id, project = clip_project(env)
    assert env.post(f"/projects/{project['id']}/export", body).status_code == 422


def test_the_routes_are_in_the_contract():
    doc = Path(__file__).resolve().parent.parent / "docs" / "editor-suite" / "api" / "step9-captions.md"
    text = doc.read_text(encoding="utf-8")
    for route in ("GET /projects/{id}/captions.ass", "GET /projects/{id}/captions",
                  "POST /projects/{id}/captions/source_times", "POST /projects/{id}/export"):
        assert route in text, route
    for field in ("captions", "srt", "srt_file", "srt_error", "captions_ass", "x", "y"):
        assert f"`{field}`" in text, field
