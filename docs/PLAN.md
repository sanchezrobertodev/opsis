# AI Media Studio — a Higgsfield-class AI video studio

## Context
`C:\Dev\AI\AI-Media Studio` is empty (greenfield, no git). Goal: a personal AI video studio with Higgsfield's video
feature set — Create Video, Edit Video, Motion Control, Motion Transfer, Object Swap, Character Creation, World
Creation, Faceless Studio, Cinema Studio, 3D Jutsu (+ lipsync, effects, upscale…) — with access to the same model
families. Video features first; image/audio only where the video features need them.

Decisions made with the user:
- **Audience:** personal studio first; every row carries `user_id` and auth/credits/billing hooks exist for later.
- **Stack:** Next.js (TypeScript) web app + Python FastAPI API + Python worker.
- **Local GPU:** RTX 5070 12GB + 64GB RAM → local runs light helpers only; heavy open models → RunPod.
  The existing ComfyUI v0.34 portable in `C:\Dev\AI\VideoStudio` (Wan 2.2 TI2V-5B installed) becomes the
  opt-in local route. No new local model installs in this plan.
- **Model mix:** API-first (fal.ai aggregator + selected direct providers); open models on RunPod fill gaps.
- **Folder:** rename to `C:\Dev\AI\ai-media-studio` (no spaces) before implementation starts.
- **Storage:** local disk + per-provider uploads (no cloud bucket), behind a storage interface so R2 can be added later.

