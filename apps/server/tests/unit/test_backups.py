from datetime import datetime

import pytest

from studio.ops import backups


def test_prune_keeps_two_weeks_and_always_the_newest(tmp_path):
    for stamp in ("20260901-033000", "20260920-033000", "20260930-033000", "20261006-033000"):
        (tmp_path / f"studio-{stamp}.dump").write_bytes(b"x")
    (tmp_path / "studio-20261007-033000.partial").write_bytes(b"x")  # unfinished: never counted or pruned
    (tmp_path / "notes.txt").write_text("not a dump")
    gone = backups.prune(tmp_path, keep_days=14, now=datetime(2026, 10, 7, 4, 0))
    assert sorted(p.name for p in gone) == ["studio-20260901-033000.dump", "studio-20260920-033000.dump"]
    assert backups.latest(tmp_path).name == "studio-20261006-033000.dump"

    # long after the last dump, the newest one still stays
    assert backups.prune(tmp_path, keep_days=14, now=datetime(2027, 1, 1))[0].name == "studio-20260930-033000.dump"
    assert [p.name for _, p in backups.dumps(tmp_path)] == ["studio-20261006-033000.dump"]


def test_the_database_comes_from_the_settings():
    db = backups.Database.from_settings()
    assert db.user and db.name and "+" not in db.name


def test_db_exec_says_how_the_postgres_tools_run(monkeypatch):
    settings = backups.get_settings()
    monkeypatch.setattr(settings, "db_exec", None)  # the compose setup: into its service
    assert backups._prefix()[:2] == ["docker", "compose"] and backups._prefix()[-1] == "postgres"
    monkeypatch.setattr(settings, "db_exec", "")  # infra/server's pod: the tools sit next to the app
    assert backups._prefix() == []
    monkeypatch.setattr(settings, "db_exec", "podman exec -i 'ams postgres'")
    assert backups._prefix() == ["podman", "exec", "-i", "ams postgres"]
    monkeypatch.setattr(settings, "db_exec", "no-such-tool-here")
    with pytest.raises(backups.BackupError, match="no-such-tool-here isn't on PATH"):
        backups._psql(backups.Database("u", "d"), "select 1")
