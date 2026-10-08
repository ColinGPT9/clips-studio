"""Rate & understand and Suggest edits on screen (ui/src/renderer/src/lib/steps.ts).

The TypeScript runs under Node here, as tests/test_ui_marketplace.py runs the
Marketplace's, and is fed what the engine really uses: the manifest helper
that decides what a plugin can be chosen for (sdk manifest.offers), the
plugins.steps cleaning the API applies to what the watch editor sends back,
and the posting and video-page wording the design gives. The job form and the
watch editor have no React test harness, so the parts that live in them are
checked in their source.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from plugins import steps as plugin_steps
from plugins._sdk import manifest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
LIB = UI / "lib"
MANIFESTS = ROOT / "tests" / "fixtures" / "plugins" / "manifests"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _run(tmp_path, body: str, data=None):
    """Run `body` (JavaScript: `m` is lib/steps.ts, `data` the JSON given) and
    return what it prints as JSON."""
    module = tmp_path / "steps.ts"
    module.write_text((LIB / "steps.ts").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps(data, default=str), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps(module.as_uri())});"
        "const fs = await import('node:fs');"
        f"const data = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'data.json'))}, 'utf8'));"
        f"console.log(JSON.stringify((() => {{ {body} }})()));"
    )
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _plugin(pid: str, inputs, outputs, **extra) -> dict:
    """An installed plugin as GET /plugins sends it (manager._summary)."""
    return {"id": pid, "name": pid.split("/")[1].replace("-", " ").title(), "version": "1.0.0", "kind": "pipeline",
            "enabled": True, "problem": None, "flag": None, "inputs": inputs, "outputs": outputs, **extra}


# ---- what a plugin can be chosen for ---------------------------------------------------------


def test_offers_match_the_manifest_helper(tmp_path):
    """Every manifest fixture, the repository's own plugins and some shapes
    the validator refuses: the screen offers a plugin for exactly the steps
    the engine would run it for."""
    paths = [*sorted(MANIFESTS.glob("*/*.yaml")), *sorted((ROOT / "plugins" / "builtin").glob("*.yaml")),
             *sorted((ROOT / "tests" / "fixtures" / "plugins").glob("*/clipskitty.yaml")),
             *sorted((ROOT / "examples").glob("**/clipskitty.yaml"))]
    data = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in paths]
    data += [None, {}, {"inputs": "moments", "outputs": "ratings"}, {"inputs": ["moments"], "outputs": None},
             {"inputs": ["moments"], "outputs": ["context", "ratings", "ranges"]},
             {"inputs": [None, "moments"], "outputs": [1, "ratings"]}, {"outputs": ["context"]},
             {"inputs": ["moments"], "outputs": ["Ratings"]}]
    got = _run(tmp_path, "return data.map((d) => m.offers(d))", data)
    assert got == [list(manifest.offers(d)) for d in data]
    # the fixtures cover every step, alone and together
    assert {tuple(g) for g in got} >= {("find",), ("understand",), ("rate",), ("edit",),
                                       ("find", "understand", "rate"), ()}


def test_step_plugins_are_offered_only_for_steps_they_can_do(tmp_path):
    plugins = [
        _plugin("example-dev/quarkbloom-finder", ["video", "transcript"], ["ranges"]),
        # describes the moments it finds itself: a Pipeline, not an understander of others'
        _plugin("example-dev/quarkbloom-captioned-finder", ["video", "transcript"], ["ranges", "context"]),
        _plugin("example-dev/quarkbloom-notes", ["moments", "transcript"], ["context"]),
        _plugin("example-dev/quarkbloom-rater", ["moments", "transcript"], ["ratings"]),
        _plugin("example-dev/quarkbloom-all-in-one", ["video", "transcript", "moments"],
                ["ranges", "context", "ratings"]),
        _plugin("example-dev/off-rater", ["moments"], ["ratings"], enabled=False),
        _plugin("example-dev/blocked-rater", ["moments"], ["ratings"], flag={"severity": "blocked"}),
        _plugin("example-dev/delisted-rater", ["moments"], ["ratings"], flag={"severity": "delisted"}),
        _plugin("example-dev/broken-notes", ["moments"], ["context"], problem="it needs Clips Kitty >=9"),
        _plugin("example-dev/caption-rater", ["moments"], ["ratings"], kind="caption-style"),
        _plugin("example-dev/old-plugin", None, None),
    ]
    got = _run(tmp_path, "return [m.usableFor(data, 'understand'), m.usableFor(data, 'rate'), m.stepPlugins(data)]"
                         ".map((list) => list.map((p) => p.id))", plugins)
    assert got[0] == ["example-dev/quarkbloom-notes", "example-dev/quarkbloom-all-in-one"]
    assert got[1] == ["example-dev/quarkbloom-rater", "example-dev/quarkbloom-all-in-one",
                      "example-dev/delisted-rater"]
    assert got[2] == ["example-dev/quarkbloom-notes", "example-dev/quarkbloom-rater",
                      "example-dev/quarkbloom-all-in-one", "example-dev/delisted-rater"]
    # each one offered can do that step, as the engine's store check sees it
    for step, ids in (("understand", got[0]), ("rate", got[1])):
        for p in plugins:
            if p["id"] in ids:
                assert manifest.step_problem(p, step) is None, (p["id"], step)


def test_edit_plugins_are_offered_only_where_the_engine_would_ask_them(tmp_path):
    """Suggest edits lists the installed plugins whose manifest offers the edit
    step (an `edits` output with `moments` in), turned on, able to run and not
    blocked. One that finds moments and suggests edits is listed too: the
    engine never asks a find run to edit, so it may be both."""
    trimmer = yaml.safe_load((ROOT / "tests" / "fixtures" / "plugins" / "trimmer" / "clipskitty.yaml")
                             .read_text(encoding="utf-8"))
    no_moments = yaml.safe_load((MANIFESTS / "invalid" / "edits-without-moments.yaml").read_text(encoding="utf-8"))
    plugins = [
        _plugin("fixture-dev/trimmer", trimmer["inputs"], trimmer["outputs"]),
        _plugin("example-dev/quarkbloom-finder-trimmer", ["video", "moments"], ["ranges", "edits"]),
        _plugin("example-dev/quarkbloom-rater", ["moments", "transcript"], ["ratings"]),
        _plugin("example-dev/edits-only", no_moments["inputs"], no_moments["outputs"]),
        _plugin("example-dev/off-trimmer", ["moments"], ["edits"], enabled=False),
        _plugin("example-dev/blocked-trimmer", ["moments"], ["edits"], flag={"severity": "blocked"}),
        _plugin("example-dev/broken-trimmer", ["moments"], ["edits"], problem="it needs Clips Kitty >=9"),
        _plugin("example-dev/caption-trimmer", ["moments"], ["edits"], kind="caption-style"),
    ]
    got = _run(tmp_path, "return [m.usableFor(data, 'edit'), m.editPlugins(data), m.stepPlugins(data)]"
                         ".map((list) => list.map((p) => p.id))", plugins)
    assert got[0] == got[1] == ["fixture-dev/trimmer", "example-dev/quarkbloom-finder-trimmer"]
    # an editor is never listed under Rate & understand for its edits
    assert got[2] == ["example-dev/quarkbloom-rater"]
    for p in plugins:
        offered = "edit" in manifest.offers(p)
        assert (p["id"] in got[0]) == (offered and p["enabled"] and not p["problem"] and not p["flag"]
                                       and p["kind"] == "pipeline"), p["id"]
        if p["id"] in got[0]:
            assert manifest.step_problem(p, "edit") is None, p["id"]


def test_ticking_the_switch_never_picks_the_videos_own_pipeline(tmp_path):
    """One plugin doing find, understand and rate, chosen as the video's
    Pipeline, can't be chosen again under Rate & understand (the API refuses
    it). With nothing else usable the job form disables the switch and says
    why, rather than showing a box that un-ticks itself."""
    both = _plugin("example-dev/quarkbloom-all-in-one", ["video", "transcript", "moments"],
                   ["ranges", "context", "ratings"])
    rater = _plugin("example-dev/quarkbloom-rater", ["moments", "transcript"], ["ratings"])
    as_pipeline = {"pipeline": {"id": both["id"]}}
    cases = [[[both], {}], [[both], None], [[both], as_pipeline], [[both, rater], as_pipeline], [[], {}]]
    got = _run(tmp_path, "return data.map(([usable, o]) => m.firstStepPlugin(usable, o)?.id ?? null)", cases)
    assert got == [both["id"], both["id"], None, rater["id"], None]
    form = (UI / "components" / "queue" / "AddVideos.tsx").read_text(encoding="utf-8")
    assert "const first = firstStepPlugin(stepUsable, next)" in form
    assert "tg.key === 'steps' && !hasSteps(slot.options) && !firstStepPlugin(stepUsable, slot.options)" in form
    assert "disabled={disabled}" in form
    assert ("'The only plugin you can use to rate or understand moments is this video’s Pipeline, and it can’t "
            "also be chosen here. Install or turn on another one in the Marketplace, or choose a different "
            "Pipeline.'") in form


def test_the_marketplace_never_says_every_pipeline_finds_moments():
    """Raters and understanders are listed under Pipelines too (kind: pipeline),
    and one that only rates or understands isn't offered under Pipeline."""
    page = (UI / "pages" / "Marketplace.tsx").read_text(encoding="utf-8")
    assert "each one finds a video’s moments" not in page
    assert ("'Pipelines made by other developers: some find a video’s moments their own way, and others rate the "
            "moments found or say what happens in them. Clips Kitty cuts, frames and captions the clips as usual.")\
        in page
    assert "{(done.outputs ?? ['ranges']).includes('ranges')" in page
    assert "t('Ready. Add a video in the Generate bar, tick Rate & understand and choose')" in page


