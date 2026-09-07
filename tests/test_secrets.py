"""The credential store.

A refresh token is a long-lived key to someone's YouTube channel. It used to
live in readable JSON next to the clips; these tests are the contract that it
no longer does, and that failing to read one is always "reconnect" rather than
a crash.
"""

import json
import sys

import pytest

from core import secrets


def test_a_secret_survives_a_round_trip(tmp_path):
    secrets.save(tmp_path, "youtube_token", {"refresh_token": "abc", "scopes": ["x"]})
    assert secrets.load(tmp_path, "youtube_token") == {"refresh_token": "abc", "scopes": ["x"]}


def test_the_value_is_not_sitting_in_the_file_in_cleartext(tmp_path):
    """On Windows this is DPAPI doing its job. Elsewhere this test documents
    that the fallback genuinely is plaintext, so nobody assumes otherwise."""
    secrets.save(tmp_path, "youtube_token", {"refresh_token": "sentinel-value"})
    blob = next((tmp_path / "credentials").iterdir()).read_bytes()
    if secrets.backend_name() == "windows-dpapi":
        assert b"sentinel-value" not in blob
    else:
        assert b"sentinel-value" in blob, "the file backend does not claim to encrypt"


def test_a_missing_secret_is_none_not_an_error(tmp_path):
    assert secrets.load(tmp_path, "never-saved") is None
    assert not secrets.has(tmp_path, "never-saved")


def test_a_corrupt_secret_reads_as_missing(tmp_path):
    """Restoring data/ onto a different Windows account leaves an
    undecryptable blob. The right answer is 'reconnect', not a stack trace on
    startup."""
    secrets.save(tmp_path, "youtube_token", {"refresh_token": "abc"})
    target = next((tmp_path / "credentials").iterdir())
    target.write_bytes(b"not a valid blob at all")
    assert secrets.load(tmp_path, "youtube_token") is None


def test_wiping_removes_it(tmp_path):
    secrets.save(tmp_path, "youtube_token", {"refresh_token": "abc"})
    secrets.wipe(tmp_path, "youtube_token")
    assert secrets.load(tmp_path, "youtube_token") is None
    assert not secrets.has(tmp_path, "youtube_token")


def test_wiping_something_that_was_never_there_is_fine(tmp_path):
    secrets.wipe(tmp_path, "nothing")


def test_saving_twice_replaces_rather_than_appends(tmp_path):
    secrets.save(tmp_path, "youtube_token", {"v": 1})
    secrets.save(tmp_path, "youtube_token", {"v": 2})
    assert secrets.load(tmp_path, "youtube_token") == {"v": 2}


def test_the_stored_file_never_matches_the_ci_secret_grep(tmp_path):
    """CI fails the build if a tracked file matches 'client_secret' or
    'token.*\\.json$'. data/ is gitignored, but a name that cannot trip the
    guard is one less trap."""
    secrets.save(tmp_path, "youtube_token", {"refresh_token": "abc"})
    secrets.save(tmp_path, "youtube_client", {"client_id": "abc"})
    for path in (tmp_path / "credentials").iterdir():
        assert not path.name.endswith("token.json")
        assert "client_secret" not in path.name


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_the_file_backend_is_owner_only(tmp_path):
    secrets.save(tmp_path, "youtube_token", {"refresh_token": "abc"})
    target = next((tmp_path / "credentials").iterdir())
    assert oct(target.stat().st_mode)[-3:] == "600"


# ---- migrating the old plaintext file --------------------------------------


def test_an_existing_plaintext_token_is_moved_in_and_deleted(tmp_path):
    legacy = tmp_path / "youtube_token.json"
    legacy.write_text(json.dumps({"refresh_token": "old"}), encoding="utf-8")

    assert secrets.migrate_plaintext(tmp_path, "youtube_token", legacy) is True
    assert secrets.load(tmp_path, "youtube_token") == {"refresh_token": "old"}
    assert not legacy.exists(), "leaving the readable copy behind defeats the point"


def test_migration_does_not_clobber_a_secret_already_in_the_store(tmp_path):
    secrets.save(tmp_path, "youtube_token", {"refresh_token": "current"})
    legacy = tmp_path / "youtube_token.json"
    legacy.write_text(json.dumps({"refresh_token": "stale"}), encoding="utf-8")

    assert secrets.migrate_plaintext(tmp_path, "youtube_token", legacy) is False
    assert secrets.load(tmp_path, "youtube_token") == {"refresh_token": "current"}


def test_migration_with_nothing_to_migrate(tmp_path):
    assert secrets.migrate_plaintext(tmp_path, "youtube_token", tmp_path / "absent.json") is False


def test_a_junk_legacy_file_is_left_alone(tmp_path):
    legacy = tmp_path / "youtube_token.json"
    legacy.write_text("{{{not json", encoding="utf-8")
    assert secrets.migrate_plaintext(tmp_path, "youtube_token", legacy) is False
    assert legacy.exists(), "we did not understand it, so we must not delete it"
