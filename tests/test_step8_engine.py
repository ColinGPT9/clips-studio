"""Editor suite step 8, part A: the engine fixes the new editor needs, tested
without FastAPI (they run on the CI light install). The route tests are in
tests/test_step8_routes.py.

Step 6's small faults (docs/editor-suite/api/step6-media.md):
- a Range with thousands of digits is ignored or refused, never a 500;
- If-Range takes the ETag only, never a date;
- the cache never looks through a link or a junction: Clear, the survey and
  Storage's clean-up leave what it points at;
- the peaks job stops FFmpeg when reading fails, instead of waiting on it;
- `video:VID1` doesn't serve the download of `vid1`;
- a clip's row that names a file that isn't a video, or a hard link, is not
  served;
- a cache folder made from a file that was replaced is an orphan, even when
  a project names the source;
- an import records what was copied, read again before it is kept.

Step 7's (docs/editor-suite/api/step7-compiler.md):
- a long AI run is cut in pieces held to the command-line budget, and
  joined before it is framed;
- an unchanged clip's export carries Apply's 0.4 s end pad (Branch B);
- an export whose file a later one replaced is marked "replaced".

Step 5's edge-fade note: deleting V1's first or last item moves the video's
fade to the new first or last item.

Step 8's own engine parts: the projects list (store.summaries), and the
clip whose file is a project export (StateDB.project_export_of).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest
from media_helpers import add_project, add_video
from test_compose_plan import FACTS, apply, from_clip, manual, plan_for, v1
from test_compose_plan import media_files as plan_media

from core.state import StateDB
from video_editor import compose
from video_editor.media import cache, fileio, importing, peaks, resolver
from video_editor.project import importer, model, ops, store

NOW = "2026-11-14T09:00:00"


# ---- Range and If-Range ------------------------------------------------------------------------


@pytest.mark.parametrize("header, expected", [
    ("bytes=" + "9" * 5000 + "-", "416"),
    ("bytes=0-" + "9" * 5000, (0, 99)),
    ("bytes=-" + "9" * 5000, (0, 99)),
    ("bytes=" + "0" * 5000 + "5-9", (5, 9)),
    ("bytes=10-" + "0" * 4999 + "1", None),            # an end before the start: ignored
])
def test_a_range_with_thousands_of_digits_is_never_an_error(header, expected):
    if expected == "416":
        with pytest.raises(fileio.Unsatisfiable):
            fileio.parse_range(header, 100)
    else:
        assert fileio.parse_range(header, 100) == expected


def test_if_range_takes_the_etag_only():
    tag, modified = fileio.etag(10, 5_000_000_000), fileio.last_modified(5_000_000_000)
    assert fileio.if_range(tag, tag, modified)
    assert not fileio.if_range(modified, tag, modified)        # a date: the whole file
    assert fileio.if_range(None, tag, modified)


# ---- links and junctions in the cache ------------------------------------------------------------


def _link_folder(link: Path, target: Path) -> None:
    """A junction on Windows (what other software makes), a symbolic link
    elsewhere."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            pytest.skip("can't make a link here")


def test_the_cache_never_reaches_through_a_link_or_junction(tmp_path):
    data = tmp_path / "data"
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"x" * 1000)
    base = cache.root(data)
    folder = base / ("a" * 64)
    folder.mkdir(parents=True)
    (folder / "peaks.bin").write_bytes(b"p" * 10)
    _link_folder(folder / "thumbs", outside)            # inside a folder
    _link_folder(base / ("b" * 64), outside)            # among the folders
    assert cache.is_link(folder / "thumbs") and cache.is_link(base / ("b" * 64))

    assert cache.survey(data) == {"folders": 2, "files": 1, "bytes": 10}
    assert cache.walk_files(folder) == [folder / "peaks.bin"]
    assert cache.orphans(data, {}) == [folder]           # the link itself is never an orphan
    cache.enforce_cap(data, 0)
    assert (outside / "keep.txt").is_file()
    out = cache.clear(data)
    assert (outside / "keep.txt").read_bytes() == b"x" * 1000
    assert out["bytes_freed"] == 0 or out["bytes_freed"] <= 10
    assert not folder.exists() and not (base / ("b" * 64)).exists()


