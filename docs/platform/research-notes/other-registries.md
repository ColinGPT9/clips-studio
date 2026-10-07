# Other registries — Node-RED, AUTOMATIC1111 extensions index, Jellyfin, Raycast, OBS

- Platforms: Node-RED Flow Library (Tier 3), AUTOMATIC1111 `stable-diffusion-webui-extensions` index (Tier 2 for the index design), Jellyfin plugin repositories (Tier 3), Raycast extensions store (Tier 3), OBS Studio plugins (Tier 3)
- Track C
- Date read: 2026-10-06
- Brief's question: what makes an extension ecosystem successful, and what mistakes should Clips Kitty avoid?
- Verdict (two lines): the cheapest registries in the set are static JSON built by CI (A1111: one JSON file per extension, PR-merged, `index.json` assembled and GitHub stars refreshed daily by Actions; Node-RED: a 2.5 MB `catalogue.json` derived from npm) and they cost nothing to run; what they lack is any pin (A1111 installs the default branch and runs the extension's `install.py`), any compatibility field, or any enforcement. Jellyfin shows the minimal repository format worth copying (a manifest URL with per-version zips, checksums and `targetAbi`); Raycast shows the review-everything monorepo that is transparent but human-gated; OBS shows a native-code ecosystem with a forum list and no registry at all.

Every claim carries a URL and the read date 2026-10-06 unless stated. Sections are compact; the A1111 section is fuller because the brief rates its index design Tier 2.

---

# A. AUTOMATIC1111 Stable Diffusion web UI extensions index (Tier 2 for index design)

Verdict: the simplest working "one JSON file per extension, PR, CI builds `index.json`" registry, consumed directly by the application; zero backend; no versions, no pins, no checks beyond JSON validity, and installs that clone the default branch and execute `install.py`.

## 1. Unit
A web UI "extension": a Git repository cloned into `extensions/<name>/` (scripts, tabs, localisations, models tooling). https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Extensions (2026-10-06; the page says it "is not actively maintained"). Index size: 353 files in `extensions/` of the index repo (shallow clone at commit 956826e, dated 2026-05-25; 2026-10-06).

## 2. Manifest
No manifest in the extension repository. The index entry is one JSON file per extension (`extension_template.json`, clone 2026-10-06):

```json
{ "name": "Extension_Name", "url": "https://github.com/OWNER/REPO.git", "description": "Extension_Description", "tags": ["script", "localization", "tab", "..."] }
```

`added` ("YYYY-MM-DD") is filled in by the build script after merge. Tags are a closed vocabulary in `tags.json` (17 tags, e.g. `script`, `tab`, `training`, `models`, `prompting`, `animation`, `online`, `ads`, `installed`); "`online` tag is Required for any extension that connections to external server during regular use aside from one time downloading of assets", "`ads` tag is Required for any extension that contains advertisements" (README). No version, no compatibility, no author field.

## 3. Distribution and install
`index.json` on the `master` branch is fetched by the web UI's Extensions → Available tab (README; `--extensions-index-url` not re-verified). Install (`modules/ui_extensions.py`, raw 2026-10-06): `git.Repo.clone_from(url, tmpdir, filter=['blob:none'])` of the default branch (or a named branch), submodules updated, moved into `extensions/<dirname>`, then `launch.run_extension_installer(target_dir)`, which runs the extension's `install.py` with the web UI's Python interpreter (`modules/launch_utils.py` lines 228–241, raw 2026-10-06). `run_extensions_installers` runs every extension's `install.py` again at each startup. Updates: `check_updates` does a dry-run fetch and `fetch_and_reset_hard` does `git fetch --all; git reset --hard origin/<branch>` (`modules/extensions.py`, raw 2026-10-06). The wiki: "Allowing the installation of extensions poses a significant security risk, as it is equivalent to permitting Arbitrary code execution"; with `--share`/`--listen` the tab is disabled unless `--enable-insecure-extension-access` ("It is highly recommended NOT to use ... during regular use").

## 4. Dependencies and isolation
In-process Python in the web UI's one environment; `install.py` typically pip-installs (the launcher provides `run_pip`); no isolation and no conflict handling (inferred from the code read).

## 5. Versioning and updates
No versions: the UI shows the commit hash (`self.version = self.commit_hash[:8]`) and branch. No pinning in the index; the web UI records a "config state" (web UI and each extension's branch/commit) and can restore one (`restore_config_state_file`, `modules/shared_options.py`, raw 2026-10-06) — a form of rollback to recorded commits. Safety switch: option `disable_all_extensions` = `none`/`extra`/`all` ("preserves the list of disabled extensions") and flags `--disable-all-extensions`, `--disable-extra-extensions` (`modules/cmd_args.py`, raw 2026-10-06).

## 6. Registry design
Repository `AUTOMATIC1111/stable-diffusion-webui-extensions` (clone 2026-10-06): PRs target the `extensions` branch; `validate_entries.yml` runs `validate.py` (required keys `name`, `url`, `description`, `tags`; tags in vocabulary; URL regex; no duplicate URLs); `wrong_file.yml` fails any PR touching files outside `extensions/*.json`; after merge `build_index.yml` runs `build_index.py`, which assembles `index.json`, carries over fields from the deployed index, and force-pushes (amend) to `master`; `update_metadata.yml` runs daily (cron `25 5 * * *`) and adds `full_name`, `github_description`, `stars`, `default_branch`, `created_at`, `default_branch_commit_sha`, `commit_time` per repository from the GitHub API with the Actions token (`update_metadata.py`). Review: "An extension will need to be functioning for it to be included", "Not all extensions will be accepted, we will review the extension and make an assessment", "You can submit extensions even if you are not the author", removal "If extension is no longer functional and or not maintained, we might redirect it to a fork or remove it from the index" (README). Removed entries move to `removed/` (4 files; last commit "remove unavailable extensions"). Cost: GitHub Actions and the GitHub API only.

## 7. Trust and permissions
None. The only declared behaviour is the `online`/`ads` tag; nothing is enforced. The warning lives in the wiki and in the `--enable-insecure-extension-access` flag name.

## 8. Models
Extensions download models themselves (e.g. ControlNet README notes "additional installation steps required"); not managed by the index.

## 9. Local/remote
Local; `online` tag marks extensions that call external services.

## 10. Incidents and limitations
Press (search, 2026-10-06): 404 Media reported in June 2024 a credential-stealing extension targeting Stable Diffusion users (the NullBulge case); the primary artefact named in the results is the ComfyUI node `ComfyUI_LLMVISION`, so no incident tied to this index is confirmed. Stated limitation: the wiki itself is unmaintained; the index has no versions.

## 11. Borrow
One small JSON file per entry, validated by CI (required keys, closed tag vocabulary, URL shape, duplicates), a bot that rejects PRs touching other files, an auto-filled `added` date, and a daily job that refreshes stars/last-commit from GitHub into the published index (this is exactly H10's "stars and last-updated at build time"). The `disable all extensions` switch as a recovery mode.

## 12. Avoid
Installing a moving default branch with no pin; running an extension's `install.py` at install and every startup; no compatibility or version fields; a closed tag list without a `permissions`/`network` field beyond `online`.

---

# B. Node-RED (Tier 3)

Verdict: npm is the package registry and the catalogue is a static JSON; the Flow Library adds a non-blocking scorecard. Cheapest "registry" in the set, but in-process and un-gated.

## 1. Unit
A node module: an npm package whose `package.json` has a `node-red` section mapping node names to `.js` files. https://nodered.org/docs/creating-nodes/packaging (raw source `docs/creating-nodes/packaging.md` in `node-red/node-red.github.io`, 2026-10-06).

## 2. Manifest
`package.json`: `"keywords": ["node-red"]` ("To help make the nodes discoverable within the npm repository"), `"node-red": { "version": ">=2.0.0", "nodes": { "sample": "sample/sample.js" } }` ("You should specify what versions of Node-RED your nodes support with a `version` entry"). Naming: "Packages should use a scoped name - such as `@myScope/node-red-sample`" for modules first published after 2022-01-31; `examples/` must be at the package root; README must describe the node. Same page.

## 3. Distribution and install
`npm publish`; the editor's Palette Manager installs modules into the user directory (default `$HOME/.node-red`) and lists name, version, node types; "if a node is currently in use within the flow, it cannot be removed or disabled". https://nodered.org/docs/user-guide/editor/palette/manager (2026-10-06). Catalogue source: `editorTheme.palette.catalogues: ['https://catalogue.nodered.org/catalogue.json']`; admins can set `externalModules.palette.allowInstall`, `allowList`, `denyList`. https://nodered.org/docs/user-guide/runtime/configuration (2026-10-06). The catalogue is a static JSON: 2,563,409 bytes, `updated_at` 2026-10-06T16:13Z, 6,282 modules, each `{id, version, description, updated_at, types, keywords, url, downloads: {week}}` (fetched 2026-10-06).

## 4. Dependencies and isolation
In-process Node.js; each module's npm dependencies are resolved by npm (nested per package). No sandbox (inferred; none documented in the pages read).

## 5. Versioning
npm semver, immutable on npm; the palette shows "when it was last updated" and offers upgrade; downgrade not documented in the pages read — not confirmed. Compatibility via `node-red.version` range.

## 6. Registry design
"As of April 2020, the Node-RED Flow Library ... a submission request has to be" made via the form at https://flows.nodered.org/add/node (packaging page); maintainers refresh with a "request refresh" link. Scorecard (https://nodered.org/blog/2022/01/31/introducing-scorecard, 2026-10-06): shown as "View scorecard" on a node's page; the Foundation "chose not to act as gatekeepers" and "the scorecard doesn't change that"; "It doesn't currently examine the code of a node". Checks seen on a live scorecard (https://flows.nodered.org/node/node-red-contrib-awtrix-epl-live/scorecard, 2026-10-06): Bugs URL supplied; Package name follows guidelines; Node-RED keyword set; Supported Node-RED Version; Node.js Version; Package uses a unique name; Nodes have unique names; Nodes have examples; Number of Dependencies; Check for Incompatible packages; Dependencies use latest versions. Cost: the Flow Library site plus the catalogue generator (OpenJS Foundation project; figures not published — not confirmed). https://flows.nodered.org/about returned 404 (2026-10-06).

## 7. Trust and permissions
None declared, none enforced; scorecard is advisory; admin allow/deny lists are the only control.

## 8. Models
Not applicable.

## 9. Local/remote
Local runtime; nodes may call any network.

## 10. Incidents and limitations
None found in the pages read. Limitation stated by the project: scorecard does not inspect code.

## 11. Borrow
A static catalogue JSON regenerated on a schedule from the package source (npm here; GitHub for Clips Kitty) with weekly download counts from the package registry (H10: counts without own telemetry when a package registry exists); a scorecard of mechanical checks shown next to each entry (unique name, compatibility range present, examples present, dependency count/freshness, bug URL present) that informs without blocking; a host-side allow/deny list.

## 12. Avoid
In-process execution; "not gatekeepers" as the whole trust story when the runtime cannot enforce anything.

---

# C. Jellyfin plugin repositories (Tier 3)

- Unit: a .NET class library (`net9.0`) inheriting `BasePlugin<PluginConfiguration>` with a `Guid` Id and Name, loaded by the server from the `plugins` directory (Windows `%UserProfile%\AppData\Local\jellyfin\plugins` or `%ProgramData%\Jellyfin\Server\plugins`). https://github.com/jellyfin/jellyfin-plugin-template README (raw, 2026-10-06) and https://jellyfin.org/docs/general/server/plugins/ (2026-10-06).
- Manifest: `build.yaml` in the template — `name`, `guid`, `version` ("1.0.0.0"), `targetAbi` ("10.11.0.0"), `framework` ("net9.0"), `overview`, `description`, `category`, `owner`, `artifacts` (DLL list), `changelog` (raw, 2026-10-06). "Ensure the package reference version matches the install version of jellyfin server, otherwise the plugin will show as NotSupported."
- Registry: a "repository" is a URL to a `manifest.json`; official `https://repo.jellyfin.org/files/plugin/manifest.json` (36 plugins on 2026-10-06), unstable at `/plugin-unstable/manifest.json`; third-party repositories are listed in the docs with "Official" vs "Third Party" labels and added by URL in the dashboard. Manifest shape (fetched 2026-10-06): array of `{guid, name, description, overview, owner, category, versions: [{version, changelog, targetAbi, sourceUrl, checksum, timestamp}]}`; `sourceUrl` is a zip per version (e.g. `bookshelf_13.0.0.0.zip`), `checksum` is a 32-hex digest (MD5-length; algorithm not stated in the manifest — not confirmed). Updates: install from the catalog and restart ("After that start Jellyfin back up, and reinstall each plugin").
- Trust: in-process .NET; the docs pages read contain no warning text about third-party repositories (checked with a targeted fetch, 2026-10-06); compatibility is `targetAbi` per version.
- Cost: static files on a web host (the official repo is `repo.jellyfin.org/files/...`); anyone can host a manifest. Review of third-party repositories: none (they are just URLs) — inferred.
- Lesson: the minimal, self-hostable repository format — one JSON manifest, per-version archive URL + checksum + target ABI + timestamp — is a good shape for Clips Kitty's index entries and for "not in default" repositories added by URL; the missing parts are signing (checksum only proves transfer integrity) and any isolation.

---

# D. Raycast extensions (Tier 3)

- Unit: a TypeScript/React extension living in the `raycast/extensions` monorepo ("This repository contains all extensions that are available in the Raycast Store", README raw, 2026-10-06), with `package.json` as manifest: `name`, `title`, `description`, `icon` (512×512 PNG, not the default icon), `author` (Raycast username), `categories` (at least one from a fixed list), `commands`, `license` ("Set `license` to `MIT`"), `platforms`, `package-lock.json`, `CHANGELOG.md` with `{PR_MERGE_DATE}`. https://developers.raycast.com/basics/prepare-an-extension-for-store (2026-10-06).
- Publishing: `npm run publish` authenticates with GitHub and "automatically opens a pull request" to the monorepo; "the Raycast team reviews your extension and may request modifications"; on merge "your extension will be automatically published to the Raycast Store". https://developers.raycast.com/basics/publish-an-extension (2026-10-06). Review: "members from Raycast and the community collaboratively review extensions"; CI checks the manifest schema, build and type errors; "The current source code can be inspected at all times." https://developers.raycast.com/information/security (2026-10-06). Rejections listed: default icon, Keychain access requests, custom navigation stacks, external analytics, "proprietary binaries lacking source transparency or integrity verification" (prepare page).
- Execution: a managed Node child process; each extension "in its own v8 isolate (worker thread)" with its own heap; "not further sandboxed" for file I/O, networking or Node features; macOS permission prompts for protected folders; password preferences and local storage are encrypted and per-extension; the Node binary is verified (security page, 2026-10-06).
- Cost: the monorepo, CI and reviewer time plus the store backend (figures not published — not confirmed).
- Lesson: mandatory open source in one monorepo plus review gives transparency and a consistent store, but every update is a human-reviewed PR and isolation (isolates) is not permission (no sandbox); exactly H4 and H5.

---

# E. OBS Studio plugins (Tier 3)

- Unit: a native shared library exporting `obs_module_load()`/`obs_module_unload()` and declared with `OBS_DECLARE_MODULE()`; plugins register sources, outputs, encoders and services. https://docs.obsproject.com/plugins (2026-10-06).
- Manifest: none at runtime beyond `obs_module_name()`/`obs_module_description()`; the template's `buildspec.json` carries `name`, `displayName`, `version`, `author`, `website`, `email`, `platformConfig.macos.bundleId`, and `dependencies` pinning `obs-studio` (31.1.1), prebuilt deps and Qt6 with per-platform SHA-256 hashes (raw, 2026-10-06). The template provides CMake, GitHub Actions for Windows/macOS/Linux (`push`, `pr-pull`, `dispatch`, `build-project`, `check-format`); pushing a semver tag "will create a draft release ... with generated installer packages"; macOS signing and notarization guidance. https://github.com/obsproject/obs-plugintemplate (2026-10-06).
- Registry: the forum Resources list — "OBS Studio Plugins (331 items)", Themes 53, Tools 468, Scripts 384; each shows creator, version, update date, star rating, downloads; "1,418" resources and "46,912,030" downloads overall. No submission rules, review or verification text was visible on the listing page (2026-10-06) — not confirmed. https://obsproject.com/forum/resources/.
- Trust: native code in the OBS process; nothing declared or enforced; installers from GitHub releases.
- Cost: a forum category (XenForo resource manager, inferred from the layout) — effectively zero marginal cost.
- Lesson: a forum list with ratings and download counts is discovery, not a registry: no manifest, no compatibility field, no pin, no checks. The useful part is the build template (CI produces signed installers per platform from a tag) — Clips Kitty's plugin template should do the same for Windows.

---

## 13. Sources, with dates

- https://github.com/AUTOMATIC1111/stable-diffusion-webui-extensions — 2026-10-06 (shallow clone at commit 956826e dated 2026-05-25: README, `extension_template.json`, `tags.json`, `index.json`, `extensions/sd-webui-controlnet.json`, `removed/`, `.github/workflows/{build_index,update_metadata,validate_entries,wrong_file}.yml`, `.github/scripts/{build_index,update_metadata,validate}.py`). The raw README URL on `master` returned 404 (the README lives on the `extensions` branch).
- https://raw.githubusercontent.com/AUTOMATIC1111/stable-diffusion-webui/master/modules/ui_extensions.py, `modules/extensions.py`, `modules/launch_utils.py`, `modules/cmd_args.py`, `modules/shared_options.py` — 2026-10-06.
- https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Extensions — 2026-10-06.
- Web search "stable-diffusion-webui malicious extension incident" — 2026-10-06 (press: 404 Media; results point to a ComfyUI node, not this index).
- https://nodered.org/docs/creating-nodes/packaging — 2026-10-06 (rendered page and raw source https://raw.githubusercontent.com/node-red/node-red.github.io/master/docs/creating-nodes/packaging.md).
- https://nodered.org/blog/2022/01/31/introducing-scorecard — 2026-10-06.
- https://nodered.org/docs/user-guide/editor/palette/manager — 2026-10-06.
- https://nodered.org/docs/user-guide/runtime/configuration — 2026-10-06.
- https://flows.nodered.org/add/node — 2026-10-06.
- https://flows.nodered.org/node/node-red-contrib-awtrix-epl-live/scorecard — 2026-10-06.
- https://catalogue.nodered.org/catalogue.json — 2026-10-06 (live JSON).
- https://flows.nodered.org/about — 2026-10-06, HTTP 404 (not confirmed).
- https://raw.githubusercontent.com/node-red/node-red-dev/main/README.md — 2026-10-06, HTTP 404 (not confirmed).
- https://jellyfin.org/docs/general/server/plugins/ — 2026-10-06 (two fetches).
- https://repo.jellyfin.org/files/plugin/manifest.json — 2026-10-06 (live JSON).
- https://raw.githubusercontent.com/jellyfin/jellyfin-plugin-template/master/README.md and `build.yaml` — 2026-10-06.
- https://developers.raycast.com/basics/publish-an-extension — 2026-10-06.
- https://developers.raycast.com/basics/prepare-an-extension-for-store — 2026-10-06.
- https://developers.raycast.com/information/security — 2026-10-06.
- https://raw.githubusercontent.com/raycast/extensions/main/README.md — 2026-10-06.
- https://github.com/obsproject/obs-plugintemplate (README) and https://raw.githubusercontent.com/obsproject/obs-plugintemplate/master/buildspec.json — 2026-10-06.
- https://docs.obsproject.com/plugins — 2026-10-06 (first 100k characters).
- https://obsproject.com/forum/resources/ — 2026-10-06.

## Matrix rows

`| AUTOMATIC1111 extensions index | Git repo cloned into extensions/ (Python scripts, tabs); index entry is one JSON file (name, url, description, tags, added) | Extensions fetch their own models; none in index | None | Git repo: JSON per extension by PR, validate.py in CI, index.json built and force-pushed to master, daily GitHub-stars refresh; consumed directly by the app | Yes (clone default branch) | Yes (in-process Python; install.py executed) | online tag only | None: commit hash shown; git reset --hard to update; config-state snapshots restore recorded commits | None; wiki warns "Arbitrary code execution"; disable-all switch; tab blocked under --share/--listen | Extensions tab in app; free | The cheapest working static index (JSON per entry + CI + daily metadata refresh); avoid unpinned branch installs and install-time scripts |`

`| Node-RED | npm package with "node-red" section (nodes map, version range) | None | Flows (JSON) are the product; nodes are the extension | flows.nodered.org (manual submission, scorecard) + static catalogue.json (6,282 modules, weekly downloads) read by the Palette Manager | No (npm) | Yes (in-process Node.js) | Nodes may call any network | npm semver; node-red.version range for host compatibility; upgrade in palette | None; scorecard advisory ("not gatekeepers"); admin allow/deny lists | Palette Manager in editor; free | Static catalogue from a package source + advisory scorecard + counts from the package registry; avoid in-process with no enforcement |`

`| Jellyfin | .NET assembly (BasePlugin) with build.yaml (guid, version, targetAbi) | None | None | Repository = URL to manifest.json with per-version zip + checksum + targetAbi + timestamp; official + third-party repos added by URL | No (zips on any host) | Yes (in-process .NET) | No | Four-part versions per entry; targetAbi per version; reinstall + restart | None; checksum only; no third-party warning text found | Plugin catalog in dashboard; free | Minimal self-hostable repository format (manifest URL, per-version archive, checksum, target ABI) for "not in default" repos |`

`| Raycast | TypeScript/React extension in the raycast/extensions monorepo; package.json manifest (MIT, icon, categories, commands) | None | None | Monorepo PR via npm run publish, CI schema/build checks, Raycast + community review, auto-publish to Store on merge | Yes (monorepo) | Yes (Node child process, per-extension v8 isolate) | Extensions call APIs with user tokens | Version per merged PR; CHANGELOG with merge date | Open source mandatory; review; isolate but "not further sandboxed"; encrypted per-extension storage | Raycast Store; free | Transparency via mandatory source + review; shows that isolates are not permissions and that every update costs a human review |`

`| OBS plugins | Native shared library (obs_module_load); buildspec.json in template (version, obs-studio dependency hashes) | None | None | Forum Resources list (331 plugins; ratings, downloads); no manifest, no checks found | No (installers from GitHub releases) | Yes (native in-process) | No | Semver tag → CI draft release with installers | None; signing/notarization on macOS only | Forum list; free | Copy the build template (tag → CI → signed installers); a forum list is discovery, not a registry |`

## Hypotheses

- H1 (out-of-process is lowest risk): all five run extensions in-process (Python, Node.js, .NET, Node isolate, native); none offers a lower-risk alternative, which supports the hypothesis by absence — Raycast's isolates are explicitly "not further sandboxed".
- H3 (static Git registry costs nothing, no backend): strongly supports. A1111 is exactly the hypothesised design (one JSON per extension, PR, CI-built `index.json`, daily GitHub metadata) with no server; Node-RED's `catalogue.json` and Jellyfin's `manifest.json` are static files too. The hypothesis' missing half — "pointing at the developer's repo at a pinned tag/commit" — is what A1111 omits and what breaks reproducibility there.
- H4 (hand review does not scale): supports. A1111 and Raycast both rely on a human "assessment"/review per PR; Node-RED and OBS avoided the problem by not reviewing at all ("not gatekeepers"); none shows a scalable human process.
- H5 (declared is not enforced): supports. A1111's `online` tag, Node-RED's scorecard and Raycast's guidelines are declarations and checks; no runtime enforces any of them.
- H6 (runtime pip installs, shared env): supports the risk. A1111 runs each extension's `install.py` (pip) at install and every startup in one shared environment; Node-RED delegates dependency isolation to npm's per-package nesting, which is the "one env per plugin costs disk" trade-off in practice.
- H10 (counts need telemetry; stars/updated from GitHub at build time): supports. A1111 refreshes `stars`, `commit_time`, `default_branch_commit_sha` daily from the GitHub API at index-build time; Node-RED shows weekly downloads taken from npm; Jellyfin's manifest has no counts; OBS counts downloads because the forum serves them. For GitHub-hosted plugins, install counts therefore need Clips Kitty's own opt-in telemetry.
