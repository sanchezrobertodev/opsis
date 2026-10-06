"""Mock provider: a $0 stand-in for every model, so the full product runs without spending.

Behaves like a real async provider: submit returns a job id, polls move through queued -> running ->
succeeded over a few seconds, and outputs are rendered on download with ffmpeg/Pillow at the exact
requested duration, aspect ratio and output count.

Failure injection via tags in the prompt:
  [[mock:fail=moderation]]         fails after running (also: invalid_request, timeout, transient)
  [[mock:fail=route_unavailable]]  rejected at submit (exercises failover)
  [[mock:ambiguous]]               submit outcome unknown (exercises the `unknown` state)
  [[mock:slow]]                    takes 4x longer
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import anyio

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.media import mock_media
from studio.providers.base import (
    AmbiguousSubmitError,
    PollResult,
    ProviderAdapter,
    ProviderError,
    ProviderOutput,
    ResolvedMedia,
    SubmitResult,
)
from studio.registry.schema import Route

TAG = re.compile(r"\[\[mock:([a-z_]+)(?:=([a-z_]+))?\]\]")


def parse_tags(prompt: str) -> dict[str, str]:
    return {m.group(1): m.group(2) or "1" for m in TAG.finditer(prompt or "")}


def strip_tags(prompt: str) -> str:
    return TAG.sub("", prompt or "").strip()


class MockAdapter(ProviderAdapter):
    key = "mock"

    def __init__(self) -> None:
        settings = get_settings()
        self.state_dir = settings.resolve_media_root().parent / "mock_jobs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.queue_s = settings.mock_queue_seconds
        self.run_s = settings.mock_run_seconds

    # Job state lives on disk so polls survive worker restarts, like a real remote queue.
    def _state_path(self, job_id: str) -> Path:
        return self.state_dir / f"{job_id}.json"

    def _load(self, job_id: str) -> dict[str, Any]:
        path = self._state_path(job_id)
        if not path.exists():
            raise ProviderError(ErrorClass.invalid_request, f"unknown mock job {job_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, job_id: str, state: dict[str, Any]) -> None:
        self._state_path(job_id).write_text(json.dumps(state), encoding="utf-8")

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        staged: dict[str, Any] = {}
        for m in media:
            staged.setdefault(m.role, []).append(str(m.path))
        return staged

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        tags = parse_tags(payload.get("prompt", ""))
        if tags.get("fail") == "route_unavailable":
            raise ProviderError(ErrorClass.route_unavailable, "mock: route unavailable (injected)")
        if "ambiguous" in tags:
            raise AmbiguousSubmitError("mock: connection dropped mid-submit (injected)")
        job_id = f"mock-{uuid.uuid4().hex[:16]}"
        slow = 4.0 if "slow" in tags else 1.0
        self._save(job_id, {
            "endpoint": endpoint,
            "payload": payload,
            "client_ref": client_ref,
            "submitted_at": time.time(),
            "queue_s": self.queue_s * slow,
            "run_s": self.run_s * slow,
            "fail": tags.get("fail"),
            "canceled": False,
        })
        return SubmitResult(job_id=job_id, raw={"status": "IN_QUEUE"})

    async def poll(self, route: Route, job_id: str) -> PollResult:
        st = self._load(job_id)
        if st["canceled"]:
            return PollResult(status="canceled")
        elapsed = time.time() - st["submitted_at"]
        if elapsed < st["queue_s"]:
            return PollResult(status="queued", progress=0.0, queue_position=1)
        run_elapsed = elapsed - st["queue_s"]
        if run_elapsed < st["run_s"]:
            return PollResult(status="running", progress=round(min(0.99, run_elapsed / st["run_s"]), 3))
        if st["fail"]:
            cls = ErrorClass(st["fail"]) if st["fail"] in ErrorClass.__members__ else ErrorClass.internal
            return PollResult(status="failed", error_class=cls, error_message=f"mock: injected {st['fail']} failure")
        payload = st["payload"]
        kind = route.output.kind
        count = int(payload.get("num_images") or 1) if kind == "image" else 1
        outputs = [ProviderOutput(index=i, kind=kind, ref=job_id) for i in range(count)]
        return PollResult(status="succeeded", progress=1.0, outputs=outputs, usage={"mock": True})

    async def cancel(self, route: Route, job_id: str) -> bool:
        st = self._load(job_id)
        st["canceled"] = True
        self._save(job_id, st)
        return True

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        st = self._load(output.ref or "")
        p = st["payload"]
        model_id, _, mode = st["endpoint"].removeprefix("mock/").partition("/")
        prompt = strip_tags(p.get("prompt", ""))
        seed = p.get("seed")
        if output.kind == "image":
            await anyio.to_thread.run_sync(lambda: mock_media.make_image(
                dest, model_id=model_id, mode=mode, prompt=prompt, resolution=p.get("resolution"),
                aspect=p.get("aspect_ratio"), index=output.index, seed=seed,
            ))
            return "image/png"
        if output.kind == "audio":
            await mock_media.make_audio(dest, workdir, duration=float(p.get("duration") or 5), seed=seed)
            return "audio/mpeg"
        await mock_media.make_video(
            dest, workdir, model_id=model_id, mode=mode, prompt=prompt,
            duration=float(p.get("duration") or 5), resolution=p.get("resolution"),
            aspect=p.get("aspect_ratio") or "16:9", audio=bool(p.get("generate_audio")), seed=seed,
        )
        return "video/mp4"
