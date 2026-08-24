# Clips Kitty Web

A browser-only taster for [Clips Kitty](https://github.com/ColinGPT9/clips-studio).
Point it at a recording, it finds the moments worth clipping and cuts them out
as landscape MP4s.

It exists to answer one question before anyone downloads anything: *is the
scoring actually any good?* Everything it deliberately cannot do — vertical
reframing with subject tracking, burned captions, frame-accurate cuts,
YouTube and Kick links, scheduling, uploads — is what the desktop app is for.

## The shape of it

**There is no server.** This is a static export. Every request goes from the
visitor's browser straight to OpenRouter or Twitch:

- **Inference** is billed to the visitor's own OpenRouter account, obtained
  through OAuth PKCE. Their key lives in their `localStorage` and is never
  sent to us — there is no API route here that could receive it even by
  accident.
- **Video** comes from Twitch's CDN to their browser, or from a file on their
  own disk. Nothing is uploaded.

So running this costs the project nothing: no inference, no bandwidth, no
compute.

## Local development

```bash
npm install     # also copies the ffmpeg.wasm core into public/ffmpeg
npm run dev
```

Sign-in will not attribute correctly on `localhost` — OpenRouter excludes
localhost traffic from its app marketplace. The flow still works; the app page
just is not created.

```bash
npm run typecheck
npm run lint
npm run build   # static export into out/
npx serve out   # `next start` does not work with output: export
```

## Deploying

A **separate Vercel project** from `whop-app/`, with **Root Directory** set to
`web`. It is deliberately not a route inside the Whop app: that one is a Whop
marketplace app with its own SDK and review process, and this needs its own
origin anyway, because OpenRouter builds an app page from the `HTTP-Referer`
we send.

Nothing else to configure — no environment variables and no secrets, because
there is no server and no key of ours anywhere.

### What it costs to host

Almost nothing, and deliberately so. The VOD goes from Twitch or Kick straight
to the visitor's browser, and inference goes from their browser straight to
OpenRouter — neither passes through us. What we serve is the page (~0.6 MB)
and, only if they cut a clip or open a VOD, the ffmpeg core (~10 MB gzipped
from 30 MB raw). Against Vercel's 100 GB/month Hobby transfer that is roughly
10,000 first-time visitors, and repeat visits cost nothing.

`vercel.json` is what protects that. Files under `_next/static` are
content-hashed and Vercel caches them forever on its own, but `public/ffmpeg/`
is not, so without an explicit rule the 30 MB core would be re-fetched on
every visit and the budget above would be wrong by an order of magnitude.

It is cached for 30 days with `stale-while-revalidate` rather than marked
`immutable`, and the difference matters: `ffmpeg-core.wasm` keeps the same
filename across `@ffmpeg/core` upgrades, so `immutable` would strand returning
visitors on a stale binary with no way to bust it. Thirty days gets
essentially all of the bandwidth saving — after that the browser revalidates
and gets a 304 costing no transfer — while still healing itself when the core
is upgraded.

> Not viable, and worth recording so nobody re-treads it: **Cloudflare Pages
> caps a single asset at 25 MiB** and the wasm is 29.3 MiB, so it rejects the
> deployment outright. **GitHub Pages** would now work — the COOP/COEP
> objection died when this moved to the single-threaded ffmpeg core — but it
> would need a Pages Action to build and a `basePath` if served from a
> subpath, and that site is for the marketing pages.

## How a run works

1. **Audio.** ffmpeg cuts it into **10-minute 16 kHz mono WAVs**, handled one
   at a time, so peak memory is one segment (~57 MB) no matter how long the
   recording is — there is no duration limit. A local file is *mounted* via
   WORKERFS and read lazily rather than copied; a Twitch or Kick VOD uses the
   cheapest audible rendition, so transcribing a long stream pulls tens of
   megabytes rather than gigabytes.
2. **Transcribe.** One request per segment. ffmpeg only *copies* the audio
   (`-c:a copy`) and the browser decodes each segment, so the platform's own
   decoder does the work rather than a wasm thread — 0.58s vs 1.27s natively
   on a 38-minute source, and half the bytes. Ten minutes is ~9 MB, under the
   25 MB upload cap — the old 60-second chunk came from misreading the limit
   as 60 seconds of *audio* when it is 60 seconds of *processing*, and cost
   10x the requests. Offsets accumulate **real sample counts**, not
   `index × 600`: the segment muxer cuts on packet boundaries, so segments are
   600 seconds *ish* and nominal offsets drift into mis-cut clips.
3. **Score.** `lib/score.ts` is a port of `analysis/highlights.py`, using the
   same prompt. See *How much of the real pipeline this is* below — it is one
   stage, not the whole thing.
4. **Cut.** ffmpeg.wasm with `-c copy` — no re-encoding. On the Twitch path
   only the segments covering the chosen moment are fetched.

## How much of the real pipeline this is

Less than it looks, and the difference is worth stating plainly rather than
discovering from a disappointing demo.

**Transcription is a different engine.** The desktop app runs
`faster_whisper.WhisperModel` locally with `word_timestamps=True` — word-level
timing is what makes the burned captions sync — plus transcript repair. This
calls OpenRouter's hosted `whisper-large-v3-turbo` and gets segment-level
timestamps. Same model family, different runner, coarser output.

**Scoring is three of five channels.** The desktop entry point is
`analysis/fusion.py`, not `highlights.py`. Ported here: the text pass
(`find_highlights`), the audio channel (`analysis/audio_features.py` →
`lib/audioEvents.ts`) and the weighted fusion that makes it count
(`_fuse` → `lib/fuse.ts`), plus the trending bonus. Between them that is
text 0.30 + audio 0.20 + engagement 0.10 of the score.

Not ported, because a browser cannot: the **visual** channel (motion, scene
cuts — needs video decode) and the **reaction** channel (active-speaker
detection — needs a model). Both fall back to the same neutral 50 the Python
uses for an unmeasured channel, so they contribute a constant and cannot
reorder anything. Also absent: the signal-peak window pass, the rerank pass,
creator intelligence and Twitch chat-replay hype.

So expect different picks from the desktop app, with the gap widest on
gameplay, where the best moment is something that happens rather than
something said. That is a fair thing for a taster to be — but do not let the
copy imply parity.

**Both ports are parity-tested.** `_fuse` matches to the float on the adaptive
low-speech path; the audio features agree on which seconds are flagged and
overlap 18/20 on the loudest seconds of a real 38-minute VOD. The small
residue is tie-ordering: numpy's `argsort` is an unstable sort, so tied values
(common in `burst`, an integer count) get arbitrary ranks. `lib/audioEvents.ts`
averages tied ranks instead, which is deterministic and sits at the centre of
what numpy picks from. Analysis cost for those 38 minutes: **137 ms**.

## Things worth knowing before changing it

**The prompt is duplicated.** `lib/score.ts` embeds
`config/prompts/score_clips.txt` verbatim, because a static bundle has no
server to read the repo from. `tests/test_web_prompt_sync.py` fails the build
if the two drift. If you improve the prompt upstream, copy it here — do not
edit the `.txt` to match this.

**ffmpeg's worker and core are self-hosted, and must stay that way.**
`scripts/copy-ffmpeg-core.mjs` copies five files into `public/ffmpeg/`: the
**ESM** core (the loader builds a `type: "module"` worker, and the UMD build
has no ES export, so shipping umd meant ffmpeg never started at all), plus
`worker.js`, `const.js` and `errors.js`. Those three are served raw and loaded
via `classWorkerURL` specifically to keep the bundler away from them —
Turbopack cannot statically resolve the `await import(coreURL)` inside
`worker.js` and replaces it with a stub that throws "Cannot find module as
expression is too dynamic". If the engine ever stops starting, check those
first.

**No COOP/COEP, on purpose.** ffmpeg.wasm only needs `SharedArrayBuffer` when
it runs multi-threaded, and stream-copying is I/O rather than compute. Adding
re-encoding later would mean `@ffmpeg/core-mt` and cross-origin isolation —
ask first whether it belongs in a browser at all.

**Twitch will break periodically.** The client-id and persisted-query hash in
`lib/twitch.ts` are the ones Twitch's own web player uses. They are not
promised to anyone. When they change, fail loudly and point at the desktop
app, which uses yt-dlp and is maintained against exactly this churn.

**YouTube is not coming.** Google's video servers send no cross-origin
headers, so a browser cannot fetch from them — this is enforced by the
browser. Third-party converter sites are not a workaround: they are websites
rather than APIs, are Cloudflare-fronted with no CORS headers of their own,
break constantly, and would put someone else's legal and advertising baggage
on this domain. A creator clipping their own video can export it from YouTube
Studio and drop the file in.

## Limits

| | |
|---|---|
| Input length | 60 minutes (`MAX_DURATION_SECONDS`) |
| File size | ~1.2 GB (`MAX_FILE_BYTES`) |
| Output | Landscape, original aspect, keyframe-aligned cuts |
| Sources | A local file, or a Twitch VOD link |

Both caps are conservative guesses about where a browser tab runs out of
memory, not measured ceilings. If you have measured one, move it and say so.
