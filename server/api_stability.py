"""How much each local API route promises to code outside Clips Kitty.

Three labels:

- **stable**: documented as supported in docs/API.md. It changes only by
  adding (a new optional field, a new route). Removing or reshaping one bumps
  API_VERSION in server/api.py, as docs/API.md has always said.
- **experimental**: meant for outside use, but its shape may still change in a
  release. Every change is noted in CHANGELOG.md.
- **internal**: serves one screen of the desktop app and changes with it.

Anything not listed below is internal, so a new route promises nothing until
someone decides it should. The developer reference
(docs/developers/api-reference.md) is generated from this table and the app's
own OpenAPI description by scripts/gen_api_reference.py, and
tests/test_api_contract.py keeps the table, the reference and the stable
routes' shapes from drifting.

Paths are FastAPI's templates (`/jobs/{job_id}`), so they match app.routes.
"""

from __future__ import annotations

STABLE = "stable"
EXPERIMENTAL = "experimental"
INTERNAL = "internal"

# Sections of docs/API.md, in its order, for grouping the reference.
SECTIONS = {
    "readiness": ("Readiness", "readiness"),
    "submit": ("Submitting work", "submitting-work"),
    "progress": ("Watching progress", "watching-progress"),
    "queue": ("The queue", "the-queue"),
    "results": ("Results", "results"),
    "models": ("Models and where the AI runs", "models"),
    "export": ("Languages and export", "languages-and-export"),
    "youtube": ("Publishing to YouTube", "publishing-to-youtube"),
    "integrations": ("Streamer integrations", "streamer-integrations"),
    "automation": ("Watched channels", "watched-channels"),
    "events": ("WebSocket events", "websocket-events"),
    "plugins": ("Plugins", "plugins"),
}

