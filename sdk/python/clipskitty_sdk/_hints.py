# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Private: how the SDK's hints name the command to type.

A module of its own so that job.py can use it without importing devrun,
which imports host, which imports job. Standard library only.
"""

from __future__ import annotations

import os
import shutil
import sys


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def python_command() -> str:
    """The command that runs the SDK with the Python running now.

    On Windows that is `py -m clipskitty_sdk` unless `python` on PATH is this
    very interpreter: `python` there is often another Python, or the Store's
    shortcut, so a developer who installed the SDK with `py -m pip` would
    get "No module named clipskitty_sdk". Elsewhere it is
    `python -m clipskitty_sdk`."""
    if sys.platform == "win32":
        found = shutil.which("python")
        if not found or not sys.executable or not _same_file(found, sys.executable):
            return "py -m clipskitty_sdk"
    return "python -m clipskitty_sdk"
