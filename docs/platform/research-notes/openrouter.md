# OpenRouter — research notes

- Platform: OpenRouter (openrouter.ai)
- Tier: 2 (Track B)
- Date read: 2026-10-06
- Brief question: can Clips Kitty have a common interface for multiple implementations of the same video-AI capability (for example `highlight_detection` implemented by Clips Kitty, Open Shorts, Community A, Community B and a remote API)?

**Verdict (two lines).** Yes, and OpenRouter is the clearest working example: one namespaced identifier (`author/slug`, optionally `:variant`) names a catalog entry with typed I/O metadata, and behind each entry sits a list of competing *endpoints* (provider implementations) selected at request time by declared preferences (`order`, `only`, `ignore`, `allow_fallbacks`, `require_parameters`, `data_collection`) plus live health. The parts not to copy are the central paid backend, vendor-gated provider onboarding, price-weighted load balancing, and "bring your own implementation" being Enterprise-only.

## 1. Unit of extension

Two units, layered:

- **Model (catalog entry)**: identified by `id` = `author/slug` (every one of the 464 entries returned anonymously on 2026-10-06 contains a `/`; 64 distinct author prefixes). This is the thing users name in requests.
- **Endpoint**: one provider's implementation of a model. `GET /api/v1/models/{author}/{slug}/endpoints` returns `endpoints[]`, each with its own `provider_name`, `quantization`, `pricing`, `context_length`, `supported_parameters`, `status`, `uptime_last_30m/5m/1d`. For `google/gemma-3-27b-it` the anonymous call returned several endpoints (DeepInfra fp8, Parasail fp8, ...), i.e. several implementations of one capability under one id. (read 2026-10-06, https://openrouter.ai/api/v1/models/google/gemma-3-27b-it/endpoints)
- Users do not extend the catalog. **Providers** do, via a form and a self-hosted `/v1/models` document (section 3). Enterprise customers can add a **private endpoint** to an existing catalog model's provider list ("OpenRouter adds your deployment to that model's provider list instead of creating a new model, so your team calls it with the same slug and API", https://openrouter.ai/docs/guides/routing/private-models.md, read 2026-10-06).

## 2. Manifest / metadata format

### 2a. Catalog entry as returned by `GET /api/v1/models` (observed anonymously, 2026-10-06)

Top-level response keys: `data`, `total_count` (464), `links` (`{"next": null}`).
Real top-level keys of one entry (`inclusionai/ling-3.1-flash`):

`id`, `canonical_slug`, `hugging_face_id`, `name`, `created`, `description`, `context_length`, `architecture`, `pricing`, `top_provider`, `per_request_limits`, `supported_parameters`, `default_parameters`, `supported_voices`, `knowledge_cutoff`, `expiration_date`, `links`, `benchmarks`, `reasoning`

Sub-objects observed:
- `architecture`: `modality` (e.g. `"text->text"`, `"text+image+file+audio+video->text"`), `input_modalities` (array of `text`/`image`/`file`/`audio`/`video`), `output_modalities`, `tokenizer`, `instruct_type`.
- `pricing`: strings in USD per unit: `prompt`, `completion`, and across the catalog also `request`-type keys `image`, `image_output`, `audio`, `audio_output`, `input_cache_read`, `input_cache_write`, `input_cache_write_1h`, `input_audio_cache`, `internal_reasoning`, `web_search`, `overrides`.
- `top_provider`: `context_length`, `max_completion_tokens`, `is_moderated`.
- `links.details`: path to the endpoints listing (e.g. `/api/v1/models/inclusionai/ling-3.1-flash-20261002/endpoints`).
- `benchmarks`: `artificial_analysis` {`intelligence_index`, `coding_index`, `agentic_index`}, `design_arena`.
- `reasoning`: `mandatory`, `default_enabled`.
- `alias_target` (`slug`, `name`) appears on `~author/family-latest` alias entries (e.g. `~deepseek/deepseek-flash-latest` → `deepseek/deepseek-v4.1-flash`).

The reference page documents the same fields plus `expiration_date` ("ISO 8601 date string ... or null if no expiration"), `knowledge_cutoff`, `per_request_limits` {`prompt_tokens`, `completion_tokens`}, and a long list of query filters (`category`, `supported_parameters`, `output_modalities`, `input_modalities`, `context`, `min_price`/`max_price`, `providers`, `zdr`, `region`, `q`, `sort`, pagination `offset`/`limit` max 1000). (https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties, read 2026-10-06)

Observed catalog statistics (2026-10-06): 464 entries; variant suffixes present in ids: `:free` (16), `:batch` (73); 177 entries carry a non-null `hugging_face_id`; 83 entries list `video` among `input_modalities`; `expiration_date` is set on a handful (e.g. `poolside/laguna-s-2.1`, `2026-10-31`).

