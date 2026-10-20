"""World Labs Marble API adapter: explorable Gaussian-splat worlds from text or a picture.

Submit is `POST /marble/v1/worlds:generate`, which returns a long-running operation; poll it at
`/marble/v1/operations/{id}` until `done`, then the world is in `response` (splats as .spz, a 360 panorama,
a collider mesh, a thumbnail). Pictures go inline as `data_base64` when small, otherwise as a web link hosted
through fal storage. The model id is a param (`marble-1.1`, `marble-1.0-draft`, ...), so new models are
registry edits. Contract verified 2026-10-07 against https://docs.worldlabs.ai/api.

Billing: credits are prepaid, but usage past the balance is charged to the card at the end of the month
("overage"), so the spend guard and caps are the only brake. The API doesn't report a job's credits, so the
registry estimate is charged.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import anyio
import httpx

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.providers.base import (
    PollResult,
    ProviderAdapter,
    ProviderError,
    ProviderOutput,
    ResolvedMedia,
    SubmitResult,
)
from studio.providers.common import (
    download_url,
    http_error,
    looks_like_moderation,
    require_key,
    route_outputs,
    submit_error,
    submit_failure,
)
from studio.registry.schema import Route

API = "https://api.worldlabs.ai"
CREDITS_PER_USD = 1250  # docs.worldlabs.ai/api/pricing, checked 2026-10-07
INLINE_LIMIT = 8 * 1024 * 1024  # inline pictures up to 8 MB; larger ones go up as web links
NAME_LIMIT = 64


def content_ref(media: ResolvedMedia, data: bytes | None, url: str | None) -> dict[str, Any]:
    """A Marble content object: inline bytes or a public link."""
    if data is not None:
        return {"source": "data_base64", "data_base64": base64.b64encode(data).decode(),
                "extension": media.path.suffix.lstrip(".").lower() or "png"}
    return {"source": "uri", "uri": url}


class WorldLabsAdapter(ProviderAdapter):
    key = "worldlabs"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self, timeout: float = 60) -> httpx.AsyncClient:
        key = require_key("World Labs", get_settings().worldlabs_api_key)
        return httpx.AsyncClient(base_url=API, timeout=timeout, transport=self._transport,
                                 headers={"WLT-Api-Key": key})

    async def _ref(self, route: Route, m: ResolvedMedia) -> dict[str, Any]:
        size = (await anyio.Path(m.path).stat()).st_size
        if size <= INLINE_LIMIT:
            return content_ref(m, await anyio.Path(m.path).read_bytes(), None)
        if not get_settings().fal_key:
            raise ProviderError(ErrorClass.invalid_request,
                                f"{m.path.name} is over {INLINE_LIMIT >> 20} MB; World Labs needs a web link for it, "
                                "which needs FAL_KEY to host the file")
        from studio.providers.factory import get_adapter

        refs = await get_adapter("fal").stage_inputs(route, [m])
        return content_ref(m, None, refs[m.role][0])

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        staged: dict[str, Any] = {}
        for m in media:
            staged.setdefault(m.role, []).append(await self._ref(route, m))
        return staged

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        body = dict(payload)
        prompt = str((body.get("world_prompt") or {}).get("text_prompt") or "").strip()
        body.setdefault("display_name", (prompt or f"Studio world {client_ref[:8]}")[:NAME_LIMIT])
        try:
            async with self._client() as c:
                r = await c.post(f"/{endpoint.lstrip('/')}", json=body)
        except httpx.HTTPError as exc:
            raise submit_failure("World Labs", exc) from exc
        if r.status_code not in (200, 201, 202):
            raise submit_error("World Labs", r)
        op = r.json()
        return SubmitResult(job_id=op["operation_id"], raw={"endpoint": endpoint, "model": body.get("model")})

    async def poll(self, route: Route, job_id: str) -> PollResult:
        try:
            async with self._client() as c:
                r = await c.get(f"/marble/v1/operations/{job_id}")
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, f"World Labs status failed: {exc!r}") from exc
        if r.status_code != 200:
            raise http_error("World Labs", r)
        op = r.json()
        meta = op.get("metadata") or {}
        world_id = meta.get("world_id")
        if not op.get("done"):
            progress = (meta.get("progress") or {}).get("status")
            return PollResult(status="running" if progress else "queued", raw={"world_id": world_id})
        error = op.get("error")
        if error:
            text = error.get("message") if isinstance(error, dict) else str(error)
            cls = ErrorClass.moderation if looks_like_moderation(text) else ErrorClass.internal
            return PollResult(status="failed", error_class=cls, error_message=f"World Labs: {text}"[:900])
        world = op.get("response") or {}
        if world.get("world_id") or world_id:
            world = await self._stored_world(world.get("world_id") or world_id, world)
        outputs = route_outputs(world, route.output)
        if not outputs:
            return PollResult(status="failed", error_class=ErrorClass.internal,
                              error_message=f"World Labs world {world_id or ''} has no {route.output.path}")
        # metric scale and ground height let the viewer stand the world on the floor at real size
        semantics = (((world.get("assets") or {}).get("splats") or {}).get("semantics_metadata")) or {}
        for o in outputs:
            o.meta = {**o.meta, "world_id": world.get("world_id") or world_id,
                      **({k: v for k, v in semantics.items() if v is not None} if o.kind == "splat" else {})}
        # the finished operation says what it cost: charge that, not the estimate
        credits = (op.get("cost") or {}).get("total_credits")
        usage = ({"credits": credits, "cost_usd": round(credits / CREDITS_PER_USD, 6)}
                 if isinstance(credits, int | float) and not isinstance(credits, bool) else {})
        return PollResult(status="succeeded", progress=1.0, outputs=outputs, usage=usage)

    async def _stored_world(self, world_id: str, fallback: dict[str, Any]) -> dict[str, Any]:
        """The world as stored. The finished operation's copy can lag behind it (a draft's panorama is still empty
        there), so read it back; reading is free. The operation's copy stands in if that fails."""
        try:
            async with self._client() as c:
                r = await c.get(f"/marble/v1/worlds/{world_id}")
        except httpx.HTTPError:
            return fallback
        if r.status_code != 200:
            return fallback
        body = r.json()
        stored = body.get("world", body) if isinstance(body, dict) else None
        return stored if isinstance(stored, dict) and stored.get("assets") else fallback

    async def cancel(self, route: Route, job_id: str) -> bool:
        return False  # the API has no way to stop a world once it's started

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        content_type = await download_url(output.url or "", dest)
        if output.kind == "splat":
            return "application/octet-stream" if content_type in (None, "binary/octet-stream") else content_type
        if output.kind == "model3d":
            return "model/gltf-binary"
        return content_type if content_type and content_type.startswith("image/") else "image/png"