# (method, path) -> (label, section, what it is for). WS marks the WebSocket.
ROUTES: dict[tuple[str, str], tuple[str, str, str]] = {
    # Readiness
    ("GET", "/health"): (STABLE, "readiness", "Liveness, app version and API version."),
    ("GET", "/health/preflight"): (STABLE, "readiness", "Whether a job can actually run here: FFmpeg, Ollama, model, Whisper, GPU, disk."),
    ("GET", "/system/stats"): (STABLE, "readiness", "CPU, memory, disk and GPU use."),
    # Submitting work
    ("POST", "/jobs"): (STABLE, "submit", "Queue one link with its options."),
    ("POST", "/jobs/batch"): (STABLE, "submit", "Queue several links, each with its own options; bad ones are reported, not fatal."),
    ("POST", "/videos/local"): (STABLE, "submit", "Queue a video file already on this computer."),
    ("GET", "/sports"): (STABLE, "submit", "The sports a job's `sport` option can name, with their choices."),
    # Watching progress
    ("GET", "/jobs"): (STABLE, "progress", "Every job, newest last (a bare array)."),
    ("GET", "/jobs/{job_id}"): (STABLE, "progress", "One job."),
    ("GET", "/jobs/{job_id}/log"): (STABLE, "progress", "The job's own log file, last lines."),
    ("POST", "/jobs/{job_id}/retry"): (STABLE, "progress", "Queue a failed job again with its original settings."),
    ("DELETE", "/jobs/{job_id}"): (STABLE, "progress", "Remove a job."),
    ("POST", "/cancel"): (STABLE, "progress", "Ask a running video to stop at its next stage boundary."),
    # The queue
    ("GET", "/queue"): (STABLE, "queue", "The grouped queue the app shows, with capacity and estimate."),
    ("POST", "/queue/pause"): (STABLE, "queue", "Stop claiming new work."),
    ("POST", "/queue/resume"): (STABLE, "queue", "Start claiming work again."),
    # Results
    ("GET", "/videos"): (STABLE, "results", "Every video in the library."),
    ("GET", "/videos/{video_id}/clips"): (STABLE, "results", "A video's clips with titles, scores and paths."),
    ("GET", "/media/{clip_id}"): (STABLE, "results", "A clip's video file."),
    ("GET", "/clips/{clip_id}/captions"): (STABLE, "results", "A clip's caption lines, clip-relative."),
    # Models and where the AI runs
    ("GET", "/models"): (STABLE, "models", "Local models through Ollama, and which one is active."),
    ("POST", "/models/activate"): (STABLE, "models", "Choose the local model."),
    ("POST", "/models/pull"): (STABLE, "models", "Download a local model through Ollama."),
    ("GET", "/ai"): (STABLE, "models", "Where the AI work runs: local or a cloud provider, and the providers offered."),
    ("PUT", "/ai/providers/{provider_id}/key"): (STABLE, "models", "Check and keep a provider's API key."),
    ("DELETE", "/ai/providers/{provider_id}/key"): (STABLE, "models", "Delete a provider's stored key."),
    ("GET", "/ai/providers/{provider_id}/models"): (STABLE, "models", "Models a provider offers for the job, with the user's key."),
    ("POST", "/ai/providers/{provider_id}/test"): (STABLE, "models", "Check key, reachability and model without spending tokens."),
    ("POST", "/ai/activate"): (STABLE, "models", "Choose which model does the AI work."),
    ("POST", "/ai/transcription"): (STABLE, "models", "Choose local Whisper or a provider for transcription."),
    # Languages and export
    ("GET", "/languages"): (STABLE, "export", "Languages for captions and translation."),
    ("POST", "/translate"): (STABLE, "export", "Translate a clip's captions."),
    ("POST", "/clips/{clip_id}/export"): (STABLE, "export", "Copy a clip to a folder."),
    ("POST", "/export/batch"): (STABLE, "export", "Copy several clips to a folder."),
    # Publishing to YouTube
    ("GET", "/youtube/status"): (STABLE, "youtube", "Whether publishing is available and connected."),
    ("POST", "/clips/{clip_id}/publish"): (STABLE, "youtube", "Publish one clip."),
    ("GET", "/clips/{clip_id}/publish"): (STABLE, "youtube", "A clip's publishing state."),
    # Streamer integrations, batch publishing and local thumbnails
    ("POST", "/integrations/streams"): (STABLE, "integrations", "Hand over a finished livestream to be clipped when its VOD appears."),
    ("GET", "/integrations/streams/{session_id}"): (STABLE, "integrations", "A handed-over stream's progress."),
    ("POST", "/integrations/streams/{session_id}/link"): (STABLE, "integrations", "Give the stream's VOD link by hand."),
    ("DELETE", "/integrations/streams/{session_id}"): (STABLE, "integrations", "Stop waiting for a stream."),
    ("GET", "/integrations/presets"): (STABLE, "integrations", "Presets a dock can offer."),
    ("POST", "/publish/plan"): (STABLE, "integrations", "Plan a batch of uploads without doing them."),
    ("POST", "/publish/plan/execute"): (STABLE, "integrations", "Carry out a plan."),
    ("POST", "/clips/{clip_id}/thumbnail/generate"): (STABLE, "integrations", "Make thumbnail candidates from the clip's frames."),
    ("GET", "/clips/{clip_id}/thumbnail/generated/{index}"): (STABLE, "integrations", "One generated thumbnail candidate."),
    # Watched channels
    ("GET", "/automation"): (STABLE, "automation", "Watched-channel settings."),
    ("PATCH", "/automation"): (STABLE, "automation", "Change watched-channel settings."),
    ("POST", "/automation/watches"): (STABLE, "automation", "Watch a channel."),
    ("GET", "/automation/watches"): (STABLE, "automation", "Every watch."),
    ("PATCH", "/automation/watches/{watch_id}"): (STABLE, "automation", "Change a watch."),
    ("DELETE", "/automation/watches/{watch_id}"): (STABLE, "automation", "Stop watching."),
    ("POST", "/automation/watches/{watch_id}/check"): (STABLE, "automation", "Check a watch now."),
    ("GET", "/automation/activity"): (STABLE, "automation", "What the watches did recently."),
    ("GET", "/automation/slots"): (STABLE, "automation", "Upcoming publishing slots."),
    ("GET", "/automation/items"): (STABLE, "automation", "Videos a watch found."),
    ("POST", "/automation/items/{item_id}/queue"): (STABLE, "automation", "Clip a found video."),
    ("POST", "/automation/items/{item_id}/publish"): (STABLE, "automation", "Publish a found video's clips."),
    ("POST", "/automation/items/{item_id}/skip"): (STABLE, "automation", "Skip a found video."),
    # Events
    ("WS", "/ws"): (STABLE, "events", "Job, progress, queue, model and publishing events."),
    # Mentioned in docs/API.md as something to use, without a full contract there.
    ("GET", "/clips/{clip_id}/words"): (EXPERIMENTAL, "results", "A clip's words with their times."),
    ("PATCH", "/clips/{clip_id}"): (EXPERIMENTAL, "export", "Change a clip's title, schedule and other fields."),
    ("PATCH", "/jobs/{job_id}"): (EXPERIMENTAL, "progress", "Change a queued job's options."),
    ("POST", "/storage/cleanup"): (EXPERIMENTAL, "progress", "Remove leftover partial downloads and other temporary files."),
    # docs/API.md documents this under thumbnails and also lists it among the
    # YouTube routes that "may change", so it is promised no more than that.
    ("POST", "/clips/{clip_id}/thumbnail"): (EXPERIMENTAL, "integrations", "Choose a clip's thumbnail."),
    # The plugin platform (plugins/api.py). Experimental while it is new; the
    # routes that change what is installed need the X-Clips-Kitty-Session header.
    ("GET", "/plugins"): (EXPERIMENTAL, "plugins", "Installed plugins with their versions, permissions and state, and the built-in modes."),
    ("POST", "/plugins/plan"): (EXPERIMENTAL, "plugins", "Fetch a plugin from a folder or a Git commit and say what installing it would do, with the install screen as text. Session header."),
    ("POST", "/plugins/install"): (EXPERIMENTAL, "plugins", "Install what a plan fetched. Session header."),
    ("POST", "/plugins/{publisher}/{name}/enable"): (EXPERIMENTAL, "plugins", "Turn a plugin on. Session header."),
    ("POST", "/plugins/{publisher}/{name}/disable"): (EXPERIMENTAL, "plugins", "Turn a plugin off. Session header."),
    ("POST", "/plugins/{publisher}/{name}/rollback"): (EXPERIMENTAL, "plugins", "Go back to the version installed before. Session header."),
    ("POST", "/plugins/{publisher}/{name}/pin"): (EXPERIMENTAL, "plugins", "Stop update offers for a plugin. Session header."),
    ("POST", "/plugins/{publisher}/{name}/unpin"): (EXPERIMENTAL, "plugins", "Offer a plugin's updates again. Session header."),
    ("GET", "/plugin-models"): (EXPERIMENTAL, "plugins", "Every model the installed plugins list, once: where it is, its licence and size, and which plugins use it."),
    ("POST", "/plugin-models/plan"): (EXPERIMENTAL, "plugins", "What downloading one plugin's model would fetch, from Hugging Face's metadata; downloads nothing. Session header."),
    ("POST", "/plugin-models/download"): (EXPERIMENTAL, "plugins", "Download one plugin's model into the shared model folder, checked against its size and SHA-256. Session header."),
    ("DELETE", "/plugins/{publisher}/{name}"): (EXPERIMENTAL, "plugins", "Remove a plugin, its files and its stored keys. Session header."),
    ("PUT", "/plugins/{publisher}/{name}/secrets"): (EXPERIMENTAL, "plugins", "Store a plugin's secret settings. Session header."),
    ("GET", "/marketplace"): (EXPERIMENTAL, "plugins", "Listed plugins from the registry indexes, searched and filtered, with what is installed."),
    ("POST", "/marketplace/refresh"): (EXPERIMENTAL, "plugins", "Fetch Clips Kitty's online list and the registry index addresses set in settings into the cache; automatic: the online list only, when a day old. Session header."),
    ("GET", "/marketplace/online"): (EXPERIMENTAL, "plugins", "Whether the Marketplace checks Clips Kitty's online list by itself, when it last fetched it and how the last try went."),
    ("PUT", "/marketplace/online"): (EXPERIMENTAL, "plugins", "Switch the Marketplace's automatic checks of Clips Kitty's online list on or off for this PC. Session header."),
    ("GET", "/marketplace/catalog"): (EXPERIMENTAL, "plugins", "Awesome Clips Kitty's apps, models, workflows, integrations and tools, searched, with their sections, labels and numbers."),
    ("GET", "/marketplace/counting"): (EXPERIMENTAL, "plugins", "Whether installs from the Marketplace are counted, and what counting sends."),
    ("PUT", "/marketplace/counting"): (EXPERIMENTAL, "plugins", "Switch install counting on or off for this PC. Session header."),
}


