"""Issue #74, as tests.

Ten of thirteen clips failed with `[WinError 32] The process cannot access
the file because it is being used by another process`. The reporter had a
strong machine and had correctly ruled out antivirus.

Nothing was wrong with their PC. `compute_tracking` opened a
`cv2.VideoCapture` and released it 160 lines later with no `try/finally`, so
an exception in between leaked the handle. On Windows an open handle blocks
deletion, so the pipeline's scratch cleanup then raised — and because that
cleanup ran inside a `finally`, the PermissionError REPLACED the exception
already in flight. The real error was destroyed for every affected clip, and
the caller discards a clip on any exception, so clips already written to disk
were thrown away as well.

Two behaviours are pinned here, plus two scans so the shape cannot come back.
"""

import ast
import pathlib
import re

import pytest

from core.paths import discard

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP = {"vendor", "build", "dist", "release", "data", "site", "ui", "tests", ".git", "whop-app"}


def _sources():
    for path in ROOT.rglob("*.py"):
        if set(path.relative_to(ROOT).parts) & SKIP:
            continue
        yield path


# ---- behaviour ---------------------------------------------------------------


def test_discard_removes_a_file(tmp_path):
    f = tmp_path / "clip.source.mp4"
    f.write_bytes(b"x")
    assert discard(f) is True
    assert not f.exists()


def test_discard_is_happy_when_the_file_is_already_gone(tmp_path):
    assert discard(tmp_path / "never-existed.mp4") is True


def test_discard_swallows_a_windows_style_lock(tmp_path, monkeypatch, capsys):
    """The #74 condition: the file cannot be deleted because a handle is open.

    `unlink(missing_ok=True)` does NOT cover this — missing_ok only suppresses
    FileNotFoundError — which is exactly why the old code raised.
    """
    f = tmp_path / "clip.source.mp4"
    f.write_bytes(b"x")

    def locked(self, **kwargs):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(pathlib.Path, "unlink", locked)

    assert discard(f) is False           # reported, not raised
    assert "could not remove" in capsys.readouterr().out


def test_a_locked_scratch_file_cannot_hide_the_real_error():
    """The core of #74: cleanup in a `finally` must not replace the exception.

    Reproduces the old control flow to show what it did, then asserts the
    shape we now require — the caller sees why the render actually failed,
    not a message about a temp file.
    """
    def real_work():
        raise RuntimeError("tracking failed: no subject found")

    # What the code used to do.
    with pytest.raises(PermissionError):
        try:
            real_work()
        finally:
            raise PermissionError(32, "file is being used by another process")

    # What it must do now: cleanup reports and the real error survives.
    with pytest.raises(RuntimeError, match="tracking failed"):
        try:
            real_work()
        finally:
            discard(None)  # best-effort cleanup, never raises


def test_video_capture_releases_even_when_the_body_raises(monkeypatch):
    """The leak that started it. Release must happen on the exception path."""
    import video.capture as capture

    released = []

    class FakeCap:
        def isOpened(self):
            return True

        def release(self):
            released.append(True)

    monkeypatch.setattr(capture.cv2, "VideoCapture", lambda _p: FakeCap())

    with pytest.raises(ValueError):
        with capture.video_capture("clip.mp4"):
            raise ValueError("tracking blew up")

    assert released, "the capture handle was leaked — this is issue #74"


def test_video_capture_releases_a_file_it_could_not_open(monkeypatch):
    released = []

    class FakeCap:
        def isOpened(self):
            return False

        def release(self):
            released.append(True)

    monkeypatch.setattr(capture_module().cv2, "VideoCapture", lambda _p: FakeCap())

    with capture_module().video_capture("clip.mp4", required=False) as cap:
        assert cap is None
    assert released


def capture_module():
    import video.capture as capture

    return capture


# ---- scans, so a new file cannot reintroduce either mistake ------------------


def test_opencv_captures_all_go_through_the_helper():
    """A bare cv2.VideoCapture is a handle nobody is guaranteed to release.

    Same reasoning as the FFmpeg and YOLO scans in test_binaries.py: this
    fails on a user's machine, not in CI, and is invisible when reading a
    diff.
    """
    offenders = []
    for path in _sources():
        if path.name == "capture.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"cv2\.VideoCapture\s*\(", line) and not line.lstrip().startswith("#"):
                offenders.append(f"{path.relative_to(ROOT)}:{n}")

    assert not offenders, (
        "these open an OpenCV capture directly and can leak the handle on an "
        f"exception (issue #74); use video.capture.video_capture(): {offenders}"
    )


def test_cleanup_in_a_finally_never_calls_unlink():
    """`unlink` inside a `finally` can replace the exception being raised.

    That is precisely how #74 hid the real failure from every affected clip.
    Cleanup there must use core.paths.discard, which cannot raise.
    """
    def deletes_a_file(call: ast.Call) -> bool:
        if not isinstance(call.func, ast.Attribute):
            return False
        if call.func.attr in ("unlink", "rmdir"):
            return True
        # os.remove, but not list.remove / set.remove / sys.path.remove.
        return (
            call.func.attr == "remove"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "os"
        )

    offenders = []
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            for stmt in getattr(node, "finalbody", []):
                # A delete wrapped in its own try/except cannot escape, so it
                # is fine where it is — only unguarded ones can replace the
                # exception the `finally` is unwinding.
                guarded = {
                    ln
                    for inner in ast.walk(stmt)
                    if isinstance(inner, ast.Try) and inner.handlers
                    for body in inner.body
                    for ln in range(body.lineno, (body.end_lineno or body.lineno) + 1)
                }
                for call in ast.walk(stmt):
                    if (
                        isinstance(call, ast.Call)
                        and deletes_a_file(call)
                        and call.lineno not in guarded
                    ):
                        offenders.append(f"{path.relative_to(ROOT)}:{call.lineno}")

    assert not offenders, (
        "these delete a file inside a `finally`, so a locked file would "
        "replace the real exception (issue #74); use core.paths.discard: "
        f"{offenders}"
    )
