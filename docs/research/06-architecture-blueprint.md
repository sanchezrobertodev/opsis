# Architecture blueprint (design pass, 2026-10-06)

This is the detailed design behind `docs/PLAN.md`. Where the two differ, **PLAN.md wins**. Storage was decided as
**local disk + per-provider uploads** (not R2): sections that mention R2 describe the later `R2Storage` option.

## Principles
1. **Two building blocks.** A *Generation* is one model call on one route, as a durable job. A *Recipe run* is a
   DAG of steps that produce generations, LLM outputs and local ffmpeg work. Every studio is a UI over these two.
2. **The registry is data** (YAML validated by Pydantic). Adapters are code only where a provider's protocol differs.
3. **Mock first.** `PROVIDER_MODE=mock` is the default, so UI work costs $0. Live routes are enabled one at a time.
4. **Ingest immediately.** Provider URLs are temporary.
5. **Postgres is the only stateful service.** It holds data, the queue and the events. Media lives on local disk.
   There is no Redis.
6. **Single user, built to grow.** Every row has `user_id`, and every query goes through `current_user`.

## Tooling
- **Web:** Node 22 LTS, pnpm 10, Next.js (stable) with Turbopack, strict TypeScript, Biome, Vitest, Playwright.
- **Server runtime:** Python 3.12 + uv, FastAPI, Pydantic v2, SQLAlchemy 2 async (**asyncpg**), Alembic,
  **procrastinate** (psycopg 3), httpx, sse-starlette.
- **Provider SDKs:** anthropic, fal-client, google-genai, runwayml, elevenlabs, replicate.
- **Python tooling:** ruff, basedpyright, pytest, vcrpy/respx, syrupy, hypothesis.
- **System:** ffmpeg full build (winget `Gyan.FFmpeg`, includes NVENC); Docker Desktop on WSL2 (Postgres only).

```
docker compose -f infra/docker-compose.yml up -d
uv sync && pnpm i && pnpm db:migrate
pnpm dev   # concurrently: uvicorn studio.api.main:app --reload :8000 | python -m studio.worker | next dev :3000
pnpm gen   # export OpenAPI (no server) -> openapi-typescript; Pydantic -> registry/schema/*.json
```

The app processes run natively on Windows (fast reloads, ffmpeg/NVENC, local ComfyUI access). Only Postgres runs
in Docker.

**Why one Python package (`apps/server`, package `studio`).** The API and worker share about 90% of their code:
the registry, adapters, state machine, storage and director. `apps/gpu-worker` is a separate uv project, outside
the workspace, because torch and CUDA extensions would otherwise bloat the shared environment.

## Data model (Postgres 18)
**Conventions:** UUIDv7 primary keys, `timestamptz`, money as `numeric(12,6)` USD, enums as text with CHECK
constraints (`Enum(native_enum=False)`), soft deletes (`deleted_at`).

### Users and budget
- `users`: id, email?, display_name, role='owner', settings jsonb. One row is seeded; `deps.current_user()`
  returns it via `OWNER_USER_ID`. Adding auth later means replacing this dependency, with no schema change.
- `budget_policies`: user_id PK, live_enabled, daily_cap_usd, monthly_cap_usd, confirm_over_usd,
  default_run_cap_usd. The row is locked `FOR UPDATE` while a reservation is made.

