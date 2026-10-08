"""The SDK's helpers for a first game pipeline: media (reading the video with
Clips Kitty's FFmpeg), signals (turning numbers and frames into moments),
text (words in the transcript) and local_model (the creator's model on this
PC), plus the "newer version" sentence job.run() gives for an SDK import
inside main().

The media tests run on the SDK's 40-second sample video (clipskitty_sdk.
samples) and skip without FFmpeg. The local-model tests talk to a fake model
server on 127.0.0.1, and check that proxy settings never route a request to
this PC through a proxy. Like every tests/test_plugin_sdk_*.py file, this
needs only pytest and PyYAML and imports nothing from Clips Kitty's engine,
so it also runs in CI's SDK (Windows) job.
"""

import base64
import importlib
import itertools
import json
import shutil
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import (  # noqa: E402
    ContractError,
    _loopback,
    devrun,
    host,
    local_model,
    media,
    read_job,
    samples,
    signals,
    testing,
    text,
)
from clipskitty_sdk import job as job_module  # noqa: E402
from clipskitty_sdk.client import LocalAPI  # noqa: E402

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
BANNER = media.Region.parse(samples.BANNER_REGION)


# ---- jobs ---------------------------------------------------------------------------


def _job(tmp_path, permissions, *, name="job", video=None, transcript=None, limits=None, ollama=None,
         ffmpeg=FFMPEG, ffprobe=FFPROBE):
    """A job folder as Clips Kitty writes it (host.build_job), read as a plugin reads it."""
    manifest = {"id": "example-dev/quarkbloom-helpers", "version": "1.0.0", "permissions": list(permissions)}
    data, said = host.build_job(manifest, video=video, transcript=transcript, limits=limits, ollama=ollama,
                                ffmpeg=ffmpeg, ffprobe=ffprobe, output_dir=tmp_path / name / "out")
    host.write_job(tmp_path / name, data, said)
    return read_job(tmp_path / name)


@pytest.fixture(scope="module")
def sample(tmp_path_factory) -> Path:
    if not (FFMPEG and FFPROBE):
        pytest.skip("needs FFmpeg and FFprobe")
    return samples.make_sample(tmp_path_factory.mktemp("media") / "sample.mp4", FFMPEG)[0]


@pytest.fixture
def video_job(tmp_path, sample):
    return _job(tmp_path, ["video.read", "ffmpeg"],
                video={"path": str(sample), "duration": samples.SAMPLE_VIDEO_SECONDS})


def _jpeg_size(data: bytes) -> tuple[int, int]:
    """(width, height) from a JPEG's frame header."""
    i = 2
    while i < len(data):
        assert data[i] == 0xFF
        marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + length
    raise AssertionError("no frame header in the JPEG")


# ---- media --------------------------------------------------------------------------


def test_region_parses_and_maps_to_pixels():
    assert BANNER == media.Region(0.30, 0.10, 0.40, 0.10)
    assert media.Region.parse("0.30 0.10, 0.40 ,0.10") == BANNER
    assert BANNER.pixels(640, 360) == (192, 36, 256, 36) == samples.region_pixels(
        samples.parse_region(samples.BANNER_REGION), 640, 360)
    assert BANNER.pixels(1920, 1080) == (576, 108, 768, 108)
    assert str(BANNER) == "0.3,0.1,0.4,0.1"
    assert media.Region(0, 0, 1, 1).pixels(7, 5) == (0, 0, 7, 5)
    assert media.Region(0.999, 0.999, 0.001, 0.001).pixels(640, 360) == (639, 359, 1, 1)  # never off the frame
    for bad in ("0.3,0.1,0.4", "a,b,c,d", "0.3,0.1,1.4,0.1", "0.8,0.1,0.4,0.1", "0.3,0.1,0,0.1"):
        with pytest.raises(ValueError):
            media.Region.parse(bad)
    with pytest.raises(ValueError, match="past the edge of the frame"):
        media.Region(0.8, 0.1, 0.4, 0.1)
    with pytest.raises(ValueError, match="four numbers from 0 to 1"):
        media.Region("left", 0.1, 0.4, 0.1)


