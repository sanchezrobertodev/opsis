"""A real dump, check and swap through the Postgres container, on a throwaway database (never the live one)."""

from __future__ import annotations

import shutil

import pytest

from studio.ops import backups

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs Docker (the Postgres container)")
TRIAL = "studio_backup_trial"


@pytest.fixture
def trial():
    user = backups.Database.from_settings().user
    db = backups.Database(user, TRIAL)
    for name in (TRIAL, f"{TRIAL}_incoming", f"{TRIAL}_restore_check"):
        backups._psql(db, f'drop database if exists "{name}"')
    backups._psql(db, f'create database "{TRIAL}"')
    backups._psql(db, "create table alembic_version (version_num text); insert into alembic_version values ('abc');"
                      "create table notes (id int); insert into notes select generate_series(1, 5)", TRIAL)
    yield db
    rows = backups._psql(db, f"select datname from pg_database where datname like '{TRIAL}%'").split()
    for name in rows:
        backups._psql(db, f'drop database if exists "{name}"')


def test_dump_check_and_replace_keep_every_row_and_drop_nothing(trial, tmp_path):
    dump = backups.dump(tmp_path, trial)
    assert dump.name.startswith(f"{TRIAL}-") and not list(tmp_path.glob("*.partial"))

    result = backups.check(dump, trial)
    assert result.ok and result.tables["notes"] == (5, 5) and result.migration == ("abc", "abc")

    backups._psql(trial, "insert into notes values (6)", TRIAL)  # a change made after the dump
    kept = backups.replace(dump, trial)
    assert backups._psql(trial, "select count(*) from notes", TRIAL).strip() == "5"  # back to the dump
    assert backups._psql(trial, "select count(*) from notes", kept).strip() == "6"  # the replaced one is kept
