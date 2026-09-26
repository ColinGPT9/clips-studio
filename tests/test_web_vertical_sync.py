"""The web tool and the desktop app mean the same thing by Vertical Live.

web/lib/vertical.ts mirrors core/modes.py by hand (there is no shared code
between the TypeScript page and the Python engine). If the two drift, one of
them would call a video 9:16 that the other refuses, or word the refusal
differently. This reads the TypeScript and compares.
"""

import re
from pathlib import Path

from core import modes

WEB = Path(__file__).resolve().parent.parent / "web" / "lib" / "vertical.ts"


def _ts() -> str:
    return WEB.read_text(encoding="utf-8")


def _number(name: str) -> float:
    match = re.search(rf"export const {name} = ([0-9.]+);", _ts())
    assert match, f"{name} not found in {WEB.name}"
    return float(match.group(1))


def test_the_9x16_range_matches():
    assert _number("VERTICAL_MIN") == modes.VERTICAL_MIN
    assert _number("VERTICAL_MAX") == modes.VERTICAL_MAX


def test_the_target_size_matches():
    match = re.search(r"export const TARGET = \{ width: (\d+), height: (\d+) \};", _ts())
    assert match and (int(match.group(1)), int(match.group(2))) == modes.TARGET


def test_the_refusal_is_worded_the_same():
    match = re.search(r'export const MISMATCH =\s*"([^"]+)";', _ts())
    assert match and match.group(1) == modes.MISMATCH
