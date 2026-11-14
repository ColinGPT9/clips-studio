# Step V1 API: one voice layer

The engine side of step V1, as the desktop app uses it. Every route here is on
`http://127.0.0.1:8765`. The new routes are internal (`server/api_stability.py`
lists none of them), so their shape may change with the Settings → Voice card.

Engine code: `voice/` (the layer: `__init__.py` decides who speaks,
`engines/kokoro.py`, `engines/piper.py`, `engines/voicebox.py`,
`engines/openai_local.py`, `net.py` the loopback-only HTTP client,
`settings.py` the JSON store, `align.py` word times, `kokoro_files.py` the
pinned files, `wav.py`), `server/voice_api.py` (the routes),
`scripts/fetch_kokoro.py`, `core/binaries.py` (`kokoro_dir`),
`core/preflight.py` (`check_voice`), `multilingual/dub.py` and
`multilingual/publish.py` (dubbing), `server/jobs.py` (the translate job),
`scripts/narrate_local.py`.

**What changes with nothing touched in Settings.** A plain clipping run loads
nothing of the layer (`tests/test_voice_kokoro_real.py`). A dub with a Piper
voice picked in the dubbing dialog makes exactly the speech calls, ffprobe
questions and ffmpeg mix it made before (`tests/test_voice_dub_piper_snapshot.py`,
against `tests/snapshots/dub_piper.json`, recorded at step 5's commit). What
does change: for English, Spanish, French, Hindi, Italian and Portuguese the
voice used when **no** voice is picked is now the built-in Kokoro voice, when
its files are there; without the files (or for any other language) it is
Piper's default, as before.

## The four engines

| Choice in Settings (`engine`) | What speaks | Needs |
|---|---|---|
| `builtin` (the default) | Kokoro inside the engine for en, es, fr, hi, it, pt; Piper for the other dubbing languages | Nothing. Kokoro's two files ship with the installer |
| `voicebox` | The person's own Voicebox, at `127.0.0.1:17493` | Voicebox open (or its "keep server running" setting on) |
| `voicestudio` | The person's own VoiceStudio, at the address in its `backend.json`, else `127.0.0.1:3900` | VoiceStudio open |
| `custom` | Any local voice app with `POST /v1/audio/speech`, at an address the person types | That app open; the address must be on this PC |

Clips Kitty never ships, downloads or starts Voicebox or VoiceStudio, and
never creates, changes or deletes anything in them. Every request to them goes
from the Python engine (no `Origin` header), and only to a loopback address
(`127.x.x.x`, `::1`, or `localhost`, which is read as `127.0.0.1` and never
looked up). Anything else is refused before a socket is opened, with:
`Only a voice app on this PC can be used. Enter an address like http://127.0.0.1:3900.`
Redirects are refused and the system's proxy settings are ignored.

## Voice ids

A voice id says which engine speaks it, so a choice keeps meaning the same
voice whatever engine is picked later:

| Id | Voice |
|---|---|
| `en_US-lessac-medium`, `fr_FR-upmc-medium#1` | A Piper voice, written as it always was (always Piper) |
| `kokoro:af_heart` | A built-in Kokoro voice |
| `voicebox:<profile id>` | A voice in the person's Voicebox |
| `voicestudio:<voice id>` | A voice in the person's VoiceStudio |
| `custom:<voice id>` | A voice of another local voice app |

Pass ids back exactly as a voice list gave them (URL-encode them in a query).

## Who may call

`GET /voice`, `POST /voice/engine`, `GET /voice/voices`, `POST /voice/speak`
and `GET /voice/preview` are guarded like the project routes of step 5:

- the `X-Clips-Kitty-Session` header with the desktop app's session secret,
  else **403**;
- an `Origin` that is the app's own (none, `null`, `file://`, the dev server,
  or the engine's own), else **403**.

`GET /voice/audio/{file}` takes no header (a media player cannot send one):
its file name is a random 128-bit token that only a speak or preview answer
contains. The older `GET /voices`, `GET /voices/preview` and `GET /languages`
are unguarded, as before.

## Errors

A voice error is `{"detail": {"message": "<plain words, show as is>", "code": "<code>"}}`:

| `code` | Status | When | Example `message` |
|---|---|---|---|
| `not_running` | 409 | The chosen voice app is closed and standing in is not allowed | `Open Voicebox to use it.` / `Open VoiceStudio to use it.` / `Open your voice app to use it.` |
| `files_missing` | 409 | The built-in voice's files are missing or do not match their SHA-256 | `Voice files missing. Run: python scripts/fetch_kokoro.py` |
| `not_local` | 400 | An address that is not this PC, or no address typed yet for `custom` | `Only a voice app on this PC can be used. Enter an address like http://127.0.0.1:3900.` |
| `no_voice` | 400 | Nothing speaks this language, or the voice no longer exists | `That voice is no longer in Voicebox. Pick another voice.` |
| `refused` | 502 | The voice app answered with an error, or with something that is not sound | `Voicebox could not say this: …` |
| `failed` | 500 (400 for empty text or an unknown `engine`) | Anything else | `The built-in voice could not say this (…).` |

A body that breaks the request's shape (unknown field, text over 5,000
characters, `speed` outside 0.5 to 2) is FastAPI's usual **422**.

