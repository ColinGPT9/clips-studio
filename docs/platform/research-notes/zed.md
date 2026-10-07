# Zed extensions — research notes

- Platform: Zed editor extensions (`extension.toml`, `zed_extension_api`, the `zed-industries/extensions` registry)
- Tier: Track C, Tier 2 (registry-design precedent; point 6 matters most)
- Date read: 2026-10-06
- Brief's question: what makes an extension ecosystem successful, and what mistakes should Clips Kitty avoid?
- Verdict (two lines): Zed is the one precedent in this set where a declared capability is an enforced one: extensions compile to WebAssembly, the manifest lists `capabilities` (`process:exec` with command/args patterns, `download_file` with host/path patterns, `npm:install` with package patterns), and the host refuses an API call unless both the extension's manifest and the user's `granted_extension_capabilities` allow it (`capability_granter.rs`). The registry is a Git repository of submodules pinned to commits plus an `extensions.toml` (`id → submodule, version, path`), submitted one extension per PR, reviewed by humans ("a few weeks ... up to one or two months"), packaged by CI into a blob store. Borrow the capability model and the pinned-commit index with CI-built artifacts; treat the human review queue as the thing to automate around.

Every claim carries a URL and the read date 2026-10-06 unless stated. Zed's docs were read from the raw sources in `zed-industries/zed` (`docs/src/extensions/...`) because the rendered pages summarise poorly; the registry repo was read from a shallow clone at commit aa00fad (dated 2026-10-05).

## 1. The unit of extension

"Zed extensions are Git repositories containing an `extension.toml` manifest. They can provide languages, themes, debuggers, snippets, and MCP servers." (`docs/src/extensions/developing-extensions.md`, 2026-10-06). The manifest struct also knows language servers, context (MCP) servers, slash commands (deprecated), debug adapters/locators and language model providers (`crates/extension/src/extension_manifest.rs`, raw, 2026-10-06). "Only language server, context server and debugger extensions require the presence of custom Rust"; the rest are data (themes, grammars, snippets). Size: 1,569 entries in `extensions.toml` and 1,567 submodules in `.gitmodules` at commit aa00fad (counted in the clone, 2026-10-06).

## 2. Manifest or metadata format

`extension.toml` (developing-extensions.md, 2026-10-06):

```toml
id = "my-extension"
name = "My extension"
version = "0.0.1"
schema_version = 1
authors = ["Your Name <you@example.com>"]
description = "Example extension"
repository = "https://github.com/your-name/my-zed-extension"
```

Other manifest fields (from `ExtensionManifest` in `extension_manifest.rs`, raw 2026-10-06): `lib` (`kind`, `version` of the API), `themes`, `icon_themes`, `languages`, `grammars` (`repository`, `rev`, `path` — a Tree-sitter grammar pinned to a git revision), `language_servers` (`language_ids`, `code_action_kinds`), `context_servers`, `slash_commands` (`description`, `requires_argument`), `snippets`, `capabilities` (list of `ExtensionCapability`), `debug_adapters` (`schema_path`), `debug_locators`, `language_model_providers` (`name`, `icon`). The older `extension.json` is rejected by the packager ("has been superseded by `extension.toml`", `src/package-extensions.js`, clone 2026-10-06).

Capability syntax (same TOML objects in the manifest and in the user setting; `docs/src/extensions/capabilities.md`, 2026-10-06):

```toml
{ kind = "process:exec", command = "gem", args = ["**"] }
{ kind = "download_file", host = "github.com", path = ["zed-industries", "zed", "**"] }
{ kind = "npm:install", package = "typescript" }
```

`"*"` matches any single value and `["**"]` any sequence (tests in `extension_manifest.rs`: `ls -la` allowed, `ls -l` not; `cargo test **` allows `cargo test --all`, rejects `cargo build`).

Registry entry (`extensions.toml`): `[my-extension] submodule = "extensions/my-extension" version = "0.0.1"` with optional `path = "packages/zed"` for a subdirectory (`docs/src/extensions/publishing/publishing-guide.md`, 2026-10-06).

Compatibility: Rust code targets `wasm32-wasip2` and depends on the `zed_extension_api` crate; "Make sure it's still compatible with Zed versions you want to support" with a table in the crate's README (link in developing-extensions.md; table not read). `schema_version` is currently `1`.

## 3. Distribution and install

