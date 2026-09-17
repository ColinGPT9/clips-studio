"""The MCP server speaks the protocol correctly and reports the traps.

Everything here runs against a fake engine, so no API, no network and no
heavy import: these run in CI, where numpy and torch do not exist.
"""

import io
import json
import urllib.error

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