def test_storage_clean_up_leaves_a_junctions_target(tmp_path):
    from core import housekeeping

    data = tmp_path / "data"
    db = StateDB(data / "state.db")
    try:
        outside = tmp_path / "precious"
        outside.mkdir()
        (outside / "keep.txt").write_bytes(b"x" * 1000)
        folder = cache.root(data) / ("c" * 64)
        folder.mkdir(parents=True)
        cache.write_json(folder / cache.SOURCE, {"ref": "video:gone", "path": str(tmp_path / "gone.mp4"),
                                                 "size": 1, "mtime_ns": 1})
        _link_folder(folder / "thumbs", outside)
        found = housekeeping.survey(db, data)
        assert all(outside not in p.parents for p in found["_groups"]["editor_cache"])
        housekeeping.clean(db, data)
        assert (outside / "keep.txt").is_file() and not folder.exists()
    finally:
        db.conn.close()


# ---- the peaks job on a read error --------------------------------------------------------------


class _StuckFFmpeg:
    """An FFmpeg that keeps writing until it is killed."""

    def __init__(self):
        self.killed = threading.Event()
        me = self

        class Out:
            def read(self, n):
                return b"\0" * 64

            def close(self):
                pass

        class Err:
            def read(self):
                me.killed.wait(10)
                return b""

            def close(self):
                pass

        self.stdout, self.stderr = Out(), Err()

    def kill(self):
        self.killed.set()

    def wait(self):
        if not self.killed.wait(10):
            raise AssertionError("waited on an FFmpeg that was never stopped")
        return -9


def test_the_peaks_job_stops_ffmpeg_when_reading_fails(monkeypatch):
    proc = _StuckFFmpeg()

    def broken(self, data):
        raise ValueError("a bin that can't be made")

    monkeypatch.setattr(peaks.Builder, "feed", broken)
    with pytest.raises(ValueError, match="can't be made"):
        peaks.compute("x.mp4", popen=lambda *a, **k: proc, duration_us=1_000_000)
    assert proc.killed.is_set()


# ---- the resolver -----------------------------------------------------------------------------


@pytest.fixture
def lib(tmp_path):
    data = tmp_path / "data"
    db = StateDB(data / "state.db")
    (data / "downloads").mkdir()
    yield db, data
    db.conn.close()


def test_an_id_in_other_case_never_serves_another_videos_download(lib):
    db, data = lib
    add_video(db.conn, "vid1")
    (data / "downloads" / "vid1.mp4").write_bytes(b"one")
    assert resolver.resolve(db.conn, data, "video:vid1").path.name == "vid1.mp4"
    with pytest.raises(resolver.NotFound):
        resolver.resolve(db.conn, data, "video:VID1")


def test_a_clip_row_naming_a_file_that_is_no_video_is_refused(lib):
    db, data = lib
    add_video(db.conn, "v1")
    clip_id = db.add_clip("v1", 1.0, 2.0, 80, "hook", path=str(data / "state.db"))
    with pytest.raises(resolver.NotFound, match="not a video"):
        resolver.resolve(db.conn, data, f"clip:{clip_id}")


def test_a_clip_row_naming_a_hard_link_is_refused(lib, tmp_path):
    db, data = lib
    add_video(db.conn, "v1")
    folder = data / "clips" / "someone"
    folder.mkdir(parents=True)
    real = folder / "real.mp4"
    real.write_bytes(b"clip")
    clip_id = db.add_clip("v1", 1.0, 2.0, 80, "hook", path=str(real))
    assert resolver.resolve(db.conn, data, f"clip:{clip_id}").path == real.resolve()
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"not ours")
    linked = folder / "linked.mp4"
    try:
        os.link(secret, linked)
    except OSError:
        pytest.skip("this drive can't make hard links")
    other = db.add_clip("v1", 3.0, 4.0, 80, "hook", path=str(linked))
    with pytest.raises(resolver.NotFound, match="hard link"):
        resolver.resolve(db.conn, data, f"clip:{other}")


# ---- orphans: a replaced source ------------------------------------------------------------------


def _folder(data: Path, name: str, ref: str, source: Path, *, same: bool) -> Path:
    folder = cache.root(data) / name
    folder.mkdir(parents=True)
    st = source.stat()
    cache.write_json(folder / cache.SOURCE, {"ref": ref, "path": str(source),
                                             "size": st.st_size if same else st.st_size + 1,
                                             "mtime_ns": st.st_mtime_ns})
    (folder / "peaks.bin").write_bytes(b"p")
    return folder


