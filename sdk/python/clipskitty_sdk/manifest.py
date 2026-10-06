"""Reading a plugin's manifest, `clipskitty.yaml`.

The manifest is YAML, so reading it needs PyYAML (`pip install pyyaml`).
Only the developer tools (`python -m clipskitty_sdk run`) read manifests; a
plugin's own code never needs to, so the rest of the SDK stays standard
library only.
"""

from __future__ import annotations

from pathlib import Path

MANIFEST_FILE = "clipskitty.yaml"


class ManifestError(ValueError):
    """A manifest that can't be read. `errors` lists every problem found."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def load(folder: str | Path) -> dict:
    """The manifest in a plugin folder, as a mapping."""
    try:
        import yaml
    except ImportError as e:
        raise ManifestError(["reading clipskitty.yaml needs PyYAML: pip install pyyaml"]) from e
    path = Path(folder) / MANIFEST_FILE
    if not path.is_file():
        raise ManifestError([f"no {MANIFEST_FILE} in {folder}"])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ManifestError([f"{MANIFEST_FILE} is not valid YAML ({e})"]) from e
    if not isinstance(data, dict):
        raise ManifestError([f"{MANIFEST_FILE} must be a mapping of fields"])
    return data
