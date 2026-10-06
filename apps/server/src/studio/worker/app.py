"""procrastinate app shared by the API (to defer jobs) and the worker (to run them).

Postgres is the queue, so there is no Redis. psycopg async needs a SelectorEventLoop on Windows: the
worker entrypoint and the API launcher both set it.
"""

from __future__ import annotations

from procrastinate import App, PsycopgConnector

from studio.config import get_settings

app = App(
    connector=PsycopgConnector(conninfo=get_settings().queue_database_url),
    import_paths=[
        "studio.worker.tasks.generation",
        "studio.worker.tasks.assets",
        "studio.worker.tasks.maintenance",
        "studio.worker.tasks.pipeline",
    ],
)

# Queue names
Q_SUBMIT = "submit"
Q_POLL = "poll"
Q_INGEST = "ingest"
Q_CPU = "cpu"
Q_MAINT = "maint"