def test_a_folder_of_a_replaced_file_is_an_orphan_even_when_a_project_names_it(tmp_path):
    data = tmp_path / "data"
    source = tmp_path / "vid1.mp4"
    source.write_bytes(b"new file")
    current = _folder(data, "1" * 64, "video:vid1", source, same=True)
    replaced = _folder(data, "2" * 64, "video:vid1", source, same=False)
    asset = _folder(data, "3" * 64, "asset:" + "3" * 64, source, same=False)
    gone_named = _folder(data, "4" * 64, "video:vid2", source, same=True)
    (cache.root(data) / ("4" * 64) / cache.SOURCE).write_text(json.dumps(
        {"ref": "video:vid2", "path": str(tmp_path / "missing.mp4"), "size": 1, "mtime_ns": 1}), encoding="utf-8")
    named = {"video": {"vid1", "vid2"}, "asset": {"3" * 64}}
    found = cache.orphans(data, named)
    assert replaced in found
    assert current not in found and asset not in found and gone_named not in found


# ---- import: the copy is read again ------------------------------------------------------------


def test_an_import_records_what_was_copied(lib, tmp_path):
    db, data = lib
    source = tmp_path / "holiday.mp4"
    source.write_bytes(b"video bytes")
    seen = []

    def check(copy):
        seen.append(Path(copy).read_bytes())
        assert Path(copy).suffix == ".mp4"           # the reader sees the file's own extension
        return {"kind": "video", "facts": {"duration_us": 9_000_000, "width": 640, "height": 360,
                                           "fps": {"num": 25, "den": 1}, "has_audio": False}}

    row = store.add_asset(db.conn, data, source, kind="audio", facts={"duration_us": 1}, check=check)
    assert seen == [b"video bytes"]
    assert row["kind"] == "video" and row["duration_us"] == 9_000_000 and row["fps_num"] == 25


def test_an_import_whose_copy_no_longer_reads_keeps_nothing(lib, tmp_path):
    db, data = lib
    source = tmp_path / "holiday.mp4"
    source.write_bytes(b"half written")

    def check(copy):
        raise importing.Refused("This isn't a video, a sound or a picture Clips Kitty can read.")

    with pytest.raises(importing.Refused):
        store.add_asset(db.conn, data, source, kind="video", check=check)
    assert db.conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 0
    assert [p for p in store.assets_dir(data).rglob("*") if p.is_file()] == []


def test_importing_a_real_file_reads_the_copy(lib, tmp_path):
    from media_helpers import make_video

    db, data = lib
    path = make_video(tmp_path / "in.mp4", seconds=1)
    info = importing.facts_of(path)
    calls = []
    real = importing.checked_copy
    importing_check = lambda copy: calls.append(copy) or real(copy)     # noqa: E731
    try:
        importing.checked_copy = importing_check
        out = importing.import_file(db.conn, data, path, info)
    finally:
        importing.checked_copy = real
    assert out["kind"] == "video" and out["duration_us"] > 0
    assert len(calls) == 1 and Path(calls[0]).parent == store.assets_dir(data)


# ---- edge fades -------------------------------------------------------------------------------


def _faded_clip() -> dict:
    """A clip of three parts with the clip's fade in and fade out."""
    return from_clip({"edit": {"keep": [[0, 2], [3, 5], [6, 9]], "fade_in": 0.5, "fade_out": 1.0}})


def test_deleting_the_first_item_moves_the_videos_fade_in():
    doc = _faded_clip()
    first, second = v1(doc)[0], v1(doc)[1]
    assert first["fades"]["in_us"] == 500_000 and second["fades"] == {"in_us": 0, "out_us": 0}
    result = ops.apply(doc, [{"op": "delete_items", "item_ids": [first["id"]]}], _env())
    head = model.track(result.document, model.MAIN_VIDEO)["items"][0]
    assert head["id"] == second["id"] and head["fades"]["in_us"] == 500_000
    assert any("fade in moved" in w for w in result.warnings)


