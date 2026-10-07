# AI Media Studio

A personal, Higgsfield-class AI video studio: Create/Edit Video, Motion Control, Motion Transfer, Object Swap,
Character Creation, World Creation, Faceless Studio, Cinema Studio, 3D Jutsu, Lipsync. It orchestrates third-party
model APIs (fal.ai, Google, Runway, ElevenLabs…), open models on RunPod, and an opt-in local ComfyUI.

- **Plan and roadmap:** `docs/PLAN.md` (source of truth; check which phase we're in before starting work).
- **Detailed design:** `docs/research/06-architecture-blueprint.md`.
- **Research:** `docs/research/01–05` (Higgsfield features, commercial APIs and pricing, open models and RunPod,
  Higgsfield's live model catalog). Model versions and prices were verified 2026-10-06 and go stale fast;
  re-verify before relying on them.

## Stack
- `apps/web`: Next.js 16 App Router (Turbopack, async `params`), React 19, strict TypeScript, Tailwind v4,
  TanStack Query, Zustand, sonner. UI primitives are hand-built in `src/components/ui/controls.tsx` (native
  elements, no shadcn) to keep the grading-suite look; see the tokens in `src/app/globals.css`.
  Next 16 differs from older versions: read `apps/web/node_modules/next/dist/docs/` before using a new API.
- `apps/server`: one Python 3.12 package `studio` (uv) with two entrypoints: `api` (FastAPI) and `worker`
  (procrastinate). SQLAlchemy 2 (asyncpg) + Alembic on Postgres 18.
- `apps/gpu-worker`: stub only. Do not install local models unless the user asks.
- `registry/`: YAML model, provider, director and preset specs. **This is the main extension point.**
- `workflows/comfy/`: ComfyUI API-format workflows + `.map.yaml` injection maps (shared by RunPod and local).
- Storage: **local disk** (`data/media/`) behind a `Storage` interface. Provider inputs go through the InputStager,
  with results cached in `asset_provider_uploads`.

## Non-negotiable rules
- **Mock first.** `PROVIDER_MODE=mock` is the default. Never call a paid route in tests. Live calls only via
  `PROVIDER_MODE=mixed` + `LIVE_ROUTE_ALLOWLIST`, or `scripts/smoke_live.py` with a `--cap`.
- **Never auto-resubmit an ambiguous paid submit.** It goes to `unknown`.
- **Ingest every provider output to disk immediately.** Veo deletes after 2 days, Replicate after 1 hour.
- **Status changes go through `domain/fsm.transition()` only.** It writes the status, a `job_events` row and
  `pg_notify` in one transaction.
- **No per-model special cases in core code.** Put the variation in registry YAML (param maps, transforms,
  constraints) or a dialect/transform module.
- Every table row carries `user_id`. Get the user from `deps.current_user()`; never hard-code it.
- Windows: the worker runs on a `SelectorEventLoop`, and ffmpeg runs via `subprocess.run` inside
  `anyio.to_thread.run_sync`, never `asyncio.create_subprocess_exec`.
- Sora 2 is shut down (2026-09-24). Do not add it.
- Before writing `studio/llm/*`, load the `claude-api` skill. Default models: `claude-opus-5-5` (scripts, shot
  plans) and `claude-sonnet-5-5` (enhance, vision review).
- `C:\Dev\AI\OpenMontage` is AGPL-3.0. Borrow ideas only; never copy its code.

## Adding a model
1. Draft YAML in `registry/models/<type>/<id>.yaml` (or run `scripts/import_catalog.py`). Include `status`,
   `license_tier`, modes with media slots, params, and ≥1 route with a pricing `verified` date and `source`.
2. Add at least one `examples:` entry, marking the cheapest one `smoke: true`.
3. Run `uv run pytest apps/server/tests/contract -k <id>` and the registry lint.
4. Optional live check: `uv run python scripts/smoke_live.py --routes <route-id> --cap 0.50 --confirm-spend`.
5. Run `pnpm gen` if the API schema changed.

## Registry expressions and mapping (generic features; use these before writing code)
- Price `quantity`/`formula` and route `eta` see the params, `mode`, `duration`, `input_video_seconds`,
  `input_video_frames`, `input_audio_seconds`, `input_images`, and `<role>_seconds` per input (e.g.
  `driving_video_seconds`; missing inputs count as 0). Functions: `max`, `min`, `round`, `ceil`. Rates can
  match on `mode` too (`when: {mode: video_edit, resolution: "720p"}`; the most specific match wins).
- Endpoints can template params: `fal-ai/kling-video/v3/{tier}/motion-control` (map the param `to: null`).
- A media field ending in `[]` is always sent as a list (`media: {input_video: "video_urls[]"}`).
- Adapters that learn the real charge return `usage.cost_usd` (RunPod `output.cost`, Runway credits x $0.01);
  ingest charges that instead of the estimate.
- Studios that share the Create form (Create video/image, Edit video, Motion transfer) pick models and modes
  by op tags; see `apps/web/src/lib/studioKinds.ts`. Tag a new model's modes and it shows up in the right studio.
- `type: paths` params are motion paths drawn on the start frame (`[{points: [[x, y], ...]}]`, 0-1 of the frame
  cropped to the output aspect; one point = pin). The Create form shows the editor under the start frame
  (`MotionPathsField`); a route transform converts them (Wan-Move: `transforms/comfy.py:wan_move_tracks`).
- `type: points` params are clicks on an image (`[[x, y, label]]`, 0-1, label 1 = this, 0 = not this); routes turn
  them into pixels using the input's `width`/`height` on the request media (SAM: `transforms/segment.py`).
- Enum params can carry `labels` (option -> what the user sees) for options that are ids, e.g. voices.
- Media kinds include `mask` and `mask_video` (white = the selected object), so a matte is never passed where a
  picture is expected. Outputs of `output_type: mask` models are stored with those kinds.
- Ops for audio and selection: `tts`, `sfx`, `music`, `segment`, `track`. Edit modes that can change one object in a
  clip carry `object_replace`; the Object swap recipe picks models by it.

## Adding a preset (effects gallery)
- One YAML per preset in `registry/presets/<category>/<id>.yaml`: `model_id`, `mode`, a `prompt` template with
  `{key}` blanks declared in `fields`, `params`, optional `direction` (camera moves and look ids from
  `registry/director/`), and `inputs` (media roles the user must bring). The registry lint rejects unknown
  models/modes/params/moves, undeclared or unused blanks, and missing required inputs.
- Rendering returns an ordinary generation request, so presets get the same routing and spend guard.

## Pipelines (recipe runs)
- `studio/pipelines/`: a recipe is a **pure planner**. `plan(input, settled_outputs)` returns every step it can
  define now (`generate`, `local`, `llm`, `checkpoint`), keyed by stable keys like `shot[02]`. The engine
  (`engine.reconcile`) diffs that against stored `pipeline_steps` by input hash: new keys are added, changed
  inputs are redone, finished work is kept. Editing a run (`POST /pipelines/{id}/revise`) reruns only what changed.
- Generate steps are normal Generations (`group_id` = run id, `pipeline_step_id` set), capped by the run's
  `cap_usd`. When one finishes, `fsm.transition()` marks the run and `pipeline.advance` is deferred after commit
  (`pipelines/hooks.py`); the reaper re-advances runs that look stuck. Local ops live in `pipelines/ops.py`.
- Add a recipe: subclass `Recipe` in `pipelines/recipes/`, register it in `recipes/__init__.py`, and test it with
  the in-memory `drain()` helper in `tests/integration/test_pipelines.py`. Use `recipes/common.py` to pick
  modes by what they need, never by model id.
- Built-in: `sequence` (last-frame chaining + ffmpeg join; also how any model "extends"), `compare` (fan-out +
  pick checkpoint), `keyframe` (render a start frame from @character refs, then animate; for Kling), `faceless`
  (script -> approve -> block plan -> narration + stills or clips -> music -> assembly -> captions -> thumbnail), and
  `object_swap` (track a SAM mask -> edit with 1-3 models -> composite through the matte -> pick).
- Claude steps are `llm` steps whose `op` is in `pipelines/llm_ops.py`; they run in `pipeline.step` like local ops.
  Generate-step outputs include `duration_ms`, so a recipe can size what follows (a clip as long as its narration).
- A `PlannedJob` can name inputs that already exist (`media=((role, asset_id), ...)`), so the run estimate prices
  them at their real length. Narration is estimated from `prompt_chars`.
- Revising a run while a checkpoint waits updates that checkpoint's question in place; it keeps waiting.

## Claude
- `LLM_MODE=mock` (default) returns canned results and never bills; `live` needs `ANTHROPIC_API_KEY`.
- Call Claude only through `studio/llm/client.structured()`: Pydantic structured output, refusal fallback,
  a budget pre-check, and a `spend_ledger` charge per call (prices in `llm/pricing.py`, with a verified date).

## Commands
```
pnpm db:up      # Postgres 18 in Docker on :5433
uv sync && pnpm i && pnpm db:migrate
pnpm dev        # API :8400 (reload), worker, web :3000
pnpm gen        # OpenAPI -> packages/api-client/src/schema.d.ts (run after API schema changes)
pnpm test       # pytest: unit, registry contract, DB integration (uses a separate studio_test database)
pnpm e2e        # Playwright against the mock provider (reuses a running `pnpm dev`)
pnpm lint       # ruff + eslint + tsc
```
- The API runs on **8400**, not 8000 (other local containers publish 8000). Web reads `NEXT_PUBLIC_API_URL`.
- Run the API via `python -m studio.api`, not bare uvicorn: on Windows it must use `asyncio:SelectorEventLoop`.
- Media is served by the API at `/media/{key}` with Range support; files live under `data/media/`.

## Providers without a model
- `local_tools`: the `video-tools` model's modes (reframe, trim, retime, reverse, loop, interpolate, upscale,
  stabilize) run on this machine's ffmpeg (`studio/media/tools.py`). Free; local routes check
  `adapter.reachable()`.
- `runpod`: Public Endpoints now (`/run` + `/status`, `RUNPOD_API_KEY`). Inputs must be web links, so files are
  hosted through fal storage (needs `FAL_KEY`); small images go inline without it. Never call `/retry`.
- `runpod_wan_core` (adapter `runpod_comfy`): our own serverless endpoint, `infra/runpod/wan-core` (worker-comfyui
  5.10.0 = ComfyUI 0.34, models on a network volume). Routes name `workflows/comfy/runpod_*.json` templates, like
  `comfy_local`; the lint checks every field a ComfyUI route sends has an input in its template. Needs
  `RUNPOD_API_KEY` + `RUNPOD_WAN_CORE_ENDPOINT` (a provider's `key_setting` may list several settings). Cost is the
  worker's `executionTime` at the provider's `gpu_usd_per_hour`, kept flagged as an estimate. Check new graphs
  against the local ComfyUI 0.34 (same version) before pushing: `/prompt` validates them without the models.

- `elevenlabs`: Eleven v4 TTS (with word timings), sound effects and music (`ELEVENLABS_API_KEY`). The API is
  synchronous, so submit parks the audio in a ResultStore (like Gemini images). TTS word timings land on the asset
  as `metadata.alignment.words` (`[{text, start, end}]`); the mock times words at a reading pace instead.

## Faceless Studio and Object Swap
- Themes and styles are data in `registry/faceless/` (`studio/faceless/catalog.py`, reloaded on change); each style
  has a caption look. Channels (`channels` table) keep a series' theme, style, notes and episode defaults; episodes
  are `faceless` runs whose input names the channel (`GET /pipelines?channel_id=`).
- Assembly is `media/assemble.py` (Ken Burns stills, clips fitted to their narration, ducked music, -14 LUFS) and
  captions `media/captions.py` (cues from word timings, SRT + ASS burned in with libass). ffmpeg 5's scheduler
  deadlocks on concat -> asplit -> sidechaincompress in one graph, so the voice track is rendered to a WAV first.
- Object swap selects with SAM 3 on fal (`sam-3-image` clicks -> mask, `sam-3-video` tracks it); `media/swap.py`
  composites the edit over the original through the thresholded, grown and feathered matte.
- The registry holder notices edited YAML in every process (stamps checked at most once a second), so the worker
  never misses a model the API accepts; a broken file keeps the last good registry.

## Local engine (ComfyUI)
- `comfy_local` provider = the VideoStudio ComfyUI at `COMFYUI_URL` (default http://127.0.0.1:8188). Start it with
  `C:/Dev/AI/VideoStudio/ComfyUI_windows_portable/python_embeded/python.exe C:/Dev/AI/VideoStudio/setup/launcher.py --no-browser`
  (stop with `--stop`). Local models: `wan-2.2-5b` (t2v/i2v) and `flux-2-klein-4b` (t2i).
- Local routes are free and run in every PROVIDER_MODE. A model with a configured local route never falls back to
  the mock; if ComfyUI is off, routing reports "isn't running" instead.
- Workflows live in `workflows/comfy/<name>.json` (API graph) + `<name>.map.yaml` (which node inputs to fill,
  optional nodes, outputs). Routes name the template as their endpoint; `transform` hooks in
  `studio/providers/transforms/` map quality/aspect to pixel sizes. Route `eta` expressions drive time estimates.
- One job at a time (12 GB). Draft Wan ≈ 70–90 s, standard ≈ 7 min.

## Reusable assets elsewhere on this machine
- `C:\Dev\AI\RunpodVideos\runpod_video_client.py`: RunPod `/run` + status polling + base64 video handling.
- `C:\Dev\AI\VideoStudio\`: ComfyUI v0.34 portable (CUDA 13) with Wan 2.2 TI2V-5B, and workflows tuned for the
  RTX 5070 (12GB VRAM, 64GB RAM). This is the `comfy_local` route target (`COMFYUI_URL=http://127.0.0.1:8188`).
- `C:\Dev\AI\Videos\cryptid-video-production.md`: verified Seedance 2.5 per-route price notes.