# ---- the Generate guard -----------------------------------------------------------------------


def test_step_problem_keys_follow_the_rows(tmp_path):
    """Keys name the row's plugin, so removing a row never moves a problem onto
    another, and a removed row has no key left to hold Generate. The Pipeline
    row keeps its bare key, which is never one of these."""
    options = {"pipeline": {"id": "example-dev/quarkbloom-finder"},
               "understand": [{"id": "example-dev/quarkbloom-notes"}],
               "rate": [{"id": "example-dev/quarkbloom-rater"}, {"id": "example-dev/pace-rater"}]}
    removed = {**options, "rate": [{"id": "example-dev/pace-rater"}]}
    got = _run(tmp_path, """
        const guard = (slot, o, bad) => Boolean((o.pipeline && bad[slot])
          || m.stepProblemKeys(slot, o).some((k) => bad[k]))
        const red = {'s1:rate:example-dev/quarkbloom-rater': true}
        return [m.stepProblemKeys('s1', data.options), m.stepProblemKeys('s1', data.removed),
                m.stepProblemKeys('s1', {pipeline: data.options.pipeline}),
                guard('s1', data.options, red), guard('s1', data.removed, red), guard('s1', {}, red),
                guard('s1', data.options, {s1: true}), guard('s1', {}, {s1: true})]
    """, {"options": options, "removed": removed})
    assert got[0] == ["s1:understand:example-dev/quarkbloom-notes", "s1:rate:example-dev/quarkbloom-rater",
                      "s1:rate:example-dev/pace-rater"]
    assert got[1] == ["s1:understand:example-dev/quarkbloom-notes", "s1:rate:example-dev/pace-rater"]
    assert got[2] == []
    # a red row holds Generate; removed, or the switch unticked, it no longer can
    assert got[3:6] == [True, False, False]
    # the Pipeline guard is today's: its bare key, only while a pipeline is chosen
    assert got[6:] == [True, False]
    # Suggest edits rows report under their own keys, after the moment steps',
    # and may name the video's own Pipeline without clashing with its bare key
    edits = {**options, "edit": [{"id": "example-dev/quarkbloom-finder"}, {"id": "fixture-dev/trimmer"}]}
    got = _run(tmp_path, """
        return [m.stepProblemKeys('s1', data), m.stepProblemKeys('s1', {edit: data.edit}),
                m.stepProblemKeys('s1', {...data, edit: []})]
    """, edits)
    assert got[0] == ["s1:understand:example-dev/quarkbloom-notes", "s1:rate:example-dev/quarkbloom-rater",
                      "s1:rate:example-dev/pace-rater", "s1:edit:example-dev/quarkbloom-finder",
                      "s1:edit:fixture-dev/trimmer"]
    assert got[1] == ["s1:edit:example-dev/quarkbloom-finder", "s1:edit:fixture-dev/trimmer"]
    assert got[2] == got[0][:3]
    form = (UI / "components" / "queue" / "AddVideos.tsx").read_text(encoding="utf-8")
    assert re.search(r"\(s\.options\.pipeline && badSettings\[s\.key\]\) \|\|\s*"
                     r"stepProblemKeys\(s\.key, s\.options\)\.some\(\(k\) => badSettings\[k\]\)", form)
    assert "onProblem={(bad) => setBadSettings((b) => ({ ...b, [slot.key]: bad }))}" in form
    assert "[`${slot.key}:${step}:${id}`]: bad" in form
    # both step blocks, Rate & understand's and Suggest edits', report so
    assert form.count("setBadSettings((b) => ({ ...b, [`${slot.key}:${step}:${id}`]: bad }))") == 2
    assert "steps={['edit']}" in form and "plugins={editPlugins}" in form


