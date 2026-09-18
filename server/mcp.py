"""An MCP server for Clips Kitty, spoken over stdio.

Lets Claude, ChatGPT, Cursor or any MCP client drive the engine in plain
language: queue a stream, watch the job, read back the clips it chose, export
one. It is a translation layer over the local HTTP API documented in
docs/API.md, which is the same API the desktop app and the OBS plugin use.

Two deliberate choices.

**No dependency.** MCP's stdio transport is newline-delimited JSON-RPC, which
the standard library already does. requirements.txt is a pinned list where
every line has a reason, and an SDK would also mean a new hidden import in
clips-studio.spec and a re-frozen backend, all for about 150 lines of
protocol. So this imports nothing that is not in Python.

**No second engine.** Every tool asks the running engine over
127.0.0.1:8765 rather than importing the pipeline. An agent that imported it
would start renders in a process with no queue, no database discipline and no
window showing the user what is happening.

Run it:

    python main.py mcp                 # from a source checkout
    api.exe mcp                        # inside an installed build

The engine has to be running: open Clips Kitty, or `python main.py serve`.
"""

import json
import os
import sys
import urllib.error
import urllib.request

# The version this server prefers. A client asking for an older one gets that
# version back if we know it, per the spec's version negotiation.
PROTOCOL_VERSION = "2025-06-18"
KNOWN_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

# JSON-RPC error codes (the ones this server can produce).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

NOT_RUNNING = (
    "Clips Kitty is not answering on {base}. Open the app, or start the engine "
    "on its own with: python main.py serve"
)


def api_base() -> str:
    """Where the engine is listening. `CLIPS_STUDIO_API` overrides it, matching
    the other CLIPS_STUDIO_* overrides in core/binaries.py."""
    return (os.environ.get("CLIPS_STUDIO_API") or "http://127.0.0.1:8765").rstrip("/")


def _app_version() -> str:
    try:
        from server.feedback import _app_version as version_of_app

        return str(version_of_app().get("app", "unknown"))
    except Exception:
        return "unknown"


