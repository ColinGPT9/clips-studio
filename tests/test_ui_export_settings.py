"""Settings → Export and the Export buttons: the desktop app's side of export
presets (editor-suite step 1, #177 and #203).

The engine is tested in test_export_presets.py, test_export_route.py and their
neighbours; this is what the screens do with it
(docs/editor-suite/api/step1-export.md):

- lib/exportJobs.ts keeps track of exports made in the background. It runs
  under Node here, as tests/test_ui_edit_suggestions.py runs its module.
- The Dashboard's Activity feed tells of an export when it starts and when it
  ends, never its raw event: the page's own describeEvent runs under Node too.
- The screens have no React test harness, so what they must keep doing is
  checked in their source: Original exports exactly as before, any other
  preset is followed and can be cancelled, the calls the engine guards carry
  the session header, and nothing new on screen bypasses t().
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_ui_i18n_editor import untranslated

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
LIB = UI / "lib"


def _source(relative: str) -> str:
    return (UI / relative).read_text(encoding="utf-8")


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _run(tmp_path, module: str, body: str, data=None):
    """Run `body` (JavaScript: `m` is lib/<module>.ts, `data` the JSON given)
    and return what it prints as JSON."""
    copy = tmp_path / f"{module}.ts"
    copy.write_text((LIB / f"{module}.ts").read_text(encoding="utf-8"), encoding="utf-8")
    return _run_file(tmp_path, copy, body, data)


def _run_file(tmp_path, path: Path, body: str, data=None):
    """As _run, with `m` the TypeScript module at `path`."""
    (tmp_path / "data.json").write_text(json.dumps(data), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps(path.as_uri())});"
        "const fs = await import('node:fs');"
        f"const data = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'data.json'))}, 'utf8'));"
        f"console.log(JSON.stringify((() => {{ {body} }})()));"
    )
    # Node writes UTF-8 to a pipe whatever the Windows code page is.
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, encoding="utf-8", timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _job(export_id=1, status="queued", fraction=0.0, clip_id=12, created_at="2026-11-05T10:18:55", **extra):
    """An export job as the engine's events and answers carry it."""
    return {"export_id": export_id, "kind": "export", "clip_id": clip_id, "status": status,
            "cancelling": False, "fraction": fraction, "pass": None, "passes": None, "encoder": None,
            "smaller": False, "attempt_fraction": None,
            "folder": "C:\\Exports", "name": "my-clip", "preset": {"kind": "custom"}, "result": None,
            "error": None, "created_at": created_at, "started_at": None, "finished_at": None, **extra}


def _result(bytes_=23_400_000, over=False):
    return {"bytes": bytes_, "width": 720, "height": 1280, "fps": 30.0, "codec": "h264", "encoder": "libx264",
            "hardware": False, "duration": 20.0, "mode": "quality", "video_kbps": None, "crf": 23,
            "audio_kbps": 128, "upscale": False, "target_bytes": None, "over_target": over,
            "over_reason": "missed" if over else None, "floor_kbps": None, "retried": False,
            "fell_back_to_cpu": False, "path": "C:\\Exports\\my-clip.mp4", "file": "my-clip.mp4"}


# ---- following an export (lib/exportJobs.ts) -------------------------------------------------


def test_a_late_answer_never_undoes_a_newer_event(tmp_path):
    """The lane's first event can beat the export route's answer, and a
    recheck can answer after newer events: whatever comes last, a job only
    moves forward, and a finished job stays as it finished."""
    steps = [
        [_job(status="running", fraction=0.4)],     # the event
        [_job(status="queued")],                      # the route's answer, arriving late
        [_job(status="running", fraction=0.3)],     # a recheck asked before the last event
        [_job(status="running", fraction=0.4, cancelling=True)],  # same place, newer news
        [_job(status="done", fraction=1.0, result=_result())],
        [_job(status="running", fraction=0.9)],     # a dropped-and-resent progress event
        [_job(status="cancelled")],                   # finished is final
    ]
    got = _run(tmp_path, "exportJobs", """
        let map = new Map()
        const seen = []
        for (const step of data) {
          const next = m.mergeJobs(map, step)
          const job = next.get(m.jobKey(step[0]))
          seen.push([next === map, job.status, job.fraction, job.cancelling])
          map = next
        }
        return seen
    """, steps)
    assert got == [
        [False, "running", 0.4, False],
        [True, "running", 0.4, False],
        [True, "running", 0.4, False],
        [False, "running", 0.4, True],
        [False, "done", 1.0, False],
        [True, "done", 1.0, False],
        [True, "done", 1.0, False],
    ]


