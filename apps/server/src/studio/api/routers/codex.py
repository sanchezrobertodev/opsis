"""Signing Codex in with a ChatGPT plan from Settings > Providers (`providers/codex_login.py`): start it, follow it
(the link and the one-time code to show), or stop it."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from studio.api.deps import current_user
from studio.config import get_settings
from studio.db.models import User
from studio.providers.codex import CodexAdapter, runner_state
from studio.providers.codex_login import CANCEL, DeviceLogin, ask_runner, clean, read_state
from studio.providers.common import ResultStore, touch_flag

router = APIRouter(prefix="/codex", tags=["codex"])


class CodexLoginOut(BaseModel):
    state: Literal["idle", "starting", "waiting", "signed_in", "failed", "canceled"]
    link: str | None = None
    code: str | None = None
    expires_at: float | None = None
    detail: str | None = None


_state: dict[str, Any] = {"state": "idle"}
_login = DeviceLogin(lambda: CodexAdapter().command(), lambda s: (_state.clear(), _state.update(s)))


def _folder():
    return ResultStore(CodexAdapter.key).root


def _configured() -> None:
    if not get_settings().codex_bin:
        raise HTTPException(409, "Codex isn't set up here: set CODEX_BIN in .env to the codex program")


@router.get("/login", response_model=CodexLoginOut)
async def login_status(_: User = Depends(current_user)) -> CodexLoginOut:
    return CodexLoginOut(**(read_state(_folder()) if get_settings().codex_runner else clean(_state)))


@router.post("/login", response_model=CodexLoginOut)
async def start_login(_: User = Depends(current_user)) -> CodexLoginOut:
    """Start a ChatGPT sign-in; follow it with GET until it shows the link and code, then until it's signed in."""
    _configured()
    if get_settings().codex_runner:
        folder = _folder()
        if runner_state(folder) is None:
            raise HTTPException(503, "The Codex runner isn't running (systemctl --user status opsis-codex)")
        ask_runner(folder)
        return CodexLoginOut(state="starting")
    _login.start()
    return CodexLoginOut(**clean(_state))


@router.delete("/login", response_model=CodexLoginOut)
async def cancel_login(_: User = Depends(current_user)) -> CodexLoginOut:
    _configured()
    if get_settings().codex_runner:
        touch_flag(_folder() / CANCEL)
        return CodexLoginOut(**read_state(_folder()))
    _login.cancel()
    return CodexLoginOut(**clean(_state))
