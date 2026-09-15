"""What the YouTube sign-in says when Google refuses it.

Google answers `access_denied` both when someone presses Cancel and when the
OAuth project is still in Testing and the account is not a listed test user.
The second is the one people actually get stuck on, and it used to be reported
as "You declined the permission request", which sends them looking in the wrong
place.
"""

from publish.oauth import _explain


def test_access_denied_explains_the_testing_block_not_just_cancel():
    message = _explain(Exception("(access_denied) Error 403: access_denied"))
    assert "Publish app" in message
    assert "Cancel" in message
    assert "declined" not in message


def test_other_failures_keep_their_own_messages():
    assert "Desktop app" in _explain(Exception("invalid_client"))
    assert "timed out" in _explain(Exception("Timeout waiting for consent"))