def test_deleting_the_last_item_moves_the_videos_fade_out():
    doc = _faded_clip()
    last, before = v1(doc)[-1], v1(doc)[-2]
    result = ops.apply(doc, [{"op": "delete_items", "item_ids": [last["id"]]}], _env())
    tail = model.track(result.document, model.MAIN_VIDEO)["items"][-1]
    assert tail["id"] == before["id"] and tail["fades"]["out_us"] == 1_000_000


def test_a_moved_fades_notice_names_no_internal_id():
    """The notice the editor shows says which part in plain words."""
    doc = _faded_clip()
    first, last = v1(doc)[0], v1(doc)[-1]
    result = ops.apply(doc, [{"op": "delete_items", "item_ids": [first["id"], last["id"]]}], _env())
    assert result.warnings == ["The video's fade in moved to the part that is first now.",
                               "The video's fade out moved to the part that is last now."]
    ids = {i["id"] for t in doc["tracks"] for i in t["items"]}
    assert not any(i in w.split() or f"item {i}" in w for w in result.warnings for i in ids)


def test_an_items_own_fade_is_never_overwritten_and_a_middle_item_moves_nothing():
    doc = _faded_clip()
    first, second, third = v1(doc)
    doc = apply(doc, {"op": "set_item_props", "item_id": second["id"], "fades": {"in_us": 200_000, "out_us": 0}})
    result = ops.apply(doc, [{"op": "delete_items", "item_ids": [first["id"]]}], _env())
    assert model.track(result.document, model.MAIN_VIDEO)["items"][0]["fades"]["in_us"] == 200_000
    assert not result.warnings
    middle = ops.apply(_faded_clip(), [{"op": "delete_items", "item_ids": [second["id"]]}], _env())
    items = model.track(middle.document, model.MAIN_VIDEO)["items"]
    assert items[0]["fades"]["in_us"] == 500_000 and items[-1]["fades"]["out_us"] == 1_000_000
    assert not middle.warnings


def test_a_moved_fade_is_shortened_to_fit_the_item():
    doc = from_clip({"edit": {"keep": [[0, 0.5], [1, 3]], "fade_out": 2.0}})
    _first, last = v1(doc)
    assert last["fades"]["out_us"] == 2_000_000
    result = ops.apply(doc, [{"op": "delete_items", "item_ids": [last["id"]]}], _env())
    kept = v1(result.document)[0]
    assert kept["fades"]["out_us"] == kept["duration_us"] < 2_000_000


def _env():
    from test_compose_plan import ENV

    return ENV


# ---- the compiler: long AI runs ---------------------------------------------------------------


def _long_ai_run(n: int) -> dict:
    """A clip of n parts of one video, each 1 s with a 0.5 s gap, at 0.5x:
    one AI run (same framing, speed and order), two inputs per item."""
    keep = [[i * 1.5, i * 1.5 + 1.0] for i in range(n)]
    return from_clip({"edit": {"keep": keep, "speed": 0.5}, "crop": "track"}, start=100.0, end=100.0 + n * 1.5)


def test_a_long_ai_run_is_cut_in_pieces_and_joined_before_it_is_framed(tmp_path):
    doc = _long_ai_run(40)
    plan = plan_for(doc, tmp_path)
    ids = [s.id for s in plan.stages]
    cuts = [s for s in plan.stages if s.id.startswith("cut-001-")]
    assert len(cuts) >= 3 and "join-001" in ids
    assert ids.index("join-001") < ids.index("frame-001") < ids.index("conform-001")
    for stage in cuts:
        assert sum(1 for a in stage.argv if a == "-i") <= 2 * compose.MAX_ITEM_INPUTS
        assert compose.cmdline_length(stage.argv) <= compose.ARGV_BUDGET
    join = next(s for s in plan.stages if s.id == "join-001")
    frame = next(s for s in plan.stages if s.id == "frame-001")
    assert join.writes == frame.reads
    # Every piece's sound joins A1, in order; the frame stage tracks the whole run as one stretch.
    listing = next(text for path, text in plan.stages[-1].scripts.items() if path.endswith("a1.txt"))
    assert [line.split("a-001-")[1][:2] for line in listing.splitlines()] == [f"{k:02d}" for k in
                                                                              range(1, len(cuts) + 1)]
    assert len(frame.params["tracking_key"]["spans"]) == 40


