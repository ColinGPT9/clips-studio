# External GTA plugin video export demo

This developer script runs an external MIT plugin using the public
`clipskitty_sdk.testing.run_plugin` process and exports its returned moments
with Clips Kitty's existing `ClipCandidate`, clip cutter, caption builder,
and optional vertical renderer. The plugin stays outside this repository.
No detector, ML model, or renderer is downloaded or added to the app.

Generate the external plugin's original test assets, then run the app export:

```sh
python /workspace/Grand-Theft-Auto-Clips-Kitty-Plugin/scripts/generate_demo.py /tmp/gta-demo.mp4
python scripts/demo_gta_plugin.py \
  --plugin /workspace/Grand-Theft-Auto-Clips-Kitty-Plugin \
  --video /tmp/gta-demo.mp4 \
  --transcript /tmp/gta-demo.transcript.json \
  --synthetic-demo \
  --output /workspace/scratch/gta-plugin-demo
```

Use the environment's Python with the supported SDK and existing app video
dependencies installed. The explicit `--synthetic-demo` flag affirms that
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

The generator makes geometric frames and tones. Its fictional transcript is
supplied test input; the tone audio contains no speech. These artifacts
validate that SDK findings become playable app-rendered clips with timed
captions. They do not demonstrate real GTA visual recognition, transcription
accuracy, framing quality, funny moments, audience engagement, or virality.
No human visual, audio, caption, or watchability scores are assigned.

Run the actual integration test with an explicit external plugin checkout:

```sh
GTA_PLUGIN_DIR=/workspace/Grand-Theft-Auto-Clips-Kitty-Plugin \
  python -m pytest -q tests/test_gta_demo.py
```

The integration test authors its own short color-and-tone source, invokes
the real plugin, checks that a selected window is cut rather than the entire
source, decodes the H.264 result, checks audio/video streams and duration,
and verifies caption and history artifacts. It is marked `slow` and skips
when `GTA_PLUGIN_DIR` is absent; the input-assertion check always runs.
