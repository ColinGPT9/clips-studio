# Test a plugin through the existing exporter

`scripts/demo_plugin_export.py` runs an external plugin with this checkout's
public MIT SDK test runner, then exports its selected moments through the
existing app cutter, captions and optional vertical renderer. It adds no
game-specific detector and never opens the user's library database.

Supply an original synthetic video and authored transcript outside both source
folders. The plugin must return at least one moment; an empty result fails
instead of inventing a clip. For a synthetic fixture of your own:

```powershell
py scripts/demo_plugin_export.py --plugin "<plugin folder>" `
  --video "<scratch>/sample.mp4" --transcript "<scratch>/sample.transcript.json" `
  --synthetic-demo --output "<scratch>/export-check"
```

`--synthetic-demo` affirms that the supplied files are original test assets.
It does not establish rights to somebody else's recording. This technical
check performs no downloads and accepts only synthetic test inputs.

The source aspect ratio is preserved by default. `--vertical` uses the existing
1080×1920 `fit_blur` layout; `--no-captions` omits captions. At most two clips
are exported using CPU encoding. Each output must retain video and any source
audio and match its selected range within 0.25 seconds.

The output includes MP4 files, authored captions, SDK jobs, a provenance manifest
and isolated `history.sqlite3`. These establish contract, media and history
integration; they do not establish real gameplay accuracy or human clip quality.

Run the host's original generic fixture without any external game plugin:

```powershell
py -m pytest tests/test_plugin_export_demo.py
```

That check uses the actual SDK process and renderer, decodes the H.264 export,
and verifies streams, selected duration, burned captions and history. Media
coverage skips when the required host dependencies or FFmpeg are unavailable.
