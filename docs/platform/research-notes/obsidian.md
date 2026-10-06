# Obsidian community plugins — research notes

- Platform: Obsidian (Electron note-taking app) community plugins
- Tier: 2 (Track C: what makes an extension ecosystem successful, and what mistakes should Clips Kitty avoid?)
- Date read: 2026-10-06
- Question for this platform: a GitHub-first, zero-payment plugin ecosystem that for years ran on one JSON file submitted by pull request and hand review, and in May 2026 replaced hand review with automated per-version checks. What exactly changed, what the manifest looks like, how installs and updates come straight from GitHub releases, and what the docs tell users about plugin security.

**Verdict (two lines):** Obsidian is the closest working precedent for "GitHub-based install at a pinned ref + free registry": the app installs `main.js`/`manifest.json`/`styles.css` from the developer's own GitHub release whose tag equals `manifest.version`, the directory was a 5-field JSON list, and `fundingUrl` is the whole payments model.
The lesson of 2026 is that the cheap part (hosting an index) was never the bottleneck; hand review was, with 2,300+ submissions queued and later versions never reviewed, so Obsidian built a hosted directory that scans every version automatically, keeps humans for popular/featured/flagged plugins, and tells users plainly that plugins cannot be restricted to permissions.

## 1. Unit of extension

A *community plugin*: a JavaScript bundle (`main.js`) plus `manifest.json` and optional `styles.css`, loaded into the Electron app's renderer with the app's own access. Themes are a separate unit (CSS only). There is no narrower unit (no "node" or "function") and no capability/permission unit; a plugin either runs with full access or not at all (section 7).

## 2. Manifest or metadata format

File: `manifest.json` at the repository root and in the release. From the Manifest reference (read 2026-10-06):

| Field | Required | Documented meaning (quoted) |
|---|---|---|
| `id` | Y (plugins) | "Must contain only lowercase letters and hyphens, can't end with `plugin`, and can't contain `obsidian`." |
| `name` | Y | "The display name." |
| `version` | Y | "Using Semantic Versioning in the format `x.y.z`" |
| `minAppVersion` | Y | "The minimum required Obsidian version." |
| `description` | Y (plugins) | "A description of your plugin." |
| `author` | Y | "The author's name." |
| `authorUrl` | N | URL to the author's website |
| `fundingUrl` | N | string, or object of label → URL: `{ "fundingUrl": { "Label": "URL", "Label2": "URL" } }` |
| `isDesktopOnly` | Y (plugins) | boolean; true when the plugin uses Node.js/Electron APIs |

Name rules: "Short and descriptive", English, "Basic Latin characters only. No punctuation (except hyphens, plus sign, and parenthesis), emoji, or special characters", and no Obsidian core plugin names, "Obsidian" or variations, or the word "Plugin" [Manifest reference, 2026-10-06]. Submission requirements add: "Only use `fundingUrl` to link to services for financial support", descriptions "Stay under 250 characters", end with a period, no emoji, and "If your plugin uses any of these APIs, you **must** set `isDesktopOnly` to `true`" (Node/Electron); "Obsidian automatically prefixes command IDs with your plugin ID." [Submission requirements for plugins, 2026-10-06].

Note the id is flat: no publisher namespace. Uniqueness is global across the whole directory ("The `id` must be unique across all published plugins and can't contain `obsidian`") [Submit your plugin, 2026-10-06].

Compatibility map: `versions.json` at the repository root. "versions.json lets you control the plugin version based on the version of the user's Obsidian app." When the user's app is older than the manifest's `minAppVersion`, "Obsidian looks for a `versions.json` file at the root of the plugin repository." Example: manifest `{"version": "1.0.0", "minAppVersion": "1.2.0"}` with `versions.json` `{"0.1.0": "1.0.0", "0.12.0": "1.1.0"}` means "the most recent plugin version for 1.1.0 is 0.12.0". Not every release must be listed: "You only need to update `versions.json` if you change the `minAppVersion`." [Versions reference, 2026-10-06].

