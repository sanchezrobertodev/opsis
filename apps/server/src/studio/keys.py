"""Provider keys pasted in Settings: kept encrypted in the database, and used in place of the same setting in .env.

The secret that encrypts them is KEYS_SECRET in .env, or else `data/keys.secret`, made on first use. Lose it and
the saved keys can't be read: they show as unreadable and .env applies until they're pasted again.

Every process reads the owner's keys into its settings (`apply`): the API at once when one is saved, the API and the
worker every few seconds (`refresh_forever`), so adapters keep reading `get_settings().fal_key` as before. The full
key never leaves the server: the API reports only where each key comes from and its last four characters.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.config import REPO_ROOT, get_settings
from studio.db.models import ProviderKey

log = logging.getLogger(__name__)

REFRESH_SECONDS = 5.0
SECRET_FILE = REPO_ROOT / "data" / "keys.secret"


@dataclass(frozen=True)
class KeySpec:
    label: str
    link: str  # where to make one
    what: str


# Settings attribute -> what Settings shows. The same keys as .env.example, in its order.
KEYS: dict[str, KeySpec] = {
    "fal_key": KeySpec("fal.ai", "https://fal.ai/dashboard/keys", "Most hosted models; also hosts files for RunPod."),
    "gemini_api_key": KeySpec("Google Gemini API", "https://aistudio.google.com/apikey", "Gemini, Veo, Nano Banana."),
    "runway_api_key": KeySpec("Runway", "https://dev.runwayml.com/", "Runway's video models."),
    "replicate_api_token": KeySpec("Replicate", "https://replicate.com/account/api-tokens", "Models hosted on Replicate."),
    "anthropic_api_key": KeySpec("Anthropic (Claude)", "https://platform.claude.com/settings/keys",
                                 "Scripts, shot plans and prompt help when Claude is live."),
    "runpod_api_key": KeySpec("RunPod", "https://console.runpod.io/user/settings",
                              "Public endpoints and your own open-model endpoint."),
    "elevenlabs_api_key": KeySpec("ElevenLabs", "https://elevenlabs.io/app/developers/api-keys",
                                  "Narration, sound effects, music, voice cloning and dubbing."),
    "reactor_api_key": KeySpec("Reactor", "https://reactor.inc/dashboard", "FastH3-Omni streaming sessions."),
    "pika_api_key": KeySpec("Pika", "https://dev.pika.art/", "The Seedance 2.5 route on Pika."),
    "worldlabs_api_key": KeySpec("World Labs", "https://platform.worldlabs.ai/", "Marble explorable worlds."),
}

_from_env: dict[str, str] = {}  # what .env (or the environment) said, before any saved key replaced it
_unreadable: set[str] = set()  # saved keys the current secret can't decrypt


def _secret() -> bytes:
    if configured := get_settings().keys_secret:
        return configured.encode()
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:  # O_EXCL: the API and the worker starting together still agree on one secret
        fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return SECRET_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    log.info("made a new key secret at %s", SECRET_FILE)
    return key


def _fernet() -> Fernet:
    return Fernet(_secret())


def encrypt(value: str) -> bytes:
    return _fernet().encrypt(value.encode())


def env_value(setting: str) -> str:
    return _from_env.get(setting, getattr(get_settings(), setting))


def apply(saved: dict[str, str]) -> None:
    """Put the saved keys into this process's settings; settings without one go back to what .env said."""
    settings = get_settings()
    for setting in KEYS:
        _from_env.setdefault(setting, getattr(settings, setting))
        setattr(settings, setting, saved.get(setting) or _from_env[setting])


async def saved_keys(session: AsyncSession, user_id: uuid.UUID) -> dict[str, str]:
    rows = (await session.scalars(select(ProviderKey).where(ProviderKey.user_id == user_id))).all()
    fernet, out = _fernet(), {}
    _unreadable.clear()
    for row in rows:
        if row.setting not in KEYS:
            continue
        try:
            out[row.setting] = fernet.decrypt(row.ciphertext).decode()
        except InvalidToken:
            _unreadable.add(row.setting)
    if _unreadable:
        log.warning("saved keys %s can't be read with this key secret; .env applies to them", sorted(_unreadable))
    return out


async def load(session: AsyncSession) -> dict[str, str]:
    """Read the owner's saved keys and apply them. One studio, one owner: the keys every job uses are theirs."""
    saved = await saved_keys(session, get_settings().owner_user_id)
    apply(saved)
    return saved


async def refresh_forever() -> None:
    from studio.db.session import session_scope

    while True:
        with contextlib.suppress(Exception):  # a missed refresh keeps the keys from the last one
            async with session_scope() as session:
                await load(session)
        await asyncio.sleep(REFRESH_SECONDS)


def _last4(value: str) -> str | None:
    return value[-4:] if len(value) >= 12 else None  # a short value would show most of itself


@dataclass(frozen=True)
class KeyStatus:
    setting: str
    spec: KeySpec
    source: Literal["app", "env"] | None
    last4: str | None
    unreadable: bool


def status(saved: dict[str, str]) -> list[KeyStatus]:
    out = []
    for setting, spec in KEYS.items():
        value = saved.get(setting) or env_value(setting)
        source = "app" if setting in saved else "env" if value else None
        out.append(KeyStatus(setting, spec, source, _last4(value), setting in _unreadable))
    return out