def test_an_engine_restart_neither_hides_new_exports_nor_leaves_old_ones_running(tmp_path):
    """The engine numbers exports from 1 again when it starts, so a job is
    known by its number and when it was made. A recheck that no longer lists
    an export this window had running ends it (the engine forgot it), but not
    one queued after the question was sent."""
    old = _job(export_id=1, status="running", fraction=0.5, created_at="2026-11-05T10:00:00")
    queued_meanwhile = _job(export_id=2, status="queued", created_at="2026-11-05T10:00:05")
    after_restart = _job(export_id=1, status="queued", clip_id=13, created_at="2026-11-05T10:30:00")
    got = _run(tmp_path, "exportJobs", """
        let map = m.mergeJobs(new Map(), [data.old])
        const asked = new Set([m.jobKey(data.old)])           // running when the recheck was sent
        map = m.mergeJobs(map, [data.queued_meanwhile])        // the route answered meanwhile
        map = m.reconcileJobs(map, [], asked, 'lost')          // the engine restarted: it lists none
        const unchanged = m.reconcileJobs(map, [], new Set(), 'lost') === map
        map = m.mergeJobs(map, [data.after_restart])            // a new export 1, after the restart
        return {
          old: [map.get(m.jobKey(data.old)).status, map.get(m.jobKey(data.old)).error],
          meanwhile: map.get(m.jobKey(data.queued_meanwhile)).status,
          restarted: map.get(m.jobKey(data.after_restart)).clip_id,
          size: map.size,
          unchanged,
        }
    """, {"old": old, "queued_meanwhile": queued_meanwhile, "after_restart": after_restart})
    assert got == {"old": ["failed", "lost"], "meanwhile": "queued", "restarted": 13, "size": 3,
                   "unchanged": True}


def test_a_clip_shows_its_running_exports_and_the_last_one_this_window_started(tmp_path):
    """Export 5 is someone else's, running: it shows under this window's last
    result rather than in its place (the fix round's two-tab finding)."""
    jobs = [
        _job(export_id=1, status="done", result=_result(), created_at="2026-11-05T10:00:00"),
        _job(export_id=2, status="failed", error="disk full", created_at="2026-11-05T10:05:00"),
        _job(export_id=3, status="done", result=_result(), clip_id=99, created_at="2026-11-05T10:06:00"),
        _job(export_id=4, status="done", result=_result(), created_at="2026-11-05T10:07:00"),  # not ours
    ]
    got = _run(tmp_path, "exportJobs", """
        const started = new Set(data.slice(0, 3).map(m.jobKey))
        const running = {...data[0], export_id: 5, status: 'running', created_at: '2026-11-05T10:09:00'}
        return {
          finished: m.clipExports(data, 12, started).map((j) => j.export_id),
          running: m.clipExports([...data, running], 12, started).map((j) => j.export_id),
          other: m.clipExports(data, 99, started).map((j) => j.export_id),
          none: m.clipExports(data, 12, new Set()).map((j) => j.export_id),
        }
    """, jobs)
    assert got == {"finished": [2], "running": [2, 5], "other": [3], "none": []}


