# Moving the studio to the home server

Plan, 2026-10-10. Nothing here is done yet.

## Where it runs today
Everything runs on the Windows PC: Postgres 18 (Docker, :5433), the API (:8400), the worker, the web app (:3000),
`data/media/` (1.7 GB) and the optional local ComfyUI (RTX 5070). Generation itself runs in the cloud (fal, World Labs,
ElevenLabs, Claude, RunPod), so the studio's own processes are light.

## The server (checked 2026-10-10)
| | |
|---|---|
| Machine | Fedora 44 laptop, Ryzen 5 5600H (12 threads), RTX 3060 Laptop 6 GB |
| Memory | 14 GB: about 4 GB used on 2026-10-10. Earlier notes say about 10 GB in normal use, and a model pull during an Ollama load caused an OOM on 2026-10-07 |
| Disk | 332 GB free of 476 GB |
| Conventions | Podman containers run by `systemd --user` units, each project on `127.0.0.1:<port>` with a `tailscale serve` HTTPS port (tailnet only), a `deploy.sh` per project, a card on the Hub (`~/server/hub/projects.toml`), docs in `~/server/README.md` and the Confluence SD "Home Server" pages |
| Free ports | local 8096 and up, HTTPS 19443 and up |

The code already runs on Linux: the Windows-only parts (SelectorEventLoop, ffmpeg through threads) check
`sys.platform`. Asset storage keys are relative to `MEDIA_ROOT`, so moving the media folder needs no database change.

## Layout on the server
| Piece | How | Port | Memory cap |
|---|---|---|---|
| Postgres 18 | container `ams-postgres`, data in a named volume | inside the pod only | 512 MB |
| API | container from one `ams-server` image (Python 3.12, uv, Debian ffmpeg with libass and x264, Postgres 18 client) | 127.0.0.1:8097 -> HTTPS 19444 | 768 MB |
| Worker | the same image, `python -m studio.worker` | none | 1.5 GB (ffmpeg assembly peaks) |
| Web | container `ams-web` (Next.js standalone build) | 127.0.0.1:8096 -> HTTPS 19443 | 512 MB |
| Data | the checkout's `data/` (media, backups, logs, reports), bind-mounted at `/app/data` in the API and worker | | |

All four run in one Podman pod (`ams`, Quadlet units like the server's ai-trader), so they share localhost.

- **Total ceiling about 3.3 GB**, so the server keeps room for Ollama and the rest.
- **Why a container rather than the host's ffmpeg:** Fedora's `ffmpeg-free` has no x264 encoder, and the studio's assembly
  and captions need x264 and libass.
- **Addresses:** the web app is `https://<server>.<tailnet>.ts.net:19443/` and the API is `:19444`. `NEXT_PUBLIC_API_URL`
  is baked in at build time; the API's `WEB_ORIGIN` is set to the web address for CORS. Both stay tailnet-only.
- **Secrets:** `.env` is copied once over SSH to the checkout (`~/Development/repos/ai-media-studio/.env`, mode 600)
  and passed with `--env-file`. It is never committed or put into an image.

## Code changes (one PR, small)
1. `infra/server/`: the two Containerfiles, the Quadlet units, the timers, `deploy.sh` and the README.
2. **Backups:** `studio/ops/backups.py` runs `docker compose exec postgres`. A `DB_EXEC` setting chooses the command.
   Unset keeps the PC's command; empty runs the Postgres 18 client in the worker container, which the server uses.
3. **Scheduled jobs:** systemd user timers on the server replace the Windows scheduled tasks (backup nightly at 03:30,
   schema check Mondays at 09:00). `schedule_tasks.ps1` stays for the PC.
4. **Web build:** `NEXT_OUTPUT=standalone` switches on Next's standalone output for the image only.
5. Docs: a "Running on the home server" section in `CLAUDE.md`.

## deploy.sh (the Hub's Deploy button)
Steps:
1. `git pull` in `~/Development/repos/ai-media-studio`.
2. `podman build` both images.
3. Run `alembic upgrade head` in a one-off container.
4. Restart the three units.
5. `podman image prune -f`.
6. Check the health URL.

Building Next.js takes 2-3 GB for a minute or two. The script waits (up to 10 minutes) until 3 GB is free and
Ollama has no model loaded, since a big pull during a model load caused the 2026-10-07 OOM.

## Moving the data (one sitting, about 30 minutes)
1. Stop the worker and API on the PC so nothing changes. Take a backup with `scripts/backup_db.py`.
2. Copy the dump and `data/media/` (1.7 GB) to the server with `scp`/`tar` over SSH.
3. Restore into the server's Postgres. Check with `restore_db.py --check` (row counts per table).
4. Start everything on the server. Check that the library shows pictures and plays clips, a mock generation completes,
   and the spend page matches the PC.
5. The PC keeps its copy until you say the server works.

## Local ComfyUI route (decision needed)
The `comfy_local` route reaches ComfyUI at `COMFYUI_URL`. Options:
- **PC over Tailscale (recommended):** the 5070 runs Wan 2.2 5B in about 1-1.5 minutes. It works when the PC is on and
  ComfyUI listens on its tailnet address; when it's off the route says "isn't running", as now.
- **The server's own VideoStudio ComfyUI (RTX 3060, 6 GB):** always on, but Wan 2.2 5B barely fits in 6 GB and would be
  several times slower.
- **Off:** the cloud routes cover the same models.

## After the move
- The PC stays the development machine: mock mode against its own local database, tests and Playwright there.
- The server is where you use the studio, with live keys, caps and backups.
- Updates go through a PR and merge, then Deploy on the Hub.
- Server docs updated in the same change: `~/server/README.md` ports table, the Hub card, the Confluence SD pages.

## What it costs
Nothing new. RunPod, fal and the others are reached from the server the same way.

## Verify
- Hub card shows healthy.
- Web on :19443 from the PC and a phone on the tailnet.
- Upload a clip, run a mock generation and a mock Faceless episode (ffmpeg assembly plus captions), and export a
  Cinema cut.
- Watch memory with `systemd-cgtop` during the assembly.
- Run the backup timer once by hand.
