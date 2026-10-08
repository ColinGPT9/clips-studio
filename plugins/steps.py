"""Understand, Rate and Suggest edits: Marketplace plugins that look at a
video's moments once they're found, and at the clips chosen from them.

A job may name, besides how its moments are found (Clips Kitty's own scoring,
Sports, Gaming scoring or a pipeline plugin), up to MAX_PER_STEP plugins that
say what happens in each moment (`understand`), up to MAX_PER_STEP that
give each moment a new score (`rate`) and up to MAX_PER_STEP that suggest an
edit for each clip (`edit`). process_video calls after_finding() between
finding the moments and writing their titles:

- Understanders run first, in the order chosen, then raters. A plugin chosen
  for both runs once, at its place among the raters, asked to do both.
- Each run is handed the moments as they are after the runs before it, so a
  rater sees the earlier notes and scores. Notes stack (at most
  MAX_NOTES_PER_MOMENT for each moment) and go to the title writer
  (analysis/metadata.py); ratings chain, and the last one counts.
- A plugin that can't run, or gives an answer Clips Kitty can't use, is
  skipped: the moments stay as they were, and the report says why in the
  creator's words. Nothing of a refused answer is kept.
- Once every plugin has run, a moment a rater scored under the creator's
  minimum score is set aside (a must-have never is), the rest are put in
  score order with must-haves first, and the creator's clip limit is applied.
  With a rater and a limit, the moments are found from a shortlist of
  RATE_POOL_FACTOR times the limit (shortlist_config), so the raters have
  more to choose from. Understanders never change which clips are made.

Then, still before the titles, process_video calls suggest_edits() with the
clips that will be made. Each edit plugin, in the order chosen, is handed
those clips and what the edit plugins before it suggested, and its fitted
suggestions are kept on the clips. A suggestion changes nothing about a clip:
not which clips are made, their windows, scores or order, nor the options
they are rendered with. It waits for the creator in the editor.

Nothing here is imported for a job that names none of the steps. What a
plugin answered is kept in each clip's subscores: `found_score`,
`plugin_ratings`, `plugin_notes` and `plugin_edits`. The per-video report goes
into the outcome as `steps` (core/outcome.py reads it for the posting code).
"""

from __future__ import annotations

import hashlib
import json

from plugins import runner, store
from plugins._sdk import contract

# Plugins for one step, in a job.
MAX_PER_STEP = 3
# With a rater and a clip limit, how many times the limit is found first.
RATE_POOL_FACTOR = 3
# Notes kept for one moment, from every plugin together.
MAX_NOTES_PER_MOMENT = 8
# Moments handed over in one run, as many as a find run may give.
MAX_MOMENTS = contract.MAX_RANGES
# Clips Kitty's own subscores a plugin is handed as a moment's signals.
SIGNALS = ("text", "audio", "visual", "engagement", "game")
FIELDS = ("understand", "rate", "edit")


def clean(field: str, value) -> list[dict]:
    """A job's `rate`, `understand` or `edit` option, checked for shape: a list of up
    to MAX_PER_STEP plugin choices, each as store.clean_choice cleans the
    `pipeline` option. A single choice or id is a list of one. Raises
    ValueError with a message that starts with the field."""
    if isinstance(value, (str, dict)):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f'{field} must be a plugin or a list of plugins: [{{"id": "publisher/name"}}]')
    if len(value) > MAX_PER_STEP:
        raise ValueError(f"{field}: at most {MAX_PER_STEP} plugins for one step")
    out: list[dict] = []
    for i, item in enumerate(value):
        choice = store.clean_choice(item, what=f"{field}[{i}]")
        if any(c["id"] == choice["id"] for c in out):
            raise ValueError(f"{field}: {choice['id']} is listed twice")
        out.append(choice)
    return out


def check_installed(data_dir, field: str, choices: list[dict]) -> None:
    """That each cleaned choice is installed, turned on, able to run here,
    not blocked, able to do the step and given settings that fit, as
    store.installed_choice checks it. Raises ValueError, its message
    starting with the item (`rate[0]: `)."""
    for i, choice in enumerate(choices):
        try:
            store.installed_choice(data_dir, choice, step=field)
        except ValueError as e:
            raise ValueError(f"{field}[{i}]: {e}") from e


