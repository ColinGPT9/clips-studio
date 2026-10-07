# Replicate — research notes

- Platform: Replicate (replicate.com; Cog packaging tool at github.com/replicate/cog)
- Tier: 2 (Track B)
- Date read: 2026-10-06
- Brief question: how can a developer expose a specialized AI capability through a remote API so users do not have to install the entire model locally?

**Verdict (two lines).** Replicate's pattern is: package code + weights in a Docker image described by `cog.yaml` and a typed Python predictor, push it to a registry under `owner/name`, and every push becomes an immutable 64-hex version whose input/output contract is an auto-generated OpenAPI schema; callers run it with `POST /v1/predictions` (sync `Prefer: wait`, poll `urls.get`, or signed webhooks) and pay per compute-second on their own account. The ownership question is settled: Cloudflare acquired Replicate on 1 December 2025 (Cloudflare 10-Q); the API, model URLs and Cog are unchanged as of today.

## 1. The unit of extension

The **model**: "A model is a trained, packaged, and published software program that accepts inputs and returns outputs" (https://replicate.com/docs/llms.txt, read 2026-10-06). A model is `owner/name`, owns an ordered list of **versions**, and is packaged with **Cog** ("Cog is an open source tool that makes it easy to put a machine learning model in a Docker container", https://replicate.com/docs/guides/build/push-a-model.md, read 2026-10-06). A **deployment** is a separately managed instance pool of one version with its own endpoint (`POST /v1/deployments/{owner}/{name}/predictions`).

## 2. Manifest or metadata format

### 2a. `cog.yaml` (package manifest; https://github.com/replicate/cog/blob/main/docs/yaml.md, read 2026-10-06)

"It has three keys: `build`, `image`, and `run`."
- `build.python_version` — "The minor (`3.13`) or patch (`3.13.1`) version of Python to use"; "Cog supports Python 3.10, 3.11, 3.12, and 3.13."
- `build.python_requirements` — "A pip requirements file"; may list local wheels/archives that "must stay inside the project directory"; supports `git+https://...@<commit>` pins ("at least six characters" of the SHA).
- `build.python_packages` — "**DEPRECATED**: This will be removed in future versions, please use python_requirements instead." Either/or with `python_requirements`.
- `build.system_packages` — "A list of Ubuntu APT packages to install."
- `build.gpu` — "Enable GPUs for this model ... Cog will automatically figure out what versions of CUDA and cuDNN to use."
- `build.cuda` — override CUDA version.
- `build.run` — "A list of setup commands to run in the environment after your system packages and Python packages have been installed"; supports secret mounts; "Your source code is not available to `run` commands."
- `build.sdk_version` — pin the cog Python SDK (PEP 440; minimum `0.16.0`).
- `run` — "The pointer to the `Runner` object in your code" (`run: "run.py:Runner"`). `predict` — "Deprecated compatibility field for `run`" (`predict: "predict.py:Predictor"`); "Cog will warn and `cog doctor --fix` can migrate".
- `image` — "The name given to built Docker images ... `r8.im` is Replicate's registry, but this can be any Docker registry."
- `model` — "The repository where Cog publishes an OCI bundle containing the model image and any managed weights"; cannot be set together with `image`; "Managed weights require `model`. Run `cog weights import` before pushing."
- `concurrency.max` — deprecated in favour of `@cog.concurrent(max=N)`.
- `observability.traces` — OpenTelemetry opt-in.

### 2b. The predictor/runner class (push-a-model guide, read 2026-10-06)

`class Predictor(BasePredictor)` with `setup(self)` ("Load the model into memory") and `predict(self, image: Path = Input(description=..., ...), scale: float = Input(..., default=1.5)) -> Path`. Supported input types: `str`, `int`, `float`, `bool`, `cog.File`, `cog.Path`. `Input()` arguments: `description`, `default` (absent = required; `None` = optional), `ge`, `le`, `choices`. The guide still shows `predict:`; the Cog yaml reference now prefers `run: "run.py:Runner"`.

### 2c. Model record (HTTP API, https://replicate.com/docs/reference/http, read 2026-10-06)

`models.create` body: `owner` (required; must equal the API token's account), `name` (required; unique per owner), `visibility` (required; `public` or `private`), `hardware` (required; SKU from `hardware.list`, e.g. `cpu`, `gpu-t4`, `gpu-a40-small`, `gpu-a40-large`), optional `description`, `github_url`, `paper_url`, `license_url`, `cover_image_url`. Model object adds `url`, `run_count`, `default_example`, `latest_version`. "There is a limit of 1,000 models per account."

### 2d. Version record

`models.versions.get` returns `id` (64-hex), `created_at`, `cog_version`, `openapi_schema`. "Every model describes its inputs and outputs with OpenAPI Schema Objects in the `openapi_schema` property" (`openapi_schema.components.schemas.Input` / `.Output`, with `x-order` on inputs).

## 3. Distribution and install

- Publisher side: `cog init` → edit `cog.yaml` and predictor → `cog predict -i image=@input.jpg` locally → create the model page (web `replicate.com/create` or `models.create`) → `cog login` → `cog push r8.im/<owner>/<model>`. Docker is required locally ("Cog uses Docker to create a container for your model"). What runs at "install": the Docker build (`build.run` commands, pip, apt), on the publisher's machine; nothing is installed on the user's machine.
- Consumer side: nothing to install; HTTP only. Version identifier forms accepted by `predictions.create`: `{owner_name}/{model_name}` (official models only), `{owner_name}/{model_name}:{version_id}`, or `{version_id}` ("the full 64-character version ID"). Files go in as "HTTP URLs or data URLs" (data URL for ≤256 kB).
- CI path documented: "Set up a CI/CD pipeline for your model ... publish new versions of your model as part of your GitHub-based development workflow" (llms.txt entry, read 2026-10-06).

## 4. Dependencies and isolation

One Docker image per model version; dependencies are frozen into the image from `python_requirements`/`system_packages`. There is no shared environment between models and therefore no conflict handling beyond the image. Setup commands can receive secrets through Docker secret mounts without baking them in. (yaml.md, read 2026-10-06)

## 5. Versioning and updates

- "The changes are published as new versions, so model authors can make improvements without disrupting the experience for people using older versions of the model. Versioning is essential to making machine learning reproducible" (https://replicate.com/docs/topics/models/versions.md, read 2026-10-06).
- A version id is a 64-hex hash, created by `cog push`; versions are addressed by hash and are not edited in place (the API has no version-update call; the docs do not use the word "immutable", so "immutable" is inferred from the hash addressing and the absence of an update endpoint).
- Deletion is restricted: "You can only delete versions from private models"; "You cannot delete a version if someone other than you has run predictions with it"; nor if it backs a training, a deployment, or an override (`models.versions.delete`). So a public version that anyone has used cannot be removed by its author.
- Unversioned calls exist only for **official models** ("Stable API ... without having to worry about breaking changes"; "Replicate maintains the official models", https://replicate.com/docs/topics/models/official-models.md, read 2026-10-06). All other models require the version.
- Deployments: "Rolling updates", "Canary deployments", "Instant rollbacks: Revert to previous versions if issues arise" (https://replicate.com/docs/topics/deployments.md, read 2026-10-06).

## 6. Registry design

Central hosted service: Docker registry `r8.im` plus the Replicate catalog and prediction queue. Submission is self-service (create model page, push). Discovery: `models.list` ("Get a paginated list of public models"), `models.search` (`QUERY` method, beta), `collections.*`, model README and examples. Listing is gated by "displayworthy" rules: to be featured, searchable, or "returned by the 'List models' API", a model "must have at least one version", "at least one example prediction", and "must be public" (https://replicate.com/docs/topics/models/publish-a-model.md, read 2026-10-06). Cost to run: paid platform; "You are billed for the compute time used to run your models" (llms.txt); public models bill the caller "only ... for the time it's active processing your requests. Setup and idle time for the model is free"; private models bill "all the time instances of the model are online" (https://replicate.com/docs/topics/billing.md, read 2026-10-06). The publisher of a public model is not charged for other people's runs (inferred from the billing page; not stated in those words).

## 7. Trust and permissions

- Visibility is the only tier: "Public - Anyone can run this model and see its source code. Private - Only you and collaborators ... can see this model" (https://replicate.com/docs/topics/models/create-a-model.md, read 2026-10-06). No "verified" badge is documented; "official models" is a Replicate-maintained collection, not a community verification.
- No review step before publishing is documented; the "displayworthy" criteria are mechanical.
- Isolation is the container; the model author's code runs on Replicate's hardware, not the caller's.
- Webhooks are signed: headers `webhook-id`, `webhook-timestamp`, `webhook-signature`; "Replicate uses an HMAC with SHA-256"; signed content is `${webhook_id}.${webhook_timestamp}.${body}`; the secret comes from `GET /v1/webhooks/default/secret` and the docs warn about replay attacks (https://replicate.com/docs/topics/webhooks/verify-webhook.md, read 2026-10-06).
- Data retention: "All input parameters, output values, and logs are automatically removed after an hour, by default, for predictions created through the API." Output files are served from `replicate.delivery`.
- Secret inputs for models exist ("Secrets: Learn how to create and use secret inputs in your Cog models", llms.txt).

## 8. Models

Weights are shipped inside the image ("keep your model weights in the same directory as your `predict.py` ... it will get copied into the Docker image") or as "managed weights" in an OCI bundle (`model:` key, `cog weights import`). Nothing is downloaded by the caller. Hardware is a model-level setting (`hardware` SKU) that can be changed ("Model hardware: How to change the hardware for models and deployments", llms.txt).

## 9. Local, remote or both

Remote execution is the product. Local use is limited to the publisher's own `cog predict` / `cog run` (Docker on Linux/NVIDIA; not a Windows desktop story). The same `cog.yaml` image can be pushed to "any Docker registry".

## 10. Known incidents and stated limitations

- **Ownership change.** Cloudflare's blog (17 Nov 2025; press/blog source) says "Replicate, the leading platform for running AI models, is joining Cloudflare" and "Your APIs and workflows will continue to work without interruption", with plans to "Integrate all 50,000+ Replicate models into Workers AI" (https://blog.cloudflare.com/replicate-joins-cloudflare/, read 2026-10-06). The close is confirmed by Cloudflare's Form 10-Q for the quarter ended 31 March 2026: "On December 1, 2025, the Company acquired all of the outstanding shares of Replicate ... for a total purchase consideration of $57.4 million" (https://www.sec.gov/Archives/edgar/data/0001477333/000147733326000038/cloud-20260331.htm, read 2026-10-06). Status on 2026-10-06: closed; replicate.com docs and API still served under the Replicate name.
- Sync mode waits at most 60 s (`Prefer: wait=n`, 1–60); longer jobs must poll or use webhooks. `Cancel-After` header minimum 5 s.
- Webhooks for `output`/`logs` are throttled to "at most once every 500ms"; webhooks are retried, so receivers must be idempotent.
- Public models share a hardware pool: "you will sometimes encounter cold boots or scaling limits depending on how other customers are using the model."
- Docker is a hard requirement for publishers; Cog's supported Python range is 3.10–3.13.
- Anthropic-style and OpenAI-style compatibility are not part of Replicate's API (not relevant here; noted for completeness).
- Security incidents: not searched within the Tier 2 budget; "not confirmed".

## 11. Borrow (for Clips Kitty)

1. **Typed, self-describing contract per version.** Generate an Input/Output JSON Schema (OpenAPI Schema Objects) from the pipeline's declared inputs (Cog derives it from typed function arguments with `description`, `default`, `ge`, `le`, `choices`). Clips Kitty's manifest can carry the same schema for `highlight_detection` inputs and the "scored, labelled time ranges" output (H2).
2. **Hash-addressed versions, `owner/name:version`.** A remote pipeline implementation is pinned by content hash; `owner/name` without a version is allowed only for a maintained, stable-API tier (Replicate's "official models" rule). Map to Clips Kitty: pinned tag/commit per registry entry (H3), and "latest" only for first-party pipelines.
3. **Prediction lifecycle as the remote job model.** `starting / processing / succeeded / failed / canceled`, `urls.get` / `urls.cancel`, `metrics.predict_time`, `Prefer: wait` for short jobs, webhooks with `webhook_events_filter` (`start`, `output`, `logs`, `completed`) for long ones. Clips Kitty already has a job queue and a completion webhook; adopt the status vocabulary and the "same body as GET" webhook rule.
4. **Signed, replay-resistant webhooks** (`webhook-id`, `webhook-timestamp`, HMAC-SHA256 over `id.timestamp.body`): apply to Clips Kitty's own job-completion webhook and to any remote implementation calling back into the local engine.
5. **Publish-time hygiene rules that are mechanical, not editorial**: at least one version, at least one example run, public visibility, a README/model card with limitations. Cheap to check in a CI index build.
6. **Honest deletion rules**: a version others have used cannot be deleted; make the same promise for registry entries at a pinned ref (the ref stays resolvable; the entry can be marked deprecated).
7. **Caller pays on their own account** for remote execution; the publisher hosts at $0 marginal cost. This fits "no payment processing" if remote implementations bill through the user's own provider account, as Clips Kitty already does with OpenRouter.

## 12. Avoid

1. Requiring Docker on the user's machine or in the plugin workflow; Clips Kitty is a frozen PyInstaller build on Windows.
2. Treating "public" as the only trust signal; Replicate has no verification tier and no review, so a Clips Kitty "Verified" label must come from a real process or not exist.
3. Unversioned identifiers for community implementations (Replicate allows them only for its own maintained models).
4. One-hour data expiry semantics without telling the user; if a remote implementation returns file URLs that expire, the local engine must fetch and store them immediately.
5. Copying the hosted-registry/billing design; Clips Kitty's registry is static and free (H3). What transfers is the contract and lifecycle, not the backend.

## 13. Sources (all read 2026-10-06)

- https://replicate.com/docs/guides/build/push-a-model.md — loaded (curl)
- https://github.com/replicate/cog/blob/main/docs/yaml.md — loaded via raw.githubusercontent.com (curl)
- https://replicate.com/docs/reference/http (as .md) — loaded (curl)
- https://replicate.com/docs/topics/models/versions.md — loaded (curl)
- https://replicate.com/docs/topics/deployments.md — loaded (curl)
- https://replicate.com/docs/topics/models/private-models.md — loaded (curl)
- https://replicate.com/docs/topics/models/publish-a-model.md — loaded (curl)
- https://replicate.com/docs/topics/models/official-models.md — loaded (curl)
- https://replicate.com/docs/topics/models/create-a-model.md — loaded (curl)
- https://replicate.com/docs/topics/predictions/create-a-prediction.md — loaded (curl)
- https://replicate.com/docs/topics/webhooks/verify-webhook.md — loaded (curl)
- https://replicate.com/docs/topics/billing.md — loaded (curl)
- https://replicate.com/docs/llms.txt — loaded (curl)
- https://replicate.com/docs/topics/webhooks/index.md, /topics/models/index.md, /topics/billing/index.md — HTTP 404 as .md (the non-.md topic pages exist; not re-fetched)
- https://blog.cloudflare.com/replicate-joins-cloudflare/ — loaded (WebFetch; press/blog, used for the announcement only)
- https://www.sec.gov/Archives/edgar/data/0001477333/000147733326000038/cloud-20260331.htm — loaded (WebFetch, second segment); primary source for the 1 Dec 2025 close
- Web search (standard) for the close date — used only to locate the 10-Q

## Matrix row

`| Replicate | Containerised model (Cog image: cog.yaml + typed predictor) published as owner/name with hash versions | Weights baked into the image or OCI "managed weights"; hardware SKU per model; no client download | None (one prediction per call; deployments for scaling) | Central hosted catalog + r8.im Docker registry; self-service push; "displayworthy" rules gate listing | No (CI from GitHub is a documented option, not the registry) | Publisher only (cog predict needs Docker) | Yes (predictions API, sync/poll/webhooks, deployments) | 64-hex immutable version ids; version required except for official models; restricted deletion; deployment rollbacks | public/private only; no review or verification; container isolation on Replicate's side; HMAC-signed webhooks; 1-hour data expiry | Paid per compute-second; caller pays | Auto-generated Input/Output JSON Schema per version, hash-pinned remote implementations, prediction status vocabulary + signed webhooks, mechanical publish rules |`

## Hypotheses

- **H1 (out-of-process over the local API is lowest risk): supported for the remote variant.** The implementation never runs in the caller's process; the contract is HTTP + JSON Schema; the only trust decision the caller makes is which `owner/name:version` to call and what data to send.
- **H9 (remote/hybrid execution is a legitimate path; nearest precedents): supported.** Replicate shows a working "publish once, anyone calls, caller pays on their own account" model with per-version schemas; combined with Clips Kitty's existing OpenRouter path this is the precedent for a remote `highlight_detection` implementation.
- **H2 (smallest contract is video in, scored ranges out): compatible.** Cog's typed `Input()`/`Path` and the generated Output schema express exactly such a contract; file inputs are URLs or data URLs.
- **H3 (static Git registry): not addressed by Replicate** (central service), but the "displayworthy" criteria are a good mechanical checklist for a CI-built index.
- **H5 (declared ≠ enforced): consistent.** Replicate declares nothing about permissions; isolation is real but on the server side.
