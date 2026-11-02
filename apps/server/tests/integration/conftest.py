"""The integration tests call the API in process as http://test, a bare name the API never answers in use (main.py's
Host check): here it does, and only here."""

from __future__ import annotations

from starlette.middleware.trustedhost import TrustedHostMiddleware

from studio.api.main import app

for _m in app.user_middleware:  # read when the app builds its middleware, at its first request
    if _m.cls is TrustedHostMiddleware:
        _m.kwargs["allowed_hosts"] = [*_m.kwargs["allowed_hosts"], "test"]