## Research findings that shape the design (Oct 2026)
Full reports get saved to `docs/research/` in step 0 (feature catalog, commercial APIs and aggregators, open
models and RunPod, Higgsfield's live model catalog).
1. **Higgsfield is mostly an orchestration layer.** Its own IP is small: the DoP camera model, the Soul image
   family, Popcorn and Speak. Everything else is a third-party model in a picker, or a "studio" (workflow + preset
   UI + prompt-direction layer). Verified from their live catalog: *Cinema Studio 4.0 = Seedance 2.5's exact modes*
   (t2v, omni_reference, video_edit, video_extension) plus era, camera body, lens, aperture, pacing, genre,
   lighting and palette presets. Faceless Studio and Explainer are Gemini Omni clips plus Seed Audio narration in
   10 s blocks. 3D Jutsu is an agent-built 3D scene (GLB/Mixamo, cameras, timeline) exported to MP4 or GLB.
   **So we rebuild each studio as: a model registry + a director/prompt compiler + pipelines + good UIs.**
2. **Their model schema is our registry template:** `id, name, provider, output_type, parameters[{name, type,
   options, default, min, max}], media roles (start_image, end_image, image_references, video_references,
   audio_references, mask, input_video, input_audio), aspect_ratios, duration, tags`.
3. **Current flagships:**
   - Google: Veo 3.1 (Std/Fast/Lite) and Gemini Omni 1.1 Flash.
   - Kling: 3.0, 3.0 Omni (+ Omni Edit), 3.0 Turbo, Motion Control 3.0, Avatar 2.0.
   - ByteDance Seedance: 2.0 and 2.5 (4–30 s, up to 50 refs, edit, extension).
   - Others: MiniMax H3, Wan 3.0/2.7 (API-only), HappyHorse 1.1, FLUX 3 Video (+ Edit), LTX-2.5, Grok Imagine
     Video 1.5, Runway Gen-4.5 + Aleph 2.0 + Act-Two, Luma Ray 3.2 (+ Modify), PixVerse V6, Vidu Q3.
   - **Sora 2's API shut down on 2026-09-24, so it is excluded.**
4. **Access:**
   - fal.ai covers about 90% of the catalog at official prices for Veo and Kling, but roughly 2× for Seedance 2.5.
   - Runway's models are not on fal.
   - The same model costs different amounts per route (Seedance 2.5 720p: $0.23/s on Replicate vs $0.47/s on
     fal), so the registry supports **multiple routes per model** with cheapest-healthy routing and failover.
   - Higgsfield now sells its own API (open.higgsfield.ai: Soul 2, DoP, Genjutsu, Cinema Studio 4.0). It is an
     optional route for those exact models.
5. **Open weights on RunPod:**
   - The Wan 2.2 family is the Apache-2.0 backbone: Animate/Animate-2, VACE, Fun Camera/Control, Wan-Move,
     Uni3C, Phantom/MAGREF/Stand-In, InfiniteTalk, S2V.
   - Also useful: LTX-2.5 (native audio), SeedVR2/FlashVSR, Qwen-Image-Edit-2511, TRELLIS.2, ACE-Step,
     Qwen3-TTS/Chatterbox/Kokoro.
   - Estimated cost is about $0.07–0.30 per 5 s 720p clip on L40S/H100 flex workers.
   - A per-model `license` tier is tracked, because some popular tools are non-commercial (MMAudio, ProPainter,
     FLUX dev, InsightFace-based tools), territory-restricted (MiniMax H3), or capped (LTX: free under $10M revenue).
6. **Provider quirks:**
   - Veo deletes outputs after 2 days and Replicate after 1 hour, so every output is ingested to disk immediately.
   - RunPod worker-comfyui accepts 10 MB payloads (pass large inputs as URLs) and needs a handler patch to return
     VHS video outputs. Use queue-based endpoints and raise the 600 s timeout.
   - fal starts at 2 concurrent jobs.

## Feature → model matrix (initial routes; all swappable in the registry)
| Feature | What we build | Primary (API) | Open / RunPod |
|---|---|---|---|
| **Create Video** | t2v, i2v, first/last frame, multi-ref, multi-shot, extend; batch and compare across models | Veo 3.1, Omni 1.1, Kling 3.0/Omni/Turbo, Seedance 2.0/2.5, H3, Wan 3.0, HappyHorse, LTX-2.5, Grok 1.5, Gen-4.5, Ray 3.2, PixVerse V6, Vidu Q3 | Wan 2.2 A14B + Lightning, LTX-2.5 |
| **Motion Control** (camera) | 65-move camera library (DoP list: crash zoom, dolly zoom, bullet time, FPV, 360 orbit…), stack up to 3 moves, Mix; draw-to-video trajectories | Moves compiled per model dialect; DoP via Higgsfield API (optional) | Wan Fun Camera, Uni3C (copy camera from a ref video), Wan-Move/ATI, LTX camera LoRAs |
| **Motion Transfer** | character image + 3–30 s driving clip; orientation and background-source controls; driving-clip library | Kling Motion Control 3.0, Seedance 2.5 omni-ref with a video ref, Runway Act-Two, Genjutsu (optional) | Wan Animate / Animate-2 (move and mix), SCAIL |
| **Object Swap** | click-to-segment (SAM) → mask track → swap using ref images; object removal | Kling 3.0 Omni Edit, Seedance 2.5 edit + refs, Runway Aleph 2.0, Omni edit; SAM 3 on fal | Wan VACE + mask + ref, Wan Animate "mix", ROSE/VOID |
| **Edit Video** | instruction edits, restyle, relight/regrade, extend, reframe, upscale, FPS boost, background removal, video-to-audio | Kling Omni Edit, Aleph 2.0, Luma Modify, FLUX 3 Video Edit, Seedance edit/extend, Omni conversational edit, Topaz/ByteDance upscale | VACE, JoyAI-Video-Edit, SeedVR2/FlashVSR, RIFE |
| **Character Creation** | Elements library (character/location/prop/voice/style), trait builder (Soul Cast / AI Influencer style), character sheets, `@mentions` that attach refs automatically, optional LoRA ("Soul ID") | Nano Banana 2/Pro, Seedream 5, GPT Image 2.5, FLUX.2/3; fal LoRA trainers; ElevenLabs voice clone | Qwen-Image-Edit-2511; ai-toolkit / musubi-tuner on a RunPod pod |
| **World Creation** | location Elements, 360 panoramas, explorable Gaussian-splat worlds, multi-angle location shots, camera path → frames and camera-ref video | Nano Banana/Seedream (panoramas, angles), World Labs Marble API (splats) | HY-World 2.0 / WorldMirror (pod), panorama LoRAs |
| **Faceless Studio** | channel (style, voice and characters persisted) → topic/script → style anchor → asset roster → script and block plan → clips or narrated stills → voiceover → music → ffmpeg assembly → captions → thumbnail | Claude (script and plan), budget video tier (Seedance Mini, Kling Turbo, Omni Flash, Veo Lite), ElevenLabs v4 + music | Wan 2.2/LTX clips; Kokoro/Chatterbox, ACE-Step |
| **Cinema Studio** | project → scenes → shots storyboard; director panel (body, lens, focal, aperture, genre, era, tempo, emotion, lighting, palette, moves); hero-frame grid → animate; multi-shot; extend/edit; AI Director chat; timeline export | Seedance 2.5 by default (as Cinema Studio 4.0 does), Kling 3.0 multi-shot, Veo 3.1; stills via Nano Banana Pro / Seedream | Wan 2.2 + VACE |
| **3D Jutsu** | in-browser 3D blocking (primitives, mannequins, GLB, lights, cameras, timeline) → render beauty/depth passes → "angles" img2img → animate, or depth video → v2v | Seedream 5 / Nano Banana (angles), Tripo/Meshy/Hunyuan3D v3 (image→3D), any i2v or first/last-frame model | Wan VACE / Fun Control (depth), TRELLIS.2 |
| **Lipsync / avatars** | image or video + script or audio → talking video | Sync lipsync-3, Kling Avatar 2.0, OmniHuman 1.5, VEED Fabric, Veo 3.1 | InfiniteTalk, LatentSync |
| **Effects / presets** | one-click templates (model + prompt template + params + required inputs) | any | any |

## Architecture
```
Browser (Next.js :3000) ──REST──► FastAPI (:8000) ──procrastinate (Postgres queue)──► Worker (Python)
        ▲  SSE (pg LISTEN/NOTIFY)        │  serves /media/* (Range)                   ├─ provider adapters
        └────────────────────────────────┘                                            ├─ pipeline engine
   Postgres (Docker) = data + queue + events      Local disk media store              ├─ media ops (ffmpeg NVENC)
   Claude API (director, scripts, prompt enhance)  (data/media/…)                      └─ input staging per provider
```
- **Two building blocks only.**
  - A *Generation* is one model call on one route, run as a durable job.
  - A *Recipe run* is a DAG of generation, LLM, ffmpeg and human-checkpoint steps.
  - Every studio is a UI over these two.
- **Web:**
  - Framework and UI: Next.js App Router, React 19, strict TypeScript, Tailwind v4, shadcn/ui.
  - State and data: TanStack Query (server state), Zustand (+ zundo undo), react-hook-form with zod schemas
    generated from the registry, and a typed client via `openapi-typescript` + `openapi-fetch`.
  - Studio components: TipTap (`@mentions`, `#presets`), media-chrome (player), @xyflow/react (lineage and
    pipeline graphs), dnd-kit (storyboard), react-konva (mask and trajectory editors), React Three Fiber + drei
    (3D Jutsu), Spark (Gaussian splats), @xzdarcy/react-timeline-editor + @remotion/player (timeline preview).
- **Server:**
  - One Python package, `studio`, with `api` and `worker` entrypoints, on Python 3.12 + uv.
  - Libraries: FastAPI, Pydantic v2, SQLAlchemy 2 (asyncpg), Alembic, **procrastinate** (Postgres-based queue, so
    no Redis; works on Windows), httpx, sse-starlette, plus fal-client, google-genai, runwayml, elevenlabs,
    replicate and anthropic.
  - Windows specifics: the worker uses a SelectorEventLoop, and ffmpeg calls go through `subprocess.run` in a thread.
- **Storage (local-first):**
  - `LocalDiskStorage` writes under `data/media/u/{user}/a/{asset}/…` (original, thumbnail, poster, filmstrip,
    proxy). FastAPI serves it with Range support.
  - An **InputStager** gives each provider its inputs in the form it accepts: fal storage upload, Google Files API
    or inline bytes, Runway/Replicate uploads or data URIs, RunPod base64 (≤10 MB) or a staged URL.
  - fal storage doubles as a generic public-URL host for providers that need a URL.
  - Staged uploads are cached in `asset_provider_uploads` (asset, provider, ref, expires_at), so the same input is
    never re-uploaded while its cached copy is still valid.
  - An `R2Storage` implementation can be added later without schema changes.

### Repo layout (pnpm workspace + uv)
```
apps/web/                         Next.js (routes: /create/{video,image}, /edit, /motion, /motion-transfer, /swap,
                                  /characters, /worlds, /faceless, /cinema, /jutsu3d, /lipsync, /presets, /library,
                                  /runs, /settings)
apps/server/src/studio/           api/ (routers, sse), worker/ (tasks), db/ (models), domain/ (fsm, assets),
                                  registry/ (schema, loader, pricing, mapping, routing), providers/ (base,
                                  declarative, fal, google_genai, runway, elevenlabs, replicate, runpod,
                                  comfy_local, local_tools, higgsfield, mock), director/ (shot_spec, compiler,
                                  dialects/), llm/, pipelines/ (engine, recipes/), media/ (ffmpeg, probe,
                                  assemble, captions), storage/ (local, stager), budget/
apps/gpu-worker/                  stub only (SAM 2, WhisperX, TTS…); not installed in this plan
packages/api-client/              generated TS types + typed hooks
registry/{models,providers,director,presets}/*.yaml   single source of truth (Pydantic-validated, JSON Schema)
workflows/comfy/*.json + *.map.yaml                   ComfyUI API workflows + node-injection maps
infra/docker-compose.yml          postgres:18   ·   infra/runpod/worker-comfyui/ (Dockerfile, handler_patch.py)
scripts/                          smoke_live.py, import_catalog.py, fetch_provider_schemas.py, make_mock_media.py
docs/research/                    research reports from this planning session
CLAUDE.md                         conventions + "how to add a model" checklist
```

## Core subsystems
1. **Model registry** (YAML, one file per model; Pydantic schema in `registry/schema.py`).
   - Entry kinds:
     - `model`: a real model.
     - `virtual`: a studio layer over base models, e.g. Cinema Studio wraps Seedance 2.5 / Kling Omni / Veo and
       adds director params.
     - `recipe`: appears in the model picker but starts a pipeline run.
   - Each entry lists modes with media slots (role, kinds, min/max, duration limits), typed params (enum, int,
     float, bool, seed, text, json, with `visible_if` and ui hints), aspect ratios, durations, status, license
     tier, element strategy and camera support.
   - Each entry lists `routes[]`, each with provider, endpoints per mode, a param/media map plus transforms, a
     pricing basis (per output second, image, request, video token, 1k chars or GPU second, with a `verified`
     date) and `examples` that drive contract and smoke tests.
   - A route is the same weights through a different vendor. Quality tiers are separate params or models, so
     failover never changes quality.
   - API endpoints: `GET /registry` (ETag = hash), `GET /registry/models/{id}` and `POST /registry/estimate`.
   - The web renders the model picker and parameter forms dynamically. Registry files hot-reload in dev.
2. **Provider adapters** share one interface: `prepare_inputs, build_payload, submit, poll, cancel, open_output,
   estimate_cost, actual_cost, classify`.
   - `DeclarativeAdapter` builds payloads from the YAML; concrete adapters only override transport.
   - Every model automatically gets a **`@mock` route** at $0. It generates ffmpeg test-pattern media at the exact
     requested duration, aspect and fps, simulates queue/progress, and injects failures via prompt tags.
   - `PROVIDER_MODE=mock|mixed|live` controls which routes are eligible; `mixed` uses `LIVE_ROUTE_ALLOWLIST`.
   - `LLM_MODE=mock` returns fixtures.
3. **Job lifecycle.** States: `draft → (awaiting_confirmation | blocked_budget) → queued → submitting →
   in_progress → ingesting → succeeded | failed | canceled | unknown`.
   - One `transition()` writes the status, a `job_events` row and `pg_notify` in a single transaction.
   - Tasks:
     - `gen.submit`: route selection, per-provider slot limits, budget reservation, staging inputs, submit.
     - `gen.poll`: one status call per run, then re-defers itself with backoff and jitter.
     - `gen.ingest`: idempotent download → disk, assets + lineage, ledger charge.
     - `asset.derive`: ffprobe, poster, thumbnail, filmstrip, proxy, waveform.
     - `maint.reap`: re-queues orphaned jobs.
   - Error classes:
     - Transient errors: retry.
     - Route unavailable: fail over, with a circuit breaker.
     - Invalid request, moderation, timeout: fail. Moderation does not fail over by default.
     - Ingest errors: retry the ingest only.
     - An ambiguous paid submit is **never auto-resubmitted** (it goes to `unknown`).
   - Idempotency keys exist at the request, attempt, asset and ledger levels.
4. **Spend guard.**
   - The estimate is shown on the Generate button.
   - Above a confirm threshold the API returns 409 and the UI shows a confirm modal.
   - The worker reserves spend under a `FOR UPDATE` lock against daily/monthly caps, then charges or releases.
   - Pipeline runs have per-run caps.
   - A global "live disabled" kill switch blocks all paid routes.
   - Claude calls go into the ledger too.
5. **Elements + director.**
   - A canonical `ShotSpec` (subjects, action, setting, camera, look, pacing, audio) is compiled by per-family
     **dialects** (veo, kling, seedance, runway, wan, nano-banana…) into the final prompt, native params and
     warnings.
   - Native controls (Wan Fun Camera, Uni3C trajectories) beat prompt phrases when the model has them.
   - Preset libraries (`registry/director/*.yaml`: camera moves, shot sizes, lenses, bodies, eras, lighting,
     palettes, genres, pacing) are data.
   - The Elements resolver picks a strategy per model: `refs_native` (attaches refs into the right roles, allocated
     by shot size), `keyframe_first` (pre-step generates a start frame from the refs), `lora`, or `text_only`.
   - Optional Claude enhancement is cached. The compiled prompt is stored on the job for reproducibility and shown
     in a "Compiled" tab; a raw mode bypasses the director.
6. **Pipeline engine** (~500 lines on procrastinate; no Temporal).
   - Recipes are Python builders (`llm`, `generate`, `local`, `checkpoint`, `map` nodes).
   - Steps persist in `pipeline_runs/steps` with stable keys (e.g. `clip[b07]`) and input hashes, so an edited
     script only regenerates the changed blocks.
   - Checkpoints: approve, edit, pick_one, pick_many, mask, budget.
   - Pipelines never write project documents directly. They produce *takes*, which the user or an explicit step
     selects.
   - Recipes: faceless, cinema.plan / takes / export, object_swap, motion_transfer, jutsu3d, world,
     character_sheet, lora_train, talking_shot.
7. **Claude usage.**
   - `claude-sonnet-5-5`: interactive prompt enhancement, vision take-review, LoRA captions.
   - `claude-opus-5-5`: scripts, shot plans, faceless briefs. Uses structured outputs via Pydantic.
   - Load the `claude-api` skill when implementing `llm/client.py`, to get the current SDK specifics right.

### Data model (Postgres 18, SQLAlchemy 2, Alembic; UUIDv7, timestamptz, USD as `numeric(12,6)`)
- **Users and budget:** `users`, `budget_policies`.
- **Assets:** `assets` (kind, status, source, storage_key, media facts, derivatives/metadata/provider_refs JSONB),
  `asset_lineage` (child, parent, relation: media role or derivation), `asset_provider_uploads`.
- **Elements:** `elements` (kind, slug for @mention, description, prompt fragments, voice/lora JSONB),
  `element_assets` (view: front, side, face_closeup…).
- **Generations:** `generations` (model, mode, status, request/compiled/routing JSONB, estimated/actual cost,
  idempotency key, project_id + shot_key, pipeline_step_id, parent_generation_id for edit threads),
  `generation_attempts` (route, provider_job_id, scrubbed payload, cost basis, polling state), `job_events`.
- **Pipelines and projects:** `pipeline_runs`, `pipeline_steps`; `projects` (type, document JSONB, version for
  optimistic concurrency), `project_revisions`, `project_assets`.
- **Presets, spend and health:** `presets`; `spend_ledger` (append-only reservations, charges and releases),
  `llm_calls`, `provider_route_health`.
- **Columns vs JSONB:** anything filtered, joined, unique or state-driving is a column. Anything whose shape varies
  per model, provider or recipe is versioned JSONB with a Pydantic model.

### Reuse from existing folders
- `C:\Dev\AI\RunpodVideos\runpod_video_client.py`: RunPod `/run` and status polling, terminal states,
  base64-video handling → port into the `runpod` adapter.
- `C:\Dev\AI\VideoStudio\workflows\*.app.json` + `setup\api\`: Wan 2.2 / FLUX.2 workflows tuned for the 5070 →
  first `workflows/comfy` templates (convert to API format). Its ComfyUI at `127.0.0.1:8188` is the `comfy_local`
  target.
- `C:\Dev\AI\Videos\cryptid-video-production.md`: verified Seedance 2.5 per-route price notes → first pricing
  entries.
- `C:\Dev\AI\OpenMontage` (AGPL-3.0) and `MoneyPrinterTurbo`: ideas only (staged pipelines, shot prompt
  layering). Copy no code.

## Phased roadmap (video-first; each phase ships something usable)

**Status (2026-10-06):** P0 done. P1 built and tested against recorded provider contracts but not yet run
live (waiting for API keys): fal, Google, Runway and Replicate adapters, routing/failover, spend guard and
history, last-frame continue, compare-across-models. The pipeline engine (planned for P4) landed early with the
sequence, compare and keyframe recipes plus ffmpeg assembly. P2 done except the Cinema Studio virtual model
(folded into P5): director and camera moves, Motion control page, Elements/characters with @mentions,
presets gallery, Claude enhance (mock until a key is added). The opt-in local ComfyUI route (Wan 2.2 5B,
FLUX.2 klein) runs on the RTX 5070.
P3 in progress: Edit video studio (instruction edits on Aleph 2, Kling O3 Edit, Luma Ray 3.2 Modify,
FLUX 3, Seedance 2.5 edit/extend, P-Video-Edit; Topaz and ByteDance upscalers; Topaz interpolation; Bria
background removal; Kling video-to-audio; free local ffmpeg tools), Motion transfer studio (Kling 3.0 and 2.6
Motion Control, Wan 2.2 Animate move/replace, Runway Act-Two) with a movement-clip library, and the RunPod
adapter on Public Endpoints (Wan 2.2 14B, P-Video-Edit, Kling 2.6 MC). Contracts verified 2026-10-06 from
published schemas; nothing has run live yet. The custom `wan-core` endpoint (infra/runpod/wan-core) is built:
worker-comfyui 5.10.0 + our handler, a 118 GB model manifest for a network volume, and validated graphs for Wan 2.2
14B t2v/i2v (Lightning), Fun Camera and Wan-Move, routed by the `runpod_comfy` adapter; camera moves with a native
Fun Camera value now set its camera control. Deployed 2026-10-06 (endpoint ovktgiubxwld1j, first live take $0.09). Still open: the trajectory editor for
Wan-Move, and VACE / Uni3C / Animate-2 graphs.
- **Step 0 — Handoff (this session, after approval):**
  1. Save the research reports to `docs/research/`.
  2. Write `CLAUDE.md` (conventions, "add a model" checklist), the plan copy, `.gitignore`, `git init`.
  3. The user renames the folder to `C:\Dev\AI\ai-media-studio` and reopens Claude Code there.
- **P0 — Foundations:**
  - Scope: docker-compose Postgres; uv + pnpm workspace; Alembic baseline; local disk storage + streaming upload
    endpoint + Range media serving; registry schema/loader/lint with 3 models + mock routes; mock adapter; procrastinate
    submit/poll/ingest/derive + reaper; SSE hub; OpenAPI→TS codegen; Next.js shell (studio sidebar, registry-driven
    Create Video form, job tray, library grid).
  - *Ships:* the full create → progress → library loop at $0.
- **P1 — Create Video (live):**
  - Scope: fal, google_genai, runway and replicate adapters + input staging; t2v, i2v, first/last frame,
    multi-ref, multi-shot, extend (via `provider_refs`); routing, failover, circuit breaker; spend guard + confirm
    modal + spend page; keyframe images (Nano Banana 2/Pro, Seedream 5, GPT Image 2.5); "use last frame";
    compare-across-models.
  - *Ships:* our own Create Video across the flagship models.
- **P2 — Director, Elements, Presets:**
  - Scope: ShotSpec + dialects; camera-move library (65 moves, stackable) + looks; Elements library + `@mentions`
    + character-sheet flow + trait builder; keyframe-first pre-step; Claude enhance; effects/preset gallery;
    Cinema Studio virtual model.
- **P3 — Edit Video, Motion Transfer, Motion Control (open models):**
  - Scope: instruction edits (Aleph 2.0, Kling Omni Edit, Omni conversational, Luma Modify, FLUX 3 Video Edit,
    Seedance edit/extend); restyle, reframe, upscale, interpolate, background removal.
  - Scope: Kling Motion Control 3.0 UI + driving-clip library.
  - Scope: RunPod adapter, starting with public endpoints, then a custom `wan-core` worker-comfyui image (Wan 2.2 +
    Lightning + VACE + Fun Camera + Wan-Move + Uni3C + Animate-2; patched handler) and `post` (SeedVR2, RIFE);
    trajectory editor; `local_tools` ffmpeg ops; `comfy_local` adapter wired to the existing ComfyUI (opt-in).
- **P4 — Pipeline engine, Faceless Studio, Object Swap:**
  - Scope: engine + checkpoint UI; ElevenLabs TTS (with timestamps), SFX and music; ffmpeg assembly (NVENC with an
    x264 fallback); captions from the TTS alignment; thumbnails; Faceless (4 themes, 12 styles, motion-montage or
    narrated-stills modes, channels).
  - Scope: SAM mask editor (fal SAM 3) → `object_swap` recipe (mask tracking → Kling Omni Edit / Seedance edit /
    Aleph / VACE; optional maskedmerge so pixels outside the mask are untouched).
- **P5 — Cinema Studio:**
  - Scope: projects with revisions; script → shot plan (Opus); storyboard; director panel; hero-frame grid; batch
    takes + selection; continuity chaining; multi-shot scenes; extend/edit per shot; timeline (Remotion preview)
    → ffmpeg export.
- **P6 — Lipsync and avatars:**
  - Scope: Sync lipsync-3, OmniHuman 1.5, Kling Avatar 2.0, VEED Fabric, InfiniteTalk; `talking_shot`; dialogue
    on Cinema shots.
- **P7 — 3D Jutsu and World Creation:**
  - Scope: R3F editor (primitives, mannequins, GLB import, image→3D, lights, keyframed camera), render passes →
    angles → animate, or depth video → VACE.
  - Scope: panoramas, Marble API splat worlds + Spark viewer + camera-path renders shared with 3D Jutsu.
- **P8 — Local GPU, Soul ID, hardening (when the user is ready):**
  - Scope: install the gpu-worker (SAM 2, DWPose, depth, WhisperX, Kokoro/Chatterbox, RIFE); LoRA training; nightly
    `pg_dump`; auth hook; R2 storage option.

## Verification
- **Unit tests:** state-machine table, pricing (hypothesis: never negative, monotonic in duration), param mapping,
  routing/failover, pipeline engine (DAG expansion, stale invalidation, resume after crash using procrastinate's
  in-memory connector), director dialect golden snapshots (syrupy).
- **Contract tests:** generated from each registry entry's `examples` (payload validates against snapshotted provider
  schemas, constraints respected, estimate within the expected range). Registry lint: unique ids, known providers,
  `shutdown` models (e.g. Sora 2) have no enabled routes, every mode has a route.
- **Adapter tests:** vcrpy/respx cassettes per provider (submit → queued → running → done, plus 429, moderation, 400)
  with secrets scrubbed.
- **E2E at $0:** Playwright flows for every studio against mock providers and mock LLM, with real ffmpeg on mock
  clips and ffprobe assertions on duration and audio.
- **Live smoke:** `scripts/smoke_live.py --cap 3.00 --confirm-spend` runs the cheapest `smoke: true` example per
  enabled route, stops at the cap, and writes a report. Run it at the end of every phase.
- **Manual:** run web + API + worker locally (browser pane preview) and exercise each studio end to end.

## Accounts and keys (user supplies them when a phase needs them)
- P1: fal.ai, Google Gemini API, Anthropic, Runway (and optionally Replicate).
- P4: ElevenLabs.
- P3: RunPod (already connected via MCP).
- Optional: OpenRouter, Higgsfield API (DoP, Genjutsu, Soul), World Labs (Marble), Kling direct, BytePlus.

## Risks and mitigations
- **Provider churn** (Sora-style shutdowns, promo pricing): registry routes with `status` and `verified` prices;
  failover; weekly provider-schema drift check.
- **API spend:** mock-first development, estimates and confirm-before-run, reservations and caps, a kill switch,
  no automatic resubmit of ambiguous submits.
- **Output expiry:** ingest immediately; ingest retries are separate from generation retries.
- **Local storage limits** (no public URLs, RunPod output size): InputStager plus fal storage as the URL host;
  base64 RunPod outputs; add the R2 implementation if limits bite.
- **Windows async quirks:** SelectorEventLoop for the worker; threaded subprocess calls for ffmpeg.
- **Policy limits** (Seedance blocks real faces in refs; Veo person-generation rules): per-model capability flags
  with fallbacks.
- **Scope** (about a dozen studios): only two building blocks, thin studio UIs, the registry as the main extension
  point, and a phase order that ships usable features each step.
