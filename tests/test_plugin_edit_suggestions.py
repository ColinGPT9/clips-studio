"""Using, hiding and taking back an edit a plugin suggested, through the app.

A suggestion waits on its clip (scores.plugin_edits) until the creator uses
it in the editor and applies their edits. These tests go through the routes
the editor calls, POST /clips/{id}/render, Apply edits & upload
(render_first on POST /clips/{id}/publish) and PATCH /clips/{id}, and run
the render job that is queued with the render stubbed out
(tests/test_caption_refit.py does the same), on a throwaway database. The
worker records a suggestion as used only with what the render really put in
the clip (plugins/edit_marks.py, whose cases these share). A forced re-run
never touches the clip's saved edit.

Nothing here starts the worker thread. Quarkbloom Arena is a made-up game.
"""

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "fixtures" / "edit_marks"


def _shared(name: str) -> dict:
    return {c["name"]: c for c in json.loads((CASES / f"{name}.json").read_text(encoding="utf-8"))["cases"]}


_RENDER = _shared("after_render")
_RERUN = _shared("carry")
# Quarkbloom Trimmer's suggestion for the clip at 100-130 s, in seconds of the video.
SUGGESTION = _RENDER["used_keeps_only_what_the_render_changed"]["entries"][0]
SID = SUGGESTION["id"]
# Quarkbloom Framer's, beside it.
FRAMER = _RERUN["rerun_keeps_decision"]["new"][1]
# The creator's own saved edit (a cut at 20-22 s and a 0.3 s fade out), in seconds from the clip's start.
MINE = _RENDER["used_keeps_only_what_the_render_changed"]["before"]
# The same with the suggestion used: its cut, mute, hidden word, fade, hook title and layout.
WITH_IT = _RENDER["used_keeps_only_what_the_render_changed"]["after"]
# What the editor says Use added, in seconds of the video.
APPLIED = _RENDER["nothing_in_the_render_is_not_marked"]["used"][0]["applied"]
# The suggestion once used.
USED = _RENDER["take_it_back"]["entries"][0]
HIDDEN_LINE = f"Marked as hidden: none of Quarkbloom Trimmer's suggested edit {SID} is left in the clip's edit"


class Studio:
    """The app on a data folder with one video, its render job run by hand."""

    def __init__(self, tmp_path, monkeypatch):
        import core.pipeline as pipeline
        from core.state import StateDB
        from main import BUNDLED_CONFIG, load_config
        from server.api import create_app

        settings = tmp_path / "settings.yaml"
        shutil.copy(ROOT / "config" / "settings.yaml", settings)
        self.data_dir = tmp_path / "data"
        self.config = load_config(BUNDLED_CONFIG)
        self.config["paths"]["data_dir"] = str(self.data_dir)
        from fastapi.testclient import TestClient

        # No `with`: startup never runs, so no worker thread starts.
        self.client = TestClient(create_app(self.config, settings), base_url="http://127.0.0.1")
        self.db = StateDB(self.data_dir / "state.db")
        self.db.upsert_video("vid321", title="A Quarkbloom Arena match", channel_name="Quarkbloom Arena")
        (self.data_dir / "downloads").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "downloads" / "vid321.mp4").write_bytes(b"not really a video")
        words = [{"start": 100 + i * 0.5, "end": 100.5 + i * 0.5, "word": f"w{i}"} for i in range(60)]
        (self.data_dir / "transcripts").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "transcripts" / "vid321.json").write_text(json.dumps({
            "language": "en",
            "segments": [{"start": 100.0, "end": 130.0, "text": " ".join(w["word"] for w in words), "words": words}],
        }), encoding="utf-8")
        self.renders: list[dict] = []
        self.fail = False

        def render(_source, candidate, _segments, clip_dir, _config, render_opts, _lang):
            self.renders.append(json.loads(json.dumps(render_opts)))
            if self.fail:
                raise RuntimeError("the render failed")
            clip_dir.mkdir(parents=True, exist_ok=True)
            out = clip_dir / f"clip_{int(candidate.start):05d}-{int(candidate.end):05d}.mp4"
            out.write_bytes(b"a clip")
            return out, json.dumps(render_opts)

        monkeypatch.setattr(pipeline, "_render_files", render)

    def add_clip(self, entries: list, render_opts: dict, start: float = 100.0, end: float = 130.0) -> int:
        return self.db.add_clip("vid321", start, end, 80, "He holds the bridge alone",
                                path=str(self.data_dir / "old.mp4"), status="queued", title="Creator title",
                                scores=json.dumps({"text": 61, "plugin_edits": entries}),
                                render_opts=json.dumps(render_opts))

    def clip(self, start: float = 100.0, end: float = 130.0) -> dict:
        row = dict(self.db.conn.execute("SELECT * FROM clips WHERE video_id = 'vid321' AND start_s = ? AND end_s = ?",
                                        (start, end)).fetchone())
        row["scores"] = json.loads(row["scores"] or "{}")
        row["render_opts"] = json.loads(row["render_opts"] or "{}")
        return row

    def jobs(self) -> int:
        return self.db.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    def payload(self, job_id: int) -> dict:
        return json.loads(self.db.get_job(job_id)["payload"])

    def apply(self, clip_id: int, body: dict) -> dict:
        """POST /clips/{id}/render, then the render job it queued."""
        r = self.client.post(f"/clips/{clip_id}/render", json=body)
        assert r.status_code == 200, r.text
        payload = self.payload(r.json()["job_id"])
        self.render(payload)
        return payload

    def render(self, payload: dict) -> None:
        from server.jobs import Worker

        Worker._rerender_clip(SimpleNamespace(config=self.config), self.db, payload)