def test_each_export_shows_its_result_the_moment_it_is_done(tmp_path):
    """Two windows exported the same clip at once (the black-box check): the
    first window's export was done at 27.6 s, but its window showed the result
    only at 55 s, when the second window's export was done too. A window now
    shows its own last result the moment it is done, with every export of the
    clip still waiting or running under it, whoever started them. Only an
    export the window itself starts afterwards takes the result's place."""
    first = _job(export_id=1, status="running", fraction=0.6, created_at="2026-11-05T10:00:00")
    second = _job(export_id=2, status="queued", created_at="2026-11-05T10:00:02")
    first_done = {**first, "status": "done", "fraction": 1.0, "result": _result(),
                  "finished_at": "2026-11-05T10:00:28"}
    second_running = {**second, "status": "running", "fraction": 0.1}
    again = _job(export_id=3, status="queued", created_at="2026-11-05T10:00:40")
    got = _run(tmp_path, "exportJobs", """
        const shows = (jobs, mine) => m.clipExports(jobs, 12, new Set(mine.map(m.jobKey)))
            .map((j) => `${j.export_id}:${j.status}`)
        const {first, second, first_done, second_running, again} = data
        return {
          // one window each
          one_while_both_run: shows([first, second], [first]),
          two_while_both_run: shows([first, second], [second]),
          one_when_its_own_is_done: shows([first_done, second_running], [first]),
          two_when_the_other_is_done: shows([first_done, second_running], [second]),
          // one window started both (Export, then Export all): both lines, then the result beside the other
          both_running: shows([first, second], [first, second]),
          both_first_done: shows([first_done, second_running], [first, second]),
          // exported again after the result: the new export takes its place
          again: shows([first_done, again], [first, again]),
          // someone else exports it again, after this window's result
          others_again: shows([first_done, again], [first]),
        }
    """, {"first": first, "second": second, "first_done": first_done, "second_running": second_running,
          "again": again})
    assert got == {
        "one_while_both_run": ["1:running", "2:queued"],
        "two_while_both_run": ["1:running", "2:queued"],
        "one_when_its_own_is_done": ["1:done", "2:running"],
        "two_when_the_other_is_done": ["2:running"],
        "both_running": ["1:running", "2:queued"],
        "both_first_done": ["1:done", "2:running"],
        "again": ["3:queued"],
        "others_again": ["1:done", "3:queued"],
    }
    editor = _source("components/ClipEditor.tsx")
    # One line each, keyed, so each keeps its place from waiting to its result.
    assert "<ExportProgress key={jobKey(job)} jobs={[job]} onCancel={exports.cancel} />" in editor
    assert re.search(r'<div ref=\{progressRow\} className="[^"]*\bscroll-mb-4\b', editor)


def test_a_file_made_again_smaller_shows_that_try(tmp_path):
    """A file that came out over its size limit is made again, smaller: the
    lane says so (`smaller`) and how far along that try is
    (`attempt_fraction`), while `fraction` stays near the end. One export
    shows that try from 0, saying "Making it smaller…"; Export all's bar never
    goes back."""
    remake = _job(status="running", fraction=0.9991, smaller=True, attempt_fraction=0.25)
    first_try = _job(status="running", fraction=0.4, attempt_fraction=0.4)
    done = _job(export_id=2, status="done", fraction=1.0, result=_result())
    got = _run(tmp_path, "exportJobs", """
        const {remake, first_try, done} = data
        return {
          remake: m.exportProgress([remake]),
          first_try: m.exportProgress([first_try]),
          starting: m.exportProgress([{...remake, attempt_fraction: null}]),
          batch: m.exportProgress([done, remake]),
          queued: m.exportProgress([{...first_try, status: 'queued', fraction: 0}]),
        }
    """, {"remake": remake, "first_try": first_try, "done": done})
    assert got["remake"] == {"fraction": 0.25, "smaller": True}
    assert got["first_try"] == {"fraction": 0.4, "smaller": False}
    assert got["starting"] == {"fraction": 0, "smaller": True}
    assert got["batch"]["smaller"] is True and got["batch"]["fraction"] == pytest.approx((1 + 0.9991) / 2)
    assert got["queued"] == {"fraction": 0, "smaller": False}
    progress = _source("components/ExportProgress.tsx")
    assert "t('Making it smaller…')" in progress and "exportProgress(jobs)" in progress


