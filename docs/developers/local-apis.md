# Local APIs

What a plugin can use on the user's own PC: Clips Kitty's tools handed over in the job, the local AI model through Ollama, Clips Kitty's own API, and other programs the user runs. Everything on this page keeps the user's media on their PC.

Status: **built** in plugin contract 1, except where a line says planned.

## Handed over in the job

The cheapest way to use what Clips Kitty has is to ask for it in your manifest's `permissions`; Clips Kitty then puts it in `job.json`, and leaves it out otherwise ([Permissions](permissions.md)).

| Permission | Your code gets | Use it for |
|---|---|---|
| `video.read` | `job.video.path` (the downloaded or local video), its id, title, duration and the games the source named | anything that reads the picture or sound |
| `transcript.read` | `job.transcript.segments()`: Whisper's transcript, with word timings when Clips Kitty has them | language: what is said, when |
| `ffmpeg` | `job.tools.ffmpeg`, `job.tools.ffprobe`: the paths of the FFmpeg Clips Kitty ships | decoding frames, extracting audio, scene changes, loudness |
| `ollama` | `job.tools.ollama`: `{"host": "http://localhost:11434", "model": "<the user's local model>"}` | asking the user's local language model about the transcript |

`job.tools.ollama.model` is the model the user chose in Clips Kitty (Gemma by default) when their AI runs locally, and empty when they chose a cloud provider: Clips Kitty never hands a plugin a cloud provider's key, so in that case your plugin either asks for its own model or does without. A plugin that needs a specific Ollama model lists it under `models:` with `source: ollama` ([Model references](model-references.md)); the user pulls it on the Models page, and the run stops with a message saying so if they haven't.

The SDK's `local_model` module (SDK 1.2.0) asks that model for you, with pictures when the model can look at them ([SDK](sdk.md#local_model-the-creators-local-model-on-this-pc), [Signals cookbook](signals-cookbook.md#asking-the-local-model-about-a-frame)):

```python
from clipskitty_sdk import local_model, media


def main(job):
    for m in job.moments:
        if local_model.can_see(job):
            said = local_model.ask(job, "What is on screen? Answer in one sentence.",
                                   images=[media.jpeg(job, (m.start + m.end) / 2)])
            job.understand(m, said)
```

It talks only to a model on this PC (127.0.0.1, localhost or ::1), refuses when Clips Kitty's AI runs at a cloud provider or at an address off this PC, and never falls back to another address. Its requests ignore proxy settings on purpose, so the prompt and the frames never leave the PC.

Calling Ollama yourself, with the standard library only, use an opener without proxies: `urllib.request.urlopen` can send a request to this PC through a proxy set in the environment.

```python
import json
import urllib.request

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ask(job, prompt):
    ollama = job.tools.ollama
    if not ollama or not ollama.get("model"):
        job.fail("This pipeline needs a local AI model. Choose one in Clips Kitty's Models page.")
    body = {"model": ollama["model"], "prompt": prompt, "stream": False, "format": "json"}
    req = urllib.request.Request(ollama["host"].rstrip("/") + "/api/generate",
                                 data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with NO_PROXY.open(req, timeout=300) as response:
        return json.loads(response.read())["response"]
```

Ollama has no password, so any program on the PC can use it; the `ollama` permission decides only whether Clips Kitty hands you the address and the user's model.

## Clips Kitty's own API

The engine's HTTP API on `http://127.0.0.1:8765` is the same one the desktop window uses ([API](api.md)). A pipeline doesn't need it to do its job: the job folder has what it needs, and its answer goes back in `result.json`. A plugin that does more (reads the library, queues another video) can call it with the SDK's client:

```python
from clipskitty_sdk.client import LocalAPI

api = LocalAPI()
api.health()                                   # {"ok": True, "app_version": ..., "api_version": 1}
recent = api.videos()                          # the library
```

Say so in your manifest: `project.read` to read the library, `project.write` to change it. These are **declared**, not enforced: the API has no authentication, so any program on the PC can call it. Stick to routes labelled stable ([API reference](api-reference.md)); others can change in any release. At 127.0.0.1, localhost or ::1, `LocalAPI` ignores proxy settings, so a request to this PC never goes to a proxy.

The plugin manager's routes need the session secret, which the desktop app sends. It keeps out web pages and scripts that don't know it, not programs running as you: any program running as the user can read the file that holds it, plugins included. A plugin must not install or remove plugins; Clips Kitty can't stop one that tries.

## Other programs on the PC

A plugin can talk to another local service the user runs (a local inference server, a game's own API, a ComfyUI install). Declare it like any connection, with `network` and the host and port, so the user sees it:

```yaml
permissions: [video.read, network]
network: [localhost:8188]
execution: local
```

Data sent to a program on the same PC hasn't left it, so a `local` pipeline needs no `sends` for this. If that program sends things on to the internet, your pipeline is `hybrid` and says what goes where ([Remote APIs](remote-apis.md)).

## Languages other than Python

The contract is files and standard output, so a plugin can be any executable: set `run.command` to it (`["bin/my-pipeline.exe"]`), read `job.json` from the job folder (the last argument on its command line, and `CLIPSKITTY_JOB` in its environment), print progress lines, write `result.json`. The Python SDK is a convenience, not a requirement ([Pipeline development](pipeline-development.md)). A TypeScript SDK is planned.

See also: [SDK](sdk.md), [Model references](model-references.md).
