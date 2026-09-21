# Extending Clips Kitty

The changes people most often want to make, and what each actually touches.
All of them are deliberately small, if one of these turns into a sprawling
diff, something has drifted and the design is worth a second look.

Read [ARCHITECTURE.md](../ARCHITECTURE.md) first for how the pieces fit.

If you want to build something *beside* the app rather than change it. A bot,
a batch runner, another front end. You probably want [API.md](API.md)
instead. The whole pipeline is already reachable over HTTP.

---

## Add a language

Translation, subtitles, dubbing and the interface all key off one table.

1. **`multilingual/languages.py`**: add a row to `LANGUAGES`:
   ```python
   "pl": ("Polish", "Polski", "Polish"),
   ```
   The third value is the name used *in the translation prompt*; be specific
   where it matters ("Brazilian Portuguese", "neutral Latin American Spanish").

2. **Same file, `SAMPLES`**: one sentence, **written in that language**. It
   is what a creator hears when auditioning a dub voice, and hearing English
   in a Polish voice tells them nothing about a Polish dub.

3. **`ui/src/renderer/src/locales/pl.json`**: copy an existing locale and
   translate the values. Then import it in `lib/i18n.ts` and add it to
   `LOCALES`.

The translator and subtitle writer are language-agnostic, so nothing else
changes. `tests/` has a check that the language table and the locale folder
stay in step. Run `pytest` after.

**Right-to-left scripts** (Arabic, Urdu, Hebrew) need a font with the glyphs
before burned captions look right; see `multilingual/burn.py`.

---

## Add an AI model

Anything Ollama serves already works. Set it on the Models page. What needs
code is only the **recommendation**, so the setup wizard suggests it.

- **`llm/manager.py`** → `RECOMMENDATIONS` (the table shown on the Models
  page) and `recommend_for()` (the single model the wizard offers).

Keep those two consistent. They disagreed once. A 12 GB card was told
`gemma3:12b` by one screen and `gemma:7b` by the other, which is why
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

Everything downstream (transcription, scoring, tracking, rendering) is
untouched, because it only ever sees a local file and a transcript.

Two things worth copying from the existing sources:

- **Ask for H.264.** YouTube serves much of its catalogue in AV1, which
  almost no consumer GPU decodes in hardware; left alone it roughly doubled
  processing time. See `sources/ytdlp_common.py`.
- **Audience signals are optional and capped.** Twitch chat replay is read
  where it exists; Kick discards chat entirely and scores fine without it. If
  your platform has something similar, add it in `analysis/hype.py`: never as
  a hard dependency.

---

## Add an export format

`video_editor/export.py` renders the final file. `longform/profiles.py` holds
the 16:9 output shapes (`short_clips`, `clips_140`, `highlights`,
`edited_stream`). A new one is usually a new entry there rather than new
rendering code.

Anything that shells out to FFmpeg **must** use `core.binaries.ffmpeg()`.
Calling `"ffmpeg"` by bare name works on your machine and fails on every
installed copy, because creators do not have FFmpeg on their PATH. A test
enforces this.

---

## Bring your own model

Clips Kitty already runs YOLOv8 (ultralytics), OpenCV and TalkNet-ASD locally.
If you care about a kind of footage it does not understand, you do not need
permission or a redesign. You need one array.

**A signal is a numpy array over the video's timeline.** That is the whole
contract. `analysis/fusion.py` percentile-ranks each one to 0..1 with `_pct()`
and puts it in `peak_signals`:

```python
peak_signals = [visual_activity, audio_excitement, combined]
```

Anything in that list gets scanned by `_signal_peak_windows()`, and moments
above `scoring.signal_peak_percentile` become clip candidates, competing with
the transcript's own picks. So a detector that emits a per-second confidence
makes its moments candidates without touching the scorer.

Per-modality scanning matters and is deliberate: averaging first hides content
strong in only one channel, which is why a silent workout survives at all.
Add your signal as its own entry rather than folding it into `combined`.

1. **Write the signal.** Copy the shape of `analysis/hype.py`:

   ```python
   def audience_curve(url: str, video_id: str, duration: float) -> np.ndarray | None:
   ```

   Returning `None` is a first-class answer. Audience data is missing for
   every Kick VOD and the pipeline scores fine without it, so nothing may
   become a hard dependency. Give it a time budget too: `hype.py` caps its
   fetch at 180s precisely so a slow source cannot stall a job.

2. **Spend detector time inside candidates, not across the whole video.**
   Global passes are cheap signals only. `analysis/visual_features.py` uses
   FFmpeg to decode downscaled grayscale frames, ~20x faster than making
   OpenCV walk every frame, and saves the neural work for
   `reaction_for_window()`, which runs per candidate window. A YOLO pass over
   a 30-minute video to find 8 clips is most of an hour spent on footage
   nobody will watch.

3. **Tune before you code.** `config/settings.yaml` under `scoring` holds the
   fusion weights, `signal_peak_percentile`, and bonuses like `action_bonus`.
   Reweighting for your content is config, not a patch, and it is worth
   exhausting that before adding anything.

**A different detector** goes in `video/tracker.py`, at `_get_model()`. Note
`_infer_lock` just above it: ultralytics inference is **not thread-safe on one
model instance**, and renders run in parallel, so the single shared model is
serialised. Keep that if you swap the model, or give each thread its own.

**A different output shape** is usually a new entry in `longform/profiles.py`,
not new rendering code.

Sports, gaming, reactions, lectures, wildlife: these are examples of what
someone might want, not a roadmap. Nobody here is building them, and a fork
that does is the point rather than a problem.

**One rule if you want it merged rather than just forked.** Make it opt-in and
prove it on real footage. Tuning the pipeline for one kind of content has
already regressed another kind here, so behaviour changes ride behind an
explicit switch and default to today's behaviour. CI has no GPU, no Ollama and
no footage, so it cannot catch this for you: say in the PR which video you
tested and what changed.

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
testing against a real video: say which one in the PR.