def test_a_long_ai_run_in_a_deep_folder_stays_under_windows_limit(tmp_path):
    deep = tmp_path.joinpath(*["d" * 40] * 5)
    doc = _long_ai_run(64)
    plan = plan_for(doc, deep)
    for stage in plan.stages:
        for argv in stage.commands:
            assert compose.cmdline_length(argv) <= compose.ARGV_BUDGET


def test_a_short_ai_run_is_one_cut_as_before(tmp_path):
    plan = plan_for(_long_ai_run(5), tmp_path)
    ids = [s.id for s in plan.stages]
    assert "cut-001" in ids and not any(i.startswith("join-0") for i in ids)


# ---- the compiler: Apply's end pad --------------------------------------------------------------


def test_an_unchanged_clip_exports_as_long_as_apply_makes_it(tmp_path):
    doc = from_clip({"crop": "track"})                      # 100 to 110 s
    plan = plan_for(doc, tmp_path)
    assert plan.program_us == 10_400_000                    # 10 s and Apply's 0.4 s
    assert compose.end_pad_us(doc) == compose.END_PAD_US
    # The draft is the export's program, tracked with the same key; the
    # document itself (the editor's timeline) is unchanged.
    draft = plan_for(doc, tmp_path, purpose="draft")
    assert draft.program_us == 10_400_000
    key = next(s for s in plan.stages if s.kind == "frame").params["tracking_key"]
    assert next(s for s in draft.stages if s.kind == "frame").params["tracking_key"] == key
    assert model.program_end(doc) == 10_000_000
    # A still finds that same run's tracking, at any time of the timeline.
    inputs = compose.Inputs(work=tmp_path / "w", media=plan_media(doc, tmp_path))
    assert compose.still_run(doc, inputs, 9_900_000)[0]["tracking_key"] == key


@pytest.mark.parametrize("change", ["cut", "trimmed end", "whole", "manual", "other video"])
def test_no_end_pad_where_apply_has_none(change, tmp_path):
    if change == "cut":
        doc = from_clip({"edit": {"keep": [[0, 4], [5, 10]]}})
    elif change == "trimmed end":
        doc = from_clip({})
        item = v1(doc)[-1]
        doc = apply(doc, {"op": "trim_item", "item_id": item["id"], "source_out_us": item["source_out_us"] - 1_000_000})
    elif change == "whole":
        doc = from_clip({"whole": True})
    elif change == "manual":
        doc = manual((10, 14, 1.0))
    else:
        doc = from_clip({})
        doc = apply(doc, {"op": "add_media", "video_id": "video-b"})
        doc = apply(doc, {"op": "insert_clip", "media_id": "m2" if "m2" in doc["media"] else
                          sorted(doc["media"])[-1], "source_in_us": 0, "source_out_us": 1_000_000})
    assert compose.end_pad_us(doc) == 0
    assert plan_for(doc, tmp_path).program_us == model.program_end(doc)


def test_the_end_pad_is_held_to_stop_by_and_the_videos_end():
    assert compose.end_pad_us(from_clip({"stop_by": 10.1})) == 100_000
    assert compose.end_pad_us(from_clip({"stop_by": 5.0})) == compose.END_PAD_US    # moved past by hand
    short = importer.import_clip({"video_id": "video-a", "start": 100.0, "end": 110.0, "render_opts": {}},
                                 {**FACTS, "duration_us": 110_200_000})
    assert compose.end_pad_us(short) == 200_000


def test_the_end_pad_captions_nothing_new_and_music_fills_it(tmp_path):
    words = [{"start": 109.0, "end": 109.5, "word": "last"}, {"start": 110.1, "end": 110.3, "word": "after"}]
    segments = [{"start": 109.0, "end": 110.3, "text": "last after", "words": words}]
    doc = from_clip({"captions": True, "edit": {"music": {"library": "music-doodles", "volume": 0.2}}})
    padded = compose.with_end_pad(doc, compose.end_pad_us(doc))
    music = model.track(padded, "A2")["items"][0]
    assert music["start_us"] + music["duration_us"] == model.program_end(padded) == 10_400_000
    inputs = compose.Inputs(work=tmp_path, media={}, segments=segments, language="en")
    text, _fonts = compose.build_ass(padded, inputs, (1080, 1920), caption_doc=doc)
    assert "last" in text.lower() and "after" not in text.lower()
    # Without the caption document, the pad's word would be captioned: the
    # parameter is what keeps it out.
    unpadded_spans, _ = compose.build_ass(padded, inputs, (1080, 1920))
    assert "after" in unpadded_spans.lower()


