# Extending Clips Studio

The four changes people most often want to make, and what each actually
touches. All of them are deliberately small — if one of these turns into a
sprawling diff, something has drifted and the design is worth a second look.

Read [ARCHITECTURE.md](../ARCHITECTURE.md) first for how the pieces fit.

---

## Add a language

Translation, subtitles, dubbing and the interface all key off one table.

1. **`multilingual/languages.py`** — add a row to `LANGUAGES`:
   ```python
   "pl": ("Polish", "Polski", "Polish"),
   ```
   The third value is the name used *in the translation prompt*; be specific
   where it matters ("Brazilian Portuguese", "neutral Latin American Spanish").

2. **Same file, `SAMPLES`** — one sentence, **written in that language**. It
   is what a creator hears when auditioning a dub voice, and hearing English
   in a Polish voice tells them nothing about a Polish dub.

3. **`ui/src/renderer/src/locales/pl.json`** — copy an existing locale and
   translate the values. Then import it in `lib/i18n.ts` and add it to
   `LOCALES`.

The translator and subtitle writer are language-agnostic, so nothing else
changes. `tests/` has a check that the language table and the locale folder
stay in step — run `pytest` after.

**Right-to-left scripts** (Arabic, Urdu, Hebrew) need a font with the glyphs
before burned captions look right; see `multilingual/burn.py`.

---

## Add an AI model

Anything Ollama serves already works — set it on the Models page. What needs
code is only the **recommendation**, so the setup wizard suggests it.

- **`llm/manager.py`** → `RECOMMENDATIONS` (the table shown on the Models
  page) and `recommend_for()` (the single model the wizard offers).

Keep those two consistent. They disagreed once — a 12 GB card was told
`gemma3:12b` by one screen and `gemma:7b` by the other — which is why
`recommend_for()` exists at all and why a test pins them together.

A model that does not fit in VRAM spills into system RAM and crawls, which
reads as broken rather than slow. Size the advice conservatively.

**A different backend entirely** (a cloud provider, say) is one new file in
`llm/` implementing `LLMBackend.generate()`, plus a line in `registry.py`.
Nothing in `analysis/`, `creator/` or `multilingual/` imports a concrete
backend, so nothing else changes.

---

## Add a platform

`sources/` is a plugin folder. Twitch, Kick and YouTube each live in one file.

1. Write `sources/yourplatform.py` exposing the same download entry point the
   others do.
2. Register the URL pattern in `sources/dispatch.py`.

Everything downstream — transcription, scoring, tracking, rendering — is
untouched, because it only ever sees a local file and a transcript.

Two things worth copying from the existing sources:

- **Ask for H.264.** YouTube serves much of its catalogue in AV1, which
  almost no consumer GPU decodes in hardware; left alone it roughly doubled
  processing time. See `sources/ytdlp_common.py`.
- **Audience signals are optional and capped.** Twitch chat replay is read
  where it exists; Kick discards chat entirely and scores fine without it. If
  your platform has something similar, add it in `analysis/hype.py` — never as
  a hard dependency.

---

## Add an export format

`video_editor/export.py` renders the final file. `longform/profiles.py` holds
the 16:9 output shapes (`short_clips`, `clips_140`, `highlights`,
`edited_stream`) — a new one is usually a new entry there rather than new
rendering code.

Anything that shells out to FFmpeg **must** use `core.binaries.ffmpeg()`.
Calling `"ffmpeg"` by bare name works on your machine and fails on every
installed copy, because creators do not have FFmpeg on their PATH. A test
enforces this.

---

## Before you open a PR

```
pytest                 # deterministic logic
ruff check .           # lint
cd ui && npm run typecheck && npm run build
```

CI runs all of that. It is fast, and it is narrow: a runner has no GPU, no
Ollama and no footage, so **a green tick does not mean clips still come out
well.** Anything touching scoring, tracking, captions or rendering needs
testing against a real video — say which one in the PR.
