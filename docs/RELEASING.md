# Releasing Clips Studio

How to turn a commit into something a creator can install.

## Build

```
pip install -r requirements-build.txt
python scripts/build_installer.py
```

One command, and it stops at the first failure with an explanation. Expect
roughly an hour, nearly all of it compressing the payload, and around 15 GB
of free disk while it works.

`--skip-backend` and `--skip-ui` reuse the previous run's output. Use them
when only the icon or the packaging config changed — but **never** when
Python code changed, or you will ship a stale engine.

## What comes out

Everything lands in `release/`:

| File | Size | What it is |
|---|---|---|
| `nsis-web/ClipsStudio-Web-Setup-<v>.exe` | ~1 MB | What people download. Fetches the payload and installs it. |
| `nsis-web/clips-studio-<v>-x64.nsis.7z` | ~2.1 GB | The payload the setup downloads. |
| `nsis-web/latest.yml` | ~1 KB | Version + SHA512 of both. The update checker reads this. |
| `ClipsStudio-<v>-x64.zip` | ~2.8 GB | Offline alternative: unzip, run `Clips Studio.exe`. |

## Publishing

Upload **all four** to the same GitHub release, with the tag matching the
version in `ui/package.json` (`v0.1.0` for `0.1.0`).

> **The setup and its payload must ship together.** The Web Setup fetches
> `clips-studio-<v>-x64.nsis.7z` **by name from the same release**. Publish
> the setup alone and every download fails partway through with a confusing
> error, because the installer is a downloader with nothing to download.

Check after publishing:

- [ ] All four files attached to the release
- [ ] Tag matches `ui/package.json`
- [ ] Release notes mention the SmartScreen warning (see below)
- [ ] Downloaded the Web Setup on a machine that has never run Clips Studio,
      and installed it end to end

## What the installer does and does not carry

**Included:** the Electron app, the frozen Python engine, every Python
dependency, CUDA PyTorch, FFmpeg, and the YOLO weights. No Python install,
no PATH edits, no terminal.

**Not included:** Ollama and the language model. Ollama is a separate
product with its own installer, GPU handling and update cycle, and models
are gigabytes whose right size depends on the user's VRAM. The app reports
what is missing at `GET /health/preflight`, in words a creator can act on.

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