# ---- the watch editor -------------------------------------------------------------------------


def test_the_watch_editor_sends_kept_steps_back_and_clears_them_when_off(tmp_path):
    """A watched channel's options are replaced on every save, so a kept choice
    goes back each time (an autosave would otherwise drop it); a queued
    video's are merged, so it is left out, as Pipeline is. Off clears both."""
    both = {"understand": [{"id": "example-dev/quarkbloom-notes"}],
            "rate": [{"id": "example-dev/quarkbloom-rater", "settings": {"strict": True}}],
            "captions": False}
    rate_only = {"rate": both["rate"]}
    got = _run(tmp_path, """
        return [m.stepsPatch(data.both, true, true), m.stepsPatch(data.both, true, false),
                m.stepsPatch(data.both, false, true), m.stepsPatch(data.both, false, false),
                m.stepsPatch(data.rate_only, true, true), m.stepsPatch({}, false, true), m.stepsPatch({}, true, false)]
    """, {"both": both, "rate_only": rate_only})
    assert got[0] == {"patch": {"rate": both["rate"], "understand": both["understand"]}, "clear": []}
    assert got[1] == {"patch": {}, "clear": []}
    assert got[2] == got[3] == {"patch": {}, "clear": ["rate", "understand"]}
    assert got[4] == {"patch": {"rate": both["rate"]}, "clear": []}
    # without steps the panel sends exactly what it sent before
    assert got[5] == got[6] == {"patch": {}, "clear": []}
    # what goes back is what the API stores: plugins.steps cleans it to itself
    for field, value in got[0]["patch"].items():
        assert plugin_steps.clean(field, value) == value
    panel = (UI / "components" / "queue" / "QueueItemSettings.tsx").read_text(encoding="utf-8")
    assert "stepsPatch(s, stepsKept, Boolean(channel))" in panel
    # turning Longform on turns these off, as it does Pipeline
    longform = re.search(r"'Longform',\s*'\(16:9\)',\s*longform,\s*\(on\) => \{(.*?)\n          \},", panel, re.S)
    assert longform and "setStepsKept(false)" in longform.group(1)
    card = (UI / "components" / "watch" / "WatchCard.tsx").read_text(encoding="utf-8")
    assert "mode: watch.publish.mode" in card and "max_posts: watch.publish.max_posts" in card
    assert "presetLongform: Boolean(automation.presets.find((p) => p.id === watch.preset)?.options?.longform)" in card


