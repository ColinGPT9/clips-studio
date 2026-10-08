"""The edit step in the SDK: what a plugin may suggest for a clip, how Clips
Kitty fits it to the clip and to the timeline editor (host.read_edits), and
the calls a plugin makes (job.suggest_edit).

Like every tests/test_plugin_sdk_*.py file, this needs only pytest and
imports nothing from Clips Kitty's engine, so it also runs in CI's SDK
(Windows) job. The examples are clips of Quarkbloom Arena, a made-up game.
"""

import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import ContractError, Suggested, contract, host, read_job  # noqa: E402

TIMELINE_EDITOR = ROOT / "ui" / "src" / "renderer" / "src" / "components" / "TimelineEditor.tsx"

# Two clips Clips Kitty is about to make, as an edit run hands them over.
CLIPS = [
    {"id": "m1", "start": 812.0, "end": 841.5, "score": 85, "found_score": 72, "found_by": "clipskitty",
     "label": "", "signals": {"text": 61}, "title": "He holds the bridge alone",
     "reason": "loud reaction and fast speech", "context": ["The final round of a Quarkbloom Arena match"]},
    {"id": "m2", "start": 900.0, "end": 925.0, "score": 64, "found_score": 58, "found_by": "clipskitty",
     "label": "", "signals": {}, "title": "", "reason": "", "context": []},
]
WINDOWS = {c["id"]: (c["start"], c["end"]) for c in CLIPS}
EDITOR = {"id": "example-dev/quarkbloom-trimmer", "version": "1.0.0", "permissions": ["transcript.read"]}
SEGMENTS = [{"start": 815.0, "end": 821.5, "text": "waiting for the respawn timer", "words": None}]
LIMITS = {"max_clips": 3, "min_duration": 10, "max_duration": 60}


def _answer(tmp_path, moments, *, name="run", **extra) -> Path:
    """A job folder holding an edit run's result.json with these answers."""
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(json.dumps({"plugin_api": 1, "ranges": [], "moments": moments, **extra}),
                                        encoding="utf-8")
    return folder


def _read(folder, *, crops=contract.CROPS, min_length=10, max_length=60, windows=WINDOWS):
    return host.read_edits(folder, windows=windows, crops=crops, min_length=min_length, max_length=max_length)


def _job(tmp_path, steps=("edit",), *, crops=contract.CROPS, moments=CLIPS, manifest=EDITOR, limits=LIMITS):
    """An edit run's job folder, built as the app builds it: the Job, and where it writes its lines."""
    folder = tmp_path / "edit-job"
    limits = {**limits, "crops": list(crops)} if "edit" in steps else dict(limits)
    job, transcript = host.build_job(manifest, transcript={"language": "en", "segments": SEGMENTS},
                                     limits=limits, output_dir=folder / "out", steps=list(steps), moments=moments)
    host.write_job(folder, job, transcript)
    out = io.StringIO()
    return read_job(folder, out=out), out


def _logged(out) -> list[str]:
    return [json.loads(line)["message"] for line in out.getvalue().splitlines()]


# ---- host.read_edits: what Clips Kitty keeps ----------------------------------------


def test_read_edits_fits_cuts_to_the_clip(tmp_path):
    folder = _answer(tmp_path, [
        {"id": "m1", "edit": {"cuts": [[815.0, 821.5], [820.0, 823.0], [800.0, 813.0], [840.0, 850.0],
                                       [950.0, 955.0]],
                              "reason": "Cuts the wait for the respawn timer"}},
        {"id": "m2", "edit": {"cuts": [[1.0, 2.0]]}},
        {"id": "m9", "edit": {"cuts": [[1.0, 2.0]]}}])
    edits, lines = _read(folder)
    # Clamped to the clip, joined where they overlap, outside spans dropped.
    assert edits == {"m1": {"edit": {"cuts": [[812.0, 813.0], [815.0, 823.0], [840.0, 841.5]]},
                            "reason": "Cuts the wait for the respawn timer"}}
    assert lines == [
        "ignored: m1's cut 950.0-955.0 s: it is outside the clip (812.0-841.5 s)",
        "ignored: m2's cut 1.0-2.0 s: it is outside the clip (900.0-925.0 s)",
        "ignored: m2's edit changes nothing",
        "ignored: an answer for m9, which isn't one of this run's moments",
    ]
    # An unrounded window, as the engine hands it over, keeps the cut's own seconds.
    edits, _ = _read(folder, windows={"m1": (812.004999, 841.49), "m2": (900.0, 925.0)})
    assert edits["m1"]["edit"]["cuts"] == [[812.005, 813.0], [815.0, 823.0], [840.0, 841.49]]
    # A sliver under a millisecond inside the clip changes nothing once it is
    # rounded, so it isn't kept as a span of no length.
    folder = _answer(tmp_path, [{"id": "m2", "edit": {"cuts": [[899.0, 900.0001]], "mutes": [[910.0, 910.0004]]}}],
                     name="sliver")
    assert _read(folder) == ({}, ["ignored: m2's edit changes nothing"])
    folder = _answer(tmp_path, [{"id": "m2", "edit": {"cuts": [[899.0, 900.0001]], "mutes": [[910.0, 911.0]]}}],
                     name="sliver-and-mute")
    assert _read(folder) == ({"m2": {"edit": {"mutes": [[910.0, 911.0]]}}}, [])


