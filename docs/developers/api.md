# API

Clips Kitty's engine is a local HTTP service on `http://127.0.0.1:8765`, running whenever the app is open (or headless with `python main.py serve --port 8765`). The desktop window is one client of it; a plugin, a script, the OBS dock and the MCP server are others. There is one API: plugins use the same one.

- **How to call it**, with real responses, webhooks and gotchas: [`docs/API.md`](../API.md).
- **Every route with its stability label**: [API reference](api-reference.md), generated from the app itself.

## Versions

`GET /health` returns `{"ok": true, "app_version": "2.0.0", "api_version": 1}`.

- `api_version` changes only when a **stable** route changes incompatibly. Check it once at start-up and tell your user to update Clips Kitty if it is lower than you need.
- `app_version` is the installed release. Plugins state the releases they work with in their manifest (see [Versioning](versioning.md)).

## What each label promises

| Label | Promise | Today |
|---|---|---|
| **stable** | Documented as supported in `docs/API.md`. Changes only by adding: a new optional field, a new route, a new event type. Removing, renaming, retyping or making something required is an incompatible change and raises `api_version`. | 59 HTTP routes and the `/ws` event stream: health, jobs, queue, library reads and media, captions, models and where the AI runs, languages and export, YouTube publishing status and publish, streamer integrations, batch publishing plans, thumbnails, watched channels |
| **experimental** | Meant for outside use, but may still change in a release. Every change is noted in `CHANGELOG.md`. | 5 routes mentioned in `docs/API.md` without a full contract (clip words, editing a clip or a queued job, storage clean-up, choosing a thumbnail), and the plugin platform's 18 routes (`/plugins`, `/marketplace` with its catalog and install counting, `/plugin-models`; [Plugin development](plugin-development.md), [Marketplace publishing](marketplace-publishing.md), [Model references](model-references.md)) |
| **internal** | Serves one screen of the desktop app and changes with it. It works, and `/docs` on a running engine shows it, but depending on it is at your own risk. | Everything else, about 110 routes |

Anything not explicitly labelled is internal, so a new route promises nothing until someone decides it should.

**How the promise is kept.** `tests/test_api_contract.py` pins what every stable route accepts (its parameters and JSON body fields, with their types and whether they are required) in `tests/fixtures/api/stable_contract.json`, and fails when a change would break a client written against it. It also pins the documented behaviour of the calls an integrator starts with: health, submitting a job and the "already queued" answer, batches, the job list, cancel, the queue and pausing, local files, sports, library reads, languages, presets and watched channels. Responses may gain fields; clients should ignore fields they do not know.

## Security

- The engine listens on `127.0.0.1` only and refuses requests whose `Host` header is not `127.0.0.1` or `localhost` (`server/api.py:577-589`).
- There is **no authentication**. Anything running on the computer as the user can call it. That includes plugins: a plugin is code that runs with your rights, so the API is not a wall between a plugin and your library. See [Security](security.md).
- The one exception: the plugin manager's routes that fetch, install, change or remove plugins need the session secret in an `X-Clips-Kitty-Session` header. It keeps web pages and stray scripts out; it is not a password against software already running as you ([Security](security.md#the-session-secret)).
- Browsers can only read responses for the app's own development origin (CORS), so a web page cannot read your library through the API.

## Calling it from a plugin

A pipeline plugin does not need the HTTP API to do its job: the engine hands it the video, transcript and tools in a job folder and takes back moments (see [Pipeline development](pipeline-development.md)). Plugins that want more, such as reading the library, use the same stable routes as any other client. The SDK's small client is described in [SDK](sdk.md).

## Changing a stable route (for Clips Kitty contributors)

1. Prefer an additive change: a new optional field or a new route.
2. Run `python scripts/gen_api_reference.py` to regenerate the reference, and `python scripts/gen_api_reference.py --update-contract` to re-pin the shapes.
3. If the change is incompatible, also raise `API_VERSION` in `server/api.py`, keep the old behaviour for at least one release where you can, and say so in `CHANGELOG.md`.
4. To promise a new route, add it to `server/api_stability.py` with a label and a one-line purpose.
