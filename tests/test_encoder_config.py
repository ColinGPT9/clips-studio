"""video/encoding.py: which encoder renders and exports use.

- The settings' video.encoder reaches every encode once configure() ran at
  engine start, not just the two calls that used to pass the config.
- "auto" probes exactly as it always has: the same probes, in the same order,
  at the first encode.
- A forced encoder that fails its probe is probed once, not before every
  encode.
- An export choosing its own encoder (encoder_for) never changes the encoder
  the renders use, and keeps its own probe cache.

Light tier: the probe is faked here, and tests/render_harness.py fakes FFmpeg
for the call-site test.
"""

from dataclasses import replace

import pytest
from render_harness import RenderHarness

from core.models import ClipCandidate
from video import cutter, encoding
from video_editor import export
from video_editor.timeline import EditList

NVENC = encoding._CANDIDATES["nvenc"]
CPU = encoding.CPU_ARGS
WINDOW = ClipCandidate(start=10.0, end=20.0, score=80)
PADDED = replace(WINDOW, end=WINDOW.end + 0.4)


@pytest.fixture
def fresh(monkeypatch):
    """The encoder state of an engine that has just started, put back afterwards."""
    monkeypatch.setattr(encoding, "_selected", None)
    monkeypatch.setattr(encoding, "_mode", "auto")
    monkeypatch.setattr(encoding, "_refused", set())
    monkeypatch.setattr(encoding, "_export_probes", {})
    return monkeypatch


def fake_probes(mp, working: set[str]) -> list[list[str]]:
    """Fake the test encode: an encoder works when its FFmpeg name is in
    `working`. Returns the probes made, as argument lists."""
    made: list[list[str]] = []

    def probe(args):
        made.append(list(args))
        return args[args.index("-c:v") + 1] in working

    mp.setattr(encoding, "_probe", probe)
    return made


def names(probes: list[list[str]]) -> list[str]:
    return [p[p.index("-c:v") + 1] for p in probes]


# ---- renders --------------------------------------------------------------------------


def test_auto_probes_as_it_always_has(fresh):
    made = fake_probes(fresh, {"h264_qsv"})
    assert encoding.configure({"video": {"encoder": "auto"}}) == "auto"
    assert made == [], "configure() probes nothing"
    assert encoding.video_encoder_args() == encoding._CANDIDATES["qsv"]
    assert made == [encoding._CANDIDATES[n] for n in ("nvenc", "amf", "qsv")]
    encoding.video_encoder_args()
    encoding.video_encoder_args({"video": {"encoder": "auto"}})
    assert len(made) == 3, "once per process"


def test_the_settings_encoder_reaches_calls_that_pass_no_config(fresh):
    made = fake_probes(fresh, {"h264_nvenc", "h264_amf"})
    encoding.configure({"video": {"encoder": "cpu"}})
    assert encoding.video_encoder_args() == CPU
    assert made == [] and not encoding.using_hardware_encoder()
    encoding.configure({"video": {"encoder": "amf"}})
    assert encoding.video_encoder_args() == encoding._CANDIDATES["amf"]
    assert names(made) == ["h264_amf"] and encoding.using_hardware_encoder()


def test_a_forced_encoder_that_fails_is_probed_once(fresh):
    made = fake_probes(fresh, set())
    encoding.configure({"video": {"encoder": "nvenc"}})
    for _ in range(15):
        assert encoding.video_encoder_args() == CPU
    assert names(made) == ["h264_nvenc"]
    assert encoding.video_encoder_args({"video": {"encoder": "nvenc"}}) == CPU
    assert len(made) == 1


def test_a_config_naming_an_encoder_still_chooses_it_for_the_process(fresh):
    """The golden-render and benchmark scripts force the CPU this way."""
    fake_probes(fresh, {"h264_nvenc"})
    assert encoding.video_encoder_args() == NVENC
    assert encoding.video_encoder_args({"video": {"encoder": "cpu"}}) == CPU
    assert encoding.video_encoder_args() == CPU


def test_a_config_naming_no_encoder_keeps_the_settings_choice(fresh):
    made = fake_probes(fresh, {"h264_nvenc"})
    encoding.configure({"video": {"encoder": "cpu"}})
    assert encoding.video_encoder_args({"clips": {}}) == CPU
    assert encoding.video_encoder_args({"video": {}}) == CPU
    assert encoding.video_encoder_args({"video": None}) == CPU
    assert made == []


@pytest.mark.parametrize("value,mode", [
    ("auto", "auto"), ("NVENC", "nvenc"), (" cpu ", "cpu"), ("videotoolbox", "videotoolbox"),
    ("turbo", "auto"), (None, "auto"), (3, "auto"),
])
def test_settings_values(fresh, value, mode):
    assert encoding.configure({"video": {"encoder": value}}) == mode


def test_no_settings_mean_auto(fresh):
    assert encoding.configure(None) == "auto"
    assert encoding.configure({}) == "auto"
    assert encoding.configure({"video": "nvenc"}) == "auto"


def _encoders_in(h: RenderHarness) -> set[str]:
    """The video encoders the recorded FFmpeg commands encode with (not the
    probes, which write nowhere)."""
    found = set()
    for c in h.commands:
        argv = c["argv"]
        if "-c:v" in argv and argv[-1] != "-":
            found.add(argv[argv.index("-c:v") + 1])
    return found - {"copy"}


