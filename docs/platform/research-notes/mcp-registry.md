# MCP Registry — research notes

- Platform: the official MCP Registry (`registry.modelcontextprotocol.io`) and the `server.json` format
- Tier: Track C, Tier 2 (registry-design precedent; point 6 matters most)
- Date read: 2026-10-06
- Brief's question: what makes an extension ecosystem successful, and what mistakes should Clips Kitty avoid?
- Verdict (two lines): a metadata-only "metaregistry": it hosts no code, points at npm/PyPI/NuGet/crates.io/OCI/MCPB packages or at remote URLs, authenticates publishers by namespace (GitHub login or DNS/HTTP proof), makes every published version immutable, and by design does no curation, ranking, scanning or more than reactive moderation ("consumers should assume minimal-to-no moderation"). It runs as a Go REST service with PostgreSQL on GKE deployed by Pulumi — far more infrastructure than a free Clips Kitty registry needs. Borrow the `server.json` shape (reverse-DNS names, exact package versions, `fileSha256`, `remotes`, `_meta`), the package-ownership marker, immutable versions with a `status` field and `updated_since` sync; do not borrow the service footprint, and do not pair "minimal moderation" with any "Verified" wording.

Every claim carries a URL and the read date 2026-10-06 unless stated. The registry repository was read from a shallow clone at commit c384c4b (dated 2026-10-06).

## 1. The unit of extension

An MCP server entry: a `server.json` document describing one server by a reverse-DNS `name` with exactly one slash (`^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$`), with zero or more `packages` (installable local servers) and zero or more `remotes` (hosted endpoints). "MCP registries are metaregistries. They host metadata about packages, but not the package code or binaries." `docs/design/ecosystem-vision.md` and `docs/reference/server-json/draft/server.schema.json` (clone, 2026-10-06).

## 2. Manifest or metadata format

`server.json` (schema `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`; the registry docs also reference the 2025-09-29 schema). From the draft schema in the clone (2026-10-06):

| Field | Required | Note |
|---|---|---|
| `name` | yes | reverse-DNS, one slash; namespace must be authenticated (§7) |
| `description` | yes | "Should focus on capabilities, not implementation details" |
| `version` | yes | "SHOULD follow semantic versioning"; must be unique per publication |
| `title`, `websiteUrl`, `icons` | no | display |
| `repository` | no | `url`, `source` (e.g. `github`), `subfolder`, `id`; "Recommended for transparency and security inspection" |
| `packages[]` | no | each: `registryType` (`npm`, `pypi`, `nuget`, `cargo`, `oci`, `mcpb`), `identifier`, `transport` (required); `registryBaseUrl`, `version` ("Must be a specific version. Version ranges are rejected"), `fileSha256` ("Required for MCPB packages and optional for other package types"), `runtimeHint` (`npx`, `uvx`, `dnx`, `docker`), `runtimeArguments`, `packageArguments`, `environmentVariables` (`name`, `description`, `isRequired`, `isSecret`, `default`) |
| `remotes[]` | no | `type` (`streamable-http`, `sse`), `url`, optional headers/variables |
| `_meta` | no | reverse-DNS namespaced; the official registry keeps only `io.modelcontextprotocol.registry/publisher-provided`, 4 KB limit |

Official-registry restrictions (`docs/reference/server-json/official-registry-requirements.md`, 2026-10-06): only `https://registry.npmjs.org`, `https://pypi.org`, `https://api.nuget.org/v3/index.json`, `https://crates.io`, OCI on docker.io / ghcr.io / quay.io / `*.pkg.dev` / `*.azurecr.io` / mcr.microsoft.com, and MCPB only from GitHub or GitLab releases. "Private registries and alternative mirrors are not allowed."

Compatibility fields: the schema read has no minimum-host-version or protocol-version field for a server; compatibility is left to the client (inferred from the property list above).

## 3. Distribution and install

- The registry distributes nothing: "The MCP Registry hosts metadata that points to those packages." Install is the client's job using the package registry named in `packages[]` (e.g. `npx`, `uvx`, `dnx Knapcode.SampleMcpServer@0.4.0-beta -- mcp start`, `docker`). https://modelcontextprotocol.io/registry/about and `generic-server-json.md` (2026-10-06).
- Publishing (`docs/modelcontextprotocol-io/quickstart.mdx`, 2026-10-06): add the ownership marker to the package (npm: `"mcpName": "io.github.my-username/weather"` in `package.json`), publish the package, install `mcp-publisher`, `mcp-publisher init` (generates `server.json`), `mcp-publisher login github`, `mcp-publisher publish`. "The `name` property in `server.json` must match the `mcpName` property in `package.json`."
- Ownership markers per package type (`package-types.mdx`, 2026-10-06): npm `mcpName` in `package.json`; PyPI and NuGet an `mcp-name: <server-name>` line in the README (may be an HTML comment); Cargo the same token as visible text (crates.io strips HTML comments); OCI the image label `io.modelcontextprotocol.server.name`; MCPB a `fileSha256` of the release asset.
- Remote-only servers publish `remotes` without `packages`; "The remote URL must be publicly accessible." Private servers are out of scope: "If you want to publish private servers, we recommend that you host your own private MCP registry." (about page, 2026-10-06).
- Nothing runs at publish time except validation (schema, namespace, ownership marker, allowed registry base URLs) — from the requirements doc; no build step exists because no code is hosted.