@pytest.mark.parametrize("call", [
    media.probe,
    media.frames,
    lambda job: media.jpeg(job, 1),
    media.loudness,
    media.scene_cuts,
], ids=["probe", "frames", "jpeg", "loudness", "scene_cuts"])
def test_media_names_the_missing_permission(tmp_path, call):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"never decoded")
    given = {"path": str(video), "duration": 40.0}
    tools = {"ffmpeg": "/no/ffmpeg", "ffprobe": "/no/ffprobe"}
    cases = [
        (["video.read"], "no-ffmpeg", media.NEEDS_FFMPEG),
        ([], "nothing", media.NEEDS_FFMPEG),
        (["ffmpeg"], "no-video", media.NEEDS_VIDEO),
    ]
    for permissions, name, message in cases:
        job = _job(tmp_path, permissions, name=name, video=given, **tools)
        with pytest.raises(media.MediaError) as refused:
            call(job)  # refused before FFmpeg is started: these paths don't exist
        assert str(refused.value) == message
    # Both permissions, but no FFmpeg was found for this run.
    job = _job(tmp_path, ["video.read", "ffmpeg"], name="not-found", video=given, ffmpeg=None, ffprobe=None)
    with pytest.raises(media.MediaError, match=r"^Clips Kitty couldn't find FF(mpeg|probe) on this PC"):
        call(job)
    assert issubclass(media.MediaError, RuntimeError)
    assert not issubclass(media.MediaError, job_module.PROGRAMMING_ERRORS)  # creators see its words


def test_frames_read_only_the_region_at_the_rate_asked(video_job):
    info = media.probe(video_job)
    assert info == media.VideoInfo(640, 360, 25.0, 40.0)

    frames = list(media.frames(video_job, fps=4, region=BANNER))
    assert len(frames) == 160
    assert [f.t for f in frames[:5]] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert {(f.width, f.height, len(f.rgb)) for f in frames} == {(32, 8, 32 * 8 * 3)}

    # Text works as a region too, and size None keeps the region's own pixels.
    at_24 = next(f for f in media.frames(video_job, fps=1, region=samples.BANNER_REGION, size=None) if f.t == 24)
    assert (at_24.width, at_24.height, len(at_24.rgb)) == (256, 36, 256 * 36 * 3)
    assert signals.colour_share(at_24, samples.BANNER_COLOUR) == 1.0  # only the banner is read
    whole = next(f for f in media.frames(video_job, fps=1, size=None) if f.t == 24)
    assert (whole.width, whole.height) == (640, 360)
    assert 0.03 < signals.colour_share(whole, samples.BANNER_COLOUR) < 0.06  # the banner is 4% of the screen
    assert whole.pixel(300, 50) == at_24.pixel(300 - 192, 50 - 36)

    other = list(media.frames(video_job, fps=0.5, size=(16, 9)))
    assert [f.t for f in other] == [2.0 * i for i in range(20)]
    assert {(f.width, f.height) for f in other} == {(16, 9)}

    # Stopping early stops FFmpeg, with no error.
    reader = media.frames(video_job, fps=25, size=None)
    assert [f.t for f in itertools.islice(reader, 3)] == [0.0, 0.04, 0.08]
    reader.close()

    with pytest.raises(ValueError):
        media.frames(video_job, fps=0)


