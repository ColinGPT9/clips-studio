# Open WebUI — research notes

- Platform: Open WebUI (Functions, Tools, Pipelines, OpenAPI/MCP tool servers, community site openwebui.com)
- Tier: 1 (Track A; main Track E security case)
- Date read: 2026-10-06
- Brief's question: third-party code may have access to the local machine. What trust and permission model does that demand, and which security mistakes should Clips Kitty avoid?

**Verdict (two lines):** Open WebUI runs community Functions and Tools inside its own server process by `exec()`-ing database-stored Python with the server's full access, pip-installs their declared `requirements` into the shared environment, and documents all of this as intended ("no sandbox, no allowlist, no capability system"); its only control is who may save code (admins, or a permission the vendor equates with shell access).
Clips Kitty should borrow the honesty (blunt warnings, "listed does not mean vetted", admin-only gating, typed Valves config, kill switches) and avoid the mechanism (in-process `exec`, runtime pip into one env, unvalidated frontmatter, branch-URL import, a bridge between registry site and app that itself became a High-severity advisory).

Source snapshot: shallow clone of https://github.com/open-webui/open-webui at commit 8bd8b4fac5e059578ac0c74b3c18d11139f88b7d (committed 2026-09-21), read only, 2026-10-06. Docs pages read 2026-10-06 unless stated. Line numbers refer to that snapshot.

## 1. Unit of extension

| Unit | What it is | Where it runs |
|---|---|---|
| Function: Pipe | "Adds a custom model or agent"; appears as a selectable model; a `pipes()` method can expose many sub-models as `<function_id>.<id>` ("manifold") | Inside the Open WebUI backend process |
| Function: Filter | middleware with `inlet()`, `stream()`, `outlet()` (and since v0.11.2 a `request` step); can be `is_global` | In-process |
| Function: Action | a button on chat messages that runs Python server-side | In-process |
| Function: Event | runs in response to system events, no UI (`class Event`) | In-process |
| Tool ("Workspace Tool") | a Python class `Tools` whose public methods become LLM-callable tools; OpenAPI-style `specs` are derived from docstrings | In-process |
| Tool server (OpenAPI, MCP HTTP, MCPO) | an external HTTP service; "the tool code runs on a separate process or machine and Open WebUI calls it over HTTP" (tools overview) | Out of process |
| Pipeline | a `.py` file loaded by the separate `pipelines` server (port 9099), exposed as an OpenAI-compatible model; README: "DO NOT USE PIPELINES!" unless offloading heavy work; docs call it legacy | Separate process/container |
| Model (workspace) | a preset (base model, system prompt, params), not code | n/a |

Type detection is by class name: `load_function_module_by_id` returns the first of `Pipe`, `Filter`, `Action`, `Event` found in the module namespace; `load_tool_module_by_id` requires a `Tools` class (`backend/open_webui/utils/plugin.py` lines 206–315).

## 2. Manifest / metadata

There is no manifest file. Metadata is "frontmatter" inside the module's leading triple-quoted docstring, parsed line by line with `^\s*([a-z_]+):\s*(.*)\s*$` (`extract_frontmatter`, plugin.py lines 151–184). Every value is a string; no schema, no validation. The dict is stored as `meta.manifest` on the row (`routers/functions.py` line 223, `routers/tools.py` line 389).

Documented fields (Tools development page): `title`, `author`, `author_url`, `git_url`, `description`, `version`, `license`, `requirements`, `required_open_webui_version`, `funding_url`, `icon_url`. Functions page: `title`, `author`, `author_url`, `version`, `icon_url`, `required_open_webui_version`, `requirements` ("Comma-separated list of pip packages to auto-install").

What the fields do in code:
- `requirements`: split on commas and passed to `pip install` (§3, §4). The only field with backend runtime effect.
- `required_open_webui_version`: compared only in the frontend editor (`src/routes/(app)/admin/functions/{create,edit}/+page.svelte`, `compareVersion(...)` warning); the backend never checks it.
- `version`, `git_url`, `license`: display only.

