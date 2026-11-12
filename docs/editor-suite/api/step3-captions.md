# Step 3 API: caption fonts, animated captions and safe zones

The engine side of step 3 (#174, fonts and captions), as the desktop app's
caption controls use it. Every route here is on `http://127.0.0.1:8765`. The
three new routes are internal (`server/api_stability.py` lists none of them),
so their shape may change with the controls that use them.

Engine code: `assets/fonts/` (the font files, their `OFL.txt` files and
`manifest.json`), `video/fonts.py` (the font registry, staging, the
subtitles filter), `video/captions.py` (fonts, animations, safe zones in the
ASS file), `config/caption_presets.json`, `server/fonts_api.py` (routes),
`analysis/clip_edit.py` and `server/mcp.py` (validators), the eight burn
places (`video/cropper.py` four times, `video/cutter.py`,
`video_editor/export.py`, `gaming/compose.py`, `multilingual/burn.py`), and
`core/pipeline.py` (removing staged fonts once a clip is rendered).

**Nothing changes until a person picks something.** With a Windows font,
`animation` `"none"` (or left out) and `safe_zone` `"off"` (or left out), the
ASS file and every FFmpeg argument are byte for byte what they were
(`tests/test_render_argv_snapshots.py`, `tests/test_caption_animations.py`).

## caption_style

`caption_style` is the same plain object as before: in a job (`POST /jobs`,
`PATCH /jobs/{id}`), a watch's options, a clip's `render_opts`, and the
editor's render (`POST /clips/{clip_id}/render`). Two fields are new; every
field keeps its meaning.

| Field | Type | Default | Values |
|---|---|---|---|
| `font` | string | `"Arial"` | A Windows font (below), or a bundled family name exactly as `GET /fonts` gives it, such as `"Poppins ExtraBold"`. An unknown name burns in Arial, as before |
| `font_size` | int | 84 | 40–140, px on the 1920-tall canvas |
| `color` | `#RRGGBB` | `#FFFFFF` | |
| `position` | string | `"bottom"` | `bottom`, `middle`, `top` |
| `words_per_caption` | int | 3 | 1–6 |
| `uppercase` | bool | true | |
| `highlight` | bool | false | Lights up the word being said. Ignored when `animation` is `"karaoke"` |
| `highlight_color` | `#RRGGBB` | `#FFE600` | The highlight's colour, and karaoke's fill colour |
| `second_speaker` | bool | false | |
| `second_speaker_color` | `#RRGGBB` | `#5CE1FF` | Applies with every animation, karaoke included |
| `animation` | string | `"none"` | **New.** `none`, `pop`, `fade`, `bounce`, `karaoke`, `slide`. An unknown value is `none` at render time |
| `safe_zone` | string | `"off"` | **New.** `off`, `tiktok`, `reels`, `shorts`. An unknown value is `off` at render time |

Windows fonts: Arial, Arial Black, Impact, Verdana, Tahoma, Trebuchet MS,
Segoe UI, Georgia, Comic Sans MS, Courier New (and the script fonts Nirmala
UI, Yu Gothic UI, Malgun Gothic, Microsoft YaHei and Leelawadee UI, which the
app picks by itself for languages that need them).

`config/settings.yaml` has no caption style, so a job without one uses
`video/captions.py`'s `DEFAULT_STYLE`, which is the Classic preset. The UI's
`DEFAULT_CAPTION_STYLE` (`CaptionStyleControls.tsx`) now carries
`animation: 'none'` and `safe_zone: 'off'`, which render exactly as leaving
them out.

### Validators

- **AI edit assistant** (`analysis/clip_edit.py _clean_caption_style`):
  accepts a bundled font (matched without regard to case), `animation` and
  `safe_zone`; an unknown value is dropped, as an unknown font always was.
  Its prompt (`config/prompts/edit_clip.txt`) lists the bundled fonts and
  the new choices.
- **MCP** (`server/mcp.py _caption_style`, tool `queue_video`): accepts a
  bundled font (`"poppins"` finds `"Poppins ExtraBold"`), `animation` and
  `safe_zone` (`"youtube"` means `shorts`, `"instagram"` `reels`, `"none"`
  `off`). Anything else is refused in plain words with the list, for
  example `Caption animation 'wobble' is not one of: none, pop, fade,
  bounce, karaoke, slide.` The tool's schema lists both as enums.
- `POST /jobs` and the clip routes take `caption_style` as they always did,
  unchecked; the renderer falls back for anything it does not know.

## Animations

Written as ASS override tags at the start of each caption's text, timed in
milliseconds from that caption's own start. A caption's start, end and words
never change. Numbers are at the 1080x1920 canvas.

| `animation` | Tags | What it looks like |
|---|---|---|
| `none` | none | Today's captions |
| `pop` | `{\fscx80\fscy80\t(0,120,\fscx100\fscy100)}` | Grows from 80% to full size in 120 ms |
| `bounce` | `{\fscx80\fscy80\t(0,120,\fscx110\fscy110)\t(120,200,\fscx100\fscy100)}` | Grows past full size to 110% by 120 ms, settles at 100% by 200 ms |
| `fade` | `{\fad(120,80)}` | Fades in over 120 ms and out over the last 80 ms |
| `slide` | `{\move(x,y+40,x,y,0,150)}` | Rises 40 px into place in 150 ms. `x,y` is where the caption stands with no animation (below) |
| `karaoke` | `{\1c<fill>\2c<colour>}{\kf..}WORD {\k..}{\kf..}WORD` | Each word fills left to right with `highlight_color` while it is said |

- Scaling is around the caption's alignment point (the bottom middle of a
  bottom caption).
