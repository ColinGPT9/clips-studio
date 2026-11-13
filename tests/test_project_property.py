"""Editor suite step 5: random operations on random projects.

A seeded generator (tests/project_helpers.py random_op) draws operations of
every kind, some of them wrong on purpose. Whatever comes out:
- the project stays valid, V1 gapless and on the frame grid, ids unique;
- an operation that is refused changes nothing;
- each inverse gives back the document from before, byte for byte, and each
  forward step the document from after;
- all the inverses, last first, give back the first document's bytes;
- the store's undo and redo land on the same bytes.

Standard library only: runs on the CI light install.
"""

import random

import pytest
from project_helpers import ENV, OP_NAMES, RATES, main_items, names_in, on_grid, project, random_op

from video_editor.project import model, ops, store


def _ids(doc: dict) -> list[str]:
    return [item["id"] for t in doc["tracks"] for item in t["items"]]


def _others(doc: dict, but: set) -> dict:
    return {item["id"]: (item["duration_us"], item["source_in_us"], item["source_out_us"])
            for item in main_items(doc) if item["id"] not in but}


def _run(seed: int, rate, count: int):
    """`count` random operations on a project. Returns (the first document's
    text, the last document, every inverse in order, the names applied)."""
    rng = random.Random(seed)
    doc = project(fps=rate, clips=3)
    first = model.canonical(doc)
    inverses, applied = [], set()
    for _ in range(count):
        op = random_op(rng, doc)
        before = model.canonical(doc)
        try:
            result = ops.apply(doc, [op], ENV)
        except ops.OpError as e:
            assert str(e), op
            # A refusal is the caller's doing and says what to change. The
            # last-resort check on the whole document is never the reason:
            # that would be the operations leaving something out of step.
            assert "would leave the project broken" not in str(e), (op, str(e))
            assert model.canonical(doc) == before, f"a refused {op} changed the document"
            continue
        assert model.canonical(doc) == before, f"{op} changed the document it was given"
        new = result.document
        assert model.validate(new) == [], op
        assert on_grid(new), op
        assert len(set(_ids(new))) == len(_ids(new)), op
        if not result.changed:
            assert model.canonical(new) == before
            continue
        assert result.text == model.canonical(new)
        back = ops.apply(new, result.inverse, ENV, internal=True).document
        assert model.canonical(back) == before, f"the inverse of {op} didn't give back the bytes"
        again = ops.apply(doc, result.forward, ENV, internal=True).document
        assert model.canonical(again) == result.text, f"the forward step of {op} gave other bytes"
        if isinstance(op, dict) and op.get("op") == "split_item" and len(result.created) == 1:
            # A cut never moves anything else, nor the end of the video.
            halves = {op["item_id"], result.created[0]["id"]}
            if op["item_id"] in {i["id"] for i in main_items(doc)}:
                assert model.program_end(new) == model.program_end(doc), op
                assert _others(new, halves) == _others(doc, halves), op
        inverses.append(result.inverse)
        applied |= names_in(op)
        doc = new
    return first, doc, inverses, applied


@pytest.mark.parametrize("rate", RATES)
@pytest.mark.parametrize("seed", [11, 23])
def test_random_operations_undo_back_to_the_first_bytes(rate, seed):
    first, doc, inverses, _applied = _run(seed * 1000 + int(rate * 100), rate, 350)
    assert len(inverses) > 60, "too few operations were applied for this to mean anything"
    for inverse in reversed(inverses):
        doc = ops.apply(doc, inverse, ENV, internal=True).document
    assert model.canonical(doc) == first


def test_every_kind_of_operation_comes_up_and_is_applied():
    applied = set()
    for seed in range(6):
        applied |= _run(seed, 30, 400)[3]
    missing = [name for name in OP_NAMES if name not in applied]
    assert not missing, f"the random run never applied {missing}"


@pytest.mark.parametrize("seed", [3, 4, 5])
def test_a_batch_is_all_or_nothing_and_one_undo_step(seed):
    rng = random.Random(seed)
    doc = project(clips=4)
    before = model.canonical(doc)
    good = [{"op": "split_item", "item_id": "i4", "at_us": 2_000_000},
            {"op": "set_item_props", "item_id": "i5", "volume": 0.5},
            {"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": 1_000_000}]
    bad = rng.choice(({"op": "delete_items", "item_ids": ["i999"]}, {"op": "set_item_props", "item_id": "i4", "speed": 9},
                      {"op": "nope"}))
    for position in range(len(good) + 1):
        with pytest.raises(ops.OpError):
            ops.apply(doc, [{"op": "batch", "ops": [*good[:position], bad, *good[position:]]}], ENV)
        assert model.canonical(doc) == before
    result = ops.apply(doc, [{"op": "batch", "ops": good}], ENV)
    assert len(result.inverse) == 1 and len(result.created) == 2
    assert model.canonical(ops.apply(result.document, result.inverse, ENV, internal=True).document) == before
    # The same operations outside a batch: also all or nothing.
    with pytest.raises(ops.OpError):
        ops.apply(doc, [*good, bad], ENV)
    assert model.canonical(doc) == before


@pytest.mark.parametrize("seed", [7, 8])
def test_the_stores_undo_and_redo_land_on_the_same_bytes(db, seed):
    rng = random.Random(seed)
    made = store.create(db.conn, project(fps=29.97, clips=3), now=0)
    project_id, revision = made["id"], made["revision"]
    texts = [model.canonical(made["document"])]
    clock = 0.0
    for _ in range(160):
        clock += 10          # never a moment apart: no two tweaks become one step
        op = random_op(rng, made["document"])
        try:
            out = store.apply_ops(db.conn, project_id, revision, [op], ENV, now=clock)
        except ops.OpError:
            assert model.canonical(store.get(db.conn, project_id)["document"]) == texts[-1]
            continue
        made = out
        if out["changed"]:
            revision = out["revision"]
            texts.append(model.canonical(out["document"]))
    assert len(texts) > 40
    assert model.canonical(store.get(db.conn, project_id)["document"]) == texts[-1]
    for wanted in reversed(texts[:-1]):
        out = store.undo(db.conn, project_id, revision, now=clock)
        revision = out["revision"]
        assert model.canonical(out["document"]) == wanted
        assert model.canonical(store.get(db.conn, project_id)["document"]) == wanted
    with pytest.raises(store.NothingToDo):
        store.undo(db.conn, project_id, revision, now=clock)
    for wanted in texts[1:]:
        out = store.redo(db.conn, project_id, revision, now=clock)
        revision = out["revision"]
        assert model.canonical(out["document"]) == wanted
    with pytest.raises(store.NothingToDo):
        store.redo(db.conn, project_id, revision, now=clock)
    # Every revision can also be rebuilt from its nearest snapshot.
    for seq in (0, 1, 24, 25, 26, len(texts) - 1):
        if seq < len(texts):
            assert model.canonical(store.document_at(db.conn, project_id, seq)) == texts[seq]
