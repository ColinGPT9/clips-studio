# Partner Center paste sheet — Clips Kitty 0.1.2

Every field, in the order Partner Center asks for it. Open this beside the
dashboard and work down. [MSSTORE.md](MSSTORE.md) explains *why* for anything
that looks odd; this file is just the values.

**Start:** Partner Center → Apps and games → Clips Kitty → **Start submission**

---

## 1. Pricing and availability

| Field | Value |
|---|---|
| Markets | All markets |
| Visibility | **Available and discoverable in the Store** |
| Pricing | **Free** |
| Free trial | No free trial |
| Publish date | **Manual publish** — do not let it go live automatically |

Manual publish matters: it lets you read the certification report before
anyone can install it.

## 2. Properties

| Field | Value |
|---|---|
| Category | **Multimedia design** |
| Subcategory | Video editing |
| Privacy policy URL | `https://colingpt9.github.io/clips-studio/privacy.html` |
| Website | `https://colingpt9.github.io/clips-studio/` |
| Support contact | `https://github.com/ColinGPT9/clips-studio/issues` |

**Product declarations — the two that are easy to miss:**

- ☑ **This product uses live generative AI** — required by policy 11.16,
  because clip titles are written by a language model from user input. The
  in-app Feedback Hub is the reporting route it also requires.
- ☑ **This product uses a third-party purchase API** — the donate button opens
  PayPal. Policy 10.8.2 requires the declaration, and requires making clear
  Microsoft is not the fundraiser. Store builds open it in the system browser
  rather than in-app, which is the route the policy names.

**System requirements:**

| | Minimum | Recommended |
|---|---|---|
| Memory | **16 GB** | **16 GB** |
| everything else | Not specified | Not specified |

Memory is the only one to fill in. 16 GB is not padding: below it a video is
analysed and then rendering fails, which is in KNOWN-ISSUES.md and would
otherwise become one-star reviews.

Leave Video memory, Processor and Graphics blank on purpose. A GPU makes the
app much faster but is not required, and anything entered under Minimum becomes
a hard requirement: the Store warns the customer and blocks them from rating
the app. "NVIDIA GPU" there would exclude every AMD and Intel user, all of whom
can run it. The description already says a graphics card helps, which is the
honest version of that claim.

## 3. Age ratings

Complete the IARC questionnaire. Every answer is **No** for Clips Kitty. The
two that need thought rather than reflex:

- **Does the app allow users to share content?** No. It writes files to the
  user's own disk. It posts nowhere. The optional YouTube upload path is
  disabled, command-line only, and needs Google credentials the user creates.
- **Does it display user-generated or uncurated content?** The user's own
  video, to themselves. Nothing from other users.

Expect PEGI 3 / ESRB Everyone.

## 4. Packages

Upload: **`release\ClipsStudio-1.1.2-x64.appx`** (7.60 GB)

The unsigned one in `release/`, not the signed copy in `build/msix-signing/`.
Microsoft re-signs after certification; the local signature exists only so the
package could be installed for testing.

Verified before upload:

```
Identity Name   ClipsStudio.ClipsStudio
Publisher       CN=82A1C822-C6B7-41D5-889B-160627060939
Version         1.1.2.0
Architecture    x64
Capabilities    runFullTrust   (the only one)
```

## 5. Store listing

Copy from [store-listing.md](store-listing.md) — product name, short
description, full description, feature bullets, and the seven search terms.

**Screenshots** — `docs/store-screenshots/`, all four:

| File | Shows |
|---|---|
| `1-dashboard.png` | the URL box, options, processed list, live CPU/RAM/GPU |
| `2-queue.png` | a completed job |
| `3-models.png` | installed models and the hardware table |
| `4-settings.png` | app and content language, appearance, notifications |

None contains a face, a video title or a channel name.

**Store logos and display images** — all from `docs/store-art/`:

| Slot on the page | File | Size |
|---|---|---|
| 9:16 Poster art | `poster-art-2x3.png` | 1440x2160 |
| 1:1 Box art | `box-art-1x1.png` | 1080x1080 |
| 16:9 Super hero art | `super-hero-art-16x9.png` | 1920x1080 |
| 1:1 App tile icon | `display-tile-300.png` | 300x300 |
| 1:1 | `display-tile-150.png` | 150x150 |
| 1:1 | `display-tile-71.png` | 71x71 |

Partner Center labels the poster slot "9:16" but asks for 2:3 dimensions. The
pixel sizes are what it validates; the label is simply wrong.

The three display tiles are optional — without them the Store upscales the
150x150 out of the package into the 300x300 slot, and that icon is the first
thing anyone sees.

**Skip entirely:** trailers, all Xbox images (branded key art, titled hero art,
featured promotional square art), Short title and Voice title. Every one of
those is Xbox-only or needs a trailer you do not have yet.

Regenerate any of these with `python scripts/build_appx_assets.py`; the folder
is gitignored, so the script is what is versioned.

## 6. Submission options

**Publishing hold:** select *Don't publish this submission until I select
Publish now*. That is what lets you read the certification report before
anyone can install it.

### Restricted capability justification

Partner Center detects `runFullTrust` in the manifest and asks why it is
needed. It is the only capability the package declares.

**The field is limited to about 500 characters**, so this is the version that
fits. Paste exactly:

```
Clips Kitty is a Win32 desktop app packaged with the Desktop Bridge, so runFullTrust is required for it to run. It is the only capability declared.

It launches three child processes that ship inside the package: a frozen Python engine for video analysis, FFmpeg for decoding and encoding, and an Ollama runtime that hosts the AI model on the user's own hardware. AppContainer cannot launch these.

It reads and writes only video files the user picks. No account, no telemetry, nothing uploaded.
```

If even that is rejected as too long, this says the same in 384:

```
Clips Kitty is a Win32 desktop app packaged with the Desktop Bridge, so runFullTrust is required for it to run, and it is the only capability declared.

It launches three child processes bundled in the package: a frozen Python engine for analysis, FFmpeg for encoding, and Ollama hosting the AI model locally. AppContainer cannot do this.

No account, no telemetry, nothing uploaded.
```

Every claim there is verifiable in the package: `resources/backend/api.exe`,
`_internal/ffmpeg/ffmpeg.exe` and `_internal/ollama/ollama.exe` are all
spawned by `ui/src/main/index.ts`, and `runFullTrust` is the only entry under
`<Capabilities>` in the manifest.

### Notes for certification

Paste into **Notes for certification**:

```
Clips Kitty processes video locally using an AI model that is downloaded on
first run, so the first launch shows a model download of roughly 3-5 GB with a
progress bar. This is model weights (data), not executable code. Nothing is
uploaded; all processing happens on the device.

To test: paste any short YouTube link on the Dashboard. The app will prompt to
download a model first if none is present. A machine with 16 GB of RAM is
required - on less, clips are analysed but rendering fails with an
out-of-memory error.

No account or login is needed anywhere in the app.

The optional YouTube publishing feature is disabled by default, is
command-line only, and requires Google Cloud credentials the user supplies
themselves, so it is not reachable in this build.

The donate button opens PayPal in the system browser (third-party purchase
API, declared in Product declarations).
```

Then **Submit to the Store**.

---

## After submitting

Certification is usually a few hours to three days. Status appears on the
submission page and email arrives at each stage.

**If it fails**, the report names the policy number. Fix it and use **Update**
on the same submission — you do not start over. Microsoft's own published
figures show 623 overturned out of 1,118 appeals, so if a rejection looks
wrong, appeal: `reportapp@microsoft.com`.

**If it passes**, it will not go live until you publish, because you chose
manual. Read the report, then publish.

**Then:**

- Store link for the README and website:
  `https://apps.microsoft.com/detail/9NB6XT7DSQZZ` (404s until it publishes)
- Note in `CHANGELOG.md` that 0.1.2 is on the Store
- Consider submitting the [winget manifest](../packaging/winget/) — it needs no
  account, no certificate and no certification, and reaches a different crowd

**Next release:** Partner Center → **Start update**, never a new product. A new
product would discard the ratings, reviews and the Store URL.
