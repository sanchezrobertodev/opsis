# Opsis on a Linux server (example deployment)

An example of running the studio on an always-on Linux server: rootless Podman with Quadlet units, systemd user
timers, and `tailscale serve` for HTTPS on your tailnet. Adapt the ports, memory caps and paths to your machine.
Generation runs in the cloud as it does on a desktop. Local models can stay on a GPU machine: point `COMFYUI_URL`
in the server's `.env` at that machine's ComfyUI (over Tailscale, for example).

| Piece | Container | Where | Memory cap |
|---|---|---|---|
| Database | `opsis-postgres` (Postgres 18, volume `ams-pgdata`) | inside the pod only | 512 MB |
| API | `opsis-api` (image `opsis-server`) | 127.0.0.1:8097 -> https://<host>.<tailnet>.ts.net:19444 | 768 MB |
| Worker | `opsis-worker` (image `opsis-server`) | none | 1.5 GB |
| Web app | `opsis-web` (image `opsis-web`, standalone Next.js) | 127.0.0.1:8096 -> https://<host>.<tailnet>.ts.net:19443 | 512 MB |
| Codex runner | `opsis-codex` (image `opsis-server`) | none, outside the pod | 1 GB |

- **Pod:** the first four run in the pod `opsis`, so they reach each other on localhost. The Codex runner doesn't
  (see [GPT Image through your ChatGPT plan](#gpt-image-through-your-chatgpt-plan)).
- **Files:** the units run from your checkout of this repository. Its `.env` (mode 600, never committed) holds the
  keys and modes, and its `data/` (media, backups, logs, reports) is bind-mounted into the API and worker at `/app/data`.
  Keys pasted in Settings are encrypted with `data/keys.secret` (or `KEYS_SECRET`); the off-site copy doesn't send it,
  so keep your own copy, or be ready to paste the keys again after a restore.
- **Access token:** put `ACCESS_TOKEN=` (see `.env.example`) in the `.env`, so the rest of the tailnet sees a sign-in
  instead of the studio. Each browser signs in once; MCP clients send it as a header (README, "Drive it from
  Claude"). `deploy.sh` warns while it's unset.
- **Settings the units fill in:** the database address, `WEB_ORIGIN` (CORS) and `DB_EXEC=` (backups run the
  Postgres 18 client in the worker container). These win over the `.env`.
- **Building:** `opsis-server` is Python 3.12, uv, Debian's ffmpeg (x264, libass) and the PGDG Postgres 18 client.
  Caption fonts are open stand-ins mapped in `fonts.conf`, so Georgia, Impact and Trebuchet captions look a little
  different than on Windows.
- **Web address:** the browser calls the API at the address compiled into `opsis-web`
  (`NEXT_PUBLIC_API_URL`), so moving the API to another address needs a rebuild (`deploy.sh` does it).

## Deploy
`infra/server/deploy.sh` (run it from anywhere, or from a dashboard button) does the following:
1. Pulls the code.
2. Waits for memory: it needs 3 GB free, and no Ollama model loading if Ollama runs on the same host
   (`OLLAMA_URL` in `.env`, default `http://127.0.0.1:11434`).
3. Builds both images.
4. Installs the Quadlet units and timers. A server set up before the rename to Opsis ran them as `ams-*` in the pod
   `ams`: the first deploy stops and removes those and their images, and the database keeps its volume.
5. Migrates the database.
6. Restarts the Codex runner, API, worker and web app.
7. Sets the two `tailscale serve` ports once.
8. Checks health.

## Timers
- `opsis-backup.timer`: nightly at 03:30, `scripts/backup_db.py` in the worker container. Dumps go to
  `data/backups/` (14 days kept) and the log to `data/logs/backup.log`.
- `opsis-schema-check.timer`: Mondays at 09:00, `scripts/fetch_provider_schemas.py`. The report (schemas, prices,
  estimates against bills) goes to `data/reports/`.

- `opsis-offsite.timer`: hourly, `offsite.sh`. It sends what's new to another device with Taildrop
  (`OPSIS_OFFSITE_TARGET` in `.env`: that device's Tailscale name; empty turns it off).
  - **What it sends:** media written since the last run, as one `opsis-media-<time>.tar`; and each new dump, as
    `opsis-studio-<time>.dump`.
  - **A failed send** waits in `data/offsite/outbox/` and is tried again next hour. Once anything has waited 48 h, the
    unit fails, so a monitor that watches failed units shows it. The log is `data/logs/offsite.log`.

Run any of them by hand: `systemctl --user start opsis-backup.service`.

## Off-site copy on a Windows PC
- **Setup, once:** on the PC that receives the Taildrop files, run
  `powershell -ExecutionPolicy Bypass -File infra\server\windows\install-backup-task.ps1 -Dest <backup folder>`.
  It registers the task that files the backups away while you're signed in.
- **What the task does:** it runs `windows\collect-backups.ps1`, which takes what Taildrop left in Downloads.
  - **Media bundles:** each is checked to read to the end, then extracted into `<backup folder>\media`, a mirror of
    `data/media` that never deletes anything.
  - **Dumps:** they go to `<backup folder>\db`, and the last 60 days are kept.
  - **Log:** `<backup folder>\collect.log`.
- **To restore:** copy the mirror back over the server's `data/media` and put a dump in `data/backups`. Then, in the
  checkout, with the API and worker stopped (`systemctl --user stop opsis-web opsis-worker opsis-api`), run:
  ```
  podman run --rm --pod opsis -e DATABASE_URL=postgresql+asyncpg://studio:studio@127.0.0.1:5432/studio \
    -e PGHOST=127.0.0.1 -e PGPASSWORD=studio -e DB_EXEC= -v "$PWD/data:/app/data:z" localhost/opsis-server:latest \
    python scripts/restore_db.py --replace --file data/backups/<dump> --yes
  ```
  Then start the API, worker and web app again.

## GPT Image through your ChatGPT plan
With `CODEX_BIN` set in `.env` (the standalone install: `/home/<you>/.codex/packages/standalone/current/bin/codex`,
the only path the runner can see), GPT Image draws from your ChatGPT plan through the Codex CLI. The runner's unit takes
it from `.env` when `deploy.sh` installs the units, so after setting or changing it run `NO_PULL=1 infra/server/deploy.sh`
rather than only restarting. Codex can read files whatever it's told, so it never runs
in the worker: the worker leaves each job in `data/provider_results/codex/` and `opsis-codex` draws it.
- **What it sees:** those jobs, its own Codex home `~/.local/share/opsis/codex-home` (its sign-in and sessions) and
  `~/.codex/packages` (the binary), read-only. Not your `~/.codex` with its sessions, plugins and MCP servers, nor
  `.env`, the keys, media, backups or the database. It runs read-only with no capabilities, outside the pod.
- **Sign it in once**, with the ChatGPT account whose plan should pay (it prints a link and a code to open on any
  device):
  ```
  podman exec -it opsis-codex /home/<you>/.codex/packages/standalone/current/bin/codex login --device-auth
  ```
  Until it's signed in with ChatGPT (an API key would bill per picture, outside the studio's caps), Settings says so
  and GPT Image goes to fal.
- **Checks:** Settings > Provider balances shows Codex as Ready. `journalctl --user -u opsis-codex` logs each job it
  draws.

## Your own models and workflows

Put them in the checkout's `data/registry/` (model, preset and template YAML, laid out like `registry/`) and
`data/workflows/comfy/` (ComfyUI graphs). They're in the bind-mounted `data/`, so the API and the worker load them
within a second, with no rebuild or deploy, and a `git pull` never conflicts with them. `scripts/import_comfy_workflow.py`
writes there, and the README's "Add a hosted model" walks through a hosted one. Errors in them show in `/health`
while the studio runs on the built-ins. The off-site copy doesn't send these folders: keep your own copy.

## Day to day
```
systemctl --user status opsis-api opsis-worker opsis-web opsis-postgres opsis-codex
journalctl --user -u opsis-worker -f
podman exec opsis-worker python scripts/restore_db.py --check     # restore the newest dump into a scratch copy
systemd-cgtop                                                    # memory per container during a render
```