## GET /voice

Which engines can speak right now, and what is chosen. Looks at each voice app
once (a connection attempt to this PC, half a second at most, all three at the
same time), so it is safe to call when the card opens and after each change.

```json
{
  "engine": "builtin",
  "engines": [
    {"id": "builtin", "name": "Built-in", "optional": false, "ready": true, "code": "ok",
     "message": "", "address": "", "version": "",
     "languages": ["ar", "bn", "de", "en", "es", "fr", "hi", "id", "it", "pt", "ru", "tr", "ur", "vi", "zh"],
     "kokoro": {"ready": true, "message": "", "languages": ["en", "es", "fr", "hi", "it", "pt"]},
     "piper": {"ready": true, "languages": ["ar", "bn", "de", "..."]},
     "licence_note": ""},
    {"id": "voicebox", "name": "Voicebox", "optional": true, "ready": false, "code": "not_running",
     "message": "Open Voicebox to use it.", "address": "http://127.0.0.1:17493", "version": "",
     "languages": [], "licence_note": ""},
    {"id": "voicestudio", "name": "VoiceStudio", "optional": true, "ready": false, "code": "not_running",
     "message": "Open VoiceStudio to use it.", "address": "http://127.0.0.1:3900", "version": "",
     "languages": [],
     "licence_note": "VoiceStudio's usual voice may not be used in videos that earn money."},
    {"id": "custom", "name": "Another local voice app", "optional": true, "ready": false, "code": "no_address",
     "message": "Enter your voice app's address first, like http://127.0.0.1:8880.", "address": "", "version": "",
     "languages": [],
     "licence_note": "Whether a voice may be used in videos that earn money is for you to check with the app it comes from."}
  ],
  "settings": {"engine": "builtin", "voices": {}, "custom_address": "", "custom_model": "",
               "fallback_to_builtin": false}
}
```

- `engines` is always these four, in this order. `name` is English: pass it
  through `t()`.