- Build and publish are done by the registry's CI, not by the author (`.github/workflows/ci.yml`, clone 2026-10-06): on PRs it packages changed extensions; on `main` it packages every extension "for which there is not already a package in the blob store" and uploads `extensions/<id>/<version>/*` to an S3-compatible bucket (DigitalOcean Spaces, `nyc3`) using a pinned `zed-extension` CLI (SHA `9ee3c503...`), Rust 1.90 and the `wasm32-wasip2` target. The packager checks that `extension.toml`'s `id` equals the `extensions.toml` key, that the built `manifest.json` version equals the `extensions.toml` version, and that a license file matches the accepted list (`src/package-extensions.js`).
- Install location on the user's machine: `installed/` ("the source code for each extension") and `work/` ("files created by the extension itself, such as downloaded language servers"); Windows path `%LOCALAPPDATA%\Zed\extensions` (`docs/src/extensions/installing-extensions.md`, 2026-10-06). How the client fetches the package from the blob store was not read — not confirmed.
- Dev install: "Install Dev Extension" compiles a local directory; "the published version will be uninstalled prior to the installation of the dev extension" (developing-extensions.md).
- What runs at install time: nothing from the extension beyond Wasm instantiation; binaries such as language servers are fetched at runtime through `download_file` under the capability check (§7). "Do not bundle a language server with the extension. Download it or check for it in the user's environment" (`publishing/prerequisites.md`, 2026-10-06).

## 4. Dependencies and isolation

- Execution: Rust compiled to WebAssembly, loaded in the Zed process; "some Rust features might not work like you would expect them to. For example, `cfg` - directives will not work and `std::env::var` will also not yield the expected results" (developing-extensions.md). Environment access goes through the API (`Worktree` for env vars and `PATH` lookups).
- Rule: "Do not read or modify anything outside the environment Zed designates for your extension." (prerequisites.md). Repositories must not use Git LFS (CI check "Enforce no Git LFS").
- Dependencies: Rust crates are compiled into the Wasm; external tools are downloaded into `work/` under `download_file`; npm packages via `npm:install`. There is no shared runtime to conflict in; each extension's downloads live in its own work directory (inferred from the directory layout).
- The boundary has had bugs: CVE-2026-27800, "Zed has Zip Slip Path Traversal in Extension Archive Extraction" — `extract_zip()` "fails to validate ZIP entry filenames for path traversal sequences ... This allows a malicious extension to write files outside its designated sandbox directory by downloading and extracting a crafted ZIP archive. Version 0.224.4 fixes the issue." Published 2026-02-25. https://api.osv.dev/v1/vulns/CVE-2026-27800 (2026-10-06; the NVD page itself did not render for the fetch tool).

## 5. Versioning and updates

- Each release is a PR that moves the submodule to a new commit and bumps `version` in `extensions.toml` to match `extension.toml` at that commit ("Update PRs are subject to the same pull request rules as new submissions"; `docs/src/extensions/publishing/updating-and-maintenance.md`, 2026-10-06). A community GitHub Action automates the PR (link in the same page).
- CI validation names: `assertVersionNotDecreased`, `validateExtensionIdsNotChanged`, `validateGitmodulesLocations` (imports in `src/package-extensions.js`, clone 2026-10-06), so versions cannot go backwards and IDs cannot change ("since an extension's ID cannot change once published, existing IDs stay as they are", `publishing/faq.md`).
- Artifacts are immutable per `(id, version)` in the bucket: the publisher skips versions already present (`unpublishedExtensionIds`).
- Client updates: "By default, every installed extension is auto-updated when Zed starts. Add an extension here with `false` to pin it to its currently installed version." (`auto_update_extensions`); "Install Another Version…" on the Extensions page installs an older version and pins it (`docs/src/reference/all-settings.md`, 2026-10-06). So pin and rollback exist on the client.
- Deprecation: agent-server and slash-command extensions "have been deprecated and submissions will no longer be accepted"; MCP server extensions "will be deprecated in favor of the MCP registry" (prerequisites.md).

## 6. Registry design

