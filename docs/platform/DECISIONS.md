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

**Decided:** (1) plugins run only as a child process with a job folder; nothing third-party is imported into the engine; (2) a plugin replaces only the detection step (`clip_direction` and `find_clips` in `process_video`), and Clips Kitty keeps titles, rendering, captions and the library (understand and rate: see D30); (3) a job names its plugin in one optional field, `pipeline: {id, version, settings}`, exclusive with Sports, Gaming scoring and Longform (understand and rate: see D30); (4) plugin progress is reported as the existing `analyze` stage with a `fraction`, so the UI's stage list is untouched (amended by D30: a run that only understands reports a new `understand` stage); (5) a plugin's own Python packages are wheels only, hash-pinned, in its own environment, and a separate line the user agrees to at install; (6) a blocked plugin is refused at run time and flagged, never deleted by Clips Kitty; (7) the registry client's default index is the copy bundled with the app until the owner publishes one, so no URL is invented (D29 adds Clips Kitty's online list: that same file on the project's main branch, which answers once merged); (8) one previous version of each plugin is kept for rollback.
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

**Decided:** a plugin's ranges go straight to rendering, cut to the job's clip limit; `min_score` is not applied, except to scores a rater gives (D30).
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

**Decided:** (1) the index build reads each manifest from GitHub's raw file address at the listed commit (`raw.githubusercontent.com/<owner>/<repo>/<commit>/...`), and tests read the same paths from a folder; (2) the index carries the latest listed version's manifest fields at its top level and each version's `requires` and `permissions`; (3) search is a word-by-word match over weighted fields with a small alias table in `plugins/registry.py` and a listing's own `aliases`, every word required; (4) when two indexes list the same id, the bundled one wins, then settings order (D29: Clips Kitty's online list adds versions to a bundled listing); (5) block lists from every cached index apply, also after an address is removed from settings; (6) a listing shows "Unofficial" whenever it names `games`; (7) `POST /marketplace/refresh` needs no session header, since it fetches only addresses the user put in settings (superseded by D29: it also fetches Clips Kitty's online list, and needs the header); (8) the CI Python job runs `scripts/build_registry_index.py --check`.
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

**Decided:** after the first install of a listing (not an update, a rollback or a version switch), the app requests one small file named after the listing at the index's `counter.install` address; the plan is a GitHub release with one such file per listing, whose public download count is the number. No server, no account, no ID, nothing about videos; the request goes in the background and never holds up an install. It is on by default with a switch in the Marketplace (and `plugins.count_installs: false` for a Windows account); Colin confirmed "On, can switch off" on the card at 16:56 UTC on 2026-10-07. The address is empty today, so nothing is sent; a test fails if it is set while the website's privacy policy and the Store answers still say "No telemetry".
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

## D29 · Clips Kitty's online list brings new pipelines between releases

**Decided:** Colin asked that creators get pipelines without GitHub or a command line (17:01 UTC on 2026-10-07). Besides the copy bundled with it, the app now reads Clips Kitty's online list: the catalog's `index.json` at its place on the project's main branch, `https://raw.githubusercontent.com/ColinGPT9/clips-studio/main/awesome-clips-kitty/index.json` (`plugins/registry.py` `ONLINE_URL`). That file changes when a change to the catalog is merged (CI's `build_registry_index.py --check` fails a change that leaves it out of date) and when the weekly numbers job commits fresh numbers (D24), so merging a listing is publishing it; the next release bundles the same file. It answers once this branch is merged (404 before). The Marketplace checks it when it opens, and the app when it starts with a pipeline installed (so a block reaches installed copies without the Marketplace being opened), if its copy is more than a day old and no check was tried in the last hour (an offline PC isn't asked on every open; after a list that wasn't there or couldn't be read, a day). One check runs at a time, and a failed one is reported in a few fixed sentences, its details in the engine's log; "Check for new pipelines" fetches it at once, and a switch next to "Count my installs" turns the automatic checks off (`<data_dir>/plugins/listing-checks.json`). Refreshing now needs the session header, so a web page can't make the app fetch anything. D22 stands: only the bundled list labels, counts or says Official. What the online list adds (a new pipeline, a new version of a listed one, a new directory entry or section) shows as Community, without install numbers or counting, until a release bundles it. It adds versions to a bundled listing and never replaces one: a version the bundled list has comes from the bundled list, with its commit, compatibility record and counting; and nothing is added from a listing whose code is in another repository or folder. A change to an existing listing's or entry's text, and a removal, arrive with the next release. It never brings or changes the project's own listings or entries, which come with the app (as Community they would read as someone else's). A copy older than the bundled list (by the date its numbers were read) is set aside; its blocks still apply, and block lists keep adding up across every list ever cached. The privacy policy says what the check sends: one file from GitHub, nothing about the person or what they browse.
**Not yet:** labels for what the online list adds (that would mean trusting it as D22 trusts the bundled list: a decision for Colin); the catalog's own repository (D21: after Colin's review), which would change the address.
**Alternatives:** a signed list on the project's website (the creator-installs plan; signing keys, an approval each Monday for the weekly numbers, and a digest that refused legitimate installs on PCs without Git, for no gain while the file lives in Colin's own repository); the website (`colingpt9.github.io`), which needs a deploy step after each merge; Hugging Face, which D20 keeps for models, never for listings.
**Why:** without it a listing reached creators only with the next app release, so a creator who heard of a new pipeline couldn't get it.
**Undo:** set `ONLINE_URL` to an address that never answers; the bundled list then stands alone as before. (The switch stops only the automatic checks: a copy already fetched stays in use, and the button still fetches.)

