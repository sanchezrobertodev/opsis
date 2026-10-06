"""Local video tools on this machine's ffmpeg: free, offline, no GPU model needed.

The route endpoint names the operation (`reframe`, `trim`, ...); params arrive by name. A job runs as a
background task in the worker process and parks its result in a ResultStore; polling reads it from there.
If the worker restarts mid-job the job is gone, so the poll reports a transient failure and the take is
retried, which is safe because local work costs nothing.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from studio.domain.enums import ErrorClass
from studio.media import tools
from studio.media.ffmpeg import FFMPEG, FFmpegError
from studio.providers.base import (
    PollResult,
    ProviderAdapter,
    ProviderError,
    ProviderOutput,
    ResolvedMedia,
    SubmitResult,
)
from studio.providers.common import ResultStore
from studio.registry.schema import Route

log = logging.getLogger(__name__)

Op = Callable[[Path, Path, dict[str, Any]], Awaitable[None]]

OPS: dict[str, Op] = {
    "reframe": lambda s, d, p: tools.reframe(s, d, p.get("aspect_ratio", "9:16"), p.get("fit", "crop")),
    "trim": lambda s, d, p: tools.trim(s, d, float(p.get("start", 0)), p.get("end")),
    "retime": lambda s, d, p: tools.retime(s, d, float(p.get("speed", 1))),
    "reverse": lambda s, d, p: tools.reverse(s, d),
    "loop": lambda s, d, p: tools.loop(s, d, int(p.get("times", 2)), bool(p.get("boomerang", False))),
    "interpolate": lambda s, d, p: tools.interpolate(s, d, int(p.get("fps", 60)), p.get("method", "motion")),
    "upscale": lambda s, d, p: tools.upscale(s, d, int(p.get("scale", 2)), bool(p.get("sharpen", True))),
    "stabilize": lambda s, d, p: tools.stabilize(s, d, p.get("strength", "gentle")),
}


class LocalToolsAdapter(ProviderAdapter):
    key = "local_tools"

    def __init__(self) -> None:
        self.store = ResultStore("local_tools")
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def reachable(self) -> bool:
        return shutil.which(FFMPEG) is not None

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        staged: dict[str, Any] = {}
        for m in media:
            staged.setdefault(m.role, []).append(str(m.path))
        return staged

    async def _run(self, op: Op, job_id: str, src: Path, params: dict[str, Any]) -> None:
        workdir = self.store.root / job_id
        try:
            await op(src, workdir / "output.mp4", params)
        except (ValueError, FFmpegError) as exc:
            msg = str(exc) if isinstance(exc, ValueError) else f"ffmpeg failed: {str(exc)[-400:]}"
            self.store.write_meta(job_id, {"status": "failed", "error": msg,
                                           "class": "invalid_request" if isinstance(exc, ValueError) else "internal"})
        except Exception as exc:
            log.exception("local tool job %s crashed", job_id)
            self.store.write_meta(job_id, {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "class": "internal"})
        else:
            self.store.write_meta(job_id, {"status": "succeeded"})

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        op = OPS.get(endpoint)
        if op is None:
            raise ProviderError(ErrorClass.invalid_request, f"no local tool called {endpoint!r}")
        src = payload.get("input_video")
        if isinstance(src, list):
            src = src[0] if src else None
        if not src or not Path(src).exists():
            raise ProviderError(ErrorClass.invalid_request, "this tool needs a video")
        params = {k: v for k, v in payload.items() if k != "input_video"}
        job_id, _ = self.store.new_job()
        task = asyncio.get_running_loop().create_task(self._run(op, job_id, Path(src), params))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t, j=job_id: self._tasks.pop(j, None))
        return SubmitResult(job_id=job_id, raw={"op": endpoint})

    async def poll(self, route: Route, job_id: str) -> PollResult:
        meta = self.store.meta(job_id)
        status = meta.get("status")
        if status == "succeeded":
            outputs = [o for o in self.store.outputs(job_id, route.output.kind) if o.ref and o.ref.endswith(".mp4")]
            return PollResult(status="succeeded", progress=1.0, outputs=outputs)
        if status == "failed":
            return PollResult(status="failed", error_class=ErrorClass(meta.get("class", "internal")),
                              error_message=meta.get("error", "the local tool failed"))
        if job_id not in self._tasks:
            return PollResult(status="failed", error_class=ErrorClass.transient,
                              error_message="the worker restarted while this ran; trying again")
        return PollResult(status="running")

    async def cancel(self, route: Route, job_id: str) -> bool:
        task = self._tasks.pop(job_id, None)
        if task:
            task.cancel()
        self.store.discard(job_id)
        return True

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        return self.store.copy_out(output, dest)