def _clip_with_layers() -> dict:
    """An unchanged clip's project (100 to 110 s, 10 s on the timeline) with
    a picture and a video over the whole program, a picture that ends at
    6 s, and text over the whole program."""
    doc = from_clip({})
    doc = apply(doc, {"op": "add_media", "asset": "image-one"}, {"op": "add_track", "kind": "video"},
                {"op": "add_track", "kind": "video"}, {"op": "add_track", "kind": "text"})
    pic = next(k for k, m in doc["media"].items() if m["kind"] == "image")
    tracks = [t["id"] for t in doc["tracks"] if t["kind"] == "video" and t["id"] != model.MAIN_VIDEO]
    return apply(doc,
                 {"op": "insert_clip", "media_id": pic, "source_in_us": 0, "source_out_us": 10_000_000,
                  "track_id": tracks[0], "start_us": 0},
                 {"op": "insert_clip", "media_id": pic, "source_in_us": 0, "source_out_us": 2_000_000,
                  "track_id": tracks[1], "start_us": 4_000_000},
                 {"op": "insert_clip", "media_id": doc["context"]["media_id"],
                  "source_in_us": FACTS["duration_us"] - 2_000_000, "source_out_us": FACTS["duration_us"],
                  "track_id": tracks[1], "start_us": 8_000_000},
                 {"op": "add_text", "content": "Follow for more", "start_us": 0, "duration_us": 10_000_000})


def test_the_end_pad_covers_every_layer_that_ran_to_the_end(tmp_path):
    """Fix 8-D10 (the round-3 review's test_t2_pad_overlay.py): the pad is
    the whole program's. A picture, a video and text laid to the end the
    timeline shows run to the export's end; a picture that ends earlier
    keeps its end; a video whose source has no more frames holds its last."""
    doc = _clip_with_layers()
    assert compose.end_pad_us(doc) == compose.END_PAD_US
    padded = compose.with_end_pad(doc, compose.end_pad_us(doc))
    end = model.program_end(padded)
    assert end == 10_400_000
    layers = {(t["id"], i["start_us"]): i for t in padded["tracks"] if t["id"] != model.MAIN_VIDEO
              for i in t["items"]}
    ends = {k: i["start_us"] + i["duration_us"] for k, i in layers.items()}
    over, short, tail = (k for k in sorted(ends) if k[0] != "T1")
    assert ends[over] == end and ends[("T1", 0)] == end
    assert ends[short] == 6_000_000                           # ended before the end: as it was
    assert ends[tail] == end                                  # the video at its source's last frame
    assert layers[tail]["source_out_us"] == FACTS["duration_us"]
    # The document (the editor's timeline) is unchanged.
    assert all(i["start_us"] + i["duration_us"] in (10_000_000, 6_000_000)
               for t in doc["tracks"] if t["id"] != model.MAIN_VIDEO for i in t["items"])
    plan = plan_for(doc, tmp_path)
    assert plan.program_us == end
    final = next(s for s in plan.stages if s.id == "final")
    graph = next(v for k, v in final.scripts.items() if k.endswith("final.graph.txt"))
    assert graph.count("tpad=stop_mode=clone:stop_duration=") == 1      # only the video past its source's end
    assert "between(t,0,10.399999)" in graph and "between(t,8,10.399999)" in graph
    assert "between(t,4,5.999999)" in graph
    ass = next(v for k, v in final.scripts.items() if k.endswith(".ass"))
    assert "0:00:10.40,Hook,,0,0,0,,Follow for more" in ass


def test_a_caption_that_ends_at_the_end_ends_there_as_apply_draws_it(tmp_path):
    """Captions follow Apply at the end of a clip: Apply builds them from the
    unpadded window, so a line that ends at the clip's end ends there, and
    the pad shows none (as before 8-D10)."""
    words = [{"start": 109.0, "end": 109.5, "word": "almost"}, {"start": 109.6, "end": 110.0, "word": "done"}]
    segments = [{"start": 109.0, "end": 110.0, "text": "almost done", "words": words}]
    doc = from_clip({"captions": True})
    padded = compose.with_end_pad(doc, compose.end_pad_us(doc))
    inputs = compose.Inputs(work=tmp_path, media={}, segments=segments, language="en")
    text, _fonts = compose.build_ass(padded, inputs, (1080, 1920), caption_doc=doc)
    lines = [line for line in text.splitlines() if line.startswith("Dialogue: 0,")]
    assert lines and lines[-1].split(",")[2] == "0:00:10.00"


