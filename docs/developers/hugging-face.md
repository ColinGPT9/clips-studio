# Hugging Face

Using a model hosted on Hugging Face in a Clips Kitty plugin. Clips Kitty uses Hugging Face as a place to download pinned files from; it is not a Hugging Face client, and your users need no Hugging Face account for a public model.

Status: **built** (Phase 9) for public models: pinned references, the licence and size shown before download, one shared copy for every plugin, the checks on each file. **Planned:** gated models (they need a Hugging Face account and token), and finding a copy the user already downloaded with Hugging Face's own tools. Tested against a stand-in for Hugging Face; checked once by hand against the real Hub (the licence, sizes and SHA-256 of `openai/whisper-tiny` at a pinned commit, and its 2 KB `config.json` downloaded). No large model has been downloaded.

## The reference

```yaml
models:
  - name: detector
    source: huggingface
    id: example-org/example-model
    revision: 0123456789abcdef0123456789abcdef01234567
    files: [model.onnx, labels.json]
    license: apache-2.0
```

- **`revision` is a full 40-character commit.** A branch (`main`) or a tag can be moved by the model's owner; a commit can't. What your users get is exactly what you tested. To find the commit of `main` today, open the model's "Files and versions" tab on Hugging Face, or ask the API: `https://huggingface.co/api/models/<owner>/<name>/revision/main` answers with a `sha` field.
- **`files` lists only what your code loads.** Clips Kitty downloads those files, not the whole repository. Paths are inside the repository (`onnx/model.onnx`).
- **`license`** is shown to the user before download. When you leave it out, Clips Kitty shows the licence from the model card (`license:` in its metadata) when it can reach Hugging Face. Give it anyway: it is shown offline too, and it is your statement of the terms you checked.

Each file is fetched from Hugging Face's file address at that commit, `https://huggingface.co/<owner>/<name>/resolve/<commit>/<file>`, which redirects to its download servers. Clips Kitty checks every file against the size and, for files Hugging Face keeps in Git LFS (most model weights), the SHA-256 it publishes, and refuses one that doesn't match.

## Formats

Prefer **safetensors** or **ONNX**. Pickle-based files (`.bin`, `.pt`, `.pth`, `.ckpt`, `.pkl`, `.pickle`) can run code when loaded; the validator warns about them, and the user has to tick that they accept one before it downloads. Hugging Face scans files and shows the result on the model's page; a scan that hasn't run is not a clean result.

If your model is only available as a pickle file, converting it to safetensors or ONNX and publishing that (under a licence that allows it) is kinder to your users.

## Licences

Hugging Face shows a licence identifier per model (`apache-2.0`, `mit`, `cc-by-4.0`, `openrail`, `gemma`, `llama3`, `other`, …). Some allow any use, some forbid commercial use, some carry conditions of use. Read the model's terms before you list it, and say in your README what they mean for your users. Clips Kitty shows the identifier; it doesn't interpret it.

Don't copy someone else's model into your own repository or a `url` you host unless its licence allows redistribution. A Hugging Face reference downloads from the original source, which is what the platform prefers.

## Gated models

Some models need an account and the authors' approval ("gated"). Downloading one needs a Hugging Face token. Clips Kitty doesn't handle Hugging Face sign-in yet (planned), so the Marketplace explains the gate instead of trying. Until then, choose an ungated model, or document how your users get access and download it themselves.

## Sizes and hardware

Say what your model needs in the manifest's `requirements` (`gpu`, `vram_gb`, `ram_gb`, `disk_gb`). The Marketplace compares them with the user's PC where it can, and shows the download size from Hugging Face before anything is fetched.

## Running a model on Hugging Face's servers instead

Hugging Face Inference Providers and Inference Endpoints run models remotely. To your plugin they are a remote API: your code calls them, the user supplies their own key as a `secret` setting, and your manifest declares the hosts and what is sent. See [Remote APIs](remote-apis.md).

## In the catalog

[Awesome Clips Kitty](../../awesome-clips-kitty/README.md), the catalog the Marketplace reads, can list a model as an entry (`registry/models/<name>.yaml`) whose source is its Hugging Face repository, and apps and pipelines name the Hugging Face models they use. Hugging Face holds the weights and the model card; it is never where a plugin is listed from, which is always a GitHub repository ([Marketplace publishing](marketplace-publishing.md)). A model's Hugging Face downloads (last 30 days) and likes belong to the model: a listing that uses it shows them under the model's id, never added to the plugin's own numbers. They are read on a schedule into the catalog, not by the app; none have been read yet.

See also: [Model references](model-references.md).
