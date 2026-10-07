"""Local video tools on this machine's ffmpeg: free, offline, no GPU model needed.

The route endpoint names the operation (`reframe`, `trim`, ...); params arrive by name. A job runs as a
background task in the worker process that submitted it and parks its result in a ResultStore. While it runs it
touches a `running` file every few seconds, so a poll from any worker process (`--queues` can split submit and
poll) can tell a live job from one whose worker died. A dead job reports a transient failure and the take is
retried, which is safe because local work costs nothing. Cancel works across processes the same way, through a
`cancel` file the running job checks.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
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

HEARTBEAT_S = 5.0  # how often a running job touches its `running` file
STALE_S = 30.0  # a `running` file older than this means the worker running the job is gone

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

    def _flag(self, job_id: str, name: str) -> Path:
        return self.store.root / job_id / name

    async def _heartbeat(self, job_id: str) -> None:
        running, cancel = self._flag(job_id, "running"), self._flag(job_id, "cancel")
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            if cancel.exists():  # canceled from another worker process
                task = self._tasks.get(job_id)
                if task:
                    task.cancel()
                return
            running.touch()

    async def _run(self, op: Op, job_id: str, src: Path, params: dict[str, Any]) -> None:
        workdir = self.store.root / job_id
        beat = asyncio.create_task(self._heartbeat(job_id))
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
        finally:
            beat.cancel()
            self._flag(job_id, "running").unlink(missing_ok=True)
            if self._flag(job_id, "cancel").exists():
                self.store.discard(job_id)

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
        self._flag(job_id, "running").touch()  # before returning, so the first poll already sees it
        task = asyncio.get_running_loop().create_task(self._run(op, job_id, Path(src), params))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t, j=job_id: self._tasks.pop(j, None))
        return SubmitResult(job_id=job_id, raw={"op": endpoint})

    def _finished(self, route: Route, job_id: str) -> PollResult | None:
        meta = self.store.meta(job_id)
        status = meta.get("status")
        if status == "succeeded":
            outputs = [o for o in self.store.outputs(job_id, route.output.kind) if o.ref and o.ref.endswith(".mp4")]
            return PollResult(status="succeeded", progress=1.0, outputs=outputs)
        if status == "failed":
            return PollResult(status="failed", error_class=ErrorClass(meta.get("class", "internal")),
                              error_message=meta.get("error", "the local tool failed"))
        return None

    async def poll(self, route: Route, job_id: str) -> PollResult:
        done = self._finished(route, job_id)
        if done:
            return done
        try:
            age = time.time() - self._flag(job_id, "running").stat().st_mtime
        except FileNotFoundError:
            age = None
        if age is not None and age < STALE_S:
            return PollResult(status="running")
        # The job may have finished between the two reads (it writes meta, then drops the running file).
        return self._finished(route, job_id) or PollResult(
            status="failed", error_class=ErrorClass.transient,
            error_message="the worker running this tool stopped; trying again")

    async def cancel(self, route: Route, job_id: str) -> bool:
        task = self._tasks.pop(job_id, None)
        if task:
            task.cancel()
            self.store.discard(job_id)
        elif self._flag(job_id, "running").exists():
            self._flag(job_id, "cancel").touch()  # another worker process runs it; its heartbeat stops it
        else:
            self.store.discard(job_id)
        return True

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        return self.store.copy_out(output, dest)
