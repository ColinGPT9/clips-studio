"""A dub with a Piper voice, pinned down call by call.

The voice layer (voice/, editor suite step V1) sits between dubbing and the
voices. A dub made with a Piper voice must come out exactly as it did before
the layer existed: the same voice file loaded, the same speech calls with the
same pace and speaker, the same ffprobe questions and the same ffmpeg mix.

So Piper, ffprobe and ffmpeg are faked here and everything dubbing asks of
them is compared with tests/snapshots/dub_piper.json. That file was recorded
by running this very test on the commit before the voice layer (758219a), and
is only ever rewritten on purpose:

    python -m pytest tests/test_voice_dub_piper_snapshot.py --update-snapshots

Nothing here needs numpy, Piper, FFmpeg or the Kokoro files.
"""

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "snapshots" / "dub_piper.json"

LINES = [
    {"start": 0.5, "end": 2.5, "text": "Hola a todos."},
    {"start": 3.0, "end": 5.0, "text": "Hoy vamos a ver algo nuevo."},
    {"start": 5.5, "end": 6.5, "text": "¿Listos?"},
    {"start": 7.0, "end": 9.0, "text": "Empecemos ahora mismo."},
]
# What ffprobe answers for each utterance's first take, in order: in its slot,
# far too long, far too short, a little long. The middle two are spoken again
# at another pace; a second take's length is never asked for.
FIRST_TAKES = [2.0, 3.6, 0.3, 2.3]

CASES = {
    "chosen_voice": {"language": "es", "voice": "es_MX-claude-high"},
    "chosen_speaker": {"language": "fr", "voice": "fr_FR-upmc-medium#1"},
    "default_voice_german": {"language": "de", "voice": None},
    "default_voice_spanish": {"language": "es", "voice": None},
    "bad_voice_id_falls_back": {"language": "it", "voice": "../../x"},
}
INSTALLED = ["es_MX-claude-high", "fr_FR-upmc-medium", "de_DE-thorsten-medium", "es_ES-davefx-medium",
             "it_IT-paola-medium"]


class Recorder:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.calls: list = []
        self.takes: dict[str, int] = {}

    def clean(self, value):
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        text = str(value)
        for root in {str(self.tmp.resolve()), str(self.tmp)}:
            text = text.replace(root, "<tmp>")
        return text.replace("\\", "/")


def _fake_piper(rec: Recorder) -> types.ModuleType:
    piper = types.ModuleType("piper")

    class SynthesisConfig:
        def __init__(self, length_scale=None, **other):
            assert not other, f"dubbing passed Piper something new: {other}"
            self.length_scale = length_scale
            self.speaker_id = None

    class PiperVoice:
        def __init__(self, model):
            self.model = Path(model)

        @classmethod
        def load(cls, model, **other):
            assert not other, f"dubbing loads a voice with something new: {other}"
            rec.calls.append(["load", rec.clean(model)])
            return cls(model)

        def synthesize_wav(self, text, wav, syn_config=None, **other):
            assert not other, f"dubbing speaks with something new: {other}"
            rec.calls.append(["speak", self.model.stem, text, syn_config.length_scale, syn_config.speaker_id])
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x01\x00" * 2205)

    piper.SynthesisConfig = SynthesisConfig
    piper.PiperVoice = PiperVoice
    return piper


@pytest.fixture
def rec(tmp_path, monkeypatch):
    import core.binaries as binaries

    rec = Recorder(tmp_path)
    monkeypatch.setitem(sys.modules, "piper", _fake_piper(rec))
    probe, mixer = binaries.ffprobe(), binaries.ffmpeg()

    def run(cmd, *args, **kwargs):
        cmd = list(cmd)
        if cmd[0] == probe:
            name = Path(cmd[-1]).name
            n = self_takes = rec.takes.get(name, 0)
            rec.takes[name] = self_takes + 1
            index = int(name.rsplit("_", 1)[1].split(".")[0])
            assert n == 0, "the length of a second take was never asked for"
            rec.calls.append(["ffprobe", ["<ffprobe>", *rec.clean(cmd[1:])]])
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{FIRST_TAKES[index]}\n", stderr="")
        assert cmd[0] == mixer, f"dubbing ran something new: {cmd[0]}"
        rec.calls.append(["ffmpeg", ["<ffmpeg>", *rec.clean(cmd[1:])], sorted(kwargs)])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    voices = tmp_path / "voices"
    voices.mkdir()
    for name in INSTALLED:
        (voices / f"{name}.onnx").write_bytes(b"model")
    return rec


