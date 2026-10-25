# External GTA plugin video export demo

This developer script runs an external MIT plugin using the public
`clipskitty_sdk.testing.run_plugin` process and exports its returned moments
with Clips Kitty's existing `ClipCandidate`, clip cutter, caption builder,
and optional vertical renderer. The plugin stays outside this repository.
No detector, ML model, or renderer is downloaded or added to the app.

## What it needs

- This repository, with the app's Python dependencies installed (the same
  environment that runs Clips Kitty from its code). The script uses this
  checkout's own SDK in `sdk/python`; no SDK needs installing with pip.
- A checkout of the external GTA plugin, which lives in its own repository and
  is not part of this one. `<plugin checkout>` below is that folder: the one
  with its `clipskitty.yaml`.
- A short synthetic video and an authored transcript for it. The plugin's
  repository has its own generator for these (`scripts/generate_demo.py`), but
  at the time of writing it exists only on an unmerged branch there. The
  commands below make an equivalent pair with FFmpeg instead, the way this
  repository's integration test does, so nothing from that branch is needed.

## Run it (PowerShell, from this repository's folder)

`<scratch>` is any folder outside this repository and the plugin checkout.

```powershell
$ffmpeg = python -c "from core.binaries import ffmpeg; print(ffmpeg())"
& $ffmpeg -hide_banner -loglevel error -y `
  -f lavfi -i "color=c=0x1a2c42:s=640x360:r=24:d=24" `
  -f lavfi -i "sine=f=440:sample_rate=44100:d=24" `
  -c:v mpeg4 -q:v 8 -c:a aac -shortest "<scratch>\gta-demo.mp4"
'{"language": "en", "segments": [{"start": 9, "end": 11, "text": "We crashed into a barrier!"}]}' |
  Set-Content -Encoding ascii "<scratch>\gta-demo.transcript.json"
python scripts\demo_gta_plugin.py `
  --plugin "<plugin checkout>" `
  --video "<scratch>\gta-demo.mp4" `
  --transcript "<scratch>\gta-demo.transcript.json" `
  --synthetic-demo `
  --output "<scratch>\gta-plugin-demo"
```

The explicit `--synthetic-demo` flag affirms that
the supplied files are original synthetic test assets. It does not establish
rights to arbitrary third-party footage. This technical check accepts local
files and performs no footage downloads.

The default export preserves the source aspect ratio and burns the supplied
authored captions. `--vertical` uses the existing 1080×1920 `fit_blur` renderer;
`--no-captions` omits captions. At most two moments are exported. CPU encoding
is selected through the app's existing encoder configuration. Direct binary
calls resolve through `core.binaries.ffmpeg()` and `ffprobe()`. The public
SDK test runner prepares its ordinary job tools using its own documented
test setup; the chosen tool paths are visible in its saved `job.json`.

The output folder contains standalone `highlight-01.mp4` (and a second file
when another moment is returned), caption `.ass` files, `manifest.json`,
`demo.log`, the SDK job folder, and isolated `history.sqlite3`. A vertical
run also keeps a source-aspect staging clip. The manifest records source
hashes, plugin identity, candidate bounds and reasons, render path, actual
FFprobe stream metadata, exported duration, and file hashes. Every export
must retain video and any source audio and match the selected duration within
0.25 seconds. The history uses the existing `StateDB` APIs in this isolated
folder; the user's application database is never opened.

The synthetic video is a plain colour and a tone. Its fictional transcript is
supplied test input; the tone audio contains no speech. These artifacts
validate that SDK findings become playable app-rendered clips with timed
captions. They do not demonstrate real GTA visual recognition, transcription
accuracy, framing quality, funny moments, audience engagement, or virality.
No human visual, audio, caption, or watchability scores are assigned.

Run the actual integration test with an explicit external plugin checkout:

```powershell
$env:GTA_PLUGIN_DIR = "<plugin checkout>"
python -m pytest -q tests/test_gta_demo.py
Remove-Item Env:GTA_PLUGIN_DIR
```

The integration test authors its own short color-and-tone source, invokes
the real plugin, checks that a selected window is cut rather than the entire
source, decodes the H.264 result, checks audio/video streams and duration,
and verifies caption and history artifacts. It is marked `slow` and skips
when `GTA_PLUGIN_DIR` is absent; the input-assertion check and the check that
the script finds this checkout's SDK always run.