- Shape: `zed-industries/extensions` holds `extensions.toml` (sorted), `.gitmodules` (sorted) and one submodule per extension pinned to a commit that "must be present on a branch and thus not be a detached commit"; HTTPS submodule URLs only; the repository must be public (publishing-guide.md, 2026-10-06).
- Submission rules: "Every PR must add or update exactly one extension", "at most three open PRs", "Respond to maintainer feedback within 3 weeks, otherwise your PR will be closed"; repeated violations "may result in a temporary suspension or a ban" (publishing-guide.md). Fork to a personal account "as this allows Zed staff to push any needed changes to your PR".
- Automated checks: Danger (`dangerfile.ts`) fails a PR that does not touch `extensions.toml` unless labelled `allow-no-extension`; `ci.yml` builds the changed extension, verifies license, id and version, enforces sorted files and no LFS; `actionlint` for workflow edits (clone, 2026-10-06). Danger runs through a proxy so it can authenticate on fork PRs (`DANGER_GITHUB_API_BASE_URL: https://danger-proxy.zed.dev/github`).
- Human review: "every submission is reviewed, and not all of them make the cut" (README); "most submissions get their first feedback within a few weeks; for some extensions it might take us longer - up to one or two months ... the biggest one being the large backlog we are working through right now" (`publishing/faq.md`, 2026-10-06). Closed-without-feedback PRs "severely violated the publishing prerequisites".
- Content rules (prerequisites.md): no duplicate functionality ("Publish functionality that is not already available in the extension registry"); unique kebab-case ID without the words `zed` or `extension`; English UI text; license from an allowlist (Apache 2.0, BSD 2/3, CC BY 4.0, GPLv3, LGPLv3, MIT, Unlicense, zlib; required since 2025-10-01, "Without a valid license, the pull request to add or update your extension will fail CI", `license-requirements.md`).
- Removal and succession: a `remove-extension.yml` workflow opens a bot PR removing an id with a reason; unresponsive owners (no reply for "at least 6 weeks" with "written proof of attempts to establish contact") can be replaced by a community fork or a fork into the `zed-extensions` organisation (faq.md).
- AI policy: "Autonomous agents are not allowed to be used for contributing to this project"; comments must be human-written (`AI_POLICY.md`, clone 2026-10-06).
- Cost to run: GitHub Actions on third-party runners (`namespace-profile-*`), a DigitalOcean Spaces bucket for packages and the CLI, a Danger proxy, a GitHub App for the removal bot (`ZED_ZIPPY_APP_ID`), and maintainer time — the latter is the bottleneck by the project's own account. No figures published — not confirmed.

## 7. Trust and permissions

- Two-sided enforcement in the host (`crates/extension_host/src/capability_granter.rs`, raw 2026-10-06): `grant_exec` first calls `manifest.allow_exec` (error "capability for process:exec {cmd} {args} was not listed in the extension manifest") and then checks the user-granted list (error "... is not granted by the extension host"); `grant_download_file` and `grant_npm_install_package` check the granted list. The docs confirm the user side: "Restricting or removing a capability will cause an error to be returned when an extension attempts to call the corresponding extension API without sufficient capabilities." (`capabilities.md`, 2026-10-06).
- Defaults: the docs' example diff shows a granted list of `process:exec command="*" args=["**"]`, `download_file host="*" path=["**"]`, `npm:install package="*"` and says removing all grants "will likely make many extensions non-functional"; the default value is not stated on the page — the permissive wildcard default is inferred from that example. Users can narrow it (e.g. `host = "github.com"`).
- Tiers / "verified": none. Trust comes from (a) the Wasm boundary, (b) the capability lists, (c) human review before listing, (d) a required open-source license. Zed makes no "verified" claim in the pages read.
- Residual risk the model does not cover: a crafted archive escaping the work directory (CVE-2026-27800, §4) — enforcement is only as good as the host's file handling.

## 8. Models

Not applicable (language model providers are a manifest kind; no model download or cache was found in the pages read).

## 9. Local, remote or both

Local Wasm in the editor; `download_file` fetches tools from allowed hosts; MCP server extensions start local servers. Remote execution is not part of the model.

## 10. Known incidents and stated limitations

- CVE-2026-27800 (Zip Slip in extension archive extraction, fixed in 0.224.4, published 2026-02-25) — https://api.osv.dev/v1/vulns/CVE-2026-27800 (2026-10-06). A related tar/symlink extraction issue was listed in search results as CVE-2026-27976 — not read, not confirmed.
- Stated limitation: review backlog of weeks to months and a stale-PR policy the project itself calls "unfair" ("we are not proud of this either", faq.md).
- MCP server extensions are a transitional mechanism to be replaced by the MCP registry (prerequisites.md).

## 11. Borrow

1. The capability model exactly as structured: a `capabilities` list in the plugin manifest with kinds and patterns (`process:exec` command + args, `download_file` host + path), checked by the runtime on every call, intersected with a user-editable granted list. This is what lets Clips Kitty say "enforced" truthfully for the subset the runtime mediates (process spawning, downloads, network hosts).
2. Index entry = `id`, pinned ref, version, optional subpath; CI verifies the pinned ref's manifest has the same id and version, that versions never decrease and ids never change.
3. CI builds or at least validates the artifact so users never run a build; artifacts keyed by `(id, version)` are never overwritten.
4. PR hygiene that keeps a queue workable: exactly one plugin per PR, a cap on open PRs, a stale deadline, a bot that refuses PRs without an index change, a license allowlist checked mechanically.
5. Operational policies written down: removal workflow with a reason, unresponsive-owner succession (6 weeks with written proof), explicit AI-contribution policy.
6. Client-side pin and rollback (`auto_update_extensions: {id: false}`, "Install Another Version…").

## 12. Avoid