## D30 · Understand and Rate: plugins after the moments are found

**Decided:** a job may name, besides how its moments are found, up to three Marketplace plugins that say what happens in each moment (**understand**) and up to three that give each moment a new score (**rate**). The creator's switch is **Rate & understand**.
- **Two optional list fields**, `understand` and `rate`, on every model `_process_options` serves, each item shaped like `pipeline`. They are allowed with Sports, Gaming scoring and a pipeline (whose own id they may not name again), and refused with Longform. With neither field a job runs exactly as before, and `plugins/steps.py` isn't imported.
- **Roles come from inputs and outputs.** `moments` in inputs, `context` and `ratings` in outputs; `kind` stays `pipeline` and `capability` stays `highlight_detection`. Three validator rules tie the words together, and none adds a warning.
- **Understand runs before rate**, so a rater sees every note. A plugin chosen for both runs once, at its place among the raters.
- **Ratings chain:** each rater sees the score the one before it gave, and the last one counts. Notes stack, at most 8 for one moment, and every plugin's notes for a clip share 400 characters in its title request.
- **`min_score` applies to rated moments**, and must-haves are exempt and kept first. The clip limit is applied after rating.
- **A 3× shortlist only with a rater and a limit:** the finder is asked for three times the creator's limit (at most 200) so raters choose from more. Without a rater nothing about finding changes.
- **A failure skips the step**, never the job, and the video page names the plugin with one sentence in the creator's words. For the job that named the step, it holds a watched channel's automatic posting (and says why in ask mode) and `then: publish`. The command-line daily upload is not held.
- **Shipped with the Marketplace, so no version floor:** raters and understanders declare `>=2.0` like every pipeline. The plugin API and manifest stay at 1; the SDK becomes 1.1.0.
- **A 10-minute default limit for moment runs** (`MOMENT_TIMEOUT_MINUTES`), not the find run's 60; `run.timeout_minutes` still applies, and the Marketplace shows each plugin's limit.
- **Re-sends keep the first publish's clips:** a watched item remembers the clips its first publish chose (`watch_items.chosen_clips`), so a forced re-run that changes scores can't change what a re-send posts. This is the one change for jobs without steps.
- **One new progress stage, `understand`** (base 0.65, weight 0.05), which amends D7(4). Raters report the existing `ranking` stage. Both carry the plugin's name, which the label shows, because the bar never goes back.

