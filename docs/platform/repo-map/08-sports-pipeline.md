# Sports framework (shared core + Soccer + Basketball) as a reference plugin implementation

Repo: `/home/user/clips-studio`, branch `claude/open-platform-w4eh9g`. Read-only survey. Every claim cites a repo-relative path; "(inferred)" marks inference.

## 1. What it is, in one paragraph

A sport is NOT a pipeline of its own. `sports/__init__.py:9-12` states it: "It adds no pipeline of its own. The sport's profile plugs into the same scoring the gaming profile uses (analysis/fusion.py), with the same Whisper, the same AI (any provider), the same renderer, queue, watches and publishing." The sport contributes (a) a data entry in `config/sports.yaml`, (b) a `SportProfile` object that answers the questions `analysis/fusion.py` already asks a gaming profile (`sports/core/profile.py:1-13`, `analysis/fusion.py:77-82` `gaming = sport`), (c) optional module-level hooks on the sport package (`profile`, `prepass`, `prepass_wait`, `framing`, `hotwords`, `READS_DESCRIPTION`) dispatched by `sports/__init__.py`, and (d) optional duck-typed attributes on the profile that `core/pipeline.py` / `analysis/fusion.py` probe with `getattr` (`look`, `title_rules`, `check_titles`, `report_data`, `one_play_per_clip`, `board`, `curves`, `footage`). The shared core (`sports/core/`) turns signals into `SportEvent`s, windows them, groups replays, attaches them to scored candidates, applies a capped bonus, and chooses which clips survive the Highlights/Period choice.

## 2. Files