1. Making human review the only gate: Zed's own FAQ documents a backlog of one to two months; Clips Kitty should let automated checks publish well-formed entries and reserve humans for flags.
2. Permissive wildcard defaults if "enforced" is claimed: an enforced `download_file host="*"` protects nothing; defaults should be the manifest's declared set, narrowed by the user.
3. Git submodules as the only pinning mechanism: they work but add contributor friction (init/update, detached-commit rules); a `repo + ref` pair in the index file with CI resolving the commit SHA gives the same pin with less ceremony (inferred).
4. Trusting the sandbox boundary without testing archive extraction (zip slip, symlinks) — the one Zed CVE found was exactly there.
5. Deprecating extension kinds without a migration path: MCP server extensions were told to republish elsewhere.

## 13. Sources, with dates

- https://raw.githubusercontent.com/zed-industries/zed/main/docs/src/extensions/developing-extensions.md — 2026-10-06 (rendered page https://zed.dev/docs/extensions/developing-extensions also fetched).
- https://raw.githubusercontent.com/zed-industries/zed/main/docs/src/extensions/capabilities.md — 2026-10-06 (rendered https://zed.dev/docs/extensions/capabilities.html also fetched).
- https://raw.githubusercontent.com/zed-industries/zed/main/docs/src/extensions/publishing/publishing-guide.md, `prerequisites.md`, `license-requirements.md`, `faq.md`, `overview.md`, `updating-and-maintenance.md` — 2026-10-06 (rendered https://zed.dev/docs/extensions/publishing/publishing-guide.html and `/overview` also fetched).
- https://raw.githubusercontent.com/zed-industries/zed/main/docs/src/extensions/installing-extensions.md — 2026-10-06.
- https://raw.githubusercontent.com/zed-industries/zed/main/docs/src/reference/all-settings.md — 2026-10-06 (`auto_install_extensions`, `auto_update_extensions`).
- https://raw.githubusercontent.com/zed-industries/zed/main/crates/extension/src/extension_manifest.rs — 2026-10-06 (658 lines).
- https://raw.githubusercontent.com/zed-industries/zed/main/crates/extension_host/src/capability_granter.rs — 2026-10-06.
- https://github.com/zed-industries/extensions — 2026-10-06 (README; shallow clone at commit aa00fad dated 2026-10-05, submodules not fetched: `extensions.toml`, `.gitmodules`, `.github/workflows/ci.yml`, `danger.yml`, `remove-extension.yml`, `actionlint.yml`, `dangerfile.ts`, `src/package-extensions.js`, `CONTRIBUTING.md`, `AI_POLICY.md`, `.github/pull_request_template.md`, `package.json`).
- https://api.osv.dev/v1/vulns/CVE-2026-27800 — 2026-10-06 (https://nvd.nist.gov/vuln/detail/CVE-2026-27800 did not render for the fetch tool).
- Web search "Zed extension removed malicious OR security extensions repository" — 2026-10-06 (surfaced the CVE; no malicious-extension incident found).

## Matrix row

`| Zed extensions | Git repo with extension.toml; optional Rust compiled to Wasm (wasm32-wasip2) against zed_extension_api | None | None | zed-industries/extensions: extensions.toml + git submodule pinned to a commit, one extension per PR, human review (weeks to months), CI packages to a Spaces bucket | Yes (submodule pinned to a commit) | Yes (Wasm inside the editor) | Only downloads of tools from allowed hosts; MCP server extensions | version in extension.toml must equal extensions.toml; never decreases; ids immutable; auto-update on start with per-extension pin and older-version install | Capabilities (process:exec, download_file, npm:install) declared in manifest and granted by user, both enforced by the host; license allowlist; CVE-2026-27800 zip slip | Extension gallery in the app; free | The only host-enforced capability model in the set; pinned-commit Git index with CI-built artifacts; human review is the bottleneck to automate around |`

## Hypotheses

- H1 (out-of-process plugins are lowest risk): partial. Zed keeps extensions in-process but behind a Wasm boundary and capability checks; the lesson is that the boundary plus mediated APIs is what reduces risk, and even that boundary had an extraction bug (CVE-2026-27800).
- H3 (static Git registry costs nothing, needs no backend): supports with a caveat. The index is a Git repo built by CI with no application server, but Zed also builds and hosts artifacts in a blob store and runs a Danger proxy; "no backend" is true, "no hosting" is not.
- H4 (hand review does not scale): supports. Zed documents first feedback "within a few weeks ... up to one or two months" and a "large backlog", and has added PR caps and stale rules to cope.
- H5 (declared is not enforced; Zed is the exception): confirms the Zed half. `capability_granter.rs` rejects calls not allowed by both the manifest and the user grant; the docs state the error behaviour.
- H8 (namespaced ids + declared capabilities): partial. IDs are flat kebab-case, not namespaced; capabilities are declared and typed by kind/pattern, which is the shape Clips Kitty needs for the "declared capabilities" half.
- H10 (counts need telemetry): not addressed by the pages read; no install counts appear in the index repository, and whether the gallery shows them was not confirmed.
