# Model references

How a plugin names the models it uses, where Clips Kitty finds them, and how one download serves every plugin that needs the same file.

Status: **built** (Phase 9): the references, finding what is already on the PC, the shared model folder, downloading Hugging Face and `url` models with their checks, and handing the paths to the plugin in `job.json`. Tested with a stand-in for Hugging Face and Ollama; no real model was downloaded in the tests. **Not built:** signing in to Hugging Face for gated models, clearing models no plugin uses any more, and a download progress bar.

## Naming a model

A manifest lists its models under `models:`. Each has a `name` (how your code finds it in the job) and a `source`:

```yaml
models:
  - name: detector
    source: huggingface
    id: example-org/example-model          # owner/name on Hugging Face
    revision: <40-character commit>         # required: a branch can change, a commit can't
    files: [model.onnx, config.json]        # only these are downloaded
    license: apache-2.0                     # shown before install
  - name: weights
    source: url
    id: https://example.com/weights.onnx
    sha256: <64 hex characters>             # required: the download is checked against it
    size_bytes: 52428800                    # optional, shown before download
  - name: chat
    source: ollama
    id: example-model:latest                # in Ollama's own store
    revision: <digest>                      # optional: only this exact build counts as installed
  - name: speech
    source: bundled
    id: whisper:small                       # a model the app ships
```

The full field list and the validator's rules are in [Plugin manifest](plugin-manifest.md#models).

| Source | Where it lives | Installed when | Who downloads it |
|---|---|---|---|
| `huggingface` | Clips Kitty's shared model folder | every listed file is there at that commit | Clips Kitty, when the user presses Download |
| `url` | Clips Kitty's shared model folder | the file is there with its SHA-256 | Clips Kitty, when the user presses Download |
| `ollama` | Ollama's own store | Ollama's `/api/tags` lists it (and the digest matches, when given) | the user, on the Models page |
| `bundled` | inside Clips Kitty | this install carries it | nobody: it ships with the app |

`bundled` covers the models Clips Kitty itself ships: `whisper:<size>` (the transcription model sizes this install carries), `yolov8n-pose` and `yolov8n` (person detection), and `panns` (the game-sound tagger). Any other `bundled` id is reported as "not a model this copy of Clips Kitty has".

A remote model (your own API, OpenRouter, Replicate, a Hugging Face Inference Provider) is not a model reference: your code calls it, and your manifest declares the hosts and what is sent. See [Remote APIs](remote-apis.md).

## What your plugin receives

`job.models` has one entry per model that is on the PC, by name:

```python
model = job.models["detector"]
model.path                      # the folder (Hugging Face), the file (url), the name:tag (Ollama), the app's path (bundled)
model.files["model.onnx"]       # each listed file's full path (Hugging Face and url)
model.revision                  # the commit, digest or SHA-256
```

The same in `job.json`: `"models": {"detector": {"source": "huggingface", "id": "...", "path": "...", "revision": "...", "files": {"model.onnx": "..."}}}`.

**A model that isn't on the PC stops the run before your plugin starts**, with a message naming it and saying where to get it ("Its AI model 'detector' isn't downloaded yet. Open Marketplace › Installed and press Download (50 MB).", the size when the manifest gives `size_bytes`). So `job.models[name]` is there whenever your code runs. The one exception is an Ollama model when Ollama isn't answering: Clips Kitty can't tell, so the run goes ahead and your code finds out when it calls Ollama.

**Loading is your job**, in your own process. Clips Kitty never loads a plugin's model: some formats run code when loaded, and a plugin's process is where that risk belongs.

## One download for every plugin

Files Clips Kitty downloads go in one folder, `plugin-models/` in Clips Kitty's data folder (`%LOCALAPPDATA%\Clips Studio\data\plugin-models` in the installed app):

```text
plugin-models/
  huggingface/models--<owner>--<name>/blobs/<sha256>              the bytes, stored once
  huggingface/models--<owner>--<name>/snapshots/<commit>/<file>    points at the blob
  files/<sha256>/<file name>                                      url models
  index.json                                                      what is where
```

- Two plugins naming the same Hugging Face file at the same commit get the same path. The second plugin's Download fetches nothing.
- A file whose SHA-256 is already in the folder, under another commit, another model or a `url` reference, is linked to the stored copy instead of fetched. Hugging Face gives the SHA-256 of large files (those it keeps in Git LFS); a small file without one is fetched, hashed, and stored once by its hash.
- "Points at" is a symbolic link where the system allows it, else a hard link (same disk, no extra space), else a copy. Windows refuses symbolic links unless Developer Mode is on, so there it is usually a hard link. The Marketplace shows which was used. Only the copy uses extra disk.
- Ollama's models and the app's own models are read where they are and never copied.

The folder is separate from `models/`, which the desktop app gives the bundled Ollama, and from the Hugging Face cache that faster-whisper uses for transcription models: Clips Kitty doesn't change where either keeps its files.

## Before downloading

The Marketplace's Installed tab shows, for each model a plugin lists: its source and id, whether it is on this PC, its licence as the manifest states it, its size once it is here, which other plugins use the same copy, and whether Windows made it a copy rather than a link. **Download…** first shows what it will fetch and how much, with the licence from the model card on Hugging Face when the manifest gives none, and only then offers Download.

- **Pickle-format files** (`.bin`, `.pt`, `.pth`, `.ckpt`, `.pkl`, `.pickle`) can run code when loaded. Downloading one needs the user to tick that they accept it. Prefer safetensors or ONNX.
- **Gated models** (Hugging Face models that need an account and the authors' approval) are explained, not attempted: Clips Kitty doesn't sign in to Hugging Face yet. The user can download them with Hugging Face's own tools; that copy isn't found by Clips Kitty yet either (planned).
- Every file is checked against the size and SHA-256 Hugging Face or the manifest gives, and refused on a mismatch. Downloads use https only and follow redirects only to https.

## Through the API

All experimental (see [API](api.md)); the ones that fetch need the session header.

| Call | Does |
|---|---|
| `GET /plugin-models` | every model the installed plugins list, once, with where it is and who uses it |
| `POST /plugin-models/plan` `{"plugin": "publisher/name", "model": "<name>"}` | what downloading it would fetch (reads Hugging Face's metadata, downloads nothing) |
| `POST /plugin-models/download` `{"plugin": ..., "model": ..., "allow_pickle": false}` | download it into the shared folder |

## Testing your references

`python -m clipskitty_sdk validate .` checks each reference's shape. To test your code with a model before publishing, install your plugin from its folder, press Download in the Marketplace (or call the routes above), then process a video with it; or run it with `python -m clipskitty_sdk run`, which hands over no models, and point your code at a local copy while developing.

See also: [Hugging Face](hugging-face.md), [Local APIs](local-apis.md), [Remote APIs](remote-apis.md).
