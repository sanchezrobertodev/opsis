# AI Media Studio on the home server

The studio runs on the Fedora home server (rootless Podman with Quadlet, like the server's other projects).
Generation runs in the cloud as on the PC. The local models stay on the PC: the server reaches its ComfyUI over
Tailscale (`COMFYUI_URL`). The plan and the reasons behind it are in `docs/home-server-move.md`.

| Piece | Container | Where | Memory cap |
|---|---|---|---|
| Database | `ams-postgres` (Postgres 18, volume `ams-pgdata`) | inside the pod only | 512 MB |
| API | `ams-api` (image `ams-server`) | 127.0.0.1:8097 -> https://fedora.tail36d2f8.ts.net:19444 | 768 MB |
| Worker | `ams-worker` (image `ams-server`) | none | 1.5 GB |
| Web app | `ams-web` (image `ams-web`, standalone Next.js) | 127.0.0.1:8096 -> https://fedora.tail36d2f8.ts.net:19443 | 512 MB |

- **Pod:** all four containers run in the pod `ams`, so they reach each other on localhost.
- **Files:** the checkout is `~/Development/repos/ai-media-studio`. Its `.env` (mode 600, never committed) holds the
  keys and modes, and its `data/` (media, backups, logs, reports) is bind-mounted into the API and worker at `/app/data`.
- **Settings the units fill in:** the database address, `WEB_ORIGIN` (CORS) and `DB_EXEC=` (backups run the
  Postgres 18 client in the worker container). These win over the `.env`.
- **Building:** `ams-server` is Python 3.12, uv, Debian's ffmpeg (x264, libass) and the PGDG Postgres 18 client.
  Caption fonts are open stand-ins mapped in `fonts.conf`, so Georgia, Impact and Trebuchet captions look a little
  different than on the PC.
- **Web address:** the browser calls the API at the address compiled into `ams-web`
  (`NEXT_PUBLIC_API_URL`), so moving the API to another address needs a rebuild (`deploy.sh` does it).

## Deploy
`infra/server/deploy.sh` (the Hub's Deploy button) does the following:
1. Pulls the code.
2. Waits for memory: it needs 3 GB free and no Ollama model loaded.
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

Run either one by hand: `systemctl --user start ams-backup.service`.

## Day to day
```
systemctl --user status ams-api ams-worker ams-web ams-postgres
journalctl --user -u ams-worker -f
podman exec ams-worker python scripts/restore_db.py --check     # restore the newest dump into a scratch copy
systemd-cgtop                                                    # memory per container during a render
```
