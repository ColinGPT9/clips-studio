"""Gaming / Split-Screen as a job option: how it is asked for, and what it
can't be combined with. Light imports only, so this runs in CI."""

import pytest

from core import modes


def test_the_toggle_is_read_from_a_job_config_or_a_clips_own_options():
    assert modes.is_gaming({"clips": {"gaming": True}})
    assert modes.is_gaming({"gaming": {"cam": None, "by": "video"}})    # a clip's render_opts
    assert not modes.is_gaming({"gaming": {}})
    assert not modes.is_gaming({"clips": {"podcast": True}})
    assert not modes.is_gaming(None)


@pytest.fixture
def api():
    pytest.importorskip("fastapi")
    from server import api as api_mod

    return api_mod


def test_the_toggle_reaches_the_job(api):
    assert api._process_options(api.JobIn(url="https://x/v", gaming=True)) == {"gaming": True}
    assert "gaming" not in api._process_options(api.JobIn(url="https://x/v"))
    assert api._process_options(api.LocalVideoIn(path="C:/v.mp4", gaming=True))["gaming"] is True
    assert api._process_options(api.BatchItemIn(url="https://x/v", gaming=True))["gaming"] is True


def test_it_cant_be_combined_with_another_layout_mode(api):
    from fastapi import HTTPException

    for extra in ({"vertical_live": True}, {"podcast": True}, {"longform": {"mode": "highlights"}}):
        with pytest.raises(HTTPException) as e:
            api._process_options(api.JobIn(url="https://x/v", gaming=True, **extra))
        assert e.value.status_code == 400 and "Gaming" in e.value.detail


def test_a_queued_job_can_turn_it_on_and_off(api):
    assert api._process_options(api.JobPatch(gaming=True), into={"captions": False}) == {
        "captions": False, "gaming": True}
    assert "gaming" not in api._process_options(api.JobPatch(clear=["gaming"]), into={"gaming": True})