# ---- export rows ------------------------------------------------------------------------------


def _clip_project(db: StateDB, data: Path, path: Path) -> tuple[int, str]:
    add_video(db.conn, "vid1")
    clip_id = db.add_clip("vid1", 1.0, 4.0, 80, "hook", path=str(path), title="Clip one")
    doc = importer.import_clip({"video_id": "vid1", "start": 1.0, "end": 4.0, "render_opts": {}}, {})
    project = store.create(db.conn, doc, clip_id=clip_id, video_id="vid1")
    return clip_id, project["id"]


def test_an_export_a_later_one_replaced_is_marked(lib):
    db, data = lib
    clip_id, pid = _clip_project(db, data, data / "clips" / "c.mp4")
    first = store.add_export(db.conn, pid, 0, clip_id=clip_id, status="done", branch="B")
    store.set_export(db.conn, first, path="/x/one.mp4", status="done")
    second = store.add_export(db.conn, pid, 0, clip_id=clip_id, status="running", branch="B")
    store.set_export(db.conn, second, path="/x/two.mp4", status="done")
    assert store.mark_replaced(db.conn, ["/x/two.mp4", "/x/one.mp4"], keep=second) == 1
    rows = {r["id"]: r for r in store.exports(db.conn, pid)}
    assert rows[first]["status"] == "replaced" and rows[second]["status"] == "done"
    assert store.mark_replaced(db.conn, [None, ""]) == 0


def test_a_clip_whose_file_is_a_project_export_is_known(lib):
    db, data = lib
    clip_id, pid = _clip_project(db, data, data / "clips" / "c.mp4")
    assert db.project_export_of(clip_id) is None
    export = data / "clips" / "projects" / f"{pid}-r0.mp4"
    row = store.add_export(db.conn, pid, 0, clip_id=clip_id, status="done", branch="B")
    store.set_export(db.conn, row, path=str(export))
    assert db.project_export_of(clip_id) is None                # the clip's file is still its own
    db.set_clip(clip_id, path=str(export))
    assert db.project_export_of(clip_id) == pid
    # A row marked replaced no longer says so, but the file is in the
    # exports folder, named after a project that is there (8-D9).
    store.set_export(db.conn, row, status="replaced")
    assert db.project_export_of(clip_id) == pid
    # Branch A writes the clip's own file, never in the exports folder: its
    # row alone never makes the clip a project export.
    own = data / "clips" / "c.mp4"
    db.set_clip(clip_id, path=str(own))
    a_row = store.add_export(db.conn, pid, 0, clip_id=clip_id, status="done", branch="A")
    store.set_export(db.conn, a_row, path=str(own))
    assert db.project_export_of(clip_id) is None                # Branch A's file is today's render
    assert db.project_export_of(99999) is None


def test_the_clip_basis_is_the_exported_revision_not_the_head(lib):
    """8-D12: after a Branch A export, the project counts as unchanged only
    at the revision the export rendered; an edit made while it ran (a
    later head) is a change of its own. Branch B is never "unchanged"."""
    db, data = lib
    clip_id, pid = _clip_project(db, data, data / "clips" / "c.mp4")
    doc = store.get(db.conn, pid)["document"]
    store.apply_ops(db.conn, pid, 0, [{"op": "set_project_props", "name": "During the export"}], env=_env())
    assert store.get(db.conn, pid)["revision"] == 1
    store.set_clip_basis(db.conn, pid, "after", revision=0)
    assert store.clip_basis(db.conn, pid) == ("after", False)
    store.set_clip_basis(db.conn, pid, "after", revision=1)
    assert store.clip_basis(db.conn, pid) == ("after", True)
    store.set_clip_basis(db.conn, pid, "after b", revision=None)
    assert store.clip_basis(db.conn, pid) == ("after b", False)
    assert doc["name"] != "During the export"


