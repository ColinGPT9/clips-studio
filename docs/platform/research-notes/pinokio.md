# Pinokio (pinokiocomputer) — Track A, Tier 2

Date read: 2026-10-06. Versions at time of reading: Electron shell `pinokio` 8.2.0 (`package.json`, clone HEAD 0765ab1, 2026-09-02); engine `pinokiod` 8.1.1 (clone HEAD add4a67, 2026-09-02); script schema version `"8.0.0"` (manual). License MIT.

**Brief's question:** how does community distribution of local AI applications work, and what does "verified" mean there? (GitHub-based app distribution, the script format, discovery: Discover page, verified vs community, who verifies and how; developer publishing; trust; and the conflict between "run isolated by default" and "execute anything on your computer".)

**Verdict (two lines):** A Pinokio app is any public git repository holding `pinokio.json` (display metadata), `pinokio.js` (menu/launcher) and JSON-RPC-style scripts (`install.js`, `start.js`, ...) whose steps are mostly `shell.run` commands; Pinokio git-clones it into `~/pinokio/api/<repo>` and runs the steps in a real pty shell with the user's rights, after one dialog saying "This will execute the 3rd party script downloaded from <url>". "Verified" means a repository that the single admin (cocktailpeanut) accepted after contact on X, that was transferred into the `pinokiofactory` GitHub org (101 repos on 2026-10-06) and reviewed by hand against path/venv conventions; "isolation" is that convention (`~/pinokio` paths, `venv` per app, shared caches), not a sandbox — the README says both things and they are consistent once read that way.

---

## 1. Unit of extension

A **launcher / app script repository** ("project"): a git repository containing scripts that Pinokio executes. Manual (desktop.pinokio.co/docs README.md, 2026-10-06): "A Pinokio launcher project usually has 4 core parts: Config `pinokio.json` ... Environment `ENVIRONMENT` ... Script ... Launcher `pinokio.js`". Example layout: `~/pinokio/api/my_project/{pinokio.json, ENVIRONMENT, pinokio.js, start.js, install.js, update.js}`.

Secondary units: **plugins** (a `pinokio.js` under `plugins/...`, listed in `/plugins`; directory tab `path=plugin`) and **skills** for the built-in "Agent Interpreter" (manual §5). Not studied further.

## 2. Manifest or metadata format

Real field names from the manual (2026-10-06):

**`pinokio.json`** (display metadata): `title`, `description`, `icon`, `posts` (array of x.com URLs rendered as a newsfeed), `links` (array of `{title, value}` or nested `{title, links:[...]}`). No version, no compatibility, no permission, no dependency fields. The older manual used `pinokio_meta.json` with `posts` for the same purpose.

**`pinokio.js`** (launcher): `version` (schema version, latest `"8.0.0"`), `pre` (array of `{text, icon, href, fs}` prerequisite download links shown before install), `on` (event name → `{href, ui:{mode,title,open,closeOnSuccess,refreshOnClose}}`), `plugins` (bundled plugin paths), `menu` (array or `async function(kernel, info)` returning items with `text, icon, image, href, params, shell, popout, inject, menu, default`). `menu` being a function means the launcher is executable JavaScript evaluated by the engine.

**Scripts** (`install.js`, `start.js`, any name; JSON or `module.exports = {...}` JS): `version`, `run` (ordered array of steps), `daemon` (keep running), `env` (prerequisite environment variables that trigger a form). Each step is "a modified version of JSON-RPC": `id`, `when` (template condition), `method`, `params`, `next`. `shell.run` params include `message`, `path` (working dir relative to the app), `venv` (string or `{path, python}`), `env`. Steps can also be inline JavaScript functions ("Custom Instruction") or a JavaScript class file ("Custom Instruction Module"), with "access to all node.js APIs".

Compatibility is not declared; scripts branch at run time on templated variables such as `{{gpu === 'nvidia'}}`, `{{gpu_driver}}`, platform.

## 3. Distribution and install