def shortlist_config(config: dict) -> dict:
    """The config the moments are found with. `config` itself, unless the job
    names a rater and a clip limit: then a copy whose `clips` asks for
    RATE_POOL_FACTOR times the limit (at most MAX_MOMENTS), so the raters
    choose the clips from more than the finder would keep. The job's own
    config is never changed."""
    clips = config.get("clips") or {}
    limit = int(clips.get("max_clips_per_video") or 0)
    if not clips.get("rate") or limit <= 0:
        return config
    return {**config, "clips": {**clips, "max_clips_per_video": min(MAX_MOMENTS, limit * RATE_POOL_FACTOR)}}


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def moments_of(moment_id: str, c) -> dict:
    """One found moment (a ClipCandidate) as job.json hands it to a plugin.

    `score` is its score now, after the plugins that rated it before, and
    `found_score` the one it was found with. `found_by` is "clipskitty" (its
    own scoring, Sports and Gaming scoring) or the pipeline that found it.
    `signals` are Clips Kitty's own numeric subscores, and `reaction` only
    when it was measured: the neutral placeholder is no evidence. `title`,
    `reason` and `context` (the notes earlier plugins gave) come from what
    was said; host.build_job leaves them out without transcript.read."""
    s = c.subscores or {}
    signals = {key: s[key] for key in SIGNALS if _number(s.get(key))}
    if s.get("reaction_measured") and _number(s.get("reaction")):
        signals["reaction"] = s["reaction"]
    return {
        "id": moment_id, "start": float(c.start), "end": float(c.end), "score": c.score,
        "found_score": s.get("found_score", c.score), "found_by": str(s.get("plugin") or "clipskitty"),
        "label": str(s.get("plugin_label") or s.get("sport_label") or ""),
        "signals": signals, "title": c.hook or "", "reason": c.reason or "",
        "context": [str(n.get("text") or "") for n in s.get("plugin_notes") or [] if isinstance(n, dict)],
    }


def _choices(value) -> list:
    """A step's choices from the config: the cleaned list the API stores, or
    one choice written by hand in settings.yaml."""
    if not value:
        return []
    return list(value) if isinstance(value, list) else [value]


def _same(a, b) -> bool:
    """The same choice: id, version and settings."""
    try:
        return store.clean_choice(a) == store.clean_choice(b)
    except ValueError:
        return a == b


def plan(understand, rate) -> list[tuple[object, list[str]]]:
    """The runs, in order: (choice, what it is asked). Every understander
    first, then every rater; a choice in both lists runs once, at its place
    among the raters, asked to understand and rate."""
    understand, rate = _choices(understand), _choices(rate)
    runs = [(c, ["understand"]) for c in understand if not any(_same(c, r) for r in rate)]
    runs += [(c, ["understand", "rate"] if any(_same(c, u) for u in understand) else ["rate"]) for c in rate]
    return runs


def _who(choice, data_dir) -> dict:
    """The plugin a choice names, for the log and the report: its id, version
    and name when it is installed, else the id it was chosen by."""
    raw = {"id": choice} if isinstance(choice, str) else choice if isinstance(choice, dict) else {}
    pid = str(raw.get("id") or "?")
    version = str(raw.get("version") or "")
    plugin = store.get(data_dir, pid, version or None) if isinstance(raw.get("id"), str) else None
    if plugin is not None:
        return {"plugin": plugin.id, "version": plugin.version, "name": plugin.name}
    return {"plugin": pid, "version": version, "name": pid}


_LABELS = {("understand",): "Understand", ("rate",): "Rate", ("understand", "rate"): "Understand and rate"}


