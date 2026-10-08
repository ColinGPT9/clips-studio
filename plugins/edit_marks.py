"""What the creator did with the edits plugins suggested: used, hidden or taken back.

A Suggest edits plugin never changes a clip (plugins/steps.suggest_edits).
Each suggestion waits on the clip's saved scores as one `plugin_edits`
entry, in one of three states:

- "new": not used yet;
- "hidden": put away by the creator (PATCH /clips/{id}), or used once and no
  longer in the clip's saved edit;
- "used": some of it is in the clip's saved edit. Its `applied` says what it
  put there and is still there, so Take it back takes out only that. It may
  carry `remade: true`: a forced re-run has since made the clip's file
  without the saved edit.

after_render() records a render the creator asked for (Apply edits, or
Apply edits & upload). The editor says what each Use added (`applied`), but
only what this render really changed is kept, and only parts of the
suggestion itself, so a client can never record more than it put in.
carry() keeps the creator's decisions when a forced re-run makes the same
suggestions again. clean_used() and clean_state() are the API's checks.

`applied` is {removed, mutes, muted_words, values}, its times in seconds of
the video like the suggestion's own:
- removed: spans the suggestion's cuts took out of the clip;
- mutes: the mute spans it added, each as the clip's edit holds it;
- muted_words: the words inside them it hid in the captions, {start, end, word};
- values: {field: {before, after}} for each of VALUES it changed.

The clip's saved edit (render_opts["edit"]) counts from the clip's start, so
it is read with the window the clip had when it was rendered with it, and
as video_editor/timeline.py reads it.

Standard library only. It is imported only where a clip has suggestions or a
request names one.
"""

from __future__ import annotations

import math

# What one Use may change besides spans: the editor's own fields.
VALUES = ("volume", "fade_in", "fade_out", "speed", "hook", "crop")
# The suggestion's field each value comes from: a plugin's hook title is its title_overlay.
FROM_SUGGESTION = {"volume": "volume", "fade_in": "fade_in", "fade_out": "fade_out", "speed": "speed",
                   "hook": "title_overlay", "crop": "crop"}
# What the render uses when the clip's options leave a value out
# (video_editor/timeline.py; core/pipeline.py frames a clip with "track").
DEFAULTS = {"volume": 1.0, "fade_in": 0.0, "fade_out": 0.0, "speed": 1.0, "hook": None, "crop": "track"}
NUMBERS = {"volume": (0.0, 2.0), "fade_in": (0.0, 3.0), "fade_out": (0.0, 3.0), "speed": (0.5, 3.0)}
MAX_USED = 8            # suggestions one render records
MAX_ID = 32
MAX_REMOVED = 64
MAX_MUTES = 20
MAX_MUTED_WORDS = 500
MAX_WORD = 200
MAX_HOOK_TEXT = 120
MAX_CROP = 32
MIN_PIECE = 0.25        # the render drops a kept piece shorter than this (video_editor/timeline.py)
NEAR = 0.01             # seconds: two times this close are the same time
SAME = 0.001            # two values this close are the same value


# ---- spans: sorted, merged (start, end) pairs in seconds -------------------------------


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _pair(raw) -> tuple[float, float] | None:
    """One [start, end] pair of finite numbers, or None."""
    if isinstance(raw, (list, tuple)) and len(raw) == 2 and _number(raw[0]) and _number(raw[1]):
        return float(raw[0]), float(raw[1])
    return None


def spans(raw) -> list[tuple[float, float]]:
    """[start, end] pairs as sorted, merged spans longer than NEAR, as the
    render cleans them; anything that isn't a pair of numbers is left out."""
    pairs = sorted(p for p in (_pair(r) for r in raw or []) if p and p[1] - p[0] > NEAR)
    merged: list[tuple[float, float]] = []
    for a, b in pairs:
        if merged and a <= merged[-1][1] + NEAR:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def intersect(a, b) -> list[tuple[float, float]]:
    """The parts of spans `a` that lie inside spans `b`."""
    out = []
    for s, e in spans(a):
        for t, u in spans(b):
            lo, hi = max(s, t), min(e, u)
            if hi - lo > NEAR:
                out.append((lo, hi))
    return spans(out)


def minus(a, b) -> list[tuple[float, float]]:
    """The parts of spans `a` outside spans `b`."""
    out = []
    for s, e in spans(a):
        pieces = [(s, e)]
        for t, u in spans(b):
            rest = []
            for p, q in pieces:
                if u <= p or t >= q:
                    rest.append((p, q))
                    continue
                if t > p:
                    rest.append((p, t))
                if u < q:
                    rest.append((u, q))
            pieces = rest
        out += pieces
    return spans(out)


