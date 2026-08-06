# Releasing Clips Studio

How to turn a commit into something a creator can install.

## Build

```
pip install -r requirements-build.txt
python scripts/build_installer.py
```

One command, and it stops at the first failure with an explanation. Expect
90 minutes or so, nearly all of it compressing the payload, and around 30 GB
of free disk while it works.

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

| File | Size | What it is |
|---|---|---|
| `nsis-web/ClipsStudio-Web-Setup-<v>.exe` | ~1 MB | What people download. Fetches the payload and installs it. |
| `nsis-web/clips-studio-<v>-x64.nsis.7z` | ~4 GB | The payload the setup downloads. |
| `nsis-web/latest.yml` | ~1 KB | Version + SHA512 of both. The update checker reads this. |
| `ClipsStudio-<v>-x64.zip` | ~5 GB | Offline alternative: unzip, run `Clips Studio.exe`. |

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
