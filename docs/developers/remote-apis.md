# Remote APIs

A pipeline doesn't have to run its AI on the user's PC. It can call your own hosted model, a Hugging Face Inference Provider, Replicate, OpenRouter or any other service. Clips Kitty still downloads the video, transcribes it, cuts, frames and captions the clips; your pipeline decides which moments, and part of that work happens elsewhere.

Status: **built** in plugin contract 1: `remote` and `hybrid` pipelines, their declarations, the warnings users see, and keys stored for the plugin. Clips Kitty does not proxy, filter or meter a plugin's network traffic; what leaves the PC is what your code sends, and your declaration is what users are shown.

## Declare what leaves the PC

```yaml
execution: hybrid                 # local | remote | hybrid
permissions: [transcript.read, network]
network: [api.example.com]
sends:
  - {data: transcript, to: Example Cloud}
  - {data: frames, to: Example Cloud, when: the "Look at the picture" setting is on}
service:
  name: Example Cloud
  url: https://example.com/pricing
  pricing: Free tier, then paid
  required: true
settings:
  api_key: {type: secret, title: Example Cloud key}
```

- `execution`: `remote` when the deciding happens on a server, `hybrid` when your plugin also works on the PC (reading the video locally, then sending a summary), `local` when nothing leaves the PC.
- `network`: every host your code connects to. Needs the `network` permission.
- `sends`: what kind of data leaves (`video`, `video_link`, `audio`, `frames`, `transcript`), to whom, and when it depends on a setting. Required for `remote` and `hybrid`; the validator refuses a `local` pipeline that lists it.
- `service`: the account or payment the service needs, with `required` saying whether the pipeline works without it.

The user sees each `sends` entry as a warning, word for word, on the listing card, on its page, before installing (where they must tick that they understand data leaves the PC), on the installed plugin, and on the video in the Generate bar when they choose the pipeline:

> ⚠ Sends your video's transcript to Example Cloud
>
> ⚠ Sends frames from your video to Example Cloud, when the "Look at the picture" setting is on

Declare honestly and completely. Clips Kitty can't check it at run time, and a user who finds an undeclared upload has every reason to report the plugin for the block list ([Security](security.md)).

## Keys

Give the user's key a `secret` setting. They enter it once, in Marketplace › Installed; it is stored encrypted for their Windows account and reaches only your plugin's process, in the environment variable `CLIPSKITTY_SECRET_<NAME>` (here `CLIPSKITTY_SECRET_API_KEY`). Read it with the SDK:

```python
key = job.secret("api_key")
if not key:
    job.fail("Add your Example Cloud key in Marketplace › Installed.")
```

A secret never goes in `job.json`, never in a job's settings, and never travels to a render PC. Other software running as the user can read the store, so treat it as a convenience, not a vault.

Clips Kitty's own cloud AI keys (OpenRouter and the like, set in Settings → AI) are never handed to a plugin. If your pipeline uses OpenRouter, the user gives your plugin its own key.

## Sending data

What you send is up to your code; keep it to what you declared, and as little as works:

- The **transcript** (`transcript.read`) is often enough for a language model to pick moments, and is much smaller than video.
- **Frames**: sample a few with FFmpeg (`ffmpeg` permission, `job.tools.ffmpeg`), downscale, send those.
- **Audio** or **video**: only when the model needs it; say so with `sends`.
- **The video's link** (`video_link`): when your service downloads public videos itself. Local files have no link.

Use https. Set timeouts, retry a little, and fail with a sentence the user understands (`job.fail("Example Cloud didn't answer. Try again later.")`): it is shown in the job's log. Report progress while you wait (`job.progress(0.5, "Waiting for Example Cloud")`). A cancelled job stops your process.

## Charging for it

Clips Kitty takes no part in payments: no fees, no revenue share, no checkout. Charge for your service however you like, on your own site. `service.url` and `service.pricing` are shown to the user so they know before installing; `links.funding` can point at a sponsor page.

## A remote-only pipeline

A pipeline can do nothing locally but send the video's link or transcript and turn the answer into ranges. It still needs Python on the PC to run its small client (or ship its own executable as `run.command`). See [Pipeline development](pipeline-development.md) for the contract and [Example pipeline](example-pipeline.md) for a complete local one to adapt.

See also: [Permissions](permissions.md), [Local APIs](local-apis.md).
