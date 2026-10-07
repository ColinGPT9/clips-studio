# Platform overview

Clips Kitty finds the best moments in a long video and turns them into short clips: it downloads the video, transcribes it with Whisper, picks the moments, then cuts, frames, captions and publishes them, all on the user's PC. The platform lets anyone replace **one** of those steps, picking the moments, with their own pipeline, without forking the app.

That is where specialised knowledge pays: someone who knows one game, one sport or one kind of show can tell a great moment from a loud one far better than a general-purpose detector. You write the part that knows your niche. Clips Kitty does the rest, and its users find your pipeline in the Marketplace.

```text
                         Marketplace (registry indexes on GitHub, searched in the app)
                                         │
                       ┌─────────────────┼─────────────────┐
                   Pipelines          Models          (Plugins, Workflows,
                  (built)       (references, built)    Providers: planned)
                       └─────────────────┼─────────────────┘
                     Plugin contract 1 + Python SDK (clipskitty_sdk)
                                         │
             Clips Kitty's engine: one local API (127.0.0.1:8765), one job queue,
             one pipeline: download → transcribe → [find moments] → cut, frame, caption
                                         │
                ┌────────────────────────┼────────────────────────┐
             FFmpeg                   Whisper                Ollama / Gemma
                └────────────────────────┼────────────────────────┘
                                   GPU, when there is one
```

There is one API, one engine and one job queue. A community pipeline runs inside the same job as Clips Kitty's own modes; it is not a second app beside it.

## What a pipeline is

A program, in any language, that Clips Kitty starts for one video. It reads a job folder (`job.json`: the video, its transcript, settings, the tools and models it asked for), works out which moments make good clips, and writes them back (`result.json`: start, end, score, label, reason). Progress goes to the app as it works. It can run entirely on the PC, call your own hosted model, or both, as long as it says so.

```text
Clips Kitty                              your pipeline
  download, transcribe   ── job.json ──▶  find the moments (your code, your models)
  cut, frame, caption    ◀─ result.json ─
  library, publishing    ◀─ progress ───
```

## Where things stand

| Piece | State |
|---|---|
| Plugin contract 1, the Python SDK, running a pipeline for a job | built |
| The manifest (`clipskitty.yaml`), its validator and JSON Schema | built |
| Install from a folder or a Git commit; update, roll back, turn off, pin, remove | built (engine routes and the Marketplace screen) |
| Registry: listing format, index build, the app's search, install from a listing, block list | built; no public registry repository or index address exists yet |
| Marketplace screen in the desktop app; the Pipeline switch on a video | built and type-checked; not yet looked at on a real PC |
| Model references (Hugging Face, url, Ollama, bundled), one shared download, licence and size shown | built for public models; gated models planned |
| Returning finished clip files instead of moments | planned |
| Plugin kinds other than pipelines (caption styles, publishers, sources, providers) | planned: the manifest names them and refuses them with a message |
| A plugin's own Python packages, installed for it | planned |
| Sandboxing a plugin's process | not planned for now; see [Security](security.md) for what that means |

"Built" means written and covered by automated tests on Linux. Nothing here has yet been run on Windows, the platform Clips Kitty ships on; the overnight report (`docs/platform/OVERNIGHT-REPORT.md`) lists what still needs a real PC.

## The pages

**Start here**
- [Getting started](getting-started.md): a working pipeline in a few minutes, tested outside the app, then installed in it.
- [Example pipeline](example-pipeline.md): a complete one, laid out as its own repository, to copy.

**Building**
- [Pipeline development](pipeline-development.md): the contract, what you receive and return, testing.
- [SDK](sdk.md): the Python helper (`clipskitty_sdk`), every call.
- [Plugin manifest](plugin-manifest.md): every field of `clipskitty.yaml`.
- [Plugin development](plugin-development.md): install, update, roll back and remove, as a user and through the API.
- [Model references](model-references.md) and [Hugging Face](hugging-face.md): naming models, one shared copy.
- [Local APIs](local-apis.md): FFmpeg, Ollama and Clips Kitty's own API from a plugin.
- [Remote APIs](remote-apis.md): calling a hosted model, declaring what leaves the PC, keys.
- [API](api.md) and the [API reference](api-reference.md): the engine's HTTP API, with a stability label on every route.

**Trust and releases**
- [Permissions](permissions.md): what each permission means, and which are enforced and which only declared.
- [Security](security.md): what Clips Kitty does and doesn't protect, for the people installing plugins.
- [Versioning](versioning.md): plugin versions, app versions, pinning, rollback.
- [Marketplace publishing](marketplace-publishing.md): getting listed, how search finds you, what "listed" means.
- [Troubleshooting](troubleshooting.md): the messages, and what to do.

## Promises

- **Free.** Listing, updating and installing cost nothing. Clips Kitty takes no share of anything and processes no payments; link to your own pricing, sponsors or support page.
- **Yours.** Your code stays in your repository under your licence. A listing points at it; nothing moves it.
- **No fork.** Everything on these pages works against an unmodified Clips Kitty.
- **Honest labels.** A permission is called enforced only where Clips Kitty enforces it, "listed" never means "reviewed", and data that leaves the PC is always shown as a warning.