def test_the_watch_editor_sends_kept_edit_plugins_back_and_clears_them_when_off(tmp_path):
    """Suggest edits is kept or turned off as Rate & understand is: a watched
    channel gets its list back on every save, a queued video leaves it out,
    and off clears it. Without it the panel sends what it sent before."""
    edit = [{"id": "fixture-dev/trimmer", "settings": {"mode": "ok"}},
            {"id": "example-dev/quarkbloom-finder-trimmer"}]
    with_edit = {"edit": edit, "rate": [{"id": "example-dev/quarkbloom-rater"}], "captions": False}
    got = _run(tmp_path, """
        return [m.editPatch(data, true, true), m.editPatch(data, true, false), m.editPatch(data, false, true),
                m.editPatch(data, false, false), m.editPatch({}, false, true), m.editPatch({edit: []}, false, true),
                m.editPatch({}, true, false)]
    """, with_edit)
    assert got[0] == {"patch": {"edit": edit}, "clear": []}
    assert got[1] == {"patch": {}, "clear": []}
    assert got[2] == got[3] == {"patch": {}, "clear": ["edit"]}
    assert got[4] == got[5] == got[6] == {"patch": {}, "clear": []}
    # what goes back is what the API stores: plugins.steps cleans it to itself
    assert plugin_steps.clean("edit", got[0]["patch"]["edit"]) == edit
    panel = (UI / "components" / "queue" / "QueueItemSettings.tsx").read_text(encoding="utf-8")
    assert "const edits = editPatch(s, editKept, Boolean(channel))" in panel
    # turning Longform on turns Suggest edits off too
    longform = re.search(r"'Longform',\s*'\(16:9\)',\s*longform,\s*\(on\) => \{(.*?)\n          \},", panel, re.S)
    assert longform and "setEditKept(false)" in longform.group(1)