def _request(method: str, path: str, body: dict | None = None, timeout: float = 60.0):
    """One call to the local API. Raises urllib errors; callers turn those into
    tool errors rather than protocol errors, because a stopped engine is a
    situation to explain, not a malformed request."""
    req = urllib.request.Request(
        api_base() + path,
        method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


# ---- the tools ---------------------------------------------------------------
#
# Every description carries the trap that goes with it, because an agent only
# ever sees these words. docs/API.md documents the same traps for humans.


def _queue_video(args: dict) -> str:
    body: dict = {"url": args["url"]}
    for key in ("force", "min_score", "max_clips", "podcast", "long_clips"):
        if args.get(key) is not None:
            body[key] = args[key]
    out = _request("POST", "/jobs", body)
    if out.get("job_id") is None:
        if out.get("already_processed"):
            return (
                f"Not queued: {out.get('video_id')} was processed before, and doing it "
                "again would cost an hour and produce duplicate clips. Use list_clips to "
                "read what it already produced, or pass force=true to mean it."
            )
        if out.get("already_queued"):
            return f"Not queued: it is already waiting as job {out.get('queued_job_id')}."
        return f"Not queued: {json.dumps(out)}"
    return (
        f"Queued as job {out['job_id']}. A long stream takes a while, so poll job_status "
        "rather than waiting on this call."
    )


def _queue_local_file(args: dict) -> str:
    body = {"path": args["path"]}
    if args.get("title"):
        body["title"] = args["title"]
    # Worth passing: creator profiles key off the channel, so an empty one means
    # catchphrase learning and preference history quietly skip this video.
    if args.get("channel"):
        body["channel"] = args["channel"]
    out = _request("POST", "/videos/local", body)
    if out.get("job_id") is None:
        return f"Not queued: {json.dumps(out)}"
    return f"Queued as job {out['job_id']} (video {out.get('video_id')})."


def _job_status(args: dict) -> str:
    job = _request("GET", f"/jobs/{int(args['job_id'])}")
    line = (
        f"Job {job.get('id')}: {job.get('status')} | video {job.get('video_id') or '?'} "
        f"| {job.get('title') or 'title not known yet'}"
    )
    if job.get("error"):
        line += f"\nerror: {job['error']}"
    if job.get("status") == "done" and job.get("video_id"):
        line += f"\nRead the clips with list_clips on video_id {job['video_id']}."
    return line


def _queue_status(_args: dict) -> str:
    queue = _request("GET", "/queue")
    counts = {k: len(queue.get(k) or []) for k in ("processing", "queued", "completed", "failed")}
    text = (
        f"processing {counts['processing']}, waiting {counts['queued']}, "
        f"done {counts['completed']}, failed {counts['failed']}, "
        f"room for {queue.get('capacity')} more"
    )
    if queue.get("paused"):
        # A paused queue that nobody un-paused looks exactly like a broken app.
        text += "\nThe queue is PAUSED, so nothing new will start until it is resumed."
    estimate = queue.get("estimate") or {}
    if estimate.get("confident"):
        text += f"\nRoughly {int(estimate.get('queued_seconds', 0) / 60)} minutes of work waiting."
    return text


def _list_videos(_args: dict) -> str:
    videos = _request("GET", "/videos")
    if not videos:
        return "No videos processed yet."
    lines = [
        f"{v['video_id']}  {v.get('clip_count', 0):>3} clips  {v.get('status')}  "
        f"{(v.get('title') or '').strip()[:70]}"
        for v in videos[:40]
    ]
    return "\n".join(lines)


def _list_clips(args: dict) -> str:
    video_id = str(args["video_id"])
    clips = _request("GET", f"/videos/{video_id}/clips")
    if not clips:
        # An unknown id returns 200 [], so a typo looks like a video with no clips.
        return (
            f"No clips for {video_id}. Either it produced none, or the id is wrong: "
            "list_videos shows the ids that exist."
        )
    lines = []
    for clip in clips:
        start, end = clip.get("start_s", 0), clip.get("end_s", 0)
        exported = " (exported)" if clip.get("exported_at") else ""
        lines.append(
            f"[{clip['id']}] score {clip.get('score', 0):>3}  "
            f"{start / 60:.0f}m{start % 60:02.0f}s-{end / 60:.0f}m{end % 60:02.0f}s  "
            f"{(clip.get('title') or clip.get('hook') or '').strip()[:70]}{exported}"
        )
    return "\n".join(lines)


def _clip_captions(args: dict) -> str:
    out = _request("GET", f"/clips/{int(args['clip_id'])}/captions")
    lines = out.get("lines") or []
    if not lines:
        return "No captions for that clip."
    return "\n".join(f"{ln['start']:>6.2f}  {ln['text']}" for ln in lines)


def _export_clip(args: dict) -> str:
    out = _request(
        "POST", f"/clips/{int(args['clip_id'])}/export", {"folder": args["folder"]}
    )
    exported = out.get("exported") or []
    if not exported:
        # 200 with an empty list is what a missing clip looks like here.
        return "Nothing was exported. Check the clip id with list_clips and the folder path."
    return "Exported:\n" + "\n".join(str(p) for p in exported)


def _engine_status(_args: dict) -> str:
    health = _request("GET", "/health", timeout=10.0)
    return (
        f"Clips Kitty {health.get('app_version', '?')} is running at {api_base()} "
        f"(API v{health.get('api_version', '?')})."
    )


def _youtube_status(_args: dict) -> str:
    status = _request("GET", "/youtube/status")
    if not status.get("enabled"):
        return (
            "YouTube publishing is switched off. Open Clips Kitty, go to Settings, and "
            "turn on Publish to YouTube. It needs the user's own Google key, so this is "
            "not something to work around from here."
        )
    if not status.get("connected"):
        return "YouTube is on but no channel is connected. Connect one in Settings."
    channel = (status.get("channel") or {}).get("title") or "a channel"
    quota = status.get("quota") or {}
    return (
        f"Connected to {channel}. "
        f"{quota.get('remaining', '?')} of {quota.get('uploads_limit', '?')} uploads left today."
    )


def _publish_plan(args: dict) -> str:
    body = {"clip_ids": list(args.get("clip_ids") or [])}
    for key in ("start_at", "every_hours", "privacy"):
        if args.get(key) is not None:
            body[key] = args[key]
    out = _request("POST", "/publish/plan", body)
    items = out.get("items") or []
    if not items:
        return "Nothing to publish: " + ("; ".join(out.get("warnings") or []) or "no usable clips.")
    lines = ["This is the plan. NOTHING has been uploaded yet.", ""]
    for item in items:
        when = item.get("publish_at") or "as soon as it uploads"
        lines.append(f"  clip {item['clip_id']}: {item['title'][:60]}  ->  {when}")
    for warning in out.get("warnings") or []:
        lines.append(f"  warning: {warning}")
    lines += [
        "",
        "Show this to the person and get a clear yes before calling publish_plan_execute. "
        "Uploads cannot be taken back, and each one spends their daily quota.",
    ]
    return "\n".join(lines)


def _publish_plan_execute(args: dict) -> str:
    items = args.get("items") or []
    if not items:
        return "No items were given, so nothing was published."
    out = _request("POST", "/publish/plan/execute", {"items": items})
    started, skipped = out.get("started") or [], out.get("skipped") or []
    lines = [f"Started {len(started)} upload(s)."]
    for row in skipped:
        lines.append(f"  clip {row['clip_id']} skipped: {row['reason']}")
    if started:
        lines.append("Follow them with publish_status. Uploading takes a few minutes each.")
    return "\n".join(lines)


def _publish_status(args: dict) -> str:
    clip_id = args.get("clip_id")
    if clip_id is not None:
        out = _request("GET", f"/clips/{int(clip_id)}/publish")
        upload, job = out.get("upload"), out.get("job")
        if job:
            return f"Clip {clip_id}: {job.get('status')} ({job.get('error') or 'in progress'})"
        if upload:
            return (
                f"Clip {clip_id} is on YouTube as {upload.get('youtube_id')}, "
                f"{upload.get('actual_privacy') or upload.get('privacy')}."
            )
        return f"Clip {clip_id} has not been published."
    uploads = _request("GET", "/youtube/uploads")
    rows = uploads if isinstance(uploads, list) else uploads.get("uploads") or []
    if not rows:
        return "Nothing has been published yet."
    return "\n".join(
        f"  clip {r.get('clip_id')}: {r.get('youtube_id')} "
        f"({r.get('actual_privacy') or r.get('privacy')})"
        for r in rows[:20]
    )


TOOLS: list[dict] = [
    {
        "name": "youtube_status",
        "title": "Is YouTube connected",
        "description": (
            "Whether Clips Kitty can publish to YouTube right now, which channel, and how "
            "many uploads are left today. Check this before planning a batch."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _youtube_status,
    },
    {
        "name": "publish_plan",
        "title": "Plan a batch of uploads",
        "description": (
            "Work out what publishing these clips would do: final titles, descriptions and "
            "publish times. Creates nothing. Give start_at (with a timezone offset) and "
            "every_hours to space them out, or neither to upload as soon as each is ready. "
            "ALWAYS show the plan and get a yes before executing it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "clip_ids": {
                    "type": "array", "items": {"type": "integer"},
                    "description": "Clips to publish, from list_clips",
                },
                "start_at": {
                    "type": "string",
                    "description": "When the first goes out, RFC 3339 with an offset, e.g. 2026-09-18T12:00:00-05:00",
                },
                "every_hours": {
                    "type": "number", "description": "Hours between videos, e.g. 1 or 0.5",
                },
                "privacy": {
                    "type": "string",
                    "description": "public, unlisted or private. Scheduled videos are private until their time.",
                },
            },
            "required": ["clip_ids"],
        },
        "handler": _publish_plan,
    },
    {
        "name": "publish_plan_execute",
        "title": "Carry out a publishing plan",
        "description": (
            "Upload the clips in a plan, one job each, so one failure does not stop the "
            "rest. Only call this after the person has agreed to the plan. Uploads cannot "
            "be undone and each one spends their daily quota."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "The plan's items, as publish_plan returned them",
                    "items": {
                        "type": "object",
                        "properties": {
                            "clip_id": {"type": "integer"},
                            "title": {"type": "string"},
                            "publish_at": {"type": "string"},
                            "privacy": {"type": "string"},
                        },
                        "required": ["clip_id"],
                    },
                },
            },
            "required": ["items"],
        },
        "handler": _publish_plan_execute,
    },
    {
        "name": "publish_status",
        "title": "How an upload is going",
        "description": (
            "Where a clip's upload has got to, or the recent uploads when no clip is named."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"clip_id": {"type": "integer"}},
        },
        "handler": _publish_status,
    },
    {
        "name": "queue_video",
        "title": "Clip a video or stream",
        "description": (
            "Hand Clips Kitty a YouTube, Twitch or Kick link and it finds the moments worth "
            "posting, crops them to vertical and captions them, all on this computer. "
            "Returns a job id; processing a long stream takes a while."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "YouTube, Twitch or Kick link"},
                "force": {
                    "type": "boolean",
                    "description": "Process again even if this video was done before",
                },
                "min_score": {"type": "integer", "description": "Quality bar, 0-100"},
                "max_clips": {"type": "integer", "description": "Cap clips from this video"},
                "podcast": {
                    "type": "boolean",
                    "description": "Multi-camera podcast footage: framing cuts per shot",
                },
                "long_clips": {
                    "type": "boolean",
                    "description": "61-180s clips instead of 10-60s",
                },
            },
            "required": ["url"],
        },
        "handler": _queue_video,
    },
    {
        "name": "queue_local_file",
        "title": "Clip a file on this computer",
        "description": (
            "Same pipeline for a video already on disk. Nothing is uploaded. Set channel "
            "when you know it: creator learning keys off it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path to the video file"},
                "title": {"type": "string", "description": "Defaults to the filename"},
                "channel": {"type": "string", "description": "Whose channel this is"},
            },
            "required": ["path"],
        },
        "handler": _queue_local_file,
    },
    {
        "name": "job_status",
        "title": "Check a processing job",
        "description": "Where a job has got to: queued, running, done, failed or cancelled.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "integer"}},
            "required": ["job_id"],
        },
        "handler": _job_status,
    },
    {
        "name": "queue_status",
        "title": "Check the queue",
        "description": (
            "What the queue is doing, how much room is left, and whether it is paused. "
            "Check this before reporting that nothing is happening."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _queue_status,
    },
    {
        "name": "list_videos",
        "title": "List processed videos",
        "description": "Videos Clips Kitty has processed, newest first, with clip counts.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _list_videos,
    },
    {
        "name": "list_clips",
        "title": "List a video's clips",
        "description": (
            "The clips from one video: id, score, timestamps and title. Scores are the "
            "model's ranking, 0-100."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"video_id": {"type": "string"}},
            "required": ["video_id"],
        },
        "handler": _list_clips,
    },
    {
        "name": "clip_captions",
        "title": "Read a clip's captions",
        "description": "The transcript of one clip, as burned-in caption lines with times.",
        "inputSchema": {
            "type": "object",
            "properties": {"clip_id": {"type": "integer"}},
            "required": ["clip_id"],
        },
        "handler": _clip_captions,
    },
    {
        "name": "export_clip",
        "title": "Export a clip to a folder",
        "description": "Copy a finished clip out to a folder, with its final filename.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "clip_id": {"type": "integer"},
                "folder": {"type": "string", "description": "Destination folder"},
            },
            "required": ["clip_id", "folder"],
        },
        "handler": _export_clip,
    },
    {
        "name": "engine_status",
        "title": "Is Clips Kitty running",
        "description": "Whether the engine is up, and which version it is.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _engine_status,
    },
]