## 4. Dependencies and isolation

Out of scope by design: "Unified runtime: Not solving how servers are executed" and "Server hosting: The registry does not provide hosting for servers" (`docs/design/roadmap.md`, 2026-10-06). Local packages run as separate processes spawned by the client over `stdio`; remote servers run elsewhere over HTTP. Secrets are declared (`isSecret: true`) so clients can prompt and store them, but no sandbox or permission is defined (schema, 2026-10-06).

## 5. Versioning and updates

From `docs/modelcontextprotocol-io/versioning.mdx` (2026-10-06):
- "The version string MUST be unique for each publication of the server. Once published, the version string (and other metadata) cannot be changed."
- Semver recommended but any string accepted; the registry parses semver to mark `isLatest`; "If parsing fails, the version will always be marked as 'latest'" (a documented footgun). Range strings (`^1.2.3`, `1.x`, `>=1.2.3`) are prohibited.
- Metadata-only updates use prerelease versions (`1.2.3-1`), with the warning that a prerelease published after `1.2.3` will not be marked latest.
- Lifecycle: `status` is `active`, `deprecated` or `deleted`; "Server metadata is generally immutable, except for the `status` field" (`registry-aggregators.mdx`). Deletion hides from default listings but "Server metadata is never permanently removed from the registry" (`faq.mdx`); `mcp-publisher status --status deleted` lets authors unpublish and later restore.
- Aggregators are told how to compare versions (latest flag, then semver, then publish timestamp).

## 6. Registry design