def test_a_limit_too_small_for_long_clips_is_said_before_and_after(tmp_path):
    """A size limit that can't keep a long clip looking good: Settings →
    Export says so beside the size line, with how long a clip can be in its
    tooltip, and an export made at the floor says why it is over the limit."""
    got = _run(tmp_path, "exportJobs", """
        const w = (s) => m.watchableUnder({longest_watchable_s: s})
        return {
          longest: m.LONGEST_CLIP_S,
          watchable: [w(45.9), w(179.9), w(180), w(872.4), w(null), m.watchableUnder(null)],
          lengths: [0.4, 1, 45.9, 60, 61, 91.7, 120, 179.9].map((s) => m.clipLength(s)),
          words: m.clipLength(61, (s) => `«${s}»`),
        }
    """)
    assert got["longest"] == 180
    assert got["watchable"] == [45.9, 179.9, None, None, None, None]
    assert got["lengths"] == ["1 second", "1 second", "45 seconds", "1 minute", "1 minute 1 second",
                              "1 minute 31 seconds", "2 minutes", "2 minutes 59 seconds"]
    assert got["words"] == "1 «minute» 1 «second»"

    card = _source("components/ExportSettingsCard.tsx")
    assert "const watchable = custom && preset.max_mb !== null ? watchableUnder(estimate) : null" in card
    assert "{t('Limit too small for long clips')}" in card and "clipLength(watchable, t)" in card
    progress = _source("components/ExportProgress.tsx")
    assert "r.over_reason === 'too_small'" in progress
    assert "{t('over the size limit')}" in progress


def test_the_export_warnings_take_keyboard_focus_and_are_announced():
    """Each ⚠ warning carries its detail in a tooltip, so it takes keyboard
    focus, where the tooltip is read as its description. The card's warnings
    sit in a polite live region that is always there, so one that appears is
    announced; the result's warning keeps its phrase on one line."""
    card = _source("components/ExportSettingsCard.tsx")
    region = card.index('<span aria-live="polite"')
    end = card.index("</p>", region)
    warnings = card[region:end]
    for label in ("{t('Enlarged')}", "{t('Limit too small for long clips')}", "{t('Not on this PC')}"):
        assert label in warnings, label
    assert warnings.count("tabIndex={0}") == 3
    progress = _source("components/ExportProgress.tsx")
    at = progress.index('className="text-warn whitespace-nowrap"')
    assert "tabIndex={0}" in progress[at:at + 80]


def test_the_progress_says_what_is_happening_once_and_in_one_place():
    """One live region per export, in place from start to finish, with only
    what is happening in it; the percentage is the bar's."""
    progress = _source("components/ExportProgress.tsx")
    assert progress.count("aria-live=") == 1
    assert re.search(r'<span className="text-xs text-muted tabular-nums" aria-hidden="true">\s*\{pct\}%', progress)
    assert "aria-valuenow={pct}" in progress


def test_the_settings_card_answers_at_once_and_stays_put():
    """The UX check's points on Settings → Export."""
    card = _source("components/ExportSettingsCard.tsx")
    # A second answer to the first load (React's development build loads twice)
    # never undoes a choice made meanwhile.
    load = card[card.index(".exportPresets()"):]
    load = load[: load.index("}, [])")]
    assert "if (wanted.current) return" in load
    # Enter saves a number and leaves the cursor in its box.
    assert ".blur()" not in card and "if (e.key === 'Enter') commit(key)" in card
    # Format can be used while the formats are checked: H.264 always works.
    assert "disabled={codecs === null}" not in card
    assert "const codecList: ExportCodec[] = codecs === null ? ['h264'] : known.map((c) => c.codec)" in card
    # Each tooltip is on its control too, not only on the name above it.
    assert "cloneElement(children, { title: tip })" in card
    # The size line keeps its height before its figure comes.
    assert '<p className="text-xs text-muted flex flex-wrap items-center gap-x-3 gap-y-1 min-h-4">' in card
    # A custom bitrate says its unit.
    bitrate = card[card.index("number('bitrate_kbps'"):]
    assert bitrate.index("{t('kbps')}") < bitrate.index("</Group>")


