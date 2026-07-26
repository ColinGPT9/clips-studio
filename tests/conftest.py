"""Shared fixtures.

The repo root goes on sys.path so tests import the engine the same way the
app does (`from creator import retrieval`), with no packaging step.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.state import StateDB, _now  # noqa: E402


@pytest.fixture
def db(tmp_path):
    """A real, empty state database in a temp directory.

    Deliberately the real StateDB rather than a mock: the schema and its
    migrations are part of what these tests are checking, and a mock that
    drifts from the real schema tests nothing.
    """
    database = StateDB(tmp_path / "state.db")
    yield database
    database.conn.close()


@pytest.fixture
def creator(db):
    """One creator row, returning its id."""
    cur = db.conn.execute(
        "INSERT INTO creators (display_name, aliases, learning_enabled, created_at)"
        " VALUES (?, '[]', 1, ?)",
        ("Test Creator", _now()),
    )
    db.conn.commit()
    return cur.lastrowid


@pytest.fixture
def segments():
    """A short transcript as the pipeline would hand it over."""
    from core.models import Segment

    return [
        Segment(start=0.0, end=2.0, text="yo what is good chat"),
        Segment(start=2.0, end=4.5, text="let's get it, we grinding today"),
        Segment(start=4.5, end=7.0, text="man I only said this once, believe me"),
        Segment(start=7.0, end=9.0, text="alright let's get it, run it back"),
    ]