def test_a_clip_whose_file_is_a_deleted_projects_export_is_still_known(lib):
    """8-D9: deleting a project deletes its project_exports rows. The clip
    whose file is its export is still told by the file itself ("": an
    export with no project left), compared as the system compares paths."""
    db, data = lib
    clip_id, pid = _clip_project(db, data, data / "clips" / "c.mp4")
    export = data / "clips" / "projects" / f"{pid}-r3.mp4"
    row = store.add_export(db.conn, pid, 3, clip_id=clip_id, status="done", branch="B")
    store.set_export(db.conn, row, path=str(export))
    db.set_clip(clip_id, path=str(export))
    assert db.project_export_of(clip_id) == pid
    assert store.delete(db.conn, pid)
    assert db.conn.execute("SELECT COUNT(*) FROM project_exports").fetchone()[0] == 0
    assert db.project_export_of(clip_id) == ""
    # Written another way: "." and ".." parts, and other case on Windows.
    other = data / "clips" / "x" / ".." / "projects" / f"{pid}-r3-2.mp4"
    db.set_clip(clip_id, path=str(other).upper() if os.name == "nt" else str(other))
    assert db.project_export_of(clip_id) == ""
    # A name that isn't an export's, in that folder: still an export there.
    db.set_clip(clip_id, path=str(data / "clips" / "projects" / "renamed.mp4"))
    assert db.project_export_of(clip_id) == ""
    # Not directly in that folder, or in another data folder: not an export.
    db.set_clip(clip_id, path=str(data / "clips" / "projects" / "deeper" / f"{pid}-r3.mp4"))
    assert db.project_export_of(clip_id) is None
    db.set_clip(clip_id, path=str(data.parent / "elsewhere" / "clips" / "projects" / f"{pid}-r3.mp4"))
    assert db.project_export_of(clip_id) is None
    db.set_clip(clip_id, path=str(data / "clips" / "c.mp4"))
    assert db.project_export_of(clip_id) is None
    assert store.same_path(db.data_dir(), data)


# ---- the projects list ------------------------------------------------------------------------


def test_the_projects_list(lib):
    db, data = lib
    assert store.summaries(db.conn) == []
    clip_id, from_clip_id = _clip_project(db, data, data / "clips" / "c.mp4")
    made = store.create(db.conn, manual((10, 14, 1.0), width=1080, height=1080), now=2_000_000_000)
    listed = store.summaries(db.conn)
    assert [p["id"] for p in listed] == [made["id"], from_clip_id]         # the last changed first
    hand = listed[0]
    assert hand["origin"] == "manual" and hand["clip_id"] is None and not hand["clip_deleted"]
    assert hand["canvas"] == {"width": 1080, "height": 1080, "fps": {"num": 30, "den": 1}}
    assert hand["duration_us"] == 4_000_000 and hand["items"] == 1 and hand["name"] == "Plan"
    assert listed[1]["origin"] == "clip" and listed[1]["clip_id"] == clip_id and not listed[1]["clip_deleted"]
    assert "document" not in hand

    db.delete_clip(clip_id)
    gone = next(p for p in store.summaries(db.conn) if p["id"] == from_clip_id)
    assert gone["origin"] == "clip" and gone["clip_id"] is None and gone["clip_deleted"]

    db.conn.execute("UPDATE projects SET doc = '{broken' WHERE id = ?", (made["id"],))
    db.conn.commit()
    broken = next(p for p in store.summaries(db.conn) if p["id"] == made["id"])
    assert broken["damaged"] and broken["canvas"] is None
    assert len(store.summaries(db.conn, limit=1)) == 1


def test_a_project_of_a_deleted_clip_still_opens_and_compiles(lib, tmp_path):
    db, data = lib
    clip_id, pid = _clip_project(db, data, data / "clips" / "c.mp4")
    db.delete_clip(clip_id)
    project = store.get(db.conn, pid)
    assert project["clip_id"] is None
    plan = plan_for(project["document"], tmp_path)
    assert plan.stages[-1].id == "final"


def test_add_project_helper_still_matches_the_list(lib):
    db, _data = lib
    pid = add_project(db.conn, {"m1": {"type": "video", "video_id": "vid9"}}, video_id="vid9")
    entry = next(p for p in store.summaries(db.conn) if p["id"] == pid)
    assert entry["video_id"] == "vid9"