def _dub(rec: Recorder, case: dict, **more):
    from multilingual import dub

    tmp = rec.tmp
    # One lane, so the calls come in the script's order on every machine.
    out = dub.dub(LINES, case["language"], tmp / "clip.mp4", tmp / "out" / "clip.dubbed.mp4",
                  tmp / "voices", tmp / "work", voice_id=case["voice"], workers=1, **more)
    return {"returned": rec.clean(out), "calls": rec.calls}


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_piper_dub_asks_for_exactly_what_it_did_before_the_voice_layer(name, rec, update_snapshots):
    got = _dub(rec, CASES[name])
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}
    if update_snapshots:
        saved[name] = got
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(saved, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                            encoding="utf-8")
        return
    assert name in saved, f"no snapshot for {name}: it is recorded on purpose, never by CI"
    assert json.loads(json.dumps(got)) == saved[name]


# The translate job hands dubbing the data folder, so the voice layer is asked
# who speaks. With nothing changed in Settings the answer for a Piper voice is
# "Piper, as always", and every call stays the recorded one.

# No voice picked for Spanish is the one case that changes on purpose when the
# built-in voice's files are there: Kokoro speaks it.
PIPER_WHATEVER_IS_INSTALLED = sorted(set(CASES) - {"default_voice_spanish"})


def _settings_untouched(monkeypatch, tmp_path, kokoro_ready: bool):
    from voice.engines import kokoro

    monkeypatch.setattr(kokoro, "readiness", lambda: (kokoro_ready, "" if kokoro_ready else "Voice files missing."))
    monkeypatch.setattr(kokoro, "speak", lambda *a, **k: pytest.fail("the built-in voice spoke a Piper dub"))
    data = tmp_path / "data"
    assert not (data / "voice_settings.json").exists()
    return data


@pytest.mark.parametrize("name", PIPER_WHATEVER_IS_INSTALLED)
def test_through_the_job_a_piper_dub_is_unchanged_with_the_built_in_voice_installed(name, rec, monkeypatch, tmp_path):
    data = _settings_untouched(monkeypatch, tmp_path, kokoro_ready=True)
    got = _dub(rec, CASES[name], data_dir=data)
    assert json.loads(json.dumps(got)) == json.loads(SNAPSHOT.read_text(encoding="utf-8"))[name]
    assert not data.exists(), "asking who speaks wrote into the data folder"


@pytest.mark.parametrize("name", sorted(CASES))
def test_through_the_job_every_dub_is_unchanged_without_the_built_in_voices_files(name, rec, monkeypatch, tmp_path):
    data = _settings_untouched(monkeypatch, tmp_path, kokoro_ready=False)
    got = _dub(rec, CASES[name], data_dir=data)
    assert json.loads(json.dumps(got)) == json.loads(SNAPSHOT.read_text(encoding="utf-8"))[name]


# A Piper voice saved as the default in Settings → Voice is the voice of a dub
# that names none (the dubbing dialog sends only voices picked in the dialog).


def test_the_persons_default_piper_voice_speaks_a_dub_that_names_no_voice(rec, monkeypatch, tmp_path):
    from voice import settings as voice_settings

    data = _settings_untouched(monkeypatch, tmp_path, kokoro_ready=True)
    (tmp_path / "voices" / "de_DE-thorsten-high.onnx").write_bytes(b"model")
    voice_settings.update(data, voices={"de": "de_DE-thorsten-high", "fr": "fr_FR-upmc-medium#1"})

    german = _dub(rec, {"language": "de", "voice": None}, data_dir=data)
    spoke = [c for c in german["calls"] if c[0] == "speak"]
    assert spoke and {c[1] for c in spoke} == {"de_DE-thorsten-high"}, "the saved default, not Piper's usual voice"
    # Apart from the voice's name, every call is the recorded one.
    usual = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["default_voice_german"]
    swapped = json.loads(json.dumps(german).replace("de_DE-thorsten-high", "de_DE-thorsten-medium"))
    assert swapped == usual

    rec.calls.clear()
    rec.takes.clear()
    french = _dub(rec, {"language": "fr", "voice": None}, data_dir=data)
    assert {(c[1], c[4]) for c in french["calls"] if c[0] == "speak"} == {("fr_FR-upmc-medium", 1)}, \
        "a saved speaker inside a voice is kept too"

    rec.calls.clear()
    rec.takes.clear()
    picked = _dub(rec, {"language": "de", "voice": "de_DE-thorsten-medium"}, data_dir=data)
    assert {c[1] for c in picked["calls"] if c[0] == "speak"} == {"de_DE-thorsten-medium"}, \
        "a voice picked in the dialog still wins over the saved default"
