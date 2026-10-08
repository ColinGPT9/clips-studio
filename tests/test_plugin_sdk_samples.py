"""The SDK's sample tools: the test video and transcript (`sample`,
`run --sample`) and the frame grab (`frame`), from clipskitty_sdk.samples.

Like every tests/test_plugin_sdk_*.py file, this needs only pytest and
PyYAML and imports nothing from Clips Kitty's engine, so it also runs in CI's
SDK (Windows) job. Tests that make or read video skip without FFmpeg; the
ones that check what happens without it clear PATH, so they run everywhere.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import devrun, host, read_job, samples  # noqa: E402
from clipskitty_sdk._hints import python_command  # noqa: E402

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="needs FFmpeg and FFprobe")
# The MIT example whose loudness and scene-cut code the sample was measured with.
EXAMPLE = ROOT / "examples" / "pipelines" / "scene-cut-highlights" / "src" / "main.py"

MANIFEST = """\
manifest_version: 1
id: example-dev/{name}
name: {title}
version: 1.0.0
kind: pipeline
capability: highlight_detection
description: A test plugin for Quarkbloom Arena (a made-up game).
license: MIT
requires: {{clips_kitty: '>=2.0', plugin_api: 1}}
run:
  command: ['{{python}}', src/main.py]
execution: local
inputs: [{inputs}]
outputs: [{outputs}]
permissions: [{permissions}]
"""

# Finds the moment where "quark burst" is said, and runs on past it.
WORDS_FINDER_MAIN = '''\
import json

from clipskitty_sdk import run


def main(job):
    (job.output_dir / "seen.json").write_text(json.dumps(job.data), encoding="utf-8")
    for s in job.transcript.segments():
        if "quark burst" in s["text"]:
            job.add_range(s["start"] - 4, s["end"] + 20, score=80, label="words_said",
                          reason='the commentary says "quark burst"')


run(main)
'''

# Rates the moments it is handed by what is said in them.
WORDS_RATER_MAIN = '''\
import json

from clipskitty_sdk import run


def main(job):
    (job.output_dir / "seen.json").write_text(json.dumps(job.data), encoding="utf-8")
    for m in job.moments:
        said = job.text(m)
        if "quark burst" in said:
            job.rate(m, 75, reason='the commentary says "quark burst"')
        elif "waiting" in said:
            job.rate(m, 10, reason='the commentary says "waiting"')


run(main)
'''

# Looks at the video it is given: its size on disk, and a moment on the banner.
VIDEO_FINDER_MAIN = '''\
import json

from clipskitty_sdk import run


def main(job):
    seen = dict(job.data, video_bytes=job.video.path.stat().st_size)
    (job.output_dir / "seen.json").write_text(json.dumps(seen), encoding="utf-8")
    job.add_range(22, 27, score=90, label="quark_burst", reason="the banner shows")


run(main)
'''


def _plugin(tmp_path, name, *, inputs, outputs, permissions, main) -> Path:
    folder = tmp_path / name
    (folder / "src").mkdir(parents=True)
    (folder / "clipskitty.yaml").write_text(MANIFEST.format(
        name=name, title=name.replace("-", " ").title(), inputs=inputs, outputs=outputs,
        permissions=permissions), encoding="utf-8")
    (folder / "src" / "main.py").write_text(main, encoding="utf-8")
    return folder


def _words_finder(tmp_path) -> Path:
    return _plugin(tmp_path, "quarkbloom-words", inputs="transcript", outputs="ranges",
                   permissions="transcript.read", main=WORDS_FINDER_MAIN)


def _words_rater(tmp_path) -> Path:
    return _plugin(tmp_path, "quarkbloom-rater", inputs="moments, transcript", outputs="ratings",
                   permissions="transcript.read", main=WORDS_RATER_MAIN)


def _video_finder(tmp_path) -> Path:
    return _plugin(tmp_path, "quarkbloom-banner", inputs="video, transcript", outputs="ranges",
                   permissions="video.read, transcript.read, ffmpeg", main=VIDEO_FINDER_MAIN)


def _cli(*args) -> int:
    from clipskitty_sdk.__main__ import main

    return main([str(a) for a in args])


def _run(tmp_path, plugin: Path, *args, job="job") -> int:
    """`python -m clipskitty_sdk run`, without FFprobe, in tmp_path/job."""
    return _cli("run", plugin, "--job-dir", tmp_path / job, "--ffprobe", tmp_path / "no-ffprobe", *args)


def _seen(tmp_path, job="job") -> dict:
    return json.loads((tmp_path / job / "out" / "seen.json").read_text(encoding="utf-8"))


def _files(folder: Path) -> set:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if "__pycache__" not in p.parts}


def _no_ffmpeg(tmp_path, monkeypatch) -> None:
    """PATH cleared of FFmpeg (and of everything else)."""
    empty = tmp_path / "empty-path"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))
    assert shutil.which("ffmpeg") is None and shutil.which("ffprobe") is None


def _rgb(path: Path, at=None) -> tuple[int, int, bytes]:
    """A video's frame at `at` seconds, or a picture: (width, height, RGB bytes), as FFmpeg reads it."""
    seek = ["-ss", str(at)] if at is not None else []
    done = subprocess.run([FFMPEG, "-v", "error", *seek, "-i", str(path), "-frames:v", "1", "-f", "image2pipe",
                           "-c:v", "ppm", "-"], capture_output=True, check=True, timeout=120)
    magic, size, _, rest = done.stdout.split(b"\n", 3)
    width, height = (int(n) for n in size.split())
    assert magic == b"P6"
    return width, height, rest


