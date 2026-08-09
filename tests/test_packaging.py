"""What the frozen build must and must not contain.

These read clips-studio.spec as text rather than building anything: a real
build is two and a half hours, so the mistakes worth catching here are the
ones that only surface in an installed copy, hours later, on someone else's
machine.

Both guards below come from bugs that shipped. A development machine has every
Python package lying around, so excluding one from the bundle changes nothing
locally and breaks the release.
"""

import re
from pathlib import Path

SPEC = Path(__file__).resolve().parent.parent / "clips-studio.spec"


def _excludes() -> list[str]:
    text = SPEC.read_text(encoding="utf-8")
    block = re.search(r"^excludes = \[(.*?)^\]", text, re.S | re.M)
    assert block, "clips-studio.spec no longer has an excludes list"
    return re.findall(r'"([^"]+)"', block.group(1))


def test_matplotlib_is_not_excluded():
    """Ultralytics imports matplotlib on the path that loads the YOLO model,
    which video/tracker.py uses and the reactions stage goes through.

    Excluding it produced "No module named 'matplotlib'" on every clip job in
    v0.1.0, while working perfectly in a checkout. If a bundle-size cull ever
    puts it back, this fails instead of the release.
    """
    assert "matplotlib" not in _excludes(), (
        "matplotlib must ship: ultralytics needs it to load a model, so "
        "excluding it breaks every clip job in an installed copy while a "
        "development machine carries on fine"
    )


def test_the_spec_still_bundles_what_the_app_cannot_fetch():
    """FFmpeg, the Ollama runtime and the Whisper weights are the difference
    between a one-click install and a scavenger hunt. Losing a datas block is
    silent until someone installs the result."""
    text = SPEC.read_text(encoding="utf-8")
    for folder in ("ffmpeg", "ollama", "whisper"):
        assert f'"vendor" / "{folder}"' in text or f"vendor_{folder}" in text, (
            f"the spec no longer bundles vendor/{folder}"
        )
