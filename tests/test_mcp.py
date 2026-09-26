"""The MCP server speaks the protocol correctly and reports the traps.

Everything here runs against a fake engine, so no API, no network and no
heavy import: these run in CI, where numpy and torch do not exist.
"""

import io
import json
import urllib.error

import pytest

from server import mcp


def _send(message: dict) -> dict | None:
    return mcp.handle(message)


def _request(msg_id, method, params=None):
    out = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        out["params"] = params
    return out


def test_initialize_agrees_on_the_client_version():
    reply = _send(_request(1, "initialize", {"protocolVersion": "2025-03-26"}))
    assert reply["result"]["protocolVersion"] == "2025-03-26"
    assert reply["result"]["capabilities"]["tools"] == {"listChanged": False}
    assert reply["result"]["serverInfo"]["name"] == "clips-kitty"


def test_initialize_offers_our_version_when_theirs_is_unknown():
    reply = _send(_request(1, "initialize", {"protocolVersion": "1.0.0"}))
    assert reply["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION


def test_a_notification_is_never_answered():
    assert _send({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_is_well_formed():
    tools = _send(_request(2, "tools/list"))["result"]["tools"]
    assert {t["name"] for t in tools} >= {"queue_video", "list_clips", "export_clip"}
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert "handler" not in tool  # the callable must never reach the client
        assert tool["description"]


def test_unknown_tool_is_a_protocol_error():
    reply = _send(_request(3, "tools/call", {"name": "nope", "arguments": {}}))
    assert reply["error"]["code"] == mcp.INVALID_PARAMS


def test_unknown_method_is_a_protocol_error():
    assert _send(_request(4, "nonsense"))["error"]["code"] == mcp.METHOD_NOT_FOUND


def test_queueing_reports_a_job_id(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {"job_id": 149})
    reply = _send(_request(5, "tools/call", {
        "name": "queue_video", "arguments": {"url": "https://youtu.be/x"}}))
    assert reply["result"]["isError"] is False
    assert "149" in reply["result"]["content"][0]["text"]


def test_an_already_processed_video_explains_itself(monkeypatch):
    # job_id null is not a failure, and an agent that assumes otherwise will
    # tell the user their video was queued when it was not.
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {
        "job_id": None, "already_processed": True, "video_id": "abc"})
    reply = _send(_request(6, "tools/call", {
        "name": "queue_video", "arguments": {"url": "https://youtu.be/x"}}))
    text = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is False
    assert "Not queued" in text and "force" in text


def test_no_clips_is_not_confused_with_a_wrong_id(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: [])
    reply = _send(_request(7, "tools/call", {
        "name": "list_clips", "arguments": {"video_id": "typo"}}))
    assert "list_videos" in reply["result"]["content"][0]["text"]


def test_a_stopped_engine_is_a_tool_error_not_a_crash(monkeypatch):
    def refuse(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(mcp, "_request", refuse)
    reply = _send(_request(8, "tools/call", {"name": "queue_status", "arguments": {}}))
    assert reply["result"]["isError"] is True
    assert "not answering" in reply["result"]["content"][0]["text"]


def test_the_publishing_tools_are_offered():
    tools = _send(_request(20, "tools/list"))["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"youtube_status", "publish_plan", "publish_plan_execute", "publish_status"} <= names


def test_a_plan_says_plainly_that_nothing_was_uploaded(monkeypatch):
    # An agent reads only this text, so it has to carry the fact that a plan is
    # a proposal and the confirmation step is not optional.
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {
        "items": [{"clip_id": 1, "title": "One", "publish_at": None},
                  {"clip_id": 2, "title": "Two", "publish_at": "2026-09-18T17:00:00Z"}],
        "warnings": [],
    })
    reply = _send(_request(21, "tools/call", {
        "name": "publish_plan", "arguments": {"clip_ids": [1, 2]}}))
    text = reply["result"]["content"][0]["text"]
    assert "NOTHING has been uploaded" in text
    assert "publish_plan_execute" in text
    assert "clip 1" in text and "clip 2" in text


def test_a_plan_passes_on_the_quota_warning(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {
        "items": [{"clip_id": 1, "title": "One", "publish_at": None}],
        "warnings": ["3 clips, but only 1 uploads left on today's quota."],
    })
    reply = _send(_request(22, "tools/call", {
        "name": "publish_plan", "arguments": {"clip_ids": [1]}}))
    assert "quota" in reply["result"]["content"][0]["text"]


def test_executing_reports_what_was_skipped(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {
        "started": [{"clip_id": 1, "publish_job_id": 9}],
        "skipped": [{"clip_id": 2, "reason": "This clip has no rendered file yet."}],
    })
    reply = _send(_request(23, "tools/call", {
        "name": "publish_plan_execute", "arguments": {"items": [{"clip_id": 1}, {"clip_id": 2}]}}))
    text = reply["result"]["content"][0]["text"]
    assert "Started 1 upload" in text
    assert "clip 2 skipped" in text


def test_executing_nothing_uploads_nothing(monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("must not call the API with an empty plan")

    monkeypatch.setattr(mcp, "_request", explode)
    reply = _send(_request(24, "tools/call", {
        "name": "publish_plan_execute", "arguments": {"items": []}}))
    assert "nothing was published" in reply["result"]["content"][0]["text"]


def test_youtube_off_tells_the_agent_to_stop_rather_than_improvise(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {"enabled": False})
    reply = _send(_request(25, "tools/call", {"name": "youtube_status", "arguments": {}}))
    text = reply["result"]["content"][0]["text"]
    assert "switched off" in text
    assert "not something to work around" in text


def test_serve_reads_lines_and_writes_one_message_per_line():
    stdin = io.StringIO(
        json.dumps(_request(1, "initialize", {"protocolVersion": "2025-06-18"})) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps(_request(2, "tools/list")) + "\n"
    )
    stdout = io.StringIO()
    assert mcp.serve(stdin, stdout) == 0
    lines = [ln for ln in stdout.getvalue().split("\n") if ln]
    assert len(lines) == 2  # the notification is not answered
    assert json.loads(lines[0])["id"] == 1
    assert json.loads(lines[1])["result"]["tools"]


def test_bad_json_gets_a_parse_error():
    stdout = io.StringIO()
    mcp.serve(io.StringIO("{not json\n"), stdout)
    assert json.loads(stdout.getvalue())["error"]["code"] == mcp.PARSE_ERROR


def test_the_api_base_can_be_overridden(monkeypatch):
    monkeypatch.setenv("CLIPS_STUDIO_API", "http://127.0.0.1:9999/")
    assert mcp.api_base() == "http://127.0.0.1:9999"


def test_the_box_can_set_the_things_the_panel_has_checkboxes_for(monkeypatch):
    sent = {}
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: sent.update({"body": b})
                        or {"job_id": 7})
    mcp.handle(_request(30, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x", "captions": False, "long_clips": True,
        "podcast": True}}))
    assert sent["body"]["captions"] is False
    assert sent["body"]["long_clips"] is True
    assert sent["body"]["podcast"] is True


def test_captions_left_alone_are_not_sent_at_all(monkeypatch):
    # The default lives in settings; sending None would override it with nothing.
    sent = {}
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: sent.update({"body": b})
                        or {"job_id": 7})
    mcp.handle(_request(31, "tools/call", {
        "name": "queue_video", "arguments": {"url": "https://youtu.be/x"}}))
    assert "captions" not in sent["body"]


