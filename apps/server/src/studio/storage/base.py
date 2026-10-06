"""Storage interface. Local disk today; an R2/S3 implementation can slot in later with the same keys."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol


def asset_key(user_id: uuid.UUID, asset_id: uuid.UUID, name: str) -> str:
    """Canonical object key: u/{user}/a/{asset}/{name}. Same shape for disk and object storage."""
    return f"u/{user_id}/a/{asset_id}/{name}"


class Storage(Protocol):
    def path_for(self, key: str) -> Path:
        """Local filesystem path for a key (for ffmpeg and streaming). Remote stores download to cache."""
        ...

    def put_file(self, key: str, src: Path, *, move: bool = False) -> int:
        """Store `src` under `key`; returns size in bytes."""
        ...

    def exists(self, key: str) -> bool: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def url_for(self, key: str) -> str:
        """Browser URL for the object (served by the API for local disk)."""
        ...

    def temp_dir(self) -> Path:
        """A scratch directory without spaces, for ffmpeg intermediates."""
        ...
