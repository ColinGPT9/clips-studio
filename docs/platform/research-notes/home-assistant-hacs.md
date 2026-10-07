# Home Assistant custom integrations + HACS — research notes

- Platform: Home Assistant (custom integrations) + HACS (Home Assistant Community Store, the `hacs/default` list)
- Tier: Track C, Tier 2 (registry-design precedent; point 6 matters most)
- Date read: 2026-10-06
- Brief's question: what makes an extension ecosystem successful, and what mistakes should Clips Kitty avoid?
- Verdict (two lines): HACS is the largest "Git list + CI checks + human review" registry in the set: a plain JSON array of `owner/repo` per category, submitted by PR, gated by eleven automated checks and then by a human queue that the HACS docs themselves say "take months"; installs are pinned to GitHub release tags, and a `removed`/`critical` list acts as a kill switch. Home Assistant itself runs custom integrations in-process with no isolation, warns in the log, and keeps a hard-coded block list of known-bad versions; the 2021 disclosure showed what in-process access costs. Borrow the release-pinned install, the mandatory `version`, the kill list, `iot_class`-style local/cloud declaration and the graduated quality scale; do not borrow in-process execution or a review queue measured in months.

Every claim carries a URL and the read date 2026-10-06 unless stated. "not confirmed" marks what could not be verified.

## 1. The unit of extension

- Home Assistant: a "custom integration", a directory `<config>/custom_components/<domain>/` containing at least `manifest.json` and `__init__.py`. Custom integrations can override a core integration with the same domain ("overriding built-in integrations is not recommended as you will no longer get updates"). https://developers.home-assistant.io/docs/creating_integration_file_structure/ (2026-10-06).
- HACS: one integration per repository ("there can only be one subdirectory to `ROOT_OF_THE_REPO/custom_components/`"), plus other categories: `plugin` (Lovelace JS cards), `theme`, `python_script`, `template`, `appdaemon` (and a legacy `netdaemon` list). https://hacs.xyz/docs/publish/integration and the category files in https://github.com/hacs/default (2026-10-06).
- Size: in the `hacs/default` clone (commit 94ae363, 2026-09-20) the category files have 3,264 lines (`integration`), 774 (`plugin`), 109 (`theme`), 57 (`appdaemon`), 13 (`template`), 10 (`python_script`); each line is one repository, so roughly 3,250 integrations and 4,200 repositories in total (line counts, approximate).

## 2. Manifest or metadata format

Two files: Home Assistant's `manifest.json` (inside the integration directory) and HACS's `hacs.json` (repository root).

`manifest.json` keys, from https://developers.home-assistant.io/docs/creating_integration_manifest/ (2026-10-06):

| Key | Note |
|---|---|
| `domain` | required; unique; must match the directory name; "Cannot be changed after creation" |
| `name` | required |
| `version` | "Required for custom integrations only"; "a valid version recognized by AwesomeVersion like CalVer or SemVer" |
| `documentation` | required (URL) |
| `issue_tracker` | URL; omitted for core |
| `dependencies`, `after_dependencies` | integrations to load first (hard / optional) |
| `codeowners` | GitHub usernames |
| `requirements` | pip packages; "Home Assistant will try to install the requirements into the deps subdirectory"; "Custom integrations should only include requirements that are not required by the Core requirements.txt" |
| `iot_class` | `assumed_state`, `cloud_polling`, `cloud_push`, `local_polling`, `local_push`, `calculated` |
| `integration_type` | `device`, `entity`, `hardware`, `helper`, `hub`, `service`, `system`, `virtual` (defaults to `hub` for custom) |
| `config_flow`, `single_config_entry` | booleans |
| `loggers` | logger names of the requirements |
| `quality_scale` | `bronze`, `silver`, `gold`, `platinum` |
| `preview_features`, `import_executor`, `disabled` | misc |
| `bluetooth`, `zeroconf`, `ssdp`, `homekit`, `mqtt`, `dhcp`, `usb` | discovery matchers |
| `supported_by`, `iot_standards` | virtual integrations |

HACS additionally requires `domain`, `documentation`, `issue_tracker`, `codeowners`, `name`, `version` in `manifest.json`. https://hacs.xyz/docs/publish/integration (2026-10-06).

