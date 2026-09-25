# Where the AI runs

Clips Kitty is local first. Out of the box, everything runs on your own PC:

- **Ollama** runs the language model that picks clips, writes titles and
  descriptions, learns about creators, translates, and answers the assistant.
- **Whisper** transcribes the video.

That costs nothing, needs no account, and sends nothing anywhere. It is the
default, and it stays the default.

## Bring your own key

Some PCs cannot run the models: an old laptop, a graphics card with 4 GB, a small
always-on mini PC watching channels. For those, **Settings → AI** can hand the
work to a cloud provider instead, using **an API key from your own account** with
that provider.

- **It is your key and your bill.** The provider charges your account for what
  you use, at its own prices. Clips Kitty has no key of its own, sells no
  credits, and runs no server in between: requests go from your PC straight to
  the provider.
- **Two separate choices.** The AI work and the transcription can each stay on
  this PC or go online: a fast PC with no GPU might keep Whisper local and send
  the AI work out, or the other way round.
- **Everything the local model did moves with it.** With a cloud model chosen,
  clip picking, re-ranking, titles and descriptions, creator learning, clip
  edits, translation and the assistant all use it.
- **Nothing falls back.** If the provider fails (bad key, out of credit, rate
  limited, model gone), the job stops and says so in plain words. It never
  switches to another provider or to the local model behind your back.
- **Switching back is one click:** choose "This PC" in the same dropdown.

### Providers

| Provider | AI work | Transcription | Get a key |
|---|---|---|---|
| OpenRouter | Yes: one key, hundreds of models (Claude, GPT, Gemini, Llama, Muse Spark, …) | Yes: Whisper large-v3 / turbo / whisper-1 | openrouter.ai/keys |
| OpenAI | Yes (Responses API) | Yes: whisper-1 | platform.openai.com/api-keys |
| Google Gemini | Yes | Not yet | aistudio.google.com/apikey |
| Anthropic Claude | Yes | No (no audio input) | console.anthropic.com |
| xAI Grok | Yes | Yes: grok-voice-transcribe | console.x.ai |
| Meta (Muse Spark) | Yes: Meta's own Model API | No (no word timings) | dev.meta.ai |

- **Muse Spark** is available two ways: directly with a Meta Model API key
  (`muse-spark-1.3`), or through OpenRouter (`meta/muse-spark-1.3`). Meta's
  `-contributor` models are cheaper, but Meta may use what you send to improve
  its products; the model list says so beside them.
- **Llama** is on OpenRouter (`meta-llama/llama-4-maverick` and others). Meta's
  own Llama API was wound down in 2026.
- **Only models that can do the job are listed.** For the AI work, that means
  models that can answer in JSON. For transcription, it means models that
  return word timings: captions, filler-word cuts and the editor's word tools
  need them. That is why OpenAI transcription uses whisper-1 and not the newer
  transcribe models, and why Meta and Anthropic are not offered for it.

### What it costs

Every provider publishes its own prices; Settings → AI links to each one. As a
rough guide to volume, a long stream is transcribed in parts and analysed in
five-minute chunks, so a two-hour VOD is a few dozen AI requests plus titles
for its clips. OpenRouter's `:free` models have small daily limits that one long
video can use up.

### What leaves your PC

- **For the AI work:** the transcript text and the app's instructions. The
  video itself is never sent.
- **For online transcription:** the video's audio, as short speech-quality MP3
  parts. Not the picture.
- **To OpenRouter, additionally:** the app's name, website and category
  (below). They identify the app, not you.

Each provider handles what it receives under its own privacy policy, which can
differ between free and paid plans. The full list of everything the app sends
anywhere is in the [privacy policy](https://colingpt9.github.io/clips-studio/privacy.html).

### Your key

Stored in the app's credential store (encrypted with Windows DPAPI, tied to your
Windows account; an owner-only file elsewhere), the same as the publishing keys.
It is checked with the provider before it is kept, sent only in request headers,
and never shown again: the app only ever displays its last four characters.
It is never put in a prompt, a log, a bug report, the settings file, or anything
an MCP client can read.

## OpenRouter attribution

Every request made to OpenRouter carries three headers:

```
HTTP-Referer: https://colingpt9.github.io/clips-studio/
X-OpenRouter-Title: Clips Kitty
X-OpenRouter-Categories: video-gen
```

They credit the usage to Clips Kitty in OpenRouter's app rankings. `video-gen`
is the category in OpenRouter's Creative section that fits; a bare `creative`
is not a category, and OpenRouter drops values it does not recognise. They are
built in one place, `llm/providers/openrouter.py`, and put on every OpenRouter
request (chat, models, key check, transcription and every retry). They are the
app's identity, not a setting. A test fails if any request goes out without them.

## For contributors: adding a provider

Providers live in `llm/providers/`:

- `catalog.py` lists them, in the order the settings show them.
- A provider is a `ProviderSpec`: its address, how it takes a key, where to get
  one, its pricing page, a one-line privacy note, and which models to offer.
- `adapters/` has one file per wire format: `chat_completions` (OpenRouter, xAI,
  Meta), `openai_responses`, `anthropic_messages`, `gemini_generate`.

**A provider that speaks OpenAI-compatible chat completions is one new
`ProviderSpec` in `catalog.py` and nothing else.** The API, the settings card and
the pipeline read the catalogue. A provider with its own format adds one adapter
file with the same four functions (`generate`, `chat`, `list_models`,
`check_key`). Add its key and pricing hosts to `EXTERNAL_ALLOWED` in
`ui/src/main/index.ts` so their links open.

The rules any new provider has to keep, and which the tests check:

- Every request goes through `llm/providers/http.send`, which puts the key in a
  header, never a URL, and turns the provider's errors into plain words.
- No key of ours, anywhere. With no key saved, nothing is sent.
- A failure is reported, never retried against something else.
- Local stays the default and is sent exactly what it always was.

Known gaps, open for pull requests: Gemini online transcription (it needs
Gemini's Files API upload), and Google's newer Interactions API for Gemini.
