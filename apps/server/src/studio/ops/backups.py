"""Database backups: pg_dump through the Postgres container, 14 days kept, and restores that never destroy data.

Dumps are pg_dump's custom format (`-Fc`), one file per run in data/backups/, written under a temporary name and
checked with `pg_restore --list` before they count. Media under data/media/ is not in the database and needs its
own copy (see CLAUDE.md, "Backups").

Restores go into a new database first. `check` restores the newest dump into a scratch database, compares it
with the live one table by table and drops it. `replace` restores into a new database and swaps it in by renaming,
keeping the old one as `<db>_before_restore_<time>`, so nothing is ever dropped by a restore.
"""

from __future__ import annotations

import re
import secrets
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from studio.config import REPO_ROOT, get_settings

COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
BACKUPS = REPO_ROOT / "data" / "backups"
NAME = re.compile(r"^(?P<db>[a-z0-9_]+)-(?P<stamp>\d{8}-\d{6})\.dump$")
KEEP_DAYS = 14
COUNTS = (
    "select table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from public.%I', "
    "table_name), false, true, '')))[1]::text from information_schema.tables "
    "where table_schema = 'public' and table_type = 'BASE TABLE' order by 1"
)


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class Database:
    user: str
    name: str

    @classmethod
    def from_settings(cls) -> Database:
        url = urlsplit(get_settings().database_url.replace("+asyncpg", ""))
        return cls(user=url.username or "postgres", name=url.path.lstrip("/") or "postgres")


def _exec(args: Sequence[str], *, stdin: Path | None = None, stdout: Path | None = None) -> str:
    """Run a command inside the Postgres container (docker compose exec), returning its text output."""
    cmd = ["docker", "compose", "-f", str(COMPOSE), "exec", "-T", "postgres", *args]
    try:
        if stdout is not None:
            with stdout.open("wb") as out:
                proc = subprocess.run(cmd, stdin=stdin.open("rb") if stdin else None, stdout=out,
                                      stderr=subprocess.PIPE, check=False)
            text = ""
        else:
            src = stdin.open("rb") if stdin else None
            try:
                proc = subprocess.run(cmd, stdin=src, capture_output=True, check=False)
            finally:
                if src:
                    src.close()
            text = proc.stdout.decode("utf-8", "replace")
    except FileNotFoundError:
        raise BackupError("docker isn't on PATH; start Docker Desktop and `pnpm db:up`") from None
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise BackupError(f"{args[0]} failed: {err[-800:] or f'exit {proc.returncode}'}")
    return text


def _psql(db: Database, sql: str, database: str | None = None) -> str:
    return _exec(["psql", "-U", db.user, "-d", database or "postgres", "-v", "ON_ERROR_STOP=1", "-At", "-c", sql])


def dump(dest: Path = BACKUPS, db: Database | None = None, now: datetime | None = None) -> Path:
    """Write one dump; it only gets its final name once pg_restore can read it."""
    db = db or Database.from_settings()
    dest.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    final = dest / f"{db.name}-{stamp}.dump"
    partial = final.with_suffix(".partial")
    try:
        _exec(["pg_dump", "-U", db.user, "-d", db.name, "-Fc"], stdout=partial)
        if partial.stat().st_size == 0:
            raise BackupError("pg_dump wrote nothing")
        listing = _exec(["pg_restore", "--list"], stdin=partial)
        if "TABLE DATA" not in listing:
            raise BackupError("the dump has no table data")
        partial.replace(final)
    finally:
        partial.unlink(missing_ok=True)
    return final


def dumps(dest: Path = BACKUPS, db_name: str | None = None) -> list[tuple[datetime, Path]]:
    """Finished dumps, oldest first."""
    found = []
    for p in dest.glob("*.dump") if dest.exists() else []:
        m = NAME.match(p.name)
        if m and (db_name is None or m["db"] == db_name):
            found.append((datetime.strptime(m["stamp"], "%Y%m%d-%H%M%S"), p))
    return sorted(found)


