"""One way to open a video with OpenCV, so the handle always gets closed.

`cv2.VideoCapture` holds an OS file handle. On Windows that handle blocks
deletion of the file, so a capture left open by an exception turns into
`PermissionError [WinError 32]` somewhere else entirely — usually in whatever
tries to clean the file up afterwards, which is nowhere near the code that
leaked it.

That is issue #74. `compute_tracking` opened a capture and released it 160
lines later with nothing in between guaranteeing it. When tracking raised, the
handle stayed open, the pipeline's scratch cleanup could not delete the file,
and the resulting error replaced the real one — so the actual failure was
never reported to anyone.

Opening captures anywhere else is prevented by a test, for the same reason
FFmpeg may not be called by bare name: the failure shows up on a user's
machine rather than in CI, and it is invisible when reading the diff.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import cv2


@contextmanager
def video_capture(path: Path | str, *, required: bool = True) -> Iterator[cv2.VideoCapture | None]:
    """Open `path`, always releasing the handle.

    required=True  -> raises RuntimeError if it will not open (rendering
                      cannot continue without frames).
    required=False -> yields None instead, for the callers that treat an
                      unreadable file as "no signal" and carry on.
    """
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            if required:
                raise RuntimeError(f"OpenCV could not open {path}")
            yield None
            return
        yield cap
    finally:
        # Unconditional: isOpened() being False still leaves an object worth
        # releasing, and this must hold on every path out, exception included.
        cap.release()