- **Any public git URL.** Old manual: "you can publish to any git hosting service and share the URL, and anyone will be able to install and run your script"; "Download from URL" button on Discover takes a git URL and optional branch. New manual §11.3: "1-Click Publish to GitHub" through "login with localhost" (GitHub OAuth from the app).
- **Install = git clone.** `pinokiod/kernel/api/script/index.js` lines 239–295 (`script.download`): resolves the repo URI, sanitises it into a folder name (`[^a-zA-Z0-9_-]` → `_`), then `git clone ${repo_uri} ${folderName}` into `kernel.api.userdir` (`~/pinokio/api`); optional `branch` → `git switch`, optional `hash` → `git switch --detach ${hash}`, optional `pull`. No signature, allowlist or scan is performed in that path.
- **Consent dialog.** `pinokiod/server/views/env_editor.ejs` line 488: "This will execute the 3rd party script downloaded from <gitRemote>", followed by a "Protection (Beta)" toggle: "Blocks packages newer than 72 hours for new runs in this app. Can reduce risk, but some installs may fail." README shows the same screen ("an alert letting you know the downloaded 3rd party script is about to be run").
- **What runs at install:** whatever `install.js` says — typically `git clone` of the upstream project, `uv pip install -r requirements.txt` inside a `venv`, model downloads (`hf.download` wraps the `hf download` CLI, `kernel/api/hf/index.js` lines 312–340), and arbitrary shell. README: "Scripts can run anything: Just like terminal apps can run shell scripts, Pinokio scripts can run any command, download files, and execute them."

## 4. Dependencies and isolation

- **Execution model (source, 2026-10-06):** `kernel/shell.js` line 862 `pty.spawn(this.shell, this.args, config)` — a real interactive shell per script via node-pty, with `cwd` set to the app path and an environment where `~/pinokio/bin` is appended to `PATH` (line 256). Conda and venv activation lines are prepended to the command (lines 1013–1420; `uv venv` when a Python version is requested, else `python -m venv`). There is no sandbox, seccomp, container or permission prompt in this path.
- **Built-in package managers** are installed under `~/pinokio/bin` (`kernel/bin/`: `conda.js`, `uv.js`, `py.js`, `node.js`, `bun.js`, `brew.js`, `git.js`, `ffmpeg.js`, `cuda.js`, `torch.js`, `huggingface.js`, `cloudflared.js`, `caddy.js`, ...).
- **One venv per app** by convention (`venv` param, "inside each app's folder"), **shared caches** across apps: `kernel/environment.js` sets `PIP_CACHE_DIR`, `UV_CACHE_DIR`, `XDG_CACHE_HOME`, `HOMEBREW_CACHE`, `TMPDIR` to `PINOKIO_HOME/cache/...` (lines 255–285) and `HF_HOME`, `TORCH_HOME` per app with a comment: "You can save disk space by deleting this line, which will store all huggingface files under PINOKIO_HOME/cache/HF_HOME without redundancy" (lines 476–490).
- **Resolving the "isolated" vs "run anything" conflict.** Both statements are in the same README (clone HEAD 2026-09-02). "Scripts are isolated by design: By default all Pinokio scripts are stored run under an isolated location (at `~/pinokio/api`). Additionally, all binaries installed through the built-in package managers in Pinokio are installed within `~/pinokio/bin` ... The risk factor is when a script intentionally tries to deviatte away from this. The script verification process checks to make sure this doesn't happen." So "isolated by default" means *default working directory and install locations are under `~/pinokio`* and the syntax (`path`, `venv`) makes deviations visible to a human reviewer; it is not runtime isolation. The source confirms: `shell.run` is a full pty shell; `path` is only resolved relative to the app folder; nothing prevents absolute paths or `cd ..`. Conclusion: Pinokio is a terminal with a UI; isolation is a review convention that applies only to the scripts the admin reviews.
- **Supply-chain mitigation:** "Package Install Protection" via Bluefairy, default `72h` freshness delay for `npm install/add/ci/update`, `uv pip install/sync`, `bun install/add/update`; "Not covered: `npx`, `uvx`, raw `pip` / `python -m pip`, `conda`, `brew`, `cargo`"; built-in installer flows default to `bluefairy: "off"` (manual §2).

## 5. Versioning and updates

- Git only. Install can pin `branch` or `hash` (`script.download`); whether the Discover page passes a hash: not confirmed. Apps usually ship `update.js` (git pull). Manual §14 "1-Click Version Control": "Switch to any past version", one-click commit/publish; every project folder is a git repo.
- Script schema `version` field ("8.0.0"); older schemas are interpreted by the engine. No deprecation process documented. "Frozen" in the Script Policy means the repository is controlled by the org, not that a version is immutable: the admin "may ... Modify the script to resolve the issue".

