# Where the AI runs

**Local first. Cloud when you need it. OpenRouter is the easiest cloud path.**

Clips Kitty uses AI for two jobs: the language model that picks clips, writes
titles and descriptions, learns about creators, translates, edits clips on
request and answers the assistant; and transcription. Each can run in one of
three places, chosen in **Settings → AI**:

1. **Ollama and Whisper on your own PC** (local AI). The default and the first
   choice.
2. **OpenRouter** (recommended cloud AI), on your own OpenRouter key.
3. **A direct provider API** (advanced): OpenAI, Anthropic Claude, Google
   Gemini, xAI Grok, Meta, DeepSeek or Qwen, on your own key with that provider.
   Experimental: a paid ChatGPT plan instead of an OpenAI key (see
   [Use a plan you already pay for](#use-a-plan-you-already-pay-for-experimental)).

Every cloud option is **bring your own key**. The key is yours and so is the
bill: the provider charges your account for what you use. Clips Kitty has no key
of its own, sells no credits, and runs no server in between; requests go from
your PC straight to the provider.

---

## 1. Ollama and Whisper: local AI

Out of the box everything runs on your computer. Ollama runs the language model
and Whisper does the transcription. There is no API key, nothing is sent
anywhere, and there is nothing to pay. On a computer with a capable graphics
card this is the best fit for Clips Kitty, and it stays the default.

The Models page lists the local models, recommends one for your graphics card,
and warns you when a model is too big for it.

## 2. OpenRouter: recommended cloud AI

If your PC cannot run the models well (an older laptop, a graphics card with
4 GB, no graphics card, or a small always-on mini PC running Watched channels),
OpenRouter is the easiest way to run the AI in the cloud instead.

**Why OpenRouter first.** It is one cloud connection to many AI models and
providers (Claude, GPT, Gemini, Muse Spark, Llama, Qwen and more) through one
key, so you can compare them and switch between them without opening a separate
account for each. If a provider serving the model you chose is down, OpenRouter
can send the request to another provider of the same model; that is on by
default on OpenRouter's side. None of this makes it better or cheaper than going
to a provider directly; it makes it simpler.

### Set it up

1. In **Settings → AI**, choose **🚀 OpenRouter**, then **Open OpenRouter**, and
   sign in or create an account.
2. Create an API key there. Add credit, or start with OpenRouter's free models.
3. Paste the key into Clips Kitty and press **Save**. Clips Kitty checks it with
   OpenRouter before keeping it.
4. Pick a **text model**. The list comes live from OpenRouter, for your account
   (the same list the website uses), and only shows models that can do this job
   (they can answer in JSON). It can be searched, grouped by who makes each model
   or sorted by lowest price, and each line shows the context size and the input
   and output price per million tokens. Under it, the model in use shows every
   price OpenRouter lists for it (cached input, reasoning, per request and so on),
   what it can do, and a link to its OpenRouter page.
5. Optional: **Test connection** checks the key and the model without spending
   anything.

From then on every clip job uses that model. To try another one, pick it from
the same list; nothing else changes. Choosing **⭐ Ollama** again returns to
local AI.

For transcription, choose OpenRouter under **Transcription** and pick a **voice
model** from OpenRouter's live speech catalogue. The key is shared, so you only
paste it once. Captions need the time of every word, and OpenRouter's catalogue
does not say which models give it, so:

- The Whisper models known to return word timings are listed first and chosen
  straight away.
- Any other voice model is checked when you pick it: Clips Kitty sends it a
  three-second test clip once (a tiny fraction of a cent on your key) and only
  switches to it if word timings come back. If they don't, it says so and
  nothing changes.

Prices come from OpenRouter each time the lists are loaded, and are kept for a
few hours at most; the card says when they were fetched, and **Refresh models**
fetches models and prices again. If they cannot be fetched, no prices are shown
rather than old ones. A model you chose that OpenRouter no longer offers is
flagged, never swapped for another; replacing your key fetches the lists again.

**Voice model prices.** OpenRouter lists a speech price with no unit. A token-
billed model is shown per million tokens, exactly. For the others, the card shows
OpenRouter's number as listed, and adds "≈ per hour of audio (estimate)" only
where the figure is clearly per second (whisper-1: 0.0001, which is OpenAI's own
$0.006 a minute, so about $0.36 an hour). Larger figures, such as Microsoft's MAI
Transcribe, are not guessed at; follow the link to OpenRouter for the exact rate.
The website shows them the same way.

### What it costs

- OpenRouter charges the underlying provider's own price for each model, with
  no markup on inference, and a fee when you buy credits. See
  [openrouter.ai/models](https://openrouter.ai/models) for current prices.
- Models whose id ends in `:free` cost nothing but are limited per day (50
  requests a day, or 1,000 once you have bought $10 of credit). A long video
  sends a few dozen requests, so the free allowance can run out partway.
- As a rough guide to volume: a two-hour VOD is analysed in five-minute chunks,
  plus one request for titles for each batch of clips.

### Watched channels on a small PC

This is what the cloud option is for. With OpenRouter doing the AI work, a
low-spec always-on PC can watch channels, download new videos, transcribe (on
the PC, or online), clip, render and publish, without needing a graphics card
big enough for the language model. Rendering and face tracking still run on the
PC.

### If something goes wrong

| What you see | What it means |
|---|---|
| "OpenRouter didn't accept the API key" | The key is wrong, was deleted, or was pasted with a missing character. Create a new one. |
| "out of credit or over its spending limit" | Top up your OpenRouter credit, or raise the key's limit. |
| "rate limiting your key" | Too many requests for now, or a free model's daily limit was reached. Wait, or pick a paid model. |
| "doesn't offer that model to your key" | No provider on OpenRouter currently serves that model with JSON output. Pick another model. |
| "isn't answering right now" | OpenRouter or the model's providers are down. Try again later. |

A failing provider stops the job with that message. Clips Kitty never switches
to another provider, or back to the local model, on its own.

### Attribution

Every request made to OpenRouter carries three headers:

```
HTTP-Referer: https://colingpt9.github.io/clips-studio/
X-OpenRouter-Title: Clips Kitty
X-OpenRouter-Categories: video-gen
```

They credit the usage to Clips Kitty in OpenRouter's app rankings. They identify
the app, not you. `video-gen` is the category in OpenRouter's Creative section
that fits; a bare `creative` is not a category, and OpenRouter drops values it
does not recognise. They are built in one place, `llm/providers/openrouter.py`,
and put on every OpenRouter request (chat, models, key check, transcription and
every retry). They are the app's identity, not a setting, and a test fails if
any request goes out without them. The app never makes a request of its own to
add to them: every one comes from something a user asked for.

## 3. Direct provider APIs (advanced)

OpenAI, Anthropic Claude, Google Gemini, xAI Grok, Meta (Muse Spark), DeepSeek
and Qwen are all available directly, under **Direct provider API**. Choose one if you
specifically want that provider: your account, limits and billing with them, or
something only their API offers.

| Provider | AI work | Transcription | Get a key |
|---|---|---|---|
| OpenAI | Yes (Responses API) | Yes: whisper-1 | platform.openai.com/api-keys |
| Anthropic Claude | Yes | No (no audio input) | console.anthropic.com |
| Google Gemini | Yes | Not yet | aistudio.google.com/apikey |
| xAI Grok | Yes | Yes: grok-voice-transcribe | console.x.ai |
| Meta (Muse Spark) | Yes: Meta's own Model API | No (no word timings) | dev.meta.ai |
| DeepSeek | Yes | No | platform.deepseek.com |
| Qwen (Alibaba Cloud) | Yes, through Model Studio | Not yet | Alibaba Cloud Model Studio console |

- **Qwen keys belong to one region.** An Alibaba Cloud Model Studio key only
  works in the region it was made in. When you save one, Clips Kitty tries it in
  Singapore, US (Virginia), China (Beijing) and China (Hong Kong), in that order,
  keeps the region that accepts it, and shows it beside the key. Keys from the
  Frankfurt and Tokyo regions need a workspace-specific address and don't work
  here yet. Qwen is also on OpenRouter (`qwen/…`), on the OpenRouter key.
- **DeepSeek** is a company based in China; what you send goes to it under
  DeepSeek's own terms. Its API answers in JSON mode but not with a strict JSON
  schema, which the app handles. DeepSeek models are also on OpenRouter.

- **Muse Spark** is also on OpenRouter as `meta/muse-spark-1.3`, on the same
  OpenRouter key. Meta's `-contributor` models are cheaper, but Meta may use
  what you send to improve its products; the model list says so beside them.
- **Llama** is on OpenRouter (`meta-llama/…`). Meta's own Llama API was wound
  down in 2026.
- **Transcription needs word timings**, for captions, filler-word cuts and the
  editor's word tools. That is why OpenAI transcription uses whisper-1 and not
  the newer transcribe models, and why Meta and Anthropic are not offered for it.

## Use a plan you already pay for (experimental)

People ask whether a Claude, ChatGPT or Google AI subscription can run Clips
Kitty instead of an API key. Only where the provider officially lets another app
use the plan, through the provider's own software, with the sign-in held by that
software and never seen by Clips Kitty. As of September 2026:

| Plan | Can Clips Kitty use it? | Why |
|---|---|---|
| **ChatGPT** (Plus, Pro, Business…) | **Yes, experimental** | OpenAI documents its Codex SDK for building Codex into other apps, with "Sign in with ChatGPT" run by Codex itself. |
| **Claude** Pro or Max | No, use a Claude API key | Anthropic: it does not permit third-party developers to offer Claude.ai login or to route requests through Free, Pro or Max plans ([legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)). |
| **Google AI** Pro or Ultra | No, use a Gemini API key | Google's Antigravity terms make using the service from other products a breach, and plan benefits apply only in Google AI Studio's own interface. The plans do include monthly Google Cloud credits that can pay for Gemini API use on your key ([Google AI plans](https://ai.google.dev/gemini-api/docs/google-ai-plans)). |

**The ChatGPT plan** is under Settings → AI → Direct provider API → **OpenAI /
ChatGPT plan**:

1. Choose **ChatGPT plan** and **Sign in with ChatGPT**. OpenAI's sign-in page
   opens in your browser; if it doesn't come back, **Use a code instead** shows a
   code to type on OpenAI's page.
2. Pick a model. The list is the one your plan offers.
3. Clip a video.

- **Free accounts can't use it.** OpenAI offers Codex on free ChatGPT accounts
  only in its own desktop app for now, and refuses requests from other apps. It
  needs a paid plan.
- **Usage comes from your plan's Codex limits** (a 5-hour and a weekly window on
  most plans), shared with your own ChatGPT and Codex use. The card shows how
  much is used, as OpenAI reports it. A two-hour video is a few dozen requests.
- **It never spends ChatGPT credits.** Once a plan's included usage is used up,
  OpenAI spends any credits you have bought, and it gives other apps no switch to
  prevent that. So before every request Clips Kitty checks the plan's limits and
  stops the job at 100%, saying when the limit resets. A request already running
  when the limit is reached may be finished by OpenAI.
- **Watched channels only use it if you allow it** ("Let Watched channels use
  this plan", off by default). OpenAI calls an API key the right way to
  authenticate automation. Videos you clip yourself, including **Clip this** on
  the Watch page, don't need the switch.
- **Not for the assistant or transcription.** The assistant needs tool calling,
  which Codex only offers other apps experimentally; Codex doesn't transcribe.
  Both stay on Ollama and Whisper, or an API key.
- **Sign out** removes the sign-in from this PC and asks OpenAI to cancel it; if
  that request fails, it is still removed here.

How it is kept to what OpenAI documents:

- It runs OpenAI's own Codex runtime, unmodified, which tells OpenAI it is Clips
  Kitty. It never pretends to be Codex.
- It keeps its own Codex folder (in the app's data folder) and never reads or
  reuses a Codex login you already have. API-key environment variables are
  blanked for it, so only the plan is used.
- Codex stores the sign-in itself, encrypted, with its key in Windows Credential
  Manager. Clips Kitty never handles a token, and nothing about it goes into the
  settings file, logs, bug reports or the MCP server.
- Codex can't run commands, search the web, load plugins, update itself or send
  analytics here. Each request is a one-off, read-only task in an empty folder,
  its answer held to the job's JSON format.
- OpenAI's own error text is never shown or stored (it can quote part of an
  internal key); the app says which case it was in its own words.

**Not in the installer or the Microsoft Store build yet.** The runtime is about
400 MB. Running from source, install it with
`pip install -r requirements-chatgpt.txt`; without it the option doesn't appear.
It stays experimental until someone with a paid plan has run a full clip job on
it.

---

## For everyone: what leaves your PC

- **For the AI work:** the transcript text and the app's instructions. The
  video itself is never sent.
- **For online transcription:** the video's audio, as short speech-quality MP3
  parts. Not the picture.
- **To OpenRouter, additionally:** the app's name, website and category (above).

Each provider handles what it receives under its own privacy policy, which can
differ between free and paid plans. The full list of everything the app sends
anywhere is in the [privacy policy](https://colingpt9.github.io/clips-studio/privacy.html).

**Your key** is stored in the app's credential store (encrypted with Windows
DPAPI, tied to your Windows account; an owner-only file elsewhere), the same as
the publishing keys. It is checked with the provider before it is kept, sent
only in request headers, and never shown again: the app only displays its last
four characters. It never goes into a prompt, a log, a bug report, the settings
file, or anything an MCP client can read.

## For contributors: adding a provider

Providers live in `llm/providers/`:

- `catalog.py` lists them, in the order the settings show them.
- A provider is a `ProviderSpec`: its address, how it takes a key, where to get
  one, its pricing page, a one-line privacy note, which models to offer, and
  its `tier`: 2 for the recommended cloud path, 3 for a direct provider.
- `adapters/` has one file per wire format: `chat_completions` (OpenRouter, xAI,
  Meta, DeepSeek, Qwen), `openai_responses`, `anthropic_messages`, `gemini_generate`.
- A provider whose keys only work in one region lists its `regions`; a new key
  is tried in each and kept with the one that accepts it (Qwen does this).

**A provider that speaks OpenAI-compatible chat completions is one new
`ProviderSpec` in `catalog.py` and nothing else.** The API, the settings card and
the pipeline read the catalogue, and its `tier` puts it in the right place. A
provider with its own format adds one adapter file with the same four functions
(`generate`, `chat`, `list_models`, `check_key`). Add its key and pricing hosts
to `EXTERNAL_ALLOWED` in `ui/src/main/index.ts` so their links open.

The rules any new provider has to keep, and which the tests check:

- Every request goes through `llm/providers/http.send`, which puts the key in a
  header, never a URL, and turns the provider's errors into plain words.
- No key of ours, anywhere. With no key saved, nothing is sent.
- A failure is reported, never retried against something else.
- Local stays the default and is sent exactly what it always was.

### Adding a plan sign-in

Plans signed in to instead of a key live in `llm/signin/`: `base.py` is the
`SignInProvider` interface (start a sign-in, status, limits, models, sign out,
the backend a job uses, and the check before a job), `catalog.py` lists them, and
`chatgpt.py` is the first. A new one is one class and one line in the catalogue;
the `/ai/signin/{id}` routes and the settings card read it from there, and it sits
under its provider's entry (`group`).

It is only accepted if:

- the provider's terms allow a third-party app to use the plan, and the pull
  request links the page that says so;
- it uses the provider's own documented, unmodified SDK or sign-in flow, which
  holds the sign-in;
- it takes no cookies, no tokens from another app, and calls no private
  endpoint;
- it never spends beyond the plan's included usage without the user's choice;
- unattended jobs use it only when the user has allowed it.

Known gaps, open for pull requests: Gemini online transcription (it needs
Gemini's Files API upload), and Google's newer Interactions API for Gemini.