**Alternatives:** a manifest `steps` field (it would say again what inputs and outputs already say); new kinds such as `rater` (the registry, the Marketplace's sections and `KIND_LABELS` would all need them); averaging the ratings (it hides which plugin decided, and the creator chose an order); raters that never change which clips are made (then a rating could only reorder, and the design lets a low score set a moment aside); rescuing fusion's `over_limit` spares for the raters (they aren't de-duplicated, so a rater could pick two copies of one moment); failing the job when a plugin fails (one plugin someone else wrote would cost the creator the whole video); a `requires.clips_kitty` floor (no released app has plugins at all); holding posting by the video's latest outcome alone (a job that named no step would be held by an earlier run's failure).
**Why:** a creator who knows a niche can add what Clips Kitty can't know, such as what a moment means in a game or which call matters, without replacing how the moments are found. The same plugin can serve every finder, and every existing job, plugin and API client stays as it was.
**Undo:** remove the `steps_chosen` hook in `process_video` and the `rate` and `understand` fields in `server/api.py`; `plugins/steps.py` can then be deleted. The SDK's additions are optional and can stay. The re-send rule is `chosen_clips` in `server/automation.py` `publish()`.

## D31 · The SDK starter kit: what a developer gets, and what waits for Colin

**Decided:** Colin asked for an SDK that is very easy for building game-specific pipelines and plugins, and that is marketed (17:20 UTC on 2026-10-07). The SDK becomes 1.2.0 (`sdk/python/CHANGELOG.md`). The plugin contract and manifest stay at 1.
- **Commands:**
  - `new` starts a plugin from five templates (blank, transcript, game-events, rater, understander);
  - `sample` and `frame` make a test video with FFmpeg's own generators and pull out a frame;
  - `run --sample` runs a plugin on that video;
  - `install` puts a plugin into the Clips Kitty running on this PC;
  - `listing` writes a plugin's catalog file locally;
  - `--version` and a `clipskitty-sdk` command.
- **New modules:** `clipskitty_sdk.testing` runs a plugin from its own tests; `media`, `text` and `local_model` hold helpers. Everything uses only the standard library, because plugins run on the app's own Python (D28).
- **Templates and the tutorial use a made-up game,** Quarkbloom Arena. The tutorial is `docs/developers/first-game-pipeline.md`, with `signals-cookbook.md` beside it. Colin's games were examples, not requests, and nothing is built for a real game.
- **Template code `new` copies keeps the SDK's MIT notice** (`templates/_shared/TEMPLATE-LICENSE.txt`). The developer's own code is under the licence they choose. A plugin's licence defaults to MIT.
- **`install` talks only to this PC.** It sends the session header only to 127.0.0.1, localhost or ::1, never through a proxy, and follows a redirect only to the same address. It never prints the secret. It goes through the app's own plan and install, so a creator sees the same plan and risks.
- **`listing` never pushes and never opens a pull request.** It refuses uncommitted or unpushed code, and code in a repository that doesn't match the manifest or its publisher.
- **Promotion is a developers page, `site/developers.html`, plus launch drafts.** The drafts are in `docs/platform/developer-launch-drafts.md`, marked DRAFT. Nothing is posted, uploaded or created, and PyPI is untouched.
- **A Windows CI job ("SDK (Windows)")** builds the wheel and runs the SDK's tests on Windows.
- **Edit and export are planned words.** `outputs: [edits]` and `kind: publisher` are refused as "planned", not "unknown".

**Waits for Colin** (the SDK plan's approval list, and the drafts' table):
- publishing `clipskitty-sdk` to PyPI;
- merging, which deploys the developers page;
- the template repository and the licence for template output (MIT-0 or CC0-1.0 instead of the MIT notice);
- the Discussions categories;
- each post;
- the "Built for Clips Kitty" badge;
- the CONTRIBUTING licence sentence;
- the release order;
- the Python version releases are built with. Comments say 3.11, and `build_installer.py` now refuses another version.

**Alternatives:** a separate SDK repository (developers would lose the engine tests the SDK is checked against, and Colin would have two repositories to look after); a cookiecutter or copier template (one more tool to install before a first plugin); templates for real games (Colin said not to).
**Why:** a developer should get from nothing to a plugin running on a test video, and then inside Clips Kitty, without reading the engine, installing more than the SDK and FFmpeg, or touching GitHub until they want a listing.
**Undo:** each part is its own commit (8a4836b to 82cd906, then the fixes up to 99788d4). Reverting the site and drafts commits removes the promotion, and the SDK's new modules can be removed without touching the engine.

## D32 · Edit: plugins suggest, the creator decides

**Decided:** a job may name up to three Marketplace plugins that suggest edits for the clips that will be made (**edit**). The creator's switch is **Suggest edits**. A suggestion waits for the creator in the timeline editor: "Clips Kitty doesn’t put a suggestion into a clip until you use it in the editor and apply your edits (Apply edits, or Apply edits & upload)."
- **One optional list field, `edit`,** on every model `_process_options` serves, each item shaped like `pipeline`. It is allowed with Sports, Gaming scoring and a pipeline, whose own id it may name (a find run is never asked to edit), and refused with Longform. Without it a job runs exactly as before.
- **The role comes from inputs and outputs:** `moments` in inputs and `edits` in outputs. `kind` stays `pipeline`, and no permission is added. Editors stay under Pipelines with a **Suggests edits** pill.
- **An edit run is a run of its own,** after rating and the clip limit and before titles, on the clips that will be made. The plugins run in the creator's order, and each is handed what the ones before it suggested (`suggested`; its text only with `transcript.read`). A plugin chosen to rate and to edit runs twice.
- **Suggest only.** Each suggestion, fitted by `host.read_edits`, is kept in the clip's `scores.plugin_edits` with state `new`. The edit step changes no clip's first render (`_clip_opts(meta)` is unchanged) and never writes a clip's `render_opts`; what a run writes to a clip it makes again is in D33. The creator uses, hides or takes back each suggestion in the timeline editor, and a render the creator asks for records what each Use really put in the clip (`applied`, `plugins/edit_marks.py`).
- **The allow-list is the editor's own fields:** cuts, mutes, volume, fades, speed, `title_overlay` (the hook title), `crop` (`track`, `center` or `letterbox`, and only for standard vertical clips) and a reason. Times are seconds of the video, as moments, transcripts and `text.said` use, and are converted at Use. Fades, speed and the hook title's seconds are fitted to the editor's choices with a `changed:` line. Unknown fields and crops are ignored with an `ignored:` line; a known field outside the render's limits refuses the answer, as in D30. Cuts that would leave less than the shortest clip (at least 1 s) are ignored.
- **Decisions survive re-runs:** a suggestion's id comes from the plugin and what it suggests, so Hide and Use carry over. Take it back takes out only the suggestion's own parts, and "used" is kept only while some of it is still in the clip's saved edit.
- **A failure holds nothing.** An edit plugin that can't run is skipped and named on the video page, and a watched channel's automatic posting and `then: publish` go ahead: it changed nothing that posts.
- **Progress stage `edit`** (base 0.70, weight 0.08, sharing reactions' span), "Suggesting edits with {name}". The 10-minute default limit of D30's moment runs applies.
- **SDK 1.3.0:** `job.suggest_edit(m)`, `job.limits.crops`, `Moment.suggested`, `host.read_edits`, `run --steps edit` and `--layout`, and the `editor` template. The plugin contract, manifest and `plugin_api` stay at 1, and editors declare `>=2.0`. An app without the edit step refuses `outputs: [edits]` as planned, so the docs say to name the release a plugin needs in its description (`docs/developers/versioning.md`).
- **No new locale values,** as with D30.
- **The public wording is a judgment call.** The design left the site, the READMEs and the launch drafts to Colin. They now say that edit plugins suggest edits that wait for the creator in the editor, and that export is coming later, in the same commit as the developer docs; otherwise the SDK picture would say "coming later" above a sentence saying edit plugins work. The site deploys only when Colin merges, and he reviews the PR first. The launch drafts stay marked DRAFT, and nothing is posted.

**Waits for Colin:**
- **R3, answered:** Colin chose "Fix it next" (D33). A run now makes a clip the creator edited with its saved edits, for every job, and `remade` stays for a file that still comes out without them.
- Whether plugin edits may ever go into clips without the creator applying them, a plugin's hook title on a channel that posts automatically included. R3 is fixed (D33). If plugin edits ever go into a first render, they must be marked as the plugin's, or a re-run would keep them as the creator's.
- The release order (D31): if a release that runs plugins comes out before one with the edit step, editors and the templates' `">=2.0"` need the first release with it as their floor.

**Alternatives:** applying suggestions at the first render (an edit nobody looked at could go out on a channel that posts automatically); `keep` spans in clip seconds (a second clock beside moments, transcripts and `text.said`, and lost when the creator trims); five crops, with `bias_left` and `bias_right` (only `track`, `center` and `letterbox` are the editor's Layout buttons, and Sports and Podcast give crops other meanings); refusing unknown crops (a later plugin's crop would make this app throw away the whole answer, so it is ignored with a line); refusing values the editor doesn't offer (they are fitted to the nearest choice, logged, so the editor's controls show what the clip gets); a snapshot Take it back (restoring a saved copy would also take out the creator's own edits made since); a bulk Use (it needs a review screen first, so the creator sees each suggestion before anything renders).
**Why:** a developer who knows a game knows where the wait is and which words to mute, but what goes into a clip is the creator's call. Suggestions in the editor let the creator see each one before anything renders, keep automatic posting exactly as it was, and make a failed plugin cost nothing.
**Undo:** remove `edit` from `FIELDS` (`plugins/steps.py`), from the API models (`server/api.py`, with `RenderIn.suggestions`, `ClipPatch.suggestion` and `RenderFirst.suggestions` in `server/youtube_api.py`) and the `suggest_edits` call in `process_video`; `plugin_edits` then stays inert in clips' scores. The SDK's additions are optional and can stay. The work is 5dcd809, 7f7e5ad, 13a6da7, 35f2b97 and eec61e9, then the docs commit after them.

## D33 · A re-run makes a clip the creator edited with their edits

**Decided:** Colin's answer to R3 (D32), on the card in the project thread on 2026-10-08: "Fix it next", "After edit, a separate change makes re-runs render every clip with its saved edits, for all jobs." When a run makes a window that already has a clip the creator made again by hand, it renders that clip with the clip's saved options, as **Make it again with my edits** does, for every job, with or without plugins. A Sports job keeps its own look.
- **Which clips:** a clip whose saved options hold something only a person sets on one clip: `edit` (the timeline editor's Apply always sends it, with any split drawn or changed there), `crop`, `caption_lines`, `speaker_edits`, `adjust`, `captions` or `normalize_audio`; or a split no run writes, turned off for that clip (`"gaming": null`) or turned on for it alone (`by` `clip`). A split saved as `by` `user` doesn't count on its own: it is also how a split set up before processing is saved on every clip that run makes. Every other clip, and every new window, renders with exactly the job's options, as before (`_clip_opts(meta)`).
- **What wins:** the clip's saved options over the job's, except what the job decides for every clip: Longform's `profile`, Podcast, Vertical Live, the sport, and who is heard talking (`speaker_turns`, heard again at each render). Outside Sports, caption style, colour, branding and the Highlights card words come from the clip, so the file matches what the editor shows.
- **Sports:** only the per-clip keys above reach a match's file: the cut, layout, caption text, speaker fixes, colour adjustments, captions on or off and loudness. The sport, its options, Vertical Live for a match filmed vertically, a saved split, and the caption style, colour preset, branding and card words are the job's: on most clips those are the first run's, nothing records which ones a person changed, and a match's look must not change other than by the creator's own choices. A look change made to a match clip stays saved on it, and **Re-render** in the clip panel makes the file with it; a re-run sets no mark for it. A story reel joins the clips as made, so an edited clip is in it with its edits, and the reel trims each clip's end card by the length the clip was made (`made_seconds`).
- **The split:** a split a person set for the clip (drawn, turned off, or turned on for it alone) is kept whatever the job; a drawn one (`by` `user`) can't be told from one set up for an earlier run, so that is kept too. In a Gaming / Reaction job an edited clip's found or remembered split is kept with its layout, and takes the webcam a person chose for this job (set up for this video or remembered for the creator) when there is one; the run's own webcam search serves the clips nobody edited, new ones, and an edited clip with no saved split. Any other job drops a found or remembered split, as before. A Sports job never takes a saved split.
- **Render PCs** get the same options. A clip with music added in the editor renders on this computer, because the music is a file here. The main PC checks a returned clip against what its edit keeps, not its window.
- **A split turned off for one clip** (`"gaming": null`) now stays off when the job has Gaming / Reaction on, in a re-run, a re-render and a preview alike.
- **`remade` stays,** for a file that really lacks the saved edit or layout: an edit saved while a run was making that clip (two Clips Kitty processes on one library). A run with the saved edit clears it.
- **What a run writes to a clip it makes again:** the file, its scores, who is heard talking, and the split it rendered with. It never replaces a caption style or card words saved on a clip the creator edited, and adds this run's card only where the clip had none. For a clip nobody edited it updates the Highlights card words and the split to this run's, as before. Titles stay, and a clip the creator trimmed is another window (R4).
- **Longform's clip mode** does the same for its 16:9 clips, matched by their `profile`.

**Alternatives:** the saved options for every clip with a row (a re-run could no longer restyle the clips nobody edited); a `by` `user` split marking a clip as edited (a split set up before processing is saved so on every clip, so clips nobody edited would stop following the job); the clip's own look in a Sports job too (a match clip with only a cut would get the first run's look and card words instead of this run's); only the per-clip keys in every job (a creator's own style, branding or card words would be missing from the file outside Sports too); the job's whole split over an edited clip's found one (it would rewrite a layout the creator changed in the editor, which keeps `by` `video`); a mark written by every hand render (a new field on every clip, and clips edited before it wouldn't have one); rendering music clips on the render PC (it would leave the music out).
**Why:** the editor shows a clip's saved options and treats them as its file, and automatic posting posts the file. A run that makes the file without them posts something the creator never approved, with their edits still on screen.
**Undo:** revert 730c698 and the docs commit after it. Each run then renders every clip with `_clip_opts(meta)` again, and `remade` marks the edited clips' files, as before.