## 6. Registry design

- **Discover page = pinokio.co**, loaded inside the app as an iframe (`pinokio/config.js`: `discover_dark: "https://pinokio.co?embed=1&theme=dark"`). It is a hosted web app with a login (`/login`), `/apps` listing (9 pages for `path=api` on 2026-10-06), tabs `api` / `plugin` / `All`, filters by platform and GPU, sorts "Recommended / Latest / Check-ins", per-app pages with follower counts, "check-ins" (users posting their hardware: platform, arch, GPU, RAM, VRAM), commit history and a "Featured" badge. Listed hosts on page 1 include `cocktailpeanut` (12), `6Morpheus6`, `saintbrodie`, `gantasmo`, `Blizaine`, `PierrunoYT`, `bilawalsidhu`, `ThomasEricB`, `TheAwaken1` and only one `pinokiofactory` repo (`ai-toolkit`) — so the directory is not limited to the verified org.
- **Older design (program.pinokio.computer manual):** the "Latest" section was built automatically from `https://api.github.com/search/repositories?q=topic:pinokio&sort=updated&direction=desc` — tag your repo with topic `pinokio` and it appears. Zero-cost, zero-review discovery. The new site's "Latest" tab shows the same kind of community entries; whether it still uses the GitHub topic search: not confirmed.
- **Verified track (README "Script Policy"):** 1. "Publisher Verification: You must be personally verified to submit scripts for consideration. Contact the Pinokio admin (https://x.com/cocktailpeanut)"; 2. invitation to the "Pinokio Factory GitHub organization"; 3. "Repository Transfer and Freeze" — transfer the repo to the org; 4. "Feature Application" by contacting the admin; 5. "Review: The script will be thoroughly reviewed and tested by the Pinokio admin"; 6. after featuring the admin may "Delist the script" or "Modify the script". https://github.com/orgs/pinokiofactory/repositories showed "101 repositories" (2026-10-06; recent: ai-toolkit, MMAudio, wan, comfy, stable-diffusion-webui-forge).
- **Cost:** the verified track costs one person's time; the hosted directory's running cost is not published (not confirmed). The old topic-search list cost nothing.

## 7. Trust and permissions