def label(method: str, path: str) -> str:
    """The stability of one route; anything not listed is internal."""
    entry = ROUTES.get((method.upper(), path))
    return entry[0] if entry else INTERNAL


def app_routes(app) -> list[tuple[str, str, str]]:
    """Every (method, path, module) the app serves, WebSocket as "WS".

    FastAPI's own pages (/docs, /redoc, /openapi.json) are left out: they
    describe the API rather than being part of it.
    """
    from fastapi.routing import APIRoute, APIWebSocketRoute

    found = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                found.append((method, route.path, route.endpoint.__module__))
        elif isinstance(route, APIWebSocketRoute):
            found.append(("WS", route.path, route.endpoint.__module__))
    return sorted(found, key=lambda r: (r[1], r[0]))


def _shape(schema, components: dict) -> str:
    """A schema as a short type string, so a contract compares what a client
    can send and not how a FastAPI release happens to spell it.

    `{"anyOf": [{"type": "string"}, {"type": "null"}]}` is `string|null`, an
    array of integers `array<integer>`, a model `{name:string, size?:integer}`.
    Defaults and prose are left out: a default is behaviour, pinned by tests.
    """
    if not isinstance(schema, dict) or not schema:
        return "any"
    if "$ref" in schema:
        return _shape(components.get(schema["$ref"].rsplit("/", 1)[-1], {}), components)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return "|".join(sorted({_shape(s, components) for s in schema[key]}))
    if "allOf" in schema and len(schema["allOf"]) == 1:
        return _shape(schema["allOf"][0], components)
    if "enum" in schema:
        return "enum[" + ",".join(sorted(str(v) for v in schema["enum"])) + "]"
    if "const" in schema:
        return f"const[{schema['const']}]"
    kind = schema.get("type", "any")
    if kind == "array":
        return f"array<{_shape(schema.get('items', {}), components)}>"
    if kind == "object" and "properties" in schema:
        required = set(schema.get("required", []))
        fields = ",".join(
            f"{name}{'' if name in required else '?'}:{_shape(sub, components)}"
            for name, sub in sorted(schema["properties"].items())
        )
        return "{" + fields + "}"
    if kind == "object" and isinstance(schema.get("additionalProperties"), dict):
        return f"map<{_shape(schema['additionalProperties'], components)}>"
    return str(kind)


