# VS Code + Open VSX — research notes

- Platform: Visual Studio Code extensions, distributed through the Visual Studio Marketplace (Microsoft) and the Open VSX Registry (Eclipse Foundation)
- Tier: 2 (Track C: what makes an extension ecosystem successful, and what mistakes should Clips Kitty avoid?)
- Date read: 2026-10-06
- Question for this platform: how a mature, central-registry ecosystem handles manifests, runtime security, verification, signing, kill switches, version pinning and pre-releases, and what the 2025 Open VSX incidents teach about registries that run CI over third-party code.

**Verdict (two lines):** VS Code's manifest, `publisher.name` identifiers, `engines` compatibility, pre-release channel, "Install Another Version" and extension packs are the reference design for a plugin manager; its security model is "no sandbox, trust the publisher, scan at publication, sign the package, kill switch after the fact", and the docs say so plainly.
Open VSX proves the same catalogue can be run by a non-profit for $0 to publishers, but its 2025 incidents (CI auto-publish token exposure, CVE-2025-6705; leaked publisher tokens and GlassWorm) show that a registry whose automation runs third-party code or holds long-lived publish tokens is the weakest link, which is exactly the trap a CI-built Git index must avoid.

## 1. Unit of extension

A VS Code *extension*: a Node.js (or web) package with a `package.json` manifest, packaged as a `.vsix` archive, identified as `${publisher}.${name}` ("The id of an extension is always `${publisher}.${name}`. For example: `vscode.csharp`.") [manifest ref, 2026-10-06]. Extensions are loaded by the *extension host* ("The extension host is responsible for running extensions in VS Code" [runtime security page, 2026-10-06]); an *Extension Pack* is an extension whose only job is to list other extensions to install together [manifest ref, 2026-10-06].

## 2. Manifest or metadata format

File: `package.json` (npm manifest plus VS Code fields). From the Extension Manifest reference (read 2026-10-06):

| Field | Required | Documented meaning (quoted) |
|---|---|---|
| `name` | Y | "The name of the extension - should be all lowercase with no spaces. The name must be unique to the Marketplace." |
| `version` | Y | "SemVer compatible version." |
| `publisher` | Y | "The publisher identifier" |
| `engines` | Y | "An object containing at least the `vscode` key matching the versions of VS Code that the extension is compatible with. Cannot be `*`. For example: `^0.10.5` indicates compatibility with a minimum VS Code version of `0.10.5`." |
| `license` | N | "If you do have a `LICENSE` file in the root of your extension, the value for `license` should be `'SEE LICENSE IN <filename>'`." |
| `displayName` | N | "must be unique to the Marketplace" |
| `categories` | N | fixed allowed list (Programming Languages, Snippets, Linters, Themes, Debuggers, Formatters, Keymaps, SCM Providers, Other, Extension Packs, Language Packs, Data Science, Machine Learning, Visualization, Notebooks, Education, Testing) |
| `keywords` | N | "currently limited to 30 keywords" |
| `preview` | N | "Sets the extension to be flagged as a Preview in the Marketplace." |
| `main` / `browser` | N | entry point (Node / web) |
| `contributes` | N | "An object describing the extension's contributions." (commands, settings, views, languages, etc.) |
| `activationEvents` | N | "An array of the activation events for this extension." |
| `sponsor` | N | "an object with a single property `url`, which links to a page where users can sponsor your extension" |
| `extensionPack` | N | "An array with the ids of extensions that can be installed together." |
| `extensionDependencies` | N | "An array with the ids of extensions that this extension depends on." |
| `extensionKind` | N | where to run in remote setups: `ui` (local), `workspace` (remote), or both, ordered by preference |
| `scripts` | N | npm scripts plus `vscode:prepublish` and `vscode:uninstall` |
| `icon` | N | PNG "of at least 128x128 pixels (256x256 for Retina screens)" |
| `pricing` | N | "Allowed values: `Free`, `Trial`. Default: `Free`." |
| `capabilities` | N | "An object describing the extension's capabilities in limited workspaces: `untrustedWorkspaces`, `virtualWorkspaces`." |
| `badges`, `galleryBanner`, `markdown`, `qna` | N | Marketplace presentation |