- **Line breaks under pop and bounce.** libass lays a caption out again at
  its scaled width in every frame, so a caption close to the space between
  the margins could change its number of lines part way through (one line
  at 80%, two at 100%, or two for a few frames at 110%). `video/caption_fit.py`
  works out where libass breaks the caption at 100% (the font file's advance
  widths and glyph ink, sized the way libass sizes a font, with WrapStyle 0's
  first pass and libass's own `wrap_lines_rebalance` step by step: each line
  measured without its trailing spaces, and after a move going on from where
  the moved line used to start, as libass does). When the breaks would
  differ at any size between 80% and the overshoot, the caption is written
  with them as `\N` and `\q2` (no wrapping), in every event of the caption:
  `{\q2\fscx80\fscy80\t(0,120,\fscx100\fscy100)}IMPOSSIBLE\NSHOT`. Otherwise
  nothing extra is written. Checked against the bundled FFmpeg's libass by
  burning 18,720 captions (22 fonts, 4 sizes each, safe zone off, TikTok and
  Reels, capitals and small letters) as libass wraps them and with the
  model's breaks: none came out different.
- **What is measured (QC fix round 3).** Latin, Greek and Cyrillic letters,
  digits, the punctuation `.,!?'":;&()`, curly quotes, `¿¡«»`, dashes and the
  ellipsis, the ASCII signs `-$%#@+/*=<>_|~^[]` and the backtick, and
  Chinese, Japanese and Korean characters, all drawn from the caption's own
  font. The bundled FFmpeg's libass has no Unicode line breaking: it breaks
  a line only at a space, never after a hyphen, a dash or any other sign,
  and never inside Chinese, Japanese or Thai text (burned to check, one
  word of two halves joined by each sign). So TWENTY-FIVE, $500 and 50% are
  words like any other. A glyph's ink is the box around every point of its
  outline, control points included, which is what libass goes by; the box
  in the glyph's own header can be tighter (Lilita One's `y` by 6 units,
  which broke `wi-fi nobody` a line early). A second burned sweep with
  these signs, small letters with fi/fl/ff and Chinese, Japanese and Korean
  text (two seeds of 4,680 Latin captions in 10 fonts, 1,440 in the three
  script fonts) came out with no caption broken differently from the model.
