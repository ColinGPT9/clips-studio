# InvokeAI — research notes

- Platform: InvokeAI (invoke-ai/InvokeAI), Track A, Tier 2
- Date read: 2026-10-06
- Brief's question: what does a smaller node ecosystem teach, including what happens when stewardship changes?
- Verdict (two lines): InvokeAI's extension story is a Python class with a decorator, discovered from a `nodes/` folder and (since v6.13.0, May 2026) installed by plain `git clone` of an unpinned URL; the only "registry" is a hand-edited markdown list reviewed by PR with no checks, and it has received no community addition since 2025-10-05 while repositories on the `invokeai-node` GitHub topic kept shipping through September 2026.
  The model manager is the strong part: a SQLite record per model with `key`, `hash` (blake3, prefixed), `source`, `source_type`, `source_api_response`, `file_size`, picklescan on install, and its own download queue into a per-key `models/` directory (no Hugging Face cache, no symlinks); but it pins no Hugging Face revision and records no license. The stewardship change (founders to Adobe, 2025-10-28) did not stop releases or docs, because two long-standing maintainers took over, but it did freeze the curated list — a Git-indexed, CI-built registry would have survived better.

All URLs below were read on 2026-10-06 unless marked otherwise. Repository code was read from a shallow clone at commit `0ff4a63` (2026-10-05, "Admin database compaction (#9688)"), version string `7.0.0-alpha.1-post`.

## 1. Unit of extension

- **Node (invocation)**: a Python class decorated with `@invocation(...)` subclassing `BaseInvocation`, with pydantic `InputField`/`OutputField` fields and an `invoke(self, context)` method returning a `BaseInvocationOutput` subclass declared with `@invocation_output("...")`. Source: `invokeai/app/invocations/baseinvocation.py` (clone), `docs/src/content/docs/development/Guides/creating-node-pack.mdx` (clone).
- **Node pack**: a Git repository whose root has an `__init__.py` that imports the node classes. "Your repository **is** the node pack." (creating-node-pack.mdx). A pack may carry `workflows/*.json`, which are imported into the user's workflow library at install.
- **Workflow**: a JSON graph (`nodes`, `edges`, `exposedFields`, `meta.version`) saved in the workflow library; loaded via "Load Workflow". Source: creating-node-pack.mdx; `docs/src/content/docs/development/Front End/workflows.mdx`.
- **Model**: a record in the model database plus files under `models/`. Section 8.
- **External model**: a record with `source_type = external` that routes generation to a third-party API (Gemini, OpenAI, BytePlus Seedream, Alibaba DashScope). Section 9.
- There is no "extension" or "plugin" concept beyond nodes; UI cannot be extended by third parties (inferred from the absence of any such mechanism in the repo).

## 2. Manifest or metadata format

There is **no manifest file** for a node pack. The metadata lives in the decorator and the repository layout:

- `@invocation(invocation_type, title=None, tags=None, category=None, version=None, use_cache=True, classification=Classification.Stable, bottleneck=Bottleneck.GPU, idle_gpu_offloadable=False)` — `baseinvocation.py` lines 722–732. `invocation_type` "Must be unique among all invocations" and must match `^\S+$`; `version` is parsed with `semver.Version.parse(version)` (line 788), so an invalid semver fails at import. `classification` values include Stable, Beta, Prototype, Special (line 87ff). The pack name is derived, not declared: `node_pack = cls.__module__.split(".")[0]` (line 754), i.e. the directory name under `nodes/`.
- Repository contract (creating-node-pack.mdx): root `__init__.py` "mandatory"; `requirements.txt` or `pyproject.toml` optional and **user-installed**; `README.md` recommended; any `.json` with top-level `nodes` and `edges` keys is treated as a workflow; the GitHub topic `invokeai-node` is recommended for discoverability.
- The installer writes its own bookkeeping file into the pack dir: `.invokeai_pack_manifest.json` with `{"workflow_ids": [...]}` (custom_nodes.py, `PACK_MANIFEST_FILENAME`). It records which workflows the install created so uninstall deletes only those.
- Workflow JSON fields (creating-node-pack.mdx example): `name`, `author`, `description`, `version`, `contact`, `tags`, `notes`, `meta.version` ("3.0.0"), `meta.category`, `exposedFields`, `nodes`, `edges`. Workflow schema migrations are done with per-version zod schemas on load; each node's `version` is checked against its current template and a mismatch warns (workflows.mdx lines 283–298).
- Compatibility fields: none for the host version. A pack declares no minimum InvokeAI version; the docs advise `>=` dependency constraints because "InvokeAI has its own dependencies".
- Model record fields: section 8.