INSTRUCTIONS = (
    "Clips Kitty turns long videos into short vertical clips, entirely on this computer. "
    "Queue work with queue_video or queue_local_file, follow it with job_status, then read "
    "the results with list_clips and export_clip. Processing a long stream takes tens of "
    "minutes, so never block on it: queue, then check back."
)


# ---- protocol ----------------------------------------------------------------


def _result(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_text(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _call_tool(params: dict) -> dict:
    name = params.get("name")
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        return _tool_text(f"Unknown tool: {name}", is_error=True)
    args = params.get("arguments") or {}
    try:
        return _tool_text(tool["handler"](args))
    except KeyError as e:
        return _tool_text(f"Missing argument: {e}", is_error=True)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        return _tool_text(f"The engine refused that ({e.code}): {detail}", is_error=True)
    except urllib.error.URLError:
        return _tool_text(NOT_RUNNING.format(base=api_base()), is_error=True)
    except (OSError, ValueError) as e:
        return _tool_text(f"Could not reach the engine: {e}", is_error=True)


def handle(message: dict) -> dict | None:
    """One JSON-RPC message in, one response out. None for a notification,
    which by definition is never answered."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")

    method = message.get("method")
    msg_id = message.get("id")
    if method is None:
        return _error(msg_id, INVALID_REQUEST, "no method")
    if msg_id is None:  # a notification: initialized, cancelled, anything else
        return None

    if method == "initialize":
        asked = (message.get("params") or {}).get("protocolVersion")
        return _result(msg_id, {
            # Same version back when we know it, ours when we do not.
            "protocolVersion": asked if asked in KNOWN_PROTOCOLS else PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": "clips-kitty",
                "title": "Clips Kitty",
                "version": _app_version(),
            },
            "instructions": INSTRUCTIONS,
        })

    if method == "ping":
        return _result(msg_id, {})

    if method == "tools/list":
        return _result(msg_id, {
            "tools": [{k: v for k, v in tool.items() if k != "handler"} for tool in TOOLS]
        })

    if method == "tools/call":
        params = message.get("params") or {}
        if not any(t["name"] == params.get("name") for t in TOOLS):
            # An unknown tool is the client's mistake, so it is a protocol
            # error. A tool that runs and fails is isError instead.
            return _error(msg_id, INVALID_PARAMS, f"Unknown tool: {params.get('name')}")
        return _result(msg_id, _call_tool(params))

    return _error(msg_id, METHOD_NOT_FOUND, f"Unknown method: {method}")


def serve(stdin=None, stdout=None) -> int:
    """Read messages until stdin closes. Nothing but MCP messages may go to
    stdout, which is why anything worth saying goes to stderr."""
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    for raw in source:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write(sink, _error(None, PARSE_ERROR, "invalid JSON"))
            continue
        response = handle(message)
        if response is not None:
            _write(sink, response)
    return 0


def _write(sink, message: dict) -> None:
    # One message per line, and json.dumps escapes any newline inside a string,
    # so a message can never be split across lines.
    sink.write(json.dumps(message, ensure_ascii=False) + "\n")
    sink.flush()