def test_cuts_that_leave_less_than_the_shortest_clip_are_ignored_and_mutes_stay(tmp_path):
    folder = _answer(tmp_path, [
        {"id": "m1", "edit": {"cuts": [[812.0, 837.5]], "mutes": [[830.2, 830.9], [830.5, 831.0]]}},
        # Pieces under a quarter of a second don't count: 9.9 s + 0.2 s left is under 10.
        {"id": "m2", "edit": {"cuts": [[900.2, 915.1]], "speed": 2}}])
    edits, lines = _read(folder)
    # The mutes stay, joined; with its cuts ignored, m2 is 25 s, and 12.5 s at 2x is enough.
    assert edits == {"m1": {"edit": {"mutes": [[830.2, 831.0]]}}, "m2": {"edit": {"speed": 2}}}
    assert lines == [
        "ignored: m1's cuts: they would leave 4.0 s, under this job's 10 s shortest clip",
        "ignored: m2's cuts: they would leave 9.9 s, under this job's 10 s shortest clip"]
    # Speed is measured on what the cuts leave; at 3x the editor's 2x is used.
    folder = _answer(tmp_path, [{"id": "m2", "edit": {"speed": 3}}], name="fast")
    assert _read(folder) == ({"m2": {"edit": {"speed": 2}}},
                             ["changed: m2's speed 3 to 2, the nearest the editor offers"])
    folder = _answer(tmp_path, [{"id": "m2", "edit": {"cuts": [[900.0, 908.0]], "speed": 2}}], name="cut-fast")
    assert _read(folder) == ({"m2": {"edit": {"cuts": [[900.0, 908.0]]}}},
                             [("ignored: m2's speed: at 2x the clip would be 8.5 s, under this job's 10 s "
                               "shortest clip")])
    folder = _answer(tmp_path, [{"id": "m1", "edit": {"speed": 0.5}}], name="slow")
    assert _read(folder, max_length=30) == ({}, [
        "ignored: m1's speed: at 0.75x the clip would be 39.3 s, over this job's 30 s longest clip",
        "ignored: m1's edit changes nothing"])
    # The floor is at least a second, whatever the job's shortest clip.
    folder = _answer(tmp_path, [{"id": "m2", "edit": {"cuts": [[900.0, 924.5]]}}], name="none")
    assert _read(folder, min_length=0)[1] == [
        "ignored: m2's cuts: they would leave 0.5 s, under this job's 1 s shortest clip",
        "ignored: m2's edit changes nothing"]


