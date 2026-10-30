# Opsis

An open-source, self-hosted AI video studio in the spirit of Higgsfield. Write a prompt, pick a model and a few
meaningful controls, watch the job progress, and get the clip in your library, without wiring a node graph by
hand. Behind the forms, the studio runs real ComfyUI graphs on your own GPU (or on RunPod) and, when you add
keys, hosted models from fal.ai, Google, Runway, ElevenLabs and others.

[![Opsis launch film, 1:58. Click to play.](docs/demo/opsis-launch.jpg)](docs/demo/opsis-launch.mp4)

**[Watch the launch film (1:58)](docs/demo/opsis-launch.mp4).** Everything generated in it (a documentary, a vlog
and a short film, the characters and worlds, the model montage, the voices and the score) was made in Opsis; the rest
is screen recordings of the app and of the ComfyUI graph behind an open model (LTX-2.5 on your own RunPod), plus a
few stock sound effects (see [NOTICE](NOTICE)).

![Create video with Wan 2.2 5B running on a local GPU: the finished clip next to the form](docs/screenshots/finished.png)

```mermaid
flowchart LR
  UI["Web app<br/>Next.js"] -->|REST + live events| API["API<br/>FastAPI"]
  API --> DB[("Postgres<br/>jobs, library, ledger")]
  DB --> W["Worker<br/>procrastinate"]
  W -->|"fills a workflow template<br/>/prompt, /history, /view"| C["ComfyUI<br/>your GPU or RunPod"]
  W -->|"API keys (optional)"| P["Hosted models"]
  W -->|"download + ffmpeg"| M["data/media/<br/>your library"]
  M --> UI
```

## What it does

| Studio | What you get |
|---|---|
| **Create video and image** | Text or picture to video and image, across local, RunPod and hosted models, with a camera and look director |
| **Edit video, Motion control, Motion transfer** | Restyle a clip, draw motion paths on the first frame, copy a performance onto a character |
| **Object swap, Face swap** | Click an object, track it through the clip, replace it; swap a face in a picture or a clip |
| **Characters** | Saved characters you `@mention` in any prompt; reference sheets; trained LoRA identities |
| **Faceless Studio** | Topic to finished narrated short: script review, shots, voice, music, captions, thumbnail |
| **Cinema Studio** | Script to storyboard to takes to a timed cut, with an AI director chat and dialogue lip-sync |
| **3D blocking and Worlds** | Block a scene with 3D models and Gaussian-splat worlds, render passes, re-render them as live action |
| **Lipsync, voices, music, dubbing** | Talking shots, cloned voices, songs, dubbing in other languages |
| **Templates** | One-form recipes: music videos, character vlogs, creature documentaries, effects |

![Templates: guided recipes such as a bedtime story, a creature documentary or a dubbed video](docs/screenshots/templates.png)

Every model is a YAML file in [`registry/`](registry/): its modes, parameters, prices and routes (which provider
runs it). Adding a model, a preset or a template is mostly writing YAML. See [CLAUDE.md](CLAUDE.md) for the
conventions.

## Quick start (mock mode, no GPU, no keys)

Requirements (Windows, macOS or Linux):

- [Docker](https://docs.docker.com/get-docker/) running, for Postgres
- Node 22 or newer with pnpm 9 (`corepack enable` sets up the pinned pnpm)
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you)
- [ffmpeg](https://ffmpeg.org/download.html) on your PATH (`ffmpeg -version` should work)

1. Get the code and make your settings file:

   ```bash
   git clone https://github.com/sanchezrobertodev/ai-media-studio.git
   cd ai-media-studio
   cp .env.example .env
   ```

2. Start Postgres (in Docker, on port 5433) and install everything:

   ```bash
   pnpm db:up
   uv sync
   pnpm install
   pnpm db:migrate
   ```

3. Start the API (:8400), the worker and the web app (:3000):

   ```bash
   pnpm dev
   ```

Open http://localhost:3000. With the default `PROVIDER_MODE=mock`, every model answers with a labelled test clip,
so you can try every studio for free. Settings shows whether the database, ffmpeg and the local engine are found.

## Run a real video on your GPU (ComfyUI + Wan 2.2 5B)

This is the fully open path: Wan 2.2 TI2V 5B (Apache-2.0) in your own ComfyUI. It is free and works offline.

**Hardware:** an NVIDIA GPU with 12 GB of VRAM or more. The workflow uses a tiled VAE decode to fit in 12 GB.

1. Install [ComfyUI](https://github.com/comfyanonymous/ComfyUI) (portable or manual install; tested with
   ComfyUI 0.34). The workflow uses only built-in nodes, so no custom nodes are needed.
2. Download the three model files into ComfyUI's `models/` folder:

   | File | Folder | Source |
   |---|---|---|
   | `wan2.2_ti2v_5B_fp16.safetensors` | `models/diffusion_models/` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/tree/main/split_files/diffusion_models) |
   | `wan2.2_vae.safetensors` | `models/vae/` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/tree/main/split_files/vae) |
   | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `models/text_encoders/` | [Comfy-Org/Wan_2.1_ComfyUI_repackaged](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/tree/main/split_files/text_encoders) |

3. Start ComfyUI on its default port (8188). `.env` already points there: `COMFYUI_URL=http://127.0.0.1:8188`.
4. Restart `pnpm dev`, open **Create video**, pick **Wan 2.2 5B (local)**, and generate. Local routes run in
   every mode, mock included, and cost nothing.

Sample prompt: *A small red sailboat drifts on a calm alpine lake at sunrise, gentle ripples, pine forest.*

