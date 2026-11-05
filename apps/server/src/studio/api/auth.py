"""The optional access token (`ACCESS_TOKEN`): one shared secret in front of every route, for a studio others on the
network can reach (the server's tailnet). Unset, nothing here applies.

Scripts and MCP clients send `Authorization: Bearer <token>`. A browser signs in once and keeps an HttpOnly cookie,
since media tags and the event stream can't send a header. The cookie holds a value derived from the token, never the
token: cookies ignore ports, so every other service on the host name sees it. A new token signs every browser out.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac

from fastapi import APIRouter, HTTPException, Request, Response

from studio.api.schemas import AuthStateOut, SignInIn
from studio.config import get_settings

COOKIE = "opsis_session"
YEAR_S = 400 * 86400  # the longest a browser keeps a cookie: sign in once per browser

router = APIRouter(prefix="/auth", tags=["auth"])


def open_path(path: str) -> bool:
    """Answered without the token: the health check (trimmed, see misc.health), signing in, and the OAuth discovery
    paths, so an MCP client without the header is told no (404) rather than starting an OAuth sign-up."""
    return path in ("/health", "/auth/session") or path.startswith("/.well-known/")


def session_value(token: str) -> str:
    return hmac.new(token.encode(), b"opsis browser session", hashlib.sha256).hexdigest()


def allowed(request: Request) -> bool:
    """Whether this request carries the token (always, when none is set)."""
    token = get_settings().access_token
    if not token:
        return True
    given = request.headers.get("authorization", "")
    if given[:7].lower() == "bearer " and hmac.compare_digest(given[7:].strip().encode(), token.encode()):
        return True
    # /mcp's tools can start billed runs and skip the Origin check, so never on a cookie, which a page on another
    # port of this host name would send too
    if request.url.path == "/mcp":
        return False
    return hmac.compare_digest(request.cookies.get(COOKIE, "").encode(), session_value(token).encode())


def _state(request: Request, signed_in: bool | None = None) -> AuthStateOut:
    required = bool(get_settings().access_token)
    return AuthStateOut(required=required, signed_in=allowed(request) if signed_in is None else signed_in)


@router.get("/session", response_model=AuthStateOut)
async def session_state(request: Request) -> AuthStateOut:
    return _state(request)


@router.post("/session", response_model=AuthStateOut)
async def sign_in(body: SignInIn, request: Request, response: Response) -> AuthStateOut:
    token = get_settings().access_token
    if not token:
        return _state(request)
    if not hmac.compare_digest(body.token.strip().encode(), token.encode()):
        await asyncio.sleep(1)  # slows guessing; the token itself is what keeps it out of reach
        raise HTTPException(401, "that isn't the studio's access token")
    response.set_cookie(COOKIE, session_value(token), max_age=YEAR_S, httponly=True, samesite="lax",
                        # behind `tailscale serve` the API sees plain http: the web app's own address says https
                        secure=get_settings().web_origin.startswith("https://"))
    return _state(request, signed_in=True)


@router.delete("/session", response_model=AuthStateOut)
async def sign_out(request: Request, response: Response) -> AuthStateOut:
    response.delete_cookie(COOKIE, httponly=True, samesite="lax", secure=get_settings().web_origin.startswith("https://"))
    return _state(request, signed_in=not get_settings().access_token)
