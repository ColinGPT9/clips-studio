# Blender extensions (extensions.blender.org)

- Platform: Blender extensions system (Blender 4.2 LTS and later) and the Blender Extensions Platform at extensions.blender.org
- Tier: 2
- Date read: 2026-10-06
- Track C question: what makes an extension ecosystem successful, and what mistakes should Clips Kitty avoid?

Verdict: Blender shows the cheapest workable registry shape: one manifest file (`blender_manifest.toml`), one generated `index.json` that anyone can host statically, and a client that treats every repository the same, so the official site is just one remote among many. Its permissions are declared and displayed but not enforced (the project says so itself), its moderation is human and first-version-only, and its wheel handling is a shared `site-packages` with "newest wins" — three things Clips Kitty should not copy as-is.

## 1. The unit of extension

An "extension" is a `.zip` archive containing a `blender_manifest.toml` plus the payload. Two types are supported today: `type = "add-on"` (Python package with at least `__init__.py`) and `type = "theme"` (an `.xml` theme file). The 4.2 release notes say extensions are "add-ons, themes, and potentially anything else that can extend Blender's native functionality (e.g. key maps, assets)". The UI code also recognises an `asset-library` type when showing permissions (source, `bl_extension_ui.py`, read 2026-10-06), so the type list is open-ended in the client but the manual still documents only two.
Sources: getting_started.html, release_notes/4.2/extensions, bl_extension_ui.py (all read 2026-10-06).

Add-ons run in-process in Blender's embedded Python with Blender's own privileges; there is no sandbox (see 7).

## 2. Manifest format

File: `blender_manifest.toml` at the root of the archive (an extra top-level folder is tolerated, "a common behavior when saving a repository as ZIP from version-control platforms").

Required fields (manual + schema 1.0.0, read 2026-10-06): `schema_version` ("use 1.0.0"), `id` ("Unique identifier for the extension"), `version` ("must follow semantic versioning"), `name`, `tagline` ("One-line short description, up to 64 characters - cannot end with punctuation"), `maintainer`, `type` ("add-on", "theme"), `blender_version_min` ("use at least 4.2.0"), `license` (list, "SPDX:" prefix, e.g. `["SPDX:GPL-3.0-or-later"]`).

Optional: `blender_version_max` ("Blender version that the extension does not support, earlier versions are supported" — exclusive upper bound; it "can be omitted and defined later on the extensions platform if an issue is found"), `website`, `copyright` (list, "Year Name" or "Year-Year Name"), `tags` (from a fixed list), `platforms` (`["windows-x64", "windows-arm64", "macos-x64", "macos-arm64", "linux-x64"]`; omitted = all), `wheels` (list of relative paths), `[permissions]` table, `[build]` table with `paths` or `paths_exclude_pattern` (gitignore-compatible; default excludes `__pycache__/`, `.git`, `*.zip`). Reserved: `[build.generated]`, written by `build --split-platforms` and "must not be included in source manifest files".

Permissions table, verbatim from the manual's example:

```
# [permissions]
# network = "Need to sync motion-capture data to server"
# files = "Import/export FBX from/to disk"
# clipboard = "Copy and paste bone transforms"
```

Keys: `files`, `network`, `clipboard`, `camera`, `microphone`. "Each permission should be followed by an explanation (short single-sentence, up to 64 characters, with no end punctuation)." The UI code notes "while this is documented to be a dict, old packages may contain a list of strings" (early schema used `permissions = ['network', 'files']`, devtalk 2024-02-15 meeting notes).

Rule: "All the values present in the manifest file must be filled (i.e., cannot be empty, nor text "", nor list [])."

Compatibility fields are `blender_version_min` / `blender_version_max` / `platforms` only; there is no dependency-on-other-extension field, by policy (see 4).

## 3. Distribution and install