def _pixel(picture, x: int, y: int) -> tuple:
    width, _, rgb = picture
    return tuple(rgb[(y * width + x) * 3:(y * width + x) * 3 + 3])


def _near(pixel, colour, tolerance=24) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, colour))


def _example():
    spec = importlib.util.spec_from_file_location("scene_cut_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def made_sample(tmp_path_factory) -> Path:
    if not (FFMPEG and FFPROBE):
        pytest.skip("needs FFmpeg and FFprobe")
    return samples.make_sample(tmp_path_factory.mktemp("media") / "sample.mp4", FFMPEG)[0]


# ---- the sample ---------------------------------------------------------------------


@needs_ffmpeg
def test_the_sample_has_the_banner_the_loud_part_and_three_cuts(tmp_path, capsys):
    out = tmp_path / "media" / "sample.mp4"
    assert _cli("sample", out) == 0
    assert capsys.readouterr().out == f"wrote {out} and {tmp_path / 'media' / 'sample.transcript.json'}: " \
                                      f"{samples.SAMPLE_NOTE}\n"
    assert sorted(p.name for p in out.parent.iterdir()) == ["sample.mp4", "sample.transcript.json"]
    probe = json.loads(subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_name,width,height", "-of", "json",
         str(out)], capture_output=True, text=True, check=True, timeout=60).stdout)
    assert abs(float(probe["format"]["duration"]) - samples.SAMPLE_VIDEO_SECONDS) < 0.05
    assert [s["codec_name"] for s in probe["streams"]] == ["mpeg4", "aac"]
    assert (probe["streams"][0]["width"], probe["streams"][0]["height"]) == (640, 360)

    # The red banner, read the way a pipeline would: its region, 4 frames a second, shrunk to 32x8.
    x, y, w, h = samples.region_pixels(samples.parse_region(samples.BANNER_REGION), 640, 360)
    assert (x, y, w, h) == (192, 36, 256, 36)
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(out), "-an", "-vf",
                          f"fps=4,crop={w}:{h}:{x}:{y},scale=32:8:flags=area", "-f", "rawvideo", "-pix_fmt",
                          "rgb24", "-"], capture_output=True, check=True, timeout=120).stdout
    size = 32 * 8 * 3
    assert len(raw) // size == 160
    red = [i / 4 for i in range(len(raw) // size)
           if sum(1 for j in range(i * size, (i + 1) * size, 3)
                  if _near(raw[j:j + 3], (0xE0, 0x30, 0x3A), 40)) / (32 * 8) > 0.5]
    assert red == [22 + k / 4 for k in range(20)]  # 22.0 to 26.75 s
    # The white square at the top right shows with it.
    assert _near(_pixel(_rgb(out, 24), 604, 34), (255, 255, 255))
    assert not _near(_pixel(_rgb(out, 21), 604, 34), (255, 255, 255))

    # Loud from 22 to 28 s, and cuts at 10, 20 and 30 s, by the example pipeline's own code.
    example = _example()
    levels = example.loudness_per_second(FFMPEG, str(out))
    assert len(levels) == 40
    assert [stretch[:2] for stretch in example.loud_stretches(levels, 6.0)] == [(22, 28)]
    assert example.scene_cuts(FFMPEG, str(out), 0.3) == list(samples.SCENE_CUTS)


def test_the_sample_transcript_says_the_words_where_designed(tmp_path):
    transcript = samples.sample_transcript()
    assert transcript["language"] == "en"
    assert [(s["start"], s["end"], s["text"]) for s in transcript["segments"]] == [
        (8.0, 12.0, "round one, here we go"),
        (21.0, 26.0, "what a quark burst"),
        (34.5, 39.0, "just waiting for the respawn timer")]
    for s in transcript["segments"]:
        words = s["words"]
        assert " ".join(w["word"] for w in words) == s["text"]
        assert words[0]["start"] == s["start"] and words[-1]["end"] == s["end"]
        assert all(a["end"] == b["start"] for a, b in zip(words, words[1:]))
    transcript["segments"].clear()
    assert len(samples.sample_transcript()["segments"]) == 3  # a new copy each time
    assert samples.transcript_path(Path("media") / "sample.mp4") == Path("media") / "sample.transcript.json"

    # What a plugin reads with job.text in the 5 sample moments of a 40-second video.
    manifest = {"id": "example-dev/quarkbloom-rater", "version": "1.0.0", "permissions": ["transcript.read"]}
    job, said = host.build_job(manifest, transcript=samples.sample_transcript(), steps=["rate"],
                               moments=devrun.sample_moments(samples.SAMPLE_VIDEO_SECONDS),
                               output_dir=tmp_path / "job" / "out")
    host.write_job(tmp_path / "job", job, said)
    read = read_job(tmp_path / "job")
    assert [read.text(m) for m in read.moments] == [
        "round one, here we go", "", "what a quark burst", "", "just waiting for the respawn timer"]


# ---- run --sample -------------------------------------------------------------------


@needs_ffmpeg
def test_run_sample_needs_no_video(tmp_path, capsys):
    pytest.importorskip("yaml")
    finder = _video_finder(tmp_path)
    before = _files(finder)
    assert _run(tmp_path, finder, "--sample") == 0
    out, err = capsys.readouterr()
    assert err.splitlines()[0] == f"note: --sample: {samples.SAMPLE_NOTE}"
    assert "no --transcript given" not in err
    job = (tmp_path / "job").resolve()
    seen = _seen(tmp_path)
    assert seen["video"]["path"] == str(job / "sample.mp4") and seen["video"]["duration"] == 40.0
    assert seen["video_bytes"] > 100_000
    assert json.loads((job / "transcript.json").read_text(encoding="utf-8"))["segments"] == \
        samples.sample_transcript()["segments"]
    assert {"sample.mp4", "sample.transcript.json"} <= {p.name for p in job.iterdir()}
    assert "1 moment(s), as Clips Kitty would take them:\n      22.0s      27.0s  score  90  quark_burst\n" in out
    assert _files(finder) == before  # nothing was written into the plugin's folder

    # A run that rates gets the 5 sample moments of a 40-second video.
    assert _run(tmp_path, _words_rater(tmp_path), "--sample", job="rate") == 0
    out, err = capsys.readouterr()
    assert [(m["id"], m["start"], m["end"]) for m in _seen(tmp_path, "rate")["moments"]] == [
        (m["id"], m["start"], m["end"]) for m in devrun.sample_moments(40.0)]
    assert 'm3   20.0s-26.7s  score 60 -> 75  the commentary says "quark burst"\n' in out
    assert "note: no --moments given, so the plugin gets 5 sample moments" in err


def test_sample_refuses_a_plugin_folder(tmp_path, capsys, monkeypatch):
    plugin = _words_finder(tmp_path)
    before = _files(plugin)
    refusal = ("error: that is inside a plugin's folder, and Clips Kitty copies everything there on install. "
               f"Write the sample somewhere else, for example: {python_command()} sample ../sample.mp4\n")
    for out in (plugin / "sample.mp4", plugin / "media" / "deeper" / "sample.mp4"):
        assert _cli("sample", out) == 2
        assert capsys.readouterr().err == refusal
    monkeypatch.chdir(plugin / "src")
    assert _cli("sample", "sample.mp4") == 2
    assert capsys.readouterr().err == refusal
    assert _files(plugin) == before
    assert samples.plugin_folder_holding(Path("../../sample.mp4")) is None  # beside the plugin is fine
    assert samples.plugin_folder_holding(Path("../sample.mp4")) == plugin.resolve()


def test_sample_and_video_together_are_refused(tmp_path, capsys):
    pytest.importorskip("yaml")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not decoded")
    transcript = tmp_path / "t.json"
    transcript.write_text(json.dumps(samples.sample_transcript()), encoding="utf-8")
    for plugin, option, value in ((_video_finder(tmp_path), "--video", video),
                                  (_words_finder(tmp_path), "--transcript", transcript),
                                  (_words_rater(tmp_path), "--duration", 40)):
        assert _run(tmp_path, plugin, "--sample", option, value) == 2
        assert capsys.readouterr().err == f"error: use --sample or {option}, not both\n"
        assert not (tmp_path / "job").exists()


def test_sample_without_ffmpeg_says_how_to_get_it(tmp_path, capsys, monkeypatch):
    pytest.importorskip("yaml")
    _no_ffmpeg(tmp_path, monkeypatch)
    refusal = ("error: --sample needs FFmpeg to make the test video. Install FFmpeg, or pass --ffmpeg and "
               "--ffprobe. An installed Clips Kitty has both, in resources\\backend\\_internal\\ffmpeg inside the "
               "folder it was installed to.\n")
    # One that reads the video, and one that only runs FFmpeg.
    ffmpeg_only = _plugin(tmp_path, "quarkbloom-cuts", inputs="transcript", outputs="ranges",
                          permissions="transcript.read, ffmpeg", main=WORDS_FINDER_MAIN)
    for plugin in (_video_finder(tmp_path), ffmpeg_only):
        assert _run(tmp_path, plugin, "--sample") == 2
        assert capsys.readouterr().err == refusal
        assert not (tmp_path / "job").exists()
    assert _cli("sample", tmp_path / "media" / "sample.mp4") == 2
    assert capsys.readouterr().err == (
        "error: sample needs FFmpeg to make the test video. Install FFmpeg, or pass --ffmpeg. An installed Clips "
        "Kitty has it, in resources\\backend\\_internal\\ffmpeg inside the folder it was installed to.\n")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not decoded")
    assert _cli("frame", video, "--at", "1", "--out", tmp_path / "frame.png") == 2
    assert capsys.readouterr().err == (
        "error: frame needs FFmpeg to read the video. Install FFmpeg, or pass --ffmpeg. An installed Clips "
        "Kitty has it, in resources\\backend\\_internal\\ffmpeg inside the folder it was installed to.\n")
    assert not (tmp_path / "media").exists() and not (tmp_path / "frame.png").exists()


def test_sample_without_ffmpeg_runs_a_plugin_that_never_reads_the_video(tmp_path, capsys, monkeypatch):
    pytest.importorskip("yaml")
    _no_ffmpeg(tmp_path, monkeypatch)
    note = ("note: --sample: no FFmpeg here, so this run gets the sample transcript and a 40-second length but no "
            "video; this plugin doesn't read the video, so that is all it needs")
    assert _run(tmp_path, _words_finder(tmp_path), "--sample") == 0
    out, err = capsys.readouterr()
    assert err.splitlines()[0] == note and "no --transcript given" not in err
    assert out.endswith("1 moment(s), as Clips Kitty would take them:\n"
                        "      17.0s      40.0s  score  80  words_said\n"  # fitted to the 40-second length
                        '       why: the commentary says "quark burst"\n')
    assert "video" not in _seen(tmp_path) and not (tmp_path / "job" / "sample.mp4").exists()

    assert _run(tmp_path, _words_rater(tmp_path), "--sample", job="rate") == 0
    out, err = capsys.readouterr()
    assert err.splitlines()[0] == note
    assert "Rate: 2 of 5 moment(s) answered, as Clips Kitty would use them:\n" in out
    assert 'm3   20.0s-26.7s  score 60 -> 75  the commentary says "quark burst"\n' in out
    assert 'm5   33.3s-40.0s  score 60 -> 10  the commentary says "waiting"\n' in out


def test_a_find_run_without_video_read_needs_no_video(tmp_path, capsys):
    pytest.importorskip("yaml")
    transcript = tmp_path / "said.json"
    transcript.write_text(json.dumps(samples.sample_transcript()), encoding="utf-8")
    finder = _words_finder(tmp_path)
    # Fitted to the end of what is said, or to --duration.
    for job, extra, end in (("said", [], "39.0s"), ("duration", ["--duration", "30"], "30.0s")):
        assert _run(tmp_path, finder, "--transcript", transcript, *extra, job=job) == 0
        out = capsys.readouterr().out
        assert f"      17.0s  {end:>9}  score  80  words_said\n" in out, out
        assert "video" not in _seen(tmp_path, job)
    # One that reads the video still needs one.
    assert _run(tmp_path, _video_finder(tmp_path), "--transcript", transcript, job="video") == 2
    assert capsys.readouterr().err.endswith(
        "error: --video is needed: a plugin with the video.read permission needs a video to run on, or try it "
        "with --sample\n")


# ---- frame --------------------------------------------------------------------------


@needs_ffmpeg
def test_frame_writes_a_png_and_prints_the_region_in_pixels(tmp_path, capsys, monkeypatch, made_sample):
    out = tmp_path / "shots" / "banner.png"
    assert _cli("frame", made_sample, "--at", "24", "--region", "0.30,0.10,0.40,0.10", "--out", out) == 0
    assert capsys.readouterr().out == (
        f"wrote {out}: the frame at 24 s of this 640x360 video\n"
        'region "0.30,0.10,0.40,0.10" is x=192 y=36 w=256 h=36 on this 640x360 video\n')
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    picture = _rgb(out)
    assert picture[:2] == (640, 360)
    assert _near(_pixel(picture, 191, 35), (255, 0, 255), 0)  # the box, just outside the region
    assert _near(_pixel(picture, 448, 72), (255, 0, 255), 0)
    assert _near(_pixel(picture, 192, 36), (0xE0, 0x30, 0x3A))  # the banner inside it shows as it is
    assert _near(_pixel(picture, 10, 10), (0x2F, 0x7F, 0x1A))  # the third scene
    # Without --region: no box, and the file is named after the time, in the current folder.
    monkeypatch.chdir(tmp_path)
    assert _cli("frame", made_sample, "--at", "24") == 0
    assert capsys.readouterr().out == "wrote frame-24s.png: the frame at 24 s of this 640x360 video\n"
    assert _near(_pixel(_rgb(tmp_path / "frame-24s.png"), 191, 35), (0x2F, 0x7F, 0x1A))
    assert _cli("frame", made_sample, "--at", "99", "--out", tmp_path / "late.png") == 1
    assert capsys.readouterr().err == f"error: there is no frame at 99 s in {made_sample}: is the video that long?\n"


def test_frame_takes_the_region_as_one_or_several_arguments(tmp_path, capsys, request):
    for text in ("0.30,0.10,0.40,0.10", "0.30 0.10 0.40 0.10", "0.30, 0.10, 0.40, 0.10", " 0.30,0.10 0.40,0.10 "):
        assert samples.parse_region(text) == (0.30, 0.10, 0.40, 0.10), text
        assert samples.region_text(text) == "0.30,0.10,0.40,0.10"
    shape = ('a region is four numbers from 0 to 1, as fractions of the frame: left, top, width and height, '
             'such as "0.30,0.10,0.40,0.10"')
    for bad, problem in (("0.30,0.10,0.40", shape), ("left,top,0.4,0.1", shape), ("1.5,0,0.1,0.1", shape),
                         ("nan,0,0.1,0.1", shape),
                         ("0.30,0.10,0,0.10", 'the width and height of "0.30,0.10,0,0.10" must be above 0'),
                         ("0.80,0.10,0.40,0.10", ('"0.80,0.10,0.40,0.10" goes past the edge of the frame: left + '
                                                  "width and top + height must be 1 or less"))):
        with pytest.raises(ValueError) as e:
            samples.parse_region(bad)
        assert str(e.value) == problem, bad
    assert samples.region_pixels((0.92, 0.05, 0.05, 0.09), 640, 360) == (589, 18, 32, 32)
    assert samples.region_pixels((0.999, 0.999, 0.001, 0.001), 640, 360) == (639, 359, 1, 1)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not decoded")
    assert _cli("frame", video, "--at", "1", "--region", "0.80,0.10", "0.40,0.10") == 2  # refused before FFmpeg
    assert capsys.readouterr().err == ('error: --region: "0.80,0.10,0.40,0.10" goes past the edge of the frame: '
                                       "left + width and top + height must be 1 or less\n")

    if not (FFMPEG and FFPROBE):
        pytest.skip("the rest needs FFmpeg")
    made = request.getfixturevalue("made_sample")
    # As PowerShell hands over an unquoted 0.30,0.10,0.40,0.10, and other ways of writing it.
    lines = set()
    for pieces in (["0.30,0.10,0.40,0.10"], ["0.30", "0.10", "0.40", "0.10"], ["0.30,0.10", "0.40,0.10"],
                   ["0.30, 0.10, 0.40, 0.10"]):
        assert _cli("frame", made, "--at", "24", "--region", *pieces, "--out", tmp_path / "f.png") == 0, pieces
        lines.add(capsys.readouterr().out.splitlines()[1])
    assert lines == {'region "0.30,0.10,0.40,0.10" is x=192 y=36 w=256 h=36 on this 640x360 video'}


def test_frame_refuses_a_plugin_folder(tmp_path, capsys, monkeypatch):
    plugin = _words_finder(tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not decoded")
    before = _files(plugin)
    refusal = ("error: that is inside a plugin's folder, and Clips Kitty copies everything there on install. "
               "Write the frame somewhere else, for example: --out ../frame.png\n")
    for out in (plugin / "frame.png", plugin / "src" / "shots" / "frame.png"):
        assert _cli("frame", video, "--at", "1", "--out", out) == 2
        assert capsys.readouterr().err == refusal
    monkeypatch.chdir(plugin)
    assert _cli("frame", video, "--at", "1") == 2  # frame-1s.png would go in the plugin's folder
    assert capsys.readouterr().err == refusal
    assert _files(plugin) == before
