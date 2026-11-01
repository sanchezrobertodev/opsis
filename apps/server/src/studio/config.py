from __future__ import annotations

import uuid
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/server/src/studio/config.py -> repo root is four parents up
REPO_ROOT = Path(__file__).resolve().parents[4]

# Fixed id of the single seeded owner. Auth later replaces deps.current_user(), not this id.
DEFAULT_OWNER_ID = uuid.UUID("00000000-0000-7000-8000-000000000001")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://studio:studio@127.0.0.1:5433/studio"
    # procrastinate uses psycopg 3, which wants a plain libpq URL
    queue_database_url: str = "postgresql://studio:studio@127.0.0.1:5433/studio"

    provider_mode: Literal["mock", "mixed", "live"] = "mock"
    live_route_allowlist: str = ""

    media_root: Path = REPO_ROOT / "data" / "media"
    registry_dir: Path = REPO_ROOT / "registry"
    web_origin: str = "http://localhost:3000"
    api_port: int = 8400  # `python -m studio.api` listens here unless --port says otherwise
    owner_user_id: uuid.UUID = DEFAULT_OWNER_ID

    # Mock provider timing (seconds). Tests shrink these.
    mock_queue_seconds: float = 2.0
    mock_run_seconds: float = 5.0

    # Worker polling backoff
    poll_initial_seconds: float = 2.0
    poll_max_seconds: float = 20.0

    fal_key: str = Field(default="", repr=False)
    gemini_api_key: str = Field(default="", repr=False)
    runway_api_key: str = Field(default="", repr=False)
    replicate_api_token: str = Field(default="", repr=False)
    anthropic_api_key: str = Field(default="", repr=False)
    runpod_api_key: str = Field(default="", repr=False)
    elevenlabs_api_key: str = Field(default="", repr=False)
    reactor_api_key: str = Field(default="", repr=False)
    pika_api_key: str = Field(default="", repr=False)
    worldlabs_api_key: str = Field(default="", repr=False)
    # Encrypts the keys pasted in Settings (studio/keys.py). Empty: data/keys.secret, made on first use.
    keys_secret: str = Field(default="", repr=False)
    # Our own RunPod serverless endpoint running infra/runpod/wan-core (empty turns its routes off)
    runpod_wan_core_endpoint: str = ""

    # Claude (prompt enhancement, scripts, shot plans). `mock` returns canned results and never bills.
    llm_mode: Literal["mock", "live"] = "mock"
    llm_fast_model: str = "claude-sonnet-5-5"  # interactive: enhance, vision review
    llm_smart_model: str = "claude-opus-5-5"  # scripts, shot plans

    # How backups reach the Postgres tools (ops/backups.py). Unset: `docker compose exec` into the dev container.
    # Empty: run pg_dump/psql here, with PGHOST/PGPASSWORD set (infra/server's pod). Else a command prefix.
    db_exec: str | None = None

    # The Codex CLI, signed in with a ChatGPT plan: GPT Image from the plan's allowance. Empty turns the route off.
    codex_bin: str = ""

    # Local ComfyUI. Empty disables the local route.
    comfyui_url: str = ""
    comfyui_workflows_dir: Path = REPO_ROOT / "workflows" / "comfy"
    # Optional shared YAML read by the local ComfyUI launcher; enables Settings > Model storage.
    comfyui_model_paths_config: Path | None = None

    @property
    def live_routes(self) -> set[str]:
        return {r.strip() for r in self.live_route_allowlist.split(",") if r.strip()}

    def resolve_media_root(self) -> Path:
        root = self.media_root if self.media_root.is_absolute() else REPO_ROOT / self.media_root
        return root.resolve()

    def resolve_registry_dir(self) -> Path:
        d = self.registry_dir if self.registry_dir.is_absolute() else REPO_ROOT / self.registry_dir
        return d.resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
