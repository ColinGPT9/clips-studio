"""Authenticated watch metadata and server-owned plugin account bindings."""

from types import SimpleNamespace

import pytest

from server.connected_feed import ConnectedFeed, source_identity
from sources.channel_feed import NewSourceVideo, Readiness

ACCOUNT = {
    "id": "twitch:42", "provider": "twitch", "user_id": "42", "login": "streamer",
    "display_name": "Streamer", "status": "connected",
}


def test_source_identity_changes_when_the_calibrated_local_media_changes(tmp_path):
    source = tmp_path / "owned-original.mp4"
    source.write_bytes(b"original media bytes")
    identity = source_identity(source)
    assert identity.startswith("sha256:") and identity == source_identity(source)
    source.write_bytes(b"trimmed or replaced media bytes")
    assert source_identity(source) != identity
    before_move = source_identity(source)
    renamed = source.rename(tmp_path / "moved.mp4")
    assert source_identity(renamed) != before_move
    assert source_identity(source) is None and source_identity(None) is None


class Service:
    def __init__(self, accounts=None, result=None, error=None):
        self.roster = [dict(ACCOUNT)] if accounts is None else accounts
        self.result = result if result is not None else {"videos": []}
        self.error = error
        self.calls = []

    def accounts(self, provider):
        assert provider == "twitch"
        return self.roster

    def perform(self, account_id, operation, **params):
        self.calls.append((account_id, operation, params))
        if self.error:
            raise self.error
        return self.result


class Fallback:
    def __init__(self):
        self.calls = []

    def resolve(self, *args, **kwargs):
        self.calls.append(("resolve", args, kwargs))
        return "legacy channel"

    def latest(self, *args, **kwargs):
        self.calls.append(("latest", args, kwargs))
        return [NewSourceVideo("kick", "other", "kick_123", "https://kick.com/other/videos/123")]

    def readiness(self, *args, **kwargs):
        self.calls.append(("readiness", args, kwargs))
        return Readiness("not_yet", "Still live")


@pytest.mark.parametrize("name", ["streamer", "@Streamer", "https://www.twitch.tv/streamer/",
                                 "https://twitch.tv/STREAMER"])
def test_known_identity_resolves_without_extracting_a_listing(name):
    fallback = Fallback()
    service = Service()
    row = ConnectedFeed(service, fallback=fallback).resolve("twitch", name)
    assert (row.platform, row.channel_key, row.name) == ("twitch", "streamer", "Streamer")
    assert not fallback.calls and not service.calls


@pytest.mark.parametrize("name", ["other", "https://twitch.tv.attacker.test/streamer",
                                 "https://attacker@www.twitch.tv/streamer",
                                 "https://www.twitch.tv:443/streamer", "https://www.twitch.tv/streamer/videos"])
def test_other_input_stays_on_the_existing_channel_resolver(name):
    fallback = Fallback()
    assert ConnectedFeed(Service(), fallback=fallback).resolve("twitch", name) == "legacy channel"
    assert fallback.calls == [("resolve", ("twitch", name), {})]


def test_authenticated_listing_uses_own_broadcaster_and_canonical_urls():
    service = Service(result={"videos": [
        {"id": "123", "title": "Old", "url": "https://attacker.test", "created_at": "2026-10-01T12:00:00Z"},
        {"id": "124", "title": "New", "created_at": "2026-10-02T12:00:00Z"},
        {"id": "123"}, {"id": "not-a-vod"}, {"id": "１２３"}, {"id": "125", "user_id": "other"},
        {"id": "126", "type": "highlight"}, None,
    ]})
    fallback = Fallback()
    found = ConnectedFeed(service, fallback=fallback).latest("twitch", "Streamer")
    assert [v.video_id for v in found] == ["tw_124", "tw_123"]
    assert found[0].url == "https://www.twitch.tv/videos/124"
    assert found[0].channel_key == "streamer" and found[0].published_at > found[1].published_at
    assert not fallback.calls
    assert service.calls == [("twitch:42", "videos", {"broadcaster_id": "42", "first": 15})]


def test_connected_listing_is_bounded():
    service = Service(result={"videos": [{"id": str(i + 1)} for i in range(30)]})
    assert len(ConnectedFeed(service).latest("twitch", "streamer")) == 15


@pytest.mark.parametrize("platform,key", [("twitch", "other"), ("youtube", "UCabc"), ("kick", "streamer")])
def test_other_watches_use_the_existing_feed(platform, key):
    fallback = Fallback()
    service = Service()
    result = ConnectedFeed(service, fallback=fallback).latest(platform, key, extract="injected")
    assert result[0].platform == "kick" and not service.calls
    assert fallback.calls == [("latest", (platform, key), {"extract": "injected"})]