DB rows (`models/functions.py`, `models/tools.py`): `id` (must satisfy `str.isidentifier()`, lower-cased; `routers/functions.py` lines 204–212), `user_id`, `name`, `type`, `content` (Python source), `meta` (`description`, `manifest`; tools also `i18n`, `has_user_valves`), `valves` (JSON; Fernet-encrypted when `ENABLE_VALVE_ENCRYPTION=true`, default `False`, env.py line 767), `is_active`, `is_global`, timestamps. Tools also carry `specs` and `access_grants` (sharing with users/groups).

Pipelines server: same docstring frontmatter, parsed by `parse_frontmatter` (split on first `:`), `requirements` pip-installed per package (pipelines `main.py` lines 113–131).

## 3. Distribution and install

Three paths, all ending in `POST /api/v1/functions/create` or `/api/v1/tools/create`:
1. Community site: sign in on openwebui.com, open the post, click **Get**, enter the instance URL, **Import**; "Your instance opens in a new tab with the matching editor already filled in"; "The post is passed from the openwebui.com tab to your instance's tab inside the browser, so `http://localhost:3000` ... works" (ecosystem/community page). The user then saves.
2. URL import: `POST /load/url` (admin only) fetches any URL with aiohttp and returns `{name, content}`. `github_url_to_raw_url` rewrites `github.com/<org>/<repo>/blob/<branch>/<path>` and `/tree/<branch>/<dir>` (appends `main.py`) to `raw.githubusercontent.com/.../refs/heads/<branch>/...`; any other URL is fetched as-is (`routers/tools.py` lines 247–315; same in functions). Code comment: "This is NOT a SSRF vulnerability: This endpoint is admin-only ... Access is enforced by authentication."
3. Editor paste; JSON export/import; `POST /sync` (admin).

What runs at install: on create and on content update the server calls `load_*_module_by_id(id, content)`, which (a) runs `pip install` for frontmatter `requirements` in a thread, then (b) `exec(content, module.__dict__)` and instantiates the class (plugin.py lines 259–315). Saving executes module top-level code before the item is "enabled". At startup `install_tool_and_function_dependencies()` re-collects `requirements` from all active functions and all admin-owned tools and pip-installs them (lines 453–484). Vendor: "Anything at module top level runs at this point ... Any side effect of step 3 (imports, monkey-patches, background tasks, route registrations) is now installed in the live application" (Under the Hood).

Code lives in the SQL database, not on disk; a temp file exists only to give the module a `__file__` and is deleted (lines 283–315). `replace_imports()` rewrites `from utils` / `from apps` / `from main` / `from config` to `open_webui.*` and writes the modified source back to the DB (lines 187–201, 270–271).

Sharing out: **Share** opens `https://openwebui.com/functions/create` in a new tab and posts the full function JSON by `postMessage` after the tab sends `loaded`; the incoming message is origin-checked, the outgoing `postMessage(..., '*')` is not (`src/lib/components/admin/Functions.svelte` lines 158–185; same in `workspace/Tools.svelte`). `COMMUNITY_ORIGINS` = `https://openwebui.com`, `https://www.openwebui.com`, `http://localhost:9999` (`src/lib/constants.ts`). Docs: "Sharing a Tool or Function publishes its full source code ... Valve values ... are not included."

Pipelines: upload a `.py`, paste a GitHub URL in admin settings, or `PIPELINES_URLS` at container start; `/pipelines` directory auto-loads at boot; `requirements` installed at startup; default API key `0p3n-w3bu!` (pipelines README). Loader uses `importlib.util.spec_from_file_location` + `exec_module`; a failed file is moved to `pipelines/failed/` (main.py lines 133–170).

## 4. Dependencies and isolation

