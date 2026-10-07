# Overnight report: Clips Kitty open platform

Branch `claude/open-platform-w4eh9g`, from `main` at 1d13723. Brief: [BRIEF.md](BRIEF.md). Every decision in full: [DECISIONS.md](DECISIONS.md). Phase by phase, with test counts: [PROGRESS.md](PROGRESS.md).

## Where things stand

Every phase in the brief is done. Clips Kitty can now install community pipelines from a folder, a GitHub commit or a registry listing, run them as one step of its normal job, show and manage them in a Marketplace screen, and download the models they name into one folder every plugin shares. Nothing existing was replaced: the same API, engine, job queue and pipeline, with one new optional job field (`pipeline`) and new routes, all labelled experimental. The code for Soccer, Basketball, Gaming and the House of Highlights style was not changed; the one change in the shared pipeline (`core/pipeline.py`) is a branch taken only when a video has a pipeline chosen.

Two things are not true yet and matter most:

- **Nobody has looked at the new screens.** The Marketplace and the Generate bar's Pipeline switch type-check and build, and their logic is tested, but they have never been opened in the desktop app or on Windows.
- **The Marketplace is empty until the owner publishes listings.** The registry lives in `registry/` in this repository and its index ships inside the app; there is no public index address, so a new listing reaches users with the next app release. No game pipelines were built, as asked: game names appear only as examples and search tests.

| Phase | State | Commits |
|---|---|---|
| 0 · Setup, brief saved, baseline | done | c3bd2c1 |
| 0A · Repository map (`docs/platform/repo-map.md`) | done | 5fc30c7 |
| 0B · Research notes (22 platforms, `docs/platform/research-notes/`) | done | 0d257ce |
| 0C · `docs/platform-research.md` | done | c349fd3 |
| 1 · `docs/platform-architecture.md` | done | 3160959 |
| §9 · Independent design review, all 26 findings applied | done | cc00115, 02611f3 |
| 2 · Public API boundary: every route labelled stable, experimental or internal; stable ones pinned by tests | done | dc64ebd, 96929d6, fa6b570 |
| 3 · Plugin contract, Python SDK, runner, `pipeline` job option | done | 4cab905, bac61a4 |
| 4 · Manifest (`clipskitty.yaml`), validator, JSON Schema, built-in modes described | done | 2e5d109, a04fa67 |
| 5 · Example external pipeline (`examples/pipelines/scene-cut-highlights/`) | done | cdbcbfb, 283465a |
| 6 · Plugin manager: install, update, roll back, turn off, pin, remove, keys | done | 10d5193, f230d31 |
| 7 · GitHub registry: listings, index build, block list, offline search | done | 09e79c9, 66802d9, 2b881ee, 9bbc9b4 |
| 8 · Marketplace screen and the Pipeline switch | done; not looked at on a screen | 2fc500c, 9d481f1 |
| 9 · Model management: references, shared folder, downloads, routes | done; one small real download | ccb40bf, 4d17956 |
| §11 · Developer documentation (`docs/developers/`: the 17 pages §11 lists, plus the generated API reference) | done; unbuilt parts labelled planned | with each phase, and 4d17956 |
| §13 · This report and the draft pull request | done | the commit that adds this file |

### The brief's success tests, in code

1. **The niche developer: passes**, with two limits. A developer can write a pipeline with the SDK, name a Hugging Face model pinned to a commit, test it with `python -m clipskitty_sdk run`, publish it on GitHub, list it with a pull request to `registry/`, and users find it in the Marketplace, install it and pick it on a video, with no fork. Limits: a listing reaches users with the next app release (no public index yet), and a pipeline that needs Python packages of its own only works if the user's Python already has them (per-plugin environments are designed, not built).
2. **Open Shorts: passes in part.** It can be wrapped as a pipeline that runs locally or calls a hosted copy, with what it sends declared. Running it locally needs its Python packages, so the same limit applies.
3. **A game-specific model: passes in part.** The model reference, the shared download and the hand-over to the plugin work; loading the model needs a runtime package such as ONNX Runtime, so the same limit applies. GPU use is the plugin's own code and was not run.
4. **The gaming market: the infrastructure passes.** Search puts the specialised listing first for every query in the brief, over a test catalogue; aliases cover WoW, LoL, CS2 and football/soccer. There are no real listings yet.

## What was verified

All on Linux (Ubuntu 24.04, Python 3.13, Node 22, no GPU, no Ollama), in this sandbox.

- **Baseline before any change:** `pytest` → 1633 passed, 14 failed, 72 skipped; with `httpx` added so the API tests run (D6): **1879 passed, 14 failed, 23 skipped**. The 14 failures are this sandbox's (a missing end-card font, OpenCV without Haar cascade files, one clip-intent test that fails on `main` here too); they are listed in PROGRESS.md.
- **After each phase**, the full suite with no new failures: Phase 2 1899 passed; 3 1987; 4 2080; 6 2144; 7 2181; Phase 8 no new failures; Phase 9 below. Phase 5 added only its own tests.
- **Final run (Phase 9):** 2243 passed, 12 failed, 26 skipped. All 12 failures are on the baseline list (the two Haar cascade tests now pass in this sandbox). To check the comparison, the base commit was run again in the same sandbox: 1881 passed, the same 12 failed, 24 skipped. The 2 extra skips are this branch's tests that need `psutil` and `jsonschema`, which this sandbox lacks.
- `ruff check .` clean before every commit. `npm run typecheck` and `npm run build` in `ui/` pass.
- New tests, all real: plugin processes started by the runner, Git installs from local repositories (including hooks and filters that would leave a mark if anything were checked out), a fake GitHub archive, a fake Hugging Face and Ollama, and the Marketplace's TypeScript run under Node against what the engine produces.
- **An independent review of the Marketplace screen** (four reviewers, each finding checked by a second agent): 30 findings, 21 confirmed and fixed, 9 refuted.
- **One live check against Hugging Face:** the licence, file sizes and SHA-256 of `openai/whisper-tiny` at a pinned commit, and its 2 KB `config.json` downloaded and checked. No large file was downloaded.

