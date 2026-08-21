# Examples

Small runnable programs, each showing one way into the codebase. They are
meant to be read and then edited — copy one, change it, see what happens.

Run them from the repo root.

| | What it shows |
|---|---|
| [`score_a_transcript.py`](score_a_transcript.py) | Clip selection on its own — no video, no rendering, seconds per run |
| [`drive_the_api.py`](drive_the_api.py) | The whole pipeline over HTTP, exactly as the desktop app drives it |
| [`fake_backend.py`](fake_backend.py) | A deterministic stand-in for the model, so scoring runs without Ollama |

## Start here: scoring without a video

```
python examples/score_a_transcript.py --fake
```

No model, no footage, no wait. It scores the sample transcript in
[`../tests/assets/`](../tests/assets/) and prints both the clips it picked
*and* the ones it dropped with the reason.

Drop `--fake` to use the real model from `config/settings.yaml`, which is
what you want when the thing you are changing is the prompt in
`config/prompts/`:

```
python examples/score_a_transcript.py
```

Re-score a stream you have already processed — no re-download, no
re-transcribe:

```
python examples/score_a_transcript.py --transcript data/transcripts/<id>.json
```

That is the loop worth internalising. Selection changes are cheap to test
this way and expensive to test any other way.

## The engine is a server; the app is just a client

```
python main.py serve                       # one terminal
python examples/drive_the_api.py           # another
```

With no arguments it only reports readiness. Give it something to do:

```
python examples/drive_the_api.py --url https://www.twitch.tv/videos/123456
python examples/drive_the_api.py --file "D:/footage/stream.mp4"
```

It submits the job, follows progress over the WebSocket, and lists the
clips. Nothing in it is privileged — the Electron UI makes the same calls.
If you want Clips Kitty to do something on a schedule, or from a bot, or
across a folder of old VODs, this file is the starting point.

## Writing your own

The engine imports cleanly from the repo root, so a script only needs the
root on `sys.path` — each example does this in its first few lines. From
there:

```python
from analysis.highlights import find_highlights   # pick clips from a transcript
from transcription.transcriber import transcribe  # audio -> segments
from core.state import StateDB                    # the library database
from llm.registry import create_backend           # the configured model
```

Two things to respect, both of which will bite otherwise:

- **Never call `ffmpeg` by bare name.** Use `core.binaries.ffmpeg()`. An
  installed copy ships its own FFmpeg in `_internal/` and has no system one.
- **Never hardcode a data path.** Use `core.paths.resolve_data_dir(config)`.
  A dev checkout writes into the repo; an installed copy writes into
  `%LOCALAPPDATA%`, because Program Files is not writable.

Adding a language, model, platform or export format is a smaller change than
it looks — [`../docs/EXTENDING.md`](../docs/EXTENDING.md) has each one.