- Build: `blender --command extension build` (options `--source-dir`, `--output-dir`, `--output-filepath`, `--valid-tags`, `--split-platforms`, `--verbose`); default output `{id}-{version}.zip`.
- Validate: `blender --command extension validate [SOURCE_PATH]` works on a directory or a zip, checks metadata only (it "does not compare permissions with code" is my inference from the CLI description "Validate the package meta-data"; marked (inferred)).
- Publish to extensions.blender.org: upload the zip (needs a Blender ID), "The extension will be held for review, and published once the moderation team approves it." CI/CD: `POST https://extensions.blender.org/api/v1/extensions/$EXTENSION/versions/upload/` with a bearer token from the user profile page, fields `version_file` and `release_notes`.
- Install paths (Preferences > Get Extensions): search and click Install from a remote repository; drag the website's install URL into Blender; "Install from Disk" (zip drop) — the latter "is installed to a Local Repository and no updates will be available".
- Install is an archive extraction; bundled wheels are extracted into a site-packages directory (see 4). Nothing else runs at install time. Add-on code runs when the add-on is enabled (the Python package is imported and `register()` called — standard add-on behaviour, (inferred) from the add-ons page's "If the Add-on does not activate when enabled, check the Console window").
- Command line also offers `list`, `sync`, `update`, `install`, `install-file -r REPO`, `remove`, `repo-list`, `repo-add --url/--directory/--access-token/--source USER|SYSTEM`, `repo-remove`.
- The listing JSON carries `archive_url`, `archive_size` and `archive_hash` ("sha256:..."); the client validates the `sha256:` prefix and computes a sha256 of the downloaded file and compares to `archive_hash` (`blender_ext.py` lines ~2035-2051 and ~4554, read 2026-10-06).

Sources: getting_started.html, extension_arguments.html, preferences/extensions.html, ci_cd, api_listing/v1, blender_ext.py (all read 2026-10-06).

## 4. Dependencies and isolation

Policy (handbook add-on guidelines and ToS, read 2026-10-06):
- "Add-ons must not install Python modules, PIP packages, Python-wheels etc." at runtime; "Extensions may include 3rd party module as python-wheels."
- "Manipulating Blender's module loading such as changing the module search path or inserting modules directly into the global module dictionary is forbidden".
- ToS 3.6: "Source code of the extension must be fully written in Python. Depending on external, pre-compiled packages is not allowed." ToS 5.1: "Extension must be self-contained and not load remote code for execution." ToS 5.3: "No functionality of the extension should be dependant on other extensions".
- Moderation guideline: "pip dependencies should be bundled as wheels or vendorized"; "Make sure there is no auto-updater on the code".

Mechanism (python_wheels.html and `wheel_manager.py`, read 2026-10-06):
- Wheels go under `./wheels/`, "must be bundled unmodified from Python's package index", "must include their dependencies"; binary wheels need one per supported platform; `build --split-platforms` produces one zip per platform to cut size.
- The manual says "installing the package will extract the wheel into the extensions own site-packages directory". The source is more precise: wheels from all extensions of a repository are extracted into one shared directory, "Typically: `~/.config/blender/4.2/extensions/.local/lib/python3.11/site-packages`", and `wheel_list_deduplicate_as_skip_set()` keeps only the newest version per wheel base name: "`pip-24.0-py3-none-any.whl`, `pip-22.1-py2-none-any.whl` will both extract the base name `pip`, de-duplicating by skipping the wheels with an older version number. This is not fool-proof, because it is possible files inside the `.whl` conflict upon extraction." The author adds that conflicts "probably needs to be handled on a policy level".
- Manual alternatives: bundle another add-on inside yours (with duplicate-registration checks) or "Vendorize" pure-Python dependencies as sub-modules ("This has the advantage of avoiding version conflicts").
- An unmerged 2024 docs PR ("New section: Add-on Dependencies", blender-developer-docs #40, closed, not merged) proposed loading wheels without touching `sys.path`; it is not policy.

So: one shared environment per repository, conflicts resolved by "newest wins" at extraction time, no per-extension isolation.

## 5. Versioning and updates

- `version` must be semver; the platform's own guidelines say semver "in spirit" for add-ons, and recommend "tracks" (1.2.x for old Blender, 1.3.x for new) with correct `blender_version_min/max` per version; "You can also update Blender version compatibility of already-uploaded versions of your add-on from the extensions website."
- Updates: "You need to manually check for available updates" (or enable "Check for Updates on Startup" per remote repository); "The current available version of an extension on the repository will always be considered the latest version." No rollback in the UI; downgrading means installing an older zip from disk (inferred). Listing entries are immutable per version on the official site (download URLs are keyed by sha256, e.g. `/download/sha256:.../add-on-photo-relief-v1.0.1.zip`).
- Upgrades keep the user data directory from `bpy.utils.extension_path_user(__package__, create=True)`; "Writing files into the add-on's directory also has the down side that upgrading the extension will remove all files."
- The UI source has `TODO(@ideasman42): handle permissions on upgrade` — "users need to be aware when upgrading an extension results in it having additional permissions" is recognised but not implemented (read 2026-10-06).
- Deprecation: legacy `bl_info` add-ons are "considered deprecated" since 4.2 but still installable via "Install legacy Add-on"; the platform can set `blender_version_max` on a published version; repositories can blocklist an id (see 7).

Sources: version_number_guidelines.html, preferences/extensions.html, addons.html, bl_extension_ui.py, api JSON.

## 6. Registry design

Official platform: extensions.blender.org, a Django site run by the Blender project (source at projects.blender.org/infrastructure/extensions-website; the about page calls it "The online directory of free and Open Source extensions for Blender" and "The platform only offers GNU GPL compliant software"). Submission: review the Terms of Service, build per the manual, upload the zip with a Blender ID, "Await review approval". The listing is served as JSON at `/api/v1/extensions/` (1,485 entries, `blocklist: []`, read 2026-10-06).

Open spec for third parties (creating_repository, static_repository, api_listing/v1, read 2026-10-06): three tiers "in order of complexity":
1. Static: "Host a static JSON file listing all the packages of your repository." `blender --command extension server-generate --repo-dir=/path/to/packages` "creates an `index.json` listing from all the .zip extensions found"; `--html` also emits an `index.html` with drag-and-drop install links; `--repo-config blender_repo.toml` can declare `[[blocklist]]` entries (`id`, `reason`). Point Blender at it with Add Remote Repository and a URL (`file:///...` works for local testing). JSON shape: `{"version": "v1", "blocklist": [...], "data": [ {manifest fields + archive_size, archive_hash, archive_url} ]}`.
2. Dynamic: "Serve the JSON file on-demand based on the Blender version and platform."
3. Platform: "Fork the entire Extensions Website to create your own."

Remote repositories support an optional "Access Token" per repository (private registries). System repositories (read-only, bundled, for offline or managed deployments) are documented in the 4.2 release notes.

Review: a human moderation team plus volunteers ("Help testing and reviewing the Awaiting Review extensions (ideally in a safe virtual environment)"; "#extension-moderators" chat). The approval queue is public: 2,136 entries across all statuses on 2026-10-06 (43 pages); in the first 300 rows I counted 25 "Awaiting Review" and 275 "Awaiting Changes", i.e. most open items wait on the author, not the moderator (tally covers six pages only). The 2024-02-15 project notes planned "Only the first version will be scrutinized/reviewed" and a workflow "[Approve | Request Changes | Reject] + a compulsory 'Comment' field" with a separate Publish step; the current moderation guidelines do not state whether later versions are reviewed (not confirmed). The moderation checklist is mostly manual: run it, check manifest permissions ("files is expected for most I/O add-ons"), grep for `requests`/`urllib`/`http`, check `__file__`, `sys.path`, `open`, `bpy.app.online_access`, run `ruff`.

Cost to run: the static tier costs static hosting only; the official platform is a hosted web app with a moderation team (no numbers published; not confirmed).

## 7. Trust and permissions

- Declared permissions are informational. Official statements: Blender blog, 15 May 2024: permissions are "not a security-oriented feature, rather a way for developers to be more transparent about their intentions." Manual (addons.html): "Add-ons that follow this setting will only connect to the internet if enabled. However, Blender cannot prevent third-party add-ons from violating this rule." Devtalk 2024-02-15: "We want to give users some clues as to what the extension may be doing."
- What the runtime does: shows the declared keys in the extension details ("Permissions" / "No permissions specified", `bl_extension_ui.py`); `_bpy_internal/extensions/permissions.py` is only a list of the five keys "to present the permissions to be picked up by translation" (read 2026-10-06). The only runtime check is Blender's own: the extension manager refuses to contact non-`file://` remotes when `bpy.app.online_access` is off (`bl_extension_ops.py` ~1752-1767), and add-ons are expected to honour the same flag voluntarily.
- Policy backstops (ToS, last updated 10 August 2026): "No surprises" description rule (3.1); "Extension code must be reviewable: no obfuscated code or byte code is allowed" (3.5); must not "temper with operating system, third-party softwares, other extensions, Blender's internal modules, or sensitive user data" (3.9); must request the `network` permission and "clearly state the reason" (4.1); "must not send data to any remote locations without authorization from the user" (4.5); no remote code (5.1); no advertising in the UI (6.1).
- Trust tiers: none beyond "listed on extensions.blender.org (reviewed, GPL)" versus "other repository". There is no "verified publisher" badge (not confirmed; none seen on the about, ToS or API pages).
- Blocklist: implemented July 2024 (issue #124954, closed 2024-07-26). Motivation: "We may discover an extension which is malicious or intentionally violates policies in ways which moderators consider justification for blocking." Behaviour: blocked ids stay listed with an error icon and cannot be installed from that repository; "Blender would not disable or remove the extension" if already installed, but shows a "Blocked" panel and a warning when an installed extension becomes blocked; the user "may download and install the extension into their own user repository". The static spec carries the same `blocklist` with `reason`.

## 8. Models

Not applicable: Blender has no model manager; an add-on that needs ML weights bundles them in the zip (ToS 5.2 forbids requiring "external functional components ... that need to be downloaded from elsewhere", and 3.10 forbids gating on external keys), or downloads them itself only with `network` permission and `bpy.app.online_access`. Nothing in the manifest describes models (read 2026-10-06; manual, ToS).

## 9. Local, remote or both

Local, in-process. Network use is allowed but must be declared, gated on the user's "Allow Online Access" preference, and (ToS 4.3) must not depend on paid or registration-walled services: extensions may connect to sites requiring a login "provided the offering on that website is complete and aligned with Blenders mission. If the offering is a mixed product (for example with optional commercial extras), it will not be accepted." Moderation guideline: "It should not depend on external servers (localhost is fine)."

## 10. Known incidents and stated limitations

- No malicious-extension takedown on extensions.blender.org is documented on the pages I read; the live `blocklist` was empty on 2026-10-06. Not confirmed either way.
- The blocklist feature was built pre-emptively (issue #124954, 2024).
- Nov 2025 press (The Hacker News, BleepingComputer, Kaspersky; seen in search results only, pages not opened) reports StealC malware in `.blend` files on third-party model sites via Blender's auto-run Python; that concerns `.blend` files and "Auto Run Python Scripts", not the extensions platform. Not confirmed here.
- Stated limitations: permissions are not enforced (7); wheel conflicts are resolved by newest-wins and "not considered a problem to 'solve' at the moment" (4); updates are not automatic and there is no rollback (5); permission changes on upgrade are a TODO (5); "Install from Disk" installs get no updates (3).
- The platform's ToS is strict enough to exclude many commercial or SaaS-backed add-ons (3.10, 4.3, 6.x); those are distributed elsewhere (Gumroad, Superhive/Blender Market) as plain zips — that is why third-party remote repositories exist (inferred).

## 11. Borrow

1. The repository contract: one manifest per extension, one generated `index.json` with `{version, blocklist, data[]}`, every entry carrying `archive_url` + `archive_hash` (sha256) + compatibility fields, and a client that treats the official index as just one remote. Clips Kitty's H3 registry can emit exactly this shape from CI and let anyone self-host a second index (including private ones with an access token).
2. `server-generate`-style tooling in the CLI: build, validate, generate index, all offline, so a developer can test a repository with a `file://` URL before publishing.
3. Permission reason strings: each permission key carries a human sentence shown in the install UI. Keep the keys, add the reasons, and show them before install. But say "declared" in the UI, as Blender's docs do, unless the runtime enforces it.
4. `blocklist` with a `reason`, honoured by the client: cannot install from that repository, loud warning if already installed, user may still override from a local repository. Cheap and already specified.
5. Compatibility as a half-open range (`blender_version_min` required, `blender_version_max` exclusive and editable after publication) plus per-platform builds (`platforms` + `--split-platforms`).
6. ToS rules that transfer directly: "No surprises" descriptions; no obfuscated code or bytecode; no remote code loading; no runtime pip installs; no auto-updaters inside the plugin; must respect the host's offline switch; do not write into the plugin's own directory, use a host-provided per-plugin user directory that survives upgrades and is deleted on uninstall.
7. A public approval queue with status badges (Awaiting Review / Awaiting Changes / Approved / Declined) and comments: transparency about where a submission is stuck costs little.
8. Namespacing by repository: Blender imports add-ons as `bl_ext.{repository_module_name}.{id}` so two repositories can ship the same id. Clips Kitty's `publisher/pipeline` ids serve the same purpose.

## 12. Avoid

1. Do not ship a permissions list that looks like a sandbox. Blender had to write "not a security-oriented feature" into its blog and manual; the brief's rule ("a permission is only called 'enforced' if the runtime enforces it") is the fix.
2. Do not share one `site-packages` across plugins with newest-version-wins; Blender's own source calls it "not fool-proof". Either one environment per plugin (disk cost) or a documented policy with a conflict report at install time.
3. Do not rely on first-version-only human review as the trust story; Blender's queue shows review scales only because volunteers test in VMs and most items wait on authors. Pair review with automated checks (manifest validation, static grep for network/exec/obfuscation, hash pinning) and a blocklist.
4. Do not make "latest on the repository" the only update target with no rollback and no warning when permissions grow on upgrade (Blender's open TODO).
5. Do not couple the registry to a licence or business rule so tight that legitimate plugins must live elsewhere (GPL-only, no SaaS, no commercial extras). Clips Kitty's registry is free to publish and never processes payments, but should allow plugins that call paid APIs on the user's own account, as the brief already decides.
6. Do not bundle models or weights in the plugin archive (Blender's self-contained rule forces this); use the shared model manager instead.

## 13. Sources (all read 2026-10-06)

- https://docs.blender.org/manual/en/latest/advanced/extensions/index.html
- https://docs.blender.org/manual/en/latest/advanced/extensions/getting_started.html (manifest example, build/validate, publish steps)
- https://docs.blender.org/manual/en/latest/advanced/extensions/creating_extensions.html — HTTP 404, not confirmed; the content is at getting_started.html
- https://docs.blender.org/manual/en/latest/advanced/extensions/addons.html (internet access, bundle dependencies, local storage, legacy conversion, namespace)
- https://docs.blender.org/manual/en/latest/advanced/extensions/python_wheels.html
- https://docs.blender.org/manual/en/latest/advanced/extensions/version_number_guidelines.html
- https://docs.blender.org/manual/en/latest/advanced/extensions/creating_repository/index.html
- https://docs.blender.org/manual/en/latest/advanced/extensions/creating_repository/static_repository.html
- https://docs.blender.org/manual/en/latest/advanced/command_line/extension_arguments.html
- https://docs.blender.org/manual/en/latest/editors/preferences/extensions.html
- https://developer.blender.org/docs/handbook/extensions/ (index; sub-pages addon_guidelines, hosted)
- https://developer.blender.org/docs/handbook/extensions/addon_guidelines/
- https://developer.blender.org/docs/handbook/extensions/hosted/
- https://developer.blender.org/docs/features/extensions/
- https://developer.blender.org/docs/features/extensions/schema/1.0.0/
- https://developer.blender.org/docs/features/extensions/api_listing/v1/
- https://developer.blender.org/docs/features/extensions/ci_cd/
- https://developer.blender.org/docs/features/extensions/moderation/
- https://developer.blender.org/docs/features/extensions/moderation/guidelines/
- https://developer.blender.org/docs/release_notes/4.2/extensions/
- https://extensions.blender.org/about/
- https://extensions.blender.org/terms-of-service/ ("Last updated: 10 August 2026")
- https://extensions.blender.org/approval-queue/ (pages 1-6)
- https://extensions.blender.org/api/v1/extensions/ (JSON; 1,485 entries; blocklist empty)
- https://code.blender.org/2024/05/extensions-platform-beta-release/ (Blender blog, 15 May 2024)
- https://devtalk.blender.org/t/2024-02-15-extensions-project/33404 (project meeting notes, 2024-02-15)
- https://projects.blender.org/blender/blender/issues/124954 (web returned 403; read via https://projects.blender.org/api/v1/repos/blender/blender/issues/124954; created 2024-07-18, closed 2024-07-26)
- https://projects.blender.org/blender/blender-developer-docs/pulls/40 (via API; closed, not merged, 2024-03-08)
- Blender source, `main` branch, raw files from projects.blender.org/blender/blender: `scripts/addons_core/bl_pkg/bl_extension_ui.py`, `scripts/addons_core/bl_pkg/bl_extension_ops.py`, `scripts/addons_core/bl_pkg/cli/blender_ext.py`, `scripts/modules/_bpy_internal/extensions/wheel_manager.py`, `scripts/modules/_bpy_internal/extensions/permissions.py`, `scripts/templates_toml/blender_manifest.toml` (read only, nothing executed)
- Press, seen only as search-result snippets, not opened, not confirmed: The Hacker News / BleepingComputer / Kaspersky on StealC in `.blend` files (Nov 2025)

## Matrix row

`| Blender extensions | Python add-on or theme in a .zip with blender_manifest.toml; runs in-process | None (weights must be bundled; no model manager) | None (add-ons register operators; no pipeline unit) | Central site extensions.blender.org plus an open static index.json spec anyone can host; client treats all remotes alike | No (zip upload or token API; index generated from zips, not from PRs) | Yes | Network only if declared and bpy.app.online_access is on; no paid/SaaS-gated services on the official site | semver required; blender_version_min/max; "latest on repo wins"; no rollback; no upgrade-permission diff (TODO) | Permissions declared and shown, not enforced ("not a security-oriented feature"); GPL-only; human review; ToS bans obfuscation, remote code, pip; repo blocklist with reason | Free only; 1,485 listed; queue 2,136 entries | Static index + CLI generate/validate; permission reason strings shown as "declared"; blocklist; half-open version range; "no surprises" ToS |`

## Hypotheses

- H1 (out-of-process plugins are lowest risk): Blender is the counter-example — add-ons are in-process Python with full access, which is why the project disclaims permissions and relies on ToS and review. Supports H1 by contrast (inferred).
- H3 (static Git registry, no backend): Supported in part. Blender proves a static `index.json` (+ optional `index.html`) is enough for discovery, install, hashes, update checks and blocklists, and the client already supports many remotes with access tokens. Difference: Blender generates the index from zips on disk (`server-generate`), not from PR-submitted metadata pointing at developer repos; Clips Kitty's CI would generate the same shape from manifests plus pinned refs. Costs static hosting only.
- H4 (hand review does not scale): Partly supported. Review is human and first-version-focused (2024 notes); the queue had 2,136 entries and relies on volunteers; the moderation checklist is grep-and-run. But the first-300-rows tally (25 awaiting review vs 275 awaiting changes) suggests the backlog is authors, not moderators (inferred, partial sample).
- H5 (declared is not enforced): Confirmed with official text: "not a security-oriented feature" (Blender blog 2024-05-15); "Blender cannot prevent third-party add-ons from violating this rule" (manual). The only enforced switch is the host's own offline mode, applied to Blender's extension manager itself.
- H6 (dependency handling): Supported. Policy bans runtime pip, obfuscation, bytecode, remote code; mechanism bundles wheels into one shared site-packages per repository with newest-wins de-duplication that the source calls "not fool-proof". One shared env does invite conflicts; Blender accepts the risk by policy.
- H8 (namespaced ids, several implementations coexist): Supported for namespacing (`bl_ext.{repo}.{id}`); no capability typing exists, and the ToS forbids extension-on-extension dependencies, so coexistence is by isolation, not by contract.
- H10 (counts need telemetry): The listing API carries no install or download counts (read 2026-10-06); whether the website shows them was not checked — not confirmed.