| Path | Lines | Role |
|---|---|---|
| `sports/__init__.py` | 228 | Registry `SPORTS` dict, yaml loader `knowledge()/spec()`, `available()` (GET /sports), `clean()` (option validation), `option()`, `profile_for()`, `framing()`, `prepass()`, `prepass_wait()`, `reads_description()`, `hotwords()`, `direction()`, `sound_groups()` |
| `sports/core/profile.py` | 255 | `SportProfile` dataclass (the contract), `STANDARD_WEIGHTS`, `TYPED_AT`, `weights_for(config)` |
| `sports/core/events.py` | 95 | `SportEvent` dataclass, `valid()`, `group_moments()`, `best_per_moment()` |
| `sports/core/windows.py` | 30 | `window(pre, post, t, *, min_len, max_len, video_end, lag, post_extra)` |
| `sports/core/detect.py` | 364 | `moments(profile, segments, *, curves, voice, screen, board, video_end, min_len, max_len, extra_after, extra_types) -> list[SportEvent]`; `runs()`, `peaks()`, `goal_roar()`, `_with_listed()` |
| `sports/core/select.py` | 42 | `event_types(spec, highlights)`, `post_extra(spec, highlights)`, `select(events, spec, highlights, period)` |
| `sports/core/clips.py` | 229 | `windows_to_add()`, `attach()`, `bonus()`, `mark()`, `choose()`, `report()`; constants `BONUS_MAX=20`, `WINDOWS_MAX=40`, `TYPED=0.66` |
| `sports/core/scorebug.py` | 266 | Shared OCR score-bug finder/reader: `texts()`, `crop()`, `score_lines()`, `find_box()`, `cluster()`, `pieces()`, `rec_line()`, `keyframe_crops()` (ffmpeg keyframe crop pass) |
| `sports/core/reels.py` | 196 | Story reels: `KINDS=("recap","teams","players")`, `Reel` NamedTuple, `plan()`, `file_name()`, `chapters()`, `join()` (ffmpeg concat) |
| `sports/core/events_import.py` | 178 | User-pasted match events: `Listed` dataclass, `parse(text, spec)`, `kickoffs()`, `place()`, `MAX_TEXT=4000` |
| `sports/soccer/__init__.py` | 48 | `profile()`, `framing()`, `prepass()` |
| `sports/soccer/profile.py` | 23 | `SoccerProfile(SportProfile)`: overrides `classify` only |
| `sports/soccer/scoreboard.py` | 352 | `Reading`, `ScoreChange`, `Scoreboard` dataclasses; `read_video(path, duration, cancel=None) -> Scoreboard` |
| `sports/soccer/ball.py` | 165 | YOLO ball/person detector `_model(name)`, `detect(model, frame, imgsz)`, `plan()`, `compute(clip_path, model_name, imgsz, ...)` |
| `sports/basketball/__init__.py` | 110 | `READS_DESCRIPTION=True`, `profile()`, `hotwords()`, `framing()`, `prepass_wait()`, `prepass()` |
| `sports/basketball/profile.py` | 618 | `BasketballProfile(SportProfile)`: many overrides (section 5) |
| `sports/basketball/scoreboard.py` | 1046 | Own `Reading`/`ScoreChange`/`Scoreboard` (two-row bug, quarter, clock) on top of `sports/core/scorebug.py` |
| `sports/basketball/reactions.py` | 309 | Cutaways from the court: `read_shots()`, `cutaways()`, `read_names()`, `moments()` |
| `sports/basketball/look.py` | 108 | `look(profile, finalists, video_path, llm)`: vision-LLM check of reaction shots via `analysis/game_vision` |
| `sports/basketball/action.py` | 344 | Framing: `compute(clip_path, model_name, imgsz, sample_fps, ...)`, `hidden_rows()` (keeps score bug out of crop) |
| `sports/basketball/keyframes.py` | 92 | ffprobe keyframe listing |
| `sports/basketball/titles.py` | 657 | `Play`, `problems()`, `written()`, `check(profile, candidates, metas, rewrite)` |
| `sports/basketball/names.py` | 253 | `for_video(title, description, teams) -> str|None` (Whisper hotwords), `sides()`, `teams()` |
| `sports/basketball/commentary.py` | 316 | `words()`, `scorer(segments, t, points, names, ...)` who scored per the commentary |
| `config/sports.yaml` | 507 | All sport data (section 6) |
| `docs/SPORTS.md` | 694 | User/dev doc; "## Adding a sport" at `docs/SPORTS.md:646-694`, "## The API" at `docs/SPORTS.md:602-645` |
| `tests/test_sports.py` (22 tests), `tests/test_sports_detect.py` (39), `tests/test_sports_import.py` (7), `tests/test_sports_pipeline.py` (13), `tests/test_sports_reels.py` (8), `tests/test_basketball.py` (138) | | All `pytest.importorskip("yaml")`; synthetic signals, no models (docstrings at each file's top) |

## 3. The profile contract (`sports/core/profile.py`)

`@dataclass class SportProfile` (`sports/core/profile.py:29`). Fields:

| Field | Type / default | Who sets it | Cite |
|---|---|---|---|
| `name` | `str` | the sport package's `profile()` | `profile.py:31` |
| `option` | `dict` = `{}` : the cleaned job option (`sports.clean`) | `profile()` | `profile.py:32` |
| `weights` | `dict` = `STANDARD_WEIGHTS` (`text .30, visual .20, reaction .20, audio .20, engagement .10`) | `weights_for(config)` | `profile.py:21,33,247-255` |
| `games` | `list` = `[]` (gaming interface, unused for a match) | — | `profile.py:34` |
| `split_layout` | `bool` = False | — | `profile.py:35` |
| `footage` | `str` = `"broadcast"` / `"sideline"` | `resolve_footage()` | `profile.py:38` |
| *(dynamic)* `curves` | `dict[str, np.ndarray]` set by `core/pipeline.py:961-976` (`_sport_inputs`): one 0..1 curve per `sound_curves()` name plus `"crowd_heard"` | pipeline | `core/pipeline.py:955-976` |
| *(dynamic)* `board`, `shots`, `cutaways`, ... | whatever keys the sport's `prepass()` returns are `setattr`'d onto the profile | pipeline | `core/pipeline.py:942-944` |
| *(dynamic)* `report_data` | dict from `sports.core.clips.report()` | fusion | `analysis/fusion.py:545` |
| *(dynamic)* `listed_report` | dict set by `detect._with_listed` (`clips.py:208` `getattr(profile, "listed_report", None)`) | detect | `sports/core/clips.py:208-215` |

Methods and properties, grouped as the file groups them:

**A. The gaming-path interface (what `analysis/fusion.py` asks, since `gaming = sport` at `fusion.py:77-82`)**

| Signature | Default behaviour | Cite |
|---|---|---|
| `resolve_footage(self, board) -> str` | `option["footage"]` if broadcast/sideline else `"broadcast"` iff `board.box` else `"sideline"` | `profile.py:39-49` |
| `spec -> dict` (property) | `{**sports.spec(name), "label": f"{label} match"}` | `profile.py:51-53` |
| `label -> str` | yaml `label` or `name.title()` | `profile.py:55-57` |
| `genre -> str` | `name` | `profile.py:59-61` |
| `game -> str` | `""` | `profile.py:63-65` |
| `game_at(self, start, end=None) -> tuple[str, str]` | `("", name)` | `profile.py:67-68` |
| `genre_track(self, seconds) -> list[str]` | `[name] * seconds` | `profile.py:70-71` |
| `spec_for(self, genre) -> dict` | `{"label": "<label> match", "highlights": yaml highlights}` for `analysis/game_vision.py` | `profile.py:73-76` |
| `reads_screen -> bool` | yaml `read_screen` (default True; both sports set `false`) | `profile.py:78-82`, `config/sports.yaml:122,389` |
| `screen_lexicon -> dict` | `{"events": {"generic": [], name: yaml screen_text}, "menu": []}` for `analysis/game_text.py` | `profile.py:84-88`, used `analysis/fusion.py:895` |
| `sound_weights(self) -> dict` | `{name: yaml sounds}` for `game_audio.sound_signal` | `profile.py:90-92` |
| `guidance(self, kind="clips", start=None, end=None) -> str` | prompt text built from yaml `highlights` + `callouts`; `kind="rerank"` shorter | `profile.py:94-117` |

**B. What a sport can change (soccer's values are the defaults)**

| Signature | Default | Cite |
|---|---|---|
| `scoring_types -> tuple` | yaml `scoring_events` or `("goal","penalty_goal","own_goal")` | `profile.py:119-124` |
| `celebration -> float` | yaml `celebration_seconds` or 45 | `profile.py:126-131` |
| `sound_curves(self) -> tuple` | yaml `curves` or `("crowd","whistle")` | `profile.py:133-136` |
| `confirmed_type(self, change, event) -> str` | event's type if scoring, else `scoring_types[0]` | `profile.py:138-144` |
| `context_weight(self, event) -> float` | `1.0` | `profile.py:146-150` |
| `extra_moments(self, events, segments, *, curves, video_end, min_len, max_len) -> list` | `events` unchanged | `profile.py:152-156` |
| `one_play_per_clip -> bool` | `False` | `profile.py:158-164` |
| `clip_span(self, candidate, event) -> tuple[float,float]` | union of candidate window and event window | `profile.py:166-171` |

**C. The sport's moments (data-driven from yaml)**

| Signature | Behaviour | Cite |
|---|---|---|
| `events_spec(self) -> dict` | yaml `events` | `profile.py:174-175` |
| `importance(self, event_type) -> int` | yaml `events[type].importance` | `profile.py:177-181` |
| `event_label(self, event_type) -> str` | yaml label or type with `_`->space | `profile.py:183-184` |
| `window_of(self, event_type) -> tuple[float,float]` | `(pre, post)` from yaml, fallback `big_moment`, then (10, 8) | `profile.py:186-188` |
| `crowd_lag -> float` | yaml `crowd_lag_seconds` | `profile.py:190-192` |
| `replay_within -> float` | yaml `replay_within_seconds` or 90 | `profile.py:194-196` |
| `replay_said(self, text) -> bool` | whole-word match of yaml `replay_words` | `profile.py:198-202` |
| `_callouts(self) -> list[tuple[str, re.Pattern]]` | compiles yaml `callouts` (word/phrase, case-insens. unless ALLCAPS) + `patterns` (regex) | `profile.py:204-217` |
| `callouts_in(self, text) -> list[tuple[str,str]]` | `(event type, matched text)` per hit | `profile.py:219-226` |
| `classify(self, said, signals) -> tuple[str, float]` | needs `TYPED_AT=2` independent signals; callout + >=1 signal -> typed (`max(kinds, key=importance)`, conf `(1+others)/3`); signals alone -> `"big_moment"`; else `("", 0.0)` | `profile.py:228-244`, `profile.py:23-25` |

**D. Optional duck-typed hooks the engine probes with `getattr` (not on the base class)**

| Attribute | Probed at | Used for |
|---|---|---|
| `look(finalists, video_path, llm) -> int` | `analysis/fusion.py:593-602` | vision check of finalists (basketball reactions) |
| `title_rules() -> str` | `core/pipeline.py:512-519` | extra rules to `generate_metadata_batch` |
| `check_titles(candidates, metas, rewrite) -> list` | `core/pipeline.py:520-528` | re-write wrong titles |
| `report_data` | `core/pipeline.py:470-472`, `longform/process.py:122-123` | outcome `"sport"` |
| `one_play_per_clip` | `analysis/fusion.py:287,565`, `sports/core/detect.py:241` | grouping / dedupe |
| `board` | `analysis/fusion.py:280`, `core/pipeline.py:978`, `sports/core/clips.py:112,141,156` | scoreboard |
| `curves` | `analysis/fusion.py:277` | sound curves |
| `footage` | `sports/core/detect.py:137`, `sports/core/clips.py:119,226` | sideline vs broadcast |
| `resolve_footage` | `core/pipeline.py:987-990` | |
| `scoring_types` | `core/pipeline.py:982-983` | log word for a score |

**E. Module-level hooks on the sport package (`sports/<name>/__init__.py`), dispatched by `sports/__init__.py`**

| Hook | Required? | Signature | Dispatcher |
|---|---|---|---|
| `profile(config, option, video=None) -> SportProfile` | yes | | `sports/__init__.py:165-171` `profile_for` |
| `framing(clip_path, config) -> dict` (`{"mode":"track","path":[(t,x),...], "rows"?: ...}`) | no | | `sports/__init__.py:174-181` |
| `prepass(video_path, duration) -> dict` (keys become profile attrs) | no | | `sports/__init__.py:184-192` |
| `prepass_wait(duration) -> float` | no (900 s default) | | `sports/__init__.py:195-204` |
| `READS_DESCRIPTION: bool` | no | | `sports/__init__.py:207-215` |
| `hotwords(option, video) -> str|None` | no | | `sports/__init__.py:218-228` |

## 4. Registration (the plugin seam as it exists today)

1. `config/sports.yaml`: one top-level key per sport (`config/sports.yaml:15 soccer:`, `:180 basketball:`). Header comment `config/sports.yaml:6-7`: "Adding a sport is an entry here, a sports/<name>/ package for anything it needs in code, and a line in sports/__init__.py."
2. The registry line: `SPORTS = {"soccer": "sports.soccer", "basketball": "sports.basketball"}` at `sports/__init__.py:24`. Each value is imported lazily with `importlib.import_module` only when a job asks (`sports/__init__.py:170,180,190,203,215,226`).
3. `available()` (`sports/__init__.py:50-69`) iterates `SPORTS` and skips any name without a yaml entry; `clean()` (`sports/__init__.py:72-134`) accepts only `name in SPORTS` with a yaml entry.
4. `docs/SPORTS.md:680-692` repeats the three steps and says the app then offers it in the Sport menu "and nothing else changes".

Yaml path is hard-coded: `CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sports.yaml"` (`sports/__init__.py:21`), cached by `@lru_cache(maxsize=1) knowledge()` (`sports/__init__.py:29-33`). A single file, a single process-wide cache: no per-plugin yaml, no reload.

## 5. What Soccer and Basketball each override

| Hook | Soccer | Basketball |
|---|---|---|
| `profile()` | `SoccerProfile(name="soccer", option, weights)` `sports/soccer/__init__.py:12-15` | `BasketballProfile(..., video_text=(title, description))` `sports/basketball/__init__.py:16-21` |
| `classify` | penalty+goal -> `penalty_goal`; penalty+save -> `penalty_miss`; own_goal drops plain goal `sports/soccer/profile.py:11-23` | and-one etc. (`sports/basketball/profile.py:335-353`) |
| `prepass` | OCR score box via `sports/soccer/scoreboard.read_video` -> `{"board": Scoreboard}` `sports/soccer/__init__.py:30-48` | score bug + cutaways -> `{"board", "shots", "cutaways"}` `sports/basketball/__init__.py:60-110` |
| `prepass_wait` | (none -> 900 s) | `max(900, duration)` `sports/basketball/__init__.py:52-58` |
| `framing` | `sports/soccer/ball.compute` (YOLO ball/people) `sports/soccer/__init__.py:18-28` | `sports/basketball/action.compute` with `hide_scoreboard` `sports/basketball/__init__.py:33-47` |
| `hotwords` / `READS_DESCRIPTION` | none | `names.for_video(title, description, teams)`; `READS_DESCRIPTION = True` `sports/basketball/__init__.py:13,24-30` |
| `one_play_per_clip` | False (default) | True `sports/basketball/profile.py:78-83` |
| `extra_moments` | default | reactions as moments (`sports/basketball/profile.py:97-168`, `reactions.moments`) |
| `clip_span` | default | a confirmed basket's own window `sports/basketball/profile.py:291-298` |
| `guidance` | default | basketball text `sports/basketball/profile.py:300-324` |
| `importance` | yaml | reactions = 100 when a reactions choice `sports/basketball/profile.py:355-359` |
| `confirmed_type` | default | by points / commentary / situation `sports/basketball/profile.py:363-386` |
| `context_weight` | 1.0 | quarter, clock, margin `sports/basketball/profile.py:517-560` |
| `look` | none | `sports/basketball/look.look` (vision LLM) `sports/basketball/profile.py:326-331` |
| `title_rules`, `check_titles`, `play_of` | none | `sports/basketball/profile.py:420-490`, `titles.check` |
| extra yaml keys | `framing`, `highlights_choices`, `periods`, `footage_choices` | `+ scoring_events, celebration_seconds, reaction_events, sound_groups (buzzer), curves, reactions{people_tall,...}, match_minutes, listed_words, period_menu, period_word, listed_hint` (`config/sports.yaml:195-201,295,396-430,500-502`) |
| own scoreboard | one-row bug, `Reading.minute/clock`, `Scoreboard.halftime` `sports/soccer/scoreboard.py:48-78` | two-row bug, `Reading.period/clock(seconds left)/halves`, `ScoreChange.points/side/shown` `sports/basketball/scoreboard.py:91-134` |

Basketball reuses soccer code directly: `from sports.soccer.ball import _model, detect` (`sports/basketball/action.py:299`, `sports/basketball/reactions.py:147,154`). So "sport packages" already depend on each other, not only on `sports/core`.

## 6. Stage sequence for a Sports job (function names)

Entry: `POST /jobs` etc. -> `server/api.py:480-488` `payload["sport"] = sports.clean(body.sport)` (400 on `ValueError`); mode clash check `server/api.py:541-544`. Queue worker `server/jobs.py:215-218` copies it to `cfg["clips"]["sport"]`; `server/jobs.py:223-231` turns `sports.direction(payload["sport"])` (teams/request) into `cfg["clips"]["focus"]` (the clip-direction feature). `core/modes.sport(config)` (`core/modes.py:82-93`) reads `config["clips"]["sport"]["name"]`.

Then in `core/pipeline.py` (Shorts path; `longform/process.py:82-123,200-228` mirrors it):

| # | Stage | Function / line | Sport-specific part |
|---|---|---|---|
| 0 | download / probe | `core/pipeline.py:223-229` | a 9:16 match forces `vertical_live=True` (keeps composition) |
| 1 | prepasses started (threads) | `MatchReading(config, video)` `core/pipeline.py:387`, class at `:871-930` | thread `game-sounds-prepass`: `game_audio.listen(video.path, sports.sound_groups(name))` (PANNs); thread `sport-prepass`: `sports.prepass(config, path, duration)` -> sport's OCR/YOLO pass. Hype thread `:343-359` also collects chat messages when `sport_name`. |
| 2 | transcribe | `core/pipeline.py:389-412` | `hint = _listening_for(config, video, url)` (`:993-1007`) -> Whisper `hotwords=` |
| 3 | wait prepasses, build profile | `match.finish(hype_out)` `core/pipeline.py:434`, `:916-930`; `_sport_inputs` `:933-990` | `cancel.wait(thread, 900 or prepass_wait)`; `profile = sports.profile_for(config, video)`; `setattr(profile, key, value)` for each prepass key; `profile.curves[...] = sound_signal(...)` per `sound_curves()`; `resolve_footage(board)` |
| 4 | score | `find_clips(..., sport=sport_profile, chat=chat, sounds=sounds)` `core/pipeline.py:437-452` -> `analysis/fusion.py:68,77-82` | `gaming = sport`; weights = `sport.weights` |
| 4a | moments | `analysis/fusion.py:269-298` | `sport_detect.moments(...)`; `sport_clips.windows_to_add(...)`; `highlights.score_windows(..., guidance=sport.guidance("windows"))`; `c.source = "sport"` |
| 4b | attach + bonus | `analysis/fusion.py:392-393, 510-518` | `sport_clips.attach`, `sport_clips.bonus`, `sport_clips.mark` -> `subscores["sport_*"]` |
| 4c | choose | `analysis/fusion.py:538-547` | `sport_clips.choose(...)` -> kept/dropped/notes; `sport.report_data = sport_clips.report(...)` |
| 4d | one-play dedupe | `analysis/fusion.py:560-580` | `one_play_per_clip` |
| 4e | look | `analysis/fusion.py:593-602` | `sport.look(finalists, video_path, llm)` |
| 4f | on-screen text | `analysis/fusion.py:894-897` | `gaming.screen_lexicon` (sport's words) |
| 5 | outcome | `core/pipeline.py:470-473` | `outcome["sport"] = sport_profile.report_data`; `db.set_outcome` |
| 6 | titles | `core/pipeline.py:512-528` | `title_rules()`, `check_titles()` |
| 7 | render | `core/pipeline.py:1188-1190, 1365-1376, 1550-1560` | `_sport_framing` -> `sports.framing(name, clip, config)`; fallback to face tracker on failure; `opts["sport"]` persisted for re-renders (`:1478-1479`) |
| 8 | reels | `core/pipeline.py:666-669, 685-745` `_sport_reels` | `reels.plan/join`, registered as clips with `subscores {"sport_reel","sport_label","sport_parts"}` |

## 7. Data shapes

**Job option** (output of `sports.clean`, `sports/__init__.py:72-134`): `{"name", "highlights", "period", "footage"?, "teams"?, "reels"?: [..], "events"?: str, "request"?: str}`. Mirrored by `ui/src/renderer/src/lib/types.ts:391-406` `SportOption`. MCP exposes it as `SPORT_PARAM` at `server/mcp.py:225-234` with `"description": "The sport: soccer or basketball"` (hard-coded names).

**GET /sports** item (`sports/__init__.py:50-69`): `{id, label, highlights:[{id,label}], periods:[{id,label}], footage:[{id,label}], period_menu?}`; UI type `SportChoice` `types.ts:408-418`.

**`SportEvent`** (`sports/core/events.py:14-32`): `type, t, confidence, importance, start, end, team, player, period, minute, signals: list[str], is_replay, group, confirmed, when, context, person`; methods `overlaps()`, `why()`.

**Candidate subscores written by `sports.core.clips.mark`** (`sports/core/clips.py:78-105`): `sport_event, sport_label, sport_t, sport_group, sport_why, sport_team?, sport_player?, sport_period?, sport_minute?, sport_when?, sport_context?, sport_person?, sport_replay?, sport_bonus?`; `choose()` adds `required` and `sport_lift` (`clips.py:173-175,191-193`). Reels add `sport_reel, sport_label, sport_parts` (`core/pipeline.py:733`). Candidate `source = "sport"` (`analysis/fusion.py:296`). UI reads them in `types.ts:78-99` and `ui/src/renderer/src/lib/sports.ts:207` `sportMoment()`; `analysis/metadata.py:190-230` reads `sport_when/sport_label/sport_team/sport_context/sport_why/sport_player` for titles.

**Report** (`sports/core/clips.py:report`, `clips.py:198-230`): `{sport, highlights, period, found: {label: n}, big_moments, replays_grouped, score, scoreboard: bool, footage, clips, notes: [..]}` -> `outcome["sport"]`; UI `SportReport` `types.ts:21`, rendered by `ui/src/renderer/src/components/SportsNote.tsx`.

**Scoreboard** (soccer `sports/soccer/scoreboard.py:48-134`): `Scoreboard(box, readings: [Reading], changes: [ScoreChange], halftime)` with `final()`, `teams()`, `period_at(t)`, `minute_at(t)`, `video_time_at()`, `hidden(lo, hi)`. The core (`detect.py:168-197`, `clips.py:141-160`) calls `board.changes/.hidden/.period_at/.minute_at/.box/.final/.teams`; basketball's adds `period_number` (`sports/basketball/profile.py:526`). This is an implicit protocol (inferred: no base class).

**Framing dict** (`sports/soccer/__init__.py:18-28`, `core/pipeline.py:1370-1375`): `{"mode": "track", "path": [(t, x_fraction), ...], "rows"?: (top, bottom)}`; `"led"` counts are popped for logging.

## 8. Progress, errors, logging, cancellation

- Logging is `print()` with 6-space indentation inside stages (e.g. `sports/soccer/__init__.py:36,43-46`, `core/pipeline.py:978-990`, `analysis/fusion.py:289-290,540`). No logger object.
- Progress events: `core/progress.py:30 def emit(**event)`; the pipeline emits `stage="transcribe"|"analyze"|"done"` (`core/pipeline.py:390,417,676`). A sport emits no progress of its own; its work is invisible between stages except via prints (inferred from grep: no `progress.emit` in `sports/`).
- Errors: every sport hook is wrapped so failure degrades, never aborts: prepass thread `core/pipeline.py:908-914` (`except Exception` -> print), sounds `:898-906`, framing `_sport_framing` `:1550-1560` -> None -> face tracker, `look` `analysis/fusion.py:598-602`, reels `core/pipeline.py:717-722`, basketball cutaways `sports/basketball/__init__.py:104-110`. Missing OCR -> `game_text.available()` False -> skip with a note (`sports/soccer/__init__.py:33-36`). Missing PANNs -> `panns.available()` False (`core/pipeline.py:900-902`).
- Cancellation: `core.cancel` — `cancel.check_active` passed into readers (`sports/soccer/__init__.py:42`, `sports/basketball/__init__.py:69,87`), `cancel.wait(thread, seconds, video_id)` (`core/pipeline.py:925`), `cancel.CancelledError` re-raised (`sports/basketball/__init__.py:107`, `analysis/fusion.py:598`).
- Timeouts: sounds thread 900 s; sport prepass `sports.prepass_wait` (`core/pipeline.py:924`); result dropped with a note if still running (`:926-929`).

## 9. Model dependencies and where they load

| Model | Used by | Loader | Availability check / weights |
|---|---|---|---|
| PANNs (AudioSet tagger, `panns_mobilenetv1.pth`) | crowd / whistle / buzzer curves: `game_audio.listen` from `MatchReading._listen` (`core/pipeline.py:894-906`) | `analysis/panns.py` (`_model` global `:43`) | `analysis/panns.py:53-60` `available()`: torch importable + `models/panns_mobilenetv1.pth` (or `sys._MEIPASS`); labels `config/audioset_labels.txt` |
| RapidOCR (PaddleOCR models on onnxruntime) | score bug (`sports/core/scorebug.py`, both scoreboards), basketball cutaway names, on-screen text | `analysis/game_text.py:103-110` `_ocr` lazy `RapidOCR()` global `_engine`; sports import the private `_ocr` (`sports/basketball/scoreboard.py:980,1016`, `sports/basketball/action.py:261`) | `analysis/game_text.py:57-60` `available()` |
| YOLOv8n (ultralytics, `yolov8n.pt`, classes person+ball) | framing (`sports/soccer/ball.py`, `sports/basketball/action.py`), basketball reactions (`reactions.read_shots` -> `ball.detect`) | `sports/soccer/ball._model(name)` `sports/soccer/ball.py:92-107`: own cache, `core.binaries.yolo_weights(name)` (`core/binaries.py:131`), `core.gpu.cuda_usable()`, shared `video.tracker._infer_lock` | model name from yaml `framing.model` (`config/sports.yaml:140,406`) |
| Whisper | transcription with `hotwords=` (`core/pipeline.py:396-412`) | `transcription.transcriber.transcribe` | `config["whisper"]["model"/"device"]` |
| LLM (any provider) | `highlights.score_windows` with `sport.guidance("windows")` (`analysis/fusion.py:292-294`), titles (`core/pipeline.py:514-528`), `look` via `analysis/game_vision` (`sports/basketball/look.py:58,86`) | `create_backend(_with_usable_model(config["llm"]))` `core/pipeline.py:431` | |
| ffmpeg / ffprobe | keyframe crops (`sports/core/scorebug.py:218`), keyframe listing (`sports/basketball/keyframes.py:59`), reel joins (`sports/core/reels.py:172`), bug detection (`sports/basketball/scoreboard.py:783`) | `core.binaries.ffmpeg/ffprobe` | |
| OpenCV | frame grabs (`video.capture.video_capture`, `cv2`) | `sports/soccer/scoreboard.py:318-321`, `sports/basketball/look.py:68-70` | |

Resource notes from the code: score-bug read ~1.2 s/keyframe, 25 min on a 79-min NBA game (`sports/basketball/__init__.py:52-58`); soccer 1,510 crops in 19 s + ~16 ms recogniser each (`sports/core/scorebug.py:7-13`); YOLO at imgsz 1280 (`config/sports.yaml:140,402-406`). All prepasses run on daemon threads concurrently with Whisper (`core/pipeline.py:889-895`).

## 10. Files read / written

- Read: `config/sports.yaml` (`sports/__init__.py:21`), `config/gaming.yaml` via `analysis.gaming.knowledge()` for shared sound groups (`sports/__init__.py:41-47`), `config/audioset_labels.txt` (`analysis/panns.py:34`), `models/panns_mobilenetv1.pth`, YOLO weights via `core/binaries.yolo_weights`, the source video, transcripts dir `data_dir / "transcripts"` (`core/pipeline.py:400`).
- Written: clips in `clip_dir` by the normal renderer; reels `clip_dir / reels.file_name(reel)` and a `.pre-card.mp4` intermediate (`core/pipeline.py:711-722`); DB rows via `db.set_clip`, `db.set_outcome`, `db.log_rejection` (`core/pipeline.py:453-472, 729`). Nothing sport-specific written to disk outside clips (inferred from grep of `sports/`: only `reels.join` writes files).

## 11. Configuration keys consumed

`config/sports.yaml` per-sport keys read by the core: `label, highlights, crowd_lag_seconds, replay_within_seconds, replay_words, events{type:{label,importance,pre,post}}, callouts{type:[words]}, patterns{type:[regex]}, read_screen, screen_text, sounds, sound_groups, curves, scoring_events, celebration_seconds, framing{model,imgsz,hide_scoreboard}, highlights_choices{id:{label,events|all,post_extra?,focus?,takes_request?}}, periods{id:label}, period_menu, period_word, footage_choices{id:label}, listed_hint, listed_words, match_minutes, reaction_events, reactions{...}` (sites: `sports/core/profile.py`, `sports/__init__.py:46-134`, `sports/core/select.py`, `sports/core/clips.py:110-124,165`, `sports/basketball/__init__.py:36-40,88-96`, `sports/core/events_import.py:64`).

Job config keys: `config["clips"]["sport"]`, `config["clips"]["min_duration"/"max_duration"/"min_score"/"focus"/"vertical_live"]`, `config["scoring"]["profiles"]["sports"]["weights"]` or `config["scoring"]["weights"]` (`sports/core/profile.py:247-255`), `config["scoring"]["look_at_game"]` (`analysis/fusion.py:596`), `config["whisper"]`, `config["llm"]`.

## 12. How close to a plugin system, and what stops a sport living outside the repo

What already looks like a plugin system:
- A registry keyed by id -> module path, lazy-imported (`sports/__init__.py:24,170`).
- A documented, mostly data-driven contract (`SportProfile` + 6 optional module hooks), where every hook is optional except `profile()` and failures degrade gracefully (section 8).
- The engine never imports `sports.soccer` / `sports.basketball` by name: `core/pipeline.py`, `analysis/fusion.py`, `server/api.py` import only `sports` and `sports.core.*` (`analysis/fusion.py:35-41`, `core/pipeline.py:898,910,922,939,1001,1555`, `server/api.py:483,2934`). `analysis/fusion.py:35-41` even tolerates the whole `sports` package being absent (`sport_clips = sport_detect = sport_select = None`), and `GET /sports` returns `[]` on ImportError (`server/api.py:2933-2937`).
- The UI is data-driven from `GET /sports` for the sport list, highlights, periods and footage (`ui/src/renderer/src/lib/sports.ts:5-46`, `SportFields.tsx:28-80`).
- Tests show a sport can be swapped by monkeypatching `sports.profile_for` / `sports.framing` / `pipeline.MatchReading` (`tests/test_sports_pipeline.py:85,201,271,301`).

A third sport inside the repo needs exactly: (1) a yaml entry under `config/sports.yaml` with at least `label, highlights, events, callouts, highlights_choices (incl. best), periods (incl. full)` (`sports/__init__.py:84-95` requires `highlights` and `period` choices to exist, `clean` defaults to `best`/`full`); (2) `sports/<name>/__init__.py` with `profile(config, option, video=None)` returning a `SportProfile` (can be the base class with `name=<name>`); (3) one entry added to `SPORTS` at `sports/__init__.py:24`. Optional: `prepass`, `prepass_wait`, `framing`, `hotwords`, `READS_DESCRIPTION`, a `profile.py` subclass, plus `SPORT_ICONS` / `SPORT_NAMES` / `HIGHLIGHT_HINTS` entries in `ui/src/renderer/src/lib/sports.ts:75-111` for icon, display name and per-choice hints (the UI works without them, inferred from `SPORT_ICONS[s.id] ? ... : ''` at `SportFields.tsx:50`).

What stops a sport living OUTSIDE the repository today:

| Blocker | Where | Severity |
|---|---|---|
| Registry is a literal dict; no discovery (entry points, directory scan, env var) | `sports/__init__.py:24` | hard |
| Single yaml at a hard-coded path, cached once; a sport's data must be a top-level key in the repo's file | `sports/__init__.py:21,29-33` | hard |
| Module path must be importable as `sports.<name>` from the engine's `sys.path`; the frozen (PyInstaller) build bundles exactly `collect_submodules("sports")` as hidden imports and copies `config/sports.yaml` into `config/` (`clips-studio.spec:51-53,115-116`); weights resolve via `sys._MEIPASS` (`analysis/panns.py:46-50`, `core/binaries.py:131-145`) | `sports/__init__.py:170`, `clips-studio.spec:51-53` | hard for the packaged app: an external sport has no import path and no bundled data |
| Sport code imports private engine internals: `analysis.game_text._ocr` (`sports/basketball/scoreboard.py:980,1016`, `action.py:261`), `analysis.game_vision._jpeg` (`look.py:86`), `video.tracker._infer_lock` (`sports/soccer/ball.py:113`), `video.framing.{is_cut,small_gray,HoldMove,stable_target}` (`ball.py:137`, `action.py:112,123,301`), `video.capture.video_capture`, `core.binaries.{yolo_weights,ffmpeg,ffprobe}`, `core.gpu.{cuda_usable,torch_device}`, `core.cancel`, `core.modes.probe_size`, `analysis.gaming.knowledge` (`sports/__init__.py:44`), `sports.core.scorebug.*` | listed in section 5 and above | medium: needs an SDK surface for OCR, detector, frame grab, cancel, binaries |
| Cross-sport import: basketball imports `sports.soccer.ball._model/detect` | `sports/basketball/action.py:299`, `reactions.py:147,154` | medium: the detector belongs in core |
| Profile is a dataclass subclass, and engine code probes ad-hoc attributes (`board`, `curves`, `look`, `title_rules`, `check_titles`, `report_data`, `listed_report`, `footage`) by `getattr` | section 3D | medium: contract is implicit |
| Scoreboard object protocol is implicit (`changes`, `box`, `readings`, `final()`, `teams()`, `period_at()`, `minute_at()`, `hidden()`, `period_number()`) | `sports/core/detect.py:168-197`, `clips.py:141-160`, `core/pipeline.py:978-983` | medium |
| `detect.moments` hard-codes the signal set and thresholds (`CROWD_AT`, `VOICE_AT`, `CLUSTER`, ...) and the soccer-shaped flow (goals, replays, score changes) | `sports/core/detect.py:29-42,94-241` | design: a non-ball sport can only hook via `classify/extra_moments/confirmed_type` |
| UI hard-codes `SPORT_ICONS`, `SPORT_NAMES`, `HIGHLIGHT_HINTS`, `COMING_SOON` | `ui/src/renderer/src/lib/sports.ts:75-111` | soft: cosmetic |
| MCP tool schema hard-codes "soccer or basketball" | `server/mcp.py:225-234` | soft |
| Sound classes come from `config/gaming.yaml` `sound_groups` + yaml `sound_groups`; PANNs label set is fixed (`config/audioset_labels.txt`) | `sports/__init__.py:41-47`, `analysis/panns.py:34` | design |
| `sports.clean` validation (option schema) is fixed: `highlights, period, footage, teams, reels, events, request` only; no sport-defined extra fields | `sports/__init__.py:72-134` | medium |
| Tests assume the two sports (`tests/test_basketball.py`, `tests/test_sports.py` "the soccer data") | | soft |

## 13. Core platform functionality the SDK should expose vs sport-specific functionality a developer would implement

| Core platform (SDK should expose) | Evidence of current use | Sport-specific (developer implements) | Evidence |
|---|---|---|---|
| Registry / discovery: register `name -> module` and a yaml/JSON data entry; `available()`, `clean()` option validation | `sports/__init__.py:24,50-134` | A data spec: `events`, `callouts`, `patterns`, `highlights_choices`, `periods`, `footage_choices`, `sounds`, `screen_text`, `replay_words`, windows | `config/sports.yaml` |
| `SportProfile` base class with the gaming-path interface and the data-driven defaults (`classify`, `callouts_in`, `importance`, `window_of`, `guidance`, ...) | `sports/core/profile.py` | Subclass overrides: `classify` combos, `confirmed_type`, `context_weight`, `extra_moments`, `clip_span`, `one_play_per_clip`, `guidance`, `importance` | `sports/soccer/profile.py`, `sports/basketball/profile.py` |
| Moment detection (`detect.moments`), windows, grouping, bonus, choose, report (`sports/core/{detect,windows,events,clips,select}.py`) | `analysis/fusion.py:269-298,392,510-547` | Optional `extra_moments` sources (reactions, cutaways) | `sports/basketball/reactions.py` |
| Sound curves per group (PANNs via `game_audio.listen/sound_signal`), `sound_groups` merge | `core/pipeline.py:894-906,955-976` | Which curves (`curves`) and extra AudioSet classes (`sound_groups: buzzer`) | `config/sports.yaml:396-398` |
| Score-bug finder/reader primitives: `find_box`, `cluster`, `pieces`, `rec_line`, `keyframe_crops`, plus the OCR engine itself (`game_text._ocr` should become public) | `sports/core/scorebug.py`, `analysis/game_text.py:103` | `parse`/`clock` of the bug's text, `Scoreboard` semantics (periods, minutes, points) | `sports/soccer/scoreboard.py`, `sports/basketball/scoreboard.py` |
| Object detector (YOLO person/ball) with weight resolution, GPU selection and the shared inference lock; frame grabbing (`video.capture`), cut detection (`video.framing`) | `sports/soccer/ball.py:92-130`, `core/binaries.py:131`, `core/gpu` | `framing(clip_path, config)`: how to plan the crop path from detections (ball vs rim vs reaction, hide bug rows) | `sports/soccer/ball.plan`, `sports/basketball/action.plan/hidden_rows` |
| Prepass scheduling: threads, `prepass_wait`, `cancel.check_active`, `cancel.wait`, degrade-on-failure | `core/pipeline.py:871-930` | `prepass(video_path, duration) -> dict` and `prepass_wait(duration)` | `sports/*/__init__.py` |
| Transcription hotwords and video metadata (title/description fetch via `sources.dispatch.description`) | `core/pipeline.py:993-1007` | `hotwords(option, video)`, `READS_DESCRIPTION` | `sports/basketball/__init__.py:13,24-30`, `names.py` |
| LLM access: `score_windows(guidance=...)`, `generate_metadata_batch(rules=...)`, vision (`game_vision`) | `analysis/fusion.py:292`, `core/pipeline.py:514-528`, `sports/basketball/look.py` | Prompt guidance text, `title_rules()`, `check_titles()`, `look()` | `sports/basketball/profile.py:300-331,420-490` |
| Clip subscore vocabulary `sport_*`, `required`, `sport_lift`, `source="sport"`, the outcome `report` schema and the UI that renders them | `sports/core/clips.py:78-105,198-230`, `SportsNote.tsx`, `ClipCard.tsx:38` | Values for `team/player/when/context/person` | `SportEvent` fields |
| Reels (`plan/join/chapters`) and `events_import` (user-listed moments) | `sports/core/reels.py`, `sports/core/events_import.py` | `listed_words`, `match_minutes`, `listed_hint` | `config/sports.yaml:426-430,502` |
| Logging/progress primitives (`print` convention, `core/progress.emit`) | `core/progress.py:30` | human-readable notes (`notes` in report) | `sports/core/clips.py:123-127` |
| UI: Sport menu, highlight/period/footage menus, chips, clip-card moment line driven by `GET /sports` + subscores | `ui/src/renderer/src/lib/sports.ts`, `SportFields.tsx` | Icon, display name, per-choice hint text (today hard-coded in the UI; should move to the sport's data) | `ui/src/renderer/src/lib/sports.ts:75-111` |

## 14. Extra signatures worth copying into an SDK

- `core/progress.py:30 def emit(**event) -> None`; `set_handler(handler)` `:18`; `set_thread_tags(**tags)` `:23`. Pipeline stages emitted: `transcribe`, `analyze`, `done` (`core/pipeline.py:390,417,676`).
- `core/cancel.py`: `check_active()` `:52`, `check(video_id)` `:46`, `wait(thread, timeout=None, video_id=None)` `:80`, `run(cmd)` `:99`, `class CancelledError(Exception)` `:32`, `set_active(video_id)` `:18`.
- `analysis/game_audio.py:70 def listen(path, groups, tag=None, chunks=None) -> dict[str, np.ndarray] | None`; `:131 def sound_signal(heard, groups, genres, genre_sounds, ...)`.
- `analysis/game_text.py:57 def available() -> bool`; `:113 def read_frame(img, events, menu, ocr=None)`; `:141 def read_screen(path, windows, genre_at, lexicon, grab=None, read=None, ...)`; private `_ocr(img)` `:103`.
- `analysis/panns.py`: `available()` `:53`, `load()` `:114`, `tag(windows, batch=32)` `:142`, `weights_path()` `:46`.
- Basketball `Scoreboard` methods beyond soccer's: `seen_twice()`, `halves()`, `last_period()`, `period_number(t)`, `clock_at(t)`, `score_before(t)`, `when(t)` (`sports/basketball/scoreboard.py:136-226`).
- `sports/basketball/reactions.py:245 def moments(profile, events, found, *, curves, video_end, min_len, max_len, react_within, focus) -> list[SportEvent]`.
- Only four engine files import `sports`: `server/jobs.py`, `server/api.py`, `analysis/fusion.py`, `core/pipeline.py` (grep `import sports|from sports` outside `sports/` and `tests/`), plus `longform/process.py` which reaches it through `core.pipeline.MatchReading` / `_sport_reels` (`longform/process.py:88,201`).

## 15. Open questions for the platform design

1. Should `config/sports.yaml` be split so each sport ships its own data file (the profile already reads everything through `sports.spec(name)`, `sports/__init__.py:36-38`), with `knowledge()`'s single `lru_cache` replaced by a per-sport loader?
2. The implicit `Scoreboard` protocol (`changes`, `box`, `readings`, `final()`, `teams()`, `period_at()`, `minute_at()`, `hidden()`, and basketball's `period_number()` reached from `BasketballProfile.context_weight`) needs a declared base class or Protocol before a third sport can rely on it (inferred).
3. `detect.moments` is soccer-shaped (goals, score changes, replays); for sports without a scoreboard or a crowd (e.g. esports-like or individual sports) the only extension points are `classify`, `extra_moments`, `confirmed_type` (inferred from `sports/core/detect.py:94-241`).
4. Private helpers the sports import (`analysis.game_text._ocr`, `analysis.game_vision._jpeg`, `video.tracker._infer_lock`, `sports.soccer.ball._model`) need public SDK names; which module owns the shared YOLO detector (currently `sports/soccer/ball.py:92-107`)?
5. The option schema in `sports.clean` (`sports/__init__.py:72-134`) is closed; should a sport declare extra option fields in its data, and how would the UI (`SportFields.tsx`) and MCP (`server/mcp.py:225-234`) render them?
6. Progress: sports print only; should the SDK give a sport `progress.emit(stage="sport-prepass", ...)` so the UI can show the 25-minute score-bug read (`sports/basketball/__init__.py:52-58`)?