- One shared environment. `install_frontmatter_requirements` runs `[sys.executable, '-m', 'pip', 'install'] + PIP_OPTIONS + new_reqs + PIP_PACKAGE_INDEX_OPTIONS` via `subprocess.check_call` (plugin.py lines 422–450). A process-global set `_installed_requirements` only de-duplicates identical strings; nothing resolves conflicts or pins hashes.
- Switches: `ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS` (default `True`), `OFFLINE_MODE` (skips installs, also sets `HF_HUB_OFFLINE=1`), `PIP_OPTIONS`, `PIP_PACKAGE_INDEX_OPTIONS`, `ENABLE_PLUGINS` (default `True`; loaders raise `RuntimeError('Plugins are disabled by ENABLE_PLUGINS=false')`), `SAFE_MODE` ("deactivating all functions").
- Vendor on its own default: "Strongly recommended: set `ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS=False` in production. Runtime pip installs allow any admin-uploaded function or tool to install arbitrary Python packages into the running process" (env reference). "Multiple tools defining different versions of the same package" causes unpredictable behaviour; "runtime installs race across workers and crash" (Tools development; Under the Hood).
- Isolation: none. "They are Python modules executed inside your Open WebUI process, with full access to the standard library, any pip package, the entire `open_webui` codebase, the live FastAPI app, and the database ... There is no sandbox, no allowlist, no capability system. The execution model is 'this is Python, you are inside the server process'" (Under the Hood). Plugins can monkey-patch backend functions, register unauthenticated FastAPI routes, spawn background tasks and share `app.state` with no namespacing (same page).
- Unload: "Disabling or deleting a plugin ... does not undo anything its module top level did. The module stays in `sys.modules` ... until the process restart." "Cross-plugin interference is not detected ... Load order is not deterministic."
- Modules are cached in `request.app.state.FUNCTIONS` / `TOOLS` and re-`exec`'d when the stored source changes; a failed function load sets `is_active=False` (plugin.py lines 307–313).
- Vendor's own guidance for isolation: "If you genuinely need GPU access, large or conflicting dependencies, hard isolation or independent scaling, run that work as an external service behind an OpenAPI or MCP tool server" (extensibility overview). External servers "only see what you pass over HTTP and can't reach into Open WebUI itself" (tools overview).

## 5. Versioning and updates

- `version` is free text; no immutability, no pinning, no update check, no rollback; editing in place replaces the only copy (no update path found in routers or frontend).
- `required_open_webui_version`: frontend warning only (§2).
- URL import resolves `refs/heads/<branch>`, a moving ref, never a tag or commit (§3).
- Vendor states internal APIs are unstable: "`open_webui.utils.*`, the internal model classes, middleware helpers ... can rename, move, or change signatures between releases" (Under the Hood). Pipelines are "legacy"; migration guidance maps pipe→Pipe Function, filter→Filter Function.

## 6. Registry design

- openwebui.com is a hosted web application run by Open WebUI Inc. with its own accounts ("Browsing needs no account. Getting a post, publishing one and syncing stats need you to sign in on openwebui.com"; ecosystem/community page). It hosts Models, Prompts, Tools, Functions, Chats, Reviews, a leaderboard and topic "communities". Home page claims "507,186 members sharing what they've built" (openwebui.com, 2026-10-06).
- Submission: the instance's **Share** button pre-fills a form on the site; "Nothing is published until you finish the form there and post it." No PR, no CI, no manifest check.
- Review: none. "Posts are submitted by users and are not reviewed for security or quality. Being featured, popular or highly rated is not an endorsement" (ecosystem/community). "Listings are community-submitted and unaudited" (community plugins page). The docs do hand-pick "Highlighted plugins" while stating "Highlighting is not vetting".
- Install from the registry copies source into the instance; no link to a repository ref survives. `git_url` is an optional string.
- Cost to run: not public; it is a hosted service with accounts and a leaderboard, not a static index. Not confirmed.
- Note: the browser-tab handoff between site and instance (`postMessage`) is what let the site work with private/localhost instances, and is also where GHSA-vpq8-f445-hcq7 occurred (§10).

