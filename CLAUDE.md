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
  `PROVIDER_MODE=mixed` + `LIVE_ROUTE_ALLOWLIST`, or `scripts/smoke_live.py` with a `--cap`. (The home server's
  `.env` is `live`, for real use.)
- **Mode switch:** `PROVIDER_MODE` and `LLM_MODE` in `.env` are a ceiling. The Settings switch (Generation:
  Mock / Mixed / Live, Claude: Mock / Live; `budget_policies.provider_mode` / `llm_mode`, `budget/modes.py`) picks that
  or safer and applies at once, with no restart. Read the mode in effect through `modes.current()` /
  `routing_env(session, reg, user_id)`, never `settings.provider_mode` directly.
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
  `driving_video_seconds`; missing inputs count as 0), and `input_video_aspect` (long over short side of the first
  clip, 16:9 if unknown; for outputs that keep the clip's shape). Functions: `max`, `min`, `round`, `ceil`. Rates can
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
- Enum params with `director: <list>` (e.g. `director: palettes`) get their options and labels from
  `registry/director/looks.yaml` when the registry loads.
- `type: shots` params are multi-shot takes (`[{prompt, duration, direction?}]`, op `multi_shot`, mode `prompt: none`).
  The plan compiles each shot's prompt with the take's direction under the shot's own and sets `duration` to the total.
  Routes send them with the `shot_list` transform (Kling `multi_prompt`) or `timed_prompt` (Seedance, one prompt that
  cuts on cue; `prompt_budget: shared` splits the prompt limit between shots).
- A route's `output` can carry `meta` (stored on each asset, e.g. `{projection: equirectangular}` marks a 360
  panorama; `GET /assets?projection=equirectangular` lists them) and `extras` (more files the same job returns, each
  its own asset: Marble's panorama and collision mesh next to its splat). Mock routes copy both.
- `kind: virtual` models (Cinema Studio) have no routes: `virtual.engines` maps each mode to `base-model/mode`, and
  `registry/virtual.py:resolve` swaps the request for the engine's before mentions, compiling, routing and pricing.
  Shared params are fitted (durations rounded, unknown values dropped with a warning); `virtual.director` params
  become shot direction. Generations store the base model and `request.via` names the studio.
- Rules can limit input length while they apply: `max_input_seconds: {input_audio: 30}` (OmniHuman at 1080p).
- An output can be decoded on our side when the answer isn't a file: `output: {path: rle, kind: mask_video, decode:
  studio.media.rle:mask_video}` (SAM 3 video's per-frame run-length masks become the matte clip at ingest). Mark
  `unpublished: true` when a provider returns a field its published schema doesn't list (the drift check skips it).
- Media slots can require `min_side` (px, both sides) and `min_fps` for pictures and clips (Kling O3 Edit: 720, 23 fps), so a
  clip a provider would refuse is turned away before submit with a clear message.
- Slots can also set `max_pixels` (width × height), `min_aspect` and `max_aspect` (width / height, inclusive).
  These limits require known dimensions and reject oversized or out-of-shape inputs before submission.
- Lipsync models carry the `lipsync` op and take `input_audio`; picture-driven ones also carry `avatar` and take
  `character_image`, clip-driven ones take `input_video`. Price them on `input_audio_seconds`. The mock makes a clip
  as long as the given `input_audio`, playing it.
- `type: sections` params are a song's lyrics (`[{name, lines, duration}]`, mode op `song`): each section's lines are
  sung over its seconds and the song is as long as its sections together (it sets `duration`). Eleven Music's `song`
  mode parks them as `_song`; `transforms/music.py:song_plan` sends them as the composition plan (music_v1 sections,
  the prompt as global styles) and drops the prompt and length, which a plan can't take.
- `prompt.dialogue: true` marks models that speak lines written in their prompt (Veo 3.1, Seedance 2.5, Kling 3.0);
  the director writes `direction.dialogue` into their prompt dialect and warns on other models.

## Adding a preset (effects gallery)
- One YAML per preset in `registry/presets/<category>/<id>.yaml`: `model_id`, `mode`, a `prompt` template with
  `{key}` blanks declared in `fields`, `params`, optional `direction` (camera moves and look ids from
  `registry/director/`), and `inputs` (media roles the user must bring). The registry lint rejects unknown
  models/modes/params/moves, undeclared or unused blanks, and missing required inputs.
- Rendering returns an ordinary generation request, so presets get the same routing and spend guard.

## Adding a template (guided stories)
- The Templates page (`/templates`, formerly Presets) shows presets and guided templates. A template is one YAML in
  `registry/templates/<id>.yaml`: `recipe`, a fixed recipe `input` whose string values may hold `{key}` blanks from
  `fields`, and `inputs` (`{key, kind, label, optional}`: media whose asset id goes into the input at `key`, or
  `kind: character`, a saved character's handle, picked from a list; optional inputs are alternatives, so choosing one
  clears the others, and the recipe says which it needs). The `vlog` and `music_video` recipes take `character` (a
  handle) as well as `character_image`: each frame prompt mentions `@handle`, so the character's pictures attach
  through the usual mention resolution, and the first Claude step checks the character exists and has a picture (the
  vlog also takes their name). Rendering
  (`POST /templates/{id}/render`, `registry/presets.py:render_template`) returns a `RunCreate`; the page estimates and
  starts it like any run. The lint checks the recipe exists, blanks match fields, and a sample input passes the
  recipe's `Input` model and `validate()` (unknown models, wrong lipsync modes).

## Pipelines (recipe runs)
- `studio/pipelines/`: a recipe is a **pure planner**. `plan(input, settled_outputs)` returns every step it can
  define now (`generate`, `local`, `llm`, `checkpoint`), keyed by stable keys like `shot[02]`. The engine
  (`engine.reconcile`) diffs that against stored `pipeline_steps` by input hash: new keys are added, changed
  inputs are redone, finished work is kept. Editing a run (`POST /pipelines/{id}/revise`) reruns only what changed.
- Generate steps are normal Generations (`group_id` = run id, `pipeline_step_id` set), capped by the run's
  `cap_usd`. When one finishes, `fsm.transition()` marks the run and `pipeline.advance` is deferred after commit
  (`pipelines/hooks.py`); the reaper re-advances runs that look stuck. When a step fails the run ends, but takes
  still rendering finish and are folded into their steps (kept for a retry); the reaper wakes any it missed. Local ops live in `pipelines/ops.py`.
- Add a recipe: subclass `Recipe` in `pipelines/recipes/`, register it in `recipes/__init__.py`, and test it with
  the in-memory `drain()` helper in `tests/integration/test_pipelines.py`. Use `recipes/common.py` to pick
  modes by what they need, never by model id.
- Built-in: `sequence` (last-frame chaining + ffmpeg join; also how any model "extends"), `compare` (fan-out +
  pick checkpoint), `keyframe` (render a start frame from @character refs, then animate; for Kling), `faceless`
  (script -> approve -> block plan -> narration + stills or clips -> music -> assembly -> captions -> thumbnail),
  `object_swap` (track a SAM mask -> edit with 1-3 models -> composite through the matte -> pick),
  `talking_shot` (a picture or clip + script -> TTS -> lipsync on 1-3 models -> pick; or recorded speech), and `vlog`
  (a picture + an idea -> Claude's shots -> each shot drawn from the picture, voiced, then lip-synced on camera or
  animated as a cutaway; a failed one falls back to its picture -> music -> cut -> captions). A narrated cut's block
  with `hold: true` (a lip-synced clip) is never slowed to fill its block; its last frame is held instead.
- `music_video`: a song (+ an optional star's picture) -> `transcribe` (a speech-to-text model, op `transcribe`;
  `scribe-v2`, ElevenLabs Scribe v2 on fal, $0.008/min) -> `shots` (local op `lyric_shots`, `media/transcript.py:shots`:
  cut at the breaths nearest ~6 s, 3-10 s each) -> `plan` (Claude: a visual per section from its lyrics) -> a picture
  and a clip per shot (a failed clip shows its picture) -> `parts` (the song cut at the shots) -> `cut` (gap 0, so the
  song plays unbroken) -> `captions` with each shot's transcript words (`captions` op `words`). An instrumental (no
  words heard) gets shots from the idea and no captions. The estimate prices a three-minute song.
- Speech-to-text models have `output_type: text`; their route decodes the answer into a transcript asset (kind
  `subtitle`, JSON `{"words": [{text, start, end}]}`, `transcript.py:words_json`). The mock hears a "la" every half
  second with a breath after every eight.
- A generate step whose take the provider refuses on content grounds (`error_code` moderation) gets one more try:
  the engine holds it, Claude (Sonnet) rewords its prompt, or each shot's of a multi-shot take, in one call, each no
  longer than the original and keeping its meaning and @mentions
  (`llm_ops.reword`; the mock drops its `[[mock:fail=...]]` tags), and the step starts again. The step keeps its input
  hash, so the plan keeps it; `_reworded` records the original. A second refusal, or a prompt Claude keeps as it was,
  fails the step as before (optional ones still fall back).
- Claude steps are `llm` steps whose `op` is in `pipelines/llm_ops.py`; they run in `pipeline.step` like local ops.
  Generate-step outputs include `duration_ms`, so a recipe can size what follows (a clip as long as its narration).
- A `PlannedJob` can name inputs that already exist (`media=((role, asset_id), ...)`), so the run estimate prices
  them at their real length. Narration is estimated from `prompt_chars`; inputs a run makes first (speech from a
  script) are priced at a guessed length with `planned_seconds=((role, seconds), ...)`.
- Revising a run while a checkpoint waits updates that checkpoint's question in place; it keeps waiting.

## Spending
- Settings shows provider balances (`GET /budget/balances`, `studio/budget/balances.py`: fal, RunPod, Runway and
  ElevenLabs report one; free reads kept for a minute; the others link to their billing pages) and where the money
  went (`GET /budget/breakdown`): ledger charges by studio feature (`studio/budget/features.py`: the run's recipe, the
  project, else the mode's op tags; a new op tag must join a feature, a test checks) and by model.
- Bills correct the estimates (`studio/budget/reconcile.py`, hourly in the worker and `POST /budget/reconcile`, reads
  only): fal per request (`/v1/models/billing-events`, admin key; matched by the request id in `provider_job_id`), our
  RunPod endpoints per settled hour (`/v2/billing/serverless`; the hour's difference spread over its charges, idle
  time with no charge booked on its own). Each correction is an `adjustment` row dated like its charge, one per
  attempt; sum spend with `domain.enums.SPENT` (charge + adjustment), never `charge` alone. Google, ElevenLabs and
  Replicate report no per-request cost and stay estimates. A provider's `idle_seconds` is added to its run time.

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
uv run python scripts/backup_db.py                 # dump the database to data/backups/ (14 days kept)
uv run python scripts/restore_db.py --check        # restore the newest dump into a scratch copy and compare
uv run python scripts/fetch_provider_schemas.py    # provider schema drift report (free; --update re-baselines)
```
- The API runs on **8400**, not 8000 (other local containers publish 8000). Web reads `NEXT_PUBLIC_API_URL`.
- Run the API via `python -m studio.api`, not bare uvicorn: on Windows it must use `asyncio:SelectorEventLoop`.
  `pnpm dev` adds `--reload`, so saving server code restarts the API and drops requests in flight (`/health`
  reports `reloads_on_change`). For a long live run, start it without `--reload` or don't edit `apps/server/src`
  meanwhile; `smoke_live.py` retries dropped requests either way. Generations aren't affected: the worker runs them.
