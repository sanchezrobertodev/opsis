"""Restore the database from a dump in data/backups/. Restores never drop anything.

    uv run python scripts/restore_db.py --check                    # newest dump into a scratch copy, compared, dropped
    uv run python scripts/restore_db.py --check --file data/backups/studio-20261007-033000.dump
    uv run python scripts/restore_db.py --replace --file <dump> --yes   # make that dump the live database

--replace needs the API and worker stopped. It restores into a new database and swaps it in by renaming; the
database it replaces is kept as studio_before_restore_<time> until you drop it yourself.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "server" / "src"))

from studio.ops import backups  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--check", action="store_true", help="restore into a scratch copy and compare (safe)")
    what.add_argument("--replace", action="store_true", help="make the dump the live database")
    ap.add_argument("--file", type=Path, help="the dump (default: the newest)")
    ap.add_argument("--yes", action="store_true", help="confirm --replace")
    args = ap.parse_args()
    try:
        if args.check:
            result = backups.check(args.file)
            print(f"Restored {result.dump.name} into a scratch copy (dropped afterwards).")
            print(f"Migration: restored {result.migration[0]}, live {result.migration[1]}")
            for table, (restored, live) in result.tables.items():
                mark = "" if restored == live else "   <- differs (changed since the dump?)"
                print(f"  {table:32} {restored:>8} restored {live:>8} live{mark}")
            print("OK: every table restored." if result.ok else "PROBLEM: tables or migration differ.")
            return 0 if result.ok else 1
        dump = args.file or backups.latest()
        if not args.yes:
            print(f"This would make {dump.name} the live database (the current one is kept, renamed). "
                  "Stop the API and worker, then run again with --yes.")
            return 1
        kept = backups.replace(dump)
        print(f"Restored {dump.name}. The previous database is kept as {kept}; drop it when you're sure.")
        return 0
    except backups.BackupError as exc:
        print(f"Restore failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
