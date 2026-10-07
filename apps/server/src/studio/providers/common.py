"""Shared adapter plumbing: error classification, output extraction, downloads, input staging, sync results."""

from __future__ import annotations

import json
import mimetypes
import shutil
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from studio.config import get_settings
from studio.db.models import AssetProviderUpload
from studio.db.session import session_scope
from studio.domain.enums import ErrorClass
from studio.providers.base import ProviderError, ProviderOutput, ResolvedMedia
from studio.registry.schema import OutputSpec

MODERATION_HINTS = (
    "moderation", "content policy", "content_policy", "safety", "nsfw", "prohibited", "sensitive content",
    "violat", "not allowed", "rai_", "responsible ai", "blocked by",
)


def looks_like_moderation(text: str | None) -> bool:
    t = (text or "").lower()
    return any(h in t for h in MODERATION_HINTS)


def classify_http(status: int, text: str = "") -> ErrorClass:
    """Map an HTTP failure to an error class (drives retry vs failover vs fail)."""
    if looks_like_moderation(text) and status in (400, 403, 422, 451):
        return ErrorClass.moderation
    if status in (401, 402, 403, 404):
        return ErrorClass.route_unavailable  # bad/missing key, out of credits, endpoint gone
    if status in (400, 409, 413, 415, 422):
        return ErrorClass.invalid_request
    if status in (408, 425, 429) or status >= 500:
        return ErrorClass.transient
    return ErrorClass.internal


def http_error(provider: str, r: httpx.Response) -> ProviderError:
    try:
        body = r.json()
        detail = body.get("detail") or body.get("error") or body.get("message") or body
    except ValueError:
        detail = r.text
    text = detail if isinstance(detail, str) else json.dumps(detail)[:800]
    hint = {401: " (check the API key)", 402: " (out of credits)", 403: " (key not allowed for this model)",
            429: " (rate limited)"}.get(r.status_code, "")
    return ProviderError(classify_http(r.status_code, text), f"{provider} {r.status_code}{hint}: {text[:800]}")


def require_key(provider_name: str, key: str) -> str:
    if not key:
        raise ProviderError(ErrorClass.route_unavailable, f"no API key configured for {provider_name}")
    return key


# ---------------------------------------------------------------- outputs


def dig(data: Any, path: str) -> Any:
    """Follow a dotted path (`video.url`, `data.0.url`) through dicts and lists."""
    node = data
    for part in path.split(".") if path else []:
        if node is None:
            return None
        if isinstance(node, list):
            node = node[int(part)] if part.isdigit() and int(part) < len(node) else None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def extract_outputs(result: Any, path: str, kind: str) -> list[ProviderOutput]:
    """Turn the value at `path` into outputs: a URL string, a {url, content_type} dict, or a list of either.
    `path` may list alternatives, `a | b`: the first that holds a value is used."""
    value = None
    for alternative in path.split("|"):
        value = dig(result, alternative.strip())
        if value:
            break
    items = value if isinstance(value, list) else [value]
    out: list[ProviderOutput] = []
    for item in items:
        url = item if isinstance(item, str) else (item or {}).get("url") if isinstance(item, dict) else None
        if not url:
            continue
        mime = (item.get("content_type") if isinstance(item, dict) else None) or mimetypes.guess_type(
            url.split("?")[0])[0]
        out.append(ProviderOutput(index=len(out), kind=kind, url=url, mime=mime))
    return out


def route_outputs(result: Any, spec: OutputSpec) -> list[ProviderOutput]:
    """The route's main output(s), then any extras it declares, each carrying the metadata the route gives it.
    An empty list means the main output is missing (extras alone don't count as a result)."""
    main = extract_outputs(result, spec.path, spec.kind)
    if not main:
        return []
    for o in main:
        o.meta = {**spec.meta, **o.meta}
    out = list(main)
    for extra in spec.extras:
        for o in extract_outputs(result, extra.path, extra.kind):
            out.append(ProviderOutput(index=len(out), kind=o.kind, url=o.url, mime=o.mime, meta=dict(extra.meta)))
    return out