def test_edit_posting_lines_say_suggestions_wait_and_clips_wait_for_every_plugin(tmp_path):
    """A watched channel with Suggest edits: an automatic one posts clips as
    they were made, so it says the suggestions wait and what a later Use
    does; one that asks says to check them; each says the clips wait for
    every plugin's answer. Through t(), like the steps' posting lines."""
    kept = {"edit": [{"id": "fixture-dev/trimmer"}]}
    auto = ("This channel posts clips as Clips Kitty made them, without waiting for you. Suggested edits wait in "
            "the editor, and Clips Kitty doesn’t put one into a clip until you use it and apply your edits. Using "
            "one later changes the clip in Clips Kitty. Posts that already went out stay as they were, and a later "
            "re-send of that clip sends the edited one.")
    ask = "Suggested edits wait for you in the editor. Check them before you publish."
    wait = ("Clips are made after every plugin has answered. Each one can add up to its time limit (shown in the "
            "Marketplace) before this channel’s clips are ready.")
    cases = [
        ({"mode": "auto", "max_posts": 3}, kept, [auto, wait]),
        ({"mode": "ask", "max_posts": 0}, kept, [ask, wait]),
        ({"mode": "off", "max_posts": 3}, kept, [wait]),
        ({"mode": "auto", "max_posts": 3}, {}, []),
        ({"mode": "auto", "max_posts": 3}, {"edit": []}, []),
    ]
    got = _run(tmp_path, "return data.map(([channel, kept]) => m.editPostingLines(channel, kept))",
               [[channel, kept] for channel, kept, _ in cases])
    assert got == [lines for _, _, lines in cases]
    said = _run(tmp_path, "const said = []; m.editPostingLines(data, {edit: [{id: 'x'}]}, (s) => { said.push(s);"
                          " return s }); return said", {"mode": "auto", "max_posts": 1})
    assert said == [auto, wait]
    # the rate posting lines are as they were beside it
    assert _run(tmp_path, "return m.stepPostingLines(data, {edit: [{id: 'x'}]})", {"mode": "auto", "max_posts": 2}) \
        == []
    panel = (UI / "components" / "queue" / "QueueItemSettings.tsx").read_text(encoding="utf-8")
    assert "const editLines = channel ? editPostingLines(channel, s, t) : []" in panel


