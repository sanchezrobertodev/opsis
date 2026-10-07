"""Nightly database backup: one pg_dump into data/backups/, dumps older than 14 days removed.

    uv run python scripts/backup_db.py                 # dump and prune
    uv run python scripts/backup_db.py --keep-days 30

Runs pg_dump inside the Postgres container, so Docker must be up (`pnpm db:up`). Media files under data/media/
are not in the database; back them up separately (CLAUDE.md, "Backups"). Exit code 1 means no new dump was made.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "server" / "src"))

from studio.ops import backups  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep-days", type=int, default=backups.KEEP_DAYS)
    args = ap.parse_args()
    stamp = datetime.now().isoformat(timespec="seconds")
    try:
        made = backups.dump()
    except backups.BackupError as exc:
        print(f"{stamp} backup FAILED: {exc}")
        return 1
    gone = backups.prune(keep_days=args.keep_days, db_name=made.name.rsplit("-", 2)[0])
    print(f"{stamp} backup {made.name} ({made.stat().st_size / 1e6:.1f} MB); removed {len(gone)} older than "
          f"{args.keep_days} days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
