"""Test configuration.

Unit/contract tests need nothing running. Tests marked `db` use an isolated `studio_test` database in
the compose Postgres (created and migrated on first use) and a temp media root. Paid routes are never
called: PROVIDER_MODE is forced to mock and the mock provider finishes instantly.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Must be set before any studio import reads settings.
_MEDIA = Path(tempfile.mkdtemp(prefix="studio-test-media-"))
# The compose Postgres on the PC; STUDIO_TEST_PG points elsewhere (the home server's pod: 127.0.0.1:5432)
PG_HOST, PG_PORT = os.environ.get("STUDIO_TEST_PG", "127.0.0.1:5433").rsplit(":", 1)
os.environ.update({
    "DATABASE_URL": f"postgresql+asyncpg://studio:studio@{PG_HOST}:{PG_PORT}/studio_test",
    "QUEUE_DATABASE_URL": f"postgresql://studio:studio@{PG_HOST}:{PG_PORT}/studio_test",
    "PROVIDER_MODE": "mock",
    "LIVE_ROUTE_ALLOWLIST": "",
    "MEDIA_ROOT": str(_MEDIA / "media"),
    "MOCK_QUEUE_SECONDS": "0",
    "MOCK_RUN_SECONDS": "0",
    "POLL_INITIAL_SECONDS": "0",
    # Tests never talk to a real ComfyUI; adapter tests point at a fake one explicitly.
    "COMFYUI_URL": "",
    # Claude is mocked; live-mode tests swap in a fake client explicitly.
    "LLM_MODE": "mock",
    "ANTHROPIC_API_KEY": "",
})

import pytest  # noqa: E402

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

SERVER_DIR = Path(__file__).resolve().parents[1]


def _db_available() -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((PG_HOST, int(PG_PORT))) == 0


_migrated = False


async def _prepare_test_db() -> None:
    global _migrated
    if _migrated:
        return
    import asyncpg

    admin = await asyncpg.connect(f"postgresql://studio:studio@{PG_HOST}:{PG_PORT}/studio")
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = 'studio_test'")
        if exists:
            await admin.execute("DROP DATABASE studio_test WITH (FORCE)")
        await admin.execute("CREATE DATABASE studio_test")
    finally:
        await admin.close()

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    # env.py runs asyncio.run(); do it on a worker thread so it doesn't nest inside pytest's loop
    await asyncio.to_thread(command.upgrade, cfg, "head")
    _migrated = True


@pytest.fixture
async def db():
    if not _db_available():
        pytest.skip(f"Postgres on {PG_HOST}:{PG_PORT} not running (docker compose -f infra/docker-compose.yml up -d)")
    await _prepare_test_db()
    from studio.db.session import get_engine

    yield
    await get_engine().dispose()


@pytest.fixture
def in_memory_queue():
    from procrastinate.testing import InMemoryConnector

    from studio.worker.app import app

    connector = InMemoryConnector()
    with app.replace_connector(connector):
        yield connector


def pytest_collection_modifyitems(items):
    for item in items:
        if "db" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.db)