def _probes_in(h: RenderHarness) -> int:
    return sum(1 for c in h.commands if c["argv"][-3:] == ["-f", "null", "-"] and "lavfi" in c["argv"])


@pytest.mark.parametrize("forced,expected,probes", [
    ("cpu", "libx264", 0),        # no probe at all
    ("nvenc", "h264_nvenc", 1),   # the harness's GPU: one probe, then NVENC everywhere
    ("amf", "libx264", 1),        # a card this PC lacks: one probe, then the CPU everywhere
])
def test_configure_reaches_the_call_sites(fresh, tmp_path, forced, expected, probes):
    """Through real render code, with FFmpeg faked (a GPU that does NVENC
    only): a cut and an editor pass, both of which call video_encoder_args()
    with no config, encode with what the settings forced."""
    h = RenderHarness(fresh, tmp_path, "nvenc")
    fresh.setattr(encoding, "_selected", None)      # an engine that hasn't encoded yet
    encoding.configure({"video": {"encoder": forced}})
    cutter.cut_clip(h.source(), PADDED, h.out("clip.source.mp4"))
    staged = h.media("clip.staged.mp4", duration=PADDED.duration)
    export.apply_edits(staged, EditList.from_dict({"keep": [[0.0, 3.0], [4.0, 9.0]]}, duration=WINDOW.duration),
                       h.root / "clip.edited.mp4")
    cutter.cut_clip(h.source(), PADDED, h.out("clip.mp4"), normalize=True, final=True)
    h.check()
    assert _encoders_in(h) == {expected}
    assert _probes_in(h) == probes


# ---- exports ----------------------------------------------------------------------------


def test_an_export_never_changes_the_render_encoder(fresh):
    fake_probes(fresh, {"libx264", "libx265", "hevc_nvenc", "h264_nvenc"})
    fresh.setattr(encoding, "_select", lambda mode: pytest.fail("an export chose the render encoder"))
    chosen = ("nvenc", NVENC)
    fresh.setattr(encoding, "_selected", chosen)
    for codec in ("h264", "hevc", "av1"):
        for pref in ("auto", "cpu", "gpu"):
            encoding.encoder_for(codec, pref)
    encoding.export_codecs()
    assert encoding._selected is chosen
    assert encoding.video_encoder_args() == NVENC
    # An engine that hasn't rendered yet: an export leaves nothing chosen.
    fresh.setattr(encoding, "_selected", None)
    encoding.encoder_for("h264", "cpu")
    encoding.encoder_for("h264", "gpu")
    assert encoding._selected is None


def test_export_probes_are_their_own_and_remembered(fresh):
    made = fake_probes(fresh, {"libx264", "h264_qsv"})
    assert encoding.encoder_for("h264") == encoding.Encoder("h264", "qsv", "h264_qsv")
    assert names(made) == ["h264_nvenc", "h264_amf", "h264_qsv"]
    assert made[-1] == encoding.export_probe_args("h264_qsv")
    assert made[-1] != encoding._CANDIDATES["qsv"], "an export probes with its own flags"
    count = len(made)
    encoding.encoder_for("h264")
    encoding.encoder_for("h264", "gpu")
    assert len(made) == count, "remembered"


def test_a_failed_export_probe_is_tried_again_later(fresh):
    made = fake_probes(fresh, set())
    assert encoding.encoder_for("av1") is None
    count = len(made)
    assert encoding.encoder_for("av1") is None and len(made) == count
    for name, (works, when) in list(encoding._export_probes.items()):
        encoding._export_probes[name] = (works, when - encoding.EXPORT_RECHECK_SECONDS - 1)
    fake_probes(fresh, {"av1_nvenc"})        # the card is free again
    assert encoding.encoder_for("av1") == encoding.Encoder("av1", "nvenc", "av1_nvenc")


def test_which_encoder_an_export_prefers(fresh):
    fake_probes(fresh, {"libx264", "h264_nvenc", "h264_qsv", "hevc_qsv", "av1_qsv"})
    assert encoding.encoder_for("h264", "cpu").name == "libx264"
    assert encoding.encoder_for("h264", "gpu").name == "h264_nvenc"
    assert encoding.encoder_for("h264").name == "h264_nvenc"      # auto: the card first
    assert encoding.encoder_for("hevc", "cpu") is None            # no libx265 in this fake FFmpeg
    assert encoding.encoder_for("hevc").name == "hevc_qsv"
    assert encoding.encoder_for("av1", "cpu") is None             # AV1 is graphics cards only
    assert encoding.encoder_for("av1").name == "av1_qsv"
    assert encoding.encoder_for("vp9") is None
    encoding.configure({"video": {"encoder": "cpu"}})
    assert encoding.encoder_for("h264").name == "libx264"         # the settings' choice first
    encoding.configure({"video": {"encoder": "qsv"}})
    assert encoding.encoder_for("h264").name == "h264_qsv"


def test_export_codecs_lists_only_what_works(fresh):
    fake_probes(fresh, {"libx264", "libx265", "hevc_nvenc"})
    assert encoding.export_codecs() == {
        "h264": {"cpu": "libx264", "gpu": None},
        "hevc": {"cpu": "libx265", "gpu": "hevc_nvenc"},
    }
