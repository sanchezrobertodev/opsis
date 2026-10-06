from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

from studio.config import get_settings


class LocalDiskStorage:
    def __init__(self, root: Path, public_prefix: str = "/media"):
        self.root = root
        self.public_prefix = public_prefix.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "tmp").mkdir(exist_ok=True)

    def path_for(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError(f"key escapes media root: {key!r}")
        return path

    def put_file(self, key: str, src: Path, *, move: bool = False) -> int:
        dest = self.path_for(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if move:
            # os.replace is atomic within a volume; fall back to copy across volumes
            try:
                os.replace(src, dest)
            except OSError:
                shutil.move(str(src), dest)
        else:
            shutil.copyfile(src, dest)
        return dest.stat().st_size

    def exists(self, key: str) -> bool:
        return self.path_for(key).exists()

    def delete_prefix(self, prefix: str) -> None:
        target = self.path_for(prefix)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()

    def url_for(self, key: str) -> str:
        return f"{self.public_prefix}/{key}"

    def temp_dir(self) -> Path:
        return self.root / "tmp"


@lru_cache
def get_storage() -> LocalDiskStorage:
    return LocalDiskStorage(get_settings().resolve_media_root())
