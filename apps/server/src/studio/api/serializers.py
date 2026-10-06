from __future__ import annotations

from studio.api.schemas import AssetOut, GenerationOut
from studio.db.models import Asset, Generation
from studio.storage.local import get_storage


def asset_out(a: Asset) -> AssetOut:
    storage = get_storage()
    d = a.derivatives or {}

    def url(name: str) -> str | None:
        key = d.get(name)
        return storage.url_for(key) if isinstance(key, str) else None

    return AssetOut(
        id=a.id, kind=a.kind.value, status=a.status.value, source=a.source.value, mime=a.mime,
        url=storage.url_for(a.storage_key), thumb_url=url("thumb"), poster_url=url("poster"),
        filmstrip_url=url("filmstrip"), filmstrip_frames=d.get("filmstrip_frames"), proxy_url=url("proxy"),
        width=a.width, height=a.height, duration_ms=a.duration_ms, fps=a.fps, has_audio=a.has_audio,
        size_bytes=a.size_bytes, title=a.title, tags=list(a.tags or []), favorite=a.favorite,
        generation_id=a.generation_id, output_index=a.output_index, metadata=a.metadata_ or {},
        created_at=a.created_at,
    )


def generation_out(g: Generation, assets: list[Asset] | None = None) -> GenerationOut:
    return GenerationOut(
        id=g.id, model_id=g.model_id, mode=g.mode, output_type=g.output_type, status=g.status.value,
        status_reason=g.status_reason, progress=g.progress, progress_is_estimated=g.progress_is_estimated,
        request=g.request or {}, routing=g.routing or {},
        estimated_cost_usd=float(g.estimated_cost_usd) if g.estimated_cost_usd is not None else None,
        actual_cost_usd=float(g.actual_cost_usd) if g.actual_cost_usd is not None else None,
        error_code=g.error_code.value if g.error_code else None, error_message=g.error_message,
        group_id=g.group_id, parent_generation_id=g.parent_generation_id, created_at=g.created_at,
        queued_at=g.queued_at, started_at=g.started_at, finished_at=g.finished_at,
        assets=[asset_out(a) for a in sorted(assets or [], key=lambda a: a.output_index or 0)],
    )