- `ready` + `message` are the reachability line. `message` is empty when ready.
  `code` is one of `ok`, `not_running`, `no_address`, `not_local`, `wrong_app`
  (another program answers at Voicebox's address) and, for `builtin`,
  `files_missing` (Kokoro's files are not there; `message` carries the fetch
  command; `ready` is still true when Piper can dub).
- `version` is Voicebox's version when it answers; empty otherwise.
- `licence_note` is for the engine's tooltip (item 9 of the spec): empty for
  Built-in and Voicebox.
- `settings` is what is stored (below).

## POST /voice/engine

Saves the person's choices and answers like `GET /voice`. Every field is
optional; what is left out stays as it is. Unknown fields are refused (422).

```json
{"engine": "voicebox",
 "voices": {"en": "voicebox:9c71…", "es": null},
 "custom_address": "127.0.0.1:8880",
 "custom_model": "",
 "fallback_to_builtin": false}
```

| Field | Meaning |
|---|---|
| `engine` | `builtin`, `voicebox`, `voicestudio` or `custom`. Anything else: 400 `failed` |
| `voices` | The default voice per language, merged language by language. `null` or `""` for a language clears it (the engine's own default is used again). A default is only used while its own engine is the chosen one, and comes back when that engine is chosen again. A voice whose engine does not speak the language (a built-in Kokoro voice for German, a Piper voice for Korean, a Voicebox voice for Thai) is refused with 400 `no_voice` and nothing of the request is saved; a voice of VoiceStudio or another local voice app is taken as given |
| `custom_address` | The address of "another local voice app". Must be on this PC, else 400 `not_local` and nothing is saved. Stored in one form: `localhost:8880/v1/` is saved as `http://127.0.0.1:8880`. `""` clears it |
| `custom_model` | The model name sent to that app's speech route. Empty means `tts-1`, which most accept |
| `fallback_to_builtin` | When the chosen voice app is not running: `false` (the default) = the job stops and says why; `true` = the built-in voice is used, and the job's notes say so |

Stored in `voice_settings.json` in the data folder (`voice/settings.py`;
defaults in code, the pattern of `remote_render/settings.py`). A missing or
damaged file is the defaults.

## GET /voice/voices?language=en[&engine=voicebox]

The voices to choose from for a language, from the chosen engine, or from
`engine` (to fill the list before the person commits to an engine).
`language` is a two- or three-letter code, else 400.

```json
{"engine": "builtin",
 "voices": [
   {"id": "kokoro:af_heart", "voice": "af_heart", "name": "Heart", "country": "United States",
    "quality": "", "language": "en", "gender": "female", "engine": "kokoro", "commercial_ok": true},
   {"id": "en_US-lessac-medium", "name": "Lessac", "country": "United States", "quality": "medium",
    "language": "en", "engine": "piper", "commercial_ok": null}
 ],
 "default": "kokoro:af_heart",
 "message": "",
 "code": "ok"}
```

- Every voice has `id`, `name`, `country`, `quality` (the four the dubbing
  dialog already reads), `language`, `engine` and `commercial_ok`. Voicebox
  voices also have `model` (its engine's name) and `ready` (`false` = Voicebox
  has not downloaded that voice's model; say so, never download it).
- **`commercial_ok`**: `true` = cleared for videos that earn money, `false` =
  not cleared: show the small "not for monetised videos" tag, `null` = not
  known: show nothing. Built-in Kokoro voices are `true`. VoiceStudio's voices
  are all `false` (its default model, OmniVoice, is CC-BY-NC, and VoiceStudio
  marks none of its models as cleared). A `custom` app's voices are all `null`,
  and the engine's `licence_note` says the licence is the person's to check.
  Piper's voices are `null` (their licences differ voice by voice and are not
  in Piper's catalogue). Voicebox's are `true` for its Kokoro, Qwen, LuxTTS and
  Chatterbox models and `null` otherwise; its "tada" voices are not listed.
- `default` is the person's stored default for the language when it is in the
  list, else the engine's own.
- A listed voice is one that can be picked: while Kokoro can't speak in this
  copy (`GET /voice` says `files_missing`: its files or its Python package are
  not there) its voices are left out, and the list is Piper's alone.
- **A closed voice app is not an error**: 200 with `voices: []`,
  `default: null`, `code: "not_running"` (or `not_local`) and `message` the
  line to show. With `fallback_to_builtin` on, the built-in voices are listed
  instead and `message` ends with `The built-in voice is used until then.`
- Voicebox lists the profiles made for the language first, then every cloned
  profile (a cloned voice speaks other languages too). A `custom` server
  without a voice list gets OpenAI's six standard names. VoiceStudio's list
  starts with `voicestudio:default` ("VoiceStudio's current voice").

## POST /voice/speak

Text to a WAV. For the voice-over tool (step V2) and the promo kit.

```json
{"text": "Welcome back to the channel.", "language": "en", "voice": "kokoro:af_heart",
 "seed": 7, "speed": 1.0, "words": true}
```

| Field | Meaning |
|---|---|
| `text` | 1 to 5,000 characters. A longer script is spoken in parts |
| `language` | Default `en` |
| `voice` | An id from a voice list. Left out: the person's default for the language, else the chosen engine's own |
| `seed` | 0 to 2,147,483,647. With a seed the built-in Kokoro voice gives the same bytes for the same request on the same PC. A voice app is handed the seed (Voicebox v0.5.0 honoured it in the real test). Piper takes none |
| `speed` | 0.5 to 2, default 1. Voicebox has no pace and ignores it |
| `words` | `true` adds each word's time. Slower: it runs the app's Whisper on the finished speech. Always the Whisper on this PC, also when online transcription is chosen for clipping: the spoken sound is sent nowhere |

```json
{"id": "5f0c…32 hex…", "url": "/voice/audio/5f0c….wav", "path": "<data folder>/voice/spoken/5f0c….wav",
 "sample_rate": 24000, "duration": 2.41,
 "engine": "kokoro", "voice": "kokoro:af_heart", "language": "en", "seed": 7, "note": "",
 "words": [{"word": "Welcome", "start": 0.0, "end": 0.38}, {"word": "back", "start": 0.38, "end": 0.6}]}
```

- Play it from `url` (prefix the API base). The file is 16-bit PCM WAV, kept
  for a day in the data folder, then removed at the next speak request. `path`
  is for engine-side tools (the editor placing it on a track).
- `engine` is who really spoke: `kokoro`, `piper`, `voicebox`, `voicestudio`
  or `custom`. `note` is not empty when the built-in voice stood in for a
  closed voice app (`Voicebox is not running, so the built-in voice is used.`).
- `words` are the script's own words, in order, with times snapped to what
  Whisper heard; a word it heard differently keeps the script's spelling and
  shares the time around it. When only the word times fail, the answer is
  still 200 with `"words": null` and a `words_error` line.

## GET /voice/preview?language=en&voice=kokoro:af_heart

"Hear this voice": a short sample spoken in `language` (the sentence from
`multilingual.languages.sample_text`). Answers
`{"id", "url", "path", "sample_rate", "duration"}`; play `url`. A built-in
voice's sample is made once and kept; a voice app's is spoken fresh each time.
A closed voice app: 409 `not_running` with the line to show.

## GET /voice/audio/{file}

The WAV a speak or preview answer pointed at (`audio/wav`, `no-store`). The
name must be 32 hex characters plus `.wav` and must be one this engine made;
anything else is 404.

## The older dubbing routes

They keep working, now through the layer:

| Route | Now |
|---|---|
| `GET /languages` | `can_dub` is true where a voice exists (the built-in ones, or a chosen voice app); `dubbing_available` likewise |
| `GET /voices?language=es` | `{"voices", "default", "engine", "message"}`: the chosen engine's voices in the shape above (`id`, `name`, `country`, `quality` unchanged, plus `engine`, `language`, `commercial_ok`). `message` is the plain line when the chosen voice app is closed (the list is then empty), or open with no voice for the language (`Voicebox has no voice for Spanish.`) |
| `GET /voices/preview?language=es&voice=<id>` | Still answers with the sound itself. Takes a built-in voice's id, or a voice of the voice app chosen in Settings → Voice; a voice of any other voice app is refused with 400 and that app is not contacted (this route has no session guard). 400 with a plain `detail` when it can't |
| `POST /translate` | `voices: {language: voice id}` takes any voice id. With `dub: true`, the job first checks the person's voice app: closed and no standing in = the job fails at once with the plain line (`Open Voicebox to use it.`), before anything is written. The same when a built-in Kokoro voice is named (or saved as the language's default) and Kokoro can't speak in this copy: the job fails at once with Kokoro's line (`Voice files missing. Run: python scripts/fetch_kokoro.py`). A voice that fails on any sentence during the job (the voice app was closed, answered with an error, or sent a sound file with no sound in it: `Voicebox sent silence instead of speech.`) writes no dub for that language at all, not one that goes quiet part-way; the other files are written and the job ends as failed with `Spanish was not dubbed: <why>`, never as done. This holds for the built-in Kokoro voice and for every voice app; Piper's own path is as it was. A caption with nothing to say (dots, music notes) that a voice makes nothing of is left out and is not a failure. A language the chosen voice app (or a named voice) has no voice for is not dubbed; the job carries on and its log says why (`Spanish was not dubbed: Voicebox has no voice for this language. Pick a voice in Settings.`). |

How a dub picks and fits its voice (`multilingual/dub.py`, `voice.dub_plan`):

- **Who speaks:** the voice named for the language in the request, else the
  person's default for the language (Settings → Voice, a Piper voice
  included), else the chosen engine's own default. With nothing saved and no
  voice named, a Piper language is spoken by Piper's usual voice with the
  same calls as before the layer.
- **Level:** Piper hands over each sentence at full level, and the mix
  (`volume=1.6` over the original at 0.12) is unchanged. A sentence from
  Kokoro or a voice app is brought to that level first (every sample times
  one number, at most 10 times; `voice.wav.to_full_level`), because Kokoro
  speaks about 8 dB lower. Measured on one clip: a Kokoro dub came out at
  -15.7 LUFS (-22.4 before this), the same clip with Piper at -12.4. Speech
  from `POST /voice/speak` and `scripts/narrate_local.py` is left at the
  voice's own level.
- **Fit:** a sentence that misses its slot by more than a tenth is spoken
  again at another pace (0.72 to 1.45 times its length) by a voice that has
  a pace. Voicebox has none, so its finished take has its tempo changed by
  FFmpeg (`atempo`) by the same amount instead.

For the dubbing dialog (`MultilingualExport.tsx`): use `default` from
`GET /voices` as the preselected voice, show `message` when it is not empty,
and remove the old "pip install piper-tts" hint (dubbing no longer depends on
one package).

## Start-up check

`GET /health/preflight` has one more check, `{"name": "voice", ...}`, never
blocking: `ok` with `Built-in voice ready`, or a warning with
`Voice files missing.` It looks at file names and sizes only, plus what the
SHA-256 check found once something has tried to speak (a file of the right
size that was refused is then a warning here too). In an installed copy the
messages name no command (`Install Clips Kitty again to put them back.`
instead of `Run: python scripts/fetch_kokoro.py`). The setup
wizard shows checks by name through its `LABEL` table
(`SetupWizard.tsx`); add a label for `voice` there.

## The built-in voice

- **Runtime:** kokoro-onnx 0.6.1 (MIT) on the ONNX Runtime the app already
  ships, on the CPU, inside the engine process. No second program.
- **Files:** `kokoro-v1.0.onnx` (325,505,369 bytes, SHA-256 `beb0d184…f0df3a`)
  and `voices-v1.0.bin` (28,214,398 bytes, SHA-256 `bca610b8…1fbf7d`), from the
  kokoro-onnx release `model-files-v1.1`. `python scripts/fetch_kokoro.py`
  puts them in `models/kokoro/` (git-ignored); the installer build runs it and
  `clips-studio.spec` bundles them into `kokoro/`. `core.binaries.kokoro_dir()`
  finds them (`CLIPS_STUDIO_KOKORO_DIR` names another folder). Both are checked
  against their SHA-256 before they are loaded; a file that does not match is
  never loaded. Nothing is downloaded while the app runs.
- **Languages** (measured on 2026-11-12 by speaking two sentences per language
  and transcribing them with Whisper large-v3-turbo): English, Spanish,
  French, Hindi, Italian and Portuguese come back as written (0.89 to 1.00
  word match). Japanese (0.09 to 0.24) and Chinese (0.25 to 0.67) do not:
  they need dictionaries (pyopenjtalk/unidic, jieba/pypinyin) that are a large
  new dependency, so they are not offered. Chinese dubbing stays on Piper;
  Japanese has no built-in voice, as before. Nobody listened for how natural
  the voices sound: that is for the owner's ear.
- **Default voices:** `af_heart` (en), `ef_dora` (es), `ff_siwis` (fr),
  `hf_alpha` (hi), `if_sara` (it), `pf_dora` (pt).
- **Speed and memory on the owner's PC (Ryzen 5 5600X, CPU):** first audio
  2.6 s after a cold process (12.8 s the first time ever, before the files are
  in the disk cache); 3.5 times faster than realtime; 428 MB after loading,
  962 MB peak on a long text. A seeded request builds a fresh model session
  (about 1.3 s more).
- **Same seed, same bytes:** yes on the same PC (a fresh session started from
  the seed, always on 4 threads). Without a seed, never. Across PCs: not
  tested.
- **Size added to an installed copy:** 337 MiB of model files plus about
  19 MiB of Python packages (espeakng-loader with the espeak-ng library and
  data, phonemizer, attrs, dlinfo, kokoro-onnx): about 356 MiB before the
  installer's compression.

## Environment switches (tests and unusual set-ups)

| Variable | Meaning |
|---|---|
| `CLIPS_STUDIO_KOKORO_DIR` | Another folder holding the two voice files |
| `CLIPS_STUDIO_VOICEBOX_URL` | Voicebox on another loopback port |
| `VOICESTUDIO_USER_DATA` | VoiceStudio's own switch for its settings folder (where `backend.json` is read) |

## Known limits

- VoiceStudio was tested against a fake server only (it is not installed on
  the owner's PC). Voicebox v0.5.0 was tested for real once.
- Voicebox has no pace setting, so a dubbed sentence that misses its slot is
  not spoken again at another pace, as it is with the built-in voices: its
  tempo is changed after the fact instead, which sounds a little less natural.
- A language the chosen voice app has no voice for is not dubbed (subtitles
  only, with a line in the job's log and in the dubbing dialog). The built-in
  voice does not stand in for it, even with standing in allowed: that switch
  is for a closed app. Whether it should is the owner's call.
- A dub spoken by Kokoro is still about 3 LU quieter than the same dub
  spoken by Piper (one clip measured); nobody has listened to either.
- A voice app's voice list is not tied to languages in a way the layer can
  read (except Voicebox's preset profiles), so all its voices are offered for
  every language.
- With a voice app chosen and a clip dubbed into several languages, every
  sentence is one request to that app, one at a time.
- Word times need the Whisper model on this PC. They never go through an
  online transcription service, whatever is chosen for clipping.
- The built-in voice's model (about 400 MB in memory) is let go when a
  translate job ends. After a sample or `POST /voice/speak` it stays loaded
  until the next translate job ends or the app closes.