## 7. Trust and permissions

- Tiers: admin and non-admin only. Function create/update/delete/URL-load routes all require `get_admin_user` (`routers/functions.py`). Tool routes accept admin or a user holding `workspace.tools` or `workspace.tools_import`; defaults `USER_PERMISSIONS_WORKSPACE_TOOLS_ACCESS=False`, `..._IMPORT=False` (`config.py` lines 1745–1770). Content edits are gated the same way: "Content edits trigger exec on load — gate them behind workspace.tools (matches /create)" (`routers/tools.py` line 517).
- Vendor's framing: "Granting a user the ability to create Tools is equivalent to giving them shell access to the server" (security policy Rule 10). "Only trusted administrators should have permission to create, import, or modify Tools and Functions" (plugin overview). "Treat plugin authors as administrators" (Under the Hood).
- "Verified": no such tier exists; featured/popular explicitly carries no security meaning (§6).
- Declared permissions: none. The one declaration with effect, `requirements`, is executed, not checked. Valves/UserValves are declared configuration (Pydantic models rendered as a form; admin `Valves`, per-user `UserValves`; "Function Valves are configurable by admins alone ... Tool Valves can also be edited by the tool's owner and by anyone holding a write grant"; Valves page). They are settings, not permissions, and enforce nothing about what code does.
- Enforcement: the runtime enforces who may save code and nothing about its behaviour. Caveat on the kill switch: "`ENABLE_PLUGINS` ... gates the listing endpoints ... The create, update, delete and valves endpoints are not gated, so a plugin can still be written through the API while the execution surface is off. Treat this as a switch for whether plugins run, not as an authorization boundary" (env reference).
- Vendor disposition, CVE-2026-0766 (ZDI-26-032; `load_tool_module_by_id` passes Tool source to `exec()`): "Rejected, not a vulnerability". Timeline: ZDI report 2025-10, closed without written explanation ("a mistake on Open WebUI's part"); CVE published 2026-01-23; disputes filed 2026-05-04, 06-15, 07-02; ZDI agreed 2026-07-08; record set to REJECTED 2026-09-02 "recording the extension system being intended functionality as the reason". Argument: "the submission is the program the user asked to have run"; comparison to Jupyter, n8n Code nodes, Home Assistant scripts; PR:L is wrong because the routes need admin or "the root-equivalent `workspace.tools` permission" (PR:H). Rules cited: 10 (Tools/Functions designed to execute user code; "ANY attack chain that involves Tools or Functions ... closed as not a vulnerability"), 9 (admin "pasting untrusted code into Functions/Tools" out of scope), 1 (must cross a boundary against a party other than the reporter).
- CVE-2026-0765 (ZDI-26-031; `requirements` passed to `pip install` as "command injection"): same disposition and timeline; vendor: "There is no 'injection' into a fixed pipeline ... the server installs those dependencies and executes the code by design."
- All 23 CVEs on the vendor-dispositions index are listed as rejected by their CNAs (index page, 2026-10-06), including CVE-2024-7806 "CSRF to pipeline code execution" and CVE-2024-7037 "path traversal in pipeline upload" (titles from the index; detail pages not read).

## 8. Models

Open WebUI does not manage model files on behalf of plugins. Pipes bring their own provider logic with keys in Valves. Base models come from Ollama / OpenAI-compatible connections; Ollama pulls are proxied (`POST /ollama/api/pull` → `{url}/api/pull`, `routers/ollama.py` lines 652–671). RAG embedding models use `huggingface_hub.snapshot_download(repo_id, cache_dir=SENTENCE_TRANSFORMERS_HOME, local_files_only=...)` with no `revision` argument; auto-update default `True` unless `OFFLINE_MODE`, which also sets `HF_HUB_OFFLINE=1` (`retrieval/utils.py` lines 1720–1760; `config.py` lines 1008–1014; `env.py` line 1206). Default embedding model `sentence-transformers/all-MiniLM-L6-v2`. No sharing of model files between plugins exists because plugins have no model declaration.

