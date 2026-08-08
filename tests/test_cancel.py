"""Cancel has to land while a stage is running, not after it finishes.

Cancellation is cooperative: the API sets a flag and the pipeline unwinds at
the next check. That only works if something checks *often enough*. It did
not. The checks sat at stage boundaries, so pressing Cancel during the two
slowest stages did nothing until they completed on their own:

  * transcription — one call over the whole video, many minutes
  * scoring — one LLM call per chunk, and a 12b model makes each one long

The app showed "cancelling" and kept working for hours. Reported against a
2-hour video on gemma3:12b with 152 minutes remaining.

Neither `transcribe()` nor `find_clips()` receives a video id — one takes a
path, the other a list of segments — so the fix is `cancel.check_active()`,
which asks about the video the worker is processing right now.
"""

import time
from pathlib import Path

import pytest

from core import cancel


@pytest.fixture(autouse=True)
def clean_cancel_state():
    """Module-level state; leaking it between tests would be invisible and
    maddening."""
    cancel.set_active(None)
    yield
    active = cancel.active_video()
    if active:
        cancel.clear(active)
    cancel.set_active(None)


def test_check_active_passes_when_nothing_is_cancelled():
    cancel.set_active("vid1")
    cancel.check_active()  # must not raise


def test_check_active_raises_for_the_running_video():
    cancel.set_active("vid1")
    cancel.request_cancel("vid1")

    with pytest.raises(cancel.CancelledError):
        cancel.check_active()


def test_check_active_ignores_a_cancel_for_a_different_video():
    """Cancelling video B must not abort video A. The flag set survives, so
    a stale id from an earlier job could otherwise kill an unrelated run."""
    cancel.set_active("vid1")
    cancel.request_cancel("vid2")

    cancel.check_active()  # must not raise
    cancel.clear("vid2")


def test_check_active_is_quiet_when_nothing_is_running():
    """The API's re-render paths call into the same code with no job active."""
    cancel.set_active(None)
    cancel.request_cancel("vid1")

    cancel.check_active()  # must not raise
    cancel.clear("vid1")


def test_cancel_interrupts_a_long_transcription():
    """The reported bug, end to end.

    Whisper returns a generator and transcription happens as it is consumed.
    A fake generator stands in for that: cancel is requested part-way, and
    the loop must stop there rather than draining all 1000 segments.
    """
    from transcription import transcriber

    consumed = []

    class FakeWhisperSegment:
        def __init__(self, i):
            self.start, self.end, self.text, self.words = float(i), i + 1.0, f"line {i}", []

    def endless_segments():
        for i in range(1000):
            consumed.append(i)
            if i == 5:  # the user presses Cancel
                cancel.request_cancel("vid1")
            yield FakeWhisperSegment(i)

    class FakeInfo:
        duration, language = 1000.0, "en"

    class FakeModel:
        def transcribe(self, *a, **k):
            return endless_segments(), FakeInfo()

    cancel.set_active("vid1")

    with pytest.raises(cancel.CancelledError):
        _run_transcribe(transcriber, FakeModel(), tmp_target="vid1")

    # Stopped promptly rather than draining the generator.
    assert len(consumed) < 20, f"kept consuming after cancel: {len(consumed)} segments"
    cancel.clear("vid1")


def _run_transcribe(transcriber, model, tmp_target):
    """Drive transcribe() with a fake model, via the real code path."""
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as td:
        with patch.object(transcriber, "_load_model", return_value=model):
            transcriber.transcribe(
                Path(td) / "video.mp4", tmp_target, Path(td), model_size="small", device="cpu"
            )


def test_cancel_interrupts_chunked_scoring():
    """Same failure in the analyze stage: one LLM call per chunk, so the
    check has to sit in the chunk loop."""
    from analysis import highlights
    from core.models import Segment

    calls = []

    class SlowBackend:
        name = "fake"

        def generate(self, prompt, *, json_mode=False):
            calls.append(1)
            if len(calls) == 2:  # cancel arrives during the second chunk
                cancel.request_cancel("vid1")
            return '{"clips": []}'

    # Long enough to chunk: chunk_seconds is 1200 by default.
    segments = [Segment(start=float(i * 30), end=float(i * 30 + 25), text=f"line {i}.")
                for i in range(300)]

    cancel.set_active("vid1")
    with pytest.raises(cancel.CancelledError):
        highlights.find_highlights(segments, SlowBackend(), chunk_seconds=600.0,
                                   long_video_threshold_seconds=100.0)

    assert len(calls) < 10, f"kept calling the model after cancel: {len(calls)}"
    cancel.clear("vid1")


def test_a_stuck_prefetch_does_not_wedge_the_job_queue():
    """One hung download must not stop the app processing anything, ever.

    wait_for used to be a bare join(). The single worker thread called it,
    a stalled Twitch fetch never returned, and the worker blocked there
    permanently — five jobs sat 'queued' with nothing in the UI to say why.
    A prefetch is best-effort, so it gets abandoned rather than waited on.
    """
    import threading

    # Imported as a module, not `from core.prefetch import ...`: the timeout
    # below is patched on the module object, and a name imported here would go
    # on holding the original value.
    import core.prefetch as pf

    p = pf.Prefetcher(Path("nonexistent.db"), Path("nonexistent"))
    never_finishes = threading.Event()
    t = threading.Thread(target=never_finishes.wait, daemon=True)
    t.start()
    try:
        p._thread, p._video_id = t, "vid1"
        original = pf._PREFETCH_JOIN_TIMEOUT
        pf._PREFETCH_JOIN_TIMEOUT = 0.2
        try:
            start = time.monotonic()
            p.wait_for("vid1")                 # must return despite the hang
            assert time.monotonic() - start < 5

            # And it must not block on the same dead thread a second time.
            start = time.monotonic()
            p.wait_for("vid1")
            assert time.monotonic() - start < 1
        finally:
            pf._PREFETCH_JOIN_TIMEOUT = original
    finally:
        never_finishes.set()
        t.join(timeout=5)


def test_waiting_for_a_different_video_returns_at_once():
    import threading

    # Module form, matching the test above — the other one patches a value on
    # the module, and mixing the two import styles for one module in a single
    # file is the kind of thing that quietly diverges later.
    import core.prefetch as pf

    p = pf.Prefetcher(Path("nonexistent.db"), Path("nonexistent"))
    never_finishes = threading.Event()
    t = threading.Thread(target=never_finishes.wait, daemon=True)
    t.start()
    try:
        p._thread, p._video_id = t, "vid1"
        start = time.monotonic()
        p.wait_for("vid2")          # not our video: nothing to wait for
        assert time.monotonic() - start < 1
    finally:
        never_finishes.set()
        t.join(timeout=5)
