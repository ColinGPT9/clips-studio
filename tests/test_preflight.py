"""Can this install actually make a clip?

An installed copy can be half-ready in ways a checkout never is: no FFmpeg,
no Ollama, no model, no disk. Each of those used to surface as a stack trace
deep inside a pipeline stage, twenty minutes into a video.

Nothing in preflight may raise — a check that fails to run is reported as a
failed check, because a crashing health check is worse than no health check.
"""

import pytest

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
    """A checkout borrows the developer's own Ollama, so the advice is the
    familiar one: go and install it."""
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(preflight, "has_bundled_ollama", lambda: False)
    check = preflight.check_ollama("http://localhost:11434", "gemma:7b")[0]

    # Not a stack trace, not a module name — something to actually do.
    # Checks for "ollama" rather than the domain: the point of the test is
    # that the text names the thing to go and get, and matching a bare
    # hostname here reads to a scanner like a (broken) URL check.
    assert "ollama" in check.fix.lower()


def test_an_installed_copy_is_never_told_to_go_and_install_anything(monkeypatch):
    """The whole point of bundling the runtime. Sending a creator to
    ollama.com when the app already ships Ollama is worse than saying nothing:
    they install a second copy, it binds a different port, and the app still
    doesn't work."""
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(preflight, "has_bundled_ollama", lambda: True)
    check = preflight.check_ollama("http://localhost:11435", "gemma:7b")[0]

    assert check.fix, "a failed check must still tell the user something"
    assert "ollama.com" not in check.fix.lower()
    assert "install" not in check.fix.lower()


def test_a_model_that_is_missing_never_suggests_the_command_line(monkeypatch):
    """`ollama pull` from a terminal talks to the default port and the default
    model folder — neither of which is the bundled runtime's. The download
    would appear to succeed and the app would still report the model missing."""
    import requests

    class Response:
        @staticmethod
        def raise_for_status() -> None:
            """A 200: there is nothing to raise."""

        @staticmethod
        def json() -> dict:
            return {"models": []}

    monkeypatch.setattr(requests, "get", lambda *a, **k: Response())
    monkeypatch.setattr(preflight, "has_bundled_ollama", lambda: True)
    model = preflight.check_ollama("http://localhost:11435", "gemma:7b")[1]

    assert model.name == "model"
    assert model.ok is False
    assert "ollama pull" not in model.fix.lower()


def _ollama_with(monkeypatch, installed: list[str]) -> None:
    """Stub Ollama as holding exactly `installed`."""
    import requests

    class Response:
        @staticmethod
        def raise_for_status() -> None:
            """A 200."""

        @staticmethod
        def json() -> dict:
            return {"models": [{"name": n, "size": 3_000_000_000} for n in installed]}

    monkeypatch.setattr(requests, "get", lambda *a, **k: Response())
    monkeypatch.setattr(preflight, "has_bundled_ollama", lambda: True)


def test_the_microsoft_certification_scenario(monkeypatch):
    """The submission failed 10.1.2.10 "Unusable Feature: Install AI Model".

    A reviewer on a laptop with no graphics card let setup download the model
    it recommended for that hardware — gemma3:4b — watched it reach 100%, and
    was still told "gemma:7b not installed". The runtime line said one model
    was installed directly above it, which is what made the app look broken.

    The check was asking whether one specific tag was present. It must ask
    whether anything can run, because setup downloads what suits the machine
    and settings.yaml ships the tag that suits an 8 GB card.
    """
    _ollama_with(monkeypatch, ["gemma3:4b"])
    model = preflight.check_ollama("http://localhost:11434", "gemma:7b")[1]

    assert model.ok is True, "this is the exact assertion that failed certification"
    assert model.detail == "gemma3:4b installed"
    assert "gemma:7b" not in model.detail, "must not name a model nobody chose"


@pytest.mark.parametrize(
    "installed, expected, why",
    [
        ([], False, "nothing installed is the only real failure"),
        (["gemma3:4b"], True, "the model the reviewer actually downloaded"),
        (["gemma:7b"], True, "the shipped default still works"),
        (["qwen3:8b"], True, "not a Gemma at all, and still fine"),
        (["mistral-nemo:12b"], True, "any installed Ollama model counts"),
        (["gemma3:4b", "gemma:7b"], True, "several installed"),
    ],
)
def test_the_check_is_model_agnostic(monkeypatch, installed, expected, why):
    """gemma:7b being absent must never fail the check on its own.

    It is the default in settings.yaml, so it is the tag most likely to be
    configured and missing at once — which is precisely the combination that
    failed the Store review.
    """
    _ollama_with(monkeypatch, installed)
    model = preflight.check_ollama("http://localhost:11434", "gemma:7b")[1]
    assert model.ok is expected, why


def test_the_check_reads_live_state_rather_than_remembering(monkeypatch):
    """Install then remove: the verdict has to follow the machine, not a cached
    success from earlier in the session."""
    _ollama_with(monkeypatch, ["gemma3:4b"])
    assert preflight.check_ollama("http://localhost:11434", "gemma:7b")[1].ok is True

    _ollama_with(monkeypatch, [])
    gone = preflight.check_ollama("http://localhost:11434", "gemma:7b")[1]
    assert gone.ok is False
    assert gone.detail == "no AI model installed"