## 9. Local, remote or both

Both. Functions/Tools: in-process on the server. Pipelines: separate server, local or remote, reached as an OpenAI-compatible endpoint with an API key. Tool servers (OpenAPI/MCP): local or remote HTTP. Pipes commonly proxy remote providers. Community import works with a `localhost` instance because the handoff is browser-side.

## 10. Known incidents and stated limitations

Incidents (advisories):
- CVE-2025-64495 / GHSA-w7xj-8fx7-wfch (published 2025-11-07, CVSS 8.7, fixed in v0.6.35): stored DOM XSS via prompts when "Insert Prompt as Rich Text" is enabled; when an admin triggers it, the script "can be used to send requests as the admin user that run malicious Python functions", i.e. RCE through the Functions API. A UI bug became code execution because the admin UI can write code.
- GHSA-vpq8-f445-hcq7 (published 2026-09-27, CVSS 8.1, affected 0.7.0–0.11.3, fixed 0.11.4, no CVE): the community "sync stats" modal "registered a `window` message listener that processed `verify:chat` messages from any web origin without checking `event.origin`, and replied to `window.opener` with target origin `*`", plus path traversal in the request URL; "Any website can steal a signed-in user's session token". Precondition: community sharing enabled (`ENABLE_COMMUNITY_SHARING` default `True`). The registry↔app bridge was the hole.
- The ten advisories on the GitHub advisories page (read 2026-10-06; dated 2026-09-09 to 2026-09-28) concern auth, token theft, access control and terminals; none concern Functions/Tools loading, consistent with the policy that those are out of scope.
- CVE-2026-0765 / CVE-2026-0766: rejected (§7).

Incident (press, used only for the incident): comparethecloud.net, 2026-03-18, reporting Cybernews research: 98 OpenWebUI instances with authentication disabled and 2,000+ with open registration; 45 of the 98 compromised; cryptominers plus infostealers; 14 malware versions from one source; running "for over a year"; Discord webhooks for notifications; "The team reached out to OpenWebUI Inc. to disclose the vulnerabilities, but the report was closed without a response." The claim that attackers used the Tools feature for code execution appears in a search-engine snippet of the Cybernews article; the article itself returned 403 to both fetch methods, so that detail is not confirmed. The two "information disclosure" routes the researchers cited (`/api/config`, `/api/version`) are the subject of CVE-2025-63391, which the vendor rejected as "mischaracterized" (public bootstrap data by design; sensitive fields gated by role).

Stated limitations (vendor, Under the Hood "Footguns"): no sandboxing; stream hooks use a stale cache; cross-plugin interference not detected; disabling does not unload; no concurrency control, no exactly-once across replicas; `requirements:` runs pip on every replica; internal APIs unstable; plugin-registered routes are unauthenticated unless the plugin adds auth; Pipelines server "runs out-of-process and does not share `sys.modules` with Open WebUI: it cannot monkey-patch the main app, but it also is not constrained by it."

## 11. Borrow

