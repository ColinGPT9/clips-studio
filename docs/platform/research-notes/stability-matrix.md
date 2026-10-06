# Stability Matrix (LykosAI) — Track A, Tier 2

Date read: 2026-10-06. Latest release at time of reading: v2.16.4 (GitHub releases page shows "16 Sep"; the CHANGELOG's top entry is v2.16.4; shallow clone HEAD 604387e dated 2026-09-30).

**Brief's question:** how should a local-first shared model store work? (package management of several AI apps, the shared Models folder and how sharing is done, embedded Git/Python, one environment per package, model reuse across packages, environment isolation, the portable data directory).

**Verdict (two lines):** Stability Matrix shares one `Data/Models/<Category>` library across packages by one of three per-package methods: a directory junction (Windows) or symlink (other OS) from the package's model folder to the shared category, a path entry written into the package's own config file (ComfyUI `extra_model_paths.yaml` under a `stability_matrix` root key), or nothing. Each package is a git clone in `Data/Packages/<name>` with its own `venv`, Python versions and `uv` are downloaded into `Data/Assets`, PortableGit into `Data/PortableGit`, and a `.sm-portable` marker file makes the whole `Data` folder movable; the package catalogue is hard-coded C# classes, not a registry.

---

## 1. Unit of extension

A **package**: a supported third-party application (ComfyUI, Forge, A1111, Fooocus, InvokeAI, SwarmUI, kohya_ss, OneTrainer, FluxGym, ...) that Stability Matrix clones, installs into its own Python environment, launches and updates. README (read 2026-10-06): "Stable Diffusion WebUI reForge, Stable Diffusion WebUI Forge, Stable Diffusion WebUI AMDGPU Forge, Automatic 1111" and others including Fooocus, ComfyUI, SwarmUI, InvokeAI, Kohya's GUI, OneTrainer. In the clone, `StabilityMatrix.Core/Models/Packages/` holds 39 `.cs` files, each a package definition subclassing `BasePackage` (`BasePackage.cs` lines 23–29: abstract `Name`, `DisplayName`, `Author`, `Blurb`, `GithubUrl`, `LicenseType`, `LicenseUrl`).

Secondary units: **models** (files in the shared library) and, for ComfyUI only, **extensions** (custom nodes) via an `IPackageExtensionManager` (`BasePackage.cs` line 278).

## 2. Manifest or metadata format

There is no manifest file for packages. A package is a C# class. Real member names from `BasePackage.cs` (clone, 2026-10-06):

| Member | Meaning |
|---|---|
| `Name`, `DisplayName`, `Author`, `Blurb`, `GithubUrl`, `LicenseType`, `LicenseUrl` | identity and attribution |
| `LaunchCommand`, `LaunchOptions`, `ExtraLaunchArguments` | how to start it |
| `AvailableTorchIndices`, `GetRecommendedTorchVersion()` | CPU/CUDA/DirectML/IPEX/MPS/ROCm selection |
| `RecommendedPythonVersion`, `MinimumPythonVersion`, `UsesVenv` (default `true`) | Python environment |
| `Prerequisites` (default `[Git, Python310, VcRedist]`) | prerequisites fetched by the app |
| `SharedFolderLayout`, `SharedFolders`, `SharedOutputFolders` | model/output folder mapping |
| `AvailableSharedFolderMethods` (default Symlink, Configuration, None), `RecommendedSharedFolderMethod` | sharing method |
| `MainBranch`, `AvailableVersionTypes`, `GetReleaseTags()`, `GetAllCommits()` | versioning |
| `KnownVulnerabilities`, `CheckForVulnerabilities()` | advisories shown in-app |

Installed state per package is `InstalledPackageVersion` (`StabilityMatrix.Core/Models/InstalledPackageVersion.cs`): `InstalledReleaseVersion`, `InstalledBranch`, `InstalledCommitSha`, `IsPrerelease`; `DisplayVersion` renders `branch@sha7` in branch mode. User-level settings live in `Data/settings.json` (`SettingsManager.cs` line 65) with `ModelDirectoryOverride` (`Settings.cs` line 288).

For ComfyUI extensions the metadata is ComfyUI-Manager's `custom-node-list.json` (see 6).

## 3. Distribution and install

- Packages are git clones: "Each installed package is cloned into its own subfolder here" (`Packages/`, docs Data Directory page, read 2026-10-06). `BaseGitPackage.cs` implements download/install/update over git.
- Embedded tooling: README "Embedded Git and Python dependencies, with no need for either to be globally installed." On Windows, `WindowsPrerequisiteHelper.cs` line 29–30 downloads `PortableGit-2.52.0-64-bit.7z.exe` from git-for-windows releases into `Data/PortableGit`. Python interpreters are installed by `uv` into `Data/Assets/Python` and the `uv` binary lives in `Data/Assets/uv` (`UvManager.cs` lines 50–55, 103–108, 213–224). Supported interpreter versions are pinned constants: 3.10.11, 3.10.17, 3.11.13, 3.12.10, 3.13.12 (`PyInstallationManager.cs` lines 18–22).
- What runs at install: the package's `InstallPackage` method runs `pip`/`uv pip install` for its requirements inside the new venv (for ComfyUI: `SetupVenvPure`, then torch per index, then requirements; `ComfyUI.cs` lines 481–610). Installing into a non-empty folder now requires explicit confirmation showing size and file count (CHANGELOG v2.16.4, issue #1733).
- The app itself ships as AGPL source; "Binaries and executable releases are licensed under the End User License Agreement" (README, 2026-10-06).

## 4. Dependencies and isolation

- **One venv per package**, inside the package folder: `SetupVenvPure(installedPackagePath, venvName = "venv")` (`BaseGitPackage.cs` line 221–223). Docs: "Each package keeps its own Python environment while sharing common resources like the model library" (Package Manager Overview, read 2026-10-06).
- Several Python versions coexist under `Data/Assets/Python`; packages pick `RecommendedPythonVersion` (ComfyUI: 3.12.10, `ComfyUI.cs` line 59; default 3.10.17). `MinimumPythonVersion` triggers a venv-recreation prompt on update (`BasePackage.cs` lines 66–69).
- Conflicts are handled per package in code: e.g. `FluxGym.cs` lines 127–130 comment on a `safetensors` version conflict between diffusers and sd-scripts and pin known-good versions; `ComfyUI.cs` line 256–263 writes `venv/uv-build-constraints.txt` (`setuptools<82`) for uv's isolated build environments.
- Whether a shared `uv`/pip cache is configured across packages: not confirmed (no `UV_CACHE_DIR` found in `StabilityMatrix.Core`).
- Process isolation: packages run as separate child processes launched with `VenvRunner.RunDetached` (`ComfyUI.cs` line 656); no sandbox.

## 5. Versioning and updates

- Per-package: install by release tag or by branch+commit (`InstalledPackageVersion`; `GetReleaseTags`, `GetAllCommits`, `AvailableVersionTypes` in `BasePackage.cs`). Docs: you can "update or roll back versions" (Package Manager Overview). Rollback = switching the git checkout; no immutable archives.
- Package `ShouldIgnoreReleases`/`ShouldIgnoreBranches` flags exist per package (`BasePackage.cs` lines 45–46).
- The app: Semantic Versioning, Keep a Changelog (CHANGELOG.md header).

## 6. Registry design

- **No registry.** The package catalogue is compiled into the app (`PackageFactory.cs`, `GetAllAvailablePackages()` line 302; 39 package classes). Adding a package means a PR to Stability Matrix itself; `CONTRIBUTING.md` contains no package-submission process (grep for "package" returned nothing, 2026-10-06).
- For ComfyUI custom nodes the app reads two static JSON indexes over a CDN: `https://cdn.jsdelivr.net/gh/ltdrdata/ComfyUI-Manager/custom-node-list.json` and `https://cdn.jsdelivr.net/gh/LykosAI/ComfyUI-Extensions-Index/custom-node-list.json` (`ComfyUI.cs` lines 773–774). That is a Git-hosted JSON file served by a free CDN — zero backend cost.
- Model discovery uses third-party APIs: CivitAI (incl. a tRPC client, `CivitTRPCModel.cs`), Hugging Face, OpenModelDB.

## 7. Trust and permissions

- No tiers, no declared permissions, nothing enforced: a package is arbitrary upstream code run with the user's rights. Trust comes from the maintainers choosing which packages to include and from `Disclaimer` text per package (`BasePackage.cs` line 30).
- Advisory channel: `KnownVulnerabilities` per package. `SimpleSDXL.cs` lines 46–66 carries `GHSA-qq8j-phpf-c63j`, "Undisclosed Data Collection and Remote Access in simpleai_base Dependency", severity Critical, published 2025-01-11, `AffectedVersions = ["*"]`, info URL `https://github.com/metercai/SimpleSDXL/issues/97`. The app shows this to the user; it does not block install.
- Model safety: the CivitAI API field `pickleScanResult` is parsed (`CivitFile.cs` line 13–14) but no UI use was found in `StabilityMatrix.Avalonia` (grep 2026-10-06; not confirmed). Accepted import formats include pickle-based ".pt, .ckpt, .pth, .bin" alongside ".safetensors ... .sft, and .gguf" (Checkpoint Manager docs). A safetensors header parser exists (`SafetensorMetadata.cs`) for "View Safetensor Metadata".

## 8. Models: reference, download, cache, share

**Layout.** One library at `Data/Models/<Category>`; categories are the `SharedFolderType` enum (`SharedFolderType.cs`): `StableDiffusion` (checkpoints), `Lora`, `LyCORIS`, `VAE`, `ControlNet`, `Embeddings`, `TextEncoders`, `DiffusionModels`, `ClipVision`, `IpAdapter`, `Ultralytics`, `Sams`, `StyleModels`, `AudioEncoders`, `ModelPatches`, `BackgroundRemoval`, etc. (36 flags). A legacy-name map renames old folders (`SharedFolders.cs` lines 23–31: `CLIP→TextEncoders`, `Unet→DiffusionModels`, `TextualInversion→Embeddings`). The root can be overridden (`ModelDirectoryOverride`; docs "Select New Models Folder").

**Sharing — confirmed from source (`SharedFolders.cs`, `SharedFoldersConfigHelper.cs`, `SharedFolderMethod.cs`, read 2026-10-06):**

| Method | Mechanism | Who uses it by default (count of overrides in clone) |
|---|---|---|
| `Symlink` | On Windows `Junction.Create(junctionDir, targetDir, overwrite)` (NTFS directory junction via `CreateFile`+`DeviceIoControl`, `ReparsePoints/Junction.cs`); elsewhere `Directory.CreateSymbolicLink`. `CreateOrUpdateLink` first moves any existing files out of the package folder into the shared category (`FileTransfers.MoveAllFilesAndDirectories(..., overwriteIfHashMatches: true)`) then replaces the folder with the link. Refuses links that would loop (`LinkSafeFileSystem.WouldLinkCycle`). | 10 packages (e.g. Forge-family, VladAutomatic, FluxGym, RuinedFooocus) |
| `Configuration` | Writes absolute shared paths into the package's own config file through a strategy per file type: `JsonConfigSharingStrategy`, `YamlConfigSharingStrategy`, `FdsConfigSharingStrategy`. ComfyUI: `RelativeConfigPath = "extra_model_paths.yaml"`, `RootKey = "stability_matrix"`, rules map `SharedFolderType.StableDiffusion → config key checkpoints`, etc. (`ComfyUI.cs` lines 61–80). The FAQ wiki: Stability Matrix "always update[s] the `stability_matrix` section with links to our model folders"; users keep custom entries outside that section. | 7 packages: ComfyUI, Fooocus, FramePackStudio, InvokeAI, Sdfx, SimpleSDXL, StableSwarm |
| `None` | Nothing is configured. | 6 packages |

No copies are ever made for sharing. Docs (Shared Folders page, 2026-10-06): on Windows "directory junctions", other platforms "symbolic links"; setup "attempts to move existing files into the shared category before replacing the folder with a link"; "Config sharing leaves them in place"; deleting through a linked folder removes the model for all packages. Removal only touches links Stability Matrix created ("Only remove links we created — never delete a real directory here", `SharedFolders.cs` line 210–218; fixed in v2.16.4 after issue #1733). A setting "Remove shared folder symbolic links on shutdown" exists "if you're having problems moving Stability Matrix to another drive" (`Resources.resx`).

Windows note (inferred from the implementation): junctions are used instead of symbolic links, which avoids the symlink privilege/Developer-Mode requirement; the docs add "the drive must support links" and that FAT32/exFAT are unsupported.

**Download.** Hugging Face downloads are single files by direct URL: `https://huggingface.co/{RepositoryPath}/resolve/main/{file}?download=true` (`HuggingFacePageViewModel.cs` line 156) saved straight into the category folder; the HF cache and HF revisions are not used (always `main`). CivitAI downloads likewise land in the category folder, with sidecar metadata and hashes (`LocalModelFile.cs`: `HashBlake3`, `HashSha256` from `ConnectedModelInfo`); the Checkpoint Manager can "Find Connected Metadata" by hash. Downloads are tracked and resumable (`TrackedDownloadService.cs`; README "pauseable downloads"). Local import: "Move" (default) or "Copy" into the library (Checkpoint Manager docs).

**Index.** A LiteDB database (`LiteDbContext.cs`, `ModelIndexService.cs`) indexes files under `Models/`.

## 9. Local, remote or both

Local only. Packages run on the user's machine; remote APIs are used only for discovery/download (CivitAI, HF, GitHub, OpenModelDB) and app updates.

## 10. Known incidents and stated limitations

- 2025-01-11: in-app Critical advisory for SimpleSDXL's `simpleai_base` dependency (undisclosed remote access and data upload), `GHSA-qq8j-phpf-c63j` (source, 2026-10-06). The package stays installable with the warning.
- v2.16.4 (CHANGELOG, 2026-10-06): installing into a folder that already contained files "silently delet[ed] everything in it" (issue #1733) — now a confirmation; turning off shared folders could delete real directories at link locations — now only links are removed; a looping link in the workflow folder froze the app.
- Docs limitations: "Sharing provides file access only; model compatibility depends on architecture and loader support"; after switching to None "models may become inaccessible"; links need write permission and link-capable drives; "Remove Symlinks on Shutdown" breaks externally launched packages.
- No CVE or security advisory for Stability Matrix itself found (web search 2026-10-06; press/aggregator results only).

## 11. Borrow

1. **Per-package choice of sharing method, declared by the package** (`RecommendedSharedFolderMethod` + `AvailableSharedFolderMethods`): a Clips Kitty pipeline manifest could declare `model_access: path-config | link | none`. Prefer the config-file method where the consumer can read a path list (it is what the `Configuration` strategy does and it never relocates files); use NTFS junctions, not symlinks, for Windows folder links.
2. **Move-then-link with hash-aware merge** (`overwriteIfHashMatches: true`) when adopting an existing model folder into the shared store; and the v2.16.4 rule "only remove links we created".
3. **Typed category folders with a legacy-name map** (`SharedFolderType`, `LegacySharedFolderMapping`): Clips Kitty's store should have fixed categories (whisper, yolo, ocr, panns, llm/ollama) and a rename table from day one.
4. **Portable data directory with a marker file** (`Data/.sm-portable`, found before `%APPDATA%/StabilityMatrix/library.json`, `SettingsManager.cs` lines 339–350) plus "the Data Directory holds platform-specific assets and should not be moved to another Operating System".
5. **Per-package in-app vulnerability notices** (`KnownVulnerabilities` with GHSA id, severity, affected versions, info URL): the plugin manifest/registry index should carry the same fields so a known-bad plugin can be flagged without a backend.
6. **Static JSON index over a free CDN** for extensions (jsdelivr over a GitHub repo) — supports H3.
7. **Per-package Python version + venv in the package folder**, with interpreters provisioned by `uv` into a shared `Assets/Python` — if Clips Kitty ever runs plugin-local Python it should copy this exactly (one venv per plugin, interpreters shared).

## 12. Avoid

1. Compiling the catalogue into the app (39 C# classes, PR required per package): Clips Kitty's registry must be data, not code.
2. Downloading HF files at `main` with no revision pin (`resolve/main/`): pin a commit hash in the model manifest.
3. Accepting pickle formats (`.ckpt/.pt/.pth/.bin`) without a warning, and parsing `pickleScanResult` without surfacing it.
4. Links as the default sharing method where a config/path entry would do: links broke on drive moves, needed a "remove on shutdown" workaround, and produced the loop/deletion bugs fixed in v2.16.4.
5. Whole-package deletion that also removes config-shared models unless clearly listed (v2.16.4 had to fix the uninstall dialog for exactly this).

## 13. Sources (all read 2026-10-06)

- https://github.com/LykosAI/StabilityMatrix — README (features, license, package list).
- https://github.com/LykosAI/StabilityMatrix/wiki/FAQ-&-Troubleshooting — shared folder in `(Data)/Models`, `stability_matrix` section of `extra_model_paths.yaml`, portable "Data" folder, venv per imported package.
- https://github.com/LykosAI/StabilityMatrix/wiki — only Home, FAQ, Inference Guide; points to docs.lykos.ai.
- https://docs.lykos.ai/ — navigation (hrefs resolve under `/stability-matrix/`).
- https://docs.lykos.ai/stability-matrix/advanced/shared-folders.html — three methods, ComfyUI mapping table, limitations. (The same path without `/stability-matrix/` returns 404.)
- https://docs.lykos.ai/stability-matrix/getting-started/data-directory.html — folder list, `.sm-portable`, FAT32/exFAT unsupported.
- https://docs.lykos.ai/stability-matrix/package-manager/overview.html — one Python environment per package, "update or roll back versions".
- https://docs.lykos.ai/stability-matrix/checkpoint-manager/overview.html — Move/Copy import, accepted extensions, delete semantics.
- https://github.com/LykosAI/StabilityMatrix/releases/latest — v2.16.4, "16 Sep".
- Shallow clone of https://github.com/LykosAI/StabilityMatrix (HEAD 604387e, 2026-09-30): `StabilityMatrix.Core/Helper/SharedFolders.cs`, `SharedFoldersConfigHelper.cs`, `Models/SharedFolderMethod.cs`, `Models/SharedFolderType.cs`, `Models/Packages/BasePackage.cs`, `BaseGitPackage.cs`, `ComfyUI.cs`, `SimpleSDXL.cs`, `FluxGym.cs`, `Models/InstalledPackageVersion.cs`, `Models/Settings/Settings.cs`, `Services/SettingsManager.cs`, `Python/PyInstallationManager.cs`, `Python/UvManager.cs`, `ReparsePoints/Junction.cs`, `Models/Database/LocalModelFile.cs`, `Models/Api/CivitFile.cs`, `StabilityMatrix.Avalonia/Helpers/WindowsPrerequisiteHelper.cs`, `ViewModels/CheckpointBrowser/HuggingFacePageViewModel.cs`, `Languages/Resources.resx`, `CHANGELOG.md`, `CONTRIBUTING.md`.
- Web search for Stability Matrix security incidents: no primary-source advisory found (not confirmed).

---

## Matrix row

`| Stability Matrix | Package (git-cloned app in its own venv); ComfyUI custom nodes as extensions | Shared `Data/Models/<Category>` library; per-package junction/symlink, config-file path entry, or none; CivitAI/HF/OpenModelDB download by direct URL (HF at `main`) | None (ComfyUI workflows library only) | None for packages (hard-coded C# catalogue); static JSON on jsdelivr for ComfyUI nodes | Yes: packages are git clones with release tag or branch@commit | Yes | No | Tag or branch@commit, roll back by checkout; app uses SemVer | No permissions; per-package GHSA advisories shown in-app; no pickle warning | No | Per-package sharing method declared by the package; config-path sharing over links; junctions on Windows; move-then-link with hash merge; typed category folders with rename map; `.sm-portable` marker; advisory fields in metadata |`

## Hypotheses

- **H1** (out-of-process is lowest risk): consistent. Packages are separate processes in their own venvs; Stability Matrix never imports package code. No permission model though.
- **H3** (static Git index, no backend): supported for extensions — ComfyUI node lists are JSON files in Git repos served by jsdelivr (`ComfyUI.cs` 773–774). Packages themselves show the cost of the opposite choice (catalogue compiled in).
- **H6** (dependency handling): supported. One venv per package (`SetupVenvPure`, `venv` inside the package dir) with shared interpreters under `Assets/Python`; conflicts are solved by hand-written pins per package (`FluxGym.cs`, `uv-build-constraints.txt`). Disk cost accepted. No prohibition of runtime pip installs — packages pip-install freely at install and update.
- **H7** (HF revision pin, blob sharing, Windows symlinks, safetensors): partially contradicted by practice. Stability Matrix does not use the HF cache: it downloads single files at `resolve/main/` with no commit pin, so there is no blob sharing between revisions and the Windows HF-cache symlink problem is sidestepped by not having a cache. Folder sharing on Windows uses NTFS junctions (no symlink privilege). Safetensors are preferred in tooling (metadata viewer) but pickle formats are accepted without a warning; `pickleScanResult` is parsed and not surfaced (not confirmed in UI). Conclusion for Clips Kitty: H7's "pin to a commit" is the right correction to what Stability Matrix does; "share blobs" is only true if the HF cache is used, which it is not here.
- **H5**: nothing to say (no permission concept).
- **H10**: nothing; no usage counts.
