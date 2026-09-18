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

            def json(self_inner):
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


def test_uploading_is_never_offered_to_the_model():
    # The single most important line in this module: a model that cannot see
    # the tool cannot call it, however it is asked.
    names = [spec["function"]["name"] for spec in agent.tool_specs(_tools())]
    assert "publish_plan_execute" not in names
    assert "publish_plan" in names and "list_videos" in names


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


def test_asking_to_execute_is_refused_even_if_the_model_tries(monkeypatch):
    out, calls, _ = _run(monkeypatch, [
        {"role": "assistant",
         "tool_calls": [{"function": {"name": "publish_plan_execute", "arguments": {}}}]},
        {"role": "assistant", "content": "I cannot do that"},
    ])
    assert calls == [], "the handler must never run"
    assert "person confirms" in out["steps"][0]["result"]


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