def test_sizes_and_totals_read_the_way_the_size_limit_counts(tmp_path):
    """Decimal megabytes, as "Keep under N MB" and the engine count them; a
    finished export counts as whole in the bar; the summary adds the files up."""
    jobs = [
        _job(export_id=1, status="done", fraction=1.0, result=_result(20_000_000)),
        _job(export_id=2, status="done", fraction=1.0, result=_result(101_000_000, over=True)),
        _job(export_id=3, status="running", fraction=0.5),
        _job(export_id=4, status="failed", error="FFmpeg stopped (exit code 1)."),
        _job(export_id=5, status="failed", error="second"),
        _job(export_id=6, status="cancelled"),
    ]
    got = _run(tmp_path, "exportJobs", """
        return {
          sizes: [0, 640000, 4_549_000, 9_960_000, 23_400_000, 999_400_000, 1_250_000_000].map(m.formatBytes),
          overall: m.overallFraction(data),
          empty: m.overallFraction([]),
          tally: m.tally(data),
        }
    """, jobs)
    assert got["sizes"] == ["0 MB", "640 KB", "4.5 MB", "10 MB", "23 MB", "999 MB", "1.3 GB"]
    assert got["overall"] == pytest.approx(5.5 / 6)
    assert got["empty"] == 0
    assert got["tally"] == {"done": 2, "failed": 2, "cancelled": 1, "active": 1, "bytes": 121_000_000,
                            "over": 1, "firstError": "FFmpeg stopped (exit code 1)."}


def test_the_processing_bar_ignores_export_events(tmp_path):
    """An export runs beside the processing queue, not in it: its events
    must not light up or reset the processing bar (lib/jobProgress.ts)."""
    got = _run(tmp_path, "jobProgress", """
        const busy = {...m.emptyProgress, active: true, label: 'Rendering clips', fraction: 0.8, videoId: 'v1'}
        const event = {type: 'export', export: data}
        return [m.applyEvent(busy, event) === busy, m.applyEvent(m.emptyProgress, event).active]
    """, _job(status="running", fraction=0.5))
    assert got == [True, False]
    events = _source("lib/types.ts")
    assert re.search(r"type: [^\n]*\| 'export'\n\s*/\*\* export only: the export job as it is now\. \*/\n"
                     r"\s*export\?: ExportJob", events)


def _dashboard_feed(tmp_path, events):
    """The Dashboard's own describeEvent (pages/Dashboard.tsx), run under Node
    on `events` in order with one memory, as the page runs it: the line the
    Activity feed adds for each event, or None. Its t() shows each word it is
    given as «word», so a line shows which of its words were translated."""
    page = _source("pages/Dashboard.tsx")
    describe = page[page.index("function describeEvent("):]
    describe = describe[: describe.index("\n}\n") + 3]
    jobs = (LIB / "exportJobs.ts").read_text(encoding="utf-8")
    (tmp_path / "exportJobs.ts").write_text(jobs, encoding="utf-8")
    module = tmp_path / "dashboard.ts"
    module.write_text("import { exportActivityLine } from './exportJobs.ts'\n"
                      "const t = (s: string): string => `«${s}»`\n"
                      f"{describe}"
                      "export { describeEvent }\n", encoding="utf-8")
    return _run_file(tmp_path, module, """
        const said = new Map()
        return data.map((e) => m.describeEvent(e, said))
    """, events)