Directory entry (per plugin, in `community-plugins.json`): exactly five string fields, `id`, `name`, `author`, `description`, `repo` (`owner/name`). Confirmed by reading the file (8,468 entries on 2026-10-06; the only keys present are `author`, `description`, `id`, `name`, `repo`) and by the mirror workflow's validation: required string `id`, `name`, `repo` (non-empty), `author` and `description` (may be empty), `repo` must match `^[^/[:space:]]+/[^/[:space:]]+$`, no duplicate ids [obsidian-releases clone, `.github/workflows/mirror-community-json.yml`, 2026-10-06].

## 3. Distribution and install

GitHub-first, with release assets as the artefact (obsidian-releases README, read 2026-10-06, quoted):
- "Obsidian will read the list of plugins in `community-plugins.json`."
- "The `name`, `author` and `description` fields are used for searching."
- "When the user opens the detail page of your plugin, Obsidian will pull the `manifest.json` and `README.md` from your GitHub repo)."
- "The `manifest.json` in your repo will only be used to figure out the latest version. Actual files are fetched from your GitHub releases."
- "If your `manifest.json` requires a version of Obsidian that's higher than the running app, your `versions.json` will be consulted to find the latest version of your plugin that is compatible."
- "When the user chooses to install your plugin, Obsidian will look for your GitHub releases tagged identically to the version inside `manifest.json`."
- "Obsidian will download `manifest.json`, `main.js`, and `styles.css` (if available), and store them in the proper location inside the vault."

Release rules (Submit your plugin, 2026-10-06): version "follows the Semantic Versioning specification, for example `1.0.0`"; "The 'Tag version' of the release must match the version in your `manifest.json`"; upload `main.js`, `manifest.json`, `styles.css` (optional) as binary attachments. The directory "processes the manifest.json at the HEAD of your repository's default branch".

What runs at install time: nothing from the plugin until the user enables it; files are copied into the vault's plugin folder. Developer policies forbid self-installation: plugins must not "Install or update themselves or their dependencies" [Developer policies, 2026-10-06].

BRAT (Beta Reviewers Auto-update Tester), the community tool Obsidian's own docs recommend ("we recommend that you use the BRAT plugin to distribute your plugin to beta testers before it's been published" [Beta-testing plugins, 2026-10-06]; the obsidian-releases README also recommends it for public betas). From reading its source (TfTHacker/obsidian42-brat, v2.2.0, `minAppVersion` 1.11.4, shallow clone 2026-10-06; read, not run):
- it lists releases with `https://api.github.com/repos/${repositoryPath}/releases`, sorts tags with a semver-coerce (so non-semver tags are tolerated), skips `prerelease` releases unless the user opts in, and can pin a "frozen version" (`specifyVersion` → `/releases/tags/${version}`; `pluginSubListFrozenVersion` in settings);
- it tries `manifest-beta.json` first, then `manifest.json` (`// attempt to get manifest-beta.json`), downloads assets via `browser_download_url` (or the API asset URL with a token for private repos), checks `minAppVersion` with `requireApiVersion` (an `allowIncompatiblePlugins` override exists) and refuses `isDesktopOnly` plugins on mobile;
- a GitHub personal access token (scope `public_repo` link in the settings UI) is stored in Obsidian's SecretStorage (1.11.4+) and raises the API rate limit;
- it still reads `https://raw.githubusercontent.com/obsidianmd/obsidian-releases/HEAD/community-plugins.json` for the official list.
BRAT is the proof that "install from a GitHub repo at a chosen release" needs no registry at all; the registry adds discovery and review, not installation.

## 4. Dependencies and isolation

One shared runtime (the Electron renderer of the app); there is no per-plugin environment and no dependency manager. Plugins bundle everything into `main.js`. Conflicts are handled by convention, e.g. the guidelines tell authors to use `Vault.process()` for atomic background edits "to prevent plugin conflicts", `FileManager.processFrontMatter()` for YAML, and to release resources on unload: "Any resources created by the plugin, such as event listeners, must be destroyed or released when the plugin unloads." [Plugin guidelines, 2026-10-06]. Mobile vs desktop is the only runtime split (`isDesktopOnly`).

## 5. Versioning and updates

