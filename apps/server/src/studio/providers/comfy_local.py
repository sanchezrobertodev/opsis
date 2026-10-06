"""Local ComfyUI adapter (the VideoStudio install at C:/Dev/AI/VideoStudio).

Route endpoints name a workflow template in workflows/comfy. The canonical payload (built by
registry/mapping.py) is injected into the template; inputs are uploaded through /upload/image.
ComfyUI's HTTP API has no progress percentage, so the worker estimates progress from the route ETA.
"""

from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Any

import anyio
import httpx

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.providers.base import (
    AmbiguousSubmitError,
    PollResult,
    ProviderAdapter,
    ProviderError,
    ProviderOutput,
    ResolvedMedia,
    SubmitResult,
)
from studio.providers.comfy_workflows import load_template
from studio.registry.schema import Route

CLIENT_ID = "ai-media-studio"
INPUT_SUBFOLDER = "ai-media-studio"
OUTPUT_KEYS = ("images", "gifs", "videos", "video", "audio")


class ComfyLocalAdapter(ProviderAdapter):
    key = "comfy_local"

    def __init__(self, base_url: str | None = None, workflows_dir: Path | None = None) -> None:
        settings = get_settings()
        self.base = (base_url or settings.comfyui_url).rstrip("/")
        self.workflows_dir = workflows_dir or settings.comfyui_workflows_dir

    async def reachable(self) -> bool:
        from studio.providers.local_status import comfy_stats

        return bool(self.base) and (await comfy_stats(self.base)) is not None

    # ------------------------------------------------------------------ http

    def _client(self, timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base, timeout=timeout)

    def _offline(self) -> ProviderError:
        return ProviderError(
            ErrorClass.route_unavailable,
            f"ComfyUI isn't running at {self.base}. Start Local Video Studio, then try again.",
        )

    async def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with self._client() as c:
                r = await c.get(path, **kwargs)
        except httpx.ConnectError:
            raise self._offline() from None
        except httpx.TimeoutException as exc:
            raise ProviderError(ErrorClass.transient, f"ComfyUI timed out on {path}") from exc
        if r.status_code >= 500:
            raise ProviderError(ErrorClass.transient, f"ComfyUI {path} returned {r.status_code}")
        return r

    # ------------------------------------------------------------------ adapter

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        staged: dict[str, Any] = {}
        for m in media:
            if m.kind != "image":
                raise ProviderError(ErrorClass.invalid_request, f"the local engine can't take {m.kind} inputs yet")
            name = f"{m.asset_id}{m.path.suffix.lower() or '.png'}"
            data = await anyio.Path(m.path).read_bytes()
            try:
                async with self._client(timeout=60) as c:
                    r = await c.post(
                        "/upload/image",
                        files={"image": (name, data, m.mime)},
                        data={"subfolder": INPUT_SUBFOLDER, "type": "input", "overwrite": "true"},
                    )
            except httpx.ConnectError:
                raise self._offline() from None
            if r.status_code != 200:
                raise ProviderError(ErrorClass.transient, f"ComfyUI rejected the upload ({r.status_code})")
            body = r.json()
            ref = f"{body['subfolder']}/{body['name']}" if body.get("subfolder") else body["name"]
            staged.setdefault(m.role, []).append(ref)
        return staged

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        template = load_template(self.workflows_dir, endpoint)
        values = {**payload, "filename_prefix": f"ai-media-studio/{time.strftime('%Y-%m-%d')}/{client_ref[:8]}"}
        graph, warnings = template.render(values)
        try:
            async with self._client(timeout=30) as c:
                r = await c.post("/prompt", json={"prompt": graph, "client_id": CLIENT_ID})
        except httpx.ConnectError:
            raise self._offline() from None
        except httpx.TimeoutException as exc:
            raise AmbiguousSubmitError(f"ComfyUI didn't answer the submit: {exc!r}") from exc
        if r.status_code == 400:
            raise ProviderError(ErrorClass.invalid_request, f"ComfyUI rejected the workflow: {_node_errors(r)}")
        if r.status_code != 200:
            raise ProviderError(ErrorClass.transient, f"ComfyUI /prompt returned {r.status_code}")
        body = r.json()
        return SubmitResult(job_id=body["prompt_id"], raw={"number": body.get("number"), "warnings": warnings,
                                                           "template": endpoint})

    async def poll(self, route: Route, job_id: str) -> PollResult:
        done = await self._history(job_id)
        if done is not None:
            return self._finished(route, job_id, done)
        q = (await self._get("/queue")).json()
        running = [item[1] for item in q.get("queue_running", [])]
        pending = sorted(q.get("queue_pending", []), key=lambda item: item[0])
        if job_id in running:
            return PollResult(status="running", progress=None, raw={"running": True})
        for pos, item in enumerate(pending, start=1):
            if item[1] == job_id:
                return PollResult(status="queued", queue_position=pos, raw={"queue_position": pos})
        # It may have finished between the two calls.
        done = await self._history(job_id)
        if done is not None:
            return self._finished(route, job_id, done)
        return PollResult(status="failed", error_class=ErrorClass.route_unavailable,
                          error_message="ComfyUI no longer has this job (was it restarted?)")

    async def _history(self, job_id: str) -> dict[str, Any] | None:
        body = (await self._get(f"/history/{job_id}")).json()
        return body.get(job_id) if isinstance(body, dict) else None

    def _finished(self, route: Route, job_id: str, entry: dict[str, Any]) -> PollResult:
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            message, cls = "ComfyUI reported an error", ErrorClass.internal
            for kind, data in status.get("messages", []):
                if kind == "execution_error":
                    message = f"{data.get('node_type', 'node')}: {data.get('exception_message', '').strip()}"
                    if "out of memory" in message.lower() or "outofmemory" in message.lower():
                        message += " (try the draft quality or a shorter clip)"
                elif kind == "execution_interrupted":
                    return PollResult(status="canceled")
            return PollResult(status="failed", error_class=cls, error_message=message[:1500])
        # The history entry lists the output nodes it executed (prompt[4]); fall back to every node with output.
        prompt = entry.get("prompt") or []
        node_ids = [str(n) for n in prompt[4]] if len(prompt) > 4 and prompt[4] else list(entry.get("outputs", {}))
        outputs: list[ProviderOutput] = []
        for node_id in node_ids:
            produced = entry.get("outputs", {}).get(node_id, {})
            for key in OUTPUT_KEYS:
                for item in produced.get(key, []) or []:
                    if not isinstance(item, dict) or "filename" not in item or item.get("type") == "temp":
                        continue
                    mime = mimetypes.guess_type(item["filename"])[0] or ""
                    kind = mime.split("/")[0] if mime.split("/")[0] in ("video", "image", "audio") else "image"
                    outputs.append(ProviderOutput(index=len(outputs), kind=kind, ref=json.dumps(item), mime=mime or None))
        if not outputs:
            return PollResult(status="failed", error_class=ErrorClass.internal,
                              error_message="ComfyUI finished without saving an output")
        return PollResult(status="succeeded", progress=1.0, outputs=outputs, raw={"prompt_id": job_id})

    async def cancel(self, route: Route, job_id: str) -> bool:
        try:
            q = (await self._get("/queue")).json()
            async with self._client() as c:
                if any(item[1] == job_id for item in q.get("queue_running", [])):
                    await c.post("/interrupt", json={"prompt_id": job_id})
                else:
                    await c.post("/queue", json={"delete": [job_id]})
        except (ProviderError, httpx.HTTPError):
            return False
        return True

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        item = json.loads(output.ref or "{}")
        params = {"filename": item["filename"], "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")}
        try:
            async with self._client(timeout=300) as c, c.stream("GET", "/view", params=params) as r:
                if r.status_code != 200:
                    raise ProviderError(ErrorClass.ingest_error, f"ComfyUI /view returned {r.status_code}")
                async with await anyio.open_file(dest, "wb") as fh:
                    async for chunk in r.aiter_bytes(1 << 20):
                        await fh.write(chunk)
        except httpx.ConnectError:
            raise self._offline() from None
        return output.mime or mimetypes.guess_type(item["filename"])[0] or "application/octet-stream"


def _node_errors(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:500]
    parts = []
    err = body.get("error") or {}
    if err.get("message"):
        parts.append(err["message"])
    for node_id, info in (body.get("node_errors") or {}).items():
        for e in info.get("errors", []):
            parts.append(f"node {node_id} ({info.get('class_type')}): {e.get('message')} {e.get('details', '')}".strip())
    return "; ".join(parts)[:1500] or "invalid workflow"
