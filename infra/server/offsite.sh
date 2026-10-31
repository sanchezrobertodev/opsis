#!/usr/bin/env bash
# Off-site copy of the studio's media and database dumps, run hourly by opsis-offsite.timer (see README.md).
# Media added or changed since the last run goes out as one tar (paths start with media/), new dumps as they are,
# both with Taildrop to OPSIS_OFFSITE_TARGET (a device on the tailnet, set in .env; empty turns this off; the name
# before the rename to Opsis, AMS_OFFSITE_TARGET, works too). On a
# Windows PC, windows/collect-backups.ps1 files them away. A send that fails waits in data/offsite/outbox/ and is
# tried again next hour; once anything has waited OFFSITE_STALE_HOURS (48) the unit fails, so a failed-unit monitor shows it.
set -euo pipefail
shopt -s nullglob
APP_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)"
DATA="$APP_DIR/data"
STATE="$DATA/offsite"
OUTBOX="$STATE/outbox"
STALE_HOURS="${OFFSITE_STALE_HOURS:-48}"

target="$(sed -n -E 's/^(OPSIS|AMS)_OFFSITE_TARGET=//p' "$APP_DIR/.env" 2>/dev/null | tail -1)"
if [ -z "$target" ]; then
  echo "off-site copy off: OPSIS_OFFSITE_TARGET is empty in .env"
  exit 0
fi
mkdir -p "$OUTBOX"
rm -f "$OUTBOX"/*.part  # a bundle an earlier run didn't finish; its files are still past the old mark
list="$(mktemp)"
err="$(mktemp)"
trap 'rm -f "$list" "$err"' EXIT

# Media written since the last bundle. The next mark is set before looking, so a file written while this runs
# goes out next time; the first run takes everything. media/tmp is scratch space (uploads and renders in progress).
mark="$STATE/media.mark"
next="$STATE/media.mark.next"
touch "$next"
if [ -f "$mark" ]; then
  (cd "$DATA" && find media -path media/tmp -prune -o -type f -newer "$mark" ! -newer "$next" -print0) > "$list"
else
  (cd "$DATA" && find media -path media/tmp -prune -o -type f ! -newer "$next" -print0) > "$list"
fi
if [ -s "$list" ]; then
  bundle="$OUTBOX/opsis-media-$(date +%Y%m%d-%H%M%S).tar"
  tar -C "$DATA" --null -T "$list" -cf "$bundle.part"
  mv "$bundle.part" "$bundle"
  echo "bundled $(tr -cd '\0' < "$list" | wc -c) media files into $(basename "$bundle") ($(du -h "$bundle" | cut -f1))"
fi
mv "$next" "$mark"

# Database dumps not sent yet (the nightly backup writes them). Copied, not linked: the copy's time is when it
# joined the outbox, which is what the 48-hour check below measures.
sent="$STATE/sent"
touch "$sent"
# Before the rename to Opsis the names started with ams-; a dump sent under that name isn't sent again.
for f in "$DATA"/backups/*.dump; do
  base="$(basename "$f")"
  name="opsis-$base"
  grep -qxF -e "$name" -e "ams-$base" "$sent" || [ -e "$OUTBOX/$name" ] || cp "$f" "$OUTBOX/$name"
done

for f in "$OUTBOX"/opsis-* "$OUTBOX"/ams-*; do
  case "$f" in *.part) continue ;; esac
  name="$(basename "$f")"
  if tailscale file cp "$f" "$target:" 2>"$err"; then
    case "$name" in *.dump) echo "$name" >> "$sent" ;; esac
    rm -f "$f"
    echo "sent $name to $target"
  else
    echo "could not send $name to $target (trying again next hour): $(head -c 300 "$err")"
    break
  fi
done

# forget dumps the nightly backup has pruned
dumps=("$DATA"/backups/*.dump)
if [ ${#dumps[@]} -gt 0 ]; then
  { printf 'opsis-%s\n' "${dumps[@]##*/}"; printf 'ams-%s\n' "${dumps[@]##*/}"; } | grep -xF -f "$sent" > "$sent.tmp" || true
  mv "$sent.tmp" "$sent"
fi

for f in "$OUTBOX"/opsis-* "$OUTBOX"/ams-*; do
  if [ $(( ($(date +%s) - $(stat -c %Y "$f")) / 3600 )) -ge "$STALE_HOURS" ]; then
    echo "$(basename "$f") has waited over $STALE_HOURS h for $target: is it on and signed in to Tailscale?"
    exit 1
  fi
done
