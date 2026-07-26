"""Can this install actually make a clip?

An installed copy can be half-ready in ways a checkout never is: no FFmpeg,
no Ollama, no model, no disk. Each of those used to surface as a stack trace
deep inside a pipeline stage, twenty minutes into a video.

Nothing in preflight may raise — a check that fails to run is reported as a
failed check, because a crashing health check is worse than no health check.
"""

from core import preflight


def test_a_failing_check_never_raises(monkeypatch):
    """Ollama unreachable is the normal case on a fresh install, not an
    exception."""
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("nothing listening")

    monkeypatch.setattr(requests, "get", boom)
    checks = preflight.check_ollama("http://localhost:11434", "gemma:7b")

    assert checks[0].name == "ollama"
    assert checks[0].ok is False
    assert checks[0].fix, "a failed check must tell the user what to do"


def test_the_fix_text_is_written_for_a_creator(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    check = preflight.check_ollama("http://localhost:11434", "gemma:7b")[0]

    # Not a stack trace, not a module name — something to actually do.
    assert "ollama.com" in check.fix.lower()


def test_gpu_absence_is_not_blocking():
    """The app works on CPU, just slowly. Reporting that as a hard failure
    would tell people it is broken when it is not."""
    check = preflight.check_gpu()
    assert check.blocking is False


def test_disk_is_not_blocking(tmp_path):
    check = preflight.check_disk(tmp_path)
    assert check.blocking is False
    assert check.detail


def test_disk_check_survives_a_missing_directory(tmp_path):
    """data_dir does not exist yet on a first run."""
    check = preflight.check_disk(tmp_path / "not" / "created" / "yet")
    assert check.name == "disk"


def test_ready_ignores_non_blocking_failures():
    """No GPU and a full-ish disk should not stop someone clipping."""
    pf = preflight.Preflight(
        checks=[
            preflight.Check(name="ffmpeg", ok=True),
            preflight.Check(name="gpu", ok=False, blocking=False),
            preflight.Check(name="disk", ok=False, blocking=False),
        ]
    )
    assert pf.ready is True


def test_ready_is_false_when_something_blocking_is_missing():
    pf = preflight.Preflight(
        checks=[
            preflight.Check(name="ffmpeg", ok=True),
            preflight.Check(name="ollama", ok=False, blocking=True),
        ]
    )
    assert pf.ready is False


def test_as_dict_shape_matches_what_the_wizard_reads():
    pf = preflight.Preflight(checks=[preflight.Check(name="ffmpeg", ok=True, detail="8.1")])
    payload = pf.as_dict()

    assert set(payload) == {"ready", "checks"}
    assert set(payload["checks"][0]) == {"name", "ok", "detail", "fix", "blocking"}


def test_full_run_reports_every_area(tmp_path):
    """The wizard shows these by name; losing one would silently stop
    checking something."""
    pf = preflight.run({"paths": {"data_dir": str(tmp_path)}, "model": "gemma:7b"})
    names = {c.name for c in pf.checks}
    assert {"ffmpeg", "ffprobe", "ollama", "model", "gpu", "disk"} <= names


def test_recommendation_matches_the_models_table():
    """The setup wizard and the Models page must never disagree about which
    model to install — they did, for a 12 GB card."""
    from llm.manager import RECOMMENDATIONS, recommend_for

    table = {hardware: model for hardware, model, _ in RECOMMENDATIONS}

    assert recommend_for(12)["model"] == table["10-12 GB VRAM"]
    assert recommend_for(24)["model"] == table["16-24 GB VRAM"]
    assert recommend_for(8)["model"] == table["6-8 GB VRAM"]
    assert recommend_for(None)["model"] == table["CPU only / iGPU"]


def test_every_recommendation_explains_itself():
    from llm.manager import recommend_for

    for vram in (None, 0, 6, 12, 24):
        rec = recommend_for(vram)
        assert rec["model"] and rec["reason"]