def test_no_model_says_no_model_rather_than_naming_one(monkeypatch):
    """"gemma:7b not installed" reads as "this app needs a thing you do not
    have". "No AI model installed" is the true statement, and the action that
    follows it — open the Models page — works whatever the hardware."""
    _ollama_with(monkeypatch, [])
    model = preflight.check_ollama("http://localhost:11434", "gemma:7b")[1]

    assert model.ok is False
    assert "gemma" not in model.detail.lower()
    assert "Models page" in model.fix


def test_the_pipeline_loads_the_model_the_check_promised(monkeypatch):
    """A green check has to mean the model that will load is present.

    Reporting gemma3:4b as usable while the pipeline still builds a gemma:7b
    backend would move the failure out of setup and into the first video —
    still an unusable feature, just found later.
    """
    from core.pipeline import _with_usable_model

    monkeypatch.setattr(
        "llm.manager.installed_models",
        lambda host: [{"name": "gemma3:4b", "size_gb": 3.3}],
    )
    resolved = _with_usable_model({"backend": "ollama/gemma:7b"})
    assert resolved["backend"] == "ollama/gemma3:4b"


def test_an_installed_model_is_left_alone(monkeypatch):
    """The fallback must not second-guess a choice that works — a 12 GB card
    running gemma3:12b keeps it, rather than being pulled to a smaller one."""
    from core.pipeline import _with_usable_model

    monkeypatch.setattr(
        "llm.manager.installed_models",
        lambda host: [{"name": "gemma3:4b", "size_gb": 3.3},
                      {"name": "gemma3:12b", "size_gb": 8.1}],
    )
    cfg = {"backend": "ollama/gemma3:12b"}
    assert _with_usable_model(cfg)["backend"] == "ollama/gemma3:12b"


def test_absent_whisper_weights_warn_without_blocking(monkeypatch):
    """The silent failure this check exists for. faster-whisper can still
    fetch the weights and produce a correct transcript, so refusing to run
    would be wrong — but it happens with no progress bar, minutes into a job,
    and is indistinguishable from a hang. Say so instead."""
    monkeypatch.setattr(preflight, "bundled_whisper_sizes", list)
    check = preflight.check_whisper("auto")

    assert check.ok is False
    assert check.blocking is False
    assert check.fix, "the user should be told the pause is coming"


def test_auto_needs_both_sizes(monkeypatch):
    """`auto` picks large-v3-turbo on a GPU and small on CPU, and which one
    this machine turns out to be isn't known until load time. Half the pair
    bundled means half of all installs still download one silently."""
    monkeypatch.setattr(preflight, "bundled_whisper_sizes", lambda: ["small"])
    check = preflight.check_whisper("auto")

    assert check.ok is False
    assert "large-v3-turbo" in check.fix


def test_a_forced_size_only_needs_that_size(monkeypatch):
    """Someone who pinned whisper.model shouldn't be warned about weights for
    a size the app will never load."""
    monkeypatch.setattr(preflight, "bundled_whisper_sizes", lambda: ["small"])
    check = preflight.check_whisper("small")

    assert check.ok is True
    assert check.fix == ""


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
    assert {"ffmpeg", "ffprobe", "ollama", "gpu", "disk"} <= names


def test_model_is_only_reported_when_ollama_can_be_asked(tmp_path):
    """With Ollama down there is no honest answer about which models are
    installed, so no model row is invented — the actionable thing is
    "install Ollama", and a second row guessing about models would only
    compete with it.

    This differs by machine: a developer usually has Ollama running and a
    fresh install does not, so both cases are pinned here.
    """
    pf = preflight.run({"paths": {"data_dir": str(tmp_path)}, "model": "gemma:7b"})
    by_name = {c.name: c for c in pf.checks}

    if by_name["ollama"].ok:
        assert "model" in by_name, "Ollama is up, so the model must be reported on"
    else:
        assert "model" not in by_name, "cannot claim anything about models here"
        assert by_name["ollama"].blocking, "no AI at all is a blocking problem"


def test_recommendation_matches_the_models_table():
    """The setup wizard and the Models page must never disagree about which
    model to install — they did, for a 12 GB card."""
    from llm.manager import RECOMMENDATIONS, recommend_for

    table = {hardware: model for hardware, model, _ in RECOMMENDATIONS}
    offered = set(table.values())

    assert recommend_for(12)["model"] == table["10-12 GB VRAM"]
    assert recommend_for(24)["model"] == table["16-24 GB VRAM"]
    assert recommend_for(8)["model"] == table["8 GB VRAM"]
    assert recommend_for(6)["model"] == table["6 GB VRAM"]
    assert recommend_for(4)["model"] == table["Under 6 GB VRAM, or an older PC"]

    # No card at all is its own row: a 7B on the processor is slow enough to
    # read as broken, so CPU-only gets the 4B rather than the tested default.
    assert recommend_for(None)["model"] == table["CPU / iGPU, no graphics card"]

    # gemma:7b is the default in config/settings.yaml and the model everything
    # was tested against. It must be offered somewhere, or the Models page
    # recommends against the app's own shipped default.
    assert "gemma:7b" in offered

    # Whatever the wizard suggests must be a model this page actually lists,
    # which is the real point: the two must never disagree.
    for vram in (None, 0, 6, 8, 12, 24, 48):
        assert recommend_for(vram)["model"] in offered, (
            f"recommend_for({vram}) offers a model the table does not list"
        )


def test_every_recommendation_explains_itself():
    from llm.manager import recommend_for

    for vram in (None, 0, 6, 12, 24):
        rec = recommend_for(vram)
        assert rec["model"] and rec["reason"]