- **No declared permissions, nothing enforced.** The only artefact is the consent dialog naming the git remote, plus the optional 72h freshness delay.
- **What "verified" means in practice:** a human (one admin) read the script, checked "Path check" (all commands run inside the app path), "Venv check" (dependencies installed via `venv`), "3rd Party Package check" (built-in managers stay under `~/pinokio`), "the reputation of the repository and the developer", tried the app, and checked the install follows the upstream README; then the repo was moved under an org the admin controls. "No scripts are approved until rigorously tested." The site shows "Featured", not "verified"; the word "verified" on pinokio.co appears only for "verified repository managers" who connected GitHub access to post announcements.
- Verification does not cover what the installed upstream project or its model files do (see issue #880 below).

## 8. Models

- Scripts download models themselves (`hf.download` → `hf download ...`; `shell.run` with `wget`/`git clone` of HF repos; `aria2` is bundled). Caches are shared across apps when `HF_HOME`/`TORCH_HOME` point at `PINOKIO_HOME/cache` (default is a per-app `./cache/HF_HOME`, with the documented option to delete the line and share).
- **Disk Saver (manual §15):** "finds files with identical contents and lets them share physical storage while every app keeps the file name and location it expects"; "Scanning is read-only"; "Make separate" restores a private copy; "Trash" for unreferenced data. Source: `kernel/vault/index.js` reports "This filesystem does not support hardlinks." and `registry_core.js` uses `unavailable_reason: "hardlinks" | "different_disk"`, i.e. a content-addressed store with hardlinks on the same volume (inferred from those strings).
- No model manifest, no hash pinning, no format policy: issue #880 (opened 2025-01-06, still open with no maintainer reply visible on 2026-10-06) reports that a verified script ("StyleTTS 2") pulls repositories with pickle files flagged unsafe by Hugging Face.

## 9. Local, remote or both

Local by design ("100% Local, running on your PC", manual §1.1). Optional exposure: LAN discovery ("LWW"), automatic HTTPS domains via bundled Caddy/cloudflared (manual §7, `kernel/bin/cloudflared.js`, `caddy.js`). Login to HF/GitHub from the app ("Login with localhost").

## 10. Known incidents and stated limitations

- **CVE-2025-44109** (MITRE CVE record, published 2025-07-23): "A URL redirection in Pinokio v3.6.23 allows attackers to redirect victim users to attacker-controlled pages." References are a PoC gist and page by the reporter.
- **Issue #880** "Pinokio = Virus/Malware Injector" (2025-01-06): concern that a verified script downloads HF-flagged unsafe pickle files; no protection against malicious code in installed projects. No maintainer response visible.
- Antivirus flags on bundled binaries (`uv.exe`, `nvprune`) reported in forums and sandbox reports in 2025 — community/press reports, not confirmed by the project.
- Stated limitations: Protection "can reduce risk, but some installs may fail"; it does not cover `pip`, `conda`, `brew`, `npx`, `uvx`, `cargo`; the README's isolation holds only "by default" and only for reviewed scripts.
- Two documentation sites: docs.pinokio.computer and program.pinokio.computer did not resolve through the proxy (CONNECT 502, 2026-10-06; not confirmed); the mirror https://pinokiocomputer.github.io/program.pinokio.computer/README.md and the current manual source https://desktop.pinokio.co/docs/README.md loaded.

## 11. Borrow

1. **The consent dialog that names the exact git remote** before anything runs, and the install option toggles next to it. Clips Kitty's installer should show `publisher/pipeline @ tag (commit)` and the repo URL the same way.
2. **Branch/hash install parameters** (`git switch --detach ${hash}`): the registry entry should carry the commit and the installer should check it out detached.
3. **Freshness delay for dependency installs** (Bluefairy: block packages newer than 72h, exempt exact pins): cheap, no backend, directly addresses the 2024–25 PyPI/npm incidents behind H6. Worth adopting for any plugin-local `pip`/`npm` step, and worth saying which commands it does not cover.
4. **Shared caches by environment variable** (`HF_HOME`, `TORCH_HOME`, `UV_CACHE_DIR`, `PIP_CACHE_DIR` under one home): the simplest form of model reuse across plugins, and it matches H7's blob sharing when the HF cache is used.
5. **Content-addressed dedup with hardlinks as an opt-in tool** (Disk Saver: read-only scan, "Make separate", Trash) rather than forcing a central library — a good fallback for models that plugins download on their own.
6. **Check-ins** (users posting platform/GPU/VRAM per app): opt-in telemetry that answers "does it run on my hardware" — fits H10 (opt-in counts).
7. **The old zero-cost discovery**: GitHub topic search as a "latest/community" feed, separate from the curated list. Clips Kitty's static index can take submissions by PR and still show a topic feed for unreviewed pipelines, clearly labelled.

## 12. Avoid

1. Calling location conventions "isolation" or "guaranteed to be secure and safe" when the runtime spawns a full shell. Clips Kitty only says "enforced" when the runtime enforces it (brief, fixed decision).
2. A verification process that runs through one person's DMs on X, requires transferring the developer's repository to the project's org, and lets the admin edit it — this is the opposite of "developers never fork" and does not scale (H4).
3. Executable manifests (`pinokio.js` with `menu` as an async function, inline JavaScript steps): the registry index must be inert JSON/YAML so it can be checked by CI.
4. No compatibility fields: GPU/platform branching inside scripts instead of declared requirements.
5. Model downloads with no hash, no revision and no format policy (issue #880 shows the consequence under the "verified" label).
6. A hosted directory that the app depends on (iframe of pinokio.co with login): discovery should work offline from a cached static index.

## 13. Sources (all read 2026-10-06)

- https://github.com/pinokiocomputer/pinokio — README "Script Policy", "Security", "Script Verification" (also read from the clone, HEAD 0765ab1, 2026-09-02).
- https://desktop.pinokio.co/docs/ — docsify shell; content loaded from https://desktop.pinokio.co/docs/README.md (271 KB; sections 1.3, 2, 11, 14, 15, "Building a launcher", "Script", "Instruction", "Launcher").
- https://raw.githubusercontent.com/pinokiocomputer/home/refs/heads/main/docs/README.md — identical manual source (pinokiod's `download_readme` script).
- https://pinokiocomputer.github.io/program.pinokio.computer/README.md — older manual: "Publish your script", "Install script from any git url", "List your script on the directory" (GitHub topic search URL).
- https://docs.pinokio.computer and https://program.pinokio.computer — CONNECT tunnel failed (502): not confirmed.
- https://pinokio.co/ , https://pinokio.co/apps?path=api , https://pinokio.co/apps?tab=latest&path=api , https://pinokio.co/apps/github-com-pinokiofactory-ai-toolkit , https://pinokio.co/apps/github-com-saintbrodie-orange-pinokio — directory structure, "Featured", check-ins, no "verified" badge.
- https://github.com/orgs/pinokiofactory/repositories — "101 repositories". (The GitHub org API was not reachable from this session.)
- Shallow clone of https://github.com/pinokiocomputer/pinokio (HEAD 0765ab1): `README.md`, `package.json`, `config.js`.
- Shallow clone of https://github.com/pinokiocomputer/pinokiod (HEAD add4a67, 2026-09-02): `kernel/api/script/index.js`, `kernel/shell.js`, `kernel/environment.js`, `kernel/api/hf/index.js`, `kernel/bin/`, `kernel/vault/index.js`, `kernel/vault/registry_core.js`, `kernel/plugin_sources.js`, `server/views/env_editor.ejs`, `package.json`.
- https://cveawg.mitre.org/api/cve/CVE-2025-44109 — CVE record (published 2025-07-23).
- https://github.com/pinokiocomputer/pinokio/issues/880 — user report, 2025-01-06.
- Web search (extended) for Pinokio incidents: SentinelOne CVE page, Malwarebytes forum and any.run reports — secondary sources, used only for the AV-flag note.

---

## Matrix row

`| Pinokio | Launcher repo: pinokio.json + pinokio.js + JSON/JS scripts of shell.run steps | Scripts download models themselves (hf download, wget); shared HF_HOME/TORCH_HOME caches optional; Disk Saver hardlink dedup | Scripts are ordered JSON-RPC steps with jump/when; daemon mode | Hosted directory pinokio.co (iframe) with Featured/check-ins; old: GitHub topic search; curated = pinokiofactory org (101 repos) | Yes: any public git URL, optional branch/hash | Yes | LAN/tunnel exposure optional | Git branches/commits; schema version 8.0.0; no immutable releases | No permissions; consent dialog naming the remote; 72h freshness delay; "verified" = one admin's manual review + repo transfer | No (free listing, no payments) | Consent dialog naming publisher/repo/commit; hash-pinned install; freshness delay for pip/npm; shared caches by env var; opt-in hardware check-ins; never call conventions isolation |`

## Hypotheses

- **H3** (static Git index, PR-submitted, CI-built, pinned ref): partly supported. Distribution is "any git URL + optional branch/hash" with no registry needed; the old "Latest" list was a GitHub topic search (no backend). The curated list, however, is an org of transferred repos plus a hosted site with login and check-ins — more than H3 needs. Pinning to a hash is supported by the installer but not shown to be used by the directory (not confirmed).
- **H4** (hand review does not scale): consistent, inferred. One admin reviews via X contact and manual testing; the directory's first page is mostly non-org repos, i.e. most listed apps did not go through the verified process.
- **H5** (declared ≠ enforced): strongly supported, by absence. Pinokio declares nothing and enforces nothing; "isolated by default" is a path convention verified by eye for the ~100 org repos, while `shell.run` is a full pty shell (`kernel/shell.js` line 862).
- **H6** (runtime installs, one env per plugin): Pinokio does the opposite of ComfyUI's registry rule — scripts pip-install at install and update — but mitigates with the Bluefairy freshness delay and one `venv` per app plus shared `UV_CACHE_DIR`/`PIP_CACHE_DIR`. Disk cost is addressed after the fact by Disk Saver.
- **H7** (HF cache sharing): supported where apps honour `HF_HOME`: the documented way to avoid redundancy is one `PINOKIO_HOME/cache/HF_HOME` for all apps. No revision pinning and no pickle policy (issue #880).
- **H1**: Pinokio is the counter-example — extensions are shell scripts in the user's session, not clients of a local API.
- **H10**: check-ins are opt-in, user-posted hardware reports per app; follower counts come from the hosted site, not GitHub.
