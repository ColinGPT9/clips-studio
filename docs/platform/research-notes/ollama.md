# Ollama — research notes

- Platform: Ollama (docs.ollama.com; github.com/ollama/ollama)
- Tier: 3 (Track B)
- Date read: 2026-10-06
- Brief question: can Ollama models fit the same model abstraction as Hugging Face models?

**Verdict (two lines).** Yes for the identity layer: an Ollama model is `[host/][namespace/]model[:tag][@digest]` (defaults `registry.ollama.ai/library/<model>:latest`), content-addressed by a SHA-256 digest, stored as `blobs/sha256-<hex>` under `OLLAMA_MODELS`, and introspectable with `/api/tags` and `/api/show` (license, details, capabilities, model_info); Hugging Face GGUF repos map straight in via `hf.co/{owner}/{repo}[:{quant}]`. Differences the abstraction must carry: Ollama's "model" is a Modelfile bundle (weights + template + parameters + system + license), it is GGUF/safetensors-for-LLMs only (no YOLO/PANNs), LoRA adapters are no longer supported, and `ollama.com` is also a cloud inference host.

## 1. The unit of extension

The **model** as built by a Modelfile: "A Modelfile is the blueprint to create and share customized models using Ollama" (https://docs.ollama.com/modelfile.md, read 2026-10-06). A model = base weights (`FROM`) + runtime parameters + prompt template + system message + license + optional message history, built with `ollama create` / `POST /api/create`, shared with `ollama push` / `POST /api/push`.

## 2. Manifest or metadata format

### 2a. Modelfile instructions (modelfile.md, read 2026-10-06)

| Instruction | Doc text |
| - | - |
| `FROM` (required) | "Defines the base model to use." Forms: `FROM <model name>:<tag>`, `FROM <model directory>` ("should contain the Safetensors weights for a supported architecture"), `FROM ./ollama-model.gguf`, split GGUF `FROM ./model-*.gguf`. |
| `PARAMETER` | "Sets the parameters for how Ollama will run the model." Documented keys include `num_ctx`, `repeat_last_n`, `repeat_penalty`, `temperature`, `seed`, `stop`, `num_predict`, `draft_num_predict`, `top_k`, `top_p`, `min_p`. |
| `TEMPLATE` | "The full prompt template to be sent to the model." Go `text/template`; variables `{{ .System }}`, `{{ .Prompt }}`, `{{ .Response }}`. |
| `SYSTEM` | "Specifies the system message that will be set in the template." |
| `LICENSE` | "Specifies the legal license." |
| `MESSAGE` | "Specify message history." roles `system`, `user`, `assistant`. |
| `REQUIRES` | "Specify the minimum version of Ollama required by the model." (`REQUIRES 0.14.0`) |
| `CAPABILITY` | "Declare an additional model capability." (`CAPABILITY decision`) |

`ADAPTER` is **not** in the current instruction table. In the source at commit `efe43c5` (sparse clone of github.com/ollama/ollama, read 2026-10-06), `server/create.go` defines `errAdaptersUnsupported = errors.New("LoRA adapters are no longer supported")` and rejects `Adapters` in create requests with HTTP 400, and `metadata.Kind() == "adapter"` sources with the same error. Treat ADAPTER as removed.

"the **`Modelfile` is not case sensitive**"; "Instructions can be in any order."

### 2b. Name grammar (types/model/name.go at `efe43c5`, read 2026-10-06)

```
{ host } "/" { namespace } "/" { model } ":" { tag } "@" { digest }
...
{ model }
"@" { digest }
```
Defaults: `defaultHost = "registry.ollama.ai"`, `defaultNamespace = "library"`, `defaultTag = "latest"`. Length limits: host ≤ 350, namespace/model/tag/digest ≤ 80 characters. User-published models are `username/model` ("The `Username` field will be used as part of your model's name (e.g. `jmorganca/mymodel`)", https://docs.ollama.com/import.md, read 2026-10-06).

### 2c. `GET /api/tags` fields (https://docs.ollama.com/api/tags.md, read 2026-10-06)

`models[]` of `ModelSummary`: `name`, `model`, `remote_model` ("Name of the upstream model, if the model is remote"), `remote_host`, `modified_at`, `size` ("Total size of the model on disk in bytes"), `digest` ("SHA256 digest identifier of the model contents"), `details` {`format` (e.g. `gguf`), `family`, `families`, `parameter_size` (e.g. `7B`), `quantization_level` (e.g. `Q4_0`)}.

### 2d. `POST /api/show` fields (https://docs.ollama.com/api-reference/show-model-details.md, read 2026-10-06)

Request `model` (required), `verbose`. Response: `license` ("The license of the model"), `modelfile` is not listed in the schema but `parameters` ("Model parameter settings serialized as text"), `template`, `details` (`parent_model`, `format`, `family`, `families`, `parameter_size`, `quantization_level`), `capabilities` ("List of supported features", e.g. `completion`, `thinking`, `vision`), `model_info` (GGUF key/value metadata such as `general.architecture`, `gemma4.context_length`, `tokenizer.ggml.*`), `thinking` {`values`, `default`}.

### 2e. `POST /api/create` fields (https://docs.ollama.com/api/create.md, read 2026-10-06)

`model`, `from`, `files` (map of filename → `sha256:<hex>` blob digest, e.g. `"model.gguf": "sha256:432f..."`), `draft_files`, `template`, `license` ("License string or list of licenses"), `system`, `parameters`, `quantize` ("Quantization level to apply during import (e.g. `nvfp4`)"), `draft_quantize`. The import page adds: "Ollama does not quantize GGUF models during import."

## 3. Distribution and install

- Pull: `POST /api/pull` with `model`, `insecure` ("Allow downloading over insecure connections"), `stream`; streams `StatusEvent` {`status`, `digest`, `total`, `completed`} (https://docs.ollama.com/api/pull.md, read 2026-10-06). Registry protocol is OCI-like: the client requests `v2/{namespace}/{model}/manifests/{tag}` and `v2/{namespace}/{model}/blobs/{digest}` (`server/images.go`, `server/download.go` at `efe43c5`).
- Push: `ollama cp mymodel myuser/mymodel && ollama push myuser/mymodel` after adding the Ollama public key (`~/.ollama/id_ed25519.pub`) to the account (import.md).
- From Hugging Face: `ollama run hf.co/{username}/{repository}` and `hf.co/{username}/{repository}:{quantization}` (e.g. `:Q8_0`, `:IQ3_M`, case-insensitive, or the full `.gguf` filename as tag); "By default, the `Q4_K_M` quantization scheme is used, when it's present inside the model repo"; template/system/params can be overridden by files named `template` (Go template), `system`, `params` (JSON) in the HF repo; private repos work after adding the Ollama SSH key to the HF account (https://huggingface.co/docs/hub/en/ollama, read 2026-10-06). This syntax is documented by Hugging Face; the docs.ollama.com index (llms.txt) has no page for it (checked 2026-10-06). The Ollama server source allows redirects to `"ollama.com", "ollama.ai", "hf.co", "huggingface.co"` (`server/images.go` `allowedRedirectHosts`, commit `efe43c5`).
- What runs at install: a download and manifest write only; no scripts.

## 4. Dependencies and isolation

One runtime (the Ollama server, llama.cpp/MLX backends) serves every model; a model carries no code, only weights and text metadata, so there is no dependency conflict surface. `REQUIRES <version>` is the only compatibility declaration.

## 5. Versioning and updates

- No semantic versions. Identity is `name:tag` plus the content `digest`; `@digest` pins exactly (name grammar). `:latest` is a moving tag.
- `ollama pull` on an existing tag fetches the new manifest; the old layers stay only while referenced (inferred from the OCI-style layout; not stated in the docs read).
- API: "Ollama's API isn't strictly versioned, but the API is expected to be stable and backwards compatible. Deprecations are rare and will be announced in the release notes" (https://docs.ollama.com/api/introduction.md, read 2026-10-06).

## 6. Registry design

Central registry `registry.ollama.ai` / library at https://ollama.com/library, run by Ollama; publishing requires an ollama.com account and a registered public key; no review step is documented. Cost: not published. Hugging Face acts as a second registry through the `hf.co/` host prefix.

## 7. Trust and permissions

- Models are data (GGUF/safetensors + text); no code execution path is documented for a pulled model. `insecure: true` on pull/push disables transport checks and is the only security toggle documented.
- No verification tiers; `library/` (no namespace) is Ollama's own namespace, `username/` is community.
- Server binds `127.0.0.1:11434` by default; `OLLAMA_HOST` opens it (https://docs.ollama.com/faq.md, read 2026-10-06). Cloud models through the local server require signing in; the FAQ documents `disable_ollama_cloud` in `~/.ollama/server.json`.

## 8. Models: reference, download, cache, share

- Reference: `name:tag` / `@digest`; HF repos as `hf.co/owner/repo:quant`.
- Storage: "Where are models stored? macOS: `~/.ollama/models`; Linux: `/usr/share/ollama/.ollama/models`; Windows: `C:\Users\%username%\.ollama\models`"; "set the environment variable `OLLAMA_MODELS` to the chosen directory" (faq.md, read 2026-10-06; `envconfig/config.go` reads `OLLAMA_MODELS`).
- Layout (source at `efe43c5`, read 2026-10-06): `manifest/paths.go` joins `envconfig.Models()` with `legacyDirName = "manifests"` or `v2DirName = "manifests-v2"` (v2 canonical host `ollama.com`; legacy default host `registry.ollama.ai`), then the name's `Filepath()`, documented in `types/model/name.go` as `{ host } "/" { namespace } "/" { model } "/" { tag }`; so a library model lives at `<OLLAMA_MODELS>/manifests/registry.ollama.ai/library/<model>/<tag>`. The manifest is OCI-style JSON (`manifest/manifest.go`): `schemaVersion`, `mediaType`, `config`, `layers[]` each with `mediaType`, `digest`, `size`, optional `from` and `name`. Blobs are content-addressed files under `blobs/` named `sha256-<hex>`; "a manifest spells a digest with ':', a filename with '-'" (`manifest/paths.go`; `server/fixblobs.go` renames legacy `sha256:` files). The Modelfile doc shows the same path: `FROM /Users/pdevine/.ollama/models/blobs/sha256-00e1...`. A `metadata` subdirectory holds GGUF metadata JSON keyed by digest (`server/gguf_metadata.go`).
- Sharing between models: two Modelfiles with the same `FROM` share the weight blob by digest (inferred from content addressing; `ollama show --modelfile` prints the blob path as the `FROM`).
- Cloud: the same API serves cloud models from `https://ollama.com/api` with an API key; `/api/tags` marks these with `remote_model`/`remote_host`.

## 9. Local, remote or both

Both: local server at `http://localhost:11434/api` (also `/v1` OpenAI-compatible and an Anthropic-compatible base URL) and hosted `https://ollama.com/api`. "Creating or deleting models requires a local Ollama server." (api/introduction.md)

## 10. Known incidents and stated limitations

- LoRA adapters: removed ("LoRA adapters are no longer supported", source at `efe43c5`); the brief's `ADAPTER` instruction no longer exists in the docs.
- Only LLM-shaped artifacts: GGUF or safetensors "for a supported architecture"; no path for detectors (YOLO), OCR or audio taggers.
- "Ollama does not quantize GGUF models during import."
- The `hf.co/` pull syntax is documented on Hugging Face's site, not on docs.ollama.com (as of 2026-10-06).
- Structured output: `format` "Supports either the string `"json"` or a JSON schema object" on `/api/generate` and `/api/chat` (https://docs.ollama.com/api/generate.md and /api/chat.md, read 2026-10-06), which Clips Kitty's scorer can use for typed range output.
- Security incidents: not searched within the Tier 3 budget; "not confirmed".

## 11. Borrow (for Clips Kitty's shared model manager)

1. **One record shape for both sources.** Store every model as {`source` (`hf` | `ollama` | `local`), `ref` (HF `owner/repo@revision` or Ollama `host/namespace/model:tag@digest`), `digest`, `size`, `format`, `quantization_level`, `license`, `capabilities`}; `/api/tags` + `/api/show` supply all of these for Ollama; the HF hub API supplies the rest for HF. The `hf.co/owner/repo:quant` bridge means one HF GGUF repo can be the *same* record under both sources.
2. **Content digest as the pin.** Use `@digest` (Ollama) and commit hash (HF, H7) as the only "pinned" form; treat `:latest`/`main` as floating and say so in the UI.
3. **Introspect, do not trust the manifest.** Read `license`, `details.family`, `model_info.*.context_length` and `capabilities` from the running server at install time, the way `/api/show` exposes them, and show them next to the plugin's declared needs.
4. **`REQUIRES <version>` and `CAPABILITY`** are tiny, honest compatibility declarations; mirror them in the plugin manifest (`requires_engine`, `requires_capabilities`).
5. **JSON-schema `format`** for the LLM scoring step gives typed "scored, labelled time ranges" (H2) without parsing prose.
6. **Expose `OLLAMA_MODELS`** in the model manager so the user can point Ollama and Clips Kitty's own cache at the same drive; do not copy blobs.

## 12. Avoid

1. Modelling non-LLM models (YOLOv8, RapidOCR, PANNs, faster-whisper) as Ollama models; Ollama has no slot for them. The abstraction must be source-agnostic with Ollama as one backend.
2. Depending on `ADAPTER`/LoRA through Ollama; it is gone.
3. Relying on `:latest`; always resolve and record the digest after pull.
4. Using `insecure: true` anywhere in Clips Kitty's pull path.
5. Assuming the local server is private: it is loopback by default, but `OLLAMA_HOST` can expose it with no authentication, the same class of risk as Clips Kitty's own engine on 127.0.0.1:8765.

## 13. Sources (all read 2026-10-06)

- https://docs.ollama.com/llms.txt — loaded (curl)
- https://docs.ollama.com/modelfile.md — loaded (curl)
- https://docs.ollama.com/api.md (Introduction) and https://docs.ollama.com/api/introduction.md — loaded (curl)
- https://docs.ollama.com/import.md — loaded (curl)
- https://docs.ollama.com/faq.md — loaded (curl)
- https://docs.ollama.com/api/tags.md — loaded (curl)
- https://docs.ollama.com/api-reference/show-model-details.md — loaded (curl)
- https://docs.ollama.com/api/pull.md, /api/push.md, /api/create.md, /api/generate.md, /api/chat.md — loaded (curl)
- https://huggingface.co/docs/hub/en/ollama — loaded (WebFetch)
- https://raw.githubusercontent.com/ollama/ollama/main/types/model/name.go — loaded (curl)
- https://raw.githubusercontent.com/ollama/ollama/main/server/manifest.go, server/modelpath.go, docs/faq.md — HTTP 404 (paths no longer exist)
- github.com/ollama/ollama sparse shallow clone (server, types, docs, envconfig, manifest) at commit `efe43c5` — read only, nothing executed; files cited: `types/model/name.go`, `server/create.go`, `server/images.go`, `server/download.go`, `server/fixblobs.go`, `server/gguf_metadata.go`, `envconfig/config.go`, `manifest/paths.go`, `manifest/manifest.go`, `manifest/layer.go`
- Web search (standard) for Ollama's own `hf.co` documentation — only Hugging Face and third-party pages found

## Matrix row

`| Ollama | Model bundle built from a Modelfile (FROM + PARAMETER + TEMPLATE + SYSTEM + LICENSE + REQUIRES + CAPABILITY) | Content-addressed GGUF/safetensors LLMs; name:tag@digest; /api/tags + /api/show introspection; OLLAMA_MODELS cache; hf.co/owner/repo:quant pulls | None | Central registry.ollama.ai (library/ + username/) plus Hugging Face as a second host; no review | No | Yes (localhost:11434) | Yes (ollama.com cloud, same API) | Tags are floating, digests pin; REQUIRES min engine version; API "not strictly versioned" | Models are data only; insecure flag; loopback by default, no auth | None (free library) | Single record shape across HF/Ollama/local keyed by digest; introspect license/capabilities at install; REQUIRES/CAPABILITY-style declarations; JSON-schema format for typed outputs |`

## Hypotheses

- **H7 (HF revision pins to a commit; cache shares blobs; prefer safetensors, warn on pickle): consistent on Ollama's side.** Ollama pins by `@digest`, stores blobs content-addressed (`blobs/sha256-<hex>`, shared by every manifest that lists the same layer digest), and imports safetensors or GGUF only (no pickle path), so an HF model and an Ollama model can share one digest-keyed record. Windows symlink behaviour is not an Ollama concern (plain files under `C:\Users\%username%\.ollama\models`).
- **H2 (video in, scored ranges out): supported for the LLM scoring step** via JSON-schema `format`.
- **H5 (declared ≠ enforced): consistent.** `REQUIRES`/`CAPABILITY` are declarations the server reads; nothing about permissions exists because models carry no code.
- **H6 (dependency handling): not applicable** (no per-model code or packages).