def test_fades_speed_and_hook_seconds_are_set_to_the_editors_choices(tmp_path):
    folder = _answer(tmp_path, [
        {"id": "m1", "edit": {"fade_in": 0.45, "fade_out": 3, "speed": 3,
                              "title_overlay": {"text": "Triple bloom!", "seconds": 10}, "volume": 0.804}},
        {"id": "m2", "edit": {"fade_in": 0.3, "fade_out": 0.1, "speed": 0.5, "volume": 1.001,
                              "title_overlay": {"text": "Bloom"}}}])
    edits, lines = _read(folder)
    assert edits == {
        "m1": {"edit": {"volume": 0.8, "fade_in": 0.5, "fade_out": 1, "speed": 2,
                        "title_overlay": {"text": "Triple bloom!", "seconds": 8}}},
        "m2": {"edit": {"fade_in": 0.3, "speed": 0.75, "title_overlay": {"text": "Bloom", "seconds": 3}}}}
    assert lines == [
        "changed: m1's fade_in 0.45 s to 0.5 s, the nearest the editor offers",
        "changed: m1's fade_out 3 s to 1 s, the nearest the editor offers",
        "changed: m1's speed 3 to 2, the nearest the editor offers",
        "changed: m1's title_overlay seconds 10 s to 8 s, the nearest the editor offers",
        "changed: m2's fade_out 0.1 s to 0 s, the nearest the editor offers",
        "changed: m2's speed 0.5 to 0.75, the nearest the editor offers",
    ]
    # Ties go toward no change: the smaller fade or seconds, the speed nearer 1.
    nearest = contract.nearest_choice
    assert [nearest(x, contract.FADE_CHOICES) for x in (0.4, 0.15, 0.75, 0.45, 0.8)] == [0.3, 0, 0.5, 0.5, 1]
    assert [nearest(x, contract.SPEED_CHOICES, toward=1) for x in (0.875, 1.125, 1.75, 1.2, 2.5)] == [
        1, 1, 1.5, 1.25, 2]
    assert [nearest(x, contract.HOOK_SECONDS_CHOICES) for x in (2.5, 4, 6.5, 1, 7)] == [2, 3, 5, 2, 8]
    folder = _answer(tmp_path, [{"id": "m1", "edit": {"fade_out": 0.4, "speed": 1.125,
                                                      "title_overlay": {"text": "Go", "seconds": 4}}}], name="ties")
    assert _read(folder) == ({"m1": {"edit": {"fade_out": 0.3, "title_overlay": {"text": "Go", "seconds": 3}}}}, [
        "changed: m1's fade_out 0.4 s to 0.3 s, the nearest the editor offers",
        "changed: m1's speed 1.125 to 1, the nearest the editor offers",
        "changed: m1's title_overlay seconds 4 s to 3 s, the nearest the editor offers"])


def _numbers(text: str) -> tuple[float, ...]:
    return tuple(float(n) for n in re.findall(r"\d+(?:\.\d+)?", text))


def test_the_choices_are_the_editors_own():
    """The fades, speeds and hook title lengths Clips Kitty fits a suggestion
    to are the ones the timeline editor's controls offer, so every control
    shows what the clip will get. Changing a list there fails here until the
    SDK's list follows. The layouts are the editor's Layout buttons."""
    text = TIMELINE_EDITOR.read_text(encoding="utf-8")
    fades = re.search(r"\['fade_in', 'fade_out'\] as const\)\.map(.*?)</select>", text, re.S).group(1)
    assert _numbers(" ".join(re.findall(r"<option value=\{([\d.]+)\}", fades))) == tuple(
        map(float, contract.FADE_CHOICES))
    speeds = re.search(r"value=\{edit\.speed \?\? 1\}.*?\{\[([\d., ]+)\]\.map", text, re.S).group(1)
    assert _numbers(speeds) == tuple(map(float, contract.SPEED_CHOICES))
    seconds = re.search(r"value=\{edit\.hook\.seconds\}.*?\{\[([\d., ]+)\]\.map", text, re.S).group(1)
    assert _numbers(seconds) == tuple(map(float, contract.HOOK_SECONDS_CHOICES))
    layouts = re.search(r"<span className=\"text-muted\">Layout</span>(.*?)\] as const", text, re.S).group(1)
    buttons = re.findall(r"^\s*\['(\w+)',", layouts, re.M)
    assert set(contract.CROPS) == set(buttons) - {"split"}  # split is the Gaming / Reaction layout