def test_the_dashboard_feed_tells_when_an_export_starts_and_ends_never_its_json(tmp_path):
    """While an export runs the lane sends about two events a second, each a
    little further along (a 126 s export sent 245). The Dashboard's Activity
    feed is always on screen: it adds one line when an export starts and one
    when it ends, in translated plain words, and never the event's JSON, which
    holds the export folder and the whole preset."""
    first = [
        _job(status="queued"),
        _job(status="running"),
        _job(status="queued"),  # the route's own 'queued', published after the lane's 'running'
        *[_job(status="running", fraction=round(i / 245, 4)) for i in range(1, 244)],
        _job(status="done", fraction=1.0, result=_result()),
        _job(status="running", fraction=0.99),  # a progress event that arrives after the end
    ]
    others = [
        _job(export_id=2, status="queued", name="second"),
        _job(export_id=2, status="running", name="second"),
        _job(export_id=2, status="running", fraction=0.4, cancelling=True, name="second"),
        _job(export_id=2, status="cancelled", fraction=0.4, name="second"),
        _job(export_id=3, status="queued", name="third"),
        _job(export_id=3, status="cancelled", name="third"),  # cancelled while it waited
        _job(export_id=4, status="running", fraction=0.7, name="fourth"),  # the feed opened mid-export
        _job(export_id=4, status="failed", fraction=0.7, name="fourth",
             error="FFmpeg stopped (exit code 1)."),
        _job(export_id=5, status="failed", name="fifth"),
        _job(export_id=6, status="done", fraction=1.0, result=_result(101_000_000, over=True)),
    ]
    events = [{"type": "export", "export": job} for job in first + others]
    events += [
        {"type": "export"},
        {"type": "job", "job_id": 7, "status": "done"},
        {"type": "progress", "stage": "render", "clip": 2, "total": 5},
        {"type": "model_pull", "tag": "gemma3:4b", "status": "success"},
    ]
    lines = _dashboard_feed(tmp_path, events)

    assert len(lines) == len(events)
    assert [line for line in lines[: len(first)] if line is not None] == [
        "«Exporting» my-clip…",
        "«Exported» my-clip.mp4 · 23 MB · 720×1280",
    ]
    assert [line for line in lines[len(first):] if line is not None] == [
        "«Exporting» second…",
        "«Export cancelled:» second",
        "«Export cancelled:» third",
        "«Exporting» fourth…",
        "«Export failed:» fourth · FFmpeg stopped (exit code 1).",
        "«Export failed:» fifth · «the engine gave no reason.»",
        "«Exported» my-clip.mp4 · 101 MB · 720×1280 · ⚠ «over the size limit»",
        # Everything else reads as it always has.
        "Job 7: done",
        "Rendering clip 2/5",
        "Model gemma3:4b: success",
    ]
    assert not any(line.lstrip().startswith("{") for line in lines if line is not None)

    # A null is no line at all, not an empty one.
    page = _source("pages/Dashboard.tsx")
    assert re.search(r"const msg = describeEvent\(e, exportsSaid\.current\)\n\s*if \(msg !== null\) \{", page)


def test_the_feed_forgets_old_exports_but_never_a_running_one(tmp_path):
    """The feed remembers the 200 exports it heard from last. A running export
    is heard from twice a second, so however many are queued behind it, its
    progress never repeats its "Exporting" line."""
    running = _job(export_id=1, status="running", fraction=0.1)
    queued = [_job(export_id=i, status="queued") for i in range(2, 302)]
    got = _run(tmp_path, "exportJobs", """
        const said = new Map()
        const lines = [m.exportActivityLine(said, data.running)]
        for (const job of data.queued) {
          lines.push(m.exportActivityLine(said, job))
          lines.push(m.exportActivityLine(said, {...data.running, fraction: 0.2}))
        }
        return {
          lines: lines.filter((line) => line !== null),
          size: said.size,
          oldest: said.has(m.jobKey(data.queued[0])),
          newest: said.has(m.jobKey(data.queued[299])),
        }
    """, {"running": running, "queued": queued})
    assert got == {"lines": ["Exporting my-clip…"], "size": 200, "oldest": False, "newest": True}


# ---- the screens -------------------------------------------------------------------------------


