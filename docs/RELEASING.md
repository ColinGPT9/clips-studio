# Releasing Clips Studio

How to turn a commit into something a creator can install.

## Build

```
pip install -r requirements-build.txt
python scripts/build_installer.py
```

One command, and it stops at the first failure with an explanation. Expect
**about two and a half hours** — 149 minutes at 0.1.0, nearly all of it
compressing 10 GB of payload twice, once into the `.7z` and once into the
`.zip` — and around 30 GB of free disk while it works.

The first run also downloads what the installer bundles but the repo does not
store — FFmpeg, the Ollama runtime and the Whisper weights, roughly 3 GB into
`vendor/`. That happens once; later builds find them and skip it. To refresh
one deliberately, run its script with `--force`:

```
python scripts/fetch_ffmpeg.py --force
python scripts/fetch_ollama.py --force     # also re-run after bumping OLLAMA_VERSION
python scripts/fetch_whisper.py --force
```

`--skip-backend` and `--skip-ui` reuse the previous run's output. Use them
when only the icon or the packaging config changed — but **never** when
Python code changed, or you will ship a stale engine.

## What comes out

Everything lands in `release/`:

Measured at 0.1.0, the first build carrying the bundled runtime and weights:

| File | Size | What it is |
|---|---|---|
| `nsis-web/ClipsStudio-Web-Setup-<v>.exe` | 813 KB | What people download. Fetches the payload and installs it. |
| `nsis-web/clips-studio-<v>-x64.nsis.7z` | 5.88 GiB | The payload the setup downloads. |
| `nsis-web/latest.yml` | 586 B | Version + SHA512 of both. The update checker reads this. |
| `ClipsStudio-<v>-x64.zip` | 6.90 GiB | Offline alternative: unzip, run `Clips Studio.exe`. |

Installed, that unpacks to about **10 GB**. Roughly 4 GB of the growth is the
bundled Ollama runtime and the two Whisper models; the rest is CUDA PyTorch,
which was always the bulk of it.

## Publishing

Two destinations, and which file goes where is not a preference.

**A GitHub release asset is capped at 2 GiB.** The payload passed that before
anything was bundled — 2.09 GiB at 0.1.0 — and carrying Ollama and the Whisper
weights roughly doubles it. Uploading it to a release does not work, and no
amount of retrying changes that.

So the big files live in a Hugging Face repository, which has no such cap,
costs nothing and serves from a CDN. This project already pushes to Hugging
Face for the website (see [MIRRORS.md](MIRRORS.md)); this is a second repo,
`ColinGPT9/clips-studio-releases`, for release payloads.

| Where | What |
|---|---|
| Hugging Face `clips-studio-releases` | `clips-studio-<v>-x64.nsis.7z`, `latest.yml`, `ClipsStudio-<v>-x64.zip` |
| GitHub release | `ClipsStudio-Web-Setup-<v>.exe` and the release notes |

Tag the GitHub release to match the version in `ui/package.json` (`v0.1.0` for
`0.1.0`).

> **The setup and its payload must go up together.** The Web Setup fetches
> `clips-studio-<v>-x64.nsis.7z` **by name, from the URL in the `publish`
> block of `ui/electron-builder.yml`**. Publish the setup before the payload
> has finished uploading and every download fails partway through with a
> confusing error, because the installer is a downloader with nothing to
> download.

Files that big need Git LFS on the Hugging Face side, or `huggingface-cli
upload`, which handles the chunking itself:

```
huggingface-cli upload ColinGPT9/clips-studio-releases release/nsis-web/ . --repo-type=model
```

### Updates come from the feed file, not from the release page

The app never reads the GitHub releases API. `electron-updater` fetches one
YAML file from the Hugging Face repo and compares versions, so **an update
exists the moment that file is uploaded** — before you have written a single
line of release notes, and whether or not the GitHub release is still a draft.
Upload the payload first and the feed file last.

Channels are separate files rather than GitHub's pre-release flag, which only
the GitHub provider understood:

| Channel in the app | Fetches | Falls back to |
|---|---|---|
| Stable (default) | `latest.yml` | — |
| Beta | `beta.yml` | `latest.yml` |
| Alpha | `alpha.yml` | `latest.yml` |

The fallback is what keeps the old promise that alpha and beta users also see
finished releases. It also means **a pre-release channel with no file is not an
error** — between pre-releases those users quietly get the stable feed.

To ship something stable users must not be pulled onto, upload it as
`alpha.yml` or `beta.yml` and leave `latest.yml` alone.

### The website's download buttons name the version

All nine "Download for Windows" buttons across `site/` link to
`releases/download/v<version>/ClipsStudio-Web-Setup-<version>.exe`, so **they
have to be bumped with the version**. The CI website job checks that internal
paths resolve; it cannot tell that an external GitHub URL now points at a
release that does not exist.