def test_posting_lines_follow_mode_and_best_n(tmp_path):
    rater = {"rate": [{"id": "example-dev/quarkbloom-rater"}]}
    notes = {"understand": [{"id": "example-dev/quarkbloom-notes"}]}
    both = {**rater, **notes}
    cases = [
        ({"mode": "auto", "max_posts": 3}, rater,
         ["This channel posts only the 3 clips these plugins rate highest, without asking you."]),
        ({"mode": "auto", "max_posts": 1}, rater,
         ["This channel posts only the clip these plugins rate highest, without asking you."]),
        ({"mode": "ask", "max_posts": 3}, rater, ["Publish sends the 3 clips these plugins rate highest."]),
        ({"mode": "ask", "max_posts": 1}, rater, ["Publish sends the clip these plugins rate highest."]),
        ({"mode": "auto", "max_posts": 0}, rater, ["Clips go out in the order these plugins rate them."]),
        ({"mode": "ask", "max_posts": 0}, rater, ["Clips go out in the order these plugins rate them."]),
        ({"mode": "auto", "max_posts": 2}, notes,
         [("This channel posts automatically, so what these plugins say about a moment can end up in the posted "
           "title, description and hashtags without you checking them.")]),
        ({"mode": "ask", "max_posts": 2}, notes, []),
        ({"mode": "auto", "max_posts": 2}, both,
         ["This channel posts only the 2 clips these plugins rate highest, without asking you.",
          ("This channel posts automatically, so what these plugins say about a moment can end up in the posted "
           "title, description and hashtags without you checking them.")]),
        ({"mode": "off", "max_posts": 3}, both, []),
        ({"mode": "auto", "max_posts": 3}, {}, []),
    ]
    got = _run(tmp_path, "return data.map(([channel, kept]) => m.stepPostingLines(channel, kept))",
               [[channel, kept] for channel, kept, _ in cases])
    assert got == [lines for _, _, lines in cases]


def test_watch_settings_autosave_key_holds_only_the_steps_switch():
    """Opening a watched channel's settings must never save: the autosave key
    holds the switch, set from the settings as the panel opens, and nothing
    the engine answers later (the step plugins, their names)."""
    panel = (UI / "components" / "queue" / "QueueItemSettings.tsx").read_text(encoding="utf-8")
    current = re.search(r"const current = JSON\.stringify\(\[(.*?)\]\)", panel, re.S)
    assert current
    items = [i.strip() for i in current.group(1).split(",") if i.strip()]
    assert items == ["captions", "longClips", "podcast", "verticalLive", "gamingScoring", "gaming", "sport",
                     "longform", "longformMode", "longformShorts", "watermark", "pipeline", "stepsKept", "editKept",
                     "style"]
    assert "const [stepsKept, setStepsKept] = useState(Boolean(s.rate?.length || s.understand?.length))" in panel
    assert "const stepPlugins = useStepPlugins()" in panel
    # Suggest edits' switch likewise, set from the settings as the panel opens
    assert "const [editKept, setEditKept] = useState(Boolean(s.edit?.length))" in panel
    assert "const editPlugins = useEditPlugins()" in panel
    for late in ("stepPlugins", "useStepPlugins", "stepNames", "stepsGone", "postingLines", "channel",
                 "editPlugins", "useEditPlugins", "editNames", "editGone", "editLines"):
        assert late not in current.group(1), late


def test_a_watched_channel_keeps_its_pipeline_through_an_autosave():
    """A channel's options are replaced on every save (PATCH /automation/watches
    takes them whole), so a kept pipeline has to be sent back, as kept steps
    are; a queued video's PATCH merges and leaves it as it is."""
    panel = (UI / "components" / "queue" / "QueueItemSettings.tsx").read_text(encoding="utf-8")
    assert re.search(r"if \(!pipeline\) clear\.push\('pipeline'\)\s*else if \(channel\) patch\.pipeline = pipeline",
                     panel)
    card = (UI / "components" / "watch" / "WatchCard.tsx").read_text(encoding="utf-8")
    assert "save={(patch) => api.patchWatch(watch.id, { options: patch })}" in card and "channel={{" in card