- Immutable by construction: a version is a GitHub release tag equal to `manifest.version`; the app fetches assets from that release. Latest version = `manifest.json` at HEAD of the default branch.
- Downgrade/compat: `versions.json` lets older apps resolve the newest compatible plugin version (section 2). The official app has no documented "install another version" UI; BRAT offers frozen versions. (The app's own rollback: not confirmed.)
- Updates: "users can download new releases from GitHub directly from within Obsidian" [Submit your plugin, 2026-10-06]. Since May 2026 every release is scanned: "You can continue to release new versions via GitHub without using the new developer dashboard. New releases are automatically reviewed. However if your update fails to pass review, you will need to use the developer dashboard to see all the details." and "Each new version is scanned, and if it fails to pass review, the plugin is removed from search within 24 hours." [Future of plugins, 2026-05-12, read 2026-10-06].
- Deprecation/removal: by policy ("Plugins and themes that don't follow these policies will be removed from the directory"), with "Immediate removal" when content "appears to be malicious", the developer is uncooperative, violations repeat, or the project is unmaintained [Developer policies, 2026-10-06]. Older plugins failing the new checks "have been temporarily granted an exception. However, all plugins and themes that do not pass the new review process will eventually be phased out of the official directory." [Future of plugins].

## 6. Registry design

**Until May 2026 (legacy, still visible in the repo):** a Git index. `community-plugins.json` in `obsidianmd/obsidian-releases`, one 5-field entry per plugin, added by pull request using a PR template (the repo still carries `.github/pull_request_template.md` pointing at `plugin.md`/`theme.md` templates, read 2026-10-06), validated by a bot, then manually approved by the Obsidian team. The blog describes it: "Until today, initial submissions were manually reviewed and approved by our small team to ensure they follow the Developer Policies. However, as Obsidian has grown in popularity we struggled to keep pace with submissions, and subsequent versions were not reviewed." [Future of plugins, 2026-05-12].

**Since 2026-05-12 (current):** a hosted directory, community.obsidian.md, with a developer dashboard. Submission (Submit your plugin, 2026-10-06): sign in with an Obsidian account, "Link your GitHub account to your profile", "Add your plugin to the directory"; "Upon submission your project will be immediately reviewed. Typically, you will see the results of your review within a few minutes." and "If your project passes, it will be available to search and download in the app within 24 hours." Ownership: "Currently only the owner of a GitHub repo can edit it in Obsidian Community. Organization repos can be claimed and edited if you have a public membership to the organization."; "Logging in via GitHub shares your username and list of public repositories. It is only used to verify ownership of your repository." Migration: "All existing plugins, themes, and queued submissions added via GitHub have been automatically migrated to the new site."

The Git repo is now a **mirror** of the hosted directory, not the source of truth. Evidence (shallow clone, 2026-10-06): `.github/workflows/mirror-community-json.yml` runs hourly (`cron: "17 * * * *"`), downloads `https://community.obsidian.md/assets/community-plugins.json` and `community-themes.json`, validates shape and a retention floor ("New file must retain at least this fraction of the current file's entry count", 95 %, absolute floor 1500 plugins), formats with prettier and commits as "Obsidian Bot" ("chore: Mirror community plugins and themes (-0/+9 plugins, -0/+0 themes)"); `.github/workflows/plugin-stat.yml` does the same daily for `community-plugin-stats.json` from `https://community.obsidian.md/assets/community-plugin-stats.json`. The last 50 commits are all such bot commits plus two staff commits ("Update public to v1.14.4.", "Remove Borozdov themes"); none is a merged pull request. Old clients (and BRAT) keep working because the file keeps its exact shape (inferred).

**What the automated review checks** (Future of plugins, 2026-05-12): "Every version is now automatically checked for code quality and security vulnerabilities. This includes malware scanning to detect potentially malicious additions to plugins. Developers can see detailed suggestions, warnings, and failure flags for every project in the developer dashboard." Local pre-check: "Use our eslint plugin to check your Obsidian plugin against the official developer guidelines locally." (the eslint plugin's README could not be fetched at the guessed path: not confirmed). The Plugin guidelines page is "common issues identified during plugin submission reviews" and includes concrete security rules such as avoiding `innerHTML`, `outerHTML`, `insertAdjacentHTML` with user input [Plugin guidelines, 2026-10-06].

**What "reviewed" now means:** passing the automated scan, with human review reserved: "Importantly, manual reviews will continue. The new system allows us to shift our efforts towards plugins that require deeper inspection such as popular plugins, featured plugins, and issues flagged by the community." Every project page shows a *scorecard*: "Users and developers can see the status of automated checks with scorecards on every project. These scorecards will continue to improve as we incorporate disclosures, privacy labels, artifact attestation, manual review results, and adoption of app capabilities." The blog admits "Scorecards are new and can contain errors. You may find false positives and false negatives." On a live page (community.obsidian.md/plugins/obsidian-git, 2026-10-06) the scorecard shows only two words, "Health: Excellent" and "Review: Caution", with no explanation; a docs page defining them was not found (not confirmed). Labels: "paid plugins and official integrations" exist now ("Only Obsidian staff can update the Official label"); "Verified authors. Labels will be added for trusted developers that have passed additional verification steps and are in good standing." is announced, not shipped.

**Queue and throughput:** "With the new system we were able to process over 2,300 queued submissions in the last few days." [Future of plugins, 2026-05-12]. Directory size on 2026-10-06: 8,468 plugins in the mirrored JSON; the site front page said 8,470 plugins and 833 themes.

**Cost to run:** the legacy design cost a GitHub repo, a validation bot and staff review time; the new design is a hosted web app with login, GitHub OAuth, a scanner pipeline and a stats endpoint (Obsidian is a commercial company; no cost figures are published: not confirmed).

## 7. Trust and permissions

- No permissions exist and the docs say so: "Due to technical limitations, Obsidian cannot reliably restrict plugins to specific permissions or access levels." Users are told exactly what a plugin can do: "Community plugins can access files on your computer", "Community plugins can connect to internet", "Community plugins can install additional programs." [Plugin security help, 2026-10-06].
- Restricted mode is the only enforced control, and it is binary and on by default: "By default, Obsidian runs in Restricted Mode to prevent third-party code execution."; turning it off is Settings → Community plugins → "Turn on community plugins".
- What review covers: "Obsidian automatically scans every plugin version for security vulnerabilities, code quality issues, and malware." and "Manual reviews continue for popular, featured, and flagged plugins." Advice: "If you're working with sensitive data and wish to install a community plugin, we recommend that you perform an independent security audit on the plugin before using it." Reporting: flag from the directory ("Report plugin" link on each page), Obsidian support, or moderators.
- Policy-level trust (Developer policies, 2026-10-06), not enforced by the runtime but checked by review: must not "Obfuscate code to hide its purpose", "Insert dynamic ads that are loaded over the internet", "Include client-side telemetry", "Install or update themselves or their dependencies"; must disclose in the README "Payment is required for full access", "An account is required for full access", "Network use. Clearly explain which remote services are used and why", "Accessing files outside of Obsidian vaults", "Server-side telemetry" (with privacy policy), closed-source code (case by case). Forks: "Forks are not allowed in the Community directory unless they meet one of the following criteria" (written approval from the original author, or the author unreachable for 6+ months with a 30-day notice).
- Payment labels, not payments (Future of plugins FAQ): "Free means the plugin does not have any form payment and is not tied to any paid services whatsoever. Donation links and sponsorship links are acceptable for Free plugins."; "Optional payments means users may optionally pay to unlock additional features or the plugin connects to paid services. If a plugin connects to a paid service or API, it must be labeled as having Optional payments, even if the service has a free tier."; "Paid means users must pay to use its primary features, even if it offers a free trial." Developers set tags and pricing in the dashboard; `fundingUrl` is the only money-related manifest field and links out (e.g. BRAT's `"fundingUrl": { "Visit my site": "https://tfthacker.com" }`).
- "Verified" today means repository ownership proven through GitHub login; the "Verified authors" badge is future work.

## 8. Models

Not applicable: Obsidian has no model concept; AI plugins bring their own API clients or local runtimes. The directory only labels them (an "AI" category on the site; "Optional payments" if they call paid APIs).

## 9. Local, remote or both

Local execution inside the app (desktop and mobile; `isDesktopOnly` gates Node/Electron use). Distribution is remote via GitHub; the directory JSON and stats are served by community.obsidian.md and mirrored to GitHub.

## 10. Known incidents and stated limitations

- Review backlog: 2,300+ queued submissions and "subsequent versions were not reviewed" until 2026-05-12 (vendor blog). This is the central stated limitation of the old process.
- Automated review is new and imperfect by the vendor's own account ("false positives and false negatives"); older plugins failing checks run on exceptions.
- No sandbox, no permissions, binary Restricted mode (vendor help page).
- Scorecard semantics undocumented as of 2026-10-06 (not confirmed).
- Specific malware incidents in the Obsidian directory: none found in the official sources read; not confirmed either way.

## 11. Borrow

- The install contract: artefacts are release assets on the developer's own GitHub repo, the release tag must equal the manifest version, the manifest at HEAD is only used to find the latest version, and a `versions.json` map resolves the newest version compatible with the running host. This is H3's "pinned tag" model in production use for years, and BRAT shows the same mechanics work for any repo, any release, including a frozen version and pre-releases.
- A 5-field index entry (`id`, `name`, `author`, `description`, `repo`) is enough for search; everything else is read from the repo at display time. Clips Kitty's index can stay that small and let CI add stars/last-updated/hash.
- A mirror-style index with a retention floor (reject a new index that lost >5 % of entries) is a cheap guard against a broken build wiping the catalogue.
- The `fundingUrl` object (several labelled links) plus three honest labels (Free / Optional payments / Paid, with the rule that any paid API means "Optional payments" even if it has a free tier).
- Developer policies that review can actually check: no obfuscation, no client-side telemetry, no self-update, disclose network use and out-of-vault file access in the README, and a fork policy that protects original authors (6 months unreachable + 30-day notice).
- Plain-language security page: list what plugins *can* do, say what review does and does not cover, recommend an audit for sensitive data, and give a one-click report path.
- Scan every version, not just the first submission; publish the result as a per-plugin scorecard; show results to developers within minutes; keep human review for popular/featured/flagged.
- `isDesktopOnly`-style capability flags that the host does enforce (the app and BRAT refuse to install a desktop-only plugin on mobile) are a model for Clips Kitty's "requires GPU / requires remote" flags.

## 12. Avoid

- Hand review as the gate: Obsidian's own numbers (2,300+ queued, later versions unreviewed) are the H4 evidence. If a human gate exists, make it the exception (featured/popular/flagged), not the path.
- Flat, global ids with no namespace: Obsidian needs uniqueness rules and bans words in ids; `publisher/name` ids avoid the squatting problem from day one (H8).
- Calling a scorecard "Review" without a legend; users on 2026-10-06 see "Review: Caution" on the most popular Git plugin with no explanation. Every status Clips Kitty shows must link to what was checked.
- Announcing "Verified authors" before the process exists; Clips Kitty's fixed decision says "Verified" only when a real process exists.
- A binary Restricted mode as the only control: Clips Kitty's out-of-process plugins over the local API (H1) can do better (per-plugin enable/disable, host-enforced flags), but must not claim more than the runtime enforces (H5).
- Letting the index be the only source of install counts without saying where they come from (section on H10).

## 13. Sources (all read 2026-10-06)

- https://docs.obsidian.md/Plugins/Releasing/Submit+your+plugin — loaded (current flow via community.obsidian.md; release rules).
- https://docs.obsidian.md/Reference/Manifest — loaded.
- https://docs.obsidian.md/Reference/Versions — loaded (versions.json).
- https://docs.obsidian.md/Plugins/Releasing/Plugin+guidelines — loaded.
- https://docs.obsidian.md/Plugins/Releasing/Beta-testing+plugins — loaded (BRAT recommendation).
- https://docs.obsidian.md/Community+directory/Developer+policies — loaded (the brief's URL https://docs.obsidian.md/Developer+policies returned "File Developer policies.md does not exist"; the page moved).
- https://docs.obsidian.md/Community+directory/Submission+requirements+for+plugins — loaded (the path under /Plugins/Releasing/ returned "does not exist").
- https://docs.obsidian.md/Community+directory and https://docs.obsidian.md/Community+directory/Scorecards — not confirmed (both "does not exist").
- https://obsidian.md/blog/future-of-plugins/ — loaded (dated May 12, 2026; quotes in sections 5–7).
- https://obsidian.md/help/plugin-security — loaded (https://help.obsidian.md/plugin-security redirects here).
- https://github.com/obsidianmd/obsidian-releases — read via shallow clone (README, `.github/pull_request_template.md`, `.github/workflows/mirror-community-json.yml`, `.github/workflows/plugin-stat.yml`, last 50 commits) and via raw.githubusercontent.com (`README.md`, `community-plugins.json`, `community-plugin-stats.json`). The GitHub REST API for this repo was blocked by the session proxy; the clone and raw files were used instead.
- https://community.obsidian.md — loaded (front page: 8,470 plugins, 833 themes; mostly a JS app).
- https://community.obsidian.md/plugins/obsidian-git — loaded (scorecard "Health: Excellent", "Review: Caution"; 3.2M downloads; "Obsidian 1.13.0+"; "Report plugin"; `obsidian://` install link).
- https://github.com/TfTHacker/obsidian42-brat — read via shallow clone (`manifest.json`, `src/features/githubUtils.ts`, `src/features/BetaPlugins.ts`, `src/settings.ts`, `src/ui/SettingsTab.ts`); https://tfthacker.com/BRAT loaded but is only an overview page.
- https://raw.githubusercontent.com/obsidianmd/eslint-plugin/main/README.md — not confirmed (404; the eslint plugin's repository path was guessed).
- https://docs.github.com/en/rest/releases/assets — loaded (release asset schema includes `download_count`, `browser_download_url`, `size`, `content_type`, `state`).

## Matrix row

`| Obsidian | Plugin (main.js + manifest.json + styles.css in the app renderer) | None | None (commands/views; no pipeline concept) | Hosted directory community.obsidian.md since 2026-05-12 with automated per-version review; community-plugins.json (5 fields) now an hourly Git mirror | Yes: install straight from the developer's GitHub release whose tag == manifest.version; index was Git-by-PR until 2026 | Yes | No (distribution only) | SemVer x.y.z; minAppVersion; versions.json compat map; releases immutable; no official downgrade UI (BRAT frozen versions) | No sandbox, no permissions ("cannot reliably restrict plugins"); Restricted mode default-on; every version scanned; manual review for popular/featured/flagged; scorecards; policies (no obfuscation, no client telemetry, no self-update, disclosures) | Free; fundingUrl links; Free / Optional payments / Paid labels; no payment rail | Copy the release-asset install contract, versions.json, the 5-field index and the funding/labels model; scan every version automatically; keep humans for exceptions; document every status |`

## Hypotheses

- **H3 (static Git registry, PR-submitted, CI index, pinned tag, no backend):** Supported for install and discovery, with a caveat on review. Obsidian ran for years on one JSON file in a Git repo, PRs with a template and a bot, and installs from GitHub releases at `tag == manifest.version`; the app still reads that JSON and BRAT installs from any repo without a registry. Hosting cost nothing. What failed was the review step, not the index, and Obsidian's 2026 answer was a hosted directory plus automated scanning; the Git file survives as a mirror with a retention guard. Clips Kitty can keep the static index if the review/scan step is automated in CI.
- **H4 (hand review does not scale):** Confirmed by the vendor: "we struggled to keep pace with submissions, and subsequent versions were not reviewed"; "over 2,300 queued submissions" cleared only after automation; manual review now reserved for popular, featured and flagged plugins.
- **H5 (declared is not enforced):** Confirmed: "Obsidian cannot reliably restrict plugins to specific permissions or access levels." The only enforced flags are Restricted mode (all or nothing) and `isDesktopOnly` (platform gate). Disclosures (network, files outside the vault, telemetry) are README text checked by review, not by the runtime.
- **H8 (namespaced ids):** Counter-example. Obsidian ids are flat and global, which forces word bans and uniqueness checks; the fork policy exists partly because competing copies collide in one namespace (inferred).
- **H10 (install counts need telemetry; stars/updated from GitHub; opt-in):** Refined. Obsidian publishes `community-plugin-stats.json` with `downloads`, `updated` and per-version counts (per-version keys include non-semver tags such as `v1.3.1`, which suggests the counts are taken from GitHub release downloads rather than app telemetry: inferred, not confirmed), and the policies ban client-side telemetry in plugins. GitHub's Releases API exposes `download_count` per release asset, so a GitHub-first index can estimate installs at build time without any client telemetry; those counts are noisy (bots, CI, re-downloads) and should be labelled "release downloads", not "installs".
- **H1, H2, H6, H7, H9:** not spoken to (in-process JS plugins, no dependency manager, no models, no pipeline contract).
