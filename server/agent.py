"""The assistant behind the app's Gemma box.

Type "clip this stream, then upload them an hour apart with #mychannel" and a
local model works out which of the engine's own tools to call, in order, and
does it. The tools are exactly the ones server/mcp.py exposes to outside
agents: one list, one set of descriptions, one place to fix a mistake.

Three rules hold it together.

**Uploading is not something the model can do.** `publish_plan_execute` is
never offered here. The model may build a plan; turning that into uploads takes
a person pressing a button. Thirty videos cannot be un-uploaded, so the
confirmation is structural rather than a matter of the model behaving.

**It needs a model that can call tools.** Gemma 4 can; the app's default
gemma3:4b cannot, and neither can gemma:7b. Ollama reports this per model, so
the box says which model it is using and says plainly when none can, instead of
producing a confident answer having called nothing.

**Nothing here does the work itself.** Every step is a call to the local API,
the same one the window uses. The model decides what to call and in what order;
the engine still processes video, renders clips and talks to YouTube.
"""

import json

# The model gets a handful of turns to reach an answer. Enough for "find the
# video, list its clips, plan the uploads"; short enough that a model looping on
# itself stops rather than running until the user gives up.
MAX_TURNS = 8
TOOL_OUTPUT_LIMIT = 4000

# Never offered to the model.
#
# Empty on purpose, and it is a deliberate reversal. Publishing used to sit
# here because it posts publicly and cannot be taken back. In practice that
# meant asking the assistant to "process this video and publish them all" got
# you the processing and silence about the rest: the model could not do the
# second half and said nothing, which is worse than either doing it or
# refusing. Colin's call, 2026-09-22: "it should do what i told it to do."
#
# What stands in for the gate: the model calls publish_plan before it
# executes, so there is a record of the intent, and a deferred publish only
# runs when the request actually asked for one (see `then` in server/jobs.py).
HUMAN_ONLY: set[str] = set()

SYSTEM = (
    "You drive Clips Kitty, a local video clipping app, through its tools.\n"
    "NEVER ask the person for something you can look up. If they describe a "
    "video by its subject or title, call list_videos and match it yourself. If "
    "you need clip ids, call list_clips. Asking for an id you could have "
    "fetched is a failure.\n"
    "Work in steps: call a tool, read the result, call the next one. A request "
    "with two halves needs at least two calls.\n"
    "Processing a video takes tens of minutes: queue it, report the job id, and "
    "do not wait for it.\n"
    "When they say how they want the clips, pass it as arguments rather than "
    "mentioning it in your reply: captions on or off and how they look, "
    "longer clips, a watermark by name, podcast footage, or a horizontal "
    "longform video. Ignoring one silently gives them the wrong render.\n"
    "To publish, call publish_plan first and show what it returns, then "
    "execute it. Do this only when they asked you to publish.\n"
    "If they ask you to process a video AND publish it, the clips do not exist "
    "yet when you queue the job, so you cannot publish them in this reply. "
    "Pass publish_when_done to queue_video instead and say you have done so: "
    "the app publishes them the moment processing finishes.\n"
    "If they want particular hashtags on every clip, pass hashtags to "
    "queue_video. Adding them afterwards clip by clip is not the way.\n"
    "Never drop part of a request in silence. If you cannot do something they "
    "asked for, say which part and why, in the same reply.\n"
    "Keep answers short and concrete."
)


