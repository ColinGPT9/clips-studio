"""The plugin SDK (sdk/python/clipskitty_sdk), importable by the engine.

The engine uses the SDK's own checks and runner so that what passes under
`python -m clipskitty_sdk run` passes in the app: one copy of the contract,
never two. The SDK is a plain folder of Python files rather than an installed
package, because plugin processes are pointed at the same folder through
PYTHONPATH. In a source checkout it is next to this package; in the frozen
build it ships as data (clips-studio.spec) under the bundle's sdk/python.
"""

import sys
from pathlib import Path


def sdk_dir() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "sdk" / "python"
    return Path(__file__).resolve().parent.parent / "sdk" / "python"


if str(sdk_dir()) not in sys.path:
    sys.path.insert(0, str(sdk_dir()))

import clipskitty_sdk  # noqa: E402
from clipskitty_sdk import contract, host  # noqa: E402

__all__ = ["clipskitty_sdk", "contract", "host", "sdk_dir"]
