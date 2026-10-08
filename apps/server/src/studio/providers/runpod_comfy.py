"""Our own RunPod serverless endpoints that run ComfyUI workflows (infra/runpod/wan-core).

Routes name a workflow template in workflows/comfy, the same kind the local engine runs. The provider spec's
`endpoint_setting` names the Settings field holding the RunPod endpoint id (e.g. RUNPOD_WAN_CORE_ENDPOINT).

Job contract (infra/runpod/wan-core/handler.py):
- in:  {"workflow": <API graph>, "inputs": [{"name", "url"} | {"name", "data"}]}. Media values in the payload
  are web links (fal storage) or data URIs from staging; submit swaps each for a file name and lists it in
  `inputs`, so the worker downloads it into ComfyUI's input folder.
- out: {"outputs": [{"node", "filename", "kind", "mime", "data": base64}], "gpu", "seconds"}. RunPod keeps the
  result for 30 minutes, so `download` re-reads it instead of the database holding the bytes.
Cost: the worker's run time (`executionTime`) plus the provider's `idle_seconds` (a worker stays billed that long after
its job), priced at `gpu_usd_per_hour` for the GPU it ran on. Still an estimate (back-to-back jobs share an idle tail);
the hourly reconcile corrects it to RunPod's bill.
It stays marked estimated: RunPod bills cold starts and idle time per worker, not per job.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any

import anyio
import httpx

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.media.ffmpeg import ffmpeg
from studio.providers.base import (
    AmbiguousSubmitError,
    PollResult,
    ProviderError,
    ProviderOutput,
    ResolvedMedia,
    SubmitResult,
)
from studio.providers.comfy_workflows import load_template
from studio.providers.common import http_error, looks_like_moderation
from studio.providers.runpod import RunPodAdapter
from studio.registry.schema import ProviderSpec, Route

NAME_MARK = "#name="  # staged links carry the file name the workflow should see; stripped before sending


class RunPodComfyAdapter(RunPodAdapter):
    key = "runpod_comfy"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, workflows_dir: Path | None = None) -> None:
        super().__init__(transport)
        self.workflows_dir = workflows_dir or get_settings().comfyui_workflows_dir

    # ------------------------------------------------------------------ config

    def _spec(self, route: Route) -> ProviderSpec:
        from studio.registry.loader import get_registry

        spec = get_registry().providers.get(route.provider)
        if spec is None:
            raise ProviderError(ErrorClass.internal, f"unknown provider {route.provider}")
        return spec

    def _endpoint(self, route: Route) -> str:
        spec = self._spec(route)
        endpoint = getattr(get_settings(), spec.endpoint_setting or "", "") if spec.endpoint_setting else ""
        if not endpoint:
            raise ProviderError(ErrorClass.route_unavailable,
                                f"{spec.name} has no endpoint id; set {(spec.endpoint_setting or '').upper()} in .env")
        return endpoint

    def _cost(self, route: Route, status: dict[str, Any], gpu: str | None) -> dict[str, Any]:
        ms = status.get("executionTime")
        spec = self._spec(route)
        rates = spec.gpu_usd_per_hour
        if not isinstance(ms, int | float) or not rates:
            return {}
        rate = next((usd for name, usd in rates.items() if gpu and name.lower() in gpu.lower()), max(rates.values()))
        seconds = ms / 1000 + spec.idle_seconds
        return {"cost_usd": round(seconds * rate / 3600, 6), "cost_estimated": True, "gpu": gpu,
                "execution_s": round(ms / 1000, 1), "idle_s": spec.idle_seconds}

    # ------------------------------------------------------------------ adapter

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        staged: dict[str, Any] = {}
        for m in media:
            fps = route.media_fps.get(m.role)
            if fps and m.kind in ("video", "mask_video"):
                url, name = await self._retimed_url(route, m, fps)
            else:
                url = await self._public_url(route, m)
                name = f"{m.asset_id}{m.path.suffix.lower() or mimetypes.guess_extension(m.mime) or ''}"
            staged.setdefault(m.role, []).append(url if url.startswith("data:") else f"{url}{NAME_MARK}{name}")
        return staged

    async def _retimed_url(self, route: Route, m: ResolvedMedia, fps: float) -> tuple[str, str]:
        """The clip re-timed to `fps` (same duration, frames dropped or repeated), hosted afresh: the per-asset upload
        cache holds the original, which other routes expect."""
        from studio.providers.factory import get_adapter

        if not get_settings().fal_key:
            raise ProviderError(ErrorClass.invalid_request, "RunPod needs a web link for this clip. Add FAL_KEY to .env.")
        name = f"{m.asset_id}-{fps:g}fps.mp4"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / name
            await ffmpeg("-y", "-i", str(m.path), "-vf", f"fps={fps:g}", "-an", "-c:v", "libx264", "-crf", "12",
                         "-pix_fmt", "yuv420p", str(out))
            try:
                url = await get_adapter("fal").client.upload_file(out)
            except Exception as exc:
                raise ProviderError(ErrorClass.transient, f"hosting the re-timed clip failed: {exc!r}") from exc
        return url, name

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        template = load_template(self.workflows_dir, endpoint)
        inputs: list[dict[str, str]] = []
        values = {k: _to_file(v, inputs) for k, v in payload.items()}
        values["filename_prefix"] = f"ai-media-studio/{time.strftime('%Y-%m-%d')}/{client_ref[:8]}"
        if "seed" in template.map.inputs and values.get("seed") is None:
            values["seed"] = secrets.randbelow(2**32)  # a fresh take each time unless the user pinned a seed
        graph, warnings = template.render(values)
        runpod_endpoint = self._endpoint(route)
        try:
            async with self._client() as c:
                r = await c.post(f"/{runpod_endpoint}/run", json={"input": {"workflow": graph, "inputs": inputs}})
        except httpx.TimeoutException as exc:
            raise AmbiguousSubmitError(f"RunPod submit timed out: {exc!r}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, f"RunPod submit failed before sending: {exc!r}") from exc
        if r.status_code not in (200, 201):
            raise http_error("RunPod", r)
        job = r.json().get("id")
        if not job:
            raise AmbiguousSubmitError(f"RunPod accepted the request but returned no job id: {r.text[:300]}")
        return SubmitResult(job_id=f"{runpod_endpoint}::{job}",
                            raw={"template": endpoint, "warnings": warnings, "seed": values.get("seed"),
                                 "inputs": [i["name"] for i in inputs]})

    async def _status(self, job_id: str) -> httpx.Response:
        endpoint, _, job = job_id.partition("::")
        try:
            async with self._client(timeout=120) as c:
                return await c.get(f"/{endpoint}/status/{job}")
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, f"RunPod status failed: {exc!r}") from exc

    async def poll(self, route: Route, job_id: str) -> PollResult:
        r = await self._status(job_id)
        if r.status_code == 404:
            return PollResult(status="failed", error_class=ErrorClass.internal,
                              error_message="RunPod no longer has this job (results are kept 30 minutes)")
        if r.status_code != 200:
            raise http_error("RunPod", r)
        t = r.json()
        status = t.get("status")
        output = t.get("output") if isinstance(t.get("output"), dict) else {}
        if status == "IN_QUEUE":
            return PollResult(status="queued", raw={"status": status, "delay_ms": t.get("delayTime")})
        if status == "IN_PROGRESS":
            return PollResult(status="running", raw={"status": status})
        usage = self._cost(route, t, output.get("gpu"))
        if status == "CANCELLED":
            return PollResult(status="canceled", usage=usage)
        if status == "TIMED_OUT":
            return PollResult(status="failed", error_class=ErrorClass.timeout, usage=usage,
                              error_message="The endpoint's time limit ran out before the workflow finished")
        if status == "FAILED" or output.get("error"):
            msg = str(t.get("error") or output.get("error") or "the job failed")[:1500]
            cls = ErrorClass.moderation if looks_like_moderation(msg) else ErrorClass.internal
            if "rejected the workflow" in msg or "bad input name" in msg:
                cls = ErrorClass.invalid_request
            return PollResult(status="failed", error_class=cls, error_message=f"wan-core: {msg}", usage=usage)
        if status != "COMPLETED":
            return PollResult(status="running", raw={"status": status})
        produced = [o for o in output.get("outputs") or [] if isinstance(o, dict)]
        if not produced:
            return PollResult(status="failed", error_class=ErrorClass.internal, usage=usage,
                              error_message="The worker finished without returning a file")
        outputs = [
            ProviderOutput(index=i, kind=o.get("kind") or "video", ref=f"{job_id}#{i}", mime=o.get("mime"),
                           meta={"filename": o.get("filename"), "bytes": o.get("bytes"), "node": o.get("node")})
            for i, o in enumerate(produced)
        ]
        return PollResult(status="succeeded", progress=1.0, outputs=outputs, usage=usage,
                          raw={"gpu": output.get("gpu"), "seconds": output.get("seconds")})

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        job_id, _, index = (output.ref or "").rpartition("#")
        r = await self._status(job_id)
        if r.status_code == 404:
            raise ProviderError(ErrorClass.ingest_error, "RunPod no longer has this result (it keeps them 30 minutes)")
        if r.status_code != 200:
            raise ProviderError(ErrorClass.ingest_error, f"RunPod status returned {r.status_code}")
        produced = ((r.json().get("output") or {}).get("outputs")) or []
        try:
            item = produced[int(index)]
            data = base64.b64decode(item["data"])
        except (IndexError, KeyError, ValueError, TypeError) as exc:
            raise ProviderError(ErrorClass.ingest_error, f"the job result has no output {index}") from exc
        await anyio.Path(dest).write_bytes(data)
        return item.get("mime") or output.mime or "application/octet-stream"


def _to_file(value: Any, inputs: list[dict[str, str]]) -> Any:
    """Swap staged links/data URIs for worker-side file names, recording what the worker must fetch."""
    if isinstance(value, list):
        return [_to_file(v, inputs) for v in value]
    if not isinstance(value, str):
        return value
    if value.startswith("data:"):
        mime = value[5:value.index(";")] if ";" in value else "application/octet-stream"
        name = f"{hashlib.sha256(value.encode()).hexdigest()[:16]}{mimetypes.guess_extension(mime) or ''}"
        inputs.append({"name": name, "data": value.split(",", 1)[1]})
        return name
    if value.startswith(("http://", "https://")) and NAME_MARK in value:
        url, _, name = value.partition(NAME_MARK)
        inputs.append({"name": name, "url": url})
        return name
    return value