## What was not verified (needs a real machine)

- **The desktop app.** The Marketplace page, the Pipeline switch, the install dialog, the link dialog and the folder picker have never been opened in Electron. Nothing was looked at on a screen.
- **Windows.** Nothing ran on Windows: process start and stop for plugins, the symbolic link fallback for shared models (Windows without Developer Mode should fall back to a hard link or a copy), keys stored with Windows' data protection, file locks on remove, and finding a Python (the Microsoft Store's `python` placeholder may be found and fail).
- **The installed (frozen) app.** It was not built; the bundled registry index and the plugin folders inside it were not checked.
- **GPU inference and real model downloads.** No plugin used a GPU; no model larger than 2 KB was downloaded. A large download has no progress bar and holds its request open until it finishes.
- **Installing from GitHub over the network.** Tests use local Git repositories and a stand-in for GitHub's archive.
- **macOS and Linux desktop builds.**

## Decisions made for the owner, most consequential first

Each is in DECISIONS.md with its alternatives; these are the ones that change what users or developers get.

1. **A plugin runs as its own process with the user's rights; there is no sandbox** (D7, D9). Clips Kitty decides what it hands over (video, transcript, tools, models) and keeps credentials out of the plugin's environment, but the plugin's own code can read the user's files and use the network. Every screen and page says so, and only what Clips Kitty controls is called enforced. *Alternative:* load plugins into the engine (more access, and impossible in the frozen app) or build a sandbox (not possible tonight). *Undo:* none needed; a sandbox would be additive.
2. **Python plugins use a Python already on the PC** (D11). The installed app ships none, so a Python plugin needs the user to have Python, or `plugins.python` set; the Marketplace now says "It needs Python, and none was found on this PC" before installing. *Alternative:* bundle a Python with the app. *Undo:* additive.
3. **The existing API stays without a password; only the plugin routes that fetch or change plugins need the app's session secret** (D5, D15). *Alternative:* a token on every route, which would break the OBS plugin, the MCP server and existing scripts. *Undo:* `plugins/session.py` and the route dependencies.
4. **No "Verified" tier** (D5). Tiers are Official (built-in), Listed (automatic checks only, labelled "not reviewed by a person"), Installed from a link, and Blocked. *Alternative:* a Verified badge, which the brief forbids without a real process. *Undo:* add a tier once a process exists.
5. **The registry lives in `registry/` in this repository, and the app's only index is the copy it ships with** (D5, D7, D17). *Alternative:* a separate repository and a public index address, which the brief ruled out tonight. *Undo:* set `plugins.registry_urls` (or a default) to the address once it exists; the app already fetches and caches remote indexes.
6. **The SDK keeps the repository's AGPL-3.0 licence** (D5); the example pipeline is MIT (D13). A plugin doesn't have to use the SDK (the contract is files and printed lines). *Undo:* a licence file.
7. **A pipeline replaces only the "find the moments" step, and can't be combined with Sports, Gaming scoring or Longform** (D7). Layouts (Vertical Live, Gaming split, Podcast) still work with it. *Undo:* the check in `server/api.py` `_process_options`.
8. **Install needs a ticked sentence for each risk; links open only after a dialog showing the address; the Pipeline switch stays hidden until a pipeline is installed** (D18).
9. **Models download into one shared folder with the standard library, only when the user presses Download, each file checked; no `huggingface_hub` dependency** (D12, D19). A run stops before the plugin starts when its model is missing.
10. **"Stable" API routes are the ones `docs/API.md` already promised** (60, D8); everything new is experimental.

## Questions only the owner can answer

1. **Where should the public registry live, and at what address?** Until then listings reach users only with app releases. Who reviews listing pull requests and the block list?
2. **Should the installed app include a Python for plugins**, or should per-plugin environments (designed in the architecture) come first? This decides whether most real plugins work for Store users.
3. **What licence should the SDK have?** AGPL-3.0 today; a permissive licence would let closed-source plugins import it.
4. **What would "Verified" mean, if anything?** A human review, a signed publisher, a test run? Nothing is promised today.
5. **Are ratings or install counts wanted?** Today the Marketplace sends nothing about what anyone browses or installs.

## Recommended next steps, in order

1. Open the branch on a Windows PC: look at the Marketplace, install `examples/pipelines/scene-cut-highlights` from its folder (with Python installed), and process one video with it.
2. Answer question 2, then build Python for plugins (bundled or per-plugin environments); most real plugins need it.
3. Answer question 1 and publish an index address, so listings don't wait for app releases.
4. Add download progress and cancel for large models, and Hugging Face sign-in for gated ones.
5. Merge the pull request once the above has been looked at.
