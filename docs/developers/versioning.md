# Versioning

Every version number in the platform, where it lives, and what it promises.

| Version | Where | Today | Rule |
|---|---|---|---|
| **App** | `ui/package.json`, reported by `GET /health` as `app_version` | `2.0.0` | A plugin states the app versions it works with in `requires.clips_kitty`. |
| **Local API** | `API_VERSION` in `server/api.py`, `GET /health` → `api_version` | `1` | A stable route changes only by adding; anything else raises it ([API](api.md)). Plugin routes are experimental. |
| **Plugin contract** | `clipskitty_sdk.PLUGIN_API_VERSION`; a plugin's `requires.plugin_api` | `1` | Within a version only optional fields are added: plugins ignore job fields they don't know, the engine ignores result fields it doesn't know. |
| **SDK** | `clipskitty_sdk.__version__`, `python -m clipskitty_sdk --version` ([changelog](../../sdk/python/CHANGELOG.md)) | `1.3.0` | Its major version is the plugin contract's. A plugin that vendors an older 1.x copy still works, because the contract is `job.json` and `result.json`, not the SDK's functions. SDK 1.0.0 ignores job.json's `steps` and `moments`, so a finder that vendors it works as before; a plugin that rates or understands needs 1.1.0 (`job.rate`, `job.understand` and `job.moments` are new in it), vendored or not vendored at all. SDK 1.1.0 ignores step names and moment keys it doesn't know, so a later 1.x can add them. A plugin that suggests edits needs 1.3.0 (`job.suggest_edit` is new in it): don't vendor an older copy, which comes first on the path and lacks it. The installed app puts its own SDK on the plugin's path, and it wins over one installed with pip. A plugin that uses a module added in a later SDK sets `requires.clips_kitty` to the first release that bundles it. |
| **Manifest** | `manifest_version` | `1` | The validator says which versions it reads. |
| **Plugin** | `version` in its manifest (SemVer) | yours | One plugin may find, understand, rate and suggest edits ([Steps](steps.md)). A clip records `plugin` and `plugin_version` in its saved scores. A suggested edit's id comes from the plugin's id and what it suggests, not its version, so the creator's decision on it carries over to a new version that suggests the same. |
| **Model** | a Hugging Face commit, an Ollama digest or a file's SHA-256 | | Pinned in the manifest; a new model revision is a new plugin version ([Model references](model-references.md)). |

## Which release runs plugins

No Clips Kitty release runs plugins yet. Plugins made with SDK 1.2.0 need the first release that includes it; until that is out, run Clips Kitty from source.

Clips Kitty bundles the SDK from the commit it is built from, so the release that first runs plugins brings the SDK of that commit. Running from source is in the README: [From source](../../README.md#from-source).

## The edit step and older Clips Kitty

The edit step ([Steps](steps.md#suggest-edits-the-edit-step)) is new in the same plugin contract, 1, with SDK 1.3.0. A Clips Kitty without it refuses to install a plugin with `outputs: [edits]`, saying "output 'edits' is planned, not supported by plugin API 1" (or calling it an unknown output, in a copy from before SDK 1.2.0). It checks the manifest before the app version, so that is what it says, not that it needs a newer Clips Kitty, and an old copy can't be changed to say otherwise. Clips Kitty's online list can still show your editor to it. Say in your plugin's description which Clips Kitty it needs.

Which `requires.clips_kitty` an editor declares follows the release order:
- No Clips Kitty release runs plugins yet. If the first release that does also has the edit step, every Clips Kitty that can install a plugin can run an editor, and `">=2.0"`, as every template has, is right.
- If a release that runs plugins comes out before one with the edit step, an editor sets `requires.clips_kitty` to the first release with it, and the templates' `">=2.0"` is raised to match.

## `requires.clips_kitty`

A range of app versions: `">=2.0"`, `">=2.0, <3"`, `"~=2.1"` (2.1 and later 2.x). Operators are `>=`, `<=`, `>`, `<`, `==`, `!=` and `~=`, joined by commas; every part must hold.

It is **checked** (built) when a plugin is planned, installed or rolled back to, when a job naming it is added, and when that job runs. A plugin an app update leaves behind stays installed and says why it can't run. An app version Clips Kitty can't read (a development build) is not held against a plugin.

Pick the lowest version you tested with, and an upper bound only when you know a later major version breaks you.

## Updates, rollback, pins

- An update installs **beside** the version in use and becomes active only once it validates. The plan shows the new version and what changes: new permissions, hosts and data warnings, whether it now runs on a remote service, and any step it now also does ("Now also: Rates moments").
- **Roll back** makes the previous version active; only the active and previous versions are kept.
- **Pin** stops the Marketplace offering a plugin's updates.
- There are **no automatic updates**. A harmful version is handled by the block list (built), never by forcing an update.
- In the registry (built; no public registry repository yet), a listed version maps to one commit, and versions are never deleted, only blocked or delisted.
