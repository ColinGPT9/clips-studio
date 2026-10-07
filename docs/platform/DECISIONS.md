# Decisions

One entry per judgment call: what was decided, the alternatives, why, and how to undo it. Newest at the bottom.

## D1 · Test baseline is taken with OpenCV and yt-dlp installed in the sandbox

**Decided:** install `opencv-python-headless` and `yt-dlp` into the sandbox interpreter (not into the repository's requirements) before recording the baseline.
**Alternatives:** record the baseline with CI's bare set (pyyaml, ruff, pytest, requests). That set is what CI runs, but this sandbox already has numpy, so the tests guarded by `pytest.importorskip("numpy")` try to import `cv2` and error at collection instead of skipping. With both packages present the suite collects completely and the baseline is 1633 passed, 14 failed, 72 skipped.
**Why:** a baseline with collection errors cannot say which tests must keep passing.
**Undo:** nothing in the repo changed; the numbers in PROGRESS.md are the only artifact.

## D2 · Per-platform research notes are gathered while the repository map is being written

**Decided:** start the Phase 0B note-taking agents while the Phase 0A readers are still running, and write the research synthesis (`docs/platform-research.md`, including every "borrow / avoid" verdict) only after `repo-map.md` is complete.
**Alternatives:** strictly finish 0A before any research starts, as the brief orders.
**Why:** the brief's reason for the order is that precedents only matter in relation to what Clips Kitty already is. That applies to the synthesis, which still waits for the map. Raw notes in the 13-point template do not depend on the map, and the sandbox runs only a few agents at a time, so serialising the two would cost the build phases several hours.
**Undo:** none needed; the notes are inputs, and anything in them that turns out to be irrelevant is simply not carried into the research document.

## D3 · The repository map was produced by parallel readers, not one pass

**Decided:** one reader per subsystem (packaging, API routes, other API surfaces, pipeline core, Gaming, Sports, AI stack and models, video/FFmpeg, desktop UI, extension seams, the Gaming-vs-Sports shared interface, and a live probe of the API), each writing notes that cite file paths; the map is assembled from them in the main thread and spot-checked against the code.
**Alternatives:** read the whole engine in the main thread. Too slow for 55 000 lines of Python plus the UI.
**Why:** the brief allows parallel workers for research and the map is the same kind of work; synthesis stays in the main thread.
**Undo:** none needed.

## D4 · Research stopped at the brief's rule

**Decided:** stop Phase 0B once every matrix cell was filled or marked "not confirmed" and every Appendix B question could be answered: 24 extension ecosystems and 16 gaming and video products. Framedrop's site could not be reached, so its row is marked "not confirmed" rather than researched further.
**Alternatives:** keep reading tier-3 products and incidents.
**Why:** brief §6.1: "Do not let it grow past that. The build phases need the rest of the night."
**Undo:** add notes under `docs/platform/research-notes/` and rows to the matrices.

## D5 · Five design defaults chosen in the research document, each listed for the owner in §7.7

**Decided:** (1) the manifest file is `clipskitty.yaml` at the plugin repository's root; (2) no "Verified" tier: tiers are Official, Listed (automated checks only), Installed from a link, Blocked; (3) the existing local API stays unauthenticated, and only the new plugin-manager routes (install, update, enable, remove) require the desktop app's per-session secret; (4) the registry lives in a `registry/` folder in this repository until a separate repository exists; (5) the SDK keeps the repository's AGPL-3.0 licence until the owner decides.
**Alternatives:** (1) `plugin.yaml` (too generic to grep for) or a `[tool.clipskitty]` table in `pyproject.toml` (ties the manifest to Python); (2) a "Verified" badge with no process behind it, which the brief forbids; (3) a token on every route, which breaks the OBS plugin, the MCP server and scripts; (4) a new repository, which the brief forbids tonight; (5) relicensing, which is the owner's legal call.
**Why:** each default is reversible and keeps existing clients working; the reasoning is in `docs/platform-research.md` §7.6 and §7.7.
**Undo:** each is a single choice in the architecture document and the code that follows it.

## D6 · httpx added to the sandbox and a second baseline recorded

**Decided:** install `httpx` 0.28.1 into the sandbox interpreter (not into the repository's requirements) and record a second baseline: 1879 passed, 14 failed, 23 skipped, with the same 14 failures as the first.
**Alternatives:** keep the first baseline, in which every `TestClient` test skips; Phase 2's contract tests would then skip here too and prove nothing.
**Why:** the brief asks for contract tests that pin current behaviour, which needs the API tests to run.
**Undo:** nothing in the repository changed; CI still installs only pyyaml, ruff, pytest and requests, so the new API tests keep the repository's `pytest.importorskip` guards and skip there.

## D7 · Architecture choices that shape the build (docs/platform-architecture.md)

**Decided:** (1) plugins run only as a child process with a job folder; nothing third-party is imported into the engine; (2) a plugin replaces only the detection step (`clip_direction` and `find_clips` in `process_video`), and Clips Kitty keeps titles, rendering, captions and the library; (3) a job names its plugin in one optional field, `pipeline: {id, version, settings}`, exclusive with Sports, Gaming scoring and Longform; (4) plugin progress is reported as the existing `analyze` stage with a `fraction`, so the UI's stage list is untouched; (5) a plugin's own Python packages are wheels only, hash-pinned, in its own environment, and a separate line the user agrees to at install; (6) a blocked plugin is refused at run time and flagged, never deleted by Clips Kitty; (7) the registry client's default index is the copy bundled with the app until the owner publishes one, so no URL is invented; (8) one previous version of each plugin is kept for rollback.
**Alternatives:** (1) in-process plugins as in ComfyUI or Blender, which give plugin code the engine's full access and cannot load in the frozen build; (2) plugins that own the whole pipeline, which would make every plugin re-implement rendering and captions; (3) separate `pipeline` and `pipeline_settings` fields; (4) a new `plugin` stage, which needs the UI's mirrored stage list changed; (5) letting pip build source packages at install, which runs their code before the user has used the plugin; (6) automatic removal, as VS Code's block list does; (7) a guessed GitHub URL; (8) keeping every version, which Colin's disk cannot afford.
**Why:** each keeps the existing API, engine and runtime as they are and follows a research finding (`docs/platform-research.md` §7.4, §7.6).
**Undo:** each is one section of the architecture document and, once built, one module.

## D8 · "Stable" means what docs/API.md already called supported

**Decided:** the 59 HTTP routes and the WebSocket that `docs/API.md` documents as supported are labelled stable; five routes it only mentions in passing (clip words, editing a clip, editing a queued job, storage clean-up, choosing a thumbnail) are experimental; every other route is internal by default. The architecture's build plan had suggested starting from a smaller stable set.
**Alternatives:** a small stable core (health, jobs, queue, library), with the rest experimental. That would quietly withdraw a promise `docs/API.md` has made since 1.1, which says supported routes change only with a CHANGELOG note.
**Why:** Phase 2 formalises the existing API; it does not redraw it. Internal-by-default means a new route needs a deliberate decision before it is promised.
**Undo:** move entries between labels in `server/api_stability.py`, then run `python scripts/gen_api_reference.py --update-contract`.

## D9 · A plugin process inherits the user's environment minus credentials, not an allow-list

**Decided:** the plugin process gets the parent environment minus Clips Kitty's own variables (`CLIPS_*`, `CLIPSKITTY_*`) and any variable whose name contains KEY, TOKEN, SECRET, PASSWORD, PASSWD, CREDENTIAL, COOKIE or AUTH (`clipskitty_sdk/host.py plugin_env`). The architecture's first design was an allow-list (`PATH`, `SYSTEMROOT`, `TEMP`, locale).
**Alternatives:** the allow-list. It breaks ordinary plugins: model and GPU libraries read `HOME`/`USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `CUDA_*` and proxy settings, and a list that grows plugin by plugin is a list nobody keeps right.
**Why:** the point is that Clips Kitty never hands a credential over by accident; the deny-list does that. Neither version is a wall, since the plugin runs as the user and can read the user's files, and the docs say so.
**Undo:** replace the loop in `plugin_env` with an allow-list; `tests/test_plugin_runner.py::test_the_process_gets_no_clips_kitty_settings_or_credentials` keeps checking the credentials stay out.

## D10 · The job's minimum score is not applied to a plugin's moments

**Decided:** a plugin's ranges go straight to rendering, cut to the job's clip limit; `min_score` is not applied.
**Alternatives:** filter by `min_score` as `find_clips` does. Plugins' scores are optional and on their own scale, and an unscored plugin's moments get rank-based scores from 90 down to 50, so a minimum of 55 (the default) would silently drop a plugin's 8th moment onwards.
**Why:** a plugin returns the moments it stands behind; the clip limit is the user's control over how many.
**Undo:** filter `candidates` by `config["clips"]["min_score"]` at the end of `plugins/runner.find_clips`.

## D11 · Python plugins use a Python the user has; per-plugin environments come later

**Superseded by D28** (2026-10-07): the installed app now runs Python plugins on its own Python.

**Decided:** `{python}` in a plugin's command resolves to the `plugins.python` setting, else the engine's own interpreter in a source checkout, else `python`, `py` or `python3` on `PATH`. The installed app does not ship a Python for plugins, so a Python plugin needs one on the PC, and the runner says so in words when none is found. A plugin's own packages in an environment of its own (`run.python_requirements`, wheels only, hash-pinned) are designed, not built in Phase 3.
**Alternatives:** bundle a Python in the installer (packaging work that cannot be tested here), or build per-plugin environments tonight before the manager exists.
**Why:** the contract and runner had to come first; plugins that need no third-party packages (the Phase 5 example) or ship their own executable as `run.command` work today.
**Undo:** none needed; both pieces are additive when built.

## D12 · What the design review changed

**Decided:** every finding of the brief §9 review was accepted. The ones that change what gets built: (1) plugin model files go in `<data_dir>/plugin-models/`, fetched with plain HTTPS from the standard library, with no `huggingface_hub` dependency and no `HF_HUB_CACHE` set for the engine; (2) no snapshots of the whole plugin set, only per-plugin rollback; (3) the manifest's JSON Schema is generated from the validator; (4) the plugin-manager session secret comes from the desktop app in `CLIPS_KITTY_SESSION_SECRET`, or is written to `<data_dir>/plugins/session.secret` when the engine runs on its own, and is described as stopping web pages and stray calls only; (5) Git installs disable hooks, submodules and symbolic links; (6) the job form's Pipeline choice stays hidden until a community pipeline is installed; (7) listing links open only through a confirmed path; (8) a separate CI job installs FastAPI, httpx, OpenCV and Pillow and runs the platform tests.
**Alternatives:** the architecture as first written: the Hugging Face library's cache under `<data_dir>/models/` (inside Ollama's store, and moving Whisper's downloads if the variable were set), plugin-set snapshots borrowed from ComfyUI, a hand-kept schema, a session secret described as protecting against any local process, the Pipeline choice always visible, and platform tests that skip in CI.
**Why:** each first version either contradicted another part of the design, changed existing behaviour, overclaimed a protection, or added something the brief did not ask for.
**Undo:** each is one paragraph of the architecture and, once built, one module; the CI job is one block in `.github/workflows/ci.yml`.

## D13 · The example pipeline is MIT-licensed

**Decided:** `examples/pipelines/scene-cut-highlights/LICENSE` is MIT, while the repository is AGPL-3.0.
**Alternatives:** AGPL-3.0 like the rest of the repository. Developers are told to copy the example as the start of their own plugin; under the AGPL their plugins would inherit it, which the brief's aim (anyone can build and share pipelines without forking) does not want decided by accident.
**Why:** the example was written tonight and contains no code from the rest of the repository; it is the owner's to license, and a permissive default is the one that matches how it is meant to be used.
**Undo:** replace the LICENSE file and the manifest's `license` field. This sits beside D5 (the SDK's own licence), which is still the owner's call.

## D14 · A Git install reads the commit's files out of Git; GitHub's archive is the fallback

**Decided:** with Git on the PC, the plugin manager fetches the one commit into an empty bare repository and writes each file from Git's object store (`git cat-file --batch`), with hooks pointed at an empty folder, credential helpers off and only `https` and `file` transports allowed. GitHub's archive of the commit is used only when Git is not installed.
**Alternatives:** the design review's suggestion to prefer the archive download; or `git clone` and a checkout with hooks disabled.
**Why:** reading objects never checks anything out, so no hook, no filter (Git LFS included) and no symbolic link is ever involved, and Git checks every file against the commit hash. The archive is GitHub's word for what the commit holds and is not checked against the hash. Most Windows PCs have no Git, so the archive path stays for them.
**Undo:** swap the two branches in `plugins/sources.fetch`.

## D15 · Planning an install needs the session header too

**Decided:** `POST /plugins/plan` requires `X-Clips-Kitty-Session`, like the routes that change what is installed. The architecture's table had it open.
**Alternatives:** leave it open because it installs nothing.
**Why:** a plan fetches from an address the caller chooses and writes files into the data folder. FastAPI 0.142 (the version tested) already refuses a body that is not sent as JSON, and a web page cannot send JSON to the engine without a CORS preflight the engine refuses, so this is a second lock rather than the only one. The app and scripts have the secret anyway.
**Undo:** drop `dependencies=guarded` from the plan route in `plugins/api.py` and the plan line in `tests/test_plugin_api.py`.

## D16 · Smaller plugin-manager choices

**Decided:** (1) the engine writes the current session secret to `<data_dir>/plugins/session.secret` at every start, including when the desktop app supplied it (the architecture had only an engine started on its own write it), so a script always finds the one in use; the file is readable only by the user, who can already read the engine's environment. (2) Installed versions live under `plugins/installed/<publisher>/<name>/<version>/` rather than directly under `plugins/`, so a publisher called `runs` or `staging` cannot collide with the manager's own folders. (3) Installing the same version again replaces its files (in a new folder, `1.0.0~2`), which is the developer's loop when installing from a folder. (4) Removing a plugin also deletes its stored keys. (5) `GET /plugins` also lists the three built-in modes, marked Official. (6) A Git source must be the full 40-character commit; no branch or tag names. (7) A plugin is refused over 5000 files or 1 GB.
**Alternatives:** the architecture's wording for (1) and (2); refusing a same-version install; keeping keys after removal (a reinstall would not ask again); a separate route for built-ins; resolving a tag at install time (the user would no longer be approving a fixed set of files).
**Why:** each keeps the user's view and the files on disk in step, and none changes existing behaviour.
**Undo:** each is a few lines in `plugins/session.py`, `plugins/manager.py` or `plugins/sources.py`.

## D17 · Registry and Marketplace search choices

**Decided:** (1) the index build reads each manifest from GitHub's raw file address at the listed commit (`raw.githubusercontent.com/<owner>/<repo>/<commit>/...`), and tests read the same paths from a folder; (2) the index carries the latest listed version's manifest fields at its top level and each version's `requires` and `permissions`; (3) search is a word-by-word match over weighted fields with a small alias table in `plugins/registry.py` and a listing's own `aliases`, every word required; (4) when two indexes list the same id, the bundled one wins, then settings order; (5) block lists from every cached index apply, also after an address is removed from settings; (6) a listing shows "Unofficial" whenever it names `games`; (7) `POST /marketplace/refresh` needs no session header, since it fetches only addresses the user put in settings; (8) the CI Python job runs `scripts/build_registry_index.py --check`.
**Alternatives:** (1) cloning each repository in CI, which the architecture rules out; (3) a search library or fuzzy matching, which would add a dependency and rank broad listings above specific ones less predictably; (4) merging listings from several indexes, which lets a second index override a listing's repository; (5) dropping a removed address's block list, which would unblock a harmful version by editing settings; (6) a maintainer-set flag, which needs a process that does not exist yet.
**Why:** each keeps the registry a static file with no server and no new dependency, and keeps a user from being shown more trust than exists.
**Undo:** each is one function in `plugins/registry.py` (or one line in `plugins/api.py` and the CI file).

## D18 · Marketplace screen choices

**Decided:** (1) search stays in the engine (`GET /marketplace`); the screen only shows what it returns, so the Phase 8 tests check the screen's logic against `permissions`, `sources` and the manifest validator rather than against `registry.py` on the fixture index as the architecture said; (2) Install is enabled only after the user ticks one sentence for each risk that applies: a source nobody listed, a listing nobody reviewed, data leaving the PC, a required outside account; (3) a developer's links open in the browser only after a native dialog showing the full address and saying it comes from the developer; there is no allow-list of hosts; (4) the Generate bar's Pipeline switch is hidden until an installed pipeline is turned on, so nothing changes for someone who never visits the Marketplace; (5) a video's pipeline choice carries no version: the active version runs, and settings an update or rollback no longer accepts are dropped from saved rows rather than refused at Generate; (6) the hardware check says "can't tell" for memory and for graphics cards other than NVIDIA, since Clips Kitty measures neither; (7) Clips Kitty's own modes show "This PC + your cloud AI" when Settings → AI uses an online provider or transcription service, instead of the manifest's "Runs on this PC"; (8) only the four checks Clips Kitty's index build runs are shown from an index, a failed one as failed, and checks from an index other than the bundled one are attributed to it; (9) the engine adds `problems_here` to listings and installed plugins (a Clips Kitty version outside the range, no Python for a plugin that runs with one) and the index now carries each manifest's `run`.
**Alternatives:** (1) a TypeScript copy of the search; (2) one "I understand" box, or none; (3) an allow-list of link hosts, which a developer's own domain would never be on; (4) always showing the switch; (5) pinning the version in the job, which would refuse jobs after every update; (6) guessing from the GPU name or total memory; (7) leaving the manifest's wording; (9) leaving Python to the first failed job.
**Why:** each keeps the screen from claiming more than Clips Kitty knows or enforces (BRIEF §4), and keeps existing users' screens unchanged until they install something.
**Undo:** (2) `confirmations` in `ui/src/renderer/src/lib/marketplace.ts`; (3) the `open-plugin-link` handler in `ui/src/main/index.ts`; (4) the `pipeline` toggle condition in `AddVideos.tsx`; (5) the cleanup effect in `AddVideos.tsx`; (7) `BuiltinCard` in `Marketplace.tsx`; (9) `problems_here` in `plugins/api.py` and `"run"` in `registry.SHOWN`.

## D19 · Model management choices

**Decided:** (1) Clips Kitty downloads a plugin's Hugging Face and `url` models itself, with the standard library, into one folder for every plugin (`<data_dir>/plugin-models/`), laid out like the Hugging Face cache (snapshots pointing at blobs) but with blobs named by SHA-256, so a file two plugins name, or the same bytes under two names, is stored once; (2) a link is a symbolic link where the system allows one, else a hard link, else a copy, and the Marketplace says when it had to copy; (3) a download happens only when the user presses Download, after seeing the files, the size and the licence; pickle-format files need a ticked box; gated models are explained and not attempted (no Hugging Face sign-in yet); (4) every file is checked against the size and, where Hugging Face or the manifest gives one, the SHA-256; https only, redirects only to https; (5) a run stops before the plugin starts when a listed model isn't on the PC, except an Ollama model when Ollama isn't answering; (6) Ollama's models and the app's own models are read where they are and never copied; Ollama is asked only when an installed plugin lists an Ollama model; (7) the paths go to the plugin in `job.json` without a permission, since they are the plugin's own declarations; Clips Kitty never loads a plugin's model; (8) Hugging Face's licence and gated flag come from `GET /api/models/<id>/revision/<commit>`, which the Hub answers but its published OpenAPI document doesn't list, so a failure there is ignored and the manifest's licence is shown.
**Alternatives:** (1) the `huggingface_hub` library, which would add a dependency and its own cache and environment variables to the engine; one folder per plugin, which downloads the same model again for each; (5) letting the plugin find out; (8) the model card's README.
**Why:** one copy per file, nothing fetched without the user's say, and every byte checked, with no new dependency and nothing changed for the engine's own models.
**Undo:** `plugins/models.py` is new and only `plugins/runner.py` (the check before a run), `plugins/api.py` (three routes) and the SDK's `build_job`/`Model` reach it; dropping the runner's check makes a run go ahead without its models.

## D20 · Colin's answers of 2026-10-07: licences, Awesome Clips Kitty, labels and install counts

**Decided (by Colin, 10:35 UTC):** Clips Kitty stays AGPL-3.0-or-later; the SDK becomes MIT; the catalog's data is CC0-1.0; third-party plugins choose their own licence, shown in the Marketplace. The registry becomes **Awesome Clips Kitty**, a curated directory of apps, pipelines, plugins, models, workflows, integrations and tools, with three relationships kept apart (built for, built with, related). Labels are ✓ Official, ✓ Compatible (automated technical checks, not a trust or security claim), ★ Featured and Community; no "Verified". Install counts are wanted, anonymous and without accounts; GitHub stars and Hugging Face downloads and likes are shown as their own numbers; comments live in GitHub Discussions. GitHub holds code; Hugging Face holds models and is never where plugins are listed. Developers pay nothing and Clips Kitty takes no share.
**Supersedes:** D5 (5) (the SDK kept the AGPL licence), the "Listed" tier name in D5 (2) and D17, and the research document's "no telemetry and no install counts" (`docs/platform-research.md` Q34-Q36, `docs/platform-architecture.md` §8 "Ratings, counts").
**How it was applied:** `sdk/python/LICENSE` (MIT) with an SPDX line in each SDK file and its own `pyproject.toml`; `ui/package.json` says AGPL-3.0-or-later, as NOTICE and winget already did; the README's licence table; `registry/` moved to `awesome-clips-kitty/` with a CC0 `LICENSE`. The SDK was written entirely in this platform work, so nobody else's contribution is relicensed.
**Undo:** the licence files and the `git mv`; the decisions themselves are Colin's.

## D21 · One small file per entry, in this repository for now

**Decided:** `awesome-clips-kitty/registry/<kind>s/<name>.yaml` for apps, models, workflows, integrations and tools, `registry/pipelines/<publisher>/<name>.yaml` for installable listings, `registry/sections.yaml` for each kind's sections and the niches wanted, `stats/` for the numbers and compatibility records; `index.json` and the generated half of `README.md` are both built by `scripts/build_registry_index.py`, and CI fails when either is stale. The folder stays in this repository for now: on the card about a repository of its own, Colin chose "After review" (16:55 UTC on 2026-10-07), so the public `awesome-clips-kitty` repository is created only after Colin has reviewed the catalog and says so.
**Alternatives:** one file per kind (`apps.yaml`, `pipelines.yaml`), which Colin's message offered "or another structure"; awesome-selfhosted-data's layout, which is also one file per entry.
**Why:** two pull requests rarely touch the same file, a refused entry names its own file, and the folder can move to its own repository with `git subtree split` without changes.
**Undo:** `plugins/catalog.py read_entries` and `CATALOG_FOLDER` in `plugins/registry.py`.

## D22 · Labels follow from facts the app can check

**Decided:** (1) ✓ Official follows from the repository's GitHub owner (`plugins/catalog.py OFFICIAL_OWNERS`, today `colingpt9`), and the build checks every listed commit is on one of that repository's own branches or tags, because GitHub serves a fork's commits under the parent's address too; the app recomputes Official, and takes Official, Compatible, Featured and the install counter only from the index bundled with it, so any other index's listings and entries are Community; (2) ✓ Compatible belongs to one version at one commit with a passed record in `stats/compatibility.json`; directory entries never carry it; (3) the project's own repository may list its examples under another publisher name (`clips-kitty-examples/scene-cut-highlights`), and that listing shows "The repository is the Clips Kitty project's own" instead of claiming the publisher owns it; (4) a listing installs with the tier `listed-official` ("✓ Official · made by the Clips Kitty project", no trust tick) or `listed` ("Community · not reviewed by a person", one tick); (5) an entry nobody has checked against the inclusion criteria goes under "Not yet checked".
**Alternatives:** trusting an index's badges; trusting the repository address alone (a fork's commit can be fetched through it); one "Listed" tier for both.
**Why:** each label says only what is true and checkable.
**Undo:** `_labels`, `check_index` and `listing_tier` in `plugins/registry.py`; `TIERS` in `plugins/permissions.py`.

## D23 · The install counter is a download count on GitHub, and is off until the catalog has a home

**Decided:** after the first install of a listing (not an update, a rollback or a version switch), the app requests one small file named after the listing at the index's `counter.install` address; the plan is a GitHub release with one such file per listing, whose public download count is the number. No server, no account, no ID, nothing about videos; the request goes in the background and never holds up an install. It is on by default with a switch in the Marketplace (and `plugins.count_installs: false` for a whole PC); Colin confirmed "On, can switch off" on the card at 16:56 UTC on 2026-10-07. The address is empty today, so nothing is sent; a test fails if it is set while the website's privacy policy and the Store answers still say "No telemetry".
**Alternatives:** a counter service of our own (a server and its costs); counting only people who switch it on (offered on Colin's card; not chosen); counting updates too (they aren't installs).
**Why:** Colin asked for install counts with minimal telemetry and no accounts; GitHub already sees the plugin's own download, and a release asset's count needs nothing new to run.
**Undo:** `plugins/counter.py` and the counting lines in `plugins/api.py install_plugin`; with `counter.install: null` it does nothing.

## D24 · Numbers are read weekly by a workflow, never by the app

**Decided:** `scripts/update_registry_metrics.py` reads GitHub (stars, last push, archived, Discussions on, and with a token the number of discussions) for every repository the index names, and Hugging Face (its 30-day download figure and likes) for every model; a figure that can't be read keeps its last value. `.github/workflows/catalog-numbers.yml` runs it on Mondays, rebuilds the index and README, and commits. An entry with no commits for 365 days, or archived, is marked ⚠.
**Alternatives:** the app asking GitHub and Hugging Face as you browse, which tells them what you looked at; daily runs, a commit a day.
**Why:** CONTRIBUTING promises browsing tells nobody anything.
**Undo:** delete the workflow; the numbers then stay as last committed.

## D25 · The compatibility check runs plugin code, so it isn't automatic yet

**Decided:** `scripts/check_compatibility.py` installs a listed version with the plugin manager, checks its requirements (no graphics card, a Python if it needs one, its models up to 2 GB), runs it on a generated 40-second video through the app's runner, and records each check. It is run by hand on a throwaway machine; there is no CI job for it. The official example passed in this session's sandbox at 1f7f3c3 on Clips Kitty 2.0.0.
**Alternatives:** a CI job on every listing pull request. A plugin under test runs with the job's rights and could rewrite the results file the same job uploads, so this needs one isolated job per plugin and a separate job that only accepts its own record; designed, not built.
**Why:** a "Compatible" label a plugin could award itself would be worse than none.
**Undo:** delete `stats/compatibility.json` records; the badge disappears on the next build.

## D26 · Plugins credit what they build on

**Decided:** an optional manifest field `based_on` (up to 10 of name, https URL, SPDX licence, and how: `runs`, `includes-code` or `port`), shown as "Built on" on the listing, the install screen and the installed plugin.
**Why:** Colin's rule that a plugin incorporating another project must respect its licence needs somewhere to say so, and adapters for open-source clipping apps are the long-term plan.
**Undo:** remove `based_on` from `OPTIONAL` in the SDK's manifest; the Marketplace only shows it when present.

## D27 · The first catalog entries come from a checked research pass

**Decided:** eight searches found 308 open-source projects with working links; each was checked against its own sources (the LICENSE file, the latest commit, the README, the Hugging Face API for models) without installing or running anything. The 160 that met the inclusion criteria, plus 20 a completeness reviewer found missing and verified, were written as entries and each entry was checked again against its sources by a second agent (62 corrections). Listed with `checked: 2026-10-07`: 178 entries. Each says in `warning` when a project sends videos, audio or transcripts to an online service by default, downloads from sites whose terms may not allow it, or has usage tracking on by default, and in `license_note` when its models or parts have other terms or a model is gated. Left out: source-available licences (n8n), non-commercial data (SponsorBlock's segments), no licence file, and projects weaker on one criterion; each is in `docs/platform/research-notes/ecosystem-projects.md` with the reason. A model entry links to its Hugging Face page. Game niches with no open-source project (Marvel Rivals among them) stay "Wanted" ideas; no game pipeline was built.
**Alternatives:** listing everything found (a collection, not a curation); putting the weaker ones under "Not yet checked" (they were checked, so that would be untrue); a separate non-free page as awesome-selfhosted has (worth it once there are enough such projects).
**Why:** Colin asked for research into existing projects and a curated directory; the Marketplace shows these entries, so each one must be accurate about licence and data.
**Undo:** delete the entry files dated 2026-10-07 under `awesome-clips-kitty/registry/{apps,models,tools,integrations,workflows}/` and rebuild.

## D28 · Pipelines run on the Python inside Clips Kitty

**Decided:** Colin pointed out that the app already ships Python (17:04 UTC on 2026-10-07): its engine is CPython frozen by PyInstaller into `api.exe`. In the installed app `{python}` is now `api.exe` itself (`plugins/runner.py` `python_for`), started with `CLIPSKITTY_SCRIPT_HOST=1`, which `plugin_env` gives every plugin process. `main.py` checks the marker before its heavy imports and hands the command line to `_clipskitty_script_host.py`, which runs the script like a small `python`: as `__main__`, with the script's folder and `PYTHONPATH` (the SDK and the plugin's own folders) ahead of the bundle, line-buffered UTF-8 output, `-c`, `-m` and interpreter options accepted, `SystemExit` codes kept, and a multiprocessing child (`--multiprocessing-fork`) loading the parent's script as `__mp_main__` first. Engine commands (`serve`, `render-worker`…) stay the engine's even with the marker, and Electron removes it from the engine's own environment. The spec bundles the whole standard library (minus GUI toolkits, tests and build tools), so ordinary imports work in the installed app and not only in a checkout. Clips Kitty's own packages can't be imported from the bundle (a plugin's own folder of the same name still wins), and a module the app lacks stops the run with "This pipeline needs X, which this version of Clips Kitty doesn't include. Ask its developer to update it." A source checkout keeps the old order (the `plugins.python` setting, this interpreter, PATH); the installed app never searches PATH, so it can't pick Windows' "python" shortcut to the Store. The creator-facing "Python 3 installed on this PC" line is gone. `plugin_env` no longer sets `PYTHONUTF8` and drops `PYTHONHOME`, `PYTHONSTARTUP`, `PYTHONINSPECT` and `PYTHONUTF8` from the developer's environment: the frozen Python ignores them all, so a developer's run now behaves like a creator's (open text files with `encoding=`).
**Not yet:** the other packages inside the app (numpy, OpenCV, onnxruntime…) can be imported but are not a promise to developers; they may change with an app update. A declared list of promised packages, and hash-pinned pure-Python wheels for anything else (`run.python_requirements`), come later. Whether `api.exe` can start itself inside the Store (MSIX) package, how long it takes to start and how much memory it uses are proven only by `scripts/build_installer.py`'s new script-mode smoke test on a Windows build, which also runs a two-process pool and checks the engine's heavy packages stay unloaded.
**Alternatives:** download a separate Python when a pipeline first needs one (the card offered it; moot once Colin said the app has one); ship python.org's embeddable package beside `api.exe` (11,249,023 bytes for 3.11.9, https://www.python.org/ftp/python/3.11.9/; kept as the fallback if the smoke test shows script mode can't work in a Store build); a second small executable sharing the bundle (fewer moving parts, but whether PyInstaller builds two executables into one folder cleanly is unproven here).
**Why:** Colin's app is creator-first; a creator who installs a pipeline from the Marketplace must not then need to install Python.
**Undo:** in `plugins/runner.py` `python_for`, drop the frozen branch; plugins fall back to a Python on PATH as under D11.