def contract(app) -> dict:
    """The request shape of every stable HTTP route, from the app's OpenAPI.

    Keyed "METHOD /path": its query and path parameters and, when it takes a
    JSON body, that body's fields, each with whether it is required and its
    type (see _shape). Responses are mostly plain dicts with no declared model, so their
    shape is pinned by behaviour tests instead.
    """
    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    out = {}
    for (method, path), (lab, _section, _purpose) in sorted(ROUTES.items()):
        if lab != STABLE or method == "WS":
            continue
        op = spec["paths"].get(path, {}).get(method.lower())
        if op is None:
            continue
        params = {
            p["name"]: {"in": p["in"], "required": bool(p.get("required")), "type": _shape(p.get("schema", {}), components)}
            for p in op.get("parameters", [])
        }
        body = None
        content = (op.get("requestBody") or {}).get("content", {})
        if "application/json" in content:
            schema = content["application/json"].get("schema", {})
            if "$ref" in schema:
                schema = components.get(schema["$ref"].rsplit("/", 1)[-1], {})
            props = schema.get("properties")
            if props is not None:
                required = set(schema.get("required", []))
                body = {name: {"required": name in required, "type": _shape(sub, components)}
                        for name, sub in sorted(props.items())}
            else:
                body = {"(body)": {"required": bool(op["requestBody"].get("required")),
                                   "type": _shape(schema, components)}}
        out[f"{method} {path}"] = {"params": params, "body": body}
    return out
