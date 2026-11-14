# Local narration in Clips Studio

`scripts/narrate_local.py` renders a text file or a list of narrated scenes to
WAV files with the app's built-in voice (Kokoro-82M, on the CPU). It speaks
through the same voice layer as the app (`voice/`, see
[`editor-suite/api/stepV1-voice.md`](editor-suite/api/stepV1-voice.md)). It
does not run inside a game plugin, use a provider key, contact a speech API or
change existing clip audio. ElevenLabs is not required.

The runtime is the MIT-licensed kokoro-onnx; the model weights are Apache-2.0.
Voicebox uses the same Kokoro model, but nothing of Voicebox is installed or
copied here.

## Setup on Windows

Use Python 3.11 or 3.12. `requirements.txt` installs the runtime (kokoro-onnx
0.6.1); the two model files are fetched once:

```powershell
python -m pip install -r requirements.txt
python scripts/fetch_kokoro.py
python scripts/narrate_local.py --text-file narration.txt --output-dir exports/narration --voice af_heart
```

`fetch_kokoro.py` downloads about 354 MB into `models/kokoro/` and keeps a file
only when it matches its pinned SHA-256. The script checks both digests again
before loading them (`--model-dir` names another folder). Generation then
works without network access, a GPU, a subscription or an API key. WAV files
are generated only when explicitly requested. Unchanged scene requests reuse
their existing files.

For video narration, pass `--scenes scenes.json` instead of `--text-file`. The
JSON is an array of objects containing unique simple `id` values and
`narration` strings; objects without narration are skipped. Use `--speed`
between 0.5 and 2.

- `--language` is `en` (the default), `es`, `fr`, `hi`, `it` or `pt`: the
  languages the built-in voice speaks. Pick a voice of that language with
  `--voice` (for example `ef_dora` for Spanish).
- Only voices of those six languages are accepted (names starting `a`, `b`,
  `e`, `f`, `h`, `i` or `p`). The pack's Japanese and Chinese voices (`j`,
  `z`), which the script used to take for English text, are now refused.
- A British voice (a name starting `b`, such as `bf_emma`) is now read with
  British sounds. Before it was read with American ones, so a British voice
  gives a different WAV than it did; the default `af_heart` gives the same.
- `--seed 7` gives the same WAV bytes for the same text, voice and speed on
  the same PC. Without a seed, two runs never sound exactly alike.

This is a command-line production utility. It never clones the speaker from a
gameplay recording.

Model SHA256: `beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a`.
Voice pack SHA256: `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d`.

Sources and licenses:

- [kokoro-onnx runtime and MIT license](https://github.com/thewh1teagle/kokoro-onnx)
- [Kokoro model and Apache-2.0 license](https://huggingface.co/hexgrad/Kokoro-82M)
- [Pinned model release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1)
- [eSpeak NG, which turns the text into sounds (GPL-3.0)](https://github.com/espeak-ng/espeak-ng)
