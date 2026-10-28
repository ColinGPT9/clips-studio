# Optional local narration in Clips Studio

This host utility renders a text file or narrated scene list to WAV files with
Kokoro-82M on the CPU. It does not run inside a game plugin, use a provider key,
contact a speech API, or change existing clip audio. Existing optional cloud AI
connections remain bring-your-own-key. ElevenLabs is not required.

Voicebox includes the same Kokoro engine family. This integration uses the
independent MIT-licensed kokoro-onnx runtime and Apache-2.0 model weights, rather
than installing another desktop app or copying its implementation.

## Setup on Windows

Use Python 3.11 or 3.12. Keep optional narration dependencies separate from the
main app environment:

```powershell
python -m venv .venv-narration
.\.venv-narration\Scripts\python.exe -m pip install -r requirements-narration.txt
New-Item -ItemType Directory -Force models/kokoro
curl.exe -L --fail -o models/kokoro/kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx
curl.exe -L --fail -o models/kokoro/voices-v1.0.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin
.\.venv-narration\Scripts\python.exe scripts/narrate_local.py --text-file narration.txt --model-dir models/kokoro --output-dir exports/narration --voice af_heart
```

The initial model download is about354MB. The script verifies both release
SHA256 digests before loading them. Generation then works locally without
network access, a GPU, a subscription or an API key. WAV files are generated only
when explicitly requested. Unchanged scene requests reuse their existing files.

For video narration, pass `--scenes scenes.json` instead of `--text-file`. The
JSON is an array of objects containing unique simple `id` values and `narration`
strings; objects without narration are skipped. Use `--speed` between0.5and2.
This is an optional command-line production utility, not a new editor screen.
It never clones the speaker from a gameplay recording.

Model SHA256: `beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a`.
Voice pack SHA256: `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d`.

Sources and licenses:

- [Voicebox Kokoro backend](https://github.com/jamiepine/voicebox/blob/main/backend/backends/kokoro_backend.py)
- [kokoro-onnx runtime and MIT license](https://github.com/thewh1teagle/kokoro-onnx)
- [Kokoro model and Apache-2.0 license](https://huggingface.co/hexgrad/Kokoro-82M)
- [Pinned model release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1)
