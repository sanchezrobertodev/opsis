"""Print the OpenAPI schema without starting the server: `python -m studio.api.export_openapi > openapi.json`."""

from __future__ import annotations

import json
import sys

from studio.api.main import app


def main() -> None:
    sys.stdout.write(json.dumps(app.openapi(), indent=2))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
