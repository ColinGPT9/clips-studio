# Hugging Face Hub — research notes

- Platform: Hugging Face Hub (Model Hub, `huggingface_hub` client, Inference Providers)
- Tier: 1 (Track B, external model source)
- Date read: 2026-10-06 (every page and API call below was read on this date)
- Brief's question: how can Clips Kitty use Hugging Face as an external model source without becoming Hugging Face?

**Verdict (two lines).** Treat Hugging Face as a read-only, content-addressed file store with a free metadata API: a plugin names a model as `owner/name` plus a 40-hex commit `sha`, the model manager resolves `main` to that `sha` once, downloads with `revision=<sha>` into the standard HF cache, and reads license, size, file list, gated status and scan status from `GET /api/models/{repo}` — no HF backend, no account for public models, no torch.
Do not re-implement any of it: no mirror, no registry of models, no inference proxy; the only Hugging Face-specific code Clips Kitty needs is a thin adapter over `hf_hub_download`/`snapshot_download` plus one JSON call, with pickle-format files refused or quarantined by default and the Windows symlink limitation handled explicitly.

---

## 1. The unit of extension

On the Hub the unit is the **repository**: "Models, Spaces, and Datasets are hosted on the Hugging Face Hub as Git repositories" (https://huggingface.co/docs/hub/repositories, 2026-10-06). A model repo is identified by `namespace/name` where the namespace is a user or an organization ("You can link repositories with an individual user, such as osanseviero/fashion_brands_patterns, or with an organization, such as facebook/bart-large-xsum", https://huggingface.co/docs/hub/models-uploading, 2026-10-06). Older repos without a namespace exist (`gpt2`, `bert-base-cased`), visible in cache folder names `models--bert-base-cased` (https://huggingface.co/docs/hub/local-cache, 2026-10-06).

For Clips Kitty the practical unit is smaller: **one file or file pattern inside one repo at one commit** (e.g. `model.safetensors` in `openai/whisper-tiny` at `169d4a43…`). Hugging Face is not an extension point for Clips Kitty; it is a source of bytes plus metadata.

Second, remote unit: a **model id served by one or more Inference Providers** (section 9).

## 2. Manifest or metadata format

The metadata file is the model card: "You can find a model card as the `README.md` file in any model repo", "a Markdown file, with a YAML section at the top that contains metadata about the model" (https://huggingface.co/docs/hub/model-cards.md, 2026-10-06). The spec says the Hub validates it: "Properties will be validated by the Hub when git pushing changes to your README.md file" (https://raw.githubusercontent.com/huggingface/hub-docs/main/modelcard.md, 2026-10-06).

Real field names from the spec (same file):

| YAML field | Meaning | Machine-readable use by the Hub |
|---|---|---|
| `language` | list of ISO 639-1 codes | filter |
| `license` | identifier from the license list (section 2b) | "Displaying the model's license", filter; surfaced as tag `license:<id>` |
| `license_name`, `license_link` | only when `license: other`; link may be `LICENSE` in the repo or a URL | display |
| `library_name` | e.g. `transformers`, `gguf`, `ctranslate2` | "recommended if your model is not a transformers model"; since August 2024 no longer inferred from `config.json` for new repos |
| `tags` | free list; libraries may be given as tags | computed into API `tags`, plus Hub-added tags (`arxiv:…`, `dataset:…`, `license:…`, `region:us`, `endpoints_compatible`) |
| `datasets` | Hub dataset ids | filter, "Datasets used to train:" link |
| `buckets` | storage-bucket ids | display |
| `metrics` | metric ids | with `model-index` |
| `base_model` | one id or a list (merges) | Hub infers `base_model_relation` ∈ `adapter, merge, quantized, finetune`, or set explicitly; filter "derived from" |
| `new_version` | id of the successor repo | "display a link to the latest version of a model" (chain followed to the latest) |
| `pipeline_tag` | task id (e.g. `automatic-speech-recognition`) | "users can filter models on the Hub by task", "used to determine which widget to use … and which APIs to use under the hood"; for transformers inferred from `config.json` |
| `model-index` | structured eval results (task/dataset/metrics/source, optional `verifyToken`) | parsed into the eval widget; a newer simpler format exists (`eval-results` page, not read) |
| `thumbnail` | URL | social sharing |
| `gated`, `extra_gated_prompt`, `extra_gated_fields`, `extra_gated_heading`, `extra_gated_description`, `extra_gated_button_content`, `extra_gated_eu_disallowed` | access-request form | gating (section 7) (https://huggingface.co/docs/hub/models-gated, 2026-10-06) |
| tag `not-for-all-audiences` | content warning | interstitial on the model page |

**There is no compatibility field and no version field.** Versions are git commits (section 5); the only successor pointer is `new_version`. Hardware requirements are not a metadata field; the nearest machine-readable signals are `safetensors.parameters` (parameter count by dtype) and per-file sizes (section 8). Anything else (VRAM, quantization) lives in free text or in the file names (GGUF quant suffixes).

What the API returns for these fields (live, anonymous, `GET https://huggingface.co/api/models/openai/whisper-tiny`, 2026-10-06): top-level keys `_id, author, cardData, config, createdAt, disabled, downloads, gated, id, lastModified, library_name, likes, model-index, modelId, pipeline_tag, private, safetensors, sha, siblings, spaces, tags, transformersInfo, usedStorage, widgetData`. `cardData` carried the YAML as parsed (`language`, `tags`, `widget`, `model-index`, `pipeline_tag`, `license: "apache-2.0"`). `tags` carried `license:apache-2.0`, `arxiv:2212.04356`, `safetensors`, `pytorch`, `tf`, `jax`, `hf-asr-leaderboard`, the language codes and `region:us`. `config` carried `architectures: ["WhisperForConditionalGeneration"]`, `model_type: "whisper"`.

### 2b. License identifiers

The full list of `license` values is a fixed table (https://huggingface.co/docs/hub/repositories-licenses, 2026-10-06): `apache-2.0, mit, openrail, bigscience-openrail-m, creativeml-openrail-m, bigscience-bloom-rail-1.0, bigcode-openrail-m, afl-3.0, artistic-2.0, bsl-1.0, bsd, bsd-2-clause, bsd-3-clause, bsd-3-clause-clear, c-uda, cc, cc0-1.0, cc-by-2.0, cc-by-2.5, cc-by-3.0, cc-by-4.0, cc-by-sa-3.0, cc-by-sa-4.0, cc-by-nc-2.0, cc-by-nc-3.0, cc-by-nc-4.0, cc-by-nd-4.0, cc-by-nc-nd-3.0, cc-by-nc-nd-4.0, cc-by-nc-sa-2.0, cc-by-nc-sa-3.0, cc-by-nc-sa-4.0, cdla-sharing-1.0, cdla-permissive-1.0, cdla-permissive-2.0, wtfpl, ecl-2.0, epl-1.0, epl-2.0, etalab-2.0, eupl-1.1, eupl-1.2, agpl-3.0, gfdl, gpl, gpl-2.0, gpl-3.0, lgpl, lgpl-2.1, lgpl-3.0, isc, h-research, intel-research, lppl-1.3c, ms-pl, apple-ascl, apple-amlr, mpl-2.0, odc-by, odbl, openmdw-1.0, openmdw-1.1, openrail++, osl-3.0, postgresql, ofl-1.1, ncsa, unlicense, zlib, pddl, lgpl-lr, deepfloyd-if-license, fair-noncommercial-research-license, llama2, llama3, llama3.1, llama3.2, llama3.3, llama4, grok2-community, gemma, unknown, other`. "In case of `license: other` please add the license's text to a `LICENSE` file inside your repo … and set a name for it in `license_name`." Note the list mixes SPDX-like ids with HF-specific ones (`llama3.2`, `gemma`, `openrail`): it is not SPDX.

## 3. Distribution and install

Files are served by the **resolve** endpoint `GET /{namespace}/{repo}/resolve/{rev}/{path}` — "This endpoint requires to follow redirection"; responses `302` redirect to the file, `307` redirect to the Xet endpoint, `304` not modified (https://huggingface.co/.well-known/openapi.md, 2026-10-06). Observed live (2026-10-06): `HEAD https://huggingface.co/openai/whisper-tiny/resolve/main/config.json` → `307`, headers `x-repo-commit: 169d4a4341b33bc18d8881c4b69c2e104e1cc0af`, `x-linked-etag: "417aa9de…"` (git sha1 for a small file); `HEAD …/resolve/main/model.safetensors` → `302` to `us.aws.cdn.hf.co/xet-bridge-us/…`, headers `x-linked-size: 151061672`, `x-linked-etag: "7ebd0e69…"` (sha256), `x-xet-hash: de70b2ca…`.

Clients:
- `huggingface_hub.hf_hub_download(repo_id, filename, revision=…, cache_dir=…, local_dir=…, token=…)` and `snapshot_download(repo_id, revision=…, allow_patterns=…, ignore_patterns=…)`; both have `dry_run=True` returning `DryRunFileInfo` (commit hash, file name, size, cached?) (https://huggingface.co/docs/huggingface_hub/guides/download, 2026-10-06).
- CLI `hf download <repo_id> [files…] --revision --include --exclude --cache-dir --local-dir --token --dry-run --quiet`, and `hf://[<TYPE>/]<ID>[@<REVISION>][/<PATH>]` URIs (https://huggingface.co/docs/huggingface_hub/guides/cli, 2026-10-06). The binary is `hf`; Clips Kitty's own docs/RELEASING.md already notes `huggingface-cli` "no longer works".
- `git clone https://huggingface.co/<ns>/<name>` with the Git-Xet extension for large files (https://huggingface.co/docs/hub/repositories-getting-started, 2026-10-06).
- Ollama: `ollama run hf.co/{username}/{repository}[:{quantization}]` for any public GGUF repo; `Q4_K_M` picked by default; repo files `template`, `system`, `params` override chat template and sampling; private repos need an SSH key in the HF account (https://huggingface.co/docs/hub/ollama, 2026-10-06). Where Ollama stores the result is not described on that page (it is Ollama's own store, inferred).

Storage backend: Xet. "While Git LFS remains supported … the Hub has adopted Xet, a modern custom storage system … It enables chunk-level deduplication" (https://huggingface.co/docs/hub/xet/index, 2026-10-06). `hf_xet` ships with `huggingface_hub` ("As of huggingface_hub 0.32.0, this will also install hf_xet"; `hf_transfer` is deprecated) (download guide, 2026-10-06). `HF_HUB_DISABLE_XET=1` falls back to plain HTTP (env-var reference, 2026-10-06).

**Nothing runs at install time.** A download is bytes on disk. Code runs at *load* time, and that is where the risk sits (sections 7 and 10): pickle (`pytorch_model.bin`, `.pt`, `.pkl`), Keras Lambda layers, `trust_remote_code`, and in 2026 a `config.json` field (CVE-2026-4372).

## 4. Dependencies and isolation

`huggingface_hub` base install needs: `click, filelock, fsspec, hf-xet (x86_64/amd64/arm64/aarch64 only), httpx2, packaging, pyyaml, tomli (<3.11), tqdm, typing-extensions`; `torch` is an extra (`extras["torch"] = ["torch", "safetensors[torch]"]`); `python_requires=">=3.10.0"` (https://raw.githubusercontent.com/huggingface/huggingface_hub/main/setup.py, 2026-10-06). The installation page lists `fastai`, `torch` as the framework extras and says the core features do not need them (https://huggingface.co/docs/huggingface_hub/installation, 2026-10-06). **So a pure-metadata/download mode without torch exists**: `model_info`, `list_repo_tree`, `list_repo_refs`, `resolve_revision`, `hf_hub_download`, `scan_cache_dir` all live in the base package. The HTTP API also needs no SDK at all (every call in these notes was plain `curl`, anonymous).

The cache is designed to be shared: "the central cache shared across libraries that depend on the Hub" (https://huggingface.co/docs/huggingface_hub/guides/manage-cache, 2026-10-06); the language-agnostic layout page lists `huggingface_hub` (and transformers, diffusers, datasets, mlx, vllm), Rust `hf-hub`, `swift-huggingface`, `@huggingface/hub` (Node), Java SMILE, and apps `llama.cpp` (since PR #20775), Llama-macOS, HuggingFaceModelDownloader, oMLX as users of the same on-disk format (https://huggingface.co/docs/hub/local-cache, 2026-10-06). Ollama is not in that list.

Conflicts: none at the file level (content-addressed blobs). At the loader level, the Hub does nothing: it is up to the loading library's environment. Download is concurrency-safe via `<CACHE_DIR>/.locks/<repo_folder>/<blob_hash>.lock` (local-cache page, 2026-10-06).

## 5. Versioning and updates

- Every repo is git: branches, tags, pull-request refs (`refs/pr/N`), commits. `revision` accepts "a specific branch, a PR, a tag or a commit hash"; "When using the commit hash, it must be the full-length hash instead of a 7-character commit hash" (download guide, 2026-10-06). The cache resolver treats a revision as a commit only "If the revision is already a 40-character hex string" (local-cache page, 2026-10-06). The HTTP API itself accepted a 7-char hash live (`/api/models/openai/whisper-tiny/revision/169d4a4` → 200 with the full `sha`), so the full-hash rule is a client/cache rule, not a server rule — pin the full 40 characters anyway.
- **Evidence of the mechanism** (anonymous, 2026-10-06):
  - `GET /api/models/openai/whisper-tiny` → `"sha": "169d4a4341b33bc18d8881c4b69c2e104e1cc0af"`, `"lastModified": "2024-02-29T10:57:33.000Z"`.
  - `GET /api/models/openai/whisper-tiny/revision/main` → identical body (same `sha`, same `etag: W/"2055-UBIg+…"`).
  - `GET /api/models/openai/whisper-tiny/refs` → `{"tags": [], "branches": [{"name": "main", "ref": "refs/heads/main", "targetCommit": "169d4a4341b33bc18d8881c4b69c2e104e1cc0af"}], "converts": []}` — this repo has no tags; `main` is the only ref.
  - `GET /api/models/openai/whisper-tiny/commits/main` → 50 commits; newest `169d4a43… "Add missing merge to tokenizer (#40)" 2024-02-29`.
  - `GET /api/models/openai/whisper-tiny/revision/169d4a4341b33bc18d8881c4b69c2e104e1cc0af?expand[]=sha&expand[]=lastModified` → `{"id":"openai/whisper-tiny","sha":"169d4a43…","lastModified":"2024-02-29T10:57:33.000Z"}`.
  - The resolver stamps every file response with `x-repo-commit: <full sha>`.
- `HfApi.resolve_revision(repo_id, revision=…)` returns a `ResolvedRevision` (a `str` subclass whose `.resolved` is the commit hash); download helpers "detect a ResolvedRevision and use the commit hash directly. Every file is guaranteed to come from the same commit, and once the files are cached no HTTP call is needed at all"; the mapping is written to `refs/` and used as an offline fallback; otherwise `RevisionResolutionError` (manage-cache guide, 2026-10-06). The docstring adds: "two calls made a few seconds apart can land on two different commits if the repo is updated in between" (hf_api.py source, 2026-10-06).
- Immutability: a commit is immutable ("A commit is immutable, so its file list never changes", manage-cache guide, `trees/` cache), but the repo is not: the OpenAPI spec lists `DELETE /api/models/{ns}/{repo}/tag/{rev}`, `DELETE …/branch/{rev}` and `POST …/super-squash/{rev}` (openapi.md, 2026-10-06), so an owner can delete tags/branches, rewrite history, make the repo private, gate it, or delete it. A pinned sha is only as durable as the owner's repo.
- Rollback: the cache "keeps the previous file intact in case you need it again"; `hf cache ls --revisions`, `hf cache rm <repo|hash>`, `hf cache prune` (removes revisions "no longer referenced by a branch or tag", `.incomplete` files, orphaned shared blobs), `hf cache verify <repo> [--revision <sha>]` checks checksums against the Hub (manage-cache guide, 2026-10-06).
- Deprecation: `new_version` in the card (section 2). No notion of yanking.
- Staleness check without download: `HF_HUB_ETAG_TIMEOUT` (default 10 s) governs the metadata call made before serving a cached file; `HF_HUB_OFFLINE=1` skips it and raises `OfflineModeIsEnabled` for `HfApi` calls (env-var reference, 2026-10-06).

## 6. Registry design

A **central service** run by Hugging Face Inc. Publishing is free with an account; no pre-publication review: the content policy (dated April 10, 2025) describes reporting, takedown and moderation actions, and no pre-publication step (https://huggingface.co/content-policy, 2026-10-06). Upload paths: web UI, `hf upload`, `git push`, library `push_to_hub`, `PyTorchModelHubMixin` (models-uploading page, 2026-10-06).

Discovery API (live, anonymous, 2026-10-06): `GET /api/models?author=openai&search=whisper&limit=3&sort=downloads&direction=-1&expand[]=sha&expand[]=safetensors&expand[]=gated` returned `openai/whisper-large-v3-turbo` (sha `41f01f3f…`, downloads 6,318,983), `openai/whisper-large-v3`, `openai/whisper-small`; `library=ctranslate2&pipeline_tag=automatic-speech-recognition` is also a valid filter pair (it returned repos whose `library_name` was not ctranslate2, so filter semantics beyond "tag present" are not confirmed). Inference-provider filters: `?inference_provider=all|<name>` (https://huggingface.co/docs/inference-providers/hub-api, 2026-10-06).

Reference documentation: "We've moved the Hub API Endpoints documentation to our OpenAPI Playground … You can also access the OpenAPI specification directly at https://huggingface.co/.well-known/openapi.json, or in Markdown version … https://huggingface.co/.well-known/openapi.md" (https://huggingface.co/docs/hub/api, 2026-10-06). Downloaded 2026-10-06: `openapi: 3.1.0`, `info.version: 0.0.1`, 298 paths, 29 under `/api/models…`. **The spec does not contain `GET /api/models`, `GET /api/models/{namespace}/{repo}` or `GET /api/models/{namespace}/{repo}/revision/{rev}`** (only `/api/kernels/{namespace}/{repo}/revision/{rev}` has a revision entry). The response schema for model info is therefore "not confirmed" from the spec; the working references are (a) the `huggingface_hub` `ModelInfo` dataclass and `ExpandModelProperty_T`, and (b) the server's own validation error, which enumerates the valid `expand[]` values: `author, baseModels, cardData, config, createdAt, disabled, downloads, downloadsAllTime, evalResults, gated, inference, inferenceProviderMapping, lastModified, library_name, likes, mask_token, model-index, pipeline_tag, private, safetensors, sha, siblings, spaces, tags, transformersInfo, trendingScore, widgetData, gguf, resourceGroup, xetEnabled, childrenModelCount, usedStorage` (live error on an invalid value, 2026-10-06). Documented in the spec: `/tree/{rev}/{path}` (`expand`, `recursive`, `limit` "1.000 by default, 100 by default for expand=true", `cursor`), `/refs` (`include_prs`), `/commits/{rev}` (`p`, `expand`, `limit`), `/scan` ("Get the security status of a repo"), `/paths-info/{rev}` (POST), `/treesize/{rev}/{path}`.

Cost to Clips Kitty: $0, bounded by **rate limits** (https://huggingface.co/docs/hub/rate-limits, 2026-10-06, "current rate limits (in September '25)", 5-minute fixed windows): Anonymous per IP — API 500, Resolvers 3,000, Pages 100; Free user — 1,000 / 5,000 / 200; PRO — 2,500 / 12,000 / 400; "Anonymous and Free users are subject to change over time depending on platform health". Exceeding gives HTTP 429; headers `RateLimit` and `RateLimit-Policy` (IETF draft). Observed live on the anonymous metadata call: `ratelimit: "api";r=472;t=235` and `ratelimit-policy: "fixed window";"api";q=500;w=300`; on the resolver: `"resolvers";q=3000;w=300`. Advice on the page: "make sure you always pass a HF_TOKEN", "replace Hub API calls with Resolver calls, whenever possible"; `huggingface_hub` ≥ 1.2.0 parses the header and retries after the exact reset delay. Other per-action limits (repo creation, commits) are undocumented.

Download counting needs no client telemetry: "No information is sent from the user, and no additional calls are made for this. The count is done server-side as the Hub serves files"; default query files `config.json, config.yaml, hyperparams.yaml, params.json, meta.yaml`; "Every HTTP request to these files, including GET and HEAD, will be counted"; GGUF files are all counted (https://huggingface.co/docs/hub/models-download-stats, 2026-10-06). `downloads` is "over the last 30 days", `downloads_all_time` cumulative (ModelInfo docstring, 2026-10-06).

## 7. Trust and permissions

There is **no permission model for models**: a repo is files. Trust signals the Hub exposes:

| Signal | Where | What it means |
|---|---|---|
| `gated: false \| "auto" \| "manual"` | model info | access requests on; "auto" grants on agreeing, "manual" needs author approval; "The model authors have complete control … they can decide at any time to block your access … without prior notice" (models-gated, 2026-10-06). Requesting access "can only be done from your browser"; downloads then need a user token (`hf auth login`, `login()`, `token=` or `HF_TOKEN`). Live: `google/gemma-3-1b-it` → `"gated": "manual"`, `cardData.license: "gemma"`, `extra_gated_prompt: "To access Gemma … Requests are processed immediately."`; anonymous `resolve/main/config.json` → `HTTP 401`, `x-error-code: GatedRepo`, `x-error-message: Access to model google/gemma-3-1b-it is restricted. You must have access to it and be authenticated to access it. Please log in.`, `www-authenticate: Bearer realm="Authentication required"`. Metadata (sha, cardData, safetensors, siblings) stays readable anonymously. |
| `private`, `disabled` | model info | private repos need a token; disabled repos are blocked by HF |
| GPG "Verified" badge | commit list | "The commit is signed and the signature is verified" against a key uploaded to the account; the pickle page says signing "does not guarantee that your file is safe, but it does guarantee the origin of the file" (security-gpg, security-pickle, 2026-10-06) |
| Security scanners | file page badges; API | see below |
| `author` namespace | model info | organizations vs users; organization "verified" badges: not confirmed (page not read) |

Scanning (all public repos, each commit):
- ClamAV: "every file of your repositories that is smaller than 2 GB", "Scanning is triggered at each commit"; "If at least one file has been scanned as unsafe, a message will warn the users" — a warning, not a block (https://huggingface.co/docs/hub/security-malware, 2026-10-06).
- Pickle import scan: "Every time you upload a `pytorch_model.bin` or any other pickled file, this scan is run"; it uses `pickletools.genops` "which allows us to read the file without executing"; "***Disclaimer***: this is not 100% foolproof. It is your responsibility as a user to check if something is safe or not … the safe/unsafe imports lists we have are maintained in a best-effort manner" (https://huggingface.co/docs/hub/security-pickle, 2026-10-06).
- Third-party: Protect AI Guardian ("All public model repositories will be scanned by Guardian automatically", https://huggingface.co/blog/protectai, 2024-10-22, read 2026-10-06), JFrog ("built with the goal to reduce false positives", https://huggingface.co/docs/hub/security-jfrog, 2026-10-06), TruffleHog secrets scanning on each push (https://huggingface.co/docs/hub/security-secrets, 2026-10-06).
- **How it is exposed in the API** (live, anonymous, 2026-10-06):
  - `GET /api/models/{repo}?securityStatus=true` → `securityRepoStatus: {"scansDone": bool, "filesWithIssues": [{"path", "level"}]}`. The documented test repo `mcpotato/42-eicar-street` returned `scansDone: true` with `model_broken_X.pkl` → `unsafe`, `danger.dat` → `unsafe`, `build_pickles.py` → `caution`, `eicar_test_file` → `unsafe`. `openai/whisper-tiny` returned `scansDone: false, filesWithIssues: []` even though every file's per-file status was `safe` — so `scansDone:false` must be read as "unknown", never as "clean". `GET /api/models/{repo}/scan` returns the same object.
  - `GET /api/models/{repo}/tree/{rev}/{path}?expand=true` → per file `securityFileStatus: {status: safe|caution|unsafe, protectAiScan, avScan, pickleImportScan {status, pickleImports:[{module,name,safety: innocuous|suspicious|dangerous}], version}, virusTotalScan, jFrogScan}` plus `lastCommit {id,title,date}`, `size`, `oid`, `lfs {oid,size,pointerSize}`, `xetHash`. Example: `openai/whisper-tiny/pytorch_model.bin` → `pickleImportScan.status: safe` with imports `torch.FloatStorage`, `collections.OrderedDict`, `torch._utils._rebuild_tensor_v2` all `innocuous`; `jFrogScan: "Safe Pickle-based model"`; `model.safetensors` → `jFrogScan: "Safe model, does not support code execution on load."`. `danger.dat` → `pickleImports: [{module: "builtins", name: "eval", safety: "dangerous"}]`, `avScan: "Detected malware signatures: Py.Malware.CodeExec_builtins_eval_STACK_GLOBAL"`. The Protect AI report links now point at `insights-db.paloaltonetworks.com` (observed; reason not confirmed).
- The Hub **does not block** flagged files: JFrog's 2024 report states the platform "marks them as 'unsafe'" while allowing download (vendor research, https://jfrog.com/blog/data-scientists-targeted-by-malicious-hugging-face-ml-models-with-silent-backdoor/, 2024-02-27, read 2026-10-06), consistent with the docs' "it is your responsibility".

Format safety (https://raw.githubusercontent.com/huggingface/safetensors/main/README.md and https://huggingface.co/docs/safetensors/index, 2026-10-06): safetensors = "8 bytes: N, an unsigned little-endian 64-bit integer, containing the size of the header", "N bytes: a JSON UTF-8 string representing the header", "Rest of the file: byte-buffer"; "A special key `__metadata__` is allowed to contain free form string-to-string map"; "The byte buffer needs to be entirely indexed, and cannot contain holes. This prevents the creation of polyglot files"; comparison table: pickle "Unsafe, runs arbitrary code"; SafeTensors safe, zero-copy, lazy loading, layout control, no custom code; H5 "Some classic use after free issues"; npz "Vulnerable to zip bombs". The header-size limit "of 100MB to prevent parsing extremely large JSON" is a design note in the README. GGUF "encodes both the tensors and a standardized set of metadata" and has a Hub viewer (https://huggingface.co/docs/hub/gguf, 2026-10-06); ONNX is not discussed on these pages (Clips Kitty already ships ONNX models fetched from the Hub, `scripts/fetch_voice_model.py`).

"Verified" on the Hub therefore means exactly one of: a signature matched a key (commits), or a scanner found nothing in a file. Neither is a review of behaviour.

## 8. Models: reference, download, cache, share

Reference = `(repo_id, revision, filename|patterns)`; URL form `https://huggingface.co/{ns}/{name}/resolve/{sha}/{path}`; URI form `hf://{ns}/{name}@{sha}/{path}`.

Cache layout (https://huggingface.co/docs/hub/local-cache and the manage-cache guide, 2026-10-06):
- Root `~/.cache/huggingface/hub`, overridden by `HF_HUB_CACHE` (priority) or `HF_HOME` (then `$HF_HOME/hub`). `HF_HOME` also holds the token (`$HF_HOME/token`, or `HF_TOKEN_PATH`) and the Xet shard cache (`$HF_HOME/xet`, `HF_XET_CACHE`). `huggingface_hub` writes a `CACHEDIR.TAG` so backup tools skip it.
- Folder `{type}s--{owner}--{name}` (`models--openai--whisper-tiny`), containing `refs/` (one text file per branch/tag/PR holding the full commit hash), `blobs/` (flat; file name = etag: SHA-1 for git-tracked files, SHA-256 for LFS/Xet files), `snapshots/<commit>/<path>` (symlinks `../../blobs/<hash>`), `.no_exist/<commit>/<path>` (empty markers for files known to be absent), `trees/<commit>.json` (cached file list; "re-running a download when everything is already cached costs a single network call: the one needed to resolve the branch or tag name into a commit hash").
- Sharing: "If a file is unchanged between two revisions, both symlinks point to the same blob with no data duplication"; worked example: a 321 MB `pytorch_model.bin` "is stored only once on disk" across two snapshots. Cross-repo sharing for Xet files: stored once at `<CACHE_DIR>/blobs/<prefix>/<xet_hash>` with the repo's `blobs/<etag>` a relative symlink to it; "requires the symlink-based cache layout"; opt out with `HF_HUB_DISABLE_SHARED_BLOBS=1`.
- **Windows without symlink permission** (quoted): "symlinks are not supported on all machines. This is a known limitation especially on Windows. When this is the case, huggingface_hub does not use the blobs/ directory but directly stores the files in the snapshots/ directory instead … However, the cache-system is less efficient as a single file might be downloaded several times if multiple revisions of the same repo are downloaded." (manage-cache guide). The Hub page: "the cache operates in a degraded mode: actual file copies are placed directly in snapshots/ instead of symlinks. The blobs/ directory is not used in this mode. This means the same file content may be duplicated across revisions, increasing disk usage. To enable symlink support on Windows, activate Developer Mode or run as administrator." (local-cache). Env: `HF_HUB_DISABLE_SYMLINKS=1` forces the copy mode ("huge files ends-up duplicated on your hard-drive"); `HF_HUB_DISABLE_SYMLINKS_WARNING=1` silences the warning "to warn you about this behavior" (env-var reference). Cross-repo shared blobs are also disabled in that mode. Disk cost: one full copy per cached revision of each file; a Clips Kitty user who keeps two revisions of a 1.5 GB whisper repo pays 3 GB, not 1.5 GB. The installation page adds a second Windows limit: Hub file paths may contain characters Windows cannot store ("impossible to download those files on Windows") (installation page, 2026-10-06).
- Alternative `local_dir=` mode: files land in a normal folder plus `.cache/huggingface/` metadata; "optimized for pulling only the latest changes"; no blob sharing (download guide, 2026-10-06).
- Inspection: `scan_cache_dir()` → `HFCacheInfo` (`size_on_disk`, repos → `CachedRepoInfo` → `CachedRevisionInfo` (`commit_hash`, `refs`, `files`) → `CachedFileInfo` (`blob_path`, `size_on_disk`)); `try_to_load_from_cache()` answers "is this file cached?" with no HTTP (manage-cache guide, 2026-10-06).
- Size before download: `GET /api/models/{repo}?blobs=true` adds `size`, `blobId`, `lfs.sha256` to each sibling (live: `model.safetensors` 151,061,672 B, `pytorch_model.bin` 151,095,027 B, `tf_model.h5` 151,253,960 B, `flax_model.msgpack` 151,048,591 B, `usedStorage` 1,831,289,730 B for the whole repo across history) ; `/tree/{rev}` gives the same per file; `safetensors: {"parameters": {"F32": 37760640}, "total": 37760640}` gives parameter count and dtype without opening the file.
- Telemetry: HF libraries send usage telemetry by default; `HF_HUB_DISABLE_TELEMETRY=1` or `DO_NOT_TRACK=1` disables it globally; `HF_HUB_DISABLE_IMPLICIT_TOKEN=1` stops sending a stored token on read calls (env-var reference, 2026-10-06).

How Clips Kitty touches the Hub today (read in the repo, 2026-10-06): `scripts/fetch_voice_model.py` already pins **full commit hashes in resolve URLs and verifies SHA-256** (`Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/f0c48c29…/voxceleb_resnet34_LM.onnx`); `scripts/fetch_asd_model.py` and `multilingual/voices.py` use unpinned `resolve/main/…`; `scripts/fetch_whisper.py` delegates to `faster_whisper.utils.download_model` (whether that pins a revision is not confirmed here); `third_party/talknet/talkNet.py` loads weights with `torch.load(path)` (pickle); `ui/electron-builder.yml` uses an HF model repo's `resolve/main` as a release feed.

## 9. Local, remote or both

Both.
- Local: everything above.
- Remote: **Inference Providers** — "Your request routes through HF to the provider", single `HF_TOKEN` (fine-grained token with "Make calls to Inference Providers"), OpenAI-compatible `https://router.huggingface.co/v1/chat/completions` (chat only; other tasks via `InferenceClient`), provider policy suffixes on the model id `:fastest` (default), `:cheapest`, `:preferred`, or `:<provider>`; `GET https://router.huggingface.co/v1/models` lists chat models with per-provider `pricing` (USD per million tokens), `context_length`, `supports_tools`, latency/throughput (https://huggingface.co/docs/inference-providers/index and /hub-api, 2026-10-06). Partners table lists Speech-to-text for Fal AI, HF Inference, Replicate, Together (index page, 2026-10-06).
- Mapping a model id to providers: `GET /api/models/{repo}?expand[]=inference` → `"warm"` or absent; `?expand[]=inferenceProviderMapping` → `{<provider>: {status: live|staging, providerId, task, isModelAuthor}}` (hub-api page, 2026-10-06). `openai/whisper-tiny` has neither (checked live: no `inference` field).
- Billing is on the user's HF account: "Free Users: $0.10, subject to change" monthly credits, PRO "$2.00", pay-as-you-go after that "credits purchase required", "no markup from Hugging Face"; or a custom provider key set in HF settings, then "Billed directly by the provider" and credits do not apply; `X-HF-Bill-To` / `bill_to=` for organizations (https://huggingface.co/docs/inference-providers/pricing, 2026-10-06). HF-Inference "focuses mostly on CPU inference" as of July 2025 (same page).

## 10. Known incidents and stated limitations

Incidents (official or vendor security research unless marked press):
- 2024-02-27, JFrog (vendor research): "around 100 instances" of models with harmful payloads; `baller423/goober2` PyTorch pickle opened a reverse shell to 210.117.212.93:4242; the Hub labelled such files "unsafe" but still served them (https://jfrog.com/blog/data-scientists-targeted-by-malicious-hugging-face-ml-models-with-silent-backdoor/, read 2026-10-06).
- 2024-05-31, Hugging Face disclosure: unauthorized access to Spaces secrets; "a subset of Spaces' secrets could have been accessed"; HF tokens revoked, org tokens removed, KMS for Spaces secrets, fine-grained tokens made default; model repos not mentioned as affected (https://huggingface.co/blog/space-secrets-disclosure, read 2026-10-06).
- 2025-02-06, ReversingLabs (vendor research), "nullifAI": two repos (`glockr1/ballr7`, `who-r-u0000/0000000000000000000000000000000000000`) with 7z-compressed, deliberately broken pickles; payload at the start of the stream executes before the error; Picklescan "showed an error … but failed to detect the presence of dangerous functions"; HF removed them "in less than 24 hours" and changed Picklescan (https://www.reversinglabs.com/blog/rl-identifies-malware-ml-model-hosted-on-hugging-face, read 2026-10-06).
- 2026-03-02, GHSA-g38g-8gr9-h9xp / CVE-2026-56315, critical 9.8: "picklescan v1.0.3 (latest) does not block at least 7 Python standard library modules that provide direct arbitrary command execution … reported as having 0 issues (CLEAN scan)"; fixed in 1.0.4 (https://github.com/advisories/GHSA-g38g-8gr9-h9xp, read 2026-10-06).
- 2026-05-26, CVE-2026-4372 / GHSA-29pf-2h5f-8g72, transformers < 5.3.0: a `config.json` with `_attn_implementation_internal` pointing at an attacker repo makes `from_pretrained()` download and execute code "bypasses the trust_remote_code security mechanism" when the `kernels` package is installed (https://advisories.gitlab.com/pypi/transformers/CVE-2026-4372/ citing the GitHub advisory and fix commit, read 2026-10-06). Consequence: even a repo with only safetensors and JSON can be a code-execution vector through the *loader*.
- 2026-07-16, Hugging Face disclosure: an intrusion into production infrastructure; "A malicious dataset abused two code-execution paths in our dataset processing (a remote-code dataset loader and a template-injection in a dataset configuration)"; "unauthorized access to a limited set of internal datasets and to several credentials used by our services"; "We have found no evidence of tampering with public, user-facing models, datasets, or Spaces, and our software supply chain (container images and published packages) was verified clean"; users told "we recommend rotating any access tokens" (https://huggingface.co/blog/security-incident-july-2026, read 2026-10-06). Press (The Hacker News, 2026-07-20) adds that the actor was an autonomous agent system (press, not relied on).
- 2026-05, press/analyst reports (TheNextWeb 2026-05-08; Cloud Security Alliance research notes 2026-05) describe "hundreds" of malicious models and HF repos used as payload staging for the ClawHub/OpenClaw skill campaign. Acronis TRU (vendor) only confirms "a limited, yet notable set of Spaces, datasets and models primarily used for either directly hosting payloads or … a staging point" and says the full extent is hard to measure (https://www.acronis.com/tru/posts/poisoning-the-well-ai-supply-chain-attacks-on-hugging-face-and-openclaw/, read 2026-10-06). The "hundreds" figure is press; not confirmed from a primary source.

Stated limitations:
- Scanning is best-effort and does not block (section 7); `securityRepoStatus.scansDone` can be `false` on a widely used repo.
- Gating is revocable at any time by the author; access requests are browser-only.
- Anonymous API quota 500 / 5 min per IP (shared by every Clips Kitty user behind one NAT) and "subject to change".
- Owners can delete, squash, privatise or rename repos; pinned hashes do not survive that.
- Windows: symlink-less degraded cache; unrepresentable file names.
- The OpenAPI spec omits the model-info endpoints (section 6).
- `HF_HUB_OFFLINE`/timeouts: a cached file still triggers a metadata HTTP call unless offline mode is set.

## 11. Borrow

1. **Pin by `(repo_id, sha)` and record it in the manifest.** Resolve once with `HfApi.resolve_revision()` (or `GET /api/models/{repo}/revision/{ref}` and read `sha`), store the 40-hex hash, download with `revision=<sha>`. `scripts/fetch_voice_model.py` already does the URL form of this plus SHA-256; generalise that pattern instead of `resolve/main` (as in `fetch_asd_model.py`, `multilingual/voices.py`, `ui/electron-builder.yml`).
2. **Read, do not download, to decide.** One anonymous call per model: `GET /api/models/{repo}?blobs=true&securityStatus=true` (or `/revision/{sha}` with the same params) gives license, sizes, gated, sha and scan state. Budget it against 500/5 min per IP and cache the JSON under the sha (a commit's file list never changes).
3. **Use the standard HF cache, not a Clips Kitty store.** Point `HF_HUB_CACHE` (or `HF_HOME`) at a Clips Kitty-chosen directory, let `huggingface_hub` manage `refs/blobs/snapshots`, and make the model manager an index over `scan_cache_dir()` plus Ollama's list plus local files (this is the brief's H7 direction). Models already cached by other apps on the same machine are then free.
4. **Handle Windows symlinks explicitly.** Detect symlink support at first run (the library already warns); show the user the choice "enable Developer Mode (dedup) or accept duplicate copies per revision"; set `HF_HUB_DISABLE_SYMLINKS_WARNING=1` only after the user has seen the message once. Never silently set `HF_HUB_DISABLE_SYMLINKS=1`.
5. **Prefer safetensors/ONNX/GGUF; refuse pickle by default.** From the `siblings` list, select `*.safetensors`, `*.onnx`, `*.gguf`, `*.json`; `ignore_patterns=["*.bin", "*.pt", "*.pth", "*.pkl", "*.ckpt", "*.h5", "*.msgpack"]` unless the plugin manifest declares the format and the user confirms; show the Hub's `pickleImportScan` imports when it does. Treat `scansDone:false` as unknown, `caution`/`unsafe` as a hard warning.
6. **Treat loaders as code.** CVE-2026-4372 shows a JSON config can trigger code execution in a loader; the model manager should pass files to the pipeline process, never import a model's `config.json` into the engine process itself (consistent with the brief's H1).
7. **Show license from the id list**, with `license_name`/`license_link` for `other`, and surface `gated` before any download with the Hub's own `extra_gated_prompt` text and a link to the model page (access must be granted in the browser).
8. **Surface `safetensors.total` and per-file bytes** as the "size/hardware" hint; do not invent VRAM numbers the Hub does not have.
9. **Model ids as `namespace/name`** with `pipeline_tag` as the capability tag: the same namespacing Clips Kitty plans for `publisher/pipeline` (H8). `base_model` and `new_version` are worth copying as optional manifest fields for plugins (lineage and successor pointers).
10. **Remote inference as a user-account feature, like OpenRouter today**: `router.huggingface.co/v1` is OpenAI-compatible, so the existing provider-agnostic LLM backend can add it with a base URL and the user's `HF_TOKEN`; only models with `inference: "warm"` are usable.
11. **Dry-run before download** (`dry_run=True` / `hf download --dry-run`) to show bytes-to-fetch, and `hf cache verify`/checksums (`x-linked-etag` sha256) after.
12. Counting without telemetry (H10): HF counts downloads server-side from request logs; Clips Kitty's static registry has no server, so it can read HF `downloads`/`likes` at index-build time as a signal, the same way it would read GitHub stars.

## 12. Avoid

1. Do not mirror, proxy or re-host model files, and do not run a model registry: the Hub already is one, and rehosting inherits its moderation and license problems.
2. Do not pin to `main`, a tag or a 7-character hash in manifests; tags and branches can be deleted or moved (`DELETE …/tag/{rev}`, `super-squash` exist).
3. Do not call the search/list API per user action from the desktop app; anonymous quota is per IP and 500/5 min across *all* API endpoints.
4. Do not treat a "safe" badge, a "Verified" commit or `scansDone` as a safety review; the scanner has had critical bypasses (nullifAI 2025, CVE-2026-56315) and the Hub does not block flagged files.
5. Do not load pickle-based weights (`torch.load` on `.pt/.bin/.pth`) from community plugins' model references inside the engine process; Clips Kitty's own `third_party/talknet` already does `torch.load(path)` for a vendored file, which is a reason to keep that path internal and pinned.
6. Do not embed or require an HF token for public models; keep tokens optional and user-owned, store them in the OS keychain rather than `$HF_HOME/token`, and set `HF_HUB_DISABLE_IMPLICIT_TOKEN=1` so read calls on public repos never carry the token.
7. Do not copy the HF cache layout into a bespoke format; use the library and its documented layout so Ollama-style third-party tools and `hf cache` keep working.
8. Do not depend on the OpenAPI spec for the model-info response shape; it is absent there — code against `ModelInfo` and the observed fields, with every field optional.
9. Do not assume Windows users have symlinks or that long/odd Hub paths are storable; fail with a message, not a traceback.
10. Do not send HF telemetry from a desktop app without consent (`HF_HUB_DISABLE_TELEMETRY=1` by default).

## 13. Sources (all read 2026-10-06)

Official documentation and specs
- https://huggingface.co/docs/hub/model-cards.md — loaded
- https://raw.githubusercontent.com/huggingface/hub-docs/main/modelcard.md — loaded (metadata spec)
- https://huggingface.co/docs/hub/repositories-licenses — loaded
- https://huggingface.co/docs/hub/models-gated — loaded
- https://huggingface.co/docs/hub/security — loaded
- https://huggingface.co/docs/hub/security-pickle — loaded
- https://huggingface.co/docs/hub/security-malware — loaded
- https://huggingface.co/docs/hub/security-protectai — loaded
- https://huggingface.co/docs/hub/security-jfrog — loaded
- https://huggingface.co/docs/hub/security-secrets — loaded
- https://huggingface.co/docs/hub/security-gpg — loaded
- https://huggingface.co/docs/hub/api — loaded (points to OpenAPI)
- https://huggingface.co/.well-known/openapi.md and https://huggingface.co/.well-known/openapi.json — loaded (openapi 3.1.0, info.version 0.0.1, 298 paths; model-info endpoints absent)
- https://huggingface.co/docs/hub/rate-limits — loaded
- https://huggingface.co/docs/hub/local-cache — loaded
- https://huggingface.co/docs/hub/repositories — loaded
- https://huggingface.co/docs/hub/repositories-getting-started — loaded
- https://huggingface.co/docs/hub/models-uploading — loaded
- https://huggingface.co/docs/hub/models-download-stats — loaded
- https://huggingface.co/docs/hub/gguf — loaded
- https://huggingface.co/docs/hub/ollama — loaded
- https://huggingface.co/docs/hub/xet/index — loaded
- https://huggingface.co/content-policy — loaded (policy dated 2025-04-10)
- https://huggingface.co/docs/huggingface_hub/guides/download — loaded (docs version v2.1.1)
- https://huggingface.co/docs/huggingface_hub/guides/manage-cache — loaded
- https://huggingface.co/docs/huggingface_hub/guides/cli — loaded (first 100k chars)
- https://huggingface.co/docs/huggingface_hub/installation — loaded
- https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables — loaded
- https://huggingface.co/docs/huggingface_hub/package_reference/hf_api — loaded only to 100k chars (model_info not in that range); replaced by the source below
- https://raw.githubusercontent.com/huggingface/huggingface_hub/main/src/huggingface_hub/hf_api.py — loaded (`ModelInfo`, `ExpandModelProperty_T`, `model_info`, `resolve_revision`, `GitRefs`, `RepoSibling`, `BlobSecurityInfo`)
- https://raw.githubusercontent.com/huggingface/huggingface_hub/main/setup.py — loaded (dependency list)
- https://huggingface.co/docs/safetensors/index — loaded (format diagram is an image; text from README below)
- https://raw.githubusercontent.com/huggingface/safetensors/main/README.md — loaded
- https://huggingface.co/docs/inference-providers/index — loaded
- https://huggingface.co/docs/inference-providers/pricing — loaded
- https://huggingface.co/docs/inference-providers/hub-api — loaded
- https://huggingface.co/docs/inference-providers/hub-integration — loaded

Live API calls (anonymous curl, 2026-10-06)
- GET https://huggingface.co/api/models/openai/whisper-tiny — 200; `sha 169d4a4341b33bc18d8881c4b69c2e104e1cc0af`; headers `ratelimit: "api";r=472;t=235`, `ratelimit-policy: "fixed window";"api";q=500;w=300`
- GET https://huggingface.co/api/models/openai/whisper-tiny/revision/main — 200, same body
- GET https://huggingface.co/api/models/openai/whisper-tiny/revision/169d4a4341b33bc18d8881c4b69c2e104e1cc0af?expand[]=sha&expand[]=lastModified — 200
- GET https://huggingface.co/api/models/openai/whisper-tiny/revision/169d4a4?expand[]=sha — 200 (server accepts short hash; client/cache does not)
- GET https://huggingface.co/api/models/openai/whisper-tiny?securityStatus=true&blobs=true — 200 (`securityRepoStatus.scansDone: false`; sizes per sibling)
- GET https://huggingface.co/api/models/openai/whisper-tiny?expand[]=…&expand[]=securityRepoStatus — 400 with the enumerated list of valid `expand` values
- GET https://huggingface.co/api/models/openai/whisper-tiny/tree/main and …/tree/main?expand=true — 200 (per-file `securityFileStatus`)
- GET https://huggingface.co/api/models/openai/whisper-tiny/refs — 200; …/commits/main — 200 (50 commits); …/scan — 200
- HEAD https://huggingface.co/openai/whisper-tiny/resolve/main/config.json — 307; …/model.safetensors — 302 (headers quoted in section 3)
- GET https://huggingface.co/api/models/mcpotato/42-eicar-street?securityStatus=true and …/tree/main?expand=true — 200 (unsafe/caution examples)
- GET https://huggingface.co/api/models/google/gemma-3-1b-it?expand[]=gated… — 200 (`gated: "manual"`); HEAD/GET https://huggingface.co/google/gemma-3-1b-it/resolve/main/config.json — 401 `GatedRepo`
- GET https://huggingface.co/api/models?author=openai&search=whisper&limit=3&sort=downloads&direction=-1&expand[]=sha… — 200

Incidents and advisories
- https://huggingface.co/blog/space-secrets-disclosure — loaded (2024-05-31)
- https://huggingface.co/blog/protectai — loaded (2024-10-22)
- https://huggingface.co/blog/security-incident-july-2026 — loaded (2026-07-16)
- https://jfrog.com/blog/data-scientists-targeted-by-malicious-hugging-face-ml-models-with-silent-backdoor/ — loaded (vendor research, 2024-02-27)
- https://www.reversinglabs.com/blog/rl-identifies-malware-ml-model-hosted-on-hugging-face — loaded (vendor research, 2025-02-06)
- https://github.com/advisories/GHSA-g38g-8gr9-h9xp — loaded (picklescan, CVE-2026-56315, 2026-03-02)
- https://advisories.gitlab.com/pypi/transformers/CVE-2026-4372/ — loaded (aggregator citing GHSA-29pf-2h5f-8g72; the GitHub advisory page itself was not opened)
- https://www.acronis.com/tru/posts/poisoning-the-well-ai-supply-chain-attacks-on-hugging-face-and-openclaw/ — loaded (vendor research, 2026)
- https://thehackernews.com/2026/07/worlds-largest-ai-model-repository.html — loaded (press, 2026-07-20; used only to locate the official disclosure)
- https://thenextweb.com/news/hugging-face-clawhub-malware-ai-supply-chain — seen in search results only (press, 2026-05-08); not loaded
- https://labs.cloudsecurityalliance.org/wp-content/uploads/2026/05/CSA_research_note_huggingface_model_supply_chain_attack_20260512-csa-styled.pdf — fetch returned unreadable binary; not confirmed

Not read / not confirmed
- Organization "verified" badge semantics on the Hub — not confirmed.
- Whether `faster_whisper.utils.download_model` pins a revision — not confirmed.
- The hub-docs `eval-results` page (newer eval metadata format) — not read.

---

## Matrix row

`| Hugging Face Hub | Model repository (git repo `owner/name`; files at a commit) plus remote model ids on Inference Providers | Native: safetensors/GGUF/ONNX/pickle files, model-card YAML (license, pipeline_tag, library_name, base_model), per-file sizes and scan status via API | None (models only; no workflows) | Central service run by Hugging Face; free publishing, no pre-publication review; search + metadata HTTP API, OpenAPI spec (model-info endpoints missing from it) | Yes: every repo is git (branches, tags, `refs/pr/N`, full 40-hex commit `sha`); resolver stamps `x-repo-commit` | Yes: `hf_hub_download`/`snapshot_download`/`hf download`, shared cache `refs/ blobs/ snapshots/` (blobs shared across revisions; Windows without symlinks duplicates per revision) | Yes: Inference Providers, OpenAI-compatible router, billed to the user's HF account ($0.10/month free credits, PRO $2) | Pin `revision=<full sha>`; `resolve_revision()`; cache keeps old snapshots; `hf cache ls/rm/prune/verify`; `new_version` successor pointer; owners can delete/squash | Scanners (ClamAV <2 GB, pickle import scan, Protect AI, JFrog, VirusTotal, TruffleHog) label files safe/caution/unsafe but never block; gating = author-granted, token-authenticated; GPG "Verified" = signature only; bypasses in 2025–2026 | None for models (free); Inference Providers is pay-as-you-go with no markup | Reference models as `owner/name@sha`, read one metadata JSON before downloading (license, sizes, gated, scan), reuse the standard HF cache as one of the model manager's stores, refuse pickle by default, handle Windows symlinks explicitly, add `router.huggingface.co/v1` as an optional LLM provider on the user's account |`

## Hypotheses

- **H1 (out-of-process plugins, lowest risk)** — Supported indirectly. Model files are data, yet loading them executes code: pickle (docs), Keras Lambda (Protect AI page), and since CVE-2026-4372 a plain `config.json` can pull code into `from_pretrained()`. Model loading belongs in the plugin/pipeline process, not the engine (section 10).
- **H3 (static Git registry, no backend)** — Supported for the model part. Everything Clips Kitty needs from Hugging Face is one anonymous GET per model, cacheable by sha; no HF-side backend is required. The cost is the anonymous API quota (500 per 5 min per IP) and the need to cache metadata at index-build time rather than per client.
- **H5 (declared vs enforced permissions)** — Not applicable to models; the Hub has no permission concept for repos beyond private/gated. Its "safe" badges are declarations by scanners, not enforcement: the Hub does not block flagged downloads (section 7).
- **H6 (dependencies)** — Supported: `huggingface_hub` has no torch dependency (setup.py), so metadata + download can live in the engine's environment; any framework-specific loader stays in the plugin's environment.
- **H7 (revision pins to a commit hash; cache shares blobs; Windows symlinks; prefer safetensors)** — **Confirmed with evidence.** `main` → `sha 169d4a4341b33bc18d8881c4b69c2e104e1cc0af` via `/api/models/…` and `/revision/main`; `refs` → `targetCommit`; `x-repo-commit` on every resolve; `revision=` takes the full hash ("must be the full-length hash"); cache `snapshots/<sha>/<file>` symlinks to `blobs/<etag>` so "the 321 MB file is stored only once on disk" across revisions; on Windows without Developer Mode/admin the cache "does not use the blobs/ directory but directly stores the files in the snapshots/ directory" and "a single file might be downloaded several times if multiple revisions … are downloaded" (`HF_HUB_DISABLE_SYMLINKS`, `HF_HUB_DISABLE_SYMLINKS_WARNING`). Safetensors is the Hub's recommended format; the pickle scanner is documented as "not 100% foolproof" and had critical bypasses (2025 nullifAI, 2026 CVE-2026-56315). Caveat: a pinned sha is not durable against owner deletion or `super-squash`.
- **H8 (namespaced ids + declared capabilities + typed I/O)** — Supported by analogy. Hub ids are `namespace/name`; `pipeline_tag` is a declared capability (`automatic-speech-recognition`) under which many implementations coexist (`openai/whisper-*`, `pyannote/*`, `argmaxinc/whisperkit-coreml` all returned for the same tag); `base_model`/`base_model_relation` express lineage; `new_version` expresses succession. Typed I/O is not modelled by the Hub beyond the task tag.
- **H10 (counts need telemetry; opt-in)** — Partly supported. The Hub counts downloads server-side "No information is sent from the user", which a static GitHub-hosted registry cannot replicate. HF `downloads` (30-day) / `downloads_all_time` / `likes` / `trendingScore` are readable anonymously and can be folded into the index at build time like GitHub stars.