- Architecture as deployed (`deploy/README.md`, 2026-10-06): a Go REST API in a Kubernetes `Deployment` on GKE (projects `mcp-registry-prod` and `mcp-registry-staging`, zone us-central1-b), PostgreSQL via the CloudNativePG operator, ingress-nginx, cert-manager, K8up/Restic backups to GCS buckets, monitoring with Grafana behind Google OAuth, all provisioned by Pulumi; staging auto-deploys from `main`, production deploys when `Pulumi.gcpProd.yaml`'s image tag changes (GitOps). Secrets: GitHub OAuth client, Ed25519 JWT signing key, Google OAuth secret, GCP service-account key. The design document admits drift from the original plan (`docs/design/tech-architecture.md` warning block, dated 2026-08-10).
- API (`docs/reference/api/official-registry-api.md` and `registry-aggregators.mdx`, 2026-10-06): `GET /v0.1/servers` (cursor pagination, `limit`, `updated_since`, `search` substring on names, `version=latest`, `include_deleted`), `GET /v0.1/servers/{name}/versions`, `GET /v0.1/servers/{name}/versions/{version|latest}`; auth endpoints `POST /v0.1/auth/{dns,http,github-at,github-oidc,oidc}`; publishing posts to `/v0/publish` (tech-architecture note). Live check 2026-10-06: `GET https://registry.modelcontextprotocol.io/v0.1/servers?limit=2` returned entries shaped `{server: {...server.json...}, _meta: {"io.modelcontextprotocol.registry/official": {status, statusChangedAt, publishedAt, updatedAt, isLatest}}}` with `metadata.nextCursor` of the form `name:version`. (The brief's `GET /v0/servers` is the older path; current docs use `/v0.1`.)
- Who runs it: "owned by the MCP open-source community, backed by major trusted contributors to the MCP ecosystem such as Anthropic, GitHub, PulseMCP and Microsoft"; a four-person Registry Working Group is named in the README; admins use `@modelcontextprotocol.io` Google identities (`docs/administration/admin-operations.md`). Status: preview launched 2025-09-08; API freeze v0.1 on 2025-10-24; "Breaking changes or data resets may occur before general availability" (README and every docs page, 2026-10-06).
- Submission and review: none by humans. Publishing is self-service after namespace authentication and marker validation; moderation is reactive ("Manual takedown"). "Avoid features that require constant human intervention or moderation" and "Build for reasonable downtime tolerance (24h acceptable)" are stated design principles (`docs/design/design-principles.md`, 2026-10-06).
- Subregistry/upstream model: "The MCP Registry is intended to be consumed primarily by downstream aggregators, such as MCP server marketplaces" which "pull new metadata on a regular but infrequent basis (for example, once per hour)" and may add "curation or additional metadata such as community ratings"; a subregistry implements the same OpenAPI spec and injects `_meta` (example keys: `user_rating`, `download_count`, `security_scan`). "The MCP Registry is not intended to be directly consumed by host applications." The codebase "is not designed for self-hosting" (about page and `registry-aggregators.mdx`, 2026-10-06).
- Cost to run: the components above (GKE cluster, Postgres, GCS backups, Grafana, two environments). No cost figures are published in the docs read (grep for cost/budget/sponsor found only a branding clause in the ToS) — not confirmed. The project calls itself "a community supported project, and we have limited active moderation capabilities" (`moderation-policy.mdx`, 2026-10-06).

## 7. Trust and permissions

- Namespace authentication: GitHub login grants `io.github.<username>/*`; `io.github.<org>/*` requires the Owner role ("Ordinary org membership is no longer sufficient"); DNS proof is a TXT record at the apex `v=MCPv1; k=ed25519; p=<pubkey>` (or `ecdsap384`), HTTP proof a `mcp-registry-auth` file; GitHub OIDC for Actions. `authentication.mdx` (2026-10-06).
- What it proves: "This namespace system ensures that only the legitimate owner of a GitHub account or domain can publish servers under that namespace" (about page). It proves who published, not that the code is safe.
- Scanning: delegated — "npm, PyPI, Docker Hub, and other package registries perform their own security scanning" and "Downstream aggregators and marketplaces can implement additional security checks" (about page).
- Moderation policy (`moderation-policy.mdx`, 2026-10-06): removes "Illegal content", "Malware, regardless of intentions", "Spam", "Non-functioning servers"; explicitly does not remove "Low-quality or buggy servers", "Servers with security vulnerabilities", duplicates, adult content. "The MCP Registry does not make guarantees about moderation, and consumers should assume minimal-to-no moderation."
- Terms: "provided 'as is' with no warranties of any kind ... we highly recommend that you evaluate each MCP server and its suitability for your intended use case(s) before deciding whether to use it." (`terms-of-service.mdx`, effective 2025-09-02, read 2026-10-06).
- Declared permissions: none beyond `environmentVariables[].isSecret`; nothing is enforced by the registry (schema, 2026-10-06).
- Spam prevention: namespace auth, "Character limits and validation", manual takedown; "stricter rate limiting, AI-based spam detection, and community reporting" are listed as future ideas (about page).

## 8. Models

Not applicable.

## 9. Local, remote or both

Both are first-class: `packages` (local, `stdio` transport, run by the client) and `remotes` (`streamable-http` or `sse`), and a server may list both (the filesystem example lists npm and OCI packages). This matches Clips Kitty's "local, remote and hybrid are all legitimate" decision.

## 10. Known incidents and stated limitations

- Status: preview with possible data resets (§6). "Download count tracking", a UI and i18n are backlog; "Server tags or categories: Not supported, to reduce moderation burden"; no rankings, no curation, no search engine (`roadmap.md`, 2026-10-06).
- Abuse handling: "report it as abuse to the underlying package registry ... and raise a GitHub issue on the registry repo with a title beginning `Abuse report: `" (`faq.mdx`, 2026-10-06).
- Press (not vendor documentation): search results on 2026-10-06 mention a malicious npm package `postmark-mcp` (September 2025, Koi Security) that BCC'd emails, and later package-level incidents; none of the results tie an incident to a registry listing or takedown, so no registry incident is confirmed.
- A neighbouring precedent intends to depend on it: Zed states "MCP server extensions will be deprecated in favor of the MCP registry in the future" (https://zed.dev/docs/extensions/publishing/prerequisites, raw source read 2026-10-06).

## 11. Borrow

1. Identifier design: reverse-DNS namespace tied to a login the registry can verify (`io.github.<user>/<pipeline>`), one slash, immutable after first publish. This is the cleanest answer to H8's "publisher/pipeline" naming and to impersonation.
2. Ownership marker inside the thing being pointed at: Clips Kitty's plugin manifest in the developer's repository should carry the registry name (as `mcpName` does), and the index build should refuse an entry whose repo manifest name does not match.
3. Exact versions only, ranges rejected; `fileSha256` for downloaded archives; `repository.url` + `subfolder` for monorepos.
4. Immutable per-version records plus a mutable `status` (`active` / `deprecated` / `deleted`) with `statusMessage`, and an `updated_since` query so clients and mirrors sync incrementally instead of re-reading everything.
5. `environmentVariables` with `isRequired`/`isSecret`/`default` as the declared way to ask for API keys (Clips Kitty's OpenRouter-style "on the user's own account" settings).
6. The subregistry idea in reverse: Clips Kitty's static index can be the upstream, and anyone may build a curated view with their own `_meta`.
7. Design principles worth adopting verbatim: minimal operational burden, vendor neutrality, interface reuse "not infrastructure".

## 12. Avoid

1. A database-backed service with OAuth, JWT keys, Kubernetes, backups and two environments — the registry needs it for self-service publishing at ecosystem scale; a free, PR-driven Clips Kitty index does not.
2. "Any version string is accepted" plus "unparsable means latest": validate semver (or CalVer) at index build and reject the rest.
3. Listing before any functional check: the registry accepts entries and only later removes "Non-functioning servers"; a Git-index CI can at least fetch the pinned ref and validate the manifest first.
4. Shipping without a host-compatibility field; Clips Kitty's manifest should state the minimum engine/API version.
5. Any wording near "Verified" while the stated policy is "assume minimal-to-no moderation" — the two cannot coexist under Clips Kitty's fixed decisions.

## 13. Sources, with dates

- https://modelcontextprotocol.io/registry/about — 2026-10-06.
- https://github.com/modelcontextprotocol/registry — 2026-10-06 (README; shallow clone at commit c384c4b dated 2026-10-06; files read: `docs/reference/server-json/generic-server-json.md`, `draft/server.schema.json`, `official-registry-requirements.md`, `docs/modelcontextprotocol-io/quickstart.mdx`, `authentication.mdx`, `package-types.mdx`, `versioning.mdx`, `moderation-policy.mdx`, `registry-aggregators.mdx`, `faq.mdx`, `terms-of-service.mdx`, `docs/reference/api/official-registry-api.md`, `docs/design/ecosystem-vision.md`, `design-principles.md`, `roadmap.md`, `tech-architecture.md`, `docs/administration/admin-operations.md`, `deploy/README.md`).
- https://registry.modelcontextprotocol.io/v0.1/servers?limit=2 — 2026-10-06 (live read-only GET).
- https://zed.dev/docs/extensions/publishing/prerequisites (raw source `docs/src/extensions/publishing/prerequisites.md` in zed-industries/zed) — 2026-10-06 (for the Zed deprecation statement).
- Web search "MCP registry malicious server removed takedown 2025 2026" — 2026-10-06 (press results only; no registry incident confirmed).

## Matrix row

`| MCP registry | MCP server metadata entry (server.json) pointing at npm/PyPI/NuGet/crates.io/OCI/MCPB packages or remote URLs | None | None | Central Go + PostgreSQL REST service on GKE (Pulumi); self-service publish via mcp-publisher after namespace auth; read API for aggregators/subregistries | No (packages live in package registries; repository URL is optional metadata) | Yes (stdio packages spawned by the client) | Yes (streamable-http / sse remotes) | Immutable per-version entries; exact package versions, ranges rejected; status active/deprecated/deleted; isLatest | Namespace auth (GitHub / DNS / HTTP), package ownership markers, sha256 for MCPB; no scanning; "assume minimal-to-no moderation" | None of its own; metadata only; subregistries add ratings and curation | Namespaced ids + ownership marker + immutable versions + status/updated_since; keep hosting and curation downstream; do not copy the service footprint |`

## Hypotheses

- H1 (out-of-process plugins speaking the local API are lowest risk): consistent. Local MCP servers are separate processes over stdio; the registry explicitly refuses to define a runtime, so isolation is the host's job.
- H3 (static Git registry costs nothing, needs no backend): counter-example, deliberately. The official registry is a hosted service (Go, Postgres, GKE, backups, OAuth) because it wants self-service publishing without PRs. Its own principle "Reusable, Extensible Shapes; Not Infrastructure" supports copying the shapes into a static index.
- H4 (hand review does not scale): supports by design choice — no human review at all, only reactive takedown, and the policy warns consumers accordingly.
- H5 (declared is not enforced): supports. The only declarations are environment variables with `isSecret`; nothing is enforced; security is delegated to package registries and aggregators.
- H8 (namespaced identifiers plus declared capabilities let implementations coexist): supports the identifier half. `io.github.<user>/<server>` plus multiple `packages` per server already lets several distributions of one server coexist; the registry has no capability vocabulary, so the typed-I/O half is not addressed.
- H10 (install counts need telemetry; stars/updated from GitHub at build time): supports. Download counts are backlog for the registry and are expected to be injected by subregistries' `_meta`; the registry itself records only `publishedAt`/`updatedAt`.