def after_finding(candidates: list, rejections: list, *, video, segments, language: str, config: dict,
                  data_dir) -> tuple[list, list, list]:
    """Run the job's Understand and Rate plugins on the moments found, then
    choose the clips. Returns (kept, rejections, report).

    The first MAX_MOMENTS candidates are handed over (m1, m2, ... in their
    order); any after them keep their scores. Each run's answers land only
    once the whole answer is checked. Then, if any rating was applied, rated
    moments under the minimum score become `below_min_score` rejections
    (must-haves are kept), and the rest are sorted by (must-have, score);
    without a rating the order is untouched. Last, the creator's clip limit
    cuts the list, the rest becoming `over_limit` rejections.

    The report has one entry for each run: {plugin, version, name, steps, ok,
    given, noted, rated, set_aside, error?}, `error` being why it was
    skipped, in the creator's words. `set_aside` counts the moments whose
    last rating came from that plugin.
    """
    from analysis.intent import is_required
    from core import cancel
    from core.models import Rejection

    clips_cfg = config.get("clips") or {}
    if not candidates:
        return [], list(rejections), []  # no moment run is ever started with no moments
    pool = list(candidates[:MAX_MOMENTS])
    beyond = list(candidates[MAX_MOMENTS:])
    for c in pool:
        # A copy of its own: the finder's dicts are never changed.
        c.subscores = dict(c.subscores or {})
    report: list[dict] = []
    rated_by: dict[int, dict] = {}  # id(candidate): the report entry of its last rating

    for choice, asked in plan(clips_cfg.get("understand"), clips_cfg.get("rate")):
        cancel.check_active()
        who = _who(choice, data_dir)
        named = " ".join(part for part in (who["name"], who["version"]) if part)
        print(f"      {_LABELS[tuple(asked)]}: {named} ({who['plugin']}) on {len(pool)} moment(s)")
        entry = {**who, "steps": list(asked), "ok": True, "given": len(pool), "noted": 0, "rated": 0,
                 "set_aside": 0}
        report.append(entry)
        moments = [moments_of(f"m{i}", c) for i, c in enumerate(pool, 1)]
        try:
            got = runner.answer_moments(choice, asked, moments, video=video, segments=segments,
                                        language=language, config=config, data_dir=data_dir,
                                        stage="understand" if asked == ["understand"] else "ranking")
        except runner.PluginError as e:
            why = e.why or str(e)
            entry.update(ok=False, error=why)
            print(f"      Going on without {who['name']}: {why}")
            print(f"      ({e})")
            continue
        entry.update(plugin=got["plugin"], version=got["version"], name=got["name"])
        by = {"plugin": got["plugin"], "version": got["version"], "name": got["name"]}
        not_kept = 0
        for i, c in enumerate(pool, 1):
            answer = got["answers"].get(f"m{i}")
            if not answer:
                continue
            s = c.subscores
            notes = answer.get("context") or []
            if notes:
                have = list(s.get("plugin_notes") or [])
                room = max(0, MAX_NOTES_PER_MOMENT - len(have))
                not_kept += max(0, len(notes) - room)
                if room:
                    s["plugin_notes"] = have + [{**by, "text": text} for text in notes[:room]]
                    entry["noted"] += 1
            if answer.get("score") is not None:
                score = max(0, min(100, int(round(float(answer["score"])))))
                s.setdefault("found_score", c.score)
                s["plugin_ratings"] = [*(s.get("plugin_ratings") or []),
                                       {**by, "score": score, "reason": answer.get("reason") or ""}]
                c.score = score
                rated_by[id(c)] = entry
                entry["rated"] += 1
        if "understand" in asked:
            print(f"      {got['name']} added notes to {entry['noted']} of {len(pool)} moment(s)")
            if not_kept:
                print(f"      {got['name']}: {not_kept} note(s) not kept: at most {MAX_NOTES_PER_MOMENT} "
                      "for each moment")
        if "rate" in asked:
            print(f"      {got['name']} rated {entry['rated']} of {len(pool)} moment(s)")

    found = pool + beyond
    new: list = []
    if rated_by:
        min_score = int(clips_cfg.get("min_score", 0))
        under = [c for c in pool if id(c) in rated_by and c.score < min_score and not is_required(c)]
        if under:
            for c in under:
                rated_by[id(c)]["set_aside"] += 1
            new += [Rejection(c, "below_min_score") for c in under]
            print(f"      {len(under)} moment(s) rated under the minimum score ({min_score}) set aside")
        set_aside = {id(c) for c in under}
        found = sorted((c for c in found if id(c) not in set_aside),
                       key=lambda c: (is_required(c), c.score), reverse=True)
    limit = int(clips_cfg.get("max_clips_per_video") or 0)
    if limit > 0 and len(found) > limit:
        new += [Rejection(c, "over_limit") for c in found[limit:]]
        found = found[:limit]
    return found, list(rejections) + new, report


# ---- Suggest edits: plugins that suggest an edit for each clip that is made --------------


def crops_for(config: dict) -> tuple[str, ...]:
    """The layouts this job's clips can use, as an edit run's `limits.crops`:
    the timeline editor's three (contract.CROPS) for a standard vertical job,
    none for every other kind. Sports and Podcast give a layout their own
    meaning, Gaming / Reaction keeps its split, and Vertical Live (a Sports
    match filmed 9:16 included) and a job with `vertical` off frame no clip."""
    from core import modes

    clips = config.get("clips") or {}
    if (not clips.get("vertical", True) or modes.is_vertical_live(config) or modes.is_gaming(config)
            or clips.get("podcast") or modes.sport(config)):
        return ()
    return tuple(contract.CROPS)