### 2b. Endpoint entry (observed, 2026-10-06)

`name`, `model_id`, `model_name`, `context_length`, `pricing`, `provider_name`, `tag` (e.g. `deepinfra/fp8`), `quantization`, `max_completion_tokens`, `max_prompt_tokens`, `supported_parameters`, `supports_tool_choice` {`none`,`auto`,`required`,`function`}, `status`, `uptime_last_30m`, `uptime_last_5m`, `uptime_last_1d`, `supports_implicit_caching`, `native_tools`, `latency_last_30m`, `throughput_last_30m`, plus feature booleans.

### 2c. Provider-side "manifest" (what a provider must publish)

A provider "must implement an endpoint that returns all models that should be served by OpenRouter. Each model is described as a set of typed **input and output modality objects**: every modality owns its capabilities, constraints, passthrough parameters, pricing, and capacity." Root fields include `id` ("the exact model identifier that OpenRouter will use when calling your API"), `quantization` (enum `int4`...`fp32` or null), `tokenizer`, `datacenters`, `deployment_region`, `openrouter.slug` ("The OpenRouter slug this model maps to"), a deprecation date, `is_ready`, `is_free`, `service_tier`, `discount_to_user`. "A document must declare at least one input modality and at least one output modality." All `cost_usd` fields are strings. (https://openrouter.ai/docs/guides/community/for-providers.md, read 2026-10-06)

### 2d. Identifier grammar (model variants)

"A variant is a suffix appended to a model ID with a colon." Two kinds:
- **Catalog variants** (`:free`, `:batch`; `:thinking` and `:extended` deprecated): "separate entries in `GET /api/v1/models` with their own metadata. Only the models that list them support them." "Sending a catalog suffix on a model that has no such entry does not fall back to the base model. The single-model lookup returns `404`, the endpoint lookup returns `200` with an empty `endpoints` array."
- **Routing variants** (`:nitro`, `:floor`, `:exacto`; `:online` deprecated): "accepted on any model ID at request time. They are not listed in `GET /api/v1/models` and change only how the request is routed."
- Combination: "A model ID carries at most one catalog variant and any number of routing variants"; "the last one in the ID determines the sort."
- Resolution rule for clients: split on `:`, keep the catalog variant, drop routing variants. "Do not strip every suffix."
- `canonical_slug` is a dated permaslug (`inclusionai/ling-3.1-flash` → `inclusionai/ling-3.1-flash-20261002`); the private-endpoints API calls it `model_permaslug`.
(https://openrouter.ai/docs/guides/routing/model-variants/overview.md, read 2026-10-06)

## 3. Distribution and install

Nothing is installed; everything is a remote HTTPS API. For providers: "fill out our form" (https://openrouter.ai/how-to-list); implement the `/v1/models` document and an OpenAI-compatible inference API; "when OpenRouter's provider monitor sees a new model in your `/v1/models` response, it auto-stages the endpoint, runs baseline tests, and unhides it (makes it live) once the tests pass and pricing is configured." Providers must support auto top-up or invoicing so "we must be able to pay for inference automatically." (for-providers.md, read 2026-10-06)

Private endpoints (Enterprise): four steps "Blueprint / Connect / Test / Activate"; the Test step "confirm[s] the key is accepted, the response is OpenAI-compatible, streaming works, usage fields are returned, and the served model ID matches what you entered." A private endpoint must "serve a model that already exists in the OpenRouter catalog, with the same request and response shape." (private-models.md, read 2026-10-06)

## 4. Dependencies and isolation

Not applicable locally. Each endpoint runs in the provider's infrastructure; OpenRouter normalises the request/response shape and filters endpoints by capability (`require_parameters: true` = "Only use providers that support all parameters in request"). (https://openrouter.ai/docs/guides/routing/provider-selection, read 2026-10-06)

## 5. Versioning and updates

- Stable `id` vs dated `canonical_slug`/permaslug; `~author/x-latest` aliases carry `alias_target` so the concrete version is visible ("Latest Model Resolution" page listed in llms.txt).
- `expiration_date` on entries; provider documents can carry a deprecation date: "When OpenRouter's provider monitor detects a deprecation date or time, it will automatically update the endpoint to display deprecation warnings to users. Models past their deprecation time may be automatically hidden from the marketplace." (for-providers.md)
- Variants are deprecated with a `Status` column in the docs table rather than removed silently (`:thinking`, `:extended`, `:online` marked Deprecated with a pointer to the replacement). (model-variants overview)
- Rollback/pinning for users: pin the dated permaslug (inferred from `canonical_slug` and `model_permaslug`).

## 6. Registry design

Central service operated by OpenRouter (database + provider monitor + baseline tests + uptime tracking). Submission: provider form, then provider-hosted `/v1/models` document polled by OpenRouter; model requests from users go "in our Discord channel" (https://openrouter.ai/docs/guides/overview/models.md, read 2026-10-06). No pull-request path. Cost to run: not published; it is a paid intermediary (pricing per token is in every entry). The catalog read is public: although the reference page states an API key is required, the anonymous `curl https://openrouter.ai/api/v1/models` returned HTTP 200 with 464 entries on 2026-10-06. Note the docs: "`GET /api/v1/models` is a catalog of models and catalog variants. It is not an exhaustive list of every model string a request can use."

## 7. Trust and permissions

- Provider admission is gated by OpenRouter (form + baseline tests + payment arrangement).
- Runtime health is measured, not declared: uptime = "successful requests ÷ total requests (excluding user errors)"; "100+ requests required before uptime calculation begins"; "95%+ uptime" normal, "80-94%" degraded/lower priority, "<80%" "only used as fallback". Default routing prioritises "providers that have not seen significant outages in last 30 seconds". (for-providers.md; provider-selection)
- Data policy is **declared, not verified**: OpenRouter shows "retention policies as reflected in each provider's terms", with per-endpoint `dataPolicy` fields (`retentionDays`, `retainsPrompts`, `training`); users filter with `data_collection: "deny"` ("Use only providers which do not collect user data") and `zdr: true` ("Only be routed to endpoints that do not retain prompts"). (https://openrouter.ai/docs/guides/privacy/provider-logging.md, read 2026-10-06)
- `top_provider.is_moderated` exposes whether the serving provider moderates content.
- Terms: "You may not violate the terms of service or policies of third-party providers that power the models on OpenRouter."

## 8. Models

Referenced by `author/slug[:variant]`. No download or cache: inference is remote. Cross-references to Hugging Face via `hugging_face_id` (177 of 464 entries non-null on 2026-10-06). Quantization is an endpoint-level attribute (`quantization`, request filter `provider.quantizations`), so the same model id can be served at fp8 by one provider and bf16 by another.

## 9. Local, remote or both

Remote only. (Clips Kitty already uses OpenRouter optionally on the user's own account; this research does not change that.)

## 10. Known incidents and stated limitations

- Documentation/behaviour drift observed: the list-models reference says bearer auth is required; the anonymous call succeeded (2026-10-06).
- Catalog is explicitly non-exhaustive (routing variants are not entries); a model picker must derive them.
- Catalog suffix on a model without that entry fails hard (404 / empty endpoints) by design.
- `/api/v1/messages` (Anthropic-style) `fallbacks` "accepts at most 3 entries" (https://openrouter.ai/docs/guides/routing/model-fallbacks.md, read 2026-10-06).
- Private Models "are available for Enterprise Plan customers" only.
- Free variants "may have different rate limits or availability compared to paid versions" (free.md, read 2026-10-06).
- Security incidents: not searched within the Tier 2 budget; "not confirmed".

## 11. Borrow (for Clips Kitty)

1. **Two-level catalog = capability + implementations.** Model Clips Kitty's registry like OpenRouter's `models` → `endpoints`: a capability entry (`highlight_detection`) with typed `input_modalities`/`output_modalities` and a `supported_parameters` superset, and under it an implementation list (`provider_name` → `publisher/pipeline`, version, `quantization`-like execution notes, `status`, cost/latency hints). This is exactly "several implementations of one capability coexist" (H8).
2. **Identifier grammar with a written resolution rule.** `publisher/pipeline` plus colon-suffixed variants, split into *catalog* variants (own metadata entry) and *routing* variants (change selection only). Publish the resolution algorithm (split on `:`, keep catalog variant, drop routing variants) and the failure rule (unknown catalog variant → not found, never silent fallback to base).
3. **Request-time preferences as a small typed object.** Reuse `order`, `only`, `ignore`, `allow_fallbacks` (default true), `require_parameters` (filter implementations that do not support a parameter the caller sent), and a `data_collection`/locality flag (`local_only`, `remote_allowed`) for the hybrid execution decision.
4. **Ordered fallback list + report what actually ran.** OpenRouter's `models` array tries the next entry on error and "Requests are priced using the model that was ultimately used, which will be returned in the `model` attribute of the response body." Clips Kitty job results should carry the implementation id and version that produced them.
5. **Measured health beats declared quality.** Expose per-implementation success rate / last-run status the way OpenRouter exposes `uptime_last_*` and `status`; keep "declared" and "measured" visibly separate.
6. **Honest lifecycle fields.** `expiration_date`, a deprecation date that triggers warnings and eventual hiding, and a `Status` column (Active/Deprecated) for suffixes instead of silent removal.
7. **`hugging_face_id` cross-reference** on model records so the shared model manager can tie a capability's model to its HF source.
8. **Stable id + dated permaslug + `~latest` alias with `alias_target`**: lets a pipeline pin a version while users can pick "latest" and still see what it resolved to.

## 12. Avoid

1. A central hosted catalog service with gated onboarding (form, baseline tests, payment arrangement): Clips Kitty's fixed decisions are $0, no payments, static Git registry (H3).
2. Price-weighted load balancing ("weighted by inverse square of the price"): irrelevant without payments; use user-declared order and measured health only.
3. "Bring your own implementation" restricted to an Enterprise tier; in Clips Kitty every publisher must be able to add an implementation of a capability.
4. Letting the reference docs and runtime disagree (auth required vs anonymous OK).
5. Presenting self-declared provider policies without labelling them as declared (OpenRouter does label them "as reflected in each provider's terms"; keep that labelling).
6. A catalog that is not exhaustive for valid identifiers unless the derivation rule is published alongside it.

## 13. Sources (all read 2026-10-06)

- https://openrouter.ai/docs/llms.txt — index (curl, 98,518 bytes, loaded)
- https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties — loaded (WebFetch)
- https://openrouter.ai/docs/guides/routing/provider-selection — loaded (WebFetch)
- https://openrouter.ai/docs/guides/routing — WebFetch returned the Model Fallbacks content (the index lists no standalone "routing" page; treated as redirected)
- https://openrouter.ai/docs/guides/routing/model-fallbacks.md — loaded (curl)
- https://openrouter.ai/docs/guides/routing/model-variants/overview.md — loaded (curl)
- https://openrouter.ai/docs/guides/routing/model-variants/free.md — loaded (curl)
- https://openrouter.ai/docs/guides/routing/private-models.md — loaded (curl)
- https://openrouter.ai/docs/guides/community/for-providers.md — loaded (curl)
- https://openrouter.ai/docs/guides/overview/models.md — loaded (WebFetch)
- https://openrouter.ai/docs/guides/privacy/provider-logging.md — loaded (WebFetch)
- https://openrouter.ai/api/v1/models — anonymous curl, HTTP 200, 772,677 bytes, 464 entries (no sign-in, no paid call)
- https://openrouter.ai/api/v1/models/google/gemma-3-27b-it/endpoints — anonymous curl, HTTP 200

## Matrix row

`| OpenRouter | Remote model endpoints behind a central catalog (providers add implementations; users only call) | Catalog of `author/slug[:variant]` entries with typed modalities, params, pricing; `hugging_face_id` cross-ref; no downloads | None (single request routing; `models` fallback array) | Central hosted service, provider form + automated baseline tests + uptime monitoring | No | No | Yes | Stable id + dated permaslug + `~latest` alias; `expiration_date`; deprecation auto-warn/hide; deprecated suffixes kept with Status | Provider gating, measured uptime tiers (95/80/<80), declared (unverified) data policies filterable by `data_collection`/`zdr` | Paid, per-token; not applicable | Two-level catalog (capability → implementations), variant grammar with a published resolution rule, request-time preferences (order/only/ignore/allow_fallbacks/require_parameters), fallback list that reports which implementation ran, measured-vs-declared separation |`

## Hypotheses

- **H8 (namespaced ids + declared typed capabilities let several implementations coexist): confirmed.** Every id is `author/slug`; `architecture.input_modalities`/`output_modalities`/`supported_parameters` are the typed contract; the endpoints listing for one id returned multiple providers with different `quantization`, `pricing`, `supported_parameters`; `require_parameters`, `order`, `only`, `ignore` select among them. Private Models show a third party adding its own implementation to an existing id "with the same request and response shape".
- **H1 (out-of-process over an API is lowest risk): supported for the remote case.** Implementations never run inside the client; the contract is an HTTP shape, validated by baseline tests before an endpoint is unhidden.
- **H5 (declared ≠ enforced): supported.** Data-retention policies are shown "as reflected in each provider's terms" (declared); by contrast `require_parameters` and uptime-based demotion are router-enforced. The distinction is visible in the docs.
- **H10 (usage/health counts need telemetry): supported.** Uptime, latency and throughput fields come from real routed traffic ("100+ requests required before uptime calculation begins"); nothing equivalent exists without a central relay.
- **H3 (static Git registry costs nothing): not addressed** (OpenRouter is the opposite design: central paid backend).
