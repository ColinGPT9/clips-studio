# Partner Center paste sheet — Clips Studio 0.1.2

Every field, in the order Partner Center asks for it. Open this beside the
dashboard and work down. [MSSTORE.md](MSSTORE.md) explains *why* for anything
that looks odd; this file is just the values.

**Start:** Partner Center → Apps and games → Clips Studio → **Start submission**

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
| Memory | 16 GB | 16 GB |
| DirectX | — | — |
| Video memory | — | 8 GB |
| Processor | x64 | x64 |
| Graphics | — | NVIDIA GPU |

16 GB is not padding. Below it a video is analysed and then rendering fails,
which is in KNOWN-ISSUES.md and would otherwise become one-star reviews.

## 3. Age ratings

Complete the IARC questionnaire. Every answer is **No** for Clips Studio. The
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

**Store logos** — `docs/store-art/`:

| File | Field |
|---|---|
| `box-art-1x1.png` | 1:1 box art — **required** |
| `poster-art-2x3.png` | 2:3 poster art — what the Store shows in most browsing surfaces |
| `super-hero-art-16x9.png` | 16:9 super hero art — optional |

## 6. Submission options

**Publishing hold:** select *Don't publish this submission until I select
Publish now*. That is what lets you read the certification report before
anyone can install it.

### Restricted capability justification

Partner Center detects `runFullTrust` in the manifest and asks why it is
needed. It is the only capability the package declares. Paste:

```
Clips Studio is a desktop application packaged with the Desktop Bridge, so runFullTrust is required for it to run at all. It is the only capability the package declares.

It is needed for three things, all local to the user's own machine:

1. The app spawns its own child processes. A frozen Python engine performs the video analysis, FFmpeg does all decoding and encoding, and an Ollama runtime hosts the AI model. All three ship inside the package and are launched as child processes, which an AppContainer app cannot do.

2. The AI model runs locally on the user's hardware, using CPU or GPU. It is not a cloud service, so the app needs ordinary desktop process and hardware access to run inference.

3. It reads and writes video files the user chooses through a standard file dialog, and writes finished clips to the user's own folders.

The app requires no network access to function beyond downloading a video the user explicitly asks for and, on first run, an AI model the user selects. No user data, video or telemetry is transmitted anywhere. There is no account and no sign-in.
```

Every claim there is verifiable in the package: `resources/backend/api.exe`,
`_internal/ffmpeg/ffmpeg.exe` and `_internal/ollama/ollama.exe` are all
spawned by `ui/src/main/index.ts`, and `runFullTrust` is the only entry under
`<Capabilities>` in the manifest.

### Notes for certification

Paste into **Notes for certification**:

```
Clips Studio processes video locally using an AI model that is downloaded on
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