# ---- the video page ---------------------------------------------------------------------------


def _run_entry(**extra) -> dict:
    """A report entry as plugins/steps.after_finding writes it."""
    entry = {"plugin": "example-dev/quarkbloom-rater", "version": "1.0.0", "name": "Quarkbloom Rater",
             "steps": ["rate"], "ok": True, "given": 12, "noted": 0, "rated": 0, "set_aside": 0}
    entry.update(extra)
    return entry


def test_the_video_page_says_what_each_plugin_did_in_plain_words(tmp_path):
    runs = [
        _run_entry(name="Quarkbloom Notes", steps=["understand"], noted=7),
        _run_entry(rated=12),
        _run_entry(rated=12, set_aside=4),
        _run_entry(steps=["understand", "rate"], noted=5, rated=12, set_aside=1),
        _run_entry(steps=["understand"]),
        _run_entry(),
        _run_entry(ok=False, error="It isn't installed any more."),
        _run_entry(ok=False, error="It said: the arena feed was empty."),
        _run_entry(name="", ok=False, error="It took longer than its 10 minute limit, so Clips Kitty stopped it."),
    ]
    got = _run(tmp_path, "return data.map((r) => m.stepRunText(r, 55))", runs)
    assert got == [
        "Quarkbloom Notes said what happens in 7 of 12 moments.",
        "Quarkbloom Rater rated 12 of 12 moments.",
        "Quarkbloom Rater rated 12 of 12 moments. 4 rated under your minimum score (55) were set aside.",
        ("Quarkbloom Rater said what happens in 5 of 12 moments and rated 12. 1 rated under your minimum score (55) "
         "were set aside."),
        "Quarkbloom Rater looked at the moments and changed nothing.",
        "Quarkbloom Rater looked at the moments and changed nothing.",
        "Clips Kitty made these clips without Quarkbloom Rater. It isn't installed any more.",
        "Clips Kitty made these clips without Quarkbloom Rater. It said: the arena feed was empty.",
        ("Clips Kitty made these clips without example-dev/quarkbloom-rater. It took longer than its 10 minute "
         "limit, so Clips Kitty stopped it."),
    ]
    assert not any(".." in line or "bug" in line.lower() for line in got)
    # the plugins named when a rater set every moment aside: each once, as core/outcome.py names them
    from core.outcome import rated_out_names

    outcome = {"steps": [_run_entry(set_aside=3), _run_entry(name="Quarkbloom Notes", steps=["understand"]),
                         _run_entry(set_aside=2), _run_entry(name="", plugin="example-dev/pace-rater", set_aside=1)]}
    assert _run(tmp_path, "return [m.ratedOutNames(data), m.ratedOutNames({}), m.ratedOutNames(null)]",
                outcome) == [rated_out_names(outcome), [], []]
    assert rated_out_names(outcome) == ["Quarkbloom Rater", "example-dev/pace-rater"]