def _earlier(entry: dict) -> dict:
    """A kept suggestion as a later edit plugin is handed it (`suggested`)."""
    out = {"by": entry.get("plugin"), "name": entry.get("name") or "", "edit": dict(entry.get("edit") or {})}
    if entry.get("reason"):
        out["reason"] = entry["reason"]
    return out


def suggestion_id(plugin_id: str, edit: dict) -> str:
    """A suggestion's id: the same plugin making the same fitted suggestion
    gets the same one in every version and every run of the video."""
    key = json.dumps({"plugin": plugin_id, "edit": edit}, sort_keys=True)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def suggest_edits(clips: list, *, video, segments, language: str, config: dict, data_dir) -> list[dict]:
    """Run the job's Suggest edits plugins on the clips that will be made.
    Returns the report.

    `clips` are the candidates as process_video will render them, after Rate
    & understand and the clip limit; the first MAX_MOMENTS are handed over
    (m1, m2, ... in their order). The plugins run one after another in the
    order chosen, each handed what the ones before it suggested
    (`suggested`). Each suggestion, fitted by host.read_edits, is added to
    the clip's subscores as one `plugin_edits` entry: {id, plugin, version,
    name, window, min_length, edit, reason, state: "new"}, with the window in
    seconds of the video rounded to 2 decimals, as the clip's row keeps it.
    Nothing else about a clip changes, and a clip with no suggestion keeps
    its subscores as they were.

    The report has one entry for each run: {plugin, version, name, steps:
    ["edit"], ok, given, suggested, noted: 0, rated: 0, set_aside: 0,
    error?}, `error` being why it was skipped, in the creator's words.
    """
    from core import cancel

    clips_cfg = config.get("clips") or {}
    if not clips:
        return []  # no edit run is ever started with no clips
    pool = list(clips[:MAX_MOMENTS])
    crops = crops_for(config)
    min_length = max(1.0, float(clips_cfg.get("min_duration") or 0))
    report: list[dict] = []

    for choice in _choices(clips_cfg.get("edit")):
        cancel.check_active()
        who = _who(choice, data_dir)
        named = " ".join(part for part in (who["name"], who["version"]) if part)
        print(f"      Suggest edits: {named} ({who['plugin']}) on {len(pool)} clip(s)")
        entry = {**who, "steps": ["edit"], "ok": True, "given": len(pool), "suggested": 0, "noted": 0,
                 "rated": 0, "set_aside": 0}
        report.append(entry)
        moments = []
        for i, c in enumerate(pool, 1):
            moment = moments_of(f"m{i}", c)
            earlier = [_earlier(e) for e in (c.subscores or {}).get("plugin_edits") or [] if isinstance(e, dict)]
            if earlier:
                moment["suggested"] = earlier
            moments.append(moment)
        try:
            got = runner.answer_moments(choice, ["edit"], moments, video=video, segments=segments,
                                        language=language, config=config, data_dir=data_dir, stage="edit",
                                        crops=crops)
        except runner.PluginError as e:
            why = e.why or str(e)
            entry.update(ok=False, error=why)
            print(f"      Going on without {who['name']}: {why}")
            print(f"      ({e})")
            continue
        entry.update(plugin=got["plugin"], version=got["version"], name=got["name"])
        for i, c in enumerate(pool, 1):
            answer = got["edits"].get(f"m{i}")
            if not answer:
                continue
            kept = {"id": suggestion_id(got["plugin"], answer["edit"]), "plugin": got["plugin"],
                    "version": got["version"], "name": got["name"],
                    "window": [round(float(c.start), 2), round(float(c.end), 2)], "min_length": min_length,
                    "edit": answer["edit"], "reason": answer.get("reason") or "", "state": "new"}
            # A copy of its own: the finder's dicts are never changed.
            subscores = dict(c.subscores or {})
            subscores["plugin_edits"] = [*(subscores.get("plugin_edits") or []), kept]
            c.subscores = subscores
            entry["suggested"] += 1
        print(f"      {got['name']} suggested edits for {entry['suggested']} of {len(pool)} clip(s)")
    return report