Draft quality (768×448, 3 s) takes about 70 seconds on a recent 12 GB card; standard (1280×704, 5 s) takes about
seven minutes. The first job after ComfyUI starts also loads the model (about 40 seconds more). Progress shows in
the form and on the job card; the clip lands in the **Library**, where it plays and downloads. A queued or running
job can be canceled from its card.

| Rendering on your GPU | In the library |
|---|---|
| ![A take rendering locally, 16% done](docs/screenshots/progress.png) | ![The library with local Wan 2.2 5B clips](docs/screenshots/library.png) |

What happens: the worker fills [`workflows/comfy/wan22_ti2v_5b.json`](workflows/comfy/wan22_ti2v_5b.json)
using its injection map ([`.map.yaml`](workflows/comfy/wan22_ti2v_5b.map.yaml): prompt, seed, size, length,
start frame), queues it on ComfyUI's `/prompt`, follows `/history`, downloads the output through `/view`, and
stores it in `data/media/`. The same templates run on RunPod serverless ([`infra/runpod/wan-core`](infra/runpod/wan-core))
for the larger graphs: LTX-2.5, Wan 2.2 14B, VACE, Animate, Uni3C and Wan-Move.

### Troubleshooting

- **"Local ComfyUI isn't running"**: ComfyUI isn't reachable at `COMFYUI_URL`. Start it, or check the port.
- **The job fails with a missing model**: a file is missing or in the wrong folder; compare with the table above.
- **Out of memory**: use Draft quality, or close other GPU apps. One local job runs at a time.
- **The API won't start on Windows**: run it as `python -m studio.api` (what `pnpm dev` does), not bare uvicorn.

## Hosted models and spending

1. Add the keys you have to `.env`. [`.env.example`](.env.example) says where to get each one; you don't need
   them all, and a model whose provider has no key is simply unavailable.
2. Raise `PROVIDER_MODE` in `.env` to `mixed` or `live` (and `LLM_MODE` to `live` for Claude). It is the ceiling:
   the switch in Settings can pick that mode or a safer one, never more.
3. Restart `pnpm dev`, then set your daily and monthly limits in Settings > Spending and pick the mode in
   Settings > Providers.

Every request is priced before it runs and checked against your limits; spending is recorded per request and
reconciled against provider bills where providers report them. Mock mode never calls a paid route. To try one
paid route at a time, use `PROVIDER_MODE=mixed` with that route in `LIVE_ROUTE_ALLOWLIST`
(for example `LIVE_ROUTE_ALLOWLIST=kling-3.0@fal`).

The price line under Takes names the provider a take will run on (for example "$0.04 on fal.ai"), and Settings'
spend history shows the route each charge ran on.

### GPT Image through your ChatGPT plan (optional)

GPT Image 2.5 can draw from a ChatGPT plan's allowance through the [Codex CLI](https://developers.openai.com/codex/cli)
instead of being billed per picture on fal.

1. Install the Codex CLI and sign in with your ChatGPT account: `codex login`. `codex login status` should say
   "Logged in using ChatGPT" (an API-key sign-in bills per picture, so it defeats the purpose).
2. Set `CODEX_BIN` in `.env` to the full path of the `codex` program (`where codex` on Windows, `which codex`
   elsewhere), then restart the API and the worker.
3. Settings > Provider balances shows **Codex (your ChatGPT plan): Ready** once it runs and is signed in. The
   model picker then shows GPT Image 2.5 as Free, and the price line reads "on Codex (your ChatGPT plan)".

It follows the Mock / Mixed / Live switch like any provider. It's slow (about 90 s a picture), and Codex picks the
size, so pictures are cropped to the shape you asked for. A take Codex can't draw (the plan's usage limit, a
refusal) moves on to fal and is billed there. Check OpenAI's terms for this use of a plan. On the Linux server
setup (`infra/server/`) the API and worker mount the host's `~/.codex`; point `CODEX_BIN` at
`/home/<you>/.codex/packages/standalone/current/bin/codex`.

## Limitations

- **Single user.** There is no sign-in; run it on your own machine or a private network (Tailscale, for
  example), never on the open internet.
- **Local media.** Files are stored on disk under `data/media/`; back that folder up yourself. The database has
  its own tools: `uv run python scripts/backup_db.py` dumps it to `data/backups/`,
  `uv run python scripts/restore_db.py --check` test-restores the newest dump, and on Windows
  `scripts/schedule_tasks.ps1` runs the backup nightly.
- **Prices go stale.** Provider prices in `registry/` were verified on the dates written next to them.
- **Hosted models need your own accounts** and cost money; the local Wan route is the only one that is free and
  open end to end.

## Development

```bash
pnpm test
pnpm lint
pnpm e2e
```

`pnpm test` runs the Python unit, registry-contract and database integration tests (it needs `pnpm db:up` and
uses a separate `studio_test` database). `pnpm e2e` runs Playwright against the mock provider; install its
browser once with `pnpm --filter @studio/web exec playwright install chromium`. It reuses a running `pnpm dev`,
so stop a dev server that is in Live mode first. After changing the API, run `pnpm gen` to refresh the TypeScript
client.

More reading:

- [`docs/PLAN.md`](docs/PLAN.md): the roadmap. [`docs/research/06-architecture-blueprint.md`](docs/research/06-architecture-blueprint.md):
  the detailed design.
- [`infra/server/`](infra/server/): an example deployment on a Linux server (rootless Podman, systemd timers,
  nightly backups).
- [`infra/runpod/wan-core/`](infra/runpod/wan-core/): your own RunPod endpoint for the larger open models.
- [`docs/local-model-storage.md`](docs/local-model-storage.md): choosing ComfyUI's model folder from Settings.
- [`docs/pika.md`](docs/pika.md) and [`docs/reactor.md`](docs/reactor.md): two optional providers.

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) for third-party terms: Remotion's license, model licenses and how
ComfyUI is used.