def test_longform_becomes_a_mode(monkeypatch):
    sent = {}
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: sent.update({"body": b})
                        or {"job_id": 7})
    mcp.handle(_request(32, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x", "longform": "highlights"}}))
    assert sent["body"]["longform"] == {"mode": "highlights"}


def test_a_watermark_is_looked_up_by_name(monkeypatch):
    calls = []

    def fake(method, path, body=None):
        calls.append(path)
        if path == "/branding":
            return [{"id": 3, "name": "Main channel"}, {"id": 4, "name": "Alt"}]
        return {"job_id": 7, "_body": body}

    monkeypatch.setattr(mcp, "_request", fake)
    mcp.handle(_request(33, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x", "watermark": "main channel"}}))
    assert "/branding" in calls


def test_a_watermark_that_does_not_exist_lists_the_real_ones(monkeypatch):
    # Guessing an id here would brand a whole stream with the wrong logo.
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: (
        [{"id": 3, "name": "Main channel"}] if p == "/branding" else {"job_id": 7}))
    reply = mcp.handle(_request(34, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x", "watermark": "nope"}}))
    text = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is True
    assert "Main channel" in text


def test_every_longform_mode_in_the_dropdown_can_be_asked_for():
    # The panel offers four; the box offered three, so "clips up to 140s for X"
    # quietly did something else.
    tools = {t["name"]: t for t in mcp.TOOLS}
    modes = tools["queue_video"]["inputSchema"]["properties"]["longform"]["enum"]
    assert modes == ["short_clips", "clips_140", "highlights", "edited_stream"]


def test_a_caption_font_is_matched_however_it_was_typed():
    assert mcp._caption_style({"font": "impact"})["font"] == "Impact"
    assert mcp._caption_style({"font": "comic sans"})["font"] == "Comic Sans MS"


def test_a_font_that_is_not_installed_is_refused_with_the_list():
    # An unknown font renders as something else without complaining, so every
    # clip of the stream would be wrong and nothing would say why.
    with pytest.raises(ValueError, match="Impact"):
        mcp._caption_style({"font": "Papyrus"})


def test_colour_words_work_as_well_as_hex():
    assert mcp._caption_style({"color": "yellow"})["color"] == "#FFE600"
    assert mcp._caption_style({"color": "#ff0000"})["color"] == "#FF0000"
    with pytest.raises(ValueError, match="not a colour"):
        mcp._caption_style({"highlight_color": "chartreuse"})


def test_centre_means_middle():
    assert mcp._caption_style({"position": "centre"})["position"] == "middle"
    assert mcp._caption_style({"position": "Center"})["position"] == "middle"
    with pytest.raises(ValueError, match="bottom, middle, top"):
        mcp._caption_style({"position": "diagonal"})


def test_sizes_are_clamped_rather_than_refused():
    # "make them huge" is a real request; it should land at the top of the
    # range instead of failing.
    assert mcp._caption_style({"font_size": 900})["font_size"] == 160
    assert mcp._caption_style({"words_per_caption": 99})["words_per_caption"] == 6


def test_what_was_not_asked_for_is_left_alone():
    # Anything sent here overrides the user's saved style, so silence matters.
    assert mcp._caption_style({"uppercase": False}) == {"uppercase": False}


def test_a_caption_request_reaches_the_job(monkeypatch):
    sent = {}
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: sent.update({"body": b})
                        or {"job_id": 7})
    mcp.handle(_request(35, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x",
        "caption_style": {"position": "top", "highlight": True, "highlight_color": "gold"}}}))
    assert sent["body"]["caption_style"] == {
        "position": "top", "highlight": True, "highlight_color": "#FFD700"}


def test_vertical_live_and_an_original_link_reach_the_engine(monkeypatch):
    sent = []
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: sent.append((p, b)) or {"job_id": 7})
    mcp.handle(_request(31, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x", "vertical_live": True}}))
    mcp.handle(_request(32, "tools/call", {"name": "queue_local_file", "arguments": {
        "path": "C:/lives/live.mp4", "vertical_live": True,
        "source_url": "https://www.tiktok.com/@someone/live"}}))
    assert sent[0] == ("/jobs", {"url": "https://youtu.be/x", "vertical_live": True})
    assert sent[1] == ("/videos/local", {"path": "C:/lives/live.mp4", "vertical_live": True,
                                         "source_url": "https://www.tiktok.com/@someone/live"})
