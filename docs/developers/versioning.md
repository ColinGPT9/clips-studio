# Versioning

Every version number in the platform, where it lives, and what it promises.

| Version | Where | Today | Rule |
|---|---|---|---|
| **App** | `ui/package.json`, reported by `GET /health` as `app_version` | `2.0.0` | A plugin states the app versions it works with in `requires.clips_kitty`. |
| **Local API** | `API_VERSION` in `server/api.py`, `GET /health` → `api_version` | `1` | A stable route changes only by adding; anything else raises it ([API](api.md)). Plugin routes are experimental. |
| **Plugin contract** | `clipskitty_sdk.PLUGIN_API_VERSION`; a plugin's `requires.plugin_api` | `1` | Within a version only optional fields are added: plugins ignore job fields they don't know, the engine ignores result fields it doesn't know. |
| **SDK** | `clipskitty_sdk.__version__` | `1.0.0` | Its major version is the plugin contract's. A plugin that vendors an older 1.x copy still works, because the contract is `job.json` and `result.json`, not the SDK's functions. |
| **Manifest** | `manifest_version` | `1` | The validator says which versions it reads. |
| **Plugin** | `version` in its manifest (SemVer) | yours | One pipeline per plugin. A clip records `plugin` and `plugin_version` in its saved scores. |
| **Model** | a Hugging Face commit, an Ollama digest or a file's SHA-256 | | Pinned in the manifest; a new model revision is a new plugin version ([Model references](model-references.md)). |

## `requires.clips_kitty`

A range of app versions: `">=2.0"`, `">=2.0, <3"`, `"~=2.1"` (2.1 and later 2.x). Operators are `>=`, `<=`, `>`, `<`, `==`, `!=` and `~=`, joined by commas; every part must hold.

It is **checked** (built) when a plugin is planned, installed or rolled back to, when a job naming it is added, and when that job runs. A plugin an app update leaves behind stays installed and says why it can't run. An app version Clips Kitty can't read (a development build) is not held against a plugin.

Pick the lowest version you tested with, and an upper bound only when you know a later major version breaks you.

## Updates, rollback, pins

- An update installs **beside** the version in use and becomes active only once it validates. The plan shows the new version and what changes: new permissions, hosts and data warnings, and whether it now runs on a remote service.
- **Roll back** makes the previous version active; only the active and previous versions are kept.
- **Pin** stops the Marketplace offering a plugin's updates.
- There are **no automatic updates**. A harmful version is handled by the block list (built), never by forcing an update.
- In the registry (built; no public registry repository yet), a listed version maps to one commit, and versions are never deleted, only blocked or delisted.