`enabledApiProposals` is not in the manifest field table as read; it is documented on the "Using Proposed API" page: add `"enabledApiProposals": ["<proposalName>"]` to `package.json`, and "While you should not publish extensions using the proposed API on the Marketplace, you can still share your extension with your peers by packaging and sharing your extension." Proposed APIs are "a set of unstable APIs that are implemented in VS Code but not exposed to the public as stable APIs does"; they need Insiders and `enable-proposed-api` in `argv.json` [proposed API page, 2026-10-06].

Activation events (reference page, 2026-10-06): 25 events are listed (`onLanguage`, `onCommand`, `onDebug`, `workspaceContains`, `onFileSystem`, `onView`, `onUri`, `onWebviewPanel`, `onCustomEditor`, `onAuthenticationRequest`, `onStartupFinished`, `onTaskType`, `onEditSession`, `onSearch`, `onOpenExternalUri`, `onNotebook`, `onRenderer`, `onTerminal`, `onTerminalProfile`, `onTerminalShellIntegration`, `onWalkthrough`, `onIssueReporterOpened`, `onChatParticipant`, `onLanguageModelTool`, `*`). Since 1.74 most are derived from `contributes`: "Beginning with VS Code 1.74.0, commands contributed by your extension do not require a corresponding `onCommand` activation event declaration for your extension to be activated." The wildcard is discouraged: "please use this activation event in your extension only when no other activation events combination works."

Workspace Trust declaration (`capabilities.untrustedWorkspaces`, extension guide, 2026-10-06): `supported: true` ("fully supported in Restricted Mode as it does not need Workspace Trust"), `false` ("cannot function without Workspace Trust"), or `'limited'` ("Some features of the extension are supported in Restricted Mode. Trust-sensitive features should be disabled until Workspace Trust is granted."), with `restrictedConfigurations` ("an array of configuration setting IDs" whose workspace values the extension is prevented from reading) and a `description`. Extensions that do not declare it "will be disabled when a workspace is in Restricted Mode".

Open VSX reads the same `package.json`; the `publisher` field is the Open VSX *namespace* ("The `publisher` field of your extension's package.json defines the namespace into which the extension will be published.") [ovsx CLI README, 2026-10-06]. Open VSX additionally requires a license: "All extensions must be licensed", via "a license expression in the package.json manifest"; non-OSI licenses are allowed ("You may use a license that is not recognized as an open source license by the Open Source Initiative.") [Eclipse Open VSX FAQ, 2026-10-06].

## 3. Distribution and install

