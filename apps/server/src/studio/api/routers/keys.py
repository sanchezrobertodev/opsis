"""Provider keys from Settings: saved encrypted, never read back (studio/keys.py)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from studio import keys
from studio.api.deps import current_user, get_session
from studio.budget.balances import clear_cache
from studio.db.models import ProviderKey, User

router = APIRouter(prefix="/keys", tags=["keys"])


class KeyOut(BaseModel):
    setting: str
    label: str
    link: str
    what: str
    source: Literal["app", "env"] | None = Field(description="app: saved here; env: from .env; null: not set")
    last4: str | None
    unreadable: bool = Field(description="saved, but the key secret can't decrypt it (.env applies)")


class KeyUpdate(BaseModel):
    value: str = Field(min_length=1, max_length=1000)


async def _list(session: AsyncSession, user: User) -> list[KeyOut]:
    await keys.load(session)
    return [KeyOut(setting=s.setting, label=s.spec.label, link=s.spec.link, what=s.spec.what, source=s.source,
                   last4=s.last4, unreadable=s.unreadable)
            for s in keys.status(await keys.saved_keys(session, user.id))]


def _known(setting: str) -> None:
    if setting not in keys.KEYS:
        raise HTTPException(404, f"no provider key called {setting}")


@router.get("", response_model=list[KeyOut])
async def list_keys(session: AsyncSession = Depends(get_session), user: User = Depends(current_user)) -> list[KeyOut]:
    """Which provider keys are set and where from. The keys themselves are never returned."""
    return await _list(session, user)


@router.put("/{setting}", response_model=list[KeyOut])
async def save_key(setting: str, body: KeyUpdate, session: AsyncSession = Depends(get_session),
                   user: User = Depends(current_user)) -> list[KeyOut]:
    """Save a key; it replaces the same key in .env, here and in the worker, within seconds."""
    _known(setting)
    value = body.value.strip()
    if not value or any(c.isspace() for c in value):
        raise HTTPException(422, "a key is one word with no spaces; paste just the key")
    row = {"user_id": user.id, "setting": setting, "ciphertext": keys.encrypt(value)}
    await session.execute(insert(ProviderKey).values(**row).on_conflict_do_update(
        index_elements=["user_id", "setting"], set_={"ciphertext": row["ciphertext"], "updated_at": func.now()}))
    await session.commit()
    clear_cache()  # balances read with the old key
    return await _list(session, user)


@router.delete("/{setting}", response_model=list[KeyOut])
async def remove_key(setting: str, session: AsyncSession = Depends(get_session),
                     user: User = Depends(current_user)) -> list[KeyOut]:
    """Forget a saved key; .env's, if it has one, applies again."""
    _known(setting)
    await session.execute(delete(ProviderKey).where(ProviderKey.user_id == user.id, ProviderKey.setting == setting))
    await session.commit()
    clear_cache()
    return await _list(session, user)