def _inside(span, theirs, *, middle: bool = False) -> bool:
    """Whether `span` (or only its middle) lies inside one of the spans `theirs`."""
    a, b = span
    if middle:
        a = b = (a + b) / 2
    return any(a >= t - NEAR and b <= u + NEAR for t, u in theirs)


def _out(pairs) -> list[list[float]]:
    return [[round(a, 3), round(b, 3)] for a, b in pairs]


# ---- what a render with given options does to the clip ----------------------------------


def _hook(raw) -> dict | None:
    """A hook title as the render reads it (EditList.from_dict), or None."""
    if not isinstance(raw, dict) or not str(raw.get("text", "")).strip():
        return None
    try:
        seconds = max(1.0, min(10.0, float(raw.get("seconds", 3.0) or 3.0)))
    except (TypeError, ValueError):
        seconds = 3.0
    return {"text": str(raw["text"]).strip()[:MAX_HOOK_TEXT], "seconds": seconds}


def view(opts, window) -> dict:
    """What a render with these options does to the clip at `window` (its
    start and end, in seconds of the video), in seconds of the video:
    {kept, removed, mutes, words, values}. Mutes and words are the edit's own
    entries, unmerged, as the editor toggles them. An edit that keeps
    nothing is one the render uses none of."""
    start, end = float(window[0]), float(window[1])
    opts = opts if isinstance(opts, dict) else {}
    crop = opts.get("crop") if isinstance(opts.get("crop"), str) and opts.get("crop") else DEFAULTS["crop"]
    out = {"kept": [(start, end)], "removed": [], "mutes": [], "words": [],
           "values": {**DEFAULTS, "crop": crop}}
    edit = opts.get("edit")
    if not isinstance(edit, dict):
        return out
    if edit.get("keep"):
        shifted = [(start + p[0], start + p[1]) for p in (_pair(r) for r in edit["keep"]) if p]
        keep = [(a, b) for a, b in intersect(shifted, [(start, end)]) if b - a >= MIN_PIECE]
        if not keep:
            return out  # the render refuses an edit that keeps nothing, and uses none of it
        if not (len(keep) == 1 and keep[0][0] < start + 0.05 and keep[0][1] > end - 0.05):
            out["kept"] = keep  # keeping the whole clip is not a cut
            out["removed"] = minus([(start, end)], keep)
    out["mutes"] = [(start + p[0], start + p[1]) for p in (_pair(r) for r in edit.get("mutes") or []) if p]
    for w in edit.get("muted_words") or []:
        if isinstance(w, dict) and _number(w.get("start")) and _number(w.get("end")):
            out["words"].append((start + float(w["start"]), start + float(w["end"]), str(w.get("word", ""))))
    try:
        numbers = {k: float(edit.get(k, DEFAULTS[k])) for k in NUMBERS}
    except (TypeError, ValueError):
        numbers = {k: DEFAULTS[k] for k in NUMBERS}  # as the render: one bad value, all four default
    for k, (lo, hi) in NUMBERS.items():
        out["values"][k] = max(lo, min(hi, numbers[k]))
    out["values"]["hook"] = _hook(edit.get("hook"))
    return out


def _same(field: str, a, b) -> bool:
    if field in NUMBERS:
        return _number(a) and _number(b) and abs(float(a) - float(b)) <= SAME
    if field == "hook":
        a, b = _hook(a), _hook(b)
        if a is None or b is None:
            return a is b
        return a["text"] == b["text"] and abs(a["seconds"] - b["seconds"]) <= SAME
    return isinstance(a, str) and a == b


def _suggested(edit: dict, field: str):
    """The value the suggestion sets for an editor field, as the editor holds it, or None."""
    value = edit.get(FROM_SUGGESTION[field])
    if field == "hook":
        return _hook(value)
    if field == "crop":
        return value if isinstance(value, str) and value else None
    return float(value) if _number(value) else None


def _same_time(a, b) -> bool:
    return all(abs(x - y) <= NEAR for x, y in zip(a[:2], b[:2])) and a[2:] == b[2:]


def _take(pool: list, item):
    """Remove from `pool` the first entry at the same times as `item`
    (and the same word, for a word), and return it; None when there is none."""
    for i, have in enumerate(pool):
        if _same_time(have, item):
            return pool.pop(i)
    return None


