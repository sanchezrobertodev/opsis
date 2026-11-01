# Setting up Opsis: a runbook for AI agents

You are setting up Opsis, a self-hosted AI video studio, for a person. Follow the steps in order and run each
check before moving on. The result is a studio running in **mock mode**: every feature works with stand-in
results and nothing is billed. Spending real money is the person's decision, never yours (see *Stop and ask*).

Working on the code rather than installing it? Read [`CLAUDE.md`](CLAUDE.md): the stack, the rules and how to
extend the studio. The person-facing guide is [`README.md`](README.md).

## Stop and ask

Never do these yourself. Explain the step and let the person do it.

- **Keys.** Never paste, invent, print or commit API keys. The person pastes them in **Settings > Keys** (or puts
  them in `.env`); each key there links to where it comes from. Don't read `.env` back to them.
- **Money.** Never raise `PROVIDER_MODE` or `LLM_MODE` above `mock`, fill `LIVE_ROUTE_ALLOWLIST`, or press Generate
  (or start a run) while the studio is in Mixed or Live mode.
- **Models and GPUs.** Don't download local model weights, install ComfyUI or create cloud endpoints unless asked.
- **Other services.** If a port is taken, find out what holds it and ask before stopping anything you didn't start.

## 1. Check the tools

| Tool | Check | Need |
| --- | --- | --- |
| Docker | `docker info` succeeds | Running (on Windows and macOS, start Docker Desktop first) |
| Node | `node --version` | 22 or newer |
| pnpm | `pnpm --version` | 9 (`corepack enable` installs the pinned one) |
| uv | `uv --version` | Any recent; it installs Python 3.12 itself |
| ffmpeg | `ffmpeg -version` | On the PATH |

Only Docker? Use the containers path (step 2b) and skip the others.

## 2a. Native setup (recommended for development)

From the repository root:

```bash
cp .env.example .env     # only if .env doesn't exist: never overwrite one
pnpm db:up               # Postgres 18 in Docker, on 127.0.0.1:5433
uv sync
pnpm install
pnpm db:migrate
pnpm dev                 # API :8400, worker, web :3000; keeps running
```

Run `pnpm dev` in the background (it doesn't exit). Checks:

- After `pnpm db:up`: it waits until Postgres is healthy; `docker ps` lists `opsis-postgres-1`.
- After `pnpm db:migrate`: it ends without an error.
- After `pnpm dev`: `curl http://127.0.0.1:8400/health` returns `"status":"ok"`, `"database":true`,
  `"ffmpeg":true` and `"provider_mode":"mock"`. The web app answers on http://localhost:3000 (it redirects to
  `/create/video`).

## 2b. Containers only

```bash
cp .env.example .env     # only if .env doesn't exist
docker compose up --build -d
```

It builds the API and web images, runs the migrations, then starts everything. Check with `docker compose ps`
(every service healthy or running) and the same `/health` and web checks as above. The containers find a ComfyUI on
the host at `host.docker.internal:8188`.

Don't run 2a and 2b at once: they use the same ports and database.

## 3. Hand over

Tell the person:

- Open http://localhost:3000. **Settings** shows the database, ffmpeg, the local engine and the mode.
- Everything runs on the mock provider until they add keys (**Settings > Keys**) and raise the mode; `README.md` > *Hosted models and
  spending* has the steps, which are theirs to do.
- Optional, on request: open models on their own GPU (`README.md` > *Open models on your GPU*), and driving the
  studio from Claude over MCP (`README.md` > *Drive it from Claude*).

## Ports

| Port | What | Change it with |
| --- | --- | --- |
| 3000 | Web app | `next dev --port` in `apps/web/package.json`, or `OPSIS_WEB_PORT` for containers; also `WEB_ORIGIN` in `.env` |
| 8400 | API (and MCP at `/mcp`) | `API_PORT` in `.env`, plus `NEXT_PUBLIC_API_URL=http://127.0.0.1:<port>` in `apps/web/.env.local`; `OPSIS_API_PORT` for containers |
| 5433 | Postgres (5432 in the container) | `OPSIS_DB_PORT`, then the port in `DATABASE_URL` and `QUEUE_DATABASE_URL` |
| 8188 | ComfyUI, optional | `COMFYUI_URL` in `.env` |

They avoid the common defaults on purpose: 5433 so a Postgres already installed on 5432 keeps working, 8400 because
8000 is often taken. Everything binds `127.0.0.1` only.

## When something fails

| Symptom | Cause | Fix |
| --- | --- | --- |
| `pnpm db:up`: cannot connect to the Docker daemon | Docker isn't running | Start Docker (Desktop), wait for `docker info`, retry |
| Port 5433, 8400 or 3000 already in use | Another program, or an earlier `pnpm dev` | Find it (`netstat -ano` on Windows, `lsof -i :<port>` elsewhere). Stop it only if it's this studio and you started it; otherwise ask, or move the port (*Ports*) |
| `/health` doesn't answer, but the port is taken | A stale API process from an earlier run | Same as above. A healthy API answers within a second |
| `"ffmpeg": false` | ffmpeg isn't on the PATH of the shell that started `pnpm dev` | Install it, open a new shell, restart `pnpm dev` |
| `pnpm db:migrate`: connection refused | Postgres not up yet, or `DATABASE_URL` points elsewhere | Rerun `pnpm db:up`; check the port in `.env` |
| Windows: the API fails with event-loop errors | Started with bare `uvicorn` | Start it as `python -m studio.api` (what `pnpm dev` does) |
| Settings: local engine off or not running | No ComfyUI at `COMFYUI_URL` | Expected without a GPU setup; everything else works |

## Checks that cost nothing

- `pnpm test`: the server tests (needs Postgres up; uses its own `studio_test` database). Run it with
  `PROVIDER_MODE=mock LLM_MODE=mock` set in the environment, so a `.env` in live mode can't affect it.
- `pnpm lint`: ruff, eslint and tsc.
- `pnpm e2e`: Playwright against a running `pnpm dev` in mock mode.
