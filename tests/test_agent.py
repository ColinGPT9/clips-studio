"""The assistant loop: what it may do, and what it must never do.

No Ollama and no network here. The loop takes the tool caller as an argument
precisely so it can be tested with a fake one, which also means these run on a
CI box with none of the pipeline's dependencies.
"""

from server import agent


def _tools():
    return [
        {"name": "list_videos", "description": "List them", "inputSchema": {"type": "object"},
         "handler": lambda a: "a video"},
        {"name": "publish_plan", "description": "Plan it", "inputSchema": {"type": "object"},
         "handler": lambda a: "a plan"},
        {"name": "publish_plan_execute", "description": "Upload", "inputSchema": {"type": "object"},
         "handler": lambda a: "uploaded"},
        {"name": "uploadpost_status", "description": "Ready?", "inputSchema": {"type": "object"},
         "handler": lambda a: "connected"},
        {"name": "uploadpost_publish", "description": "Post everywhere",
         "inputSchema": {"type": "object"}, "handler": lambda a: "posted"},
    ]


class _Ollama:
    """A fake Ollama that replies with whatever script it is given."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def post(self, url, json=None, timeout=None):
        self.seen.append(json)
        reply = self.replies.pop(0)

        class Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"message": reply}

        return Response()


def _run(monkeypatch, replies, message="do it"):
    fake = _Ollama(replies)
    monkeypatch.setitem(__import__("sys").modules, "requests", fake)
    calls = []

    def call_tool(name, args):
        calls.append(name)
        return f"result of {name}", ({"items": [{"clip_id": 1}]} if name == "publish_plan" else None)

    out = agent.run(message, [], _tools(), call_tool, "http://x", "gemma4:e4b")
    return out, calls, fake


def test_publishing_is_offered_to_the_model():
    # This assertion used to say the opposite. Withholding the publish tools
    # meant "process this video and publish them all" got the processing and
    # silence about the rest, which is worse than either doing it or saying
    # no. Colin's call, 2026-09-22: it should do what it is told.
    names = [spec["function"]["name"] for spec in agent.tool_specs(_tools())]
    assert "publish_plan_execute" in names
    assert "uploadpost_publish" in names
    # Still offered, and still what the model should reach for first.
    assert "uploadpost_status" in names
    assert "publish_plan" in names and "list_videos" in names


def test_nothing_is_withheld_from_the_model():
    # The gate is empty on purpose. If something is ever put back, it needs a
    # reason written next to it, and this test says so out loud.
    assert agent.HUMAN_ONLY == set()


def test_a_plain_answer_ends_the_loop(monkeypatch):
    out, calls, _ = _run(monkeypatch, [{"role": "assistant", "content": "Here you go"}])
    assert out["reply"] == "Here you go"
    assert calls == []


def test_a_tool_call_runs_and_the_answer_follows(monkeypatch):
    out, calls, _ = _run(monkeypatch, [
        {"role": "assistant", "tool_calls": [{"function": {"name": "list_videos", "arguments": {}}}]},
        {"role": "assistant", "content": "There is one video"},
    ])
    assert calls == ["list_videos"]
    assert out["reply"] == "There is one video"
    assert out["steps"][0]["tool"] == "list_videos"


def test_a_plan_is_handed_back_for_a_human_to_confirm(monkeypatch):
    out, _, _ = _run(monkeypatch, [
        {"role": "assistant", "tool_calls": [{"function": {"name": "publish_plan", "arguments": {}}}]},
        {"role": "assistant", "content": "Here is the plan"},
    ])
    assert out["plan"] == {"items": [{"clip_id": 1}]}


def test_executing_a_publish_now_runs(monkeypatch):
    out, calls, _ = _run(monkeypatch, [
        {"role": "assistant",
         "tool_calls": [{"function": {"name": "publish_plan_execute", "arguments": {}}}]},
        {"role": "assistant", "content": "Published"},
    ])
    assert calls == ["publish_plan_execute"], "the handler must run now"
    assert out["reply"] == "Published"


def test_multi_platform_publishing_now_runs(monkeypatch):
    out, calls, _ = _run(monkeypatch, [
        {"role": "assistant",
         "tool_calls": [{"function": {"name": "uploadpost_publish",
                                      "arguments": {"clip_id": 1, "platforms": ["youtube"]}}}]},
        {"role": "assistant", "content": "Published"},
    ])
    assert calls == ["uploadpost_publish"]


def test_string_arguments_are_parsed(monkeypatch):
    # Some models hand back the arguments as a JSON string rather than an object.
    out, calls, _ = _run(monkeypatch, [
        {"role": "assistant",
         "tool_calls": [{"function": {"name": "list_videos", "arguments": '{"a": 1}'}}]},
        {"role": "assistant", "content": "done"},
    ])
    assert calls == ["list_videos"]
    assert out["steps"][0]["arguments"] == {"a": 1}


def test_a_model_that_loops_forever_is_stopped(monkeypatch):
    forever = [
        {"role": "assistant", "tool_calls": [{"function": {"name": "list_videos", "arguments": {}}}]}
    ] * (agent.MAX_TURNS + 2)
    out, calls, _ = _run(monkeypatch, forever)
    assert len(calls) == agent.MAX_TURNS
    assert "one thing at a time" in out["reply"]


def test_the_model_is_told_what_time_it_is(monkeypatch):
    _, _, fake = _run(monkeypatch, [{"role": "assistant", "content": "ok"}])
    system = fake.seen[0]["messages"][0]["content"]
    assert "The time right now is" in system
    # Without this the model invented a date in the past and the schedule was
    # refused.
    assert "RFC 3339" in system


def test_scheduling_a_batch_is_allowed(monkeypatch):
    """Turning a video into a month of posts is exactly the sort of thing to
    ask for in a sentence, so the model can now do it when asked."""
    tools = [
        *_tools(),
        {"name": "schedule_clips_plan", "description": "Plan", "inputSchema": {"type": "object"},
         "handler": lambda a: "a schedule"},
        {"name": "schedule_clips_execute", "description": "Post",
         "inputSchema": {"type": "object"}, "handler": lambda a: "posted"},
    ]
    names = [spec["function"]["name"] for spec in agent.tool_specs(tools)]
    assert "schedule_clips_execute" in names
    assert "schedule_clips_plan" in names