def test_the_video_page_says_what_each_edit_plugin_suggested(tmp_path):
    """A Suggest edits run (plugins/steps.suggest_edits: steps ['edit'], with
    `suggested`) says how many clips it suggested edits for, or that it
    suggested nothing; one that didn't run says no suggestions came, since
    its failure changed no clip. Never "looked at the moments"."""
    trimmer = {"plugin": "example-dev/quarkbloom-trimmer", "version": "1.0.0", "name": "Quarkbloom Trimmer",
               "steps": ["edit"], "ok": True, "given": 5, "suggested": 4, "noted": 0, "rated": 0, "set_aside": 0}
    runs = [
        trimmer,
        {**trimmer, "suggested": 0},
        {**trimmer, "ok": False, "suggested": 0, "error": "It isn't installed any more."},
        {**trimmer, "ok": False, "suggested": 0, "error": "It said: the arena feed was empty."},
        {**trimmer, "ok": False, "suggested": 0},
        {**trimmer, "name": "", "ok": False, "suggested": 0, "error": "Clips Kitty couldn't use its answer."},
        # A rater beside it reads as before.
        _run_entry(rated=12),
    ]
    got = _run(tmp_path, "return data.map((r) => m.stepRunText(r, 55))", runs)
    assert got == [
        "Quarkbloom Trimmer suggested edits for 4 of 5 clips. Open a clip in the editor to see them.",
        "Quarkbloom Trimmer looked at the clips and suggested nothing.",
        "No edit suggestions from Quarkbloom Trimmer. It isn't installed any more.",
        "No edit suggestions from Quarkbloom Trimmer. It said: the arena feed was empty.",
        "No edit suggestions from Quarkbloom Trimmer.",
        "No edit suggestions from example-dev/quarkbloom-trimmer. Clips Kitty couldn't use its answer.",
        "Quarkbloom Rater rated 12 of 12 moments.",
    ]
    assert not any(".." in line or "moments and changed nothing" in line for line in got[:6])
    # Through t(), like the lines around it.
    words = _run(tmp_path, "const said = []; m.stepRunText(data, 55, (s) => { said.push(s); return s });"
                           " return said", trimmer)
    assert sorted(words) == ["clips. Open a clip in the editor to see them.", "of", "suggested edits for"]


def test_the_rate_and_understand_switch_reads_as_designed():
    """The job form's switch, its rows and the video page use the design's
    words, through t() like their neighbours; the Pipeline switch's own words
    are unchanged."""
    form = (UI / "components" / "queue" / "AddVideos.tsx").read_text(encoding="utf-8")
    assert "label: 'Rate & understand',\n    hint: '(Marketplace)'," in form
    assert ("'Plugins you installed from the Marketplace look at the moments once they’re found, by Clips Kitty, "
            "Sports, Gaming scoring or a pipeline. One that understands says what happens in each moment, so the "
            "titles, descriptions and hashtags can say it. One that rates gives each moment its own score, which "
            "decides which clips are made and their order. Clips Kitty does any step you leave to it. Not with "
            "Longform.'") in form
    assert "if (key === 'steps') return Boolean(o.rate?.length || o.understand?.length)" in form
    assert "{t('Fix the pipeline setting marked in red first.')}" in form
    fields = (UI / "components" / "queue" / "StepFields.tsx").read_text(encoding="utf-8")
    for words in ("Moments found by", "Understand them with", "and with", "Rate them with", "then with",
                  "Clips Kitty (from what’s said)", "Clips Kitty’s own scores", "Choose a plugin", "Add another",
                  "Up to 3 plugins for each step",
                  ("They rate in this order: each one sees the score the one before it gave. The last one’s score "
                   "is the one that counts."),
                  "Clips Kitty can use what each of them says (up to 8 notes for each moment).",
                  ("Rating decides which clips are made and their order, and which are posted when only the best "
                   "few are."),
                  "This plugin isn’t installed and turned on any more. Choose another, or remove it."):
        assert f"'{words}'" in fields, words
    pipeline = (UI / "components" / "PipelineFields.tsx").read_text(encoding="utf-8")
    assert ("missingText ?? t('This pipeline isn’t installed and turned on any more. Choose another, or untick "
            "Pipeline.')") in pipeline
    assert "{label ?? t('Pipeline')}" in pipeline and "aria-label={`${label ?? t('Pipeline')} ${name}`}" in pipeline
    note = (UI / "components" / "PluginStepsNote.tsx").read_text(encoding="utf-8")
    assert "t('Marketplace plugins on this video')" in note
    explained = (UI / "components" / "NoClipsExplanation.tsx").read_text(encoding="utf-8")
    assert "t('Lower the minimum score, or turn off Rate & understand for this video.')" in explained
