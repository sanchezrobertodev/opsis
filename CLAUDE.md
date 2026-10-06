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

## Reusable assets elsewhere on this machine
- `C:\Dev\AI\RunpodVideos\runpod_video_client.py`: RunPod `/run` + status polling + base64 video handling.
- `C:\Dev\AI\VideoStudio\`: ComfyUI v0.34 portable (CUDA 13) with Wan 2.2 TI2V-5B, and workflows tuned for the
  RTX 5070 (12GB VRAM, 64GB RAM). This is the `comfy_local` route target (`COMFYUI_URL=http://127.0.0.1:8188`).
- `C:\Dev\AI\Videos\cryptid-video-production.md`: verified Seedance 2.5 per-route price notes.
