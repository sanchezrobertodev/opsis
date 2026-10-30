# Opsis on a Linux server (example deployment)

An example of running the studio on an always-on Linux server: rootless Podman with Quadlet units, systemd user
timers, and `tailscale serve` for HTTPS on your tailnet. Adapt the ports, memory caps and paths to your machine.
Generation runs in the cloud as it does on a desktop. Local models can stay on a GPU machine: point `COMFYUI_URL`
in the server's `.env` at that machine's ComfyUI (over Tailscale, for example).

| Piece | Container | Where | Memory cap |
|---|---|---|---|
| Database | `ams-postgres` (Postgres 18, volume `ams-pgdata`) | inside the pod only | 512 MB |
| API | `ams-api` (image `ams-server`) | 127.0.0.1:8097 -> https://<host>.<tailnet>.ts.net:19444 | 768 MB |
| Worker | `ams-worker` (image `ams-server`) | none | 1.5 GB |
| Web app | `ams-web` (image `ams-web`, standalone Next.js) | 127.0.0.1:8096 -> https://<host>.<tailnet>.ts.net:19443 | 512 MB |

- **Pod:** all four containers run in the pod `ams`, so they reach each other on localhost.
- **Files:** the units run from your checkout of this repository. Its `.env` (mode 600, never committed) holds the
  keys and modes, and its `data/` (media, backups, logs, reports) is bind-mounted into the API and worker at `/app/data`.
- **Settings the units fill in:** the database address, `WEB_ORIGIN` (CORS) and `DB_EXEC=` (backups run the
  Postgres 18 client in the worker container). These win over the `.env`.
- **Building:** `ams-server` is Python 3.12, uv, Debian's ffmpeg (x264, libass) and the PGDG Postgres 18 client.
  Caption fonts are open stand-ins mapped in `fonts.conf`, so Georgia, Impact and Trebuchet captions look a little
  different than on Windows.
- **Web address:** the browser calls the API at the address compiled into `ams-web`
  (`NEXT_PUBLIC_API_URL`), so moving the API to another address needs a rebuild (`deploy.sh` does it).

## Deploy
`infra/server/deploy.sh` (run it from anywhere, or from a dashboard button) does the following:
1. Pulls the code.
2. Waits for memory: it needs 3 GB free, and no Ollama model loading if Ollama runs on the same host
   (`OLLAMA_URL` in `.env`, default `http://127.0.0.1:11434`).
3. Builds both images.
4. Installs the Quadlet units and timers.
5. Migrates the database.
6. Restarts the API, worker and web app.
7. Sets the two `tailscale serve` ports once.
8. Checks health.

## Timers
- `ams-backup.timer`: nightly at 03:30, `scripts/backup_db.py` in the worker container. Dumps go to
  `data/backups/` (14 days kept) and the log to `data/logs/backup.log`.
- `ams-schema-check.timer`: Mondays at 09:00, `scripts/fetch_provider_schemas.py`. The report goes to `data/reports/`.

- `ams-offsite.timer`: hourly, `offsite.sh`. It sends what's new to another device with Taildrop
  (`AMS_OFFSITE_TARGET` in `.env`: that device's Tailscale name; empty turns it off).
  - **What it sends:** media written since the last run, as one `ams-media-<time>.tar`; and each new dump, as
    `ams-studio-<time>.dump`.
  - **A failed send** waits in `data/offsite/outbox/` and is tried again next hour. Once anything has waited 48 h, the
    unit fails, so a monitor that watches failed units shows it. The log is `data/logs/offsite.log`.

Run any of them by hand: `systemctl --user start ams-backup.service`.

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
  checkout, with the API and worker stopped (`systemctl --user stop ams-web ams-worker ams-api`), run:
  ```
  podman run --rm --pod ams -e DATABASE_URL=postgresql+asyncpg://studio:studio@127.0.0.1:5432/studio \
    -e PGHOST=127.0.0.1 -e PGPASSWORD=studio -e DB_EXEC= -v "$PWD/data:/app/data:z" localhost/ams-server:latest \
    python scripts/restore_db.py --replace --file data/backups/<dump> --yes
  ```
  Then start the API, worker and web app again.

## Day to day
```
systemctl --user status ams-api ams-worker ams-web ams-postgres
journalctl --user -u ams-worker -f
podman exec ams-worker python scripts/restore_db.py --check     # restore the newest dump into a scratch copy
systemd-cgtop                                                    # memory per container during a render
```