def test_fields_outside_the_allow_list_are_ignored_and_logged(tmp_path):
    never = {"music": {"path": "plugin-song.mp3"}, "watermark": {"text": "plugin-mark"}, "mute_all": True,
             "muted_words": [{"start": 830.2, "end": 830.9, "word": "x"}], "gaming": {"preset": "split"},
             "podcast": True, "profile": "loud", "sport": "soccer", "caption_lines": 2, "filter": "warm",
             "adjust": {"contrast": 2}, "start": 800.0}
    folder = _answer(tmp_path, [
        {"id": "m1", "edit": {"keep": [[812.0, 820.0]], "hook": {"text": "WATCH"}, **never, "fade_in": 0.3}},
        {"id": "m2", "edit": {"keep": [[900.0, 910.0]]}}])
    edits, lines = _read(folder)
    assert edits == {"m1": {"edit": {"fade_in": 0.3}}}
    assert lines == [
        "ignored: m1's keep: write the spans to take out as cuts",
        "ignored: m1's hook: write the hook title as title_overlay",
        ("ignored: music, watermark, mute_all, muted_words, gaming, podcast, profile, sport, caption_lines, "
         "filter, adjust, start in m1's edit: Clips Kitty doesn't take them from a plugin"),
        "ignored: m2's keep: write the spans to take out as cuts",
        "ignored: m2's edit changes nothing",
    ]
    # Each name is put on one line and cut short, and only the first 12 are named.
    odd = {"music\nchanged: m1's speed 2 to 1, the nearest the editor offers": 1, "v" * 100: 1,
           **{f"extra{k}": k for k in range(20)}}
    _, lines = _read(_answer(tmp_path, [{"id": "m1", "edit": {**odd, "fade_in": 0.3}}], name="odd"))
    assert lines == [("ignored: music changed: m1's speed 2 to 1, " + "v" * 32 + ", extra0, extra1, extra2, extra3, "
                      "extra4, extra5, extra6, extra7, extra8, extra9 and 10 more in m1's edit: Clips Kitty "
                      "doesn't take them from a plugin")]
    # None of them is refused: the answer is only checked for the fields it names.
    assert contract.check_result(json.loads((folder / "result.json").read_text(encoding="utf-8")),
                                 steps=["edit"]) == []
    # Scores, notes and ranges in an edit run are ignored as in any moment run.
    folder = _answer(tmp_path, [{"id": "m1", "score": 90, "context": ["A note"], "edit": {"volume": 1}}],
                     name="extra", ranges=[{"start": 1, "end": 5}])
    assert _read(folder) == ({}, ["ignored: m1's edit changes nothing",
                                  "ignored: 1 range(s): this run was asked about moments, not to find new ones",
                                  "ignored: scores, because this run wasn't asked to rate",
                                  "ignored: notes, because this run wasn't asked to understand"])


def test_a_crop_the_job_does_not_use_is_ignored_not_refused(tmp_path):
    folder = _answer(tmp_path, [{"id": "m1", "edit": {"crop": "center"}},
                                {"id": "m2", "edit": {"crop": "bias_left", "fade_out": 0.5}}])
    assert contract.check_result(json.loads((folder / "result.json").read_text(encoding="utf-8")),
                                 steps=["edit"]) == []
    assert _read(folder) == ({"m1": {"edit": {"crop": "center"}}, "m2": {"edit": {"fade_out": 0.5}}},
                             ["ignored: m2's crop \"bias_left\": this job's clips don't use \"bias_left\""])
    # A job whose clips use no layout (Sports, Podcast, Gaming, Vertical Live, whole frame).
    assert _read(folder, crops=()) == ({"m2": {"edit": {"fade_out": 0.5}}}, [
        "ignored: m1's crop \"center\": this job's clips don't use a layout",
        "ignored: m1's edit changes nothing",
        "ignored: m2's crop \"bias_left\": this job's clips don't use a layout"])


