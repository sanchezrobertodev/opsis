"""Pictures for Claude to look at: the user's own ready pictures, in the order given (a clip gives its middle frame),
each a base64 JPEG block sent before the prompt text.

Pictures are scaled down to a 1280 px long edge, past which Claude sees no more detail for the tokens, and each is
counted as ceil(w / 28) * ceil(h / 28) input tokens in the budget ceiling.
"""

from __future__ import annotations

import base64
import io
import math
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import anyio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import Asset
from studio.domain.enums import AssetKind, AssetStatus
from studio.llm.client import LLMError
from studio.media.derive import open_picture
from studio.media.ffmpeg import FFmpegError
from studio.media.ops import extract_frame
from studio.storage.local import get_storage

LONG_EDGE = 1280
TOKEN_PIXELS = 28  # Claude counts a picture in 28 x 28 pixel tiles, about a token each
KINDS = (AssetKind.image, AssetKind.video)


class ImageError(LLMError):
    """A picture that can't go to Claude (not the user's, not ready, not a picture or clip, unreadable)."""


def tokens(width: int, height: int) -> int:
    return math.ceil(width / TOKEN_PIXELS) * math.ceil(height / TOKEN_PIXELS)


def encode(path: Path) -> tuple[str, int, int]:
    """The picture at `path` as base64 JPEG no longer than LONG_EDGE on either side: (data, width, height)."""
    with open_picture(path) as im:
        alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        im = im.convert("RGBA" if alpha else "RGB")
        im.thumbnail((LONG_EDGE, LONG_EDGE), Image.Resampling.LANCZOS)
        if alpha:  # a cutout's clear parts would turn black: lay it on white, the way it's usually shown
            im = Image.alpha_composite(Image.new("RGBA", im.size, "white"), im).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode("ascii"), im.width, im.height


async def load(session: AsyncSession, user_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> list[Asset]:
    """The user's ready pictures and clips in the order given (repeats once). ImageError names the first that isn't."""
    wanted = list(dict.fromkeys(ids))
    rows = {
        a.id: a
        for a in (
            await session.execute(
                select(Asset).where(
                    Asset.id.in_(wanted), Asset.user_id == user_id, Asset.deleted_at.is_(None)
                )
            )
        ).scalars()
    }
    for asset_id in wanted:
        asset = rows.get(asset_id)
        if asset is None:
            raise ImageError(f"picture {asset_id} not found")
        if asset.status != AssetStatus.ready:
            raise ImageError(f"{asset.title or 'a picture'} isn't ready yet ({asset.status})")
        if asset.kind not in KINDS:
            raise ImageError(f"{asset.title or 'an input'} is {asset.kind}, not a picture or a clip")
    return [rows[i] for i in wanted]


async def image_blocks(
    session: AsyncSession, user_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> tuple[list[dict[str, Any]], int]:
    """Claude image blocks for the assets, in order, and the input tokens they come to."""
    storage = get_storage()
    blocks: list[dict[str, Any]] = []
    total = 0
    for asset in await load(session, user_id, ids):
        src, frame = storage.path_for(asset.storage_key), None
        try:
            if asset.kind == AssetKind.video:
                frame = storage.temp_dir() / f"claude-{uuid.uuid4().hex}.png"
                await extract_frame(src, frame, at=(asset.duration_ms or 0) / 2000)
                src = frame
            data, width, height = await anyio.to_thread.run_sync(encode, src)
        except (OSError, ValueError, FFmpegError) as exc:
            raise ImageError(f"couldn't read {asset.title or 'a picture'}: {str(exc)[-200:]}") from None
        finally:
            if frame is not None:
                await anyio.to_thread.run_sync(lambda f=frame: f.unlink(missing_ok=True))
        blocks.append(
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}
        )
        total += tokens(width, height)
    return blocks, total
