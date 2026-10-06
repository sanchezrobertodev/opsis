from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from studio.config import get_settings
from studio.db.models import User
from studio.db.session import get_sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise


async def current_user(session: AsyncSession = Depends(get_session)) -> User:
    """Single-user studio: the seeded owner. Replace this dependency with real auth later."""
    user = await session.get(User, get_settings().owner_user_id)
    if user is None:
        raise HTTPException(500, "owner user missing; run `pnpm db:migrate`")
    return user