def test_original_exports_exactly_as_before():
    """With Original (the default), the Export button and Export all show what
    they always showed; only an answer with jobs takes the new path."""
    api = _source("lib/api.ts")
    assert "exportClip: async (id: number, folder: string, preset: ExportPresetChoice = 'default') =>" in api
    assert "exportBatch: async (clipIds: number[], folder: string, preset: ExportPresetChoice = 'default') =>" in api
    assert "body: JSON.stringify({ folder, preset })" in api
    assert "body: JSON.stringify({ clip_ids: clipIds, folder, preset })" in api

    editor = _source("components/ClipEditor.tsx")
    one = editor[editor.index("const exportOne = "):]
    one = one[: one.index("\n    })\n")]
    assert re.search(r"const res = await api\.exportClip\(clip\.id, folder\)\n\s*if \(res\.jobs\?\.length\) \{\n"
                     r"\s*exports\?\.start\(res\.jobs\)\n(\s*setShowProgress\(.*\)\n)?\s*return\n\s*\}\n"
                     r"\s*flash\(res\.exported\.length \? `Exported: \$\{res\.exported\[0\]\}` : 'Nothing exported'\)\n"
                     r"\s*onChanged\(\)", one)

    studio = _source("pages/ClipStudio.tsx")
    every = studio[studio.index("const exportAll = "):]
    every = every[: every.index("\n  }\n")]
    lane = every.index("if (res.jobs?.length) {")
    assert every.index("exports.start(res.jobs, { folder, starred: skipped, missing: res.skipped?.length ?? 0 })") > lane
    assert every.index("return", lane) < every.index("const n = res.exported.length")
    # The copy's notice is untouched.
    assert "`Exported ${n} clip${n === 1 ? '' : 's'} to ${folder}.` +" in every
    assert "(missing > 0 ? ` ${missing} had no video file to copy.` : '')" in every


def test_a_background_export_shows_progress_a_result_and_can_be_cancelled():
    editor = _source("components/ClipEditor.tsx")
    assert "const clipJobs = exports ? clipExports(exports.jobs.values(), clip.id, exports.started) : []" in editor
    assert "disabled={busy !== null || exporting}" in editor
    assert "<ExportProgress key={jobKey(job)} jobs={[job]} onCancel={exports.cancel} />" in editor
    studio = _source("pages/ClipStudio.tsx")
    assert "exports={exports}" in studio
    assert "disabled={exportingAll || batchRunning || toExport.length === 0}" in studio
    assert "<ExportProgress jobs={batchJobs} onCancel={exports.cancel} batch={exports.batch} />" in studio
    # A finished export stars its clip on the engine; the list is read again.
    assert "if (job.status === 'done' && activeVideo) void refreshClips(activeVideo)" in studio

    progress = _source("components/ExportProgress.tsx")
    assert 'role="progressbar"' in progress and "onCancel(active)" in progress
    # The result: the file, its size and its width and height.
    assert "{t('Exported')} {r.file} · {formatBytes(r.bytes)} · {r.width}×{r.height}" in progress

    hook = _source("lib/useExportJobs.ts")
    # Waiting exports are stopped before the running one, so the lane doesn't
    # start the next as the running one ends.
    assert ("[...jobs.filter((j) => j.status === 'queued'), ...jobs.filter((j) => j.status === 'running')]"
            in hook)
    assert "if (e.type === 'export' && e.export) take(mergeJobs(exportSession.jobs, [e.export]))" in hook


def test_the_calls_the_engine_guards_carry_the_session_header_and_json():
    """request() replaces the headers object when one is given, so a guarded
    call sends Content-Type itself (docs/editor-suite/api/step1-export.md)."""
    api = _source("lib/api.ts")
    helper = api[api.index("async function sessionHeaders()"):]
    helper = helper[: helper.index("\n}\n")]
    assert "const headers: Record<string, string> = { 'Content-Type': 'application/json' }" in helper
    assert "if (secret) headers['X-Clips-Kitty-Session'] = secret" in helper
    for call in ("exportClip: async", "exportBatch: async", "cancelExport: async", "saveExportDefault: async"):
        body = api[api.index(call):]
        body = body[: body.index("}),")]
        assert "headers: await sessionHeaders()" in body, call
    assert "request<{ ok: boolean; export: ExportPreset }>('/settings', {\n      method: 'PATCH'" in api
    # The default export preset never goes out through the unguarded settings call.
    assert "patchSettings: (patch: Partial<Omit<Settings, 'export'>> &" in api