def test_probe_measures_a_turned_video_as_its_frames_are_read(tmp_path, sample):
    """A phone records upright video as a landscape picture plus "turn it by
    90 degrees"; FFmpeg turns each frame, so probe() gives the turned size."""
    turned = tmp_path / "turned.mp4"
    done = subprocess.run([FFMPEG, "-v", "error", "-y", "-display_rotation", "90", "-i", str(sample), "-c", "copy",
                           "-t", "2", str(turned)], capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        pytest.skip(f"this FFmpeg can't mark a video as turned: {done.stderr.strip()[-200:]}")
    job = _job(tmp_path, ["video.read", "ffmpeg"], video={"path": str(turned)})
    info = media.probe(job)
    assert (info.width, info.height) == (360, 640)
    first = next(iter(media.frames(job, fps=1, size=None)))
    assert (first.width, first.height) == (360, 640)


def test_jpeg_is_a_jpeg_no_bigger_than_asked(video_job):
    picture = media.jpeg(video_job, 24)
    assert picture[:2] == b"\xff\xd8" and picture[-2:] == b"\xff\xd9"
    assert _jpeg_size(picture) == (640, 360)  # never enlarged
    assert _jpeg_size(media.jpeg(video_job, 24, max_side=320)) == (320, 180)
    width, height = _jpeg_size(media.jpeg(video_job, 24, max_side=100))
    assert width == 100 and 55 <= height <= 57
    with pytest.raises(media.MediaError) as refused:
        media.jpeg(video_job, 99)
    assert str(refused.value) == "there is no frame at 99 s in the video: is it that long?"


def test_loudness_finds_the_loud_part(video_job):
    levels = media.loudness(video_job)
    assert len(levels) == 40
    assert all(level >= media.SILENCE for level in levels)
    loud = signals.spikes(levels, louder_by=6.0)
    assert loud == [22, 23, 24, 25, 26, 27]
    assert signals.stretches(loud) == [(22.0, 27.0)]
    # The same as the MIT example it is adapted from (examples/pipelines/scene-cut-highlights).
    assert min(levels[22:27]) > max(levels[:22]) + 20


def test_scene_cuts_are_at_10_20_30(video_job):
    assert media.scene_cuts(video_job) == list(samples.SCENE_CUTS) == [10.0, 20.0, 30.0]


def test_colour_share_finds_the_banner_from_22_to_26_75(video_job):
    shown = [f.t for f in media.frames(video_job, fps=4, region=BANNER)
             if signals.colour_share(f, samples.BANNER_COLOUR) > 0.5]
    assert shown == [22 + k / 4 for k in range(20)]
    assert signals.stretches(shown) == [(22.0, 26.75)]
    # The colour may be written with "#" or as (red, green, blue).
    at_24 = next(f for f in media.frames(video_job, fps=1, region=BANNER) if f.t == 24)
    assert signals.colour_share(at_24, "#E0303A") == signals.colour_share(at_24, (224, 48, 58)) == 1.0
    assert signals.colour_share(at_24, "ffffff") == 0.0


def test_brightness_and_difference_compare_frames():
    black = media.Frame(0.0, 2, 1, bytes([0, 0, 0, 0, 0, 0]))
    white = media.Frame(1.0, 2, 1, bytes([255] * 6))
    half = media.Frame(2.0, 2, 1, bytes([255, 255, 255, 0, 0, 0]))
    red = media.Frame(3.0, 2, 1, bytes([224, 48, 58, 30, 30, 30]))
    assert signals.brightness(black) == 0.0
    assert signals.brightness(white) == pytest.approx(255.0)
    assert signals.brightness(half) == pytest.approx(127.5)
    assert signals.difference(black, white) == 255.0
    assert signals.difference(half, half) == 0.0
    assert signals.difference(black, half) == 127.5
    assert signals.colour_share(red, "e0303a") == 0.5
    assert signals.colour_share(red, "e0303a", tolerance=0) == 0.5
    assert signals.colour_share(red, "202020", tolerance=10) == 0.5
    with pytest.raises(ValueError, match="differ in size"):
        signals.difference(black, media.Frame(0.0, 1, 1, bytes(3)))
    for bad in ("red", "e0303", (300, 0, 0)):
        with pytest.raises(ValueError):
            signals.colour_share(red, bad)


def test_spikes_stretches_merge():
    quiet = [-40.0] * 40
    for second in (22, 23, 24, 25, 26):
        quiet[second] = -20.0
    assert signals.spikes(quiet, louder_by=6.0) == [22, 23, 24, 25, 26]
    assert signals.spikes(quiet, louder_by=30.0) == []
    # A rolling median: a video that gets louder is compared with its own surroundings.
    rising = [-60.0 + 0.2 * i for i in range(100)]
    rising[80] += 10
    assert signals.spikes(rising, louder_by=6.0, window=30) == [80]
    assert signals.spikes([], louder_by=6.0) == []
    assert signals.spikes([1, 1, 50], louder_by=6.0, window=30) == [2]  # fewer values than the window

    assert signals.stretches([22, 23, 24, 30, 31]) == [(22.0, 24.0), (30.0, 31.0)]
    assert signals.stretches([5.0, 22.0, 22.25, 22.5]) == [(22.0, 22.5)]  # a time on its own is too short
    assert signals.stretches([5.0], min_length=0) == [(5.0, 5.0)]
    assert signals.stretches([1, 3, 5], gap=2.0) == [(1.0, 5.0)]
    assert signals.stretches([]) == []

    assert signals.merge([(30, 35), (10, 12), (13, 20), (40, 41)]) == [(10.0, 20.0), (30.0, 35.0), (40.0, 41.0)]
    assert signals.merge([(10, 20), (12, 15)]) == [(10.0, 20.0)]  # one inside another
    assert signals.merge([(10, 12, "quark_burst"), (13, 14, "loud")], gap=0.5) == [(10.0, 12.0), (13.0, 14.0)]
    assert signals.merge([]) == []


def test_around_fits_the_limits_and_the_video(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"never decoded")

    def job(name, *, limits=None, duration=40.0, with_video=True):
        return _job(tmp_path, ["video.read"] if with_video else ["transcript.read"], name=name, limits=limits,
                    video={"path": str(video), "duration": duration} if with_video else None)

    usual = job("usual", limits={"min_duration": 10, "max_duration": 60})
    assert signals.around(usual, 22, 27) == (16.0, 30.0)
    assert signals.around(usual, 22, 27, lead=2, tail=1) == (20.0, 30.0)  # made as long as the shortest clip
    assert signals.around(usual, 2, 5) == (0.0, 10.0)  # never before the start
    assert signals.around(usual, 36, 39) == (30.0, 40.0)  # never past the end
    assert signals.around(job("long", limits={"min_duration": 15, "max_duration": 60}), 36, 39) == (25.0, 40.0)
    assert signals.around(job("short", limits={"max_duration": 12}), 10, 30) == (4.0, 16.0)  # keeps the start
    assert signals.around(job("no-limits"), 22.333, 27.111) == (16.33, 30.11)
    # Without the video's length (a plugin that reads only the transcript), the end isn't cut.
    assert signals.around(job("words", with_video=False), 38, 39, tail=10) == (32.0, 49.0)
    assert signals.around(job("unknown", duration=None), 38, 39, tail=10) == (32.0, 49.0)


# ---- text ---------------------------------------------------------------------------


def test_find_words_matches_whole_words_in_any_case():
    assert text.find_words("What a QUARK   Burst!", ["quark burst", "burst", "win"]) == ["quark burst", "burst"]
    assert text.find_words("look out the window", ["win"]) == []
    assert text.find_words("we win!", "win, window") == ["win"]
    assert text.find_words("a quark\nburst", "Quark Burst") == ["Quark Burst"]
    assert text.find_words("STRASSE", ["straße"]) == ["straße"]
    assert text.find_words("", ["win"]) == [] and text.find_words("we win", []) == []
    assert text.words_of(" quark burst,  Triple   Bloom , QUARK BURST,, ") == ["quark burst", "Triple Bloom"]
    assert text.words_of(None) == [] and text.words_of("") == []
    assert text.normalise("  Ｑuark\tBURST ") == "quark burst"  # a full-width Q, a tab


def test_said_and_hits_use_the_transcript(tmp_path):
    job = _job(tmp_path, ["transcript.read"], transcript=samples.sample_transcript())
    words = "quark burst, waiting, round one, triple bloom"
    # The words' own times (the sample gives each word a time).
    assert text.said(job, words) == [(8.0, 9.6, "round one"), (23.5, 26.0, "quark burst"),
                                     (35.25, 36.0, "waiting")]
    assert text.said(job, ["Quark Burst"]) == [(23.5, 26.0, "Quark Burst")]

    # A transcript without word times gives the segment's.
    plain = samples.sample_transcript()
    for segment in plain["segments"]:
        del segment["words"]
    plain["segments"].append({"start": 30.0, "end": 31.0, "text": "Quark burst! Quark burst!"})
    plain_job = _job(tmp_path, ["transcript.read"], name="plain", transcript=plain)
    assert text.said(plain_job, ["quark burst"]) == [(21.0, 26.0, "quark burst"), (30.0, 31.0, "quark burst")]

    moments = read_job_with_moments(tmp_path)
    assert [text.hits(job, m, words) for m in moments] == [
        ["round one"], [], ["quark burst"], [], ["waiting"]]
    assert text.hits(job, (24.0, 25.0), words) == ["quark burst"]
    assert text.hits(job, (26.0, 30.0), words) == []  # touching isn't during

    # The same refusal as job.text without the transcript.
    no_words = _job(tmp_path, [], name="no-transcript")
    with pytest.raises(ContractError) as refused:
        text.said(no_words, words)
    with pytest.raises(ContractError) as also:
        no_words.text(moments[0])
    assert str(refused.value) == str(also.value)


def read_job_with_moments(tmp_path):
    """The 5 sample moments of a 40-second video, as a rater is handed them."""
    manifest = {"id": "example-dev/quarkbloom-rater", "version": "1.0.0", "permissions": ["transcript.read"]}
    data, said = host.build_job(manifest, transcript=samples.sample_transcript(), steps=["rate"],
                                moments=devrun.sample_moments(samples.SAMPLE_VIDEO_SECONDS),
                                output_dir=tmp_path / "moments" / "out")
    host.write_job(tmp_path / "moments", data, said)
    return read_job(tmp_path / "moments").moments


# ---- the local model ------------------------------------------------------------------


class _Server:
    """An HTTP server on 127.0.0.1 in a thread that records every request.
    `answer(path, body)` gives (status, JSON) for each, or (status, JSON,
    headers)."""

    def __init__(self, answer):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = raw
                outer.requests.append({"method": self.command, "path": self.path, "body": body,
                                       "headers": dict(self.headers)})
                status, reply, *headers = answer(self.path, body)
                data = json.dumps(reply).encode("utf-8")
                self.send_response(status)
                for name, value in (headers[0] if headers else {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _handle

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def paths(self):
        return [r["path"] for r in self.requests]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _ollama(capabilities=("completion",), response="A quark burst banner is on screen.", missing=()):
    def answer(path, body):
        if (body or {}).get("model") in missing:
            return 404, {"error": f"model '{body['model']}' not found"}
        if path == "/api/show":
            return 200, {"capabilities": list(capabilities)}
        if path == "/api/generate":
            return 200, {"model": body["model"], "response": response, "done": True}
        return 404, {"error": "not found"}
    return answer


@pytest.fixture(autouse=True)
def _fresh_capabilities(monkeypatch):
    """local_model remembers what each address and model can do; a test's
    fake server may reuse an earlier one's port."""
    monkeypatch.setattr(local_model, "_CAPABILITIES", {})


@pytest.fixture
def servers():
    made = []

    def make(answer):
        server = _Server(answer)
        made.append(server)
        return server

    yield make
    for server in made:
        server.close()


# Each way a plugin uses the local model: ask, can_see, model.
ASKING = (lambda job: local_model.ask(job, "Say hi"), local_model.can_see, local_model.model)


def _model_job(tmp_path, url, *, model="test-model", name="model-job", permissions=("ollama",)):
    return _job(tmp_path, list(permissions), name=name, ollama={"host": url, "model": model})


def test_ask_sends_prompt_images_and_json_to_this_pc(tmp_path, servers):
    ollama = servers(_ollama(response='{"what": "quark burst"}'))
    job = _model_job(tmp_path, ollama.url)
    picture = tmp_path / "frame.png"
    picture.write_bytes(b"\x89PNG not really")
    assert local_model.model(job) == "test-model"

    answer = local_model.ask(job, "What is on screen?", images=[b"\xff\xd8 a jpeg", picture], json=True)
    assert json.loads(answer) == {"what": "quark burst"}
    assert ollama.paths() == ["/api/show", "/api/generate"]
    assert ollama.requests[0]["body"] == {"model": "test-model"}
    sent = ollama.requests[1]
    assert sent["method"] == "POST"
    assert sent["body"] == {"model": "test-model", "prompt": "What is on screen?", "stream": False,
                            "images": [base64.b64encode(b"\xff\xd8 a jpeg").decode("ascii"),
                                       base64.b64encode(b"\x89PNG not really").decode("ascii")],
                            "format": "json"}  # this model can't think, so nothing about thinking

    # Plain text: no format, no images, and no need to ask what the model can do.
    assert local_model.ask(job, "Say hi") == '{"what": "quark burst"}'
    assert ollama.requests[-1]["body"] == {"model": "test-model", "prompt": "Say hi", "stream": False}
    assert ollama.paths() == ["/api/show", "/api/generate", "/api/generate"]

    # The address may be written without http://, as Ollama's own setting is.
    bare = _model_job(tmp_path, ollama.url.removeprefix("http://"), name="bare")
    assert local_model.ask(bare, "Say hi") == '{"what": "quark burst"}'

    # What Ollama says when it can't answer.
    ollama_missing = servers(_ollama(missing=("missing-model",)))
    gone = _model_job(tmp_path, ollama_missing.url, model="missing-model", name="gone")
    with pytest.raises(local_model.LocalModelError) as refused:
        local_model.ask(gone, "Say hi")
    assert str(refused.value) == "The local model couldn't answer: model 'missing-model' not found"
    with pytest.raises(TypeError):
        local_model.ask(job, "Say hi", images=[42])


def test_ask_turns_thinking_off_for_json(tmp_path, servers):
    ollama = servers(_ollama(capabilities=("completion", "thinking", "vision")))
    job = _model_job(tmp_path, ollama.url, model="test-model-thinks")
    local_model.ask(job, "Rate it", json=True)
    assert ollama.requests[-1]["body"]["think"] is False
    assert ollama.requests[-1]["body"]["format"] == "json"
    local_model.ask(job, "Rate it", json=True)
    assert ollama.paths().count("/api/show") == 1  # asked once
    local_model.ask(job, "Describe it")
    assert "think" not in ollama.requests[-1]["body"]  # text answers are left as Ollama sends them


def test_ask_refuses_an_address_off_this_pc(tmp_path, servers):
    ollama = servers(_ollama())
    port = ollama.url.rsplit(":", 1)[1]
    for i, address in enumerate((f"http://192.168.1.20:{port}", f"http://0.0.0.0:{port}",
                                 f"http://127.0.0.2:{port}", f"http://localhost.example.com:{port}",
                                 "https://ollama.example.com")):
        job = _model_job(tmp_path, address, name=f"off-{i}")
        for call in ASKING[:2]:
            with pytest.raises(local_model.LocalModelError) as refused:
                call(job)
            assert str(refused.value) == (f"Clips Kitty's model address {address} isn't on this PC, and this "
                                          "helper only talks to a model on this PC")
    assert ollama.requests == []  # never a fallback to another address
    assert [_loopback.is_this_pc(url) for url in ("http://127.0.0.1:11434", "http://localhost:11434",
                                                   "http://LOCALHOST", "http://[::1]:11434")] == [True] * 4
    assert [_loopback.is_this_pc(url) for url in ("http://10.0.0.1", "http://127.0.0.2", "", "not a url",
                                                   "http://[::1")] == [False] * 5


def test_ask_follows_no_redirect_to_another_address(tmp_path, servers):
    """A redirect to another address, even one on this PC, isn't followed:
    the model's answer comes only from the address Clips Kitty gave."""
    elsewhere = servers(_ollama(response="an answer from elsewhere"))
    redirecting = servers(lambda path, body: (302, None, {"Location": elsewhere.url + path}))
    job = _model_job(tmp_path, redirecting.url, name="redirected")
    for call in ASKING[:2]:
        with pytest.raises(local_model.LocalModelError) as refused:
            call(job)
        assert str(refused.value) == ("The local model couldn't answer: it redirected to another address, and "
                                      "requests to this PC don't follow that")
    assert elsewhere.requests == []
    assert redirecting.paths() == ["/api/generate", "/api/show"]


def _raw_server(reply: bytes):
    """A server on 127.0.0.1 that reads one request and answers it with
    `reply`, then hangs up. Returns (its address, its listening socket)."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def serve():
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        with conn:
            try:
                conn.settimeout(10)
                data = b""
                while b"\r\n\r\n" not in data:
                    data += conn.recv(65536) or b"\r\n\r\n"
                head, _, body = data.partition(b"\r\n\r\n")
                length = [int(line.split(b":")[1]) for line in head.lower().splitlines()
                          if line.startswith(b"content-length:")]
                while length and len(body) < length[0]:
                    body += conn.recv(65536) or b" " * length[0]
                conn.sendall(reply)
                conn.shutdown(socket.SHUT_WR)
            except OSError:
                pass  # the client hung up first; the test checks what the client saw

    threading.Thread(target=serve, daemon=True).start()
    return f"http://127.0.0.1:{listener.getsockname()[1]}", listener


def test_ask_says_when_the_answer_cant_be_read(tmp_path):
    """Something that isn't Ollama, or an answer cut off part way, is a
    LocalModelError the creator can read, not http.client's own error."""
    cut_off = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n"
               b'{"response": "cut')
    for i, reply in enumerate((b"SSH-2.0-OpenSSH_9.6\r\n", cut_off)):
        url, listener = _raw_server(reply)
        try:
            with pytest.raises(local_model.LocalModelError) as refused:
                local_model.ask(_model_job(tmp_path, url, name=f"unreadable-{i}"), "Say hi")
        finally:
            listener.close()
        assert str(refused.value) == "The local model gave an answer this helper can't read"
    with pytest.raises(local_model.LocalModelError) as refused:
        local_model.ask(_model_job(tmp_path, "http://localhost:11x34", name="bad-port"), "Say hi")
    assert str(refused.value) == "Clips Kitty's model address http://localhost:11x34 has a port this helper can't use"


def test_ask_refuses_an_empty_model(tmp_path, servers):
    ollama = servers(_ollama())
    for i, name in enumerate(("", "   ")):
        job = _model_job(tmp_path, ollama.url, model=name, name=f"empty-{i}")
        for call in ASKING:
            with pytest.raises(local_model.LocalModelError) as refused:
                call(job)
            assert str(refused.value) == ("No local model is set in Clips Kitty (its AI may run at a cloud "
                                          "provider), and this helper only uses a model on this PC")
    assert ollama.requests == []


def test_ask_needs_the_ollama_permission(tmp_path, servers):
    ollama = servers(_ollama())
    job = _model_job(tmp_path, ollama.url, permissions=("transcript.read",))
    assert job.tools.ollama is None  # Clips Kitty hands the model over only with the permission
    for call in ASKING:
        with pytest.raises(local_model.LocalModelError) as refused:
            call(job)
        assert str(refused.value) == ("This pipeline doesn't ask for the local model: add ollama to permissions "
                                      "in clipskitty.yaml")
    assert ollama.requests == []
    assert not issubclass(local_model.LocalModelError, job_module.PROGRAMMING_ERRORS)  # creators see its words


def test_can_see_reads_capabilities(tmp_path, servers):
    sees = servers(_ollama(capabilities=("completion", "vision")))
    job = _model_job(tmp_path, sees.url, model="test-model-sees", name="sees")
    assert local_model.can_see(job) is True
    assert local_model.can_see(job) is True
    assert sees.paths() == ["/api/show"]  # asked once
    blind = servers(_ollama(capabilities=("completion",)))
    assert local_model.can_see(_model_job(tmp_path, blind.url, name="blind")) is False
    old = servers(lambda path, body: (200, {"modelfile": "an Ollama that doesn't list capabilities"}))
    assert local_model.can_see(_model_job(tmp_path, old.url, name="old")) is False

    # Nothing answering at the address.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{s.getsockname()[1]}"
    with pytest.raises(local_model.LocalModelError, match=r"^The local model didn't answer at "
                                                          + closed.replace(".", r"\.") + r" \(.*\): is Ollama running\?$"):
        local_model.can_see(_model_job(tmp_path, closed, name="closed"))


@pytest.fixture
def proxy_settings(monkeypatch, servers):
    """http_proxy, HTTP_PROXY and HTTPS_PROXY pointing at a server that
    records what reaches it, as a plugin started with them would see them:
    urllib's openers are made again, since each reads the settings when it
    is made."""
    proxy = servers(lambda path, body: (502, {"error": "this is the proxy"}))
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    for name in ("http_proxy", "HTTP_PROXY", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, proxy.url)
    monkeypatch.setattr(urllib.request, "_opener", None)  # urlopen()'s own, made on its next use
    importlib.reload(_loopback)
    return proxy


def test_ask_and_can_see_ignore_proxy_settings(tmp_path, servers, proxy_settings):
    ollama = servers(_ollama(capabilities=("completion", "vision", "thinking")))
    job = _model_job(tmp_path, ollama.url, model="test-model-proxied")
    # Without the SDK's own route, urllib sends a request for this PC to the proxy.
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.build_opener().open(ollama.url + "/api/show", timeout=10)
    assert len(proxy_settings.requests) == 1
    proxy_settings.requests.clear()

    assert local_model.can_see(job) is True
    assert local_model.ask(job, "Rate it", images=[b"\xff\xd8"], json=True)
    assert ollama.paths() == ["/api/show", "/api/generate"]
    assert proxy_settings.requests == []


def test_localapi_ignores_proxy_settings_for_this_pc(servers, proxy_settings):
    app = servers(lambda path, body: (200, {"ok": True, "app_version": "test", "api_version": 1}))
    assert LocalAPI(app.url).health() == {"ok": True, "app_version": "test", "api_version": 1}
    assert app.paths() == ["/health"]
    assert proxy_settings.requests == []


# ---- job.run(): an SDK import inside main() -------------------------------------------

MANIFEST = """\
manifest_version: 1
id: example-dev/quarkbloom-imports
name: Quarkbloom imports
version: 1.0.0
kind: pipeline
capability: highlight_detection
description: A test plugin for Quarkbloom Arena (a made-up game).
license: MIT
requires: {clips_kitty: '>=2.0', plugin_api: 1}
run:
  command: ['{python}', src/main.py]
execution: local
inputs: [transcript]
outputs: [ranges]
permissions: [transcript.read]
"""


def _importing_plugin(tmp_path, name, statement) -> Path:
    folder = tmp_path / name
    (folder / "src").mkdir(parents=True)
    (folder / "clipskitty.yaml").write_text(MANIFEST, encoding="utf-8")
    (folder / "src" / "main.py").write_text(
        "from clipskitty_sdk import run\n"
        "\n"
        "\n"
        "def main(job):\n"
        f"    {statement}\n"
        "    job.add_range(21, 30, score=80)\n"
        "\n"
        "\n"
        "run(main)\n", encoding="utf-8")
    return folder


def _run_it(tmp_path, plugin):
    return testing.run_plugin(plugin, transcript=testing.sample_transcript(), duration=testing.SAMPLE_VIDEO_SECONDS,
                              tmp_path=tmp_path)


def test_run_says_newer_version_for_an_sdk_import_inside_main(tmp_path):
    pytest.importorskip("yaml")
    for i, statement in enumerate(("from clipskitty_sdk import nothing_here",
                                   "import clipskitty_sdk.nothing_here",
                                   "from clipskitty_sdk.job import NothingHere")):
        run = _run_it(tmp_path, _importing_plugin(tmp_path, f"plugin-{i}", statement))
        assert not run.ok
        assert run.error == job_module.NEWER_VERSION == (
            "This pipeline needs a newer version of Clips Kitty. Update Clips Kitty, or ask the pipeline's "
            "developer which version it needs.")
        assert "Traceback (most recent call last):" in run.log  # the original, for the developer
        assert [line for line in run.log if line.startswith(("ImportError: ", "ModuleNotFoundError: "))
                and "nothing" in line.lower()]
        summary = [line for line in run.log if line.endswith("the clipskitty_sdk it runs with doesn't have it")]
        assert len(summary) == 1 and "(src/main.py line 5)" in summary[0], run.log


def test_run_keeps_other_import_errors_as_they_are(tmp_path):
    pytest.importorskip("yaml")
    for i, (statement, expected) in enumerate((
            ("from json import nothing_here", f"cannot import name 'nothing_here' from 'json' ({json.__file__})"),
            ("import not_a_real_package_xyz", "No module named 'not_a_real_package_xyz'"),
            ("import clipskitty_sdkx", "No module named 'clipskitty_sdkx'"))):
        run = _run_it(tmp_path, _importing_plugin(tmp_path, f"plugin-{i}", statement))
        assert not run.ok
        assert run.error == expected  # its own message, as before
        assert not [line for line in run.log if line.endswith("doesn't have it")]


def test_needs_newer_sdk_matches_the_sdk_and_its_modules_only():
    def error(name, cls=ImportError):
        return cls("missing", name=name)

    assert job_module.needs_newer_sdk(error("clipskitty_sdk"))
    assert job_module.needs_newer_sdk(error("clipskitty_sdk.media", ModuleNotFoundError))
    assert job_module.needs_newer_sdk(error("clipskitty_sdk.media.deeper"))
    assert not job_module.needs_newer_sdk(error("clipskitty_sdkx", ModuleNotFoundError))
    assert not job_module.needs_newer_sdk(error("numpy", ModuleNotFoundError))
    assert not job_module.needs_newer_sdk(error(None))
    assert not job_module.needs_newer_sdk(KeyError("clipskitty_sdk"))