def tool_specs(tools: list[dict]) -> list[dict]:
    """The MCP tool list in the shape Ollama's chat API wants."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["inputSchema"],
            },
        }
        for tool in tools
        if tool["name"] not in HUMAN_ONLY
    ]


def can_call_tools(host: str, model: str) -> bool:
    """Whether Ollama says this model supports tool calling.

    Asked rather than assumed: the answer differs between gemma3 and gemma4,
    and a model that cannot call tools fails by inventing an answer, which is
    the worst possible failure for something that is supposed to act.
    """
    import requests

    try:
        r = requests.post(f"{host}/api/show", json={"model": model}, timeout=15)
        r.raise_for_status()
        return "tools" in (r.json().get("capabilities") or [])
    except Exception:
        return False


def usable_model(host: str, preferred: str = "") -> str:
    """A tool-capable model that is actually installed, or "".

    Prefers the one already chosen for clip scoring, so most people never think
    about it, and falls back to any installed model that can call tools.
    """
    import requests

    if preferred and can_call_tools(host, preferred):
        return preferred
    try:
        r = requests.get(f"{host}/api/tags", timeout=15)
        r.raise_for_status()
        installed = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return ""
    # Gemma 4 first, by name. It is what this feature was built around, what
    # the Models page recommends, and what the app ships the rest of its AI on:
    # reaching past it for some other installed model would be a surprise.
    ordered = sorted(installed, key=lambda n: (not n.startswith("gemma4"), n))
    for name in ordered:
        if can_call_tools(host, name):
            return name
    return ""


def run(
    message: str,
    history: list[dict],
    tools: list[dict],
    call_tool,
    host: str,
    model: str,
) -> dict:
    """One exchange: the model calls tools until it can answer.

    `call_tool(name, arguments)` runs one tool and returns its text, so the
    loop itself never touches the API and stays testable without a network.

    Returns the reply, the steps taken (for the box to show its working) and
    the most recent publishing plan, which the window turns into a Confirm
    button.
    """
    # A model has no clock. Without this, "tomorrow at noon" came back as a
    # date in 2025 and the schedule was refused as being in the past.
    from datetime import datetime

    import requests

    now = datetime.now().astimezone()
    when = (
        f"The time right now is {now.isoformat(timespec='seconds')}. "
        "Work out any 'tomorrow' or 'tonight' from that, and always pass times "
        "in RFC 3339 WITH the offset, like 2026-09-18T12:00:00-05:00."
    )
    messages = [{"role": "system", "content": SYSTEM + chr(10) + when}]
    messages += [m for m in history if m.get("role") in ("user", "assistant")]
    messages.append({"role": "user", "content": message})

    specs = tool_specs(tools)
    steps: list[dict] = []
    plan: dict | None = None

    for _ in range(MAX_TURNS):
        r = requests.post(
            f"{host}/api/chat",
            json={"model": model, "messages": messages, "tools": specs, "stream": False},
            timeout=600,
        )
        r.raise_for_status()
        reply = r.json().get("message") or {}
        messages.append(reply)

        calls = reply.get("tool_calls") or []
        if not calls:
            return {
                "reply": (reply.get("content") or "").strip(),
                "steps": steps,
                "plan": plan,
                "model": model,
            }

        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if name in HUMAN_ONLY:
                text = "Not allowed from here. The person confirms uploads in the app."
            else:
                text, structured = call_tool(name, args)
                if name == "publish_plan" and structured:
                    plan = structured
            steps.append({"tool": name, "arguments": args, "result": text[:400]})
            messages.append({"role": "tool", "content": text[:TOOL_OUTPUT_LIMIT]})

    return {
        "reply": (
            "I could not finish that in a reasonable number of steps. "
            "Try asking for one thing at a time."
        ),
        "steps": steps,
        "plan": plan,
        "model": model,
    }


def run_cloud(message: str, history: list[dict], tools: list[dict], call_tool, backend) -> dict:
    """The same exchange as run(), on a cloud model with the user's own key.

    run() is the local path and is left exactly as it is. This is its twin for
    a CloudBackend: the same system prompt, the same tools, the same rules
    about publishing, and the same answer shape, with each provider's own
    message format handled inside its adapter (backend.chat).
    """
    from datetime import datetime

    now = datetime.now().astimezone()
    when = (
        f"The time right now is {now.isoformat(timespec='seconds')}. "
        "Work out any 'tomorrow' or 'tonight' from that, and always pass times "
        "in RFC 3339 WITH the offset, like 2026-09-18T12:00:00-05:00."
    )
    messages = [{"role": "system", "content": SYSTEM + chr(10) + when}]
    messages += [{"role": m["role"], "content": str(m.get("content") or "")}
                 for m in history if m.get("role") in ("user", "assistant")]
    messages.append({"role": "user", "content": message})

    specs = tool_specs(tools)
    steps: list[dict] = []
    plan: dict | None = None

    for _ in range(MAX_TURNS):
        turn = backend.chat(messages, specs)
        messages.append({
            "role": "assistant",
            "content": turn.text,
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in turn.tool_calls],
            "raw": turn.raw,
        })
        if not turn.tool_calls:
            return {"reply": turn.text.strip(), "steps": steps, "plan": plan, "model": backend.name}

        for call in turn.tool_calls:
            args = dict(call.arguments or {})
            if call.name in HUMAN_ONLY:
                text = "Not allowed from here. The person confirms uploads in the app."
            else:
                text, structured = call_tool(call.name, args)
                if call.name == "publish_plan" and structured:
                    plan = structured
            steps.append({"tool": call.name, "arguments": args, "result": text[:400]})
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                             "content": text[:TOOL_OUTPUT_LIMIT]})

    return {
        "reply": (
            "I could not finish that in a reasonable number of steps. "
            "Try asking for one thing at a time."
        ),
        "steps": steps,
        "plan": plan,
        "model": backend.name,
    }