The version-free `releases/latest/download/...` form would avoid this, but
GitHub's "latest" **skips pre-releases** — while the project ships alphas, that
URL 404s. Switch to it when a release goes out without the pre-release flag.

Check after publishing:

- [ ] Payload, zip and feed file uploaded to Hugging Face; Web Setup on the
      GitHub release
- [ ] Nothing over 2 GiB was attached to the GitHub release
- [ ] Tag matches `ui/package.json`
- [ ] Feed file uploaded **last**, after the payload finished
- [ ] Named `alpha.yml` / `beta.yml` if stable users should not get it
- [ ] Release notes mention the SmartScreen warning (see below)
- [ ] Release notes written for creators — they appear inside the app, in the
      update bar's "What's new"
- [ ] Downloaded the Web Setup on a machine that has never run Clips Studio,
      and installed it end to end

## Testing it the way a stranger meets it

A development machine has Python, FFmpeg and Ollama lying around, so it will
pass an install test it should fail. The claim being made — install one thing
and nothing else — can only be checked somewhere none of that exists.

**Windows Sandbox** is the cheap way to get that: a throwaway Windows that
boots clean and is destroyed on close.

1. **BIOS**: enable virtualization. On AMD it is called **SVM Mode**, usually
   under Advanced → CPU Configuration; on Intel, VT-x. `systeminfo` reports
   `Virtualization Enabled In Firmware: Yes` once it is on.
2. **Feature**, in an admin PowerShell:
   ```
   Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All
   ```
3. Launch [`scripts/test-install.wsb`](../scripts/test-install.wsb):

   ```
   & "$env:WINDIR\System32\WindowsSandbox.exe" scripts\test-install.wsb
   ```

   Double-clicking often does nothing: enabling the feature does not always
   register a handler for `.wsb`, and Windows then has no idea what the file
   is. Launching the exe explicitly sidesteps it.

   The Web Setup lands on the sandbox desktop. Only `release/sandbox-test` is
   mapped, which holds the installer and nothing else — mapping
   `release/nsis-web` would put the payload beside it, and an installer that
   finds its payload locally never downloads one, which is the entire thing
   being tested.

What to confirm in there:

- [ ] The setup downloads its payload — this is the only real proof the
      Hugging Face URL baked into the exe is correct
- [ ] The wizard never asks you to install anything
- [ ] The model pull starts by itself and reports progress
- [ ] A clip renders end to end

Two things the sandbox cannot tell you. **CUDA does not work in it**, so the
app will report no GPU and run on CPU — expected there, and no reflection on a
real install. And everything is destroyed on close, including the ~6 GB it
downloaded, so do not close the window mid-run.

No virtualization available? A second Windows user account is a weaker
substitute: it gives a clean `%LOCALAPPDATA%`, so the first-run wizard and the
per-user install are genuinely exercised, but system-wide Python and any Ollama
on the default port are still visible and the "nothing else needed" claim goes
untested.

## What the installer does and does not carry

**Included:** the Electron app, the frozen Python engine, every Python
dependency, CUDA PyTorch, FFmpeg, the Ollama runtime, and the YOLO, TalkNet
and Whisper weights. No Python install, no second installer, no PATH edits,
no terminal. A creator installs Clips Studio and nothing else.

The bundled Ollama listens on **127.0.0.1:11435**, not its default 11434, so
it cannot collide with one the creator already runs. Electron starts it, tells
the engine where it is via `CLIPS_STUDIO_OLLAMA_HOST`, and kills the process
tree on quit. Its models go to `%LOCALAPPDATA%\Clips Studio\data\models`.

**Not included:** the language model itself. Not for packaging reasons — it
would fit — but licensing ones: Gemma and friends ship under terms the person
downloading has to accept, and bundling them would mean accepting on their
behalf. It is also 5 GB whose right size depends on their VRAM. The app pulls
it on first launch behind a progress bar, which works because Ollama's pull
API reports progress; that is the same reason the Whisper weights *are*
bundled, since their download has no such hook and looked exactly like a hang.

The app reports anything missing at `GET /health/preflight`, in words a
creator can act on.

Installed copies keep videos, clips and the database in
`%LOCALAPPDATA%\Clips Studio\data`, never inside Program Files. Uninstalling
leaves that alone — removing a program must not delete someone's footage.

## SmartScreen

The installer is **not code-signed**, so Windows shows "Windows protected
your PC" on first run and the user has to click *More info → Run anyway*.
Say so plainly in the release notes; a creator who was not warned assumes
it is a virus and stops.

This clears once a certificate is bought (an OV certificate warns until it
builds reputation; an EV certificate does not warn at all). Signing is off
in `ui/electron-builder.yml`, and turning it back on is documented there.

## Version bump

Version lives in `ui/package.json` only. Bump it, commit, then build — the
artifact names and `latest.yml` follow from it.