def _added(now: list, was: list) -> list:
    """The entries in `now` that `was` didn't have, counting repeats."""
    pool = list(now)
    for item in was:
        _take(pool, item)
    return pool


# ---- applied: what a used suggestion put in the clip --------------------------------------


def _parts(applied) -> tuple[list, list, list, dict]:
    """A stored or sent `applied`, as (removed, mutes, words, values); anything malformed is left out."""
    applied = applied if isinstance(applied, dict) else {}
    removed = spans(applied.get("removed"))
    mutes = [p for p in (_pair(r) for r in applied.get("mutes") or []) if p and p[1] > p[0]]
    words = [(float(w["start"]), float(w["end"]), str(w.get("word", "")))
             for w in applied.get("muted_words") or []
             if isinstance(w, dict) and _number(w.get("start")) and _number(w.get("end"))]
    values = {k: v for k, v in (applied.get("values") or {}).items()
              if k in VALUES and isinstance(v, dict)} if isinstance(applied.get("values"), dict) else {}
    return removed, mutes, words, values


def _record(removed, mutes, words, values) -> dict:
    out: dict = {}
    if removed:
        out["removed"] = _out(spans(removed))
    if mutes:
        out["mutes"] = _out(mutes)
    if words:
        out["muted_words"] = [{"start": round(s, 3), "end": round(e, 3), "word": w} for s, e, w in words]
    if values:
        out["values"] = values
    return out


def _fresh(entry: dict, applied, was: dict, now: dict) -> dict:
    """What of one Use's `applied` this render really put in the clip: parts
    of the suggestion itself that the saved options didn't have and the
    rendered ones do."""
    edit = entry.get("edit") if isinstance(entry.get("edit"), dict) else {}
    removed, mutes, words, values = _parts(applied)
    # Spans the suggestion cuts, that the saved edit kept and this render takes out.
    removed = intersect(intersect(intersect(removed, spans(edit.get("cuts"))), was["kept"]), now["removed"])
    theirs = spans(edit.get("mutes"))
    added = _added(now["mutes"], was["mutes"])
    mutes = [hit for hit in (_take(added, m) for m in mutes if _inside(m, theirs)) if hit]
    added = _added(now["words"], was["words"])
    words = [hit for hit in (_take(added, w) for w in words if _inside(w[:2], theirs, middle=True)) if hit]
    kept = {}
    for field, change in values.items():
        value = _suggested(edit, field)
        if (value is not None and _same(field, change.get("after"), value)
                and _same(field, now["values"][field], value)
                and not _same(field, was["values"][field], value)):
            kept[field] = {"before": change.get("before"), "after": value}
    return _record(removed, mutes, words, kept)


def _still(applied, now: dict) -> dict:
    """The part of a used suggestion's `applied` the rendered options still hold."""
    removed, mutes, words, values = _parts(applied)
    pool = list(now["mutes"])
    mutes = [hit for hit in (_take(pool, m) for m in mutes) if hit]
    pool = list(now["words"])
    words = [hit for hit in (_take(pool, w) for w in words) if hit]
    values = {k: v for k, v in values.items() if _same(k, now["values"][k], v.get("after"))}
    return _record(intersect(removed, now["removed"]), mutes, words, values)


def _merge(held: dict, fresh: dict) -> dict:
    a, b = _parts(held), _parts(fresh)
    return _record(a[0] + b[0], a[1] + b[1], a[2] + b[2], {**a[3], **b[3]})