def test_title_overlay_is_one_line_without_web_addresses(tmp_path):
    folder = _answer(tmp_path, [
        {"id": "m1", "edit": {"title_overlay": {"text": "Triple\nbloom!\r\n\tSee https://example.com/x and "
                                                        "www.example.com", "seconds": 3}}},
        {"id": "m2", "edit": {"title_overlay": {"text": " http://example.com/a\n\nwww.example.org "}}}])
    edits, lines = _read(folder)
    # A bare domain or an @handle is not a web address that starts with http, https or www.
    assert edits == {"m1": {"edit": {"title_overlay": {"text": "Triple bloom! See and", "seconds": 3}}}}
    assert lines == [("ignored: m2's title_overlay: no text is left once web addresses and line breaks are "
                      "taken out"), "ignored: m2's edit changes nothing"]
    folder = _answer(tmp_path, [{"id": "m1", "edit": {"title_overlay": {"text": "example.com @quarkbloom " + "x" * 96},
                                                      "reason": "See\nhttps://example.com " + "r" * 130}}],
                     name="long")
    edits, _ = _read(folder)
    assert edits["m1"]["edit"]["title_overlay"]["text"] == "example.com @quarkbloom " + "x" * 96
    assert edits["m1"]["reason"] == "See " + "r" * 130
    # An invisible character can't hide a web address: a zero-width space, a
    # word joiner, a byte order mark, a soft hyphen or a right-to-left override.
    hidden = ["Join https\u200b://example.com/promo", "Join https\u2060://example.com/promo",
              "Join \ufeffwww.example.com", "Join ww\u200bw.example.com", "Join ht\u00adtps://example.com",
              "Join \u202ewww.example.com\u202c", "Join \u2066https://example.com\u2069"]
    for k, text in enumerate(hidden):
        folder = _answer(tmp_path, [{"id": "m1", "edit": {"title_overlay": {"text": text}, "reason": text}}],
                         name=f"hidden{k}")
        edits, _ = _read(folder)
        assert edits == {"m1": {"edit": {"title_overlay": {"text": "Join", "seconds": 3}}, "reason": "Join"}}, text
    # A reversed address isn't one, but it no longer shows as one either.
    edits, _ = _read(_answer(tmp_path, [{"id": "m1", "edit": {"title_overlay": {
        "text": "Triple bloom \u202emoc.elpmaxe.www"}}}], name="reversed"))
    assert edits["m1"]["edit"]["title_overlay"]["text"] == "Triple bloom moc.elpmaxe.www"
    # Joiners that emoji and some scripts need stay.
    kept = "Quinn \U0001F469\u200d\U0001F467 \u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"
    edits, _ = _read(_answer(tmp_path, [{"id": "m1", "edit": {"title_overlay": {"text": kept}}}], name="joiners"))
    assert edits["m1"]["edit"]["title_overlay"]["text"] == kept


# ---- job.suggest_edit: what a plugin calls ------------------------------------------


def test_suggest_edit_checks_at_once_and_adds_cuts(tmp_path):
    job, out = _job(tmp_path)
    m1, m2 = job.moments
    s = job.suggest_edit(m1)
    assert job.suggest_edit(m1) is s and job.suggest_edit(m2) is not s
    assert s.cut(815.0, 818.0).cut(817.0, 821.5).cut(830, 831) is s
    assert s.cuts == ((815.0, 821.5), (830.0, 831.0))  # cuts add up; overlapping ones join
    assert s.length == pytest.approx(29.5 - 6.5 - 1.0)
    s.cut(950.0, 955.0)  # wholly outside the clip: logged once, not kept
    s.cut(950.0, 955.0)
    assert s.cuts == ((815.0, 821.5), (830.0, 831.0))
    for call, message in (
            (lambda: s.cut(830.0, 820.0), "cut needs start < end, in seconds of the video (got 830.0 to 820.0)"),
            (lambda: s.cut(-1, 820.0), "cut needs a start of 0 or more, in seconds of the video (got -1)"),
            (lambda: s.cut("a", 820.0), "cut needs numbers of seconds of the video (got 'a' to 820.0)"),
            (lambda: s.mute(5, 5), "mute needs start < end, in seconds of the video (got 5 to 5)"),
            (lambda: s.speed(4), "speed must be a number from 0.5 to 3 (got 4)"),
            (lambda: s.volume(-0.5), "volume must be a number from 0 to 2 (got -0.5)"),
            (lambda: s.fade(fade_out=3.5), "fade_out must be a number from 0 to 3 (got 3.5)"),
            (lambda: s.title_overlay(" \n "), "title_overlay needs text (got ' \\n ')"),
            (lambda: s.title_overlay("Go", seconds=11), "title_overlay seconds must be a number from 1 to 10 (got 11)"),
            (lambda: s.crop("c" * 33), "crop must be text of 1 to 32 characters (got '" + "c" * 33 + "')"),
            (s.keep_only, "keep_only needs at least one (start, end) span to keep"),
            (lambda: s.keep_only((830, 820)), "keep_only needs start < end, in seconds of the video (got 830 to 820)"),
            (lambda: s.trim(830, 820), "trim needs start < end, in seconds of the video (got 830 to 820)"),
            (lambda: s.trim(start=950), ("trim needs start < end, in seconds of the video (got 950 to the clip's "
                                         "end, 841.5)")),
            (lambda: s.trim(end=800), ("trim needs start < end, in seconds of the video (got the clip's start, "
                                       "812 to 800)"))):
        with pytest.raises(ContractError) as e:
            call()
        assert str(e.value) == f"suggest_edit: {message}"
    assert s.cuts == ((815.0, 821.5), (830.0, 831.0))  # a refused call keeps nothing
    many = job.suggest_edit(m2)
    for k in range(20):
        many.mute(900 + k, 900.5 + k)
    with pytest.raises(ContractError, match=r"^suggest_edit: at most 20 mutes for one clip$"):
        many.mute(921, 921.5)
    assert len(many.mutes) == 20
    many.mute(900.2, 900.4)  # inside one already there: still 20

    # trim and keep_only are cuts at the ends and between the spans kept.
    job, _ = _job(tmp_path / "trim")
    m1, m2 = job.moments
    t = job.suggest_edit(m1).trim(start=815.0)
    assert t.cuts == ((812.0, 815.0),) and t.length == pytest.approx(26.5)
    t.trim(end=840.0)
    assert t.cuts == ((812.0, 815.0), (840.0, 841.5))
    k = job.suggest_edit(m2).keep_only((902.0, 910.0), (915.0, 930.0), (800.0, 801.0))
    assert k.cuts == ((900.0, 902.0), (910.0, 915.0))
    assert k.length == pytest.approx(18.0)
    k.speed(1.4)  # Clips Kitty uses the editor's 1.5x: 18 s becomes 12 s
    assert k.length == pytest.approx(12.0)

    # What finish() writes: the edit as the plugin set it, checked like any answer.
    job, out = _job(tmp_path / "finish")
    m1, m2 = job.moments
    (job.suggest_edit(m1).cut(815.0, 821.5).mute(830.2, 830.9).fade(fade_out=0.4).volume(0.8)
     .title_overlay("Triple\nbloom!", seconds=3).crop("center").reason("Cuts the wait\nfor the respawn timer"))
    job.suggest_edit(m2)  # nothing set: no answer
    job.rate(m1, 90)  # not asked to rate: logged, not kept
    written = json.loads(job.finish().read_text(encoding="utf-8"))
    assert written["moments"] == [{"id": "m1", "edit": {
        "cuts": [[815.0, 821.5]], "mutes": [[830.2, 830.9]], "volume": 0.8, "fade_out": 0.4,
        "title_overlay": {"text": "Triple bloom!", "seconds": 3.0}, "crop": "center",
        "reason": "Cuts the wait for the respawn timer"}}]
    assert contract.check_result(written, steps=["edit"]) == []
    assert _logged(out) == ["m1: fade_out 0.4 s will be 0.3 s, the nearest the editor offers",
                            "rate ignored: this job didn't ask for ratings"]


