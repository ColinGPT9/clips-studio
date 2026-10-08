# Clips Kitty SDK (`clipskitty_sdk`)

A small Python package for writing [Clips Kitty](https://github.com/ColinGPT9/clips-studio) plugins: read the job, report progress, find moments or rate and describe the moments found, and check a plugin's manifest. It uses the Python standard library only (reading a YAML manifest also needs PyYAML).

**Licence: MIT** ([LICENSE](https://github.com/ColinGPT9/clips-studio/blob/main/sdk/python/LICENSE)). Clips Kitty itself is AGPL-3.0-or-later; the SDK is MIT so that a plugin, app or tool built on it can use any licence its author chooses, open or closed. Using the SDK does not put your code under the AGPL.

## Install

PowerShell (Windows):

```powershell
py -m pip install "clipskitty-sdk[yaml] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
```

bash (macOS, Linux):

```bash
python -m pip install "clipskitty-sdk[yaml] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
```

No Clips Kitty release runs plugins yet. Plugins made with SDK 1.2.0 need the first release that includes it; until that is out, run Clips Kitty from source.

How to run it from source: [From source](https://github.com/ColinGPT9/clips-studio#from-source). Which release runs plugins: [Versioning](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/versioning.md#which-release-runs-plugins).

The `yaml` extra adds PyYAML, for reading manifests. Write `[yaml,test]` in place of `[yaml]` to add pytest too, for your plugin's own tests.

Inside the app you never install it. Clips Kitty bundles the SDK from the commit it is built from and puts that copy on your plugin's path, where it wins over one installed with pip.

## Try it

PowerShell (Windows):

```powershell
py -m clipskitty_sdk --version
py -m clipskitty_sdk validate path\to\your-plugin
py -m clipskitty_sdk run path\to\your-plugin --video video.mp4
```

bash (macOS, Linux):

```bash
python -m clipskitty_sdk --version
python -m clipskitty_sdk validate path/to/your-plugin
python -m clipskitty_sdk run path/to/your-plugin --video video.mp4
```

`--version` prints the SDK's version and the plugin contract it follows, such as `clipskitty-sdk 1.2.0 (plugin contract 1)`. pip also installs a `clipskitty-sdk` command that does the same as `python -m clipskitty_sdk`; these pages use the `-m` form, because pip's command folder is often not on `PATH` on Windows.

## How a plugin fits in

```text
Clips Kitty SDK

Input                      a link or a video file
  ↓
Video                      Clips Kitty downloads it and writes down what is said
  ↓
Your plugin                every step is optional
  ├── find                 picks the moments                  built
  ├── understand           says what happens in each one      built
  ├── rate                 scores each moment                 built
  ├── edit                 suggests cuts and framing          coming later
  └── export               posts to a platform                coming later
  ↓
Clips Kitty                does every step no plugin does, then cuts, frames and captions the clips
  ↓
Creator / Social Platform  posts when the creator clicks Publish, or on a schedule or automatic posting the creator switched on
```

- One plugin can find moments for a video; it replaces Clips Kitty's own finding.
- Up to 3 plugins can understand and up to 3 can rate, after any finder.
- A plugin's role comes from `inputs` and `outputs` in its manifest ([Steps](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/steps.md)).
- Edit and export are not part of plugin contract 1: `job.wants("edit")` and `job.wants("export")` answer `False`, and `outputs: [edits]` and `kind: publisher` are refused as planned.

## Get listed

When your plugin works, list it in Awesome Clips Kitty, the catalog Clips Kitty's Marketplace reads: [Getting listed](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/marketplace-publishing.md#getting-listed).

## Documentation

- [Your first game pipeline](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/first-game-pipeline.md): from `new` to a plugin in Clips Kitty, step by step
- [Signals cookbook](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/signals-cookbook.md): recipes for frames, colours, loudness, scene cuts, words and the local model
- [SDK reference](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/sdk.md)
- [Developer docs](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/README.md)
- [Versioning](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/versioning.md)
- [Changelog](https://github.com/ColinGPT9/clips-studio/blob/main/sdk/python/CHANGELOG.md)