def after_render(entries, used, before, after, window, new_window=None) -> tuple[list[dict], list[str]]:
    """A clip's `plugin_edits` after a render the creator asked for, and the
    lines for the job log.

    `used` is the render's suggestions.used: [{id, applied}], what the editor
    says each Use added. `before` is the clip's saved options as they were,
    for the clip at `window` (start, end); `after` the options it was just
    rendered with, at `new_window` when the render moved the clip.

    - A used item keeps only what this render really changed: parts of the
      suggestion's cuts the saved edit kept and this one takes out; its
      mutes and hidden words that this edit has and the saved one didn't;
      and values set to the suggestion's own, which the saved options
      didn't hold. If anything is left, the suggestion is used, with that
      as its `applied`; if not, or its id isn't on the clip, a line says so.
    - Every used suggestion keeps in `applied` only what the rendered
      options still hold (after Take it back, Reset or a hand change), and
      one with nothing left is hidden.
    - `remade` goes: this render used the clip's saved options.
    """
    was = view(before, window)
    now = view(after, new_window or window)
    sent: dict[str, dict] = {}
    for item in used or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            sent.setdefault(item["id"], item.get("applied") if isinstance(item.get("applied"), dict) else {})
    out: list[dict] = []
    lines: list[str] = []
    seen: set = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        entry = {k: v for k, v in entry.items() if k != "remade"}
        sid = entry.get("id")
        who = f"{entry.get('name') or entry.get('plugin') or 'a plugin'}'s suggested edit {sid}"
        held = _still(entry.get("applied"), now) if entry.get("state") == "used" else {}
        if sid in sent and sid not in seen:
            seen.add(sid)
            fresh = _fresh(entry, sent[sid], was, now)
            if not fresh:
                lines.append(f"Not marked as used: none of {who} is in this render")
            held = _merge(held, fresh)
        if held:
            entry.update(state="used", applied=held)
        else:
            entry.pop("applied", None)  # only a used suggestion carries one
            if entry.get("state") == "used":
                entry["state"] = "hidden"
                lines.append(f"Marked as hidden: none of {who} is left in the clip's edit")
        out.append(entry)
    lines += [f"Not marked as used: this clip has no suggested edit {sid}" for sid in sent if sid not in seen]
    return out, lines


def carry(old, new, kept, rendered=None) -> list[dict]:
    """The `plugin_edits` of a clip a forced re-run made again at the same
    window (core/pipeline._register_clip). `old` are the row's entries,
    `new` this run's; `kept` is the row's saved options, which the re-run
    keeps, and `rendered` the options its new file was made with.

    - A new suggestion with the id of an old one takes the creator's
      decision on it: hidden stays hidden, and used stays used, with its
      `applied`. A suggestion that changed at all has a new id and is new.
    - A used suggestion this run didn't make again is kept, so Take it back
      stays; other old ones go, as old ratings do.
    - When the saved options have an edit or a layout the new file wasn't
      made with, every used suggestion gets `remade: true`.
    """
    kept = kept if isinstance(kept, dict) else {}
    rendered = rendered if isinstance(rendered, dict) else {}
    remade = ((kept.get("edit") or None) != (rendered.get("edit") or None)
              or (kept.get("crop") or DEFAULTS["crop"]) != (rendered.get("crop") or DEFAULTS["crop"]))
    earlier = [e for e in old or [] if isinstance(e, dict)]
    decided: dict = {}
    for e in earlier:
        decided.setdefault(e.get("id"), e)
    out: list[dict] = []
    for entry in new or []:
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        before = decided.get(entry.get("id"))
        if (before is not None and entry.get("state", "new") == "new"
                and before.get("state") in ("used", "hidden")):
            entry["state"] = before["state"]
            if before["state"] == "used" and before.get("applied"):
                entry["applied"] = before["applied"]
        out.append(entry)
    made = {e.get("id") for e in out}
    out += [dict(e) for e in earlier if e.get("state") == "used" and e.get("id") not in made]
    for e in out:
        e.pop("remade", None)
        if e.get("state") == "used" and remade:
            e["remade"] = True
    return out


def set_state(entries, sid: str, state: str) -> list[dict] | None:
    """The entries with suggestion `sid` hidden or shown ("hidden" or "new"),
    or None when the clip has no such suggestion. A used one loses its
    `applied` and `remade`: only a used suggestion carries them."""
    entries = [e for e in entries or [] if isinstance(e, dict)]
    if not any(e.get("id") == sid for e in entries):
        return None
    out = []
    for e in entries:
        if e.get("id") == sid:
            e = {k: v for k, v in e.items() if k not in ("applied", "remade")}
            e["state"] = state
        out.append(e)
    return out


# ---- the API's checks --------------------------------------------------------------------


def _is_value(field: str, value) -> bool:
    """Whether `value` is one the editor could hold for `field`, inside the render's own limits."""
    if field in NUMBERS:
        lo, hi = NUMBERS[field]
        return _number(value) and lo <= value <= hi
    if field == "hook":
        return (isinstance(value, dict) and not set(value) - {"text", "seconds"}
                and isinstance(value.get("text"), str) and 0 < len(value["text"].strip()) <= MAX_HOOK_TEXT
                and _number(value.get("seconds", 3)) and 1 <= value.get("seconds", 3) <= 10)
    return isinstance(value, str) and 0 < len(value) <= MAX_CROP