- Package: `vsce package` produces a `.vsix` ("This command creates a `.vsix` file in your extension's root folder. For example, `my-extension-0.0.1.vsix`."); `vsce publish` uploads it. Install from a file: "Select **Install from VSIX** in the Extensions view command dropdown" or `code --install-extension my-extension-0.0.1.vsix` [publishing page; marketplace page, 2026-10-06].
- Marketplace publishing needs a publisher ID (unique, unchangeable) and an Azure DevOps PAT with the Marketplace **Manage** scope; the docs warn "On December 1, 2026, global Personal Access Tokens (PATs) in Azure DevOps are retired." [publishing page, 2026-10-06].
- Platform-specific packages: `vsce publish --target win32-x64 win32-arm64`; targets are `win32-x64`, `win32-arm64`, `linux-x64`, `linux-arm64`, `linux-armhf`, `alpine-x64`, `alpine-arm64`, `darwin-x64`, `darwin-arm64`, `web` [publishing page, 2026-10-06].
- Open VSX publishing (wiki "Publishing Extensions", 2026-10-06): create an Eclipse account whose GitHub username matches; log in to open-vsx.org with GitHub; sign the Eclipse Foundation Open VSX Publisher Agreement; create an access token (shown once); `npx ovsx create-namespace <name> -p <token>`; `npx ovsx publish <file>.vsix -p <token>` (or from the extension root). Alternative: a community GitHub Action (HaaLeo/publish-vscode-extension) [wiki, 2026-10-06].
- Open VSX trusted publishing (wiki "Trusted Publishing"; CLI README; PR eclipse-openvsx/openvsx#1980 merged 2026-07-27; all read 2026-10-06): a namespace *owner* with a signed agreement registers provider + owner + repo + workflow file (+ optional environment); "The registry resolves these names to numeric IDs during registration, ensuring that renamed or re-created repositories don't accidentally match registrations"; CI exchanges an OIDC ID token at `POST /api/-/trusted-publishing/token`; the issued token "expires within minutes (five by default)" per the wiki (the PR text says "default 15 min"; the two sources disagree, so the exact default is not confirmed), is scoped to one extension, and "Versions published this way are marked on the extension page: a rocket icon next to _Published by_". `npx ovsx publish --trusted-publishing` with `id-token: write`. The public API exposes `publishedWithTrustedPublishing` per extension [open-vsx.org/api/redhat/java, 2026-10-06].
- Runs at install time: nothing from the extension itself (no install hooks are documented); extensions run at *activation* in the extension host. `vscode:uninstall` is a documented script hook on uninstall [manifest ref, 2026-10-06].
- Workspace recommendations: `.vscode/extensions.json` with `{"recommendations": ["dbaeumer.vscode-eslint", "esbenp.prettier-vscode"]}` [marketplace page, 2026-10-06].

## 4. Dependencies and isolation

- All extensions share one extension host process with VS Code's own privileges; there is no per-extension sandbox: "The extension host has the same permissions as VS Code itself. This means that any action that VS Code can perform, an extension can also perform through the extension host." Examples given: "an extension can read and write files on your machine, make network requests, run external processes, and modify workspace settings." [runtime security page, 2026-10-06].
- Node dependencies are bundled inside each `.vsix` (`dependencies` field; "Any runtime Node.js dependencies your extensions needs"), so there is no shared package environment and no cross-extension dependency conflict at the npm level [manifest ref, 2026-10-06].
- Extension-to-extension dependencies are declared with `extensionDependencies` (installed with the extension) and `extensionPack` (installed together; "An Extension Pack should not have any functional dependencies with its bundled extensions and the bundled extensions should be manageable independent of the pack.") [manifest ref, 2026-10-06]. Trusting a pack's publisher trusts the dependents' publishers too: "When you trust the publisher of an extension pack or an extension with dependencies on other extensions, you are also trusting the publishers of the dependent extensions." [runtime security, 2026-10-06].

## 5. Versioning and updates

- Versions are SemVer; `engines.vscode` is the compatibility range and "Cannot be `*`" [manifest ref, 2026-10-06].
- Pinning/rollback: "If you want to install a specific version of an extension, right-click the extension and select **Install Another Version**. You can then select a version from the available list." Auto-update is global or per extension ("toggling the **Auto Update** item"), with `extensions.autoUpdateDelay` (default 12 hours) [marketplace page, 2026-10-06].
- Pre-release channel: `vsce publish --pre-release`; convention "major.EVEN_NUMBER.patch for release versions and major.ODD_NUMBER.patch for pre-release versions"; requires `engines.vscode` >= 1.63.0; users pick "Install Pre-Release Version" from the Install dropdown [publishing page; marketplace page, 2026-10-06].
- Unpublish vs remove vs deprecate (publishing page, 2026-10-06): unpublishing keeps statistics; removing is irreversible and "Once an extension is removed, its extension name is permanently reserved and cannot be reused, even by the original publisher."; deprecation is requested in a GitHub discussion thread and "The deprecated extension will be rendered with a dimmed strike-through text in the UI".
- Open VSX keeps every published version addressable (`/api/{ns}/{ext}/versions`; redhat/java had `totalSize: 664` versions on 2026-10-06), with `deprecated`, `preRelease` and `preview` flags and `ovsx unpublish` for members of the namespace (owners any version; contributors only their own; registry >= 1.2.0) [API sample; CLI README, 2026-10-06].

## 6. Registry design

- Visual Studio Marketplace: a Microsoft-run central service. Submission = `vsce publish` with a PAT; there is no human pre-review, but every package is scanned before it goes live: "The scan, which uses several antivirus engines, is run for each new extension and for each extension update. Until the scan is all clear, the extension won't be published in the Marketplace for public usage." plus "dynamic detection by verifying the extension's runtime behavior by running it in a sandboxed environment (clean room VM)" and secret scanning that blocks publication [runtime security, 2026-10-06]. Reports get "an initial response within one business day".
- Open VSX: a central service run by "the Eclipse Foundation's Open VSX Working Group"; "There are no fees associated with publishing or consuming content from the Open VSX Registry." [Eclipse FAQ, 2026-10-06]. Submission = publisher agreement + token + `ovsx publish`. Publish-time checks (wiki "Extension Scanning", 2026-10-06): secret detection ("If secrets are found and the check is enforced, publishing is rejected"), blocklist check ("Compares file hashes against known-bad files"), namespace similarity ("Prevents typosquatting by detecting names too similar to existing extensions"); "Each check is configured by the registry operator. Some may be enabled in 'monitor-only' mode (findings are recorded but publishing proceeds), while others may block publishing outright."; false positives can be marked `// secret-detector:ignore`.
- What it costs to run (wiki "Deploying Open VSX", 2026-10-06): a Spring Boot server (`openvsx-server` image), "a PostgreSQL instance", "Elasticsearch is used as default search engine", file storage ("From version 0.18.0 files are stored on the local file system ... Currently Azure Blob Storage and Google Cloud Storage are supported as external storage providers."), GitHub OAuth, and an `application.yml`; "Open VSX does not provide any facility to deploy the other components (database, search engine etc.)". This is a real backend with a database, search cluster and blob storage, funded by Working Group members (Mikaël Barbero names "AWS, Google, Cursor, and the Alpha Omega open source cybersecurity project" as partners, 2026-03-30 article).
- Open VSX also has a Git-index-by-PR side door: the `publish-extensions` repository, whose nightly CI auto-publishes open-source extensions on behalf of authors. This is the component behind CVE-2025-6705 (section 10).
- Public API (read 2026-10-06): `GET /api/{namespace}/{extension}` returns `verified`, `downloadCount`, `averageRating`, `reviewCount`, `engines`, `dependencies`, `bundledExtensions`, `extensionKind`, `license`, `preRelease`, `deprecated`, `publishedWithTrustedPublishing`, `sponsorLink`, `targetPlatform`, `allVersions`, and a `files` map with `download` (.vsix), `sha256`, `signature` (.sigzip) and `publicKey`; `GET /api/-/search?query=...` for discovery.

## 7. Trust and permissions

- Tiers in practice: (a) any publisher (after the 1.97 dialog: "when you first install an extension from a third-party publisher, VS Code shows a dialog prompting you to confirm that you trust the publisher of that extension"); (b) *Verified Publisher*: "The check mark indicates that the publisher has proven domain-name ownership to the Marketplace. It also shows that the Marketplace has verified both the existence of the domain name and the good standing of the publisher on the Marketplace for at least six months." Requirements: "A publisher must have one or more extensions on the VS Marketplace for a minimum of 6 months, and the registration of the domain must also be at least 6 months old."; DNS TXT record; "within 5 business days"; "Any changes to the publisher display name will revoke the verified badge." [runtime security; publishing page, 2026-10-06]. Verified says *who*, not *what the code does*.
- Open VSX "verified" means namespace ownership plus membership, nothing about the code: since 2020-12-17 "only members of a namespace have the authority to publish"; "When you create a namespace, you are assigned as _contributor_ ... Initially the namespace has no owner, therefore it is regarded as _unverified_."; ownership is claimed "publicly by creating an issue in github.com/EclipseFdn/open-vsx.org/issues/new/choose. By this the act of granting ownership is totally transparent."; an extension is verified when "its namespace is verified and its publishing user is a member of the namespace" (shield icon vs warning icon) [wiki Namespace Access, 2026-10-06]. (The ovsx CLI README still says "Initially, everyone will be able to publish an extension with the new namespace", which contradicts the wiki's 2020 change; which text is current is not confirmed.)
- Declared permissions: none. VS Code has no permission manifest; the only declarations are `capabilities.untrustedWorkspaces` / `virtualWorkspaces`, and those are partly host-enforced (VS Code disables undeclared extensions in Restricted Mode and withholds `restrictedConfigurations` values) and partly honour-system (`'limited'` relies on the extension disabling its own trust-sensitive features) [Workspace Trust guide, 2026-10-06] (inferred split).
- Integrity: "The Visual Studio Marketplace signs all extensions when they're published. VS Code checks this signature when you install an extension to verify the integrity and the source of the extension package." Failure shows "Cannot install extension because Visual Studio Code cannot verify the extension signature" [runtime security; marketplace page, 2026-10-06]. Open VSX signs too, with a per-registry key: `ovsx verify <extension.vsix>` "checks a downloaded `.vsix` file's signature against the registry's public key - the same check VS Code itself performs", and "Open VSX registries each hold their own signing key, rather than trusting a single baked-in Marketplace key" [CLI README, 2026-10-06].
- Kill switch: "If a malicious extension is reported and verified, or a vulnerability is found in an extension dependency, the extension is removed from the Marketplace and added to a block list. If the extension has been installed, it's automatically uninstalled by VS Code." [runtime security, 2026-10-06].
- Users are told the truth: the page's headline fact is that extensions are not sandboxed, and Workspace Trust is offered as the mitigation ("decide whether code in a project folder can be executed by VS Code and extensions without explicit approval").

## 8. Models

Not applicable: neither registry has a model concept; ML-related extensions bundle or download what they need themselves. The `categories` list includes "Data Science" and "Machine Learning" as labels only [manifest ref, 2026-10-06].

## 9. Local, remote or both

Both. Extensions run locally in the extension host; in remote setups `extensionKind` chooses `ui` (local) or `workspace` (remote machine) [manifest ref, 2026-10-06]. The registry itself is remote; VSIX install is the offline path.

## 10. Known incidents and stated limitations

1. **CVE-2025-6705, Open VSX auto-publish CI (vendor advisory, Eclipse blog 2025-07-02; read 2026-10-06).** The `publish-extensions` automation "lacked proper build isolation", exposing a privileged token that allowed "publishing of new extension versions under any namespace" (not deletion or admin). Reported by Koi Security 2025-05-04; confirmed and initial fix 2025-05-05..17; fix deployed and token rotated 2025-06-24; CVE 2025-06-27. "No evidence of compromise was found"; 81 extension versions across 65 extensions were proactively deactivated and manually reviewed. Press (The Hacker News, June 2025; Koi's own post now redirects to Palo Alto Networks and could not be read): the nightly workflow ran `npm install` on the auto-published extensions, and "npm install runs the arbitrary build scripts of all the auto-published extensions, and their dependencies, while providing them with access to the OVSX_PAT environment variable" (Oren Yomtov, Koi, quoted by press).
2. **Leaked publisher tokens and GlassWorm, October 2025 (Eclipse blog by Mikaël Barbero, 2025-10-27; read 2026-10-06).** Wiz reported "several extension publishing tokens inadvertently exposed by developers within public repositories"; "a small number of tokens had been leaked ... These exposures were caused by developer mistakes, not a compromise of the Open VSX infrastructure. All affected tokens were revoked immediately once identified."; "we introduced a token prefix format in collaboration with MSRC to enable easier and more accurate scanning for exposed tokens across public repositories" (the prefix string itself is not stated: not confirmed). Koi Security reported GlassWorm, which "leveraged some of these leaked tokens to publish malicious extensions"; Eclipse says "this was not a self-replicating worm in the traditional sense. The malware in question was designed to steal developer credentials"; "the reported download count of 35,800 overstates the actual number of affected users, as it includes inflated downloads generated by bots and visibility-boosting tactics used by the threat actors."; contained "As of October 21, 2025". Announced changes: "Token lifetime limits: All tokens will have shorter validity periods by default", "Simplified revocation", "Security scanning at publication: Automated scanning of extensions will now occur at the time of publication". Press (The Hacker News, Nov 2025) reports a second wave hiding code in invisible Unicode characters, naming `ai-driven-dev.ai-driven-dev`, `adhamu.history-in-sublime-merge`, `yasuyuky.transient-emacs`.
3. **2026 follow-through** (Mikaël Barbero, Eclipse Head of Security, on Security Boulevard 2026-03-30; press venue, author is the vendor): "similarity checks on extension names and namespaces", "secret scanning", "malware-oriented scanning", "Release automation now uses more trusted publishing patterns, reducing reliance on long-lived credentials", "scanning workflows, administrative visibility, and support for asynchronous external scanners". Trusted publishing landed in the server on 2026-07-27 (PR #1980).
4. **Stated limitations (VS Code docs):** no sandbox; the Marketplace's dynamic detection and block list act after publication or after a report; Workspace Trust protects against workspace *content*, not against a malicious extension.

## 11. Borrow

- `publisher/name` identifiers with a required, non-wildcard host compatibility range (`engines.vscode`-style `engines.clipskitty`), SemVer versions, `extensionDependencies` and `extensionPack` semantics (a pack is metadata only; dependents must stay independently manageable). Supports H8.
- A manifest-declared `sponsor.url` (VS Code) and `pricing: Free|Trial` as labels: donations and labels without a payment rail, which matches the $0 / no-payments decision.
- The user-facing version controls: "Install Another Version", per-plugin auto-update toggle, an update delay, and a pre-release channel with a documented numbering convention.
- The honesty of the security page: say "plugins run with the app's own access" in the first paragraph, offer the real mitigation, and name what scanning does and does not catch.
- Post-publication kill switch: a block list the client consults and acts on (auto-disable or uninstall, with a notice). Cheap to add to a static index (a `blocklist.json` with ids and version ranges) and the single most useful response tool both registries rely on.
- Package signing with a registry key and a published `sha256`: a static index can still publish the hash of the pinned ref's archive at index-build time.
- Verification that states exactly what it proves (domain or repository ownership, time in good standing) and nothing more; Open VSX's public ownership claim via a GitHub issue is a zero-cost, auditable process a Git index can copy.
- Trusted publishing: if Clips Kitty ever accepts uploads rather than Git refs, prefer OIDC from the developer's CI over long-lived tokens.

## 12. Avoid

- Never execute plugin code (or `npm install`/`pip install` of plugin dependencies) inside the index-build CI, and never give that CI a secret that can publish or rewrite entries: CVE-2025-6705 is the exact failure. The index build should read metadata only and sign/hash what it reads.
- Long-lived publish tokens without prefix, expiry or easy revocation (Open VSX, October 2025).
- Claiming a "sandbox" or "permissions" the runtime does not enforce; VS Code's own docs avoid that, and `capabilities.untrustedWorkspaces: 'limited'` shows how a half-enforced declaration reads: the host enforces what it can and the rest is the extension's promise. Label such fields "declared", not "enforced" (H5).
- Treating download counts as trust signals: Eclipse itself says bots inflated GlassWorm's counts (H10 caution).
- Trusting a pack's publisher transitively for its dependencies without saying so; VS Code states it, Clips Kitty should show it in the install dialog.
- Relying on a 6-month-plus verification waiting period as the only trust tier: it is honest but leaves new, legitimate developers unverified for half a year; pair it with a cheaper "repository ownership proven" tier.

## 13. Sources (all read 2026-10-06)

- https://code.visualstudio.com/api/references/extension-manifest — loaded; field table quoted above.
- https://code.visualstudio.com/docs/configure/extensions/extension-runtime-security — loaded; sandbox, scanning, verified publisher, signing, block list, Workspace Trust quotes.
- https://code.visualstudio.com/api/working-with-extensions/publishing-extension — loaded; vsce, VSIX, PAT, verified publisher, pre-release, targets, unpublish/remove/deprecate, pricing.
- https://code.visualstudio.com/docs/configure/extensions/extension-marketplace — loaded; Install Another Version, auto-update, pre-release install, VSIX install, extensions.json, signature error.
- https://code.visualstudio.com/api/references/activation-events — loaded.
- https://code.visualstudio.com/api/advanced-topics/using-proposed-api — loaded.
- https://code.visualstudio.com/api/extension-guides/workspace-trust — loaded.
- https://www.eclipse.org/legal/open-vsx-registry-faq/ — loaded; working group, publisher agreement, license requirement, no fees.
- https://github.com/eclipse/openvsx/wiki — loaded (page list: Publishing Extensions, Namespace Access, Deleting Extensions, Trusted Publishing, Using Open VSX in VS Code, Deploying Open VSX, Extension Scanning, Registry API, Registry Changes Feed, NGINX HTTPS Configuration).
- https://github.com/eclipse/openvsx/wiki/Publishing-Extensions — loaded.
- https://github.com/eclipse/openvsx/wiki/Namespace-Access — loaded.
- https://github.com/eclipse/openvsx/wiki/Extension-Scanning — loaded.
- https://github.com/eclipse/openvsx/wiki/Trusted-Publishing — loaded.
- https://github.com/eclipse/openvsx/wiki/Deploying-Open-VSX — loaded.
- https://raw.githubusercontent.com/eclipse-openvsx/openvsx/master/cli/README.md — loaded (ovsx CLI: tokens, trusted publishing, unpublish, create-namespace, verify, token store).
- https://github.com/eclipse-openvsx/openvsx/pull/1980 — loaded ("feat: Trusted Publishing support", merged 2026-07-27).
- https://open-vsx.org/api/redhat/java , https://open-vsx.org/api/redhat/java/versions?size=3 , https://open-vsx.org/api/-/search?query=python&size=1 — loaded (JSON field names quoted in section 6).
- https://blogs.eclipse.org/post/mikaël-barbero/open-vsx-security-update-october-2025 — loaded (2025-10-27 post; quotes in section 10).
- https://blogs.eclipse.org/post/mikaël-barbero/eclipse-open-vsx-registry-security-advisory — loaded (2025-07-02, CVE-2025-6705).
- https://securityboulevard.com/2026/03/security-at-scale-how-open-vsx-is-raising-the-bar — loaded; press venue, author Mikaël Barbero (Eclipse), 2026-03-30.
- https://thehackernews.com/2025/06/critical-open-vsx-registry-flaw-exposes.html — loaded; press, used for the Koi quotes on the CI flaw.
- https://thehackernews.com/2025/11/glassworm-malware-discovered-in-three.html — loaded; press, used for the second-wave extension names.
- https://www.koi.ai/blog/marketplace-takeover-how-we-couldve-taken-over-every-developer-using-a-vscode-fork-putting-millions-at-risk and https://koi.ai/blog/glassworm-first-self-propagating-worm-using-invisible-code-hits-openvsx-marketplace — not confirmed: both redirect (301) to paloaltonetworks.com and the posts could not be read.
- Open VSX token prefix string and default token lifetime — not confirmed (prefix not stated in the Eclipse post; wiki says five minutes for trusted-publishing tokens, PR #1980 says 15).

## Matrix row

`| VS Code + Open VSX | Extension (Node/web package, .vsix, publisher.name) | None (labels only) | None (contributes: commands/views/tasks; no pipeline concept) | Central services: VS Marketplace (Microsoft) and Open VSX (Eclipse; Spring Boot + PostgreSQL + Elasticsearch + blob storage) | Partly: Open VSX publish-extensions is a Git index with CI auto-publish (source of CVE-2025-6705) | Yes (extension host) | Yes (extensionKind ui/workspace; registry remote) | SemVer; engines.vscode range; Install Another Version; per-ext auto-update; pre-release channel (odd minor); deprecate/unpublish/remove | No sandbox; publisher-trust dialog (1.97); verified publisher = domain + 6 months; AV + dynamic scan + secret scan at publish; signed packages; block list auto-uninstall; Workspace Trust declarations partly enforced | Free; sponsor.url; pricing Free/Trial labels; Open VSX $0 and no fees | Copy the manifest/id/compat/pack model and the version controls; add a client-side block list and hashes; never run plugin code or hold publish secrets in the index CI; call declared capabilities "declared" |`

## Hypotheses

- **H3 (static Git registry, CI-built index, no backend):** Partly supported, with a hard warning. Both registries are real backends (Open VSX needs PostgreSQL + Elasticsearch + storage), so they are evidence that a *central* registry is not free. But Open VSX's `publish-extensions` repo is exactly a Git-by-PR index with CI, and it produced CVE-2025-6705 because the CI ran `npm install` on contributed code with a publish-capable token in the environment. A CI-built index is viable only if it reads metadata, never executes plugin code, and holds no secret able to rewrite the index outside the PR review path.
- **H4 (hand review does not scale):** Supported indirectly. The Marketplace does no human pre-review; it uses AV scanning, a clean-room VM, secret scanning and a post-hoc block list. Open VSX moved to "Automated scanning of extensions will now occur at the time of publication" after October 2025, and to pre-publication similarity/secret/malware checks in 2026.
- **H5 (declared is not enforced):** Confirmed. "The extension host has the same permissions as VS Code itself." There is no permission manifest; the only declaration (`capabilities.untrustedWorkspaces`) is enforced by the host for on/off and `restrictedConfigurations`, and left to the extension for `'limited'`.
- **H8 (namespaced ids + declared capabilities, several implementations coexist):** Supported for the identifier half: `${publisher}.${name}`, Open VSX namespaces with ownership, `extensionDependencies`/`extensionPack`, and typosquatting checks on names. VS Code has no typed capability I/O, so the second half is not spoken to.
- **H10 (counts need telemetry; stars/updated from GitHub; counts opt-in):** Partly rejected, partly refined. Both registries count downloads server-side at the registry (no client telemetry): Open VSX exposes `downloadCount` and `reviewCount` in its API. The cost is that server-side counts are gameable: Eclipse says GlassWorm's 35,800 "includes inflated downloads generated by bots". For a GitHub-first distribution, see the Obsidian notes for release-asset counts.
- H1, H2, H6, H7, H9: not spoken to by this platform (VS Code runs in-process in a shared host, has no model concept and no pipeline contract).