def prune(dest: Path = BACKUPS, keep_days: int = KEEP_DAYS, now: datetime | None = None,
          db_name: str | None = None) -> list[Path]:
    """Delete dumps older than `keep_days`, always keeping the newest one. Returns what was deleted."""
    found = dumps(dest, db_name)
    cutoff = (now or datetime.now()) - timedelta(days=keep_days)
    gone = [p for when, p in found[:-1] if when < cutoff]
    for p in gone:
        p.unlink()
    return gone


def latest(dest: Path = BACKUPS, db_name: str | None = None) -> Path:
    found = dumps(dest, db_name)
    if not found:
        raise BackupError(f"no dumps in {dest}")
    return found[-1][1]


def _counts(db: Database, database: str) -> dict[str, int]:
    rows = _psql(db, COUNTS, database).splitlines()
    return {name: int(n) for name, n in (r.split("|", 1) for r in rows if "|" in r)}


def _fresh_name(db: Database, purpose: str) -> str:
    """A database name no other run uses, so a run only ever drops what it created itself."""
    return f"{db.name}_{purpose}_{secrets.token_hex(4)}"


@contextmanager
def _restored(db: Database, dump_file: Path, purpose: str) -> Iterator[str]:
    """A new database (a name this run made up) holding the dump, dropped on the way out. A caller that renames it
    into place leaves nothing under that name, so the drop then does nothing."""
    name = _fresh_name(db, purpose)
    _psql(db, f'create database "{name}"')  # fails rather than reuse a database that already exists
    try:
        _exec(["pg_restore", "-U", db.user, "-d", name, "--no-owner", "--exit-on-error"], stdin=dump_file)
        yield name
    finally:
        _psql(db, f'drop database if exists "{name}"')


@dataclass
class CheckResult:
    dump: Path
    tables: dict[str, tuple[int, int]]  # table -> (rows in the restored copy, rows live now)
    migration: tuple[str, str]  # (restored, live) alembic revision

    @property
    def ok(self) -> bool:
        live_tables = {t for t, (_, live) in self.tables.items() if live >= 0}
        restored = {t for t, (rows, _) in self.tables.items() if rows >= 0}
        return restored == live_tables and self.migration[0] == self.migration[1]


def check(dump_file: Path | None = None, db: Database | None = None) -> CheckResult:
    """Restore a dump into a scratch database, compare it with the live one, and drop the scratch copy."""
    db = db or Database.from_settings()
    dump_file = dump_file or latest(db_name=db.name)
    with _restored(db, dump_file, "restore_check") as scratch:
        restored, live = _counts(db, scratch), _counts(db, db.name)
        migration = (_psql(db, "select version_num from alembic_version", scratch).strip(),
                     _psql(db, "select version_num from alembic_version", db.name).strip())
    tables = {t: (restored.get(t, -1), live.get(t, -1)) for t in sorted(set(restored) | set(live))}
    return CheckResult(dump_file, tables, migration)


def replace(dump_file: Path, db: Database | None = None, now: datetime | None = None) -> str:
    """Make a dump the live database. The current one is renamed, not dropped; returns its new name."""
    db = db or Database.from_settings()
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d_%H%M%S")
    kept = f"{db.name}_before_restore_{stamp}"
    busy = _psql(db, f"select count(*) from pg_stat_activity where datname = '{db.name}' and pid <> pg_backend_pid()")
    if int(busy.strip() or 0):
        raise BackupError(f"{busy.strip()} connections are open to {db.name}; stop `pnpm dev` (API and worker) first")
    with _restored(db, dump_file, "incoming") as incoming:
        _psql(db, f'alter database "{db.name}" rename to "{kept}"')
        try:
            _psql(db, f'alter database "{incoming}" rename to "{db.name}"')
        except BackupError:
            _psql(db, f'alter database "{kept}" rename to "{db.name}"')  # put the original back
            raise
    return kept