`hacs.json` keys, from https://hacs.xyz/docs/publish/start (2026-10-06): `name` (required, "The display name that will be used in the HACS UI"), `content_in_root`, `zip_release` ("Indicates whether the content is in a zipped archive when releases are published", integrations only), `filename`, `hide_default_branch` ("Tells HACS to not offer downloading the default branch"), `country` (ISO 3166-1 alpha-2), `homeassistant` (minimum Home Assistant version), `hacs` (minimum HACS version), `persistent_directory` ("kept safe during upgrades", integrations only). The HACS validator refuses `zip_release: true` without `filename` (`validate/hacsjson.py` in https://github.com/hacs/integration, read raw 2026-10-06).

Compatibility enforcement: HACS's `can_download` returns False when `hacs.json`'s `homeassistant` is higher than the running Home Assistant version (only when the repository uses releases) — `custom_components/hacs/repositories/base.py` in https://github.com/hacs/integration (raw, 2026-10-06).

Host-side version enforcement (`homeassistant/loader.py` in https://github.com/home-assistant/core, branch `dev`, raw, 2026-10-06): a custom integration without a `version` key is logged "does not have a version key in the manifest file and was blocked from loading"; an unparsable version is likewise blocked; and a hard-coded `BLOCKED_CUSTOM_INTEGRATIONS` dict blocks specific domains below a "lowest good version" with reasons such as "breaks Home Assistant", "crashes Home Assistant", "prevents recorder from working", "breaks the template engine" (the list includes `hacs` itself).

## 3. Distribution and install

- Source of truth: GitHub only ("Public GitHub repositories only"). https://hacs.xyz/docs/publish/start (2026-10-06).
- Version resolution: "If the repository uses GitHub releases, the tag name from the latest release is used to set the remote version." Without releases, "the 7 first characters of the last commit will be used". Same page.
- What is downloaded (`repositories/base.py`, raw, 2026-10-06): with `zip_release`, HACS downloads the release asset named `filename` for the selected tag (`github_release_asset(repository, version=ref, filename)`) and extracts it into the integration path; otherwise it downloads the repository archive at the ref (`download_repository_zip`) or the files one by one from the tree. So an install is pinned to a release tag (or, if the user picks it, the default branch's commit).
- Version choice in the UI: HACS "will present the user with a nice selection view of the 5 latest releases together with the default branch" (https://hacs.xyz/docs/publish/integration, 2026-10-06); "Under Need a different version?, select the version you want to download" (https://hacs.xyz/docs/use/repositories/dashboard, 2026-10-06).
- Not-in-default repositories: menu "Custom repositories" → URL + type → ADD; "Not all repositories will work in HACS, since HACS still needs the repository to have a known structure." https://hacs.xyz/docs/faq/custom_repositories/ (2026-10-06). The page carries no statement that these are unreviewed (they are not in the default list, so none of the §6 checks ran — inferred).
- What runs at install time: HACS runs nothing from the repository; Home Assistant pip-installs `requirements` when the integration loads (§2). Brand assets (`brand/icon.png`) are read, not executed.
- Store data: the HACS client reads pre-built JSON from `https://data-v2.hacs.xyz/<category>/data.json` and `/repositories.json`. I fetched `integration/data.json` (2026-10-06): keyed by GitHub repository id, each entry carries `manifest.name`, `description`, `domain`, `downloads`, `etag_releases`, `etag_repository`, `full_name`, `last_commit`, `last_updated`, `last_version`, `open_issues`, `stargazers_count`, `topics`, `last_fetched`. The `removed` and `critical` lists are uploaded by `hacs/default` workflows to a Cloudflare R2 bucket named `data-v2` (`aws s3 sync upload/removed s3://data-v2/removed --endpoint-url $CF_R2_ENDPOINT_DATA`) followed by a Cloudflare cache purge (`.github/workflows/upload-removed.yml`, clone 2026-10-06). Which job builds the per-category `data.json` (with stars and downloads) was not located in `hacs/default` — not confirmed (inferred: a scheduled job in another HACS repository using the GitHub API).

## 4. Dependencies and isolation

- In-process Python, no isolation: custom integrations are imported from the `custom_components` package into the Home Assistant process (`loader.py` `_get_custom_components`, raw 2026-10-06). The loader logs: "We found a custom integration %s which has not been tested by Home Assistant. This component might cause stability problems, be sure to disable it if you experience issues with Home Assistant" (`CUSTOM_WARNING`, same file).
- Dependencies: pip `requirements` installed into Home Assistant's `deps` directory, one shared environment; the only conflict guidance is to avoid duplicating core requirements (§2). No per-integration environment (inferred from the above; nothing in the docs read describes one).
- Consequence on record: the 2021 disclosure — "a directory traversal attack via an unauthenticated webview, allowing an attacker to access any file that is accessible by the Home Assistant process", including stored credentials. https://www.home-assistant.io/blog/2021/01/22/security-disclosure/ (2026-10-06).

## 5. Versioning and updates

- `version` is mandatory for custom integrations and validated by AwesomeVersion (CalVer, SemVer, SimpleVer, BuildVer, PEP 440 strategies in `loader.py`, raw 2026-10-06).
- HACS computes `pending_update` by comparing the installed and available version (release repos) or commit (branch installs), and offers the last 5 releases plus the default branch; selecting an older release is the rollback path (inferred from the version picker; no explicit "rollback" feature documented).
- Immutability: a GitHub release tag can be moved by the author; HACS stores `etag_releases`/`etag_repository` to detect changes but does not pin a commit hash for release installs — not confirmed beyond the fields seen in `data.json` and `base.py`.
- Deprecation/removal: entries in `removed` carry `removal_type` and optional `reason`/`link`; counts in the clone (2026-10-06): `remove` 331, `blacklist` 70, `removal` 13, `removed` 8, `replaced` 5, `archived` 2, `deprecated` 2, `critical` 1. `critical` entries are "Security issues, known to steal auth tokens." (the only entry is the placeholder `test/test`).
- Host block list: `BLOCKED_CUSTOM_INTEGRATIONS` (§2) lets the core team stop a known-bad version from loading without touching HACS.

## 6. Registry design

- Shape: a Git repository (`hacs/default`) whose category files are JSON arrays of `"owner/repo"` strings, alphabetically sorted (`scripts/is_sorted.py`, `scripts/sort.py`). No per-repository metadata file: the metadata (name, description, versions, stars) is derived from the repository and from `hacs.json`/`manifest.json` at index-build time. https://github.com/hacs/default (clone 2026-10-06).
- Submission: "Only the owner or a major contributor of a repository can submit a pull request (PR) to add it as a default." Requirements: public repo, works as a custom repository, passes the HACS Action and Hassfest, "Create a new GitHub release (not just a tag, a full release) after the actions run successfully", PR editable by maintainers, `country` in `hacs.json` when region-specific. https://hacs.xyz/docs/publish/include (2026-10-06). The PR template demands links to the release, the passing HACS action run and (integrations) the Hassfest run, and opens with "DO NOT REQUEST REVIEWS ... IF YOU DO THE PULL REQUEST WILL BE CLOSED!" (`.github/PULL_REQUEST_TEMPLATE.md`, clone 2026-10-06).
- Automated checks (docs list): "Check brands (integrations only), Check manifest (integrations only), Check hacs-validation, Check HACS manifest, Check archived status, Check releases, Check owner credentials, Check images (plugins/themes only), Check repository (description, issues enabled, topics defined), Lint jq, Lint sorted." https://hacs.xyz/docs/publish/include (2026-10-06).
- Automated checks (as implemented in `.github/workflows/checks.yml`, clone 2026-10-06): the run type comes from PR labels (`New default repository`, `renamed-repositories`, `remove-repositories`); jobs `owner` (PR author is the repo owner or a contributor with at least one third of the top contributor's commits, and not in `REMOVED_PUBLISHERS`), `editable` (`maintainer_can_modify`), `releases` (GitHub API returns at least one release), `removed` (not in `data-v2.hacs.xyz/removed/repositories.json`), `existing` (not already in any category), `hassfest` (runs `ghcr.io/home-assistant/hassfest:latest` in Docker against the cloned integration), `hacs` (the `hacs/action@main` action, whose ignorable checks are `archived`, `brands`, `description`, `hacsjson`, `images`, `information`, `issues`, `topics`; https://hacs.xyz/docs/publish/action, 2026-10-06), and a `completed` gate that fails if any dependency failed.
- Brands: the `brands` validator accepts `brand/icon.png` in the repository, else requires the domain to be in the `custom` list of https://brands.home-assistant.io/domains.json (`validate/brands.py` in https://github.com/hacs/integration, raw 2026-10-06).
- Human review: "HACS is growing fast, with new repositories added almost daily, but new additions still take months to be reviewed and included." https://hacs.xyz/docs/publish/include (2026-10-06).
- Removal: `scripts/remove_repo.py` moves a repository to `blacklist` and appends to `removed` with `removal_type`/`reason`/`link`; `scripts/remove_publishers.py` bans whole publishers (three listed, each linked to an issue). Removed lists are then uploaded to R2 and the cache purged (§3).
- Cost to run: GitHub Actions on a public repository, a `GITHUB_TOKEN` for API checks, a Cloudflare R2 bucket plus cache purge token, a Discord webhook for failures (workflow secrets `CF_R2_*`, `CF_ZONE_ID`, `DISCORD_WEBHOOK_ACTION_FAILURE`). No server code in `hacs/default`. Money figures are not published — not confirmed. The scarce resource is reviewer time (inferred from the "months" statement).

## 7. Trust and permissions

- Home Assistant's position: "The Home Assistant project does not review, security audit, maintain, or support third-party custom integrations." https://developers.home-assistant.io/docs/core/integration-quality-scale/ (2026-10-06). The loader warning (§4) is shown for every custom integration.
- Integration Quality Scale tiers: Bronze ("Can be easily set up through the UI. The source code adheres to basic coding standards and development guidelines."), Silver ("Provides a stable user experience under various conditions. Has one or more active code owners"), Gold ("the best end-user experience ... Can be automatically discovered"), Platinum ("fully typed with type annotations"); special tiers No score, Internal, Legacy, Custom ("Community-developed integrations, not officially supported"). Compliance is tracked in a `quality_scale.yaml` per integration; the core team approves tier assignments; "New integrations are required to fulfill at least the bronze tier" (core). Same page and https://developers.home-assistant.io/docs/creating_integration_manifest/ (2026-10-06). For custom integrations the scale is a documentation signal, not a gate (inferred: custom integrations show as "Custom").
- Declared permissions: none. `iot_class` declares local vs cloud behaviour, but nothing enforces network use (no permission model exists in the docs read). "Verified" is not a HACS concept; "in the default list" means the checks in §6 passed and a maintainer merged the PR (inferred from §6; HACS makes no audit claim).

## 8. Models

Not applicable (no model management in Home Assistant or HACS).

## 9. Local, remote or both

Local process; integrations declare whether they talk to the cloud via `iot_class` (`cloud_polling`/`cloud_push` vs `local_*`). HACS itself needs GitHub access for store data and downloads (§3).

## 10. Known incidents and stated limitations

- 2021-01-22 security disclosure: unauthenticated directory traversal in seven custom integrations (HACS fixed in v1.10.0, Dwains Lovelace Dashboard v2.0.1, Font Awesome v1.3.0, BWAlarm v1.12.8, Simple Icons v1.10.0, Custom Updater; "Custom icons" and "Hass-album" not fixed, removal recommended); Home Assistant Core 2021.1.3 "added extra protection to stop directory traversal attacks before reaching the vulnerable code". https://www.home-assistant.io/blog/2021/01/22/security-disclosure/ (2026-10-06). A second disclosure followed on 2021-01-23 (https://www.home-assistant.io/blog/2021/01/23/security-disclosure2/, found by search, not read — not confirmed).
- Host-side block list of custom integrations that crash the core or break the recorder (§2) — evidence that in-process plugins take the host down.
- Stated limitation: review latency of months (§6); custom repositories bypass review entirely (§3).

## 11. Borrow

1. Mandatory `version` in the manifest, validated at load; refuse to load a plugin without one (HA `loader.py`).
2. A host-side kill list (`BLOCKED_CUSTOM_INTEGRATIONS` with "lowest good version" and a reason) and a registry-side `removed`/`critical` JSON that the client checks before install and on update — cheap, immediate, no backend.
3. Install from a GitHub release tag by default, let the user pick among the last N releases, and treat "default branch" as an explicit opt-in (`hide_default_branch`).
4. A per-plugin `iot_class`-style field (local / remote / hybrid) and a `homeassistant`-style minimum host version field that gates download (`can_download`).
5. The automated PR checks worth copying one-for-one: owner-or-major-contributor, repository has a release, not previously removed, not a duplicate, list sorted, description/topics/issues enabled, icon present.
6. A graduated, rule-based quality scale (bronze/silver/gold/platinum) as a displayed signal that is earned by checklists, with the lowest tier required for a listing.

## 12. Avoid

1. In-process execution with the host's full file and credential access (2021 disclosure; crash block list).
2. A human review queue that takes months with no automated fast path for well-formed submissions.
3. pip `requirements` into one shared environment with no conflict handling.
4. Claiming any trust signal beyond "checks passed and a maintainer merged" — HACS does not, and neither should Clips Kitty.
5. Letting the registry client install from a moving branch by default.

## 13. Sources, with dates

- https://developers.home-assistant.io/docs/creating_integration_manifest/ — 2026-10-06.
- https://developers.home-assistant.io/docs/creating_integration_file_structure/ — 2026-10-06.
- https://developers.home-assistant.io/docs/core/integration-quality-scale/ — 2026-10-06.
- https://raw.githubusercontent.com/home-assistant/core/dev/homeassistant/loader.py — 2026-10-06 (1,855 lines).
- https://hacs.xyz/docs/publish/include — 2026-10-06.
- https://hacs.xyz/docs/publish/integration — 2026-10-06.
- https://hacs.xyz/docs/publish/start — 2026-10-06.
- https://hacs.xyz/docs/publish/action — 2026-10-06.
- https://hacs.xyz/docs/use/repositories/dashboard — 2026-10-06.
- https://hacs.xyz/docs/faq/custom_repositories/ — 2026-10-06.
- https://github.com/hacs/default — 2026-10-06 (shallow clone at commit 94ae363, dated 2026-09-20: README, category files, `removed`, `critical`, `blacklist`, `.github/workflows/checks.yml`, `upload-removed.yml`, `upload-critical.yml`, `scripts/`, PR template).
- https://raw.githubusercontent.com/hacs/integration/main/custom_components/hacs/repositories/base.py — 2026-10-06.
- https://raw.githubusercontent.com/hacs/integration/main/custom_components/hacs/validate/brands.py and `validate/hacsjson.py` — 2026-10-06 (`validate/manifest.py` returned 404 at that path — not confirmed).
- https://data-v2.hacs.xyz/integration/data.json, `/integration/repositories.json`, `/removed/repositories.json` — 2026-10-06 (live JSON).
- https://www.home-assistant.io/blog/2021/01/22/security-disclosure/ — 2026-10-06.
- https://www.home-assistant.io/blog/2021/01/23/security-disclosure2/ — found by search 2026-10-06, not read.

## Matrix row

`| Home Assistant + HACS | Python custom integration (custom_components/<domain>/manifest.json); HACS also JS cards, themes, scripts, templates | None | None (automations are core features, not plugins) | hacs/default: JSON lists per category, PR + 11 automated checks + human review ("months"); client reads static JSON from data-v2.hacs.xyz (Cloudflare R2) | Yes (GitHub only; install from release tag or default branch) | Yes (in-process Python) | Integrations may call cloud; declared by iot_class | manifest version required (AwesomeVersion); last 5 releases selectable; removed/critical lists; host block list of bad versions | No permission model; loader warning only; 2021 traversal disclosure; brands icon required | HACS store UI inside HA; free, no payments | Release-pinned install, mandatory version + kill lists, local/cloud declaration, graduated quality scale, concrete PR checks; avoid in-process execution and a months-long human queue |`

## Hypotheses

- H1 (out-of-process is lowest risk): supports by counter-example. Custom integrations run in-process; the 2021 disclosure exposed "any file that is accessible by the Home Assistant process", and the core keeps a block list of versions that crash it.
- H3 (static Git registry costs nothing, needs no backend): mostly supports. `hacs/default` is a Git list built by GitHub Actions with no application server; but the client reads a hosted static JSON (Cloudflare R2 + cache purge) and the checks call the GitHub API with a token, so "no backend" means "no code to run", not "no hosting".
- H4 (hand review does not scale): supports. "new additions still take months to be reviewed and included" despite eleven automated checks.
- H5 (declared is not enforced): supports. There are no declared permissions at all; `iot_class` is informational; nothing is enforced.
- H10 (counts need telemetry; stars and last-updated from GitHub at build time): supports. `data.json` carries `stargazers_count`, `open_issues`, `last_updated`, `last_version` fetched from GitHub; a `downloads` field also exists, whose source (GitHub release-asset download counts, inferred) was not confirmed.