## 3. Distribution and install

Two paths, both ending in Python code executed inside the server process:

1. **Manual**: `git clone` (or copy) into the `nodes` directory (`custom_nodes_dir`, default `nodes`; `config_default.py` line 211). At startup `load_custom_nodes()` iterates the directory, skips names starting with `_` or `.` and dirs without `__init__.py`, and imports each with `importlib` `spec_from_file_location` + `exec_module` (`invokeai/app/invocations/load_custom_nodes.py`). A failing pack is logged and skipped. The README copied into `nodes/` still says "you must restart the app to see the changes" (custom_nodes/README.md line 5) — stale since the runtime loader arrived.
2. **Custom Node Manager** (introduced in v6.13.0; release notes: "There is now a Custom Node Manager tab in the left-hand panel which allows you to install and manage related groups of nodes organized into node packs." https://github.com/invoke-ai/InvokeAI/releases/tag/v6.13.0 ; tag commit date 2026-05-26 from the fetched tag object). Router `invokeai/app/api/routers/custom_nodes.py`, prefix `/api/v2/custom_nodes`:
   - `GET /` list packs (name, path, node_count, node_types);
   - `POST /install` with `{"source": "<git URL>"}` → `subprocess.run(["git", "clone", source, target_dir], timeout=120)`; requires root `__init__.py` (else the clone is deleted); loads the module at runtime; scans `*.json` recursively for workflows, imports them as public workflows owned by the installing admin, tagged `node-pack:<pack-name>`; detects `requirements.txt`/`pyproject.toml` and returns `requires_dependencies=true` with the comment "do NOT install them automatically";
   - `DELETE /{pack_name}` → `rmtree`, `InvocationRegistry.unregister_pack`, purge `sys.modules`, delete manifest-listed workflows;
   - `POST /reload` → re-scan the directory.
   - All four routes take `AdminUserOrDefault` (admin in multi-user mode; the single default user otherwise). A process-wide `_PACK_MUTATION_LOCK` serialises install/uninstall/reload.
   - No `update` route exists although the user doc says the manager "installs, updates, and removes" (custom-node-manager.mdx); updating means uninstall and reinstall, or `git pull` by hand (inferred).
   - The pack name is the last path segment of the URL (minus `.git`), validated against `^[A-Za-z0-9][A-Za-z0-9._-]*$`. Two packs with the same repository name cannot coexist ("already exists. Uninstall it first").
   - **No ref, tag or commit is pinned**: the clone takes the default branch head. Nothing records which commit was installed.
- What runs at install time: `git clone`, then the pack's `__init__.py` and everything it imports — arbitrary Python, in-process, immediately. The doc says so: "Custom nodes execute arbitrary Python on your machine. **Only install node packs from authors you trust.**" (custom-node-manager.mdx, caution box).

## 4. Dependencies and isolation

- One shared environment: the InvokeAI venv (launcher-managed; `pins.json` fixes Python 3.12 and the torch index per platform/accelerator). Node packs run in the server process with the server's privileges and full `context.services` (model manager, images, boards, etc.) — no sandbox, no permission layer.
- Pack dependencies are **never auto-installed**; the API only reports `dependency_file`. Rationale in both code and docs: "arbitrary pip installs can break the InvokeAI environment" (custom_nodes.py comment). Users are told to `pip install -r requirements.txt` inside the activated venv, then press Reload.
- Conflict handling: advice only ("Avoid pinning versions too tightly"; "Use minimum version constraints (`>=`)"). Nothing checks or resolves conflicts.
- Name collisions: `InvocationRegistry.register_invocation` replaces an existing type and logs a warning: `Overriding core node "<type>" with node from "<pack>"` (baseinvocation.py line 394) — a custom pack can silently (warning-level) replace a core node. Docs tell authors to prefix type strings (`my_pack_resize` not `resize`). Operators can restrict with `allow_nodes` / `deny_nodes` lists in `invokeai.yaml` (config_default.py lines 272–273).

## 5. Versioning and updates

- Node version: semver string in the decorator; workflows store the node version used and warn on mismatch at load (workflows.mdx). No compatibility range between node and host.
- Pack version: none. Install is an unpinned clone; there is no record of the installed commit and no update endpoint; rollback = reinstall an older state by hand (inferred).
- Workflow schema: `meta.version` with migrations kept per version (workflows.mdx "Workflow Migrations").
- Host releases: monthly-ish, tags listed below; `SECURITY.md`: "Only the latest version of Invoke will receive security updates."
- Models: a record keeps `hash`, `source`, `source_api_response`; HF installs are not pinned to a revision (section 8).

## 6. Registry design

- **The community list is a markdown page in the docs**: `docs/src/content/docs/Users Guide/08.Workflows/community-nodes.mdx` (formerly `docs/nodes/communityNodes.md`; moved by "chore(docs): reorganize document tree", 2026-09-29, https://github.com/invoke-ai/InvokeAI/commits/main/docs/src/content/docs/Users%20Guide/08.Workflows/community-nodes.mdx). Published at https://invoke.ai/features/workflows/community-nodes/ (loads). The doc's own link target `/users-guide/workflows/community-nodes/` returned 404 on 2026-10-06.
- Entry format (quoted from the page template in creating-nodes.mdx): `### <Name>`, `**Description:**`, `**Node Link:** <GitHub URL>`, optional `**Example Node Graph:**` and `**Output Examples**` images. 49 `**Node Link**` entries plus the "Example Node Template" entry on 2026-10-06.
- Submission (creating-nodes.mdx, steps 2–4): put the node in a repo with a README, add the `invokeai-node` tag, "Submit a pull request with a link to your node(s) repo in GitHub against the `main` branch to add the node to the Community Nodes list", "A maintainer will review the pull request and node. If the node is aligned with the direction of the project, you may be asked for permission to include it in the core project."
- Checks: none. No CI workflow in `.github/workflows/` references the list or runs link checks (listed: build-container, build-wheel, clean-caches, close-inactive-issues, deploy-docs, frontend-checks, frontend-tests, label-pr, lfs-checks, openapi-checks, python-checks, python-tests, release, typegen-checks, uv-lock-checks). Nothing verifies the linked repo exists, loads, or is the author's.
- Disclaimer on the page: "The nodes linked have been developed and contributed by members of the Invoke AI community. While we strive to ensure the quality and safety of these contributions, we do not guarantee the reliability or security of the nodes."
- Cost to run: zero beyond PR review time — and that is the failure mode. History of the old path (https://github.com/invoke-ai/InvokeAI/commits/main/docs/nodes/communityNodes.md): 40 commits from 2023-09-28 to 2026-04-18; the last community addition is "docs: add BiRefNet and Image Export to communityNodes.md" by veeliks on 2025-10-05; everything after is maintainer doc reorganisation (2026-04-17/18 "New Documentation Fixes" and its revert/re-revert; 2026-09-29 tree reorganisation). Meanwhile the GitHub topic https://github.com/topics/invokeai-node lists 26 repositories, several updated in 2026 (e.g. Pfannkuchensack's five repos "Updated Aug 24, 2026", licyk's `invoke_wd14_tagger` "Updated Sep 17, 2026"); neither author appears on the list (grep of the mdx: 0 hits for each). The list is stale relative to real activity.
- Discovery in-app: the Custom Node Manager has no catalogue; the user pastes a URL. The GitHub topic is the de-facto index.

## 7. Trust and permissions

- Tiers: none. "Verified" does not exist. A listed node means a maintainer once merged a PR adding a link.
- Declared permissions: none; a node has whatever the process has. The only operator controls are `allow_nodes`/`deny_nodes` (by type string) and admin-only pack routes.
- Model files: picklescan runs on `.ckpt/.pt/.pth/.bin` at probe time and aborts on an infected file or a scan error (`model_on_disk.py` lines 178–198); `unsafe_disable_picklescan` config documented as "UNSAFE ... will allow arbitrary code execution during model installation" (config_default.py line 148). Safetensors are read header-only.
- Security policy: `SECURITY.md` — report to security@invoke.ai or huntr; "we do not maintain a formal bug bounty program".
- 7.0 alpha adds users/auth; pack management requires admin (`require_admin_or_default`).

## 8. Models

Architecture doc: https://invoke.ai/development/architecture/model-manager/ (loads; `lastUpdated: 2026-02-18` in the clone). Four services: `ModelRecordServiceBase`, `ModelInstallServiceBase`, `DownloadQueueServiceBase`, `ModelLoadServiceBase`. Note the doc is partly stale: it says the hash is from "sampling several parts of the model's files using the `imohash` library" and names fields `model_type`/`base_model`; the code uses blake3 and `type`/`base`/`format`.

- **Record** (`invokeai/backend/model_manager/configs/base.py`, `Config_Base`): `key` (uuid, default_factory), `hash`, `path` (relative to Invoke root / models dir), `file_size`, `name`, `description`, `source` ("The original source of the model (path, URL or repo_id)"), `source_type` (`ModelSourceType`: `path`, `url`, `hf_repo_id`, `external`), `source_api_response` ("The original API response from the source, as stringified JSON"), `source_url`, `cover_image`; subclasses add `type`, `base`, `format`, and e.g. `repo_variant`, `variant`, `config_path`, `trigger_phrases`. **No license field** (grep of model_manager/, model_install/, model_records/: only docstring notes about Gemma/PiD/LTX licences; `metadata/__init__.py` docstring mentions `LicenseRestrictions` but no such class exists in the package).
- **Database**: SQLite table `models` with `id` and a JSON `config` column; `hash`, `base`, `type`, `path`, `format`, `name`, `description`, `source`, `source_type`, `source_api_response`, `trigger_phrases` are generated virtual columns via `json_extract` (`sqlite_migrator/migrations/migration_7.py`). Uniqueness is enforced on (name, type, base): "A model with name=..., type=..., base=... is already installed" (`model_records_sql.py` line 135).
- **Hashing** (`invokeai/backend/model_hash/model_hash.py`): default `blake3_single` (config `hashing_algorithm`, options include `blake3_multi`, hashlib names, and `random` which "disables hashing"); output prefixed with the algorithm (`blake3:<hex>`); for a directory, hash each `.ckpt/.safetensors/.bin/.pt/.pth` file and blake3 the sorted list of hashes. Full-file hashing, 8 MiB chunks.
- **Install sources** (`model_install_default.py`): `LocalModelSource` with `inplace` (register in place or copy into `models/`), `URLModelSource`, `HFModelSource(repo_id, variant, subfolder/subfolders, access_token)`; string syntax `org/repo:fp16:subfolder`. HF metadata is fetched with `HfApi().model_info(repo_id, files_metadata=True, revision=variant)` and file URLs built with `hf_hub_url(..., revision=variant or "main")` (`metadata/fetch/huggingface.py` lines 100, 118). **`revision` is only ever a variant branch name (fp16/…) or `main`; no commit hash is pinned.** Per-file LFS `sha256` from the API is kept in `RemoteModelFile.sha256`, and the whole `model_info.__dict__` is serialised into `source_api_response`, so the repo `sha` and file hashes are present in the record only as opaque JSON (inferred from `api_response=json.dumps(model_info.__dict__)`).
- **Downloading and cache**: InvokeAI's own multithreaded download queue (`services/download/download_default.py`; no `huggingface_hub` download calls there) writes files into the models directory, one directory per model `key`; `download_cache_dir` defaults to `models/.download_cache` for "dynamically downloaded models". The Hugging Face hub cache and its symlinks are not used for installed models, so the Windows symlink question does not arise for them. Exceptions: a few utility models (`image_util/safety_checker.py` `snapshot_download`, `lineart.py`/`hed.py` `hf_hub_download("lllyasviel/Annotators", ...)`) go through the HF cache.
- **Sharing**: one models directory per install; relative paths resolve against `models_dir`; models can be registered in place (`inplace=True`) from any path. Starter models are curated lists in `backend/model_manager/starter_models/` (some entries say "Non-commercial license — accept it on HuggingFace first", i.e. licence handling is prose, not data).
- **Tokens**: `remote_api_tokens` (regex → bearer token) in config; HF token via `huggingface_hub.get_token`.

## 9. Local, remote or both

- Local by default: self-hosted server + web UI; the launcher (https://github.com/invoke-ai/launcher) installs it.
- Remote inference as **external models**: Google Gemini, OpenAI GPT Image/DALL·E 3, BytePlus Seedream, Alibaba DashScope, with keys in `api_keys.yaml` next to `invokeai.yaml`; each external model "declares its own capabilities" (modes, reference images, aspect ratios, seed, batch) which drive the UI (docs/src/content/docs/Users Guide/03.Models/External Models/01.introduction.mdx; `services/external_generation/`). Billing is on the user's provider account.
- The hosted service is gone (section 10). No remote node execution exists.

## 10. Known incidents and stated limitations

- **CVE-2024-12029 / GHSA-mcrp-whpw-jp68** (https://github.com/advisories/GHSA-mcrp-whpw-jp68, read 2026-10-06): "InvokeAI Deserialization of Untrusted Data vulnerability", CVSS 9.8 critical, versions 5.3.1–5.4.2, patched 5.4.3rc2, published to the GitHub Advisory Database 2025-03-21; unauthenticated `/api/v2/models/install` + `torch.load` on an attacker-supplied model. The picklescan-before-`torch.load` path in `model_on_disk.py` is the present mitigation (inferred).
- Stated limitations: custom nodes execute arbitrary Python (docs caution); dependencies are not installed and may conflict; the data model allows one `base` per model ("Limitations of the Data Model" in the architecture doc); only the latest version gets security fixes.
- Stewardship change (facts, with source type):
  - Press (Adobe press release, https://news.adobe.com/news/2025/10/adobe-max-2025-firefly-foundry, dated 2025-10-28): "the team from Invoke—a generative media solution for creative production—has joined the Adobe Firefly Foundry team to help build the future of AI-powered creative workflows for businesses."
  - Official status (https://invoke.ai, heading "About the former officially-hosted version"): "The Invoke.ai hosted platform has been shut down as the founding team joined Adobe. However, Invoke lives on as a thriving open-source project maintained by the community. … Stewardship of the project has been passed to Lincoln Stein (lstein) and Vic (Blessedcoolant), who have been core maintainers since the project's inception". No date on the page. A search snippet attributed to invoke.ai says the service was shut down in October 2025; https://app.invoke.ai ("Hosted Service Discontinued" in search results) did not resolve (DNS ENOTFOUND) — not confirmed. Dealroom/Substack items reporting a "$3.8M" figure are press and were not opened — not confirmed.
  - Releases (tag commit dates from fetched tag objects; authors from the release pages): v6.9.0 2025-10-17 by psychedelicious (last pre-transition release); v6.10.0rc1 2025-12-26 by lstein, whose notes say "This is the first InvokeAI Community Edition release since the closure of the commercial venture, and we think you will be happy with the progress." (https://github.com/invoke-ai/InvokeAI/releases/tag/v6.10.0rc1); v6.10.0 2026-01-05; v6.12.0 2026-03-21; v6.13.0 2026-05-26 (Custom Node Manager); v6.14.0 2026-08-25; v7.0.0-alpha.1 2026-10-04. Every release since v6.10.0rc1 is published by lstein. Release cadence did not break.
  - Docs: the old site https://invoke-ai.github.io/InvokeAI/ now 301-redirects to https://invoke.ai ; the former commercial domain hosts the Astro/Starlight docs (`docs/package.json`: `@astrojs/starlight`). Docs were reorganised twice in 2026 (April, September). Architecture docs lag the code (imohash vs blake3; "updates" vs no update route).
  - Community node list: frozen at the transition (last addition 2025-10-05), see section 6. Repo not archived; README badges and Discord remain; 28.3k stars on the GitHub page (2026-10-06).
  - Policy change by the new maintainers: dependencies are explicitly not auto-installed; pack routes are admin-only; `AGENTS.md` files codify engineering rules per area (clone root).

## 11. Borrow

- **Model record shape**: `key`, `hash` with algorithm prefix (`blake3:…`), `path`, `file_size`, `source`, `source_type` enum (path/url/hf_repo_id/external), `source_api_response` (raw provider JSON), `source_url`, `cover_image`; store as one JSON column with generated columns for the queryable fields. Add what InvokeAI lacks: `revision` (HF commit sha), `license` (from the HF card), per-file sha256 as first-class fields.
- **Directory hash rule**: hash each weight file, then hash the sorted list; prefix the algorithm so it can change later; offer `random` only for tests.
- **Picklescan gate before any `torch.load`**, abort on scan error, and a loudly named escape hatch (`unsafe_disable_picklescan`). Pair with "prefer safetensors".
- **Own download queue into a per-model directory**, not the HF hub cache: avoids Windows symlink trouble and keeps one models root the user can see.
- **External-model capability declaration** that drives the UI (modes, limits) — the same idea Clips Kitty needs for remote/hybrid pipelines.
- **Pack install bookkeeping**: a manifest written at install that records exactly what the install created (InvokeAI: workflow ids) so uninstall removes only that; never rely on user-editable tags.
- **Runtime load/unload** with registry invalidation, so install needs no restart; a mutex around install/uninstall/reload.
- **Node contract**: `type` + semver `version` + `classification` (Stable/Beta/Prototype) in the declaration; workflows record the node version and warn on mismatch.
- **Dependency honesty**: report `requires_dependencies` and the file name instead of pretending to resolve it; if Clips Kitty chooses per-plugin environments, say so because of exactly this limitation.
- **The `invokeai-node` topic** shows a zero-cost discovery channel that kept working after the list froze; a CI-built index can read topic + stars + last-updated (H10).

## 12. Avoid

- A hand-edited markdown list reviewed by humans with no CI: it froze the moment the paying staff left, and it already missed active authors. Use the Git index with one metadata file per extension, validated by CI (H3), and automated checks before any human look (H4).
- `git clone` of an unpinned URL with no record of the installed commit and no update/rollback path.
- Pack identity = repository directory name: no publisher namespace, collisions resolved by "uninstall it first", and type-string collisions that silently override core nodes.
- In-process extensions with the server's full access and no permission concept; "only install from authors you trust" is the whole security model.
- Docs that describe a feature the code does not have ("installs, updates, and removes") and architecture docs that drift (imohash vs blake3).
- An unauthenticated model-install endpoint that deserialises what it is given (CVE-2024-12029). Clips Kitty's engine is unauthenticated on localhost; any install endpoint needs the Host check plus a scan step plus a user confirmation.
- Recording provenance only as an opaque API blob; keep revision, hashes and licence as typed fields.

## 13. Sources (all read 2026-10-06)

- https://invoke.ai — status notice; loads; no dates on page.
- https://invoke.ai/features/workflows/community-nodes/ — loads; ~50 entries; disclaimer.
- https://invoke.ai/features/workflows/ — loads; overview only.
- https://invoke.ai/development/architecture/model-manager/ — loads; partly stale (imohash).
- https://invoke.ai/users-guide/workflows/community-nodes/ — HTTP 404.
- https://invoke-ai.github.io/InvokeAI/ — 301 to http://invoke.ai/.
- https://app.invoke.ai — DNS ENOTFOUND; not confirmed.
- https://github.com/invoke-ai/InvokeAI — loads; 28.3k stars; Apache-2.0 plus model licences.
- https://github.com/invoke-ai/InvokeAI/releases (pages 1–4) — loads; the summariser's years were unreliable, so dates were taken from tag objects fetched into the scratch clone (`git fetch --depth=1 origin refs/tags/<tag>`).
- https://github.com/invoke-ai/InvokeAI/releases/tag/v6.10.0rc1 , …/v6.9.0 , …/v6.12.0 , …/v6.13.0 — load; quotes above.
- https://github.com/invoke-ai/InvokeAI/commits/main/docs/nodes/communityNodes.md — loads; 40 commits 2023-09-28 → 2026-04-18.
- https://github.com/invoke-ai/InvokeAI/commits/main/docs/src/content/docs/Users%20Guide/08.Workflows/community-nodes.mdx — loads; one commit 2026-09-29.
- https://github.com/topics/invokeai-node — loads; 26 repositories.
- https://github.com/advisories/GHSA-mcrp-whpw-jp68 — loads; CVE-2024-12029.
- https://news.adobe.com/news/2025/10/adobe-max-2025-firefly-foundry — press; loads; dated 2025-10-28.
- https://news.adobe.com/news/downloads/pdfs/2025/10/102825-adobe-firefly-foundry.pdf — press; binary PDF, text not extractable; not confirmed.
- api.github.com and github.com via curl — blocked by the session proxy for this repository ("GitHub access to this repository is not enabled for this session"); not used.
- Shallow clone: /tmp/claude-0/-home-user-clips-studio/8828d716-03cc-5de9-81b9-53d443093a47/scratchpad/research-clones/InvokeAI (commit 0ff4a63, 2026-10-05). Files read: `README.md`, `SECURITY.md`, `AGENTS.md`, `pins.json`, `invokeai/version/invokeai_version.py`, `invokeai/app/invocations/__init__.py`, `invokeai/app/invocations/load_custom_nodes.py`, `invokeai/app/invocations/baseinvocation.py`, `invokeai/app/invocations/custom_nodes/README.md`, `invokeai/app/api/routers/custom_nodes.py`, `invokeai/app/api/auth_dependencies.py`, `invokeai/app/services/config/config_default.py`, `invokeai/app/services/model_install/model_install_default.py`, `invokeai/app/services/model_records/model_records_sql.py`, `invokeai/app/services/shared/sqlite_migrator/migrations/migration_7.py`, `invokeai/app/services/download/download_default.py`, `invokeai/backend/model_manager/configs/base.py`, `invokeai/backend/model_manager/taxonomy.py`, `invokeai/backend/model_manager/model_on_disk.py`, `invokeai/backend/model_manager/metadata/*`, `invokeai/backend/model_hash/model_hash.py`, `docs/src/content/docs/Users Guide/08.Workflows/{community-nodes,custom-node-manager}.mdx`, `docs/src/content/docs/development/Guides/{creating-nodes,creating-node-pack}.mdx`, `docs/src/content/docs/development/Architecture/model-manager.mdx`, `docs/src/content/docs/development/Front End/workflows.mdx`, `docs/src/content/docs/Users Guide/03.Models/External Models/*.mdx`, `.github/workflows/` listing, `docs/package.json`.
- Search snippets (not opened, press): Dealroom and a Substack post on the Adobe deal; a snippet attributing "shut down in October 2025" to invoke.ai.

## Matrix row

`| InvokeAI | Python node class (@invocation) in a Git "node pack" discovered from nodes/ | SQLite record: key, blake3 hash, source, source_type, source_api_response, file_size; HF/URL/local install; picklescan; no revision pin, no license field | JSON workflow graphs with meta.version + migrations; packs ship workflows | Markdown list in docs, PR-reviewed by a maintainer, no checks, frozen since 2025-10 | git clone of unpinned URL via UI (v6.13.0, 2026-05) or by hand | Yes (self-hosted server) | External API models (Gemini/OpenAI/Seedream/DashScope) via api_keys.yaml; hosted service closed 2025-10 | Node semver in decorator; no pack version, no installed-commit record, no update/rollback | In-process, no permissions; admin-only routes; allow/deny node lists; CVE-2024-12029 (torch.load on install) | None (GitHub topic invokeai-node is the de-facto index, 26 repos) | Copy the model record and hash/scan pipeline; do not copy the hand-edited list or unpinned clone; two-maintainer handover kept releases and docs alive but froze curation |`

## Hypotheses

- **H1 (out-of-process plugins are lowest risk)** — supported by contrast: InvokeAI nodes run in-process with full service access; the docs' only defence is "Only install node packs from authors you trust"; CVE-2024-12029 showed what an in-process load path costs.
- **H2 (smallest contract)** — not addressed directly; the node contract is "typed pydantic inputs → typed outputs" with semver and classification, which is compatible with a "video in, time ranges out" node.
- **H3 (static Git registry, one file per extension, CI-built)** — supported by failure: InvokeAI's registry is one markdown file edited by PR, with no per-entry file and no CI; it cost nothing, needed no backend, and stopped being updated when stewardship changed (last addition 2025-10-05 vs. topic repos updated Aug–Sep 2026). The zero-cost part held; the "index built by CI" part is what was missing.
- **H4 (hand review does not scale)** — supported: review is "A maintainer will review the pull request and node"; 49 entries in three years; no automated checks; no review capacity after the transition.
- **H5 (declared ≠ enforced)** — supported: no declared permissions at all; the only enforced controls are admin-only routes and `allow_nodes`/`deny_nodes`.
- **H6 (dependency handling)** — supported: shared venv; auto `pip install` deliberately refused "since arbitrary pip installs can break the InvokeAI environment"; users install by hand; conflicts handled by advice only.
- **H7 (HF revision pins to a commit; cache shares blobs; Windows symlinks; prefer safetensors, warn on pickle)** — partly rejected for this platform: InvokeAI does **not** pin a commit (revision = variant branch or `main`), does **not** use the HF hub cache for installed models (own downloader into `models/<key>/`, so no symlink sharing and no Windows symlink issue), **does** record `source`, `source_type`, `source_api_response` (which contains the API `sha` and per-file LFS sha256 as JSON) and its own content hash, and **does** gate pickle formats with picklescan while reading safetensors header-only. It records no licence. Verdict: records source + hash, not revision or licence; Clips Kitty should add both.
- **H8 (namespaced ids + declared capabilities with typed I/O)** — supported by absence: pack identity is the directory name, type strings are global, collisions override with a warning, and the docs ask authors to hand-prefix (`my_pack_resize`).
- **H9** — not addressed.
- **H10 (counts need telemetry; stars/last-updated from GitHub)** — supported: InvokeAI has no install counts; the topic page already gives stars and last-updated per repo at zero cost.