def test_finish_says_what_clips_kitty_will_ignore_for_the_clips_length(tmp_path):
    job, out = _job(tmp_path)
    m1, m2 = job.moments
    job.suggest_edit(m1).keep_only((812.0, 816.0))  # 4 s left
    job.suggest_edit(m2).trim(900.0, 916.0).speed(2)  # 16 s at 2x is 8 s
    job.finish()
    assert _logged(out) == [
        "m1: cuts leave 4.0 s, under this job's 10 s shortest clip: Clips Kitty will ignore the cuts",
        "m2: cuts and speed leave 8.0 s, under this job's 10 s shortest clip: Clips Kitty will ignore the speed"]
    edits, lines = host.read_edits(job.folder, windows=WINDOWS, crops=job.limits.crops, min_length=10,
                                   max_length=60)
    assert edits == {"m2": {"edit": {"cuts": [[916.0, 925.0]]}}}
    assert lines == ["ignored: m1's cuts: they would leave 4.0 s, under this job's 10 s shortest clip",
                     "ignored: m1's edit changes nothing",
                     "ignored: m2's speed: at 2x the clip would be 8.0 s, under this job's 10 s shortest clip"]


def test_suggest_edit_outside_an_edit_run_is_ignored(tmp_path):
    job, out = _job(tmp_path, ("rate",))
    assert not job.wants("edit") and job.limits.crops == ()
    m1, _ = job.moments
    s = job.suggest_edit(m1).cut(815.0, 821.5)
    job.suggest_edit(m1).fade(fade_in=0.3)
    assert job.suggest_edit(m1) is s and s.cuts == ((815.0, 821.5),)
    with pytest.raises(ContractError):  # still checked, so one function serves every run
        s.speed(9)
    # Nothing is said about what Clips Kitty would do to a suggestion it never
    # gets: no nearest choice, no crop the job doesn't use, no span outside the clip.
    _, m2 = job.moments
    job.suggest_edit(m2).fade(fade_out=0.4).crop("center").cut(950, 960).speed(1.1).title_overlay("Go", seconds=4)
    job.rate(m1, 80)
    written = json.loads(job.finish().read_text(encoding="utf-8"))
    assert written["moments"] == [{"id": "m1", "score": 80.0, "reason": ""}]
    assert _logged(out) == ["suggest_edit ignored: this job didn't ask for edits"]
    # On a range of the plugin's own, in a find run.
    job, out = _job(tmp_path / "find", ("find",), moments=None)
    own = job.add_range(10, 40, score=70)
    job.suggest_edit(own).trim(12).fade(fade_out=0.4).crop("center").cut(50, 60).speed(1.1)
    assert job.suggest_edit(own).cuts == ((10.0, 12.0),)
    written = json.loads(job.finish().read_text(encoding="utf-8"))
    assert "moments" not in written and "edit" not in written["ranges"][0]
    assert _logged(out) == [("suggest_edit ignored on r1: Clips Kitty asks for edits on the clips it hands "
                             "over (job.moments)")]
    # A moment from another job is refused, as rate() refuses it.
    other, _ = _job(tmp_path / "other")
    with pytest.raises(ContractError, match="m1 is not a moment of this job"):
        job.suggest_edit(other.moments[0])


