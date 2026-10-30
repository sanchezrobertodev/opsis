#!/usr/bin/env bash
# Deploys Opsis on a Linux server (rootless Podman, Quadlet; see infra/server/README.md).
# Run from anywhere: <checkout>/infra/server/deploy.sh   (or from a dashboard's deploy button)
#   NO_PULL=1   deploy what's checked out without `git pull`
#   FORCE=1     build even when memory is short (see wait_for_memory)
set -euo pipefail
APP_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)"
HERE="$APP_DIR/infra/server"
UNIT_DIR="$HOME/.config/containers/systemd"
USER_UNIT_DIR="$HOME/.config/systemd/user"
WEB_PORT="${WEB_PORT:-8096}"
API_PORT="${API_PORT:-8097}"
WEB_HTTPS="${WEB_HTTPS:-19443}"
API_HTTPS="${API_HTTPS:-19444}"
cd "$APP_DIR"

host="${TS_HOST:-$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')}"
WEB_ORIGIN="https://$host:$WEB_HTTPS"
API_URL="https://$host:$API_HTTPS"

if [ ! -f .env ]; then
  echo "no .env in $APP_DIR: copy the studio's .env here (chmod 600) and run again"; exit 1
fi
chmod 600 .env
mkdir -p data/media data/backups data/logs data/reports "$HOME/.codex"  # the worker mounts ~/.codex

if [ "${NO_PULL:-0}" != 1 ] && [ -d .git ]; then
  before="$(git rev-parse HEAD)"
  git pull --ff-only
  if [ "$(git rev-parse HEAD)" != "$before" ]; then
    # bash keeps executing the copy of this script it already opened: run the new one
    exec env NO_PULL=1 "$HERE/deploy.sh" "$@"
  fi
fi

# Building the web app takes 2-3 GB for a minute or two. On a small server, a big pull while Ollama loads a model can
# run it out of memory, so wait (up to 10 minutes) for 3 GB free and no model loading. OLLAMA_URL (environment or
# .env) is where an Ollama on this host answers; nothing answering there counts as no model loading.
OLLAMA_URL="${OLLAMA_URL:-$(sed -n 's/^OLLAMA_URL=//p' .env | tail -1)}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
wait_for_memory() {
  for _ in $(seq 1 60); do
    avail_kb="$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
    loading="$(curl -fsS --max-time 3 "$OLLAMA_URL/api/ps" 2>/dev/null | grep -c '"expires_at"' || true)"
    if [ "$avail_kb" -ge 3000000 ] && [ "${loading:-0}" = 0 ]; then return 0; fi
    echo "==> waiting: $((avail_kb / 1024)) MB free, Ollama models loaded: ${loading:-0}"
    sleep 10
  done
  [ "${FORCE:-0}" = 1 ] && return 0
  echo "still short of memory after 10 minutes; stop something or run with FORCE=1"; exit 1
}
wait_for_memory

echo "==> building images (API address $API_URL)"
podman build -q -t localhost/ams-server:latest -f "$HERE/Containerfile.server" --ignorefile "$HERE/.containerignore" .
podman build -q -t localhost/ams-web:latest -f "$HERE/Containerfile.web" --ignorefile "$HERE/.containerignore" \
  --build-arg NEXT_PUBLIC_API_URL="$API_URL" .

echo "==> installing units"
mkdir -p "$UNIT_DIR" "$USER_UNIT_DIR"
fill() { sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@WEB_PORT@|$WEB_PORT|g" -e "s|@API_PORT@|$API_PORT|g" \
             -e "s|@WEB_ORIGIN@|$WEB_ORIGIN|g" "$1"; }
for f in "$HERE"/quadlet/*; do fill "$f" > "$UNIT_DIR/$(basename "$f")"; done
for f in "$HERE"/systemd/*; do fill "$f" > "$USER_UNIT_DIR/$(basename "$f")"; done
systemctl --user daemon-reload

echo "==> database"
systemctl --user start ams-pod.service ams-postgres.service  # waits until Postgres answers
podman run --rm --pod ams --env-file .env \
  -e DATABASE_URL=postgresql+asyncpg://studio:studio@127.0.0.1:5432/studio \
  -w /app/apps/server localhost/ams-server:latest alembic upgrade head

echo "==> restarting the API, worker and web app"
systemctl --user restart ams-api.service ams-worker.service ams-web.service
systemctl --user enable --now ams-backup.timer ams-schema-check.timer ams-offsite.timer >/dev/null

# HTTPS on the tailnet (tailnet only). Set on every deploy: it replaces the port's mapping, so a changed local port
# is picked up too.
tailscale serve --bg --https="$WEB_HTTPS" "http://127.0.0.1:$WEB_PORT" >/dev/null
tailscale serve --bg --https="$API_HTTPS" "http://127.0.0.1:$API_PORT" >/dev/null

health="http://127.0.0.1:$API_PORT/health"
echo -n "==> waiting for $health"
for _ in $(seq 1 90); do
  if curl -fsS "$health" >/dev/null 2>&1 && curl -fsS -o /dev/null "http://127.0.0.1:$WEB_PORT/"; then echo " ok"; break; fi
  echo -n "."; sleep 2
done
curl -fsS "$health" >/dev/null || { echo; echo "API not healthy: journalctl --user -u ams-api -e"; exit 1; }
curl -fsS -o /dev/null "http://127.0.0.1:$WEB_PORT/" || { echo; echo "web not up: journalctl --user -u ams-web -e"; exit 1; }

podman image prune -f >/dev/null
echo "Studio: $WEB_ORIGIN  (API $API_URL)"