- Media is served by the API at `/media/{key}` with Range support; files live under `data/media/`.

## Backups and schema checks
- `scripts/schedule_tasks.ps1` registers two Windows scheduled tasks (`-Remove` takes them away): the database
  backup nightly at 03:30 and the provider schema check on Mondays at 09:00, logging to `data/logs/`.
- Backups (`studio/ops/backups.py`) run `pg_dump -Fc` inside the Postgres container (Docker must be up), keep 14
  days in `data/backups/`, and are only kept once `pg_restore --list` reads them. Restores never drop anything:
  `--check` uses a scratch database; `--replace --yes` (API and worker stopped) restores into a new database and
  swaps it in, keeping the old one as `studio_before_restore_<time>`.
- **Media is not in the database.** `data/media/` (every asset) needs its own copy, e.g.
  `robocopy data\media E:\studio-media /MIR` to an external drive; a restored database pointing at missing files
  shows broken assets.
- The schema check (`studio/registry/drift.py`) reads each route's published schema (fal per endpoint, Runway,
  ElevenLabs, World Labs from the OpenAPI block in its public docs pages; Replicate with its key) and reports gone or
  deprecated endpoints, fields a route sends that the endpoint doesn't take, required fields it never sends, choices
  the endpoint rejects (after the model's own rules), and changes since the baselines in `registry/provider-schemas/`.
  It compares top-level fields only: of a nested one (Marble's `world_prompt`) it sees just the type, i.e. which
  prompt kinds exist, not the fields inside them. The contract tests check every route against those baselines offline. After a reviewed change, fix the
  YAML and run with `--update` (`--provider` limits it).

## Running on the home server
- The studio you use runs on the Fedora home server (`infra/server/`, README there; plan in `docs/home-server-move.md`):
  - **Containers:** rootless Podman with Quadlet runs the pod `ams` with Postgres 18, the API, the worker and the web app.
  - **Addresses:** web `https://fedora.tail36d2f8.ts.net:19443/`, API `:19444`, tailnet only.
  - **Files:** the checkout is `~/Development/repos/ai-media-studio`, with its `.env` and `data/`.
- The PC is for development: mock mode against its own database, tests and Playwright.
- **Deploying:** merge, then press Deploy on the Hub (`infra/server/deploy.sh`). It pulls, builds both images (the API
  address is compiled into the web image), migrates and restarts.
- The local models stay on the PC: the server's `COMFYUI_URL` points at the PC's ComfyUI over Tailscale. When the PC is
  off, the route says it isn't running.
- **Backups:** `DB_EXEC` says how backups reach the Postgres tools. Unset is `docker compose exec` (the PC); empty
  runs them in place (the worker container has the Postgres 18 client). systemd timers replace the Windows tasks.
- **Off-site copy:** the hourly `ams-offsite.timer` (`infra/server/offsite.sh`) sends new media, as tar bundles, and
  new dumps to the PC with Taildrop. The PC's "AI Media Studio backup collect" task files them on
  `E:\Backups\ai-media-studio`: a media mirror plus 60 days of dumps.
- The server's caption fonts are open stand-ins (`infra/server/fonts.conf`).

## Providers without a model
- `local_tools`: the `video-tools` model's modes (reframe, trim, retime, reverse, loop, interpolate, upscale,
  stabilize) run on this machine's ffmpeg (`studio/media/tools.py`). Free; local routes check
  `adapter.reachable()`.
- `runpod`: Public Endpoints now (`/run` + `/status`, `RUNPOD_API_KEY`). Inputs must be web links, so files are
  hosted through fal storage (needs `FAL_KEY`); small images go inline without it. Never call `/retry`.
- `runpod_wan_core` (adapter `runpod_comfy`): our own serverless endpoint, `infra/runpod/wan-core` (worker-comfyui
  5.10.0 = ComfyUI 0.34 with a CUDA 13 torch, which turns on comfy-kitchen's kernels; models on a network volume;
  RTX PRO 6000 hosts with CUDA 13 drivers only). Routes name `workflows/comfy/runpod_*.json` templates, like
  `comfy_local`; the lint checks every field a ComfyUI route sends has an input in its template. Needs
  `RUNPOD_API_KEY` + `RUNPOD_WAN_CORE_ENDPOINT` (a provider's `key_setting` may list several settings). Cost is the
  worker's `executionTime` at the provider's `gpu_usd_per_hour`, kept flagged as an estimate. Check new graphs
  against the local ComfyUI 0.34 (same version) before pushing: `/prompt` validates them without the models.
  Workflows: LTX-2.5 (`ltx-2.5`, t2v/i2v/flf2v with sound, the open default since 2026-10-10; two stages via
  its x2 latent upscaler), Wan 2.2 14B Fast (i2v only), Fun Camera, Wan-Move, VACE masked edit (`wan-vace`, `object_replace`, so Object
  swap uses it with the tracked matte), Animate-2 (`wan-animate-2`, motion transfer) and Uni3C camera copy
  (`wan-uni3c`, high-noise expert only). Clip inputs are re-timed to 16 fps before upload (route `media_fps`, hosted
  afresh, not from the per-asset upload cache), so 81 frames cover 5 s of any clip; clip-driven ones size the output
  from the clip (`transforms/comfy.py:wan_clip_size`). New weights go in `models.yaml` with exact sizes; the
  graphs travel with each request, so adding a workflow needs no image rebuild.
  Results over 5 MB go to a fal storage upload slot the adapter makes per output at submit (`uploads`, kept a day)
  and are ingested from its link; smaller ones, and any whose upload fails (up to 20 MB), come back inline as base64.

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
- The Kids song mode is Faceless `format: song` (offered for the Kids theme): Claude writes lyrics in sections, each
  with a picture (`writer.write_song`; or your own lyrics, a section per paragraph, `[Chorus]` names one), Eleven Music
  sings them with fixed section lengths (about 4 s a line plus a bar in, `section_seconds`), `split_audio` cuts the
  song at the sections, and `narrated_cut` plays each piece under its picture back to back (`gap_s: 0`). Captions
  spread each section's lines after its bar of lead-in (`lead_in`), a line per caption; no narration, no score.
- Object swap selects with SAM 3 on fal (`sam-3-image` clicks -> mask, `sam-3-video` tracks it); `media/swap.py`
  composites the edit over the original through the thresholded, grown and feathered matte.
- The registry holder notices edited YAML in every process (stamps checked at most once a second), so the worker
  never misses a model the API accepts; a broken file keeps the last good registry.

## Cinema Studio (projects)
- A film is a `projects` row (`kind: cinema`) whose JSONB `document` is `studio/cinema/document.py:CinemaDocument`
  (script, cast slugs, settings, scenes of shots with short stable ids). Saves send the `version` they started from
  (stale -> 409) and every save is kept in `project_revisions`; restoring makes a new version.
- Runs never write the document. `cinema_frames` (hero frames: grids up to 3 x 3, opening or closing frames,
  split into calls the image model can make) and `cinema_takes` (takes, chained from the previous shot's last frame
  where a shot continues, and ending on the shot's picked closing frame `end_asset_id` with the model's start+end mode) make generations tagged `project_id` + `shot_key`
  (`GET /projects/{id}/takes`); the editor picks them into the document.
- The cut is `document.cut`: picture items (shots, whose picked takes play trimmed as the shot says, and title
  cards) in order, and audio clips on dialogue, music and SFX tracks; with no items, picked takes play in story
  order. Timing is in whole frames at 24 fps on both sides: `media/timeline.py:plan` and `web/src/lib/cut.ts`
  (fades overlap like `xfade`), so the Remotion preview (`CutPreview.tsx`, `TransitionSeries`) and the
  `cinema_export` file have the same length and order. The export renders each item to its frame count, joins
  them, then mixes the tracks (dialogue to a WAV first so the music can duck under it). Dialogue tied to a shot
  (`shot_id`) follows the shot; `GET /projects/{id}/dialogue` finds each shot's speech (what its picked take was
  lip-synced to, else the shot's joined lines). Remotion's license is free for individuals and small companies.
- More takes from a take: `cinema_scene` films a scene's shots in one multi-shot call and `split_shots` cuts it back
  into a take per shot; `cinema_extend` continues a take from its last frame (`after`) or draws a frame from a moment
  earlier and animates it into the take's first frame (`before`, needs a start+end-frame mode), then `join_takes`;
  `cinema_edit` changes the whole take with an Edit Video model; an `object_swap` run given `project_id` + `shot_key`
  makes a region edit. Clips made by a local step carry `metadata.project_id/shot_key/made_by/parent_asset_id`, which
  is how the takes list finds them and shows what each was made from.
- Storyboard: cards drag with dnd-kit (pointer on the card, keyboard on its handle; `lib/cinema.ts:moveShot`), and
  shots duplicate and split there too (a filmed shot's take is shared: the parts' trims meet at the split). Cost
  badges come from one `cinema_takes` estimate split by shot (`PlannedJob.key` -> `RunEstimateOut.by_key`) and from
  `GET /projects/{id}/spend` (charged costs per `shot_key`).
- `POST /projects/{id}/plan` asks Claude (Opus) for scenes and shots in the director's vocabulary; unknown ids are
  dropped and nothing is saved until the editor applies the proposal.
- Keeping faces the same: the plan also proposes people who recur but aren't in the cast (`characters`: handle, name,
  look), written into shots as `@handle`; an `@handle` nobody has becomes a plain word. `POST /projects/{id}/cast` saves
  the kept ones as character Elements (slug from the handle) and returns the scenes pointed at their slugs; the Script
  tab then adds them to the cast and starts `cinema_cast`, which draws a front portrait of each (`add_element_image`
  keeps it as the reference and cover). Hero frames on a `refs_native` model attach it wherever they're mentioned.
  Until a mentioned character has a picture (the portrait is still drawing), a mode that needs reference images only
  for the mentions runs as the model's text mode instead, with a warning (`domain/generations.py:_text_mode_without_refs`).
- The AI Director chat (`POST /projects/{id}/director`, `cinema/director_chat.py`, the editor's Director panel): Claude
  answers a request with edits by shot and scene id (update, add, move, remove), applied to the storyboard on screen
  and returned as proposed scenes plus a diff (`changes`, shots numbered as they'll read). An update keeps the shot's
  picks, trims and the rest of its look. Applying is refused in the browser if the storyboard changed since asking.
- Dialogue: a shot's `lines` (who + words; blank lines are kept for editing and skipped when filming) travel to the
  takes run as `direction.dialogue` with each speaker's voice from their Element (`elements.voice` =
  `{model_id, params}`). Models with `prompt.dialogue` speak them; on others `cinema_takes` voices each line
  (`line[shot:k]`), joins them (`track[shot]`, op `join_audio`) and lip-syncs every take to the track
  (`lip[shot:n]`, the film's `lipsync_model_id`), each a new take.
- The director (`studio/director/compiler.py`): besides the look lists, `lights` (up to two placed sources) and
  `cast_emotions` (a feeling per @mentioned cast member, phrased with the name the mention resolved to). Golden
  snapshots per dialect live in `tests/unit/golden/director/`; regenerate with `UPDATE_GOLDEN=1` and review the diff.

## Trained characters (Soul ID)
- The `lora_train` recipe learns a character's face: `dataset` (local op `lora_dataset`: 10-80 of its photos zipped
  into an `archive` asset) -> `train` (`wan-2.2-lora-trainer`, fal's Wan 2.2 trainer, $0.0045/step, captions written by
  fal) -> `attach` (local op `attach_lora`: `element.lora = {base, trigger, low, high, steps, run_id, trained_at}`) ->
  an optional portrait preview. The trigger word defaults to a made-up word from the name; the character page's
  "Trained identity" panel starts runs and can forget one (`PATCH /elements/{id}` `clear_lora`).
- Models with `elements: {strategy: lora, lora_base: wan-2.2-a14b}` (`wan-2.2-image` stills, `wan-2.2-lora` video on
  fal) load a mentioned character's LoRA: the resolver attaches the files in the mode's `auto` slots (`lora`,
  `lora_high`; never shown in forms) and the mention reads "Name (trigger)". The `lora_list` transform turns the staged
  links into fal's `loras` (high-noise file on the high expert, low on low; a single file on both).
- Route map targets starting with `_` are parked for a payload hook and never sent; the schema check skips them.
- LoRA files (`lora`) and zips (`archive`) are never probed and stay out of the default library listing.

## 3D blocking and Worlds
- Media kinds `model3d` (GLB) and `splat` (.spz/.ply) are never probed: uploads are checked by magic bytes, and their
  library pictures are drawn in the browser and posted to `POST /assets/{id}/thumbnail` (`lib/thumbs.ts`).
- A scene is a `projects` row (`kind: jutsu3d`) whose document is `studio/jutsu3d/document.py` (objects, keyframe
  tracks, settings; a `world` object holds a splat and its collision mesh). Render passes are drawn on the editor's
  canvas (`RenderPanel.tsx:renderScene`), uploaded frame by frame (`api/routers/renders.py`) and joined by ffmpeg into
  a video per pass, or stills at the camera's keys. The `jutsu3d` recipe makes those renders real: angles,
  depth (the depth pass drives Wan VACE depth) or real (the picture pass re-rendered as live action by LTX-2.3 render to
  real, op `render_to_real`; `lib/jutsu3d.ts:realParams` sizes it to the render, 720p up to about 6 s, else 480p).
- Rigged models play clips on the timeline: a model object's `animation` (`{clip, source_asset_id, start, offset,
  speed, loop}`) names a clip in its own GLB or in another on the same skeleton (bones match by name), e.g. a rigging
  job's walking or running GLB. `SceneObjects.tsx:Animated` clones the rig (SkeletonUtils), poses it with a paused
  AnimationMixer at `clipTime(...)` for the playhead, and leaves `userData.seek` for the render passes, which pose each
  frame. A model without clips can be rigged from its Animation panel: `meshy-rigging` (op `rig`, role
  `input_model`; Meshy 7.1 can rig while it generates) returns the rigged GLB plus walking, running and an optional
  library clip, each a `model3d` extra tagged `metadata.clip_source`; the rigged GLB replaces the object's model.
- Scenes are lit by a studio room generated in the browser (`lib/room.ts`, three.js `RoomEnvironment`; strength
  `settings.environment`, "Room light", default 0.4), so the metal of GLBs from picture-to-3D services reflects it
  instead of going black; it lights and never shows, and data passes don't see it. GLB library pictures use it too:
  Library > 3D models > Redraw pictures draws them all again (a collision mesh keeps a picture it has). A posted
  thumbnail gets a new file name each time, since `/media` is cached as immutable per key.
- Splats render with Spark (`components/three/Splat.tsx`): one `SparkLayer` per canvas, worlds flipped from OpenCV
  axes, `spark.update()` before each captured frame; data passes hide splats and draw the collision mesh instead.
- `worldlabs` (Marble, `WORLDLABS_API_KEY`): submit returns an operation, polled until `done`; it can't be canceled.
  Besides text and a picture, Marble takes several pictures (`multi_image_to_world`, `image_references`: up to 4, each
  facing a direction when `views_facing: around`, or up to 8 overlapping photos with Auto layout, `reconstruct_images`;
  a rule's `max_inputs` holds the 4) or a video of up to 30 s (`video_to_world`); `transforms/worlds.py:marble_views`
  wraps the pictures as World Labs' `{content, azimuth}`.
  The finished operation reports its credits (`cost.total_credits`, $1 = 1,250), which are charged; the world's
  files are read from the stored world, since the operation's copy can lack the panorama. Usage past the prepaid
  balance is billed to the card at month end: keep caps on.

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