def test_a_rate_run_logs_edits_as_ignored(tmp_path):
    folder = _answer(tmp_path, [{"id": "m1", "score": 80, "edit": {"speed": 9, "cuts": "all"}},
                                {"id": "m2", "edit": {"fade_in": 0.3}}])
    # Not checked in a rate run, so not refused; logged, and the scores are unchanged.
    answers, ignored = host.read_answers(folder, steps=["rate"], ids=["m1", "m2"])
    assert answers == {"m1": {"score": 80.0, "reason": ""}}
    assert ignored == ["ignored: edits, because this run wasn't asked to suggest edits"]
    answers, ignored = host.read_answers(folder, steps=["understand"], ids=["m1", "m2"])
    assert answers == {} and ignored == ["ignored: scores, because this run wasn't asked to rate",
                                         "ignored: edits, because this run wasn't asked to suggest edits"]
    # In an edit run the same answer is refused whole: speed 9 is outside the render's limits.
    with pytest.raises(ContractError, match=r"moments\[0\]: edit.speed must be a number from 0.5 to 3"):
        _read(folder)


def test_suggested_text_needs_transcript_read(tmp_path):
    """What earlier edit plugins suggested reaches a later one read-only, in
    job.json and on its Moments. Their reason and hook title are plugin text
    that may come from what was said, so they are left out without
    transcript.read, as a moment's title, reason and notes are."""
    earlier = {"by": "example-dev/quarkbloom-framer", "name": "Quarkbloom Framer",
               "edit": {"crop": "center", "fade_in": 0.3, "title_overlay": {"text": "Bloom!", "seconds": 3}},
               "reason": "Keeps both players in frame"}
    clips = [{**CLIPS[0], "suggested": [earlier]}, CLIPS[1]]
    job, _ = _job(tmp_path, moments=clips)
    m1, m2 = job.moments
    assert m1.suggested == (Suggested(by="example-dev/quarkbloom-framer", name="Quarkbloom Framer",
                                      edit=earlier["edit"], reason="Keeps both players in frame"),)
    assert m2.suggested == ()
    assert job.data["moments"][0]["suggested"] == [earlier] and "suggested" not in job.data["moments"][1]
    blind, _ = _job(tmp_path / "blind", moments=clips, manifest={**EDITOR, "permissions": []})
    assert blind.data["moments"][0]["suggested"] == [
        {"by": "example-dev/quarkbloom-framer", "name": "Quarkbloom Framer",
         "edit": {"crop": "center", "fade_in": 0.3}}]
    assert blind.moments[0].suggested == (Suggested(by="example-dev/quarkbloom-framer", name="Quarkbloom Framer",
                                                    edit={"crop": "center", "fade_in": 0.3}),)
    assert blind.moments[0].title == "" and blind.moments[0].context == ()
    # The engine's own copy is never changed by the gating.
    assert clips[0]["suggested"][0]["edit"]["title_overlay"] == {"text": "Bloom!", "seconds": 3}
    # An edit run's limits say which layouts the clips can use; a moment run's don't.
    assert job.limits.crops == ("track", "center", "letterbox") and job.limits.min_score is None
    assert job.wants("edit") and job.steps == ("edit",)