- **A caption that cannot be measured** has a character outside that list
  (an emoji; Thai, Hindi or Arabic, which libass shapes), a letter the font
  lacks (libass draws it from another font), or a decision within 0.25 px
  of going the other way where the two ways give different lines (about
  0.4% of captions). Pop and bounce do not scale such a caption at all,
  because libass would lay it out again at 80% and at 110%: it fades in at
  full size over the same 120 ms, `{\fad(120,0)}` (across the word
  highlight's events `\fade(a,0,255,0,120-o,...)`, as fade does), so its
  lines never change and it never overshoots. Only a font whose file the
  machine does not have (never one of Windows' own on Windows, nor an
  intact bundled one) is written with the plain pop or bounce tags: libass
  draws it in some other font, so there is nothing to measure.
- **Bounce's overshoot** is 110%, or less when the caption's widest line at
  110% would reach the margins (`floor(100 x (wrap width - 1) / widest line)`,
  at least 100%), so it never grows past them.
- `slide`'s `x,y`: across, the middle between the side margins; down, the
  bottom margin from the bottom for `bottom`, the top margin for `top`, the
  middle of the frame for `middle`. The 40 px scale with the canvas height
  (22 px on a 1920x1080 clip).
- **With the word highlight on** (one ASS event per word), a caption animates
  once, from its first event. An event that starts while the animation is
  still playing picks it up where it has got to and plays the rest, so a
  first word shorter than the animation never cuts it off. For an event
  starting `o` ms into the caption: pop `\fscx{80+20o/120}\t(0,120-o,...)`,
  bounce the size it has reached then `\t` to the overshoot and back with
  times shifted by `o`, slide `\move(x,y+40(1-o/150),x,y,0,150-o)`, and fade
  `\fade(a,0,255,0,120-o,t3,t4)` with `a` the fade-in's alpha at `o` and
  the fade-out over the caption's last 80 ms, even across a last word
  shorter than that. A caption that is one event keeps `\fad(120,80)`;
  events after the animation has played carry no tag (libass puts them
  where the slide ended).
- **Fade on a short caption (QC fix round 3).** With `\fad(120,80)` on a
  caption shorter than 200 ms the fade-out starts inside the fade-in, and
  libass jumps from one to the other between two frames (92% to 24% on a
  140 ms caption; 59% at most on an 80 ms one). The two fades together take
  at most 60% of a caption, kept 3:2: for a caption of `T` ms under 334,
  `budget = floor(0.6 T)`, in `round(0.6 x budget)`, out the rest. 140 ms
  gives `\fad(50,34)` with 56 ms held at full colour; 100 ms `\fad(36,24)`.
  From 334 ms up it is `\fad(120,80)` as before. The word highlight's
  events use the same two times, so both paths fade alike.
- **Captions on screen together.** `\t`, `\move` and `\pos` turn off
  libass's stacking of captions whose times overlap (hand-edited times can).
  A caption whose time overlaps another caption's is written with no pop,
  bounce or slide, so libass still stacks the two; `fade` and `karaoke`
  stack either way and are kept.
- **Karaoke** is one event per caption. `\kf` durations are in hundredths
  of a second: each word fills from when it starts to when it ends (or the
  next word starts), and a pause between words is an empty `{\kN}`. The
  words' real timings are used when they line up with the caption;
  otherwise, as for hand-edited text or transcripts without word timings,
  the caption's time is split by word length, the same as the highlight.
  The fill colour is `highlight_color`; the unfilled colour is the caption's
  colour, or `second_speaker_color` on the other speaker's lines.

## Safe zones

Guidance values, in px on a 1080x1920 clip: where each app lays its own
bottom bar (name, description, sound) and its column of buttons over the
video. They are the same numbers the Gaming layout editor uses
(`gaming/layouts.json`; a test keeps them equal), measured in September 2026
from replicas of each app's feed on a phone taller than 9:16, using
kreatli.com's safe zone checkers, as `docs/GAMING.md` records. None of the
apps publishes these for ordinary posts and they vary by device and by how
long the description is, so they are guidance, not a guarantee.

| `safe_zone` | Bottom bar | Top bar | Right column |
|---|---|---|---|
| `tiktok` | 420 | 180 | 190 |
| `reels` | 400 | 245 | 205 |
| `shorts` | 350 | 250 | 190 |

What changes in the caption style (`Style: Default`), at 1080x1920:

| `safe_zone` | MarginL / MarginR | MarginV, bottom | MarginV, top | Middle |
|---|---|---|---|---|
| `off` | 60 / 60 | 440 | 140 | frame middle |
| `tiktok` | 199 / 199 | 440 (480 with `slide`) | 200 | frame middle |
| `reels` | 214 / 214 | 440 (460 with `slide`) | 265 | frame middle |
| `shorts` | 199 / 199 | 440 | 270 | frame middle |

- A bottom caption stays at least 20 px above the app's bottom bar, a top
  caption 20 px below its top bar: `max(today's margin, bar + 20)`. Today's
  bottom margin (440) already clears all three bottom bars. A bottom caption
  that slides in starts 40 px below its place, so with `slide` it stands
  40 px higher again: `max(440, bar + 20 + 40)`, and even its first frame
  clears the bar.
- Both side margins become the width of the right-hand column plus 9 px:
  the margins bound the letters, and the style's outline (7 px) and shadow
  (2 px) reach that much further. The caption keeps clear of the buttons and
  stays centred (wrapping a little sooner).
- **A word wider than the safe area.** libass breaks lines only between
  words, so one word wider than the space between the safe-zone margins
  (682 px for TikTok and Shorts, 652 px for Reels, at 1080 wide) would still
  run under the buttons: CONGRATULATIONS at the default Arial 84 px is
  757 px wide, and an ordinary 8-letter word can be too wide at 140 px.
  With a safe zone, `video/captions.py` `_fit_scale` measures each caption's
  widest word (`caption_fit.widest_word`) and, when it is wider than that
  space less 1 px, draws that caption at `floor(100 x (space - 1) / widest
  word)` %: `{\fscx89\fscy89}CONGRATULATIONS EVERYONE`. The tag goes at the
  start of every event of the caption (the word highlight's, karaoke's,
  fade's and slide's), and pop and bounce grow to that size instead of 100%
  (`{\fscx71.2\fscy71.2\t(0,120,\fscx89\fscy89)}`); bounce overshoots only
  as far as the margins allow, so not at all when the word fills the space.
  Times, words, margins and the other captions are unchanged, and without a
  safe zone nothing is measured.
- **Languages written without spaces (QC fix round 3).** A Chinese,
  Japanese or Thai caption is one word to libass, which never breaks it, so
  it is measured and shrunk the same way: Chinese, Japanese and Korean
  glyph by glyph from the script font's file (`Microsoft YaHei`, `Yu Gothic
  UI`, `Malgun Gothic`; .ttc collections are read), Thai as the sum of its
  glyphs plus 4% for libass's shaping (burned widths were within 4 px of
  the plain sum). A 12-character Chinese caption at 120 px in TikTok's zone
  is written `{\fscx62\fscy62}...` and ends 7 px short of the column.
- **Late fixes (2026-11-11).** (1) The style's font size and bottom margin
  are scaled as `round(value * (height / 1920))`, the order they always
  had, so no frame height differs by 1 px from before step 3. (2) Karaoke
  is not written for right-to-left languages (`ar`, `he`, `fa`, `ur` and
  the like): libass lays the per-word runs out left to right, which
  reverses the line, so those clips burn as `none` with no highlight.
  (3) `build_captions(..., karaoke_untimed=True)`: the pipeline passes it
  when the caption lines were moved to an edited timeline (cuts or
  speed); karaoke then spreads each line's fill by word length instead of
  using word times from the unedited clip. Nothing else reads the flag.
  (4) A staged font's temporary copy is named `~<hex>.tmp`, never longer
  than the font's own file name, so a long clip folder keeps its font;
  when the folder is too long for Windows the job log says so in plain
  words and the captions use Arial.
- **Spaces (QC fix round 4).** libass draws a caption's text as written and
  breaks a line only at an ordinary space (U+0020): a doubled space is twice
  as wide, and words joined by a no-break space (U+00A0, U+202F; French puts
  one before `? ! : ;`) are one run it never breaks. What is measured has to
  be what is drawn, so with an animation or a safe zone on, the caption's
  text is written with single ordinary spaces (`video/captions.py`
  `_spaced`): every run of spaces of any kind (tabs, no-break and other
  Unicode spaces) becomes one space, the ends are trimmed, and the
  zero-width space, word joiner, byte-order mark and Mongolian vowel
  separator are dropped. A caption left with no text is not written. The
  word highlight and karaoke have always written their words this way. A
  French `?` after a no-break space can therefore start a line of its own
  when the line is full. With animation `none` and safe zone `off` (and on a
  landscape clip with no animation) the text is not touched: it is byte for
  byte what was typed, as before. `caption_fit.widest_word` splits only at
  U+0020, and `_scale_lines` holds (fades in at full size) any text that is
  not written this way.
- **Not measured:** a caption whose font file the machine does not have, or
  with a character outside the list above and Thai (an emoji, Hindi,
  Arabic), or a letter the font lacks. It is written as before, so one very
  long word in such a caption can still reach under the buttons. Hindi and
  Arabic have spaces, so libass still wraps them between words.
- A middle caption stays in the middle of the frame, which is inside every
  app's safe area.
- Safe zones apply to vertical clips only. On a landscape clip (1920x1080)
  `safe_zone` changes nothing.

## Bundled fonts

Twelve families, one static TTF each, from `github.com/google/fonts` (`ofl/`)
at commit `5bb28733cec313b63fbd415b574b5ae35c8aa8b8`, unmodified, under the
SIL Open Font License 1.1, each with its `OFL.txt`. About 1.1 MB in all (the
limit is 3 MB). `assets/fonts/manifest.json` lists for every file: `family`
(the TTF name table's family name, ID 1, which is what libass matches and
what goes in `caption_style.font`), `weight`, `file`, `sha256`, `bytes`,
`scripts`, `licence` (`OFL-1.1`), `licence_file`, `copyright`, `source_url`
and `commit`. NOTICE lists every family with its copyright line.

| Family | Weight | Scripts |
|---|---|---|
| Anton | 400 | latin, latin-ext, vietnamese |
| Archivo Black | 400 | latin, latin-ext |
| Bangers | 400 | latin, latin-ext, vietnamese |
| Barlow ExtraBold | 800 | latin, vietnamese |
| Bebas Neue | 400 | latin, latin-ext |
| Bowlby One | 400 | latin |
| Kanit ExtraBold | 800 | latin, latin-ext, vietnamese, thai |
| Lilita One | 400 | latin |
| Passion One Black | 900 | latin |
| Poppins ExtraBold | 800 | latin |
| Russo One | 400 | latin, cyrillic |
| Titan One | 400 | latin |

- `scripts` lists a writing system only when the file's character map has
  every letter of it, in both cases (`video/alphabets.py` `SCRIPT_LETTERS`):
  `latin` is A-Z plus the Western European letters (é, ñ, ü, ß, æ, ø, þ, œ...),
  `latin-ext` the whole of Latin Extended-A plus Romanian's Ș and Ț,
  `cyrillic` the Russian, Ukrainian, Belarusian, Bulgarian, Serbian and
  Macedonian letters, `cyrillic-ext` the extra letters of Kazakh, Kyrgyz,
  Tajik, Mongolian, Bashkir and Tatar, then `greek`, `vietnamese` and `thai`.
  `tests/test_caption_fonts.py` reads every file's character map
  (`video/fontfile.py`, standard library) and checks the list is exactly that.
- The list describes the font; it does not decide which clips use it (next
  section).
- A family that comes in one weight of 400 is written with `Bold` 0 in the
  ASS style, because it is already a heavy display face and libass would
  thicken it artificially; every other caption style is bold as before.
  The preview should draw such a family at `font-weight: 400` and the rest
  at their weight.
- Montserrat, Oswald, Rubik and Fredoka exist only as variable fonts at that
  commit, so they are not included.

### Other languages

`caption_font_for(language, font)` decides the font that burns. A bundled
font is kept only when its file has every letter the clip's language is
written with, in both cases (`video/alphabets.py` `letters_for`: A-Z and the
Western European letters, plus the language's own, such as Romanian's
Ă Â Î Ș Ț, Maltese's Ċ Ġ Ħ Ż, Esperanto's Ĉ Ĝ Ĥ Ĵ Ŝ Ŭ, Welsh's Ŵ Ŷ, Maori's
macrons, Azerbaijani's Ə, or the extra Cyrillic letters of Kazakh, Kyrgyz,
Tajik, Mongolian, Bashkir and Tatar). Otherwise libass would draw each
missing letter in another font in the middle of a word. Then the captions
burn in the Windows font for that language
(`Nirmala UI` for Hindi and the other Indic languages, `Yu Gothic UI` for
Japanese, `Malgun Gothic` for Korean, `Microsoft YaHei` for Chinese,
`Leelawadee UI` for Thai, `Segoe UI` for Vietnamese and Arabic, Persian,
Urdu), or in Arial for languages Arial covers (Russian and the other
Cyrillic languages, Greek, Polish, Czech, Turkish and the other Central
European languages, Hebrew). Examples: Russian with Russo One stays Russo
One; Romanian or Kazakh with Russo One burns in Arial; Russian with Bangers
burns in Arial; Thai with Kanit ExtraBold stays Kanit; Hindi with any bundled
font burns in Nirmala UI. Windows fonts are chosen exactly as before. When a
bundled file cannot be read, the manifest's `scripts` decide, and the render
then says the font is missing (below).

### Staging and fallback

libass on Windows finds installed fonts only. So the burn of an ASS file
that names a bundled font (in any style or `\fn` override, the caption,
hook or watermark styles alike) first copies that family's files into a
`fonts` folder beside the ASS file, and its subtitles filter becomes
`subtitles=<name>.ass:fontsdir=fonts`. FFmpeg's working folder is the ASS
file's, as it always was, so no Windows path is escaped.

- `video/fonts.subtitles_filter(ass_path)` builds the filter at all eight
  burn places. With Windows fonts only it returns `subtitles=<name>.ass`,
  exactly as before, and copies nothing.
- `stage()` copies a file only if it is there and matches its sha256. A
  file staged already by a clip rendering beside it is reused.
- `release(folder)` removes the staged files (only the ones the manifest
  names, then the folder if it is empty) once no `.ass` file is left in the
  folder. The pipeline calls it after deleting a clip's ASS file, and the
  translated-captions burn after its own, so clips rendering side by side in
  one folder never lose their fonts. A render that fails keeps its ASS file
  and so its staged fonts, as it keeps the ASS file today.
- **A render never fails over a font.** When a bundled font's file is
  missing or damaged, `build_captions` writes Arial in its place and the job
  log says: `The caption font Anton is missing or damaged in this copy of
  the app, so the captions use Arial`. A font name this copy of the app
  does not have at all (an engine built without `assets/fonts`, an
  unreadable `manifest.json`, or a style saved by a newer app) burns in
  Arial, as it always did, and the job log says `The caption font Poppins
  ExtraBold is not in this copy of the app, so the captions use Arial`,
  adding `(the app's own caption fonts are missing)` when the fonts folder
  or its manifest cannot be found. Only the log changes: the ASS file is
  the one Arial gives. If copying a font fails at burn time
  (a full disk), the burn goes ahead without `fontsdir` and the log says
  `Could not get the caption font ... ready (...); it was left out`.

### Where else captions burn

- **Make clips again, re-renders, the editor's render and the AI edit
  assistant** all go through `core/pipeline._render_files`, so they get the
  fonts, animations and safe zones the same way.
- **Translated captions** (`multilingual/burn.py`) build their ASS with the
  clip's caption style and the translation's language, so a language the
  font cannot write takes the Windows font for it (above).
- **Export presets and the editor lane (step 1)** re-encode a finished clip,
  whose captions are already burned in. They burn nothing, so nothing
  changes there.
- **Remote rendering**: the render worker runs the same `_render_files` with
  its own copy of the app's fonts. A worker PC on an **older** app would not
  know the bundled fonts, `animation` or `safe_zone` and would burn those
  clips in Arial with no animation and the old margins, without an error.
  So the render worker protocol (`remote_render/protocol.py` `PROTOCOL`) is
  now 2: the main PC refuses an older worker's pairing and heartbeat with 409,
  the worker shows "worker update required: install the same Clips Kitty
  version as the main PC", and it claims no jobs. In the Automatic mode the
  main PC then renders the clips itself, after the usual wait for a worker;
  with one worker chosen, the jobs wait for it as they do for a worker that
  is offline. A newer worker paired with an older main PC is refused the
  same way. This applies to every job, captions or not: the two PCs need
  the same app version.

## Presets

`config/caption_presets.json` (shipped in the engine build beside
`settings.yaml`):

```json
{"about": "...", "presets": [{"id": "classic", "name": "Classic", "style": {...}}, ...]}
```

Each `style` is a complete `caption_style` with every field above. Picking
a preset fills every control; changing a control afterwards just edits the
style. `second_speaker` is off in every preset: the UI should keep the
person's own second-speaker switch and colour when they pick a preset.

| `id` | `name` | Font | Size | Words | Highlight | `animation` | `safe_zone` | Position |
|---|---|---|---|---|---|---|---|---|
| `classic` | Classic | Arial | 84 | 3 | off | `none` | `off` | bottom |
| `bold_pop` | Bold pop | Poppins ExtraBold | 92 | 2 | on, #FFE600 | `pop` | `tiktok` | bottom |
| `clean_fade` | Clean fade | Barlow ExtraBold | 78 | 4, not uppercase | off | `fade` | `reels` | bottom |
| `karaoke` | Karaoke | Archivo Black | 80 | 4 | fill #FFE600 | `karaoke` | `tiktok` | bottom |
| `bounce` | Bounce | Titan One | 86 | 2 | on, #7CFF4F | `bounce` | `tiktok` | middle |
| `slide_up` | Slide up | Bebas Neue | 110 | 3 | off | `slide` | `shorts` | bottom |

Classic is exactly `DEFAULT_STYLE` (a test checks it), and every preset
passes both validators unchanged.

## Routes

### GET /fonts

```json
{"fonts": [
  {"family": "Anton", "weight": 400, "scripts": ["latin", "latin-ext", "vietnamese"],
   "files": ["Anton-Regular.ttf"], "urls": ["/fonts/Anton-Regular.ttf"], "licence": "OFL-1.1"},
  ...
]}
```

In the manifest's order (alphabetical). `urls` are paths on the engine:
prefix `http://127.0.0.1:8765`. An empty list when this copy has no fonts
folder.

### GET /fonts/{file}

The font file, `Content-Type: font/ttf`, `Cache-Control: public,
max-age=31536000, immutable`, `Access-Control-Allow-Origin: *` (a web font
is fetched in CORS mode, and the packaged app's `file://` page has no origin
the engine's CORS settings list; the files are the app's own public fonts,
and the Host check still refuses any name but 127.0.0.1 and localhost).
`{file}` is a bare file name from `files`
(case matters). Anything else is 404 `No bundled font file has that name.`:
a name not in the manifest, a path (`anton/Anton-Regular.ttf`, `../...`,
encoded or not), `manifest.json`, an `OFL.txt`, or a font file that is
missing or does not match its sha256.

### GET /caption-presets

The presets file as it is, `{"about": ..., "presets": [...]}`, or
`{"presets": []}` when it cannot be read.

### For the preview

- The renderer's CSP gains `font-src 'self' http://127.0.0.1:8765`, and
  nothing else in it changed. Load each bundled family with `@font-face`
  from its URL, with the family name from `GET /fonts` and its weight.
  `ui/src/renderer/src/lib/captionLook.ts` does this with the FontFace API
  (`display: swap`, so a name shows in the fallback font while its face
  loads). The browser dev server's relaxed copy of the CSP
  (`ui/vite.config.mts`) has the same `font-src`.
- `ui/src/renderer/src/lib/types.ts`: `CaptionStyle.animation`
  (`CaptionAnimation`), `CaptionStyle.safe_zone` (`CaptionSafeZone`),
  `BundledFont` (one entry of `GET /fonts`) and `CaptionPreset` (one entry
  of `GET /caption-presets`).
- The preview's animation is a CSS approximation of the tags above; the
  burned video is the real thing. The safe-zone overlay can draw the
  table's numbers (bottom bar, top bar, right column) at the preview's
  scale.
- Electron-builder needs nothing for the fonts: they ship inside the
  engine build (`clips-studio.spec` bundles `assets/fonts` like
  `assets/outro`), which electron-builder already copies whole.