KINDS = {**{k: f"a number from {lo:g} to {hi:g}" for k, (lo, hi) in NUMBERS.items()},
         "hook": f"a hook title {{text, seconds}} of at most {MAX_HOOK_TEXT} characters and 1 to 10 seconds",
         "crop": f"a layout's name of at most {MAX_CROP} characters"}


def _is_word(w) -> bool:
    return (isinstance(w, dict) and set(w) == {"start", "end", "word"} and _number(w["start"])
            and _number(w["end"]) and isinstance(w["word"], str) and len(w["word"]) <= MAX_WORD)


def _clean_applied(applied, where: str) -> dict:
    if not isinstance(applied, dict) or set(applied) - {"removed", "mutes", "muted_words", "values"}:
        raise ValueError(f"{where}: expected {{removed, mutes, muted_words, values}}")
    out: dict = {}
    for key, most in (("removed", MAX_REMOVED), ("mutes", MAX_MUTES)):
        pairs = applied.get(key, [])
        if not isinstance(pairs, list) or len(pairs) > most or not all(_pair(p) for p in pairs):
            raise ValueError(f"{where}.{key}: expected at most {most} [start, end] pairs of seconds")
        if pairs:
            out[key] = [list(_pair(p)) for p in pairs]
    words = applied.get("muted_words", [])
    if not isinstance(words, list) or len(words) > MAX_MUTED_WORDS or not all(_is_word(w) for w in words):
        raise ValueError(f"{where}.muted_words: expected at most {MAX_MUTED_WORDS} {{start, end, word}}, "
                         "in seconds")
    if words:
        out["muted_words"] = [dict(w) for w in words]
    values = applied.get("values", {})
    if not isinstance(values, dict):
        raise ValueError(f"{where}.values: expected {{field: {{before, after}}}}")
    for field, change in values.items():
        if field not in VALUES:
            raise ValueError(f"{where}.values: {field!r} isn't one of {', '.join(VALUES)}")
        if (not isinstance(change, dict) or set(change) - {"before", "after"}
                or not _is_value(field, change.get("after"))
                or not (change.get("before") is None or _is_value(field, change["before"]))):
            raise ValueError(f"{where}.values.{field}: expected {{before, after}}, each {KINDS[field]}")
    if values:
        out["values"] = {k: dict(v) for k, v in values.items()}
    return out


def clean_used(value) -> dict:
    """A render's `suggestions` (RenderIn, RenderFirst), checked:
    {"used": [{"id", "applied"}]}. Raises ValueError with a message that
    starts with "suggestions"."""
    if not isinstance(value, dict) or set(value) - {"used"}:
        raise ValueError("suggestions: expected {used: [{id, applied}]}")
    used = value.get("used", [])
    if not isinstance(used, list):
        raise ValueError("suggestions.used: expected a list of {id, applied}")
    if len(used) > MAX_USED:
        raise ValueError(f"suggestions.used: at most {MAX_USED} suggestions in one render")
    out = []
    for i, item in enumerate(used):
        where = f"suggestions.used[{i}]"
        if not isinstance(item, dict) or set(item) - {"id", "applied"} or "id" not in item:
            raise ValueError(f"{where}: expected {{id, applied}}")
        sid = item["id"]
        if not isinstance(sid, str) or not sid or len(sid) > MAX_ID:
            raise ValueError(f"{where}.id: expected a suggestion's id, at most {MAX_ID} characters")
        if any(o["id"] == sid for o in out):
            raise ValueError(f"{where}.id: {sid} is named twice")
        out.append({"id": sid, "applied": _clean_applied(item.get("applied", {}), f"{where}.applied")})
    return {"used": out}


def clean_state(value) -> tuple[str, str]:
    """A clip patch's `suggestion`, checked: {"id", "state": "hidden" | "new"}.
    Returns (id, state). Raises ValueError with a message that starts with
    "suggestion"."""
    if (not isinstance(value, dict) or set(value) - {"id", "state"} or not isinstance(value.get("id"), str)
            or not value["id"] or len(value["id"]) > MAX_ID):
        raise ValueError("suggestion: expected {id, state}, with a suggestion's id of at most "
                         f"{MAX_ID} characters")
    if value.get("state") not in ("hidden", "new"):
        raise ValueError("suggestion: state must be hidden or new; a suggestion is used by applying it")
    return value["id"], value["state"]