@pytest.fixture
def studio(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yaml")
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    pytest.importorskip("yt_dlp")  # sources.dispatch imports the YouTube source
    s = Studio(tmp_path, monkeypatch)
    yield s
    s.db.conn.close()


def _used(applied: dict) -> dict:
    return {"used": [{"id": SID, "applied": applied}]}


def test_a_render_records_used_suggestions_only_when_it_succeeds(studio, capsys):
    cid = studio.add_clip([SUGGESTION], MINE)
    r = studio.client.post(f"/clips/{cid}/render", json={"render_opts": WITH_IT, "suggestions": _used(APPLIED)})
    assert r.status_code == 200, r.text
    payload = studio.payload(r.json()["job_id"])
    assert payload["suggestions"] == _used(APPLIED)

    studio.fail = True
    with pytest.raises(RuntimeError, match="the render failed"):
        studio.render(payload)
    row = studio.clip()
    # A failed render records nothing: the clip, its suggestion and its saved edit are as they were.
    assert row["id"] == cid and row["scores"]["plugin_edits"] == [SUGGESTION] and row["render_opts"] == MINE

    studio.fail = False
    studio.render(payload)
    row = studio.clip()
    assert studio.renders[-1] == row["render_opts"] == WITH_IT
    assert row["scores"] == {"text": 61, "plugin_edits": [USED]}
    assert row["title"] == "Creator title"
    assert "Not marked" not in capsys.readouterr().out


def test_use_then_reset_then_apply_records_nothing_as_used(studio, capsys):
    cid = studio.add_clip([SUGGESTION], MINE)
    # Use, then Reset, then a fade in by hand and Apply: Reset cleared the Use, so none is sent.
    hand = {"edit": {**MINE["edit"], "fade_in": 1}}
    studio.apply(cid, {"render_opts": hand})
    assert studio.clip()["scores"]["plugin_edits"] == [SUGGESTION]
    # A client that still sends the Use records nothing either: none of it is in the render.
    studio.apply(studio.clip()["id"], {"render_opts": {"edit": {**MINE["edit"], "fade_in": 0.5}},
                                       "suggestions": _used(APPLIED)})
    assert studio.clip()["scores"]["plugin_edits"] == [SUGGESTION]
    assert (f"Not marked as used: none of Quarkbloom Trimmer's suggested edit {SID} is in this render"
            in capsys.readouterr().out)
    # A suggestion used before, then Reset and Apply: nothing of it is left, so it is hidden.
    studio.db.set_clip(studio.clip()["id"], scores=json.dumps({"plugin_edits": [USED]}),
                       render_opts=json.dumps(WITH_IT))
    studio.apply(studio.clip()["id"], {"render_opts": {"edit": None, "crop": "track"}})
    assert studio.clip()["scores"]["plugin_edits"] == [{**SUGGESTION, "state": "hidden"}]
    assert HIDDEN_LINE in capsys.readouterr().out


def test_a_mixed_apply_records_only_the_suggestions_parts(studio):
    cid = studio.add_clip([SUGGESTION], {"edit": {"fade_out": 0.3}})
    # One Apply: the creator's own cut at 20-22 s and volume 120%, and the suggestion used.
    mixed = {"edit": {**WITH_IT["edit"], "volume": 1.2}, "crop": "center"}
    # A client that also claims the creator's cut and volume as the suggestion's.
    claimed = {**APPLIED, "removed": [[102.0, 106.5], [120.0, 122.0]],
               "values": {**APPLIED["values"], "volume": {"before": 1, "after": 1.2}}}
    studio.apply(cid, {"render_opts": mixed, "suggestions": _used(claimed)})
    (entry,) = studio.clip()["scores"]["plugin_edits"]
    assert entry == USED  # only the suggestion's own cut, mute, word, fade, hook title and layout
    assert entry["applied"]["removed"] == [[102.0, 106.5]] and "volume" not in entry["applied"]["values"]
    # Take it back, then Apply: the creator's own cut and volume stay, the suggestion is hidden.
    own = {"edit": {"keep": [[0, 20], [22, 30]], "fade_out": 0.3, "volume": 1.2}, "crop": "track"}
    studio.apply(studio.clip()["id"], {"render_opts": own})
    row = studio.clip()
    assert row["scores"]["plugin_edits"] == [{**SUGGESTION, "state": "hidden"}]
    assert row["render_opts"] == own


def test_apply_and_upload_records_used_suggestions(studio):
    from server import youtube_service

    youtube_service.save_settings(studio.db, {"enabled": True})
    cid = studio.add_clip([SUGGESTION], MINE)
    publish = {"title": "Quark burst!", "privacy": "private"}
    # A malformed record is refused before anything is queued.
    r = studio.client.post(f"/clips/{cid}/publish", json={**publish, "render_first": {
        "render_opts": WITH_IT, "suggestions": {"used": "all"}}})
    assert r.status_code == 400 and r.json()["detail"] == "suggestions.used: expected a list of {id, applied}"
    assert studio.jobs() == 0 and not studio.db.active_publish_job_for_clip(cid)

    r = studio.client.post(f"/clips/{cid}/publish", json={**publish, "render_first": {
        "render_opts": WITH_IT, "suggestions": _used(APPLIED)}})
    assert r.status_code == 200, r.text
    payload = studio.payload(r.json()["render_job_id"])
    assert payload["suggestions"] == _used(APPLIED)
    studio.render(payload)
    row = studio.clip()
    assert row["scores"]["plugin_edits"] == [USED] and row["render_opts"] == WITH_IT


def test_make_it_again_renders_the_saved_options_and_clears_remade(studio):
    cid = studio.add_clip([{**USED, "remade": True}, FRAMER], WITH_IT)
    payload = studio.apply(cid, {})  # the card's button: the render route with nothing new
    assert payload == {"clip_id": cid}
    assert studio.renders == [WITH_IT]  # the clip's saved options, as they are
    row = studio.clip()
    assert row["scores"]["plugin_edits"] == [USED, FRAMER] and row["render_opts"] == WITH_IT


def test_patch_hides_and_shows_a_suggestion(studio):
    cid = studio.add_clip([SUGGESTION, FRAMER], MINE)

    def patch(body):
        return studio.client.patch(f"/clips/{cid}", json=body)

    r = patch({"suggestion": {"id": SID, "state": "hidden"}})
    assert r.status_code == 200, r.text
    assert r.json()["scores"] == {"text": 61, "plugin_edits": [{**SUGGESTION, "state": "hidden"}, FRAMER]}
    r = patch({"suggestion": {"id": SID, "state": "new"}})
    assert r.json()["scores"]["plugin_edits"] == [SUGGESTION, FRAMER]
    r = patch({"suggestion": {"id": SID, "state": "used"}})
    assert r.status_code == 400
    assert r.json()["detail"] == "suggestion: state must be hidden or new; a suggestion is used by applying it"
    # A bad suggestion changes nothing else in the same patch.
    r = patch({"title": "Changed", "suggestion": {"id": SID}})
    assert r.status_code == 400 and studio.clip()["title"] == "Creator title"
    # Hiding a used one: what it recorded goes with it. Nothing renders, at any point.
    studio.db.set_clip(cid, scores=json.dumps({"plugin_edits": [{**USED, "remade": True}]}))
    r = patch({"suggestion": {"id": SID, "state": "hidden"}, "title": "Quark burst"})
    assert r.json()["scores"]["plugin_edits"] == [{**SUGGESTION, "state": "hidden"}]
    assert r.json()["title"] == "Quark burst"
    assert studio.jobs() == 0 and studio.renders == []
    assert studio.clip()["render_opts"] == MINE


def test_an_unknown_suggestion_id_is_refused(studio, capsys):
    cid = studio.add_clip([SUGGESTION], MINE)
    other = studio.db.add_clip("vid321", 200.0, 230.0, 70, "h", scores=json.dumps({"text": 50}))
    for clip_id in (cid, other):
        r = studio.client.patch(f"/clips/{clip_id}", json={"suggestion": {"id": "0123456789ab", "state": "hidden"}})
        assert r.status_code == 404 and r.json()["detail"] == "no such suggestion on this clip"
    assert studio.clip()["scores"]["plugin_edits"] == [SUGGESTION]
    # A render's record that isn't one is refused before anything is queued.
    r = studio.client.post(f"/clips/{cid}/render", json={"render_opts": WITH_IT, "suggestions": {"used": [{"id": 5}]}})
    assert r.status_code == 400
    assert r.json()["detail"] == "suggestions.used[0].id: expected a suggestion's id, at most 32 characters"
    assert studio.jobs() == 0
    # One that names an id the clip doesn't have renders, and marks nothing.
    studio.apply(cid, {"render_opts": WITH_IT, "suggestions": {"used": [{"id": "0123456789ab", "applied": {}}]}})
    assert studio.clip()["scores"]["plugin_edits"] == [SUGGESTION]
    assert "Not marked as used: this clip has no suggested edit 0123456789ab" in capsys.readouterr().out


def test_a_rerun_never_touches_the_saved_edit(studio, monkeypatch, tmp_path):
    """A forced re-run of the video (process_video at a window the clip
    already has) keeps the clip's title, status and saved edit, points it at
    a file made without that edit, and carries the creator's decisions on
    the suggestions it makes again (scratchpad map, section 2.3)."""
    import core.pipeline as pipeline
    from analysis.metadata import ClipMetadata
    from core.models import ClipCandidate

    db = studio.db
    meta = ClipMetadata(title="AI title", description="", hashtags=[])
    cid = studio.add_clip([USED, {**FRAMER, "state": "hidden"}], WITH_IT)
    db.set_clip(cid, status="uploaded")
    made = ClipCandidate(100.0, 130.0, 88, "new hook", subscores={"text": 64, "plugin_edits": [SUGGESTION, FRAMER]})
    out = pipeline._register_clip(db, "vid321", made, tmp_path / "clip_00100-00130.mp4", meta, "", {})
    assert out is None
    row = studio.clip()
    assert (row["id"], row["title"], row["status"]) == (cid, "Creator title", "uploaded")
    assert row["render_opts"] == WITH_IT and row["path"] == str(tmp_path / "clip_00100-00130.mp4")
    assert row["scores"] == {"text": 64, "plugin_edits": [{**USED, "remade": True}, {**FRAMER, "state": "hidden"}]}

    # A clip and a run with no suggestions never import plugins.edit_marks, and keep everything as before.
    saved = {"edit": {"keep": [[0, 5], [8, 20]], "speed": 1.5}, "crop": "center"}
    plain = db.add_clip("vid321", 200.0, 220.0, 70, "h", path="/old/clip_00200-00220.mp4", status="uploaded",
                        title="Creator title", scores=json.dumps({"text": 61}), render_opts=json.dumps(saved))
    plugins = sys.modules["plugins"]
    with monkeypatch.context() as m:
        m.setitem(sys.modules, "plugins.edit_marks", None)  # importing it now fails
        m.delattr(plugins, "edit_marks", raising=False)
        again = ClipCandidate(200.0, 220.0, 72, "h", subscores={"text": 64})
        out = pipeline._register_clip(db, "vid321", again, tmp_path / "clip_00200-00220.mp4", meta, "", {})
    assert out is None
    row = studio.clip(200.0, 220.0)
    assert (row["id"], row["title"], row["status"]) == (plain, "Creator title", "uploaded")
    assert row["render_opts"] == saved and row["scores"] == {"text": 64}

    # A clip the creator trimmed (302-318 s) keeps its row; the re-run's window gets one of its own.
    db.add_clip("vid321", 300.0, 320.0, 60, "h", path="/old/clip_00300-00320.mp4", status="queued",
                title="Trimmed", render_opts=json.dumps({"edit": {"fade_in": 1.0}}))
    db.conn.execute("UPDATE clips SET start_s = 302, end_s = 318 WHERE start_s = 300")
    db.conn.commit()
    trimmed = ClipCandidate(300.0, 320.0, 61, "h", subscores={})
    out = pipeline._register_clip(db, "vid321", trimmed, tmp_path / "clip_00300-00320.mp4", meta, "", {})
    assert out is not None
    assert studio.clip(302.0, 318.0)["render_opts"] == {"edit": {"fade_in": 1.0}}
    assert (studio.clip(300.0, 320.0)["title"], studio.clip(300.0, 320.0)["render_opts"]) == ("AI title", {})
