"""Where this install keeps downloads, clips and the database.

Deliberately dependency-free — stdlib only. It lived in main.py, which meant
answering "where does data go?" required importing the entire pipeline:
numpy, OpenCV, PyTorch. That is a heavy question for a simple answer, and it
made the rule impossible to test without a GPU-sized environment.
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def resolve_data_dir(config: dict) -> Path:
    """The absolute directory for this install's data.

    `data_dir` ships as the relative path "data", and it used to be resolved
    against the CURRENT WORKING DIRECTORY. In a checkout that quietly means
    "wherever you happened to run python from". In an installed copy it is
    worse: Electron spawns the engine without setting a working directory, so
    a creator's videos could land inside Program Files, which is not writable
    without admin — the first download simply fails.

    An absolute path in settings.yaml is always honoured: someone pointing
    data_dir at a big second drive means it. A relative one resolves against
    a stable base instead — per-user app data for an installed build, the
    repo folder for a checkout.
    """
    raw = Path(str((config.get("paths") or {}).get("data_dir") or "data"))
    if raw.is_absolute():
        return raw

    if getattr(sys, "frozen", False):
        # Installed: per-user, writable, and survives reinstalling the app.
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "Clips Studio" / raw

    # Checkout: next to the code, not next to the terminal.
    return _REPO_ROOT / raw