1. Say it plainly, everywhere code is installed. Reuse the posture, not the words: "execute arbitrary Python on your server", "Listed does not mean vetted", "Highlighting is not vetting", "Treat plugin authors as administrators". The brief's "never hide a security or permission risk" is what Open WebUI does in its docs; the install screen in Clips Kitty should carry the same sentence, and the registry UI should say "not reviewed" next to any curated list.
2. Typed, declared configuration (Valves/UserValves): a Pydantic/JSON-schema block in the manifest that the app renders as a form, with an admin tier and a per-user tier, secrets kept out of source and optionally encrypted at rest, and "sharing publishes source but not valve values". This maps directly onto a Clips Kitty manifest `settings` section.
3. Kill switches that are real: one global "plugins off" switch, a "no network installs" switch (`OFFLINE_MODE`), and automatic `is_active=False` when a load fails. Unlike Open WebUI, make the switch gate the write endpoints too (see Avoid 5).
4. The external tool-server pattern is H1 in production: an HTTP contract (OpenAPI/MCP) where "they only see what you pass over HTTP and can't reach into [the host] itself", recommended by the vendor for "hard isolation", conflicting dependencies and GPU work. Clips Kitty's plugin SDK over the local API is the same shape.
5. A written security policy that states what is intended behaviour and what is out of scope (Rules 1, 9, 10), plus a public vendor-disposition page per disputed CVE. Clips Kitty will get "plugin can run code" reports; decide the scope in writing before the first one.
6. The import flow that keeps the instance private (handoff happens in the user's browser; a `localhost` app works) is the right goal for a local-first app; implement it without a cross-origin `postMessage` bridge (Avoid 6).

## 12. Avoid

1. Executing plugin source inside the engine process. `exec()` of DB-stored code, module top level running on save, monkey-patching the host, "disabling does not unload", non-deterministic cross-plugin interference: every one of these follows from in-process loading. Clips Kitty's engine (FastAPI on 127.0.0.1:8765) must never `exec`/import plugin code; plugins are separate processes speaking the API (H1).
2. Runtime `pip install` of a comma-split string into the shared environment, on save and at startup, default on. The vendor now tells production users to turn its own default off. Clips Kitty's frozen PyInstaller build cannot pip into itself anyway; dependency resolution belongs to the plugin's own environment at install time, with the manifest forbidding runtime installs (H6).
3. Unvalidated metadata: regex-parsed free-text frontmatter, `version` that nothing compares, `required_open_webui_version` checked only in the browser, no update or rollback path. Clips Kitty's manifest must be schema-validated by the app, with compatibility enforced by the engine and installs pinned to a tag/commit.
4. "Install from URL" that accepts any URL and resolves GitHub `blob`/`tree` links to `refs/heads/<branch>` (a moving target), with `/tree/` silently mapped to `main.py`. Pin to a commit or tag, show it, and verify it.
5. A safety flag that is not a boundary: `ENABLE_PLUGINS=false` hides the UI and stops execution but leaves create/update endpoints open (vendor's own note). A permission is only "enforced" if the runtime enforces it; the same goes for switches.
6. A registry↔app bridge built on `window.postMessage` with `'*'` targets and (once) no origin check: GHSA-vpq8-f445-hcq7 (High) came from exactly this, and CVE-2025-64495 shows any XSS becomes RCE when an authenticated admin UI can create code. For Clips Kitty the stakes are higher: the engine has no authentication and only a Host-header check, so any browser-reachable "install this plugin" endpoint would let any web page install plugins. Installs must require a user gesture inside the Electron UI plus an explicit token, never a bare local HTTP call from a web origin.
7. A code-execution feature next to weak default access: the 2026 cryptominer campaign hit instances with auth off or open signup, not a code bug. Clips Kitty's unauthenticated local API plus a plugin manager is the same shape; add a per-install secret or OS-user check before shipping the manager.
8. Hosted registry with accounts, zero review, and a "Highlighted"/"featured" list on the same site: curation without review sends a mixed signal. If Clips Kitty features anything, say what "featured" means (and what it does not) in the same breath, and never use "Verified" without a real process.
9. (Minor, H7) Downloading Hugging Face models with no `revision`: `snapshot_download(repo_id, cache_dir, local_files_only)` floats on the default branch. Pin by commit hash.

## Matrix row

`| Open WebUI | Python Functions (Pipe/Filter/Action/Event) and Tools stored in the DB and exec'd in-process; Pipelines = separate server; OpenAPI/MCP tool servers = external HTTP | Pipes wrap providers themselves; no model-file management for plugins (Ollama pull proxied; HF snapshot_download, unpinned, for RAG embeddings only) | Filters chain per chat (inlet/stream/outlet, priority valve); no DAG | Hosted community site openwebui.com with accounts, no review ("unaudited") | No: source is copied into the instance; optional git_url string; URL import resolves branches, not tags | Yes (in-process) | Yes (Pipelines server, tool servers, Pipes to remote APIs) | Free-text version; no pinning, update or rollback; required_open_webui_version checked only in the UI | None at runtime: exec() with the server's access, pip from frontmatter; only control is admin-only creation ("equivalent to shell access"); vendor: "no sandbox, no allowlist, no capability system" | Free, unaudited, one-click Get → browser handoff → save | Be as blunt as the vendor; gate who may install; never load plugin code in the engine; no runtime pip into the shared env; validate the manifest and pin to a ref; keep install off any browser-reachable unauthenticated endpoint |`

## Hypotheses

- H1 (out-of-process plugins speaking the local API are lowest risk): **Confirmed, with a caveat.** The vendor's own docs contrast in-process plugins ("no sandbox, no allowlist, no capability system", can monkey-patch the host, cannot be unloaded) with external tool servers ("only see what you pass over HTTP and can't reach into Open WebUI itself") and recommend the latter for "hard isolation". Caveat: out-of-process protects the host app and its data, not the machine; the Pipelines server still runs arbitrary code with its own full process rights. OS-level sandboxing is a separate question Open WebUI does not answer.
- H3 (static Git registry): **Not contradicted; Open WebUI is the opposite design.** A hosted site with accounts and a browser bridge, no pinned refs, and the bridge produced a High advisory. Shows what the hosted path costs in attack surface.
- H4 (hand review does not scale): **Consistent.** Open WebUI chose no review at all and says so ("unaudited"); it still curates "Highlighted plugins" with a disclaimer.
- H5 (declared permission is not an enforced one): **Strongly confirmed.** There are no declared permissions at all; the only declaration with effect (`requirements`) is executed, not checked; `required_open_webui_version` is a browser-side warning; `ENABLE_PLUGINS` is explicitly "not an authorization boundary". Extensions run with the app's own access (vendor statement).
- H6 (dependencies; runtime pip is a hazard; shared env invites conflicts): **Confirmed by the vendor's own reversal.** Runtime pip into the shared env is the default and the docs now say "Strongly recommended: set ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS=False in production"; version conflicts "unpredictable"; installs "race across workers and crash".
- H7 (HF revision pinning): **Side evidence.** Open WebUI's `snapshot_download` call passes no `revision`; an example of what to avoid, not of the hypothesis itself. Windows symlink behaviour: not examined here.
- H8 (namespaced identifiers): **Weak support (inferred).** Ids are flat, lower-cased Python identifiers unique per instance with no publisher namespace; cloning appends `_clone`; manifold sub-models get `<function_id>.<id>`. Collisions are avoided by hand, not by design.
- H10 (counts need telemetry; opt-in): **Partly.** The community site has a "Leaderboard" of "messages opted-in users have synced from their instances" (ecosystem page); install counts for plugins: not confirmed.
- H2, H9: not addressed by this platform.

## 13. Sources (all read 2026-10-06)

Official docs and vendor pages:
- https://docs.openwebui.com/features/extensibility/plugin — plugin overview, arbitrary-code warning, admin-only gating, "unaudited".
- https://docs.openwebui.com/features/extensibility/plugin/functions/ — Function types, storage ("stored in the database ... executed server-side"), frontmatter fields, `ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS`.
- https://docs.openwebui.com/features/extensibility/plugin/functions/pipe — Pipe structure, `pipes()` manifold.
- https://docs.openwebui.com/features/extensibility/plugin/tools/ — tool kinds, "equivalent to giving them shell access", external tool servers; raw text also at .../tools.txt.
- https://docs.openwebui.com/features/extensibility/plugin/tools/development — frontmatter field list, Valves, production pip warning, conflict warning.
- https://docs.openwebui.com/features/extensibility/plugin/tools/openapi-servers — OpenAPI tool servers, limitations.
- https://docs.openwebui.com/features/extensibility — "hard isolation ... external service behind an OpenAPI or MCP tool server"; community import.
- https://docs.openwebui.com/features/extensibility/community — "Highlighted does not mean vetted", safety checklist, sharing guidance.
- https://docs.openwebui.com/ecosystem/community — site structure, import/share flows, "Listed does not mean vetted", browser-tab handoff.
- https://docs.openwebui.com/features/extensibility/plugin/development/under-the-hood — loader steps, "no sandbox, no allowlist, no capability system", footguns.
- https://docs.openwebui.com/features/extensibility/plugin/development/valves — Valves/UserValves semantics.
- https://docs.openwebui.com/features/extensibility/pipelines/ — Pipelines concepts, legacy status, install methods.
- https://docs.openwebui.com/reference/env-configuration — `ENABLE_PLUGINS`, `ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS` and hardening note, `ENABLE_VALVE_ENCRYPTION`, `USER_PERMISSIONS_WORKSPACE_TOOLS_*`, `PIP_OPTIONS`, `ENABLE_COMMUNITY_SHARING`, `OFFLINE_MODE`, `SAFE_MODE` (page states it is current to v0.11.1).
- https://docs.openwebui.com/security/security-policy — Rules 1, 9, 10, 13.
- https://docs.openwebui.com/security/vendor-dispositions/ — index of 23 rejected CVEs.
- https://docs.openwebui.com/security/vendor-dispositions/cve-2026-0766 (.txt) — tool `exec()` disposition and timeline.
- https://docs.openwebui.com/security/vendor-dispositions/cve-2026-0765 (.txt) — pip `requirements` disposition.
- https://docs.openwebui.com/security/vendor-dispositions/cve-2025-63391 (.txt) — `/api/config` disposition.
- https://github.com/open-webui/open-webui/security/advisories — ten published GHSAs.
- https://github.com/open-webui/open-webui/security/advisories/GHSA-vpq8-f445-hcq7 — community stats message handler advisory.
- https://github.com/advisories/GHSA-w7xj-8fx7-wfch — CVE-2025-64495.
- https://github.com/open-webui/pipelines — README (raw, main) and `main.py` (raw, main): loader, pip install, `PIPELINES_URLS`.
- https://openwebui.com/ — community home page.

Source code (shallow clone, commit 8bd8b4f, 2026-09-21): `backend/open_webui/utils/plugin.py`, `routers/functions.py`, `routers/tools.py`, `models/functions.py`, `models/tools.py`, `utils/valves.py`, `env.py`, `config.py`, `retrieval/utils.py`, `routers/ollama.py`, `src/lib/constants.ts`, `src/lib/components/admin/Functions.svelte`, `src/lib/components/workspace/Tools.svelte`, `src/routes/(app)/admin/functions/*/+page.svelte`.

Press (incident only):
- https://www.comparethecloud.net/news/open-source-ai-servers-hijacked-with-cryptominers-and-infostealers-for-over-a-year — 2026-03-18, reporting Cybernews.

Not loaded / not confirmed:
- https://cybernews.com/resources/malicious-campaign-targeting-vulnerable-openwebui-servers-technical-analysis/ — HTTP 403/404 on two attempts; the "Tools feature used for RCE" detail comes from a search snippet only: not confirmed.
- https://www.scworld.com/brief/openwebui-servers-targeted-for-extensive-cryptomining — 403.
- https://www.techradar.com/pro/security/multiple-openwebui-ai-servers-infected-with-cryptominers-and-infostealers-stayed-up-for-over-a-year — body truncated, unusable.
- https://docs.openwebui.com/openapi-servers/ — 404 (moved; see the tools/openapi-servers page above).
- https://docs.openwebui.com/getting-started/env-configuration — 404 (moved to /reference/env-configuration).
- Cost of running openwebui.com; plugin install counts on the community site: not confirmed.