async def download_url(url: str, dest: Path, *, headers: dict[str, str] | None = None,
                       timeout: float = 600) -> str | None:
    """Stream a URL to disk; return the Content-Type. Raises ProviderError(ingest_error) on failure."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c, \
                c.stream("GET", url, headers=headers or {}) as r:
            if r.status_code != 200:
                raise ProviderError(ErrorClass.ingest_error, f"download returned {r.status_code}")
            async with await anyio.open_file(dest, "wb") as fh:
                async for chunk in r.aiter_bytes(1 << 20):
                    await fh.write(chunk)
            return r.headers.get("content-type", "").split(";")[0] or None
    except httpx.HTTPError as exc:
        raise ProviderError(ErrorClass.ingest_error, f"download failed: {exc!r}") from exc


# 3D files: a specific type is kept; a vague one falls back so ingest names the file by its URL or its kind
NOT_MEDIA_MIME = {"model3d": "model/gltf-binary", "splat": "application/octet-stream"}


def best_mime(*candidates: str | None, kind: str) -> str:
    if kind in NOT_MEDIA_MIME:
        return next((c for c in candidates if c and c not in ("application/octet-stream", "binary/octet-stream")),
                    NOT_MEDIA_MIME[kind])
    for c in candidates:
        if c and c != "application/octet-stream" and c.split("/")[0] in ("video", "image", "audio"):
            return c
    return {"video": "video/mp4", "image": "image/png", "audio": "audio/mpeg", "mask": "image/png",
            "mask_video": "video/mp4"}[kind]


# ---------------------------------------------------------------- input staging (cached per provider)

Uploader = Callable[[ResolvedMedia], Awaitable[tuple[str, datetime | None]]]
REUSE_MARGIN = timedelta(hours=2)  # don't hand a provider a reference that may expire mid-job


async def staged_ref(provider: str, media: ResolvedMedia, upload: Uploader) -> str:
    """Provider-side reference for an input asset, uploading only if there's no fresh cached copy."""
    asset_id = uuid.UUID(media.asset_id)
    async with session_scope() as s:
        row = (await s.execute(select(AssetProviderUpload).where(
            AssetProviderUpload.asset_id == asset_id, AssetProviderUpload.provider == provider))).scalar_one_or_none()
        if row and (row.expires_at is None or row.expires_at > datetime.now(UTC) + REUSE_MARGIN):
            return row.remote_ref
    ref, expires_at = await upload(media)
    async with session_scope() as s:
        stmt = insert(AssetProviderUpload).values(
            id=uuid.uuid4(), asset_id=asset_id, provider=provider, remote_ref=ref, expires_at=expires_at)
        await s.execute(stmt.on_conflict_do_update(
            index_elements=["asset_id", "provider"], set_={"remote_ref": ref, "expires_at": expires_at}))
    return ref


# ---------------------------------------------------------------- results of synchronous APIs


class ResultStore:
    """Synchronous providers (e.g. Gemini image) return bytes in the submit call. We park them on disk under
    a job id so the normal poll -> ingest flow picks them up like any async job."""

    def __init__(self, provider: str) -> None:
        self.root = get_settings().resolve_media_root().parent / "provider_results" / provider

    def new_job(self) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex
        path = self.root / job_id
        path.mkdir(parents=True, exist_ok=True)
        return job_id, path

    def outputs(self, job_id: str, kind: str) -> list[ProviderOutput]:
        path = self.root / job_id
        files = sorted(p for p in path.glob("*") if p.is_file() and p.suffix != ".json") if path.exists() else []
        return [ProviderOutput(index=i, kind=kind, ref=str(p), mime=mimetypes.guess_type(p.name)[0])
                for i, p in enumerate(files)]

    def meta(self, job_id: str) -> dict[str, Any]:
        f = self.root / job_id / "meta.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}

    def write_meta(self, job_id: str, meta: dict[str, Any]) -> None:
        (self.root / job_id / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def copy_out(self, output: ProviderOutput, dest: Path) -> str:
        shutil.copyfile(output.ref or "", dest)
        return output.mime or "application/octet-stream"

    def discard(self, job_id: str) -> None:
        shutil.rmtree(self.root / job_id, ignore_errors=True)