@pytest.mark.parametrize("code", ["reconnect_required", "invalid_grant", "missing_scope"])
def test_a_lapsed_connection_keeps_the_watch_on_the_public_listing(code, capsys):
    class ReconnectError(Exception):
        pass

    error = ReconnectError("access_token=private-credential")
    error.code = code
    service = Service(accounts=[{**ACCOUNT, "status": "reconnect_required"}], error=error)
    fallback = Fallback()
    feed = ConnectedFeed(service, fallback=fallback)
    for _ in range(2):
        assert [v.video_id for v in feed.latest("twitch", "streamer")] == ["kick_123"]
    # Not connected: Helix is never asked, and the notice is said once.
    assert not service.calls
    assert fallback.calls == [("latest", ("twitch", "streamer"), {})] * 2
    out = capsys.readouterr().out
    assert out.count("Twitch needs reconnecting") == 1 and "private-credential" not in out


def test_a_failing_connection_falls_back_without_leaking_errors(capsys):
    class Lapsed(Exception):
        code = "invalid_token"

    for service in (Service(error=OSError("private-token")), Service(result={"private-token": "bad"}),
                    Service(error=Lapsed("access_token=private-token"))):
        fallback = Fallback()
        assert [v.video_id for v in ConnectedFeed(service, fallback=fallback).latest("twitch", "streamer")] == [
            "kick_123"]
        assert len(service.calls) == 1 and len(fallback.calls) == 1
    assert "private-token" not in capsys.readouterr().out


def test_a_good_answer_rearms_the_fallback_notice(capsys):
    service = Service(error=OSError("offline"))
    feed = ConnectedFeed(service, fallback=Fallback())
    feed.latest("twitch", "streamer")
    service.error = None
    assert feed.latest("twitch", "streamer") == []
    service.error = OSError("offline")
    feed.latest("twitch", "streamer")
    assert capsys.readouterr().out.count("Couldn't read the connected Twitch channel") == 2


def test_readiness_preserves_existing_live_and_processing_checks():
    fallback = Fallback()
    status = ConnectedFeed(Service(), fallback=fallback).readiness("https://www.twitch.tv/videos/123", 60)
    assert status.state == "not_yet" and status.reason == "Still live"
    assert fallback.calls == [("readiness", ("https://www.twitch.tv/videos/123", 60), {})]


def test_binding_requires_provider_verified_vod_ownership():
    service = Service(result={"video_id": "123", "broadcaster_id": "42", "url": "https://www.twitch.tv/videos/123?t=0s"})
    feed = ConnectedFeed(service)
    assert feed.binding_for_video(SimpleNamespace(video_id="tw_123", channel="anything")) == {
        "account_id": "twitch:42", "broadcaster_id": "42", "video_id": "123",
    }
    assert service.calls == [("twitch:42", "vod_link", {"video_id": "123", "seconds": 0})]


@pytest.mark.parametrize("file_seconds,whole", [(3600.4, True), (3598.5, True), (1800.0, False), (None, False)])
def test_only_a_file_measured_as_the_whole_archive_is_marked_whole(tmp_path, file_seconds, whole):
    media = tmp_path / "tw_123.mp4"
    media.write_bytes(b"downloaded archive bytes")
    service = Service(result={"video_id": "123", "broadcaster_id": "42", "duration_seconds": 3600})
    measured = []

    def measure(path):
        measured.append(path)
        return file_seconds

    binding = ConnectedFeed(service, file_seconds=measure).binding_for_video(
        SimpleNamespace(video_id="tw_123", path=media, duration=3600.0))
    assert measured == [media]
    assert binding["source_id"].startswith("sha256:")
    if whole:
        assert binding["whole_vod"] is True and binding["vod_duration"] == 3600.0
    else:
        assert "whole_vod" not in binding and "vod_duration" not in binding


@pytest.mark.parametrize("result", [
    {"video_id": "123", "broadcaster_id": "other"}, {"video_id": "other", "broadcaster_id": "42"},
    {"video_id": "123"}, None,
])
def test_same_display_name_cannot_grant_a_different_broadcaster_access(result):
    service = Service(result=result)
    assert ConnectedFeed(service).binding_for_video(SimpleNamespace(video_id="tw_123", channel="Streamer")) is None


@pytest.mark.parametrize("video_id", ["youtube123", "tw_bad", "tw_123/456", "", "tw_１２３"])
def test_other_media_has_no_account_binding(video_id):
    service = Service()
    assert ConnectedFeed(service).binding_for_video(SimpleNamespace(video_id=video_id)) is None
    assert not service.calls


def test_binding_can_select_the_verified_owner_among_multiple_connections():
    class Multiple(Service):
        def perform(self, account_id, operation, **params):
            self.calls.append((account_id, operation, params))
            if account_id == "twitch:41":
                raise ValueError("Not this connected account's VOD")
            return {"video_id": "123", "broadcaster_id": "42"}

    service = Multiple(accounts=[{**ACCOUNT, "id": "twitch:41", "user_id": "41"}, dict(ACCOUNT)])
    assert ConnectedFeed(service).binding_for_video(SimpleNamespace(video_id="tw_123"))["account_id"] == "twitch:42"
    assert len(service.calls) == 2