### Assets
- `assets`:
  - Columns: id, user_id, kind, status, source, mime, storage_key, size_bytes, content_hash, width, height,
    duration_ms, fps, has_audio, codec, generation_id, output_index, project_id, title, tags text[], favorite,
    deleted_at.
  - `kind`: video, image, audio, mask, mask_video, depth, pose, model3d, splat, panorama, lora, subtitle, document.
  - `status`: pending_upload, processing, ready, failed.
  - `source`: upload, generation, derived, pipeline, import, render.
  - JSONB: `derivatives` (thumb, poster, filmstrip, proxy, waveform), `metadata`, and `provider_refs` (e.g.
    `{google:{file_uri,expires_at}, kling:{video_id}}`, because extend needs the provider's own handle).
  - Unique `(generation_id, output_index)`. Indexes on `(user_id, kind, created_at desc)`, GIN(tags), content_hash.
- `asset_lineage`: child_asset_id, parent_asset_id, relation (a media role, or a derivation such as extend_of,
  edit_of, upscale_of, interpolate_of, frame_of, render_of, composite_of), generation_id, pipeline_run_id.
  Ancestry is a recursive CTE.
- `asset_provider_uploads` (local-storage decision): asset_id, provider, remote_ref/url, expires_at. Caches staged
  inputs so they aren't re-uploaded.

### Elements
- `elements`: id, user_id, kind (character, environment, prop, style, voice), name, slug (unique per user; the
  @mention handle), description (≤300 chars), prompt_fragment, negative_fragment, attributes jsonb, voice jsonb,
  lora jsonb {asset_id, trigger, base_model, strength}, cover_asset_id, version.
- `element_assets`: element_id, asset_id, view (front, three_quarter, side, back, face_closeup, full_body, sheet,
  wide, detail, style), is_primary, sort.

### Generations
- `generations`:
  - Identity and grouping: id, user_id, group_id (variations, compare), parent_generation_id (re-rolls,
    conversational edit threads), kind (generate, operation, training, tool).
  - What runs: model_id, mode, output_type.
  - State: status, status_reason (e.g. provider_concurrency, budget_blocked), progress, progress_is_estimated.
  - JSONB: request, compiled (final prompt, refs, native params, warnings), routing.
  - Cost and dedupe: estimated_cost_usd, actual_cost_usd, idempotency_key (unique per user).
  - Links: project_id, shot_key, pipeline_step_id, preset_id, registry_hash.
  - Errors and timing: error_code, error_message, created/queued/started/finished_at.
  - Indexes: partial index on non-terminal status; `(project_id, shot_key)`.
- `generation_attempts`: id, generation_id, attempt_no, route_id, provider, provider_job_id, status
  (submitting, submitted, running, succeeded, failed, canceled, unknown), payload jsonb (scrubbed), last_response,
  error_class, error_message, estimated/actual cost, cost_basis, poll_count, next_poll_at, timestamps, latency_ms.
  Unique `(route_id, provider_job_id)`; a partial index counts in-flight attempts per provider for concurrency slots.
- `job_events`: id bigserial, user_id, topic, subject_id, type, payload jsonb (small). Replays SSE via
  Last-Event-ID; pruned after 7 days.

### Pipelines and projects
- `pipeline_runs`: id, user_id, recipe_id, recipe_version, project_id, status (pending, running, waiting_input,
  paused, succeeded, failed, canceled), inputs, outputs, budget_cap_usd, spent_usd, auto_approve, error.
- `pipeline_steps`: id, run_id, key (unique per run, e.g. `clip[b07]`), template_key, kind (llm, generate, local,
  gpu, checkpoint, map), status (pending, ready, running, waiting, succeeded, failed, skipped, canceled, stale),
  depends_on text[], inputs, input_hash, outputs, checkpoint jsonb {kind, schema, options, decision}, attempts,
  error.
- `projects`: id, user_id, type (cinema, faceless, world, scene3d, edit, freeform), title, status,
  schema_version, document jsonb, version (optimistic concurrency via If-Match), settings, cover_asset_id.
- `project_revisions`: keeps the last ~50 versions. `project_assets`: rebuilt on save from references in the document.
- **Pipelines never write project documents.** They produce *takes* (generations tagged with project_id +
  shot_key). The user, or an explicit auto-select step, picks one by patching the document.

### Presets, spend and health
- `presets`: user_id NULL means built-in (mirrored from YAML at boot). Columns: slug, kind (effect, template,
  camera_move, look, recipe_preset), title, category, model_id or requires (capability query), mode,
  prompt_template, params, director, required_inputs, preview_url, tags, version.
- `spend_ledger` (append-only): entry_type (reservation, release, charge, refund, adjustment), amount_usd signed,
  provider, route_id, generation_attempt_id, pipeline_run_id, llm_call_id, basis, estimated. Unique
  `(generation_attempt_id, entry_type)`. Views: `v_spend_daily`, `v_open_reservations`.
- `llm_calls`: purpose, model, request_hash (dev cache), token counts including cache read/write, cost_usd,
  stop_reason, latency, response.
- `provider_route_health`: route_id PK, breaker_state, consecutive_failures, open_until, success/failure_24h,
  p50/p95 latency, last error.
- Later (hooks only): provider_credentials (bring-your-own-key), credit_accounts/credit_ledger, webhook_inbox,
  auth_sessions.

**Columns vs JSONB:** use a column for anything you filter, sort, join, aggregate, make unique, or drive a state
machine with. Use JSONB for shapes that vary per model, provider or recipe, or that are edited as a whole
document. Every JSONB blob has a Pydantic model and a `schema_version`.

## Model registry
**Entry kinds:**
- `model`: a real model.
- `virtual`: a studio layer, e.g. Cinema Studio wraps Seedance 2.5 / Kling Omni / Veo and adds era, body, lens,
  aperture, pacing, genre, lighting and palette params. This is Higgsfield's own pattern.
- `recipe`: shows in the model picker but starts a pipeline.

```python
MediaRole = Literal["start_image","end_image","image_references","video_references","audio_references",
                    "mask","input_video","input_audio","control_video","style_image","replacement_image"]
OpTag = Literal["t2v","i2v","flf2v","multi_ref","multi_shot","extend","instruction_edit","restyle","reframe",
                "upscale","interpolate","bg_remove","motion_transfer","object_replace","lipsync","avatar","t2i","i2i"]
class MediaSlot(BaseModel): role; kinds; required=False; min=0; max=1; max_duration_s=None; max_mb=None
class ParamBase(BaseModel): name; label=None; group="basic"|"advanced"; modes=None; visible_if=None; ui=None
# discriminated union on "type": enum | int | float | bool | seed | text | json
class Mode(BaseModel): id; label; medias: list[MediaSlot]; ops: list[OpTag]
class Pricing(BaseModel):
    basis: Literal["per_output_second","per_image","per_request","per_video_token","per_1k_chars","per_gpu_second","formula"]
    rates: list[Rate]            # [{when:{resolution:"720p"}, usd:0.23}]
    multipliers: list[Multiplier]
    quantity: str | None         # "duration * num_outputs"
    formula: str | None          # simpleeval arithmetic only
    minimum_usd: float = 0; verified: date; source: HttpUrl | None
class Route(BaseModel):
    id; provider; enabled=True; modes: dict[str, RouteMode]   # mode -> {endpoint, fixed}
    map: ParamMap; transform: str | None; constraints: dict; unsupported_params: list[str]
    pricing: Pricing; timeout_s=1800; output: OutputSpec
class ModelSpec(BaseModel):
    id; name; kind="model"|"virtual"|"recipe"; family; vendor; output_type
    status: Literal["active","preview","deprecated","shutdown"]; tags; license_tier
    prompt: PromptSpec; aspect_ratios; resolutions; duration
    parameters: list[Param]; modes: list[Mode]
    elements: ElementStrategy | None   # refs_native | keyframe_first | lora | text_only
    camera: CameraSupport | None       # native preset map vs prompt-only
    wraps: list[str]; recipe_id: str | None; routes: list[Route]; examples: list[Example]
```

Example YAML (endpoints and field names are illustrative; verify per provider):
```yaml
id: seedance-2.5
name: Seedance 2.5
family: seedance
vendor: bytedance
output_type: video
status: active
prompt: {max_chars: 3000, dialect: seedance, supports_negative: false}
aspect_ratios: ["16:9","9:16","1:1","4:3","3:4","21:9"]
duration: {min: 4, max: 30, default: 5}
parameters:
  - {name: resolution, type: enum, options: ["480p","720p","1080p"], default: "720p"}
  - {name: duration, type: int, min: 4, max: 30, default: 5, ui: slider}
  - {name: generate_audio, type: bool, default: true}
  - {name: seed, type: seed, group: advanced}
modes:
  - {id: t2v, label: Text to Video, ops: [t2v]}
  - id: i2v
    label: Image to Video
    ops: [i2v, flf2v]
    medias:
      - {role: start_image, kinds: [image], required: true}
      - {role: end_image, kinds: [image]}
  - id: omni_reference
    label: Omni Reference
    ops: [multi_ref]
    medias:
      - {role: image_references, kinds: [image], max: 9}
      - {role: video_references, kinds: [video], max: 3, max_duration_s: 15}
      - {role: audio_references, kinds: [audio], max: 3}
  - {id: video_edit, ops: [instruction_edit, restyle], medias: [{role: input_video, kinds: [video], required: true}]}
  - {id: video_extension, ops: [extend], medias: [{role: input_video, kinds: [video], required: true}]}
elements: {strategy: refs_native, ref_role: image_references, token: "[Image {n}]"}
routes:
  - id: seedance-2.5@replicate
    provider: replicate
    modes: {t2v: {endpoint: bytedance/seedance-2.5}, i2v: {endpoint: bytedance/seedance-2.5}}
    map:
      params: {prompt: prompt, duration: duration, resolution: resolution}
      media: {start_image: image, end_image: last_frame_image}
    pricing: {basis: per_output_second, rates: [{when: {resolution: "720p"}, usd: 0.23}], verified: 2026-10-01}
    output: {path: output, kind: video}
  - id: seedance-2.5@fal
    provider: fal
    modes: {t2v: {endpoint: bytedance/seedance-2.5/text-to-video}, i2v: {endpoint: bytedance/seedance-2.5/image-to-video}}
    map:
      params: {prompt: prompt, duration: {to: duration, transform: str}, resolution: resolution}
      media: {start_image: image_url, image_references: reference_image_urls}
    pricing: {basis: per_video_token, quantity: "width*height*fps*duration/1024", rates: [{usd: 0.0214, per: 1000}], verified: 2026-10-04}
    output: {path: video.url, kind: video}
examples:
  - {name: i2v_smoke, mode: i2v, smoke: true, prompt: "a red fox trots through fresh snow",
     params: {duration: 5, resolution: "480p"}, media: {start_image: "fixture:images/fox.png"},
     expect: {cost_usd_range: [0.2, 2.5]}}
```

**What a route is.** A route is the same weights through a different vendor. Quality tiers are separate models or
params, so failover never changes quality.

**Routing:**
1. A route is eligible when it is enabled, its key is present, its breaker is closed, it supports the mode and
   every set param (droppable params warn), and it meets its constraints. In mock mode, only `@mock` routes are
   eligible.
2. Sort by `cheapest_healthy` (default: estimated cost, then 24 h success rate, then p50 latency), `fastest`, or
   `pinned:<route>`.
3. The sorted list is also the failover order.

**Pricing:**
- Structured bases cover ~90% of models, with a simpleeval formula as the escape hatch.
- Every price carries `verified` and `source`.
- Actual cost is reconciled from provider usage (RunPod executionTime, Replicate predict_time). If it exceeds 1.5×
  the estimate, a `system.pricing_drift` event fires.

**Mapping:**
- A declarative `map` covers "endpoint + JSON" providers.
- Transforms: str, int, enum_map, seconds_to_frames(fps), aspect_to_wh. Media roles map to one URL or a list.
- Python `transform` hooks handle odd shapes (multi-shot lists, Kling elements).
- SDK-based adapters (Google, Runway, ElevenLabs) still read the map.

**Serving the registry:**
- The loader validates every file, checks provider references, and computes `registry_hash`, which is stored on
  each generation.
- `watchfiles` hot-reloads in dev. A bad file keeps the last good version loaded and emits a
  `system.registry_error` toast.
- Endpoints: `GET /registry` (ETag), `GET /registry/models/{id}` (public spec; routes summarized as id, provider,
  enabled, disabled_reason, price, health; mapping internals withheld), `POST /registry/estimate`.

**Forms:**
- `specToZod(spec, mode)` + `<ParamForm>` with a field registry keyed by type and ui.
- `visible_if` and mode filtering control which fields show; media slots come from `mode.medias`.
- The server re-validates with a cached `pydantic.create_model` built from the same spec.

**Codegen:**
- `uv run python -m studio.api.export_openapi > openapi.json`, then `openapi-typescript` → `packages/api-client`.
- The web uses `openapi-fetch` + `openapi-react-query`.
- CI runs `pnpm gen && git diff --exit-code`.

**Bootstrap:**
- `scripts/import_catalog.py` drafts YAML from fal OpenAPI and the Higgsfield catalog export.
- `scripts/fetch_provider_schemas.py` snapshots provider input schemas for contract tests and a weekly drift diff.

## Provider adapters
```python
class ProviderAdapter(Protocol):
    provider_id: str
    execution: Literal["async_job", "sync"]
    async def prepare_inputs(self, route, media) -> dict[str, str | list[str]]: ...   # InputStager
    def build_payload(self, route, mode, req, inputs) -> ProviderPayload: ...
    async def submit(self, route, payload, *, client_ref) -> SubmitResult: ...
    async def poll(self, route, job_id) -> PollResult: ...        # status, progress, queue_pos, outputs, error, usage
    async def cancel(self, route, job_id) -> bool: ...
    async def open_output(self, out) -> AsyncIterator[bytes]: ...
    def estimate_cost(self, route, req) -> CostEstimate: ...
    def actual_cost(self, route, req, res) -> CostEstimate | None: ...
    def classify(self, err) -> ErrorClass: ...
```

| Adapter | Transport | Quirks it handles |
|---|---|---|
| fal | fal-client submit/status/result/cancel + storage upload | starts at 2 concurrent; progress from queue position and logs; **fal storage = generic public URL host** |
| google_genai (Veo 3.1, Omni 1.1, Nano Banana) | long-running operation + files.download | 2-day expiry → high-priority ingest; `provider_refs.google` for extend; Omni edit threads via parent_generation_id |
| runway (Gen-4.5, Aleph 2.0, Act-Two) | runwayml tasks | expiring URLs |
| elevenlabs | TTS with timestamps, SFX, music, Scribe | alignment stored as a `subtitle` asset → free captions |
| replicate | predictions | outputs deleted after 1 h; predict_time → actual cost |
| runpod (custom endpoints) | httpx /run, /status, /cancel | workflow template + `.map.yaml` injection; patched handler (inputs by URL, VHS `gifs` outputs); cost = executionTime × GPU rate |
| runpod (public endpoints) | same class, `public: true` routes | fixed schemas, per-run pricing |
| comfy_local | /prompt, ws, /history, /view, /upload/image | same templates as RunPod; vram_gb gate 12; concurrency 1; disabled until `COMFYUI_URL` is set (your own ComfyUI) |
| local_tools | in-process ffmpeg (trim, concat, frame extract, crop-reframe, speed) | same job tray and lineage as everything else |
| higgsfield, kling, byteplus, openrouter | declarative | alternate routes; Higgsfield-only features |
| mock | ffmpeg + Pillow | see below |

## Generation state machine
```
draft ─► queued ◄──── (transient retry / pre-acceptance failover)
awaiting_confirmation ─► queued | canceled          blocked_budget ─► queued | canceled
queued ─► submitting ─► in_progress ─► ingesting ─► succeeded
              │              │              └─► failed(ingest_error)  ← retry ingest only
              │              └─► failed | queued (post-acceptance failover, if allowed)
              ├─► unknown (crash/timeout mid-submit with no job id — human resolves)
              └─► failed
any non-terminal ─► canceled
```
One `transition()` checks the `ALLOWED` table and writes the status, a `job_events` row and
`pg_notify('studio_events', …)` in one transaction.

## Queue: procrastinate
| Option | Verdict |
|---|---|
| Celery | Windows workers are effectively unsupported, heavy, sync-first. No. |
| Dramatiq | needs Redis or RabbitMQ; sync-first. |
| arq | needs Redis; low maintenance activity. |
| **procrastinate** | uses our Postgres; async; deferred jobs; periodic tasks; retries; queueing locks; cancellation; in-memory connector for tests. **Pick.** |

**Windows caveats:**
- psycopg async needs `asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)` in the worker.
- Asyncio subprocesses need Proactor, so ffmpeg runs via `anyio.to_thread.run_sync(subprocess.run, …)`.
- The API uses asyncpg (works on either loop).
- The queue sits behind a thin `enqueue()` interface so it can be replaced.

**Consistent enqueue:**
1. The API commits the row as `queued`.
2. It then defers `gen.submit`.
3. If the process dies in between, the reaper re-defers orphaned `queued` rows within 60 s.

**Tasks:**
- `gen.submit` (queue `submit`):
  1. Exit if the generation was canceled.
  2. Choose the route.
  3. Check provider slots by counting in-flight attempts. If full, re-defer in 10–20 s with
     `status_reason=provider_concurrency`.
  4. Reserve budget.
  5. Write the attempt as `submitting`.
  6. Stage inputs.
  7. Submit.
  8. Save `provider_job_id` and defer the first poll.
- `gen.poll`:
  - One status call per execution, then re-defer with backoff (3 s × 1.5, cap 20 s for video or 5 s for images,
    with jitter).
  - Emit an event only when status changes or progress moves ≥1%.
  - On timeout: cancel at the provider, then fail or fail over.
- `gen.ingest` (idempotent, ≤5 retries):
  1. Copy outputs to disk.
  2. Create assets and lineage from every input role.
  3. Charge the ledger and release the reservation.
  4. Mark `succeeded`.
  5. Defer `asset.derive`, and `pipeline.advance` if the job belongs to a pipeline.
- `asset.derive` (queue `cpu`): ffprobe, poster, webp thumbnail, filmstrip sprite (hover-scrub), proxy if the codec
  isn't browser-safe, waveform peaks.
- `pipeline.advance`: `queueing_lock="adv:{run}"` and `lock="run:{run}"`.
- `maint.reap` (every minute): re-defer orphaned `queued` rows and stale polls; mark `submitting` attempts older than
  2 min with no job id as `unknown`; advance running pipelines.
- `maint.prune`: delete old events.
- Dev runs one worker at concurrency 8. Later, split out `cpu` (concurrency 2) and `local_gpu` (concurrency 1).

## SSE
- `GET /events/stream` (sse-starlette).
- The API holds one asyncpg `LISTEN studio_events` connection; a hub fans events out to per-client queues filtered
  by user.
- Payloads are small (ids, status, progress, cost, route) to stay under the 8 KB NOTIFY limit; clients refetch
  details via TanStack Query.
- `id:` is the `job_events.id`, so Last-Event-ID replays missed events. Heartbeat every 15 s.
- The browser connects directly to FastAPI (CORS), because Next.js rewrites can buffer SSE.

## Ingestion and storage (local-first)
- **Layout:** `data/media/u/{user}/a/{asset}/original.ext` plus thumb.webp, poster.jpg, filmstrip.webp, proxy.mp4;
  uploads under `in/`; intermediates under `tmp/` (cleaned periodically).
- **Downloads:** streamed to a temp file while computing sha256, then moved into place. Assets are keyed on
  `(generation_id, output_index)`, so ingesting twice is a no-op.
- **InputStager:** prepares inputs per provider (fal upload, Google Files or inline bytes, Runway/Replicate upload
  or data URI, RunPod base64 ≤10 MB or a fal-staged URL). Results are cached in `asset_provider_uploads`.
- **Browser:** uploads go to a streaming FastAPI endpoint; playback is via `/media/...` with Range support (seeking
  works).

## Idempotency
1. An `Idempotency-Key` header, unique per `(user_id, key)`. Pipeline-created generations use
   `run:{id}:{step}:{input_hash}`.
2. The submit task checks status first.
3. Ingestion has the unique asset key and deterministic file paths.
4. The ledger has unique `(attempt_id, entry_type)`.
5. **An ambiguous submit on a paid route is never auto-resubmitted.** It goes to `unknown`, and the UI offers
   "mark failed" or "attach provider job id".

## Retries and failover
| Class | Examples | Action |
|---|---|---|
| transient | 429, 5xx, network | retry the same route with backoff (≤3 submits) |
| route_unavailable | 401/403, quota, 404 endpoint, outage, breaker open | update health and fail over. Always allowed before acceptance. After acceptance, needs `allow_failover`, budget room, and a check of `charges_on_failure` |
| invalid_request | 400/422 | fail; flag the registry entry |
| moderation | content policy | fail; fail over only if `allow_failover_on_moderation` |
| timeout | > timeout_s | cancel at the provider, then fail over if allowed |
| ingest_error | download or write failed | retry the ingest only; alert if provider expiry is near |

**Circuit breaker:** opens after 3 consecutive failures for 10 min, then half-opens with one probe.

## Mock provider
- Every model automatically gets an `@mock` route at $0.
- Video is generated with ffmpeg (`lavfi testsrc2`, `drawtext` showing model, mode, seed and prompt) at the exact
  duration, aspect and fps, plus a sine tone if `generate_audio` is on. Images use Pillow.
- It simulates a queue: ~2 s queued, then progress over 3–8 s.
- Failures are injected with prompt tags: `[[mock:fail=moderation]]`, `[[mock:slow]]`, `[[mock:ambiguous]]`,
  `[[mock:fail=route_unavailable]]`.
- `LLM_MODE=mock` returns fixtures validated against task schemas (polyfactory fallback).
- `PROVIDER_MODE=mixed` + `LIVE_ROUTE_ALLOWLIST` runs selected routes live and everything else mocked.

## Spend guard
1. **At the API:** estimate the cost. Above `confirm_over_usd` without a matching `confirm_cost_usd`, return 409
   `confirmation_required`.
2. **In the worker, in one transaction:**
   - Lock `budget_policies` with `FOR UPDATE`.
   - Add today's and this month's charges to open reservations and the new estimate.
   - If a cap is exceeded, set `blocked_budget`. Otherwise insert a reservation.
3. **Settlement:** charge the actual cost (or the estimate) on ingest. On failure or cancel, release, and charge if
   the route bills failures.
4. **Pipeline caps:** approved at run start; when exceeded, the run pauses at a `budget` checkpoint.
5. **Kill switch:** `live_enabled=false`.
6. Claude calls are recorded in the ledger.

## Pipeline engine (~500 lines; no Temporal)
Recipes are Python builders that produce a static template graph; `map` nodes expand at runtime.
```python
@recipe(id="faceless", version=1, inputs=FacelessInputs)
def faceless(r, inp):
    brief  = r.llm("brief", task=FacelessBrief)                       # Opus
    r.checkpoint("approve_brief", kind="edit", of=brief)
    styles = r.generate("style_frames", model=inp.image_model, n=4, request=style_req)
    r.checkpoint("pick_style", kind="pick_one", of=styles)
    script = r.llm("script", task=ScriptAndBlocks, after=["approve_brief","pick_style"])
    r.checkpoint("approve_script", kind="edit", of=script)
    vo     = r.generate("voiceover", model="elevenlabs-tts", request=tts_req)   # with timestamps
    keys   = r.map("keyframe", over=lambda c: c["approve_script"].blocks, key=lambda b: b.id,
                   step=lambda b: GenerateStep(model=inp.image_model, request=keyframe_req(b)))
    r.checkpoint("review_keyframes", kind="pick_many", of=keys, optional=True)
    clips  = r.map("clip", over="keyframe", step=lambda k: GenerateStep(model=inp.video_model, request=i2v_req(k)))
    music  = r.generate("music", model="elevenlabs-music", request=music_req)
    cut    = r.local("assemble", fn=assemble_faceless, after=[clips, vo, music])
    r.local("captions", fn=burn_captions_from_alignment, after=[cut, vo])
    r.generate("thumbnail", model=inp.image_model, request=thumb_req)
```

**How `advance(run_id)` works** (safe to call repeatedly):
1. Lock the run.
2. Expand `map` templates into child steps with stable keys (from block ids, not positions).
3. For each pending step whose dependencies succeeded:
   - Resolve inputs and compute `input_hash`.
   - If a succeeded step with the same key and hash exists, reuse its outputs.
   - Otherwise dispatch: `llm`/`local` defer a task; `generate` creates generations via the same service as the
     API (run/step idempotency key); `checkpoint` sets `waiting` and emits an event.
4. Failure policy: `fail_fast` (default) or `continue`.

**Triggers:** a generation reaching a terminal state, a checkpoint decision
(`POST /pipeline-runs/{id}/steps/{key}/decision`), a retry (`POST …/retry`, which resets downstream steps), or the
reaper.

**Checkpoints:** approve, edit (JSON-Schema form or script editor), pick_one, pick_many, mask (SAM editor),
budget.

**Partial re-runs:** editing an approved output marks downstream steps `stale`. Unchanged map children keep their
outputs, so editing one block regenerates one clip.

**Recipes:**

| Recipe | Steps |
|---|---|
| faceless.v1 | as in the example above |
| cinema.plan | script → scenes and shots (Opus, structured output) → proposed document patch → approve |
| cinema.takes | N takes per selected shot; continuity via the previous take's last frame; or one multi-shot generation per scene |
| cinema.export | timeline JSON → ffmpeg render |
| object_swap.v1 | frame → SAM clicks (mask checkpoint) → track mask → review → replacement edit (Kling Omni Edit / Seedance edit / Aleph / VACE+mask) → optional maskedmerge |
| motion_transfer.v1 | driving video + character/Element → trim/crop → Kling MC 3.0 / Wan Animate / Seedance omni-ref |
| jutsu3d.v1 | browser render passes → angles (img2img N variants) → pick → animate (i2v / first-last frame / depth → VACE) |
| world.v1 | prompt or image → panorama → Marble splat → camera-path renders → frames and refs → video |
| character_sheet.v1 | sheet → split views → Element |
| lora_train.v1 | Element images → captions (Sonnet vision) → trainer model (`output_type: lora`) → LoRA asset attached to the Element |
| talking_shot.v1 | line + voice Element → TTS → lipsync |

## Director
**Canonical input:** `ShotSpec` = subjects (Element refs), action, setting, camera (shot size, angle, move with
speed and direction, lens mm, aperture, body, height), look (era, palette, lighting, genre, grade), pacing, audio
(dialogue with speaker Element, SFX, music, ambience), duration, aspect, negative.

**Preset libraries** live in `registry/director/*.yaml`:
```yaml
- id: dolly_in
  label: Dolly In
  category: push
  preview: presets/camera/dolly_in.mp4
  params: {speed: [slow, medium, fast]}
  prompt:
    default: "{speed} dolly-in toward {subject}"
    veo: "The camera {speed}ly dollies in toward {subject}"
    kling: "camera pushes in, {speed}"
  native:
    wan-2.2-fun-camera: {param: camera_pose, value: ZoomIn}
    uni3c: {trajectory: trajectories/dolly_in.json}
```

**Dialects:** one class per model family, `render(spec, refs) → CompiledPrompt(prompt, negative, native_params,
warnings)`. Each owns its section order, reference-token format and `max_chars` with priority truncation (subject
and action first, then camera, look, audio, extras).

**Compile order.** Everything except step 5 is pure, so golden-file tests work.
1. Expand the preset template (sandboxed Jinja2).
2. Resolve `@mentions`.
3. Merge director controls: native control > dialect phrase > default phrase.
4. The dialect renders the prompt.
5. Optional LLM enhancement (cached).
6. Validate against the spec → `CompiledRequest{prompt, negative, params, media[(role, asset_id)], warnings,
   pre_steps}`.

`POST /director/compile` powers the "Compiled" tab; a raw toggle bypasses the director.

**Element strategies:**
- `refs_native`: attach refs and substitute the dialect token. Allocate slots in order: characters (≤2 each,
  chosen by shot size: close-up → face_closeup, wide → full_body), then environment (1), then props. Warn on drops.
- `keyframe_first`: a pre-step generates a start frame with Nano Banana Pro or Seedream 5 from the refs (one-click
  two-step).
- `lora`: if the base model matches, add the trigger word and LoRA param.
- `text_only`: inline the description.
- In all cases the identity description is included. Voice Elements feed TTS, lipsync and Veo audio cues.

**Claude:**

| Task | Model | Effort |
|---|---|---|
| Prompt enhance | `claude-sonnet-5-5` | low |
| Scripts, shot plans, faceless briefs | `claude-opus-5-5` | high, streamed |
| Take review, LoRA captions | `claude-sonnet-5-5` with vision | — |

- Use structured outputs with Pydantic models.
- Put dialect guides and vocabularies in a byte-stable cached system prefix.
- Handle `stop_reason == "refusal"`.
- **Load the `claude-api` skill when implementing `llm/client.py`** for current SDK details: structured-output
  API, effort parameter, tool_choice limits, fallbacks, prices.

## Frontend
**Routes:**
- Layout: `app/layout.tsx` (sidebar of studios, ⌘K palette via cmdk, JobTray, RealtimeProvider).
- Studios: `/create/{video,image}`, `/edit`, `/motion`, `/motion-transfer`, `/swap`, `/lipsync`,
  `/characters[/id]`, `/worlds[/id]`, `/faceless[/runId]`, `/cinema[/projectId[/timeline]]`,
  `/jutsu3d/[projectId]`, `/presets[/slug]`.
- Utility: `/library[/assetId]`, `/runs/[runId]`, `/settings` (budgets, routing, route health, key status).

**State:**
- TanStack Query holds server state.
- `RealtimeProvider` opens one EventSource; events call `setQueryData` on details and throttled invalidation on
  lists.
- Zustand holds client state: create drafts (persisted), job tray, timeline editor (zundo undo; JSON Patch flush
  with If-Match), 3D scene (debounced autosave), mask editor.
- Forms: react-hook-form + runtime zod.

| Component | Built with |
|---|---|
| ModelPicker | cmdk + filters (output type, ops, capabilities); "from" price and health |
| ModelParamForm | spec-driven fields; live cost chip (debounced `/registry/estimate`) |
| MediaSlot(Group) | role-aware dropzones: library drag, upload with progress, paste, "use last frame of…"; enforces limits |
| PromptEditor | TipTap Mention (`@elements`, `#presets`), director chips, Enhance with diff, Compiled tab |
| DirectorPanel | camera-move grid with looping previews; lens, body, era, lighting and palette pickers |
| JobTray | SSE-driven progress, cancel, cost, "waiting for slot" |
| AssetGrid / LineageGraph | @tanstack/react-virtual + filmstrip hover-scrub; @xyflow/react + dagre |
| Player | media-chrome. Frame-exact ops (extract frame, SAM) run **server-side by frame index** |
| Storyboard | dnd-kit shot cards with takes |
| Timeline | @xzdarcy/react-timeline-editor + @remotion/player preview of the same timeline JSON; ffmpeg filtergraph export (shared subset: cuts, crossfades, fades, text and ASS captions, volume and ducking) |
| MaskEditor | react-konva over a server-extracted frame; positive/negative clicks and a box → `POST /tools/segment` |
| TrajectoryEditor | Konva splines → JSON tracks (Wan-Move/ATI) |
| SceneEditor3D | R3F + drei (TransformControls, CameraControls, GLTF); WebGLRenderTarget passes (depth, normal, ID); CatmullRom camera path; deterministic frame capture → PNG sequence → server MP4 |
| SplatViewer / Panorama | @sparkjsdev/spark (fallback @mkkellogg/gaussian-splats-3d); inverted-sphere panorama; camera-path tool shared with 3D Jutsu |
| CheckpointPanel / PipelineRunView | per-checkpoint renderers; step graph |
| UI shell | shadcn/ui, Tailwind v4, lucide-react, sonner |

## Testing
- **Contract tests**, generated from registry `examples`: the payload validates against the snapshotted provider
  schema, required media is present, constraints hold, the estimate is within range, unsupported params are flagged.
- **Registry lint:** unique ids, known providers, shutdown models have no enabled routes, every mode has a route,
  every route has an example.
- **Live smoke:** `scripts/smoke_live.py --cap 3.00 --confirm-spend [--routes …]` runs the cheapest smoke example
  per route, one at a time, through the real API. It stops at the cap, writes `smoke_reports/YYYY-MM-DD.json` and
  updates route health.
- **Drift:** weekly `fetch_provider_schemas.py --diff`.
- **Adding a model:**
  1. Run `import_catalog.py`.
  2. Edit the YAML.
  3. Run `pytest tests/contract -k <id>`.
  4. Run `smoke_live --routes <route> --cap 0.5`.
  5. Commit.

## Risks
| Risk | Mitigation |
|---|---|
| Model and API churn | registry `status`, `verified` prices, drift diffs, contract tests, multiple routes, failover, pricing-drift alarms |
| Runaway spend | locked reservations, caps, confirmations, no auto-resubmit of ambiguous submits, kill switch, mock default |
| Output expiry | immediate high-priority ingest; ingest retried separately |
| Double charges | unique keys per attempt, ledger and asset; `unknown` state |
| Windows async | Selector loop for the worker; threaded subprocess calls for ffmpeg; asyncpg in the API |
| Blackwell wheels for local deps | keep local models out of the main env; use WSL2/Docker or the existing ComfyUI portable |
| RunPod limits | patched handler, queue endpoints, raised timeouts, start with public endpoints |
| Remotion vs ffmpeg drift | shared timeline JSON; small common feature set; real-render snapshot test |
| Scope | two building blocks; thin studios; phased shipping |
| Likeness and voice consent | official APIs only; consent notes on Elements; personal use until terms are reviewed |
| Data loss | soft deletes; nightly pg_dump; content-hashed assets |