def test_settings_has_the_export_card_and_woopsocial_the_smaller_copy():
    settings = _source("pages/Settings.tsx")
    assert re.search(r"<ExportFolderCard />\n(\s*\{/\*.*?\*/\}\n)?\s*<ExportSettingsCard />", settings, re.S)

    card = _source("components/ExportSettingsCard.tsx")
    for name in ("Export as", "Size", "Shape", "Fill", "Quality", "Format", "Frame rate", "Sound", "Keep under"):
        assert f"t('{name}')" in card, name
    # Only the formats this PC can make, asked of the engine; the estimate a minute; the upscale marker.
    assert re.search(r"api\s*\.exportCodecs\(\)", card) and re.search(r"api\s*\.exportEstimate\(\{ preset \}\)", card)
    assert "{t('Enlarged')}" in card and "estimate?.upscale" in card
    assert "api.saveExportDefault(asked)" in card

    woop = _source("components/WoopSocialCard.tsx")
    assert "api.patchWoopSocialSettings({ smaller_copy: on })" in woop
    assert "{t('Make a smaller copy when a clip is over 100 MB')}" in woop
    assert "checked={Boolean(status.smaller_copy)}" in woop


def test_sizes_start_at_720p():
    """The owner's call (2026-11-06): 240p-540p are gone from the card and the
    types; a custom size starts at 720 (the engine refuses less, and loads an
    old saved size at 720p: tests/test_export_presets.py)."""
    card = _source("components/ExportSettingsCard.tsx")
    sizes = card[card.index("const SIZES"):card.index("const SHAPES")]
    assert re.findall(r"\['([^']+)',", sizes) == ["same", "720p", "1080p", "1440p", "2160p", "custom"]
    types = _source("lib/types.ts")
    assert "export type ExportResolution = 'same' | '720p' | '1080p' | '1440p' | '2160p' | 'custom'" in types
    for removed in ("240p", "360p", "480p", "540p"):
        assert removed not in sizes and f"'{removed}'" not in types
    assert "t('Your own size in pixels, from 720 to 4096. It sets the shape too. Odd numbers are made even.')" in card


def test_what_a_size_limit_does_is_said_as_it_is():
    """A limit is said to keep a clip from growing only where it does: a clip
    that may fit, or one the app can't size up beforehand, is made as
    without the limit, and the smallest file made is the one kept. A file
    made again is "made again, smaller" (once or twice; at the floor too), and
    a miss says what happened instead of blaming the clip's length."""
    card = _source("components/ExportSettingsCard.tsx")
    assert "a clip that may fit, or one Clips Kitty can’t size up beforehand, is made just as it would be without " \
           "one, so short clips aren’t made bigger to fill it." in card
    assert "you get the smallest file made." in card
    progress = _source("components/ExportProgress.tsx")
    assert "t('It came out over the size limit, so it was made again, smaller.')" in progress
    assert "t('It came out over your size limit even after it was made again to fit. Try a slightly lower limit.')" \
        in progress
    assert "so Clips Kitty didn’t squeeze it down to your limit." in progress
    for gone in ("once more, a little smaller", "too small for a clip this long", "made as small as still looks good"):
        assert gone not in progress and gone not in card, gone


def test_the_new_screens_show_no_untranslated_english():
    """Every word the new export screens show goes through t(), the rule the
    new editor's code is held to (tests/test_ui_i18n_editor.py)."""
    found = [f"{name} {problem}"
             for name in ("components/ExportSettingsCard.tsx", "components/ExportProgress.tsx")
             for problem in untranslated(_source(name))]
    assert not found, "\n".join(found)
