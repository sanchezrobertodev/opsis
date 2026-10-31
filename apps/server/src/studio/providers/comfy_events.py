"""Live progress and preview frames from a ComfyUI's websocket (`/ws`), for the local ComfyUI adapter.

ComfyUI sends a job's events only to the client id it was queued under, so each process queues under its own id and
keeps one connection per ComfyUI open while it follows jobs there (it closes after IDLE_S without one). Progress
counts the graph's slow nodes (samplers and model upscales) equally up to 90 %; decoding and saving are the rest.
Previews are a sampler's latest frame, which ComfyUI sends when the job asks for them (`extra_data.preview_method`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import websockets

log = logging.getLogger(__name__)

CLIENT_ID = f"opsis-{uuid.uuid4().hex[:12]}"
IDLE_S = 300.0  # a job no poll asked about for this long is dropped; with none left the connection closes
RETRY_S = 5.0
SAMPLING_SHARE = 0.9  # of the bar; decoding and saving fill the rest
PREVIEW_IMAGE, PREVIEW_IMAGE_WITH_METADATA = 1, 4  # ComfyUI's binary event types (protocol.py)


def samplers(graph: dict[str, Any]) -> list[str]:
    """The nodes whose steps make up a job: samplers (KSampler, SamplerCustomAdvanced... take a latent_image) and
    model upscales (ImageUpscaleWithModel takes an upscale_model)."""
    return [nid for nid, node in graph.items() if {"latent_image", "upscale_model"} & set(node.get("inputs") or {})]


@dataclass
class _Job:
    samplers: list[str]  # empty when another process queued it: then every node that reports counts
    steps: dict[str, float] = field(default_factory=dict)  # node -> fraction of its steps done
    progress: float = 0.0
    preview: tuple[bytes, str] | None = None
    sent: int = 0  # previews handed out
    seen: int = 0  # previews received
    touched: float = field(default_factory=time.monotonic)


class Listener:
    def __init__(self, base_url: str) -> None:
        self.url = base_url.replace("https://", "wss://").replace("http://", "ws://") + f"/ws?clientId={CLIENT_ID}"
        self.jobs: dict[str, _Job] = {}
        self.current: str | None = None  # the job running now, for previews that don't name it
        self.task: asyncio.Task[None] | None = None

    def follow(self, prompt_id: str, nodes: list[str] | None = None) -> None:
        job = self.jobs.setdefault(prompt_id, _Job(samplers=list(nodes or [])))
        job.touched = time.monotonic()
        if nodes:
            job.samplers = list(nodes)
        loop = asyncio.get_running_loop()
        if self.task is None or self.task.done() or self.task.get_loop() is not loop:
            self.task = loop.create_task(self._run())

    def take(self, prompt_id: str) -> tuple[float | None, tuple[bytes, str] | None]:
        """The job's progress (None until a sampler has done a step: the estimate goes on meanwhile) and a preview
        frame not handed out before."""
        job = self.jobs.get(prompt_id)
        if job is None:
            return None, None
        job.touched = time.monotonic()
        preview = None
        if job.seen != job.sent:
            preview, job.sent = job.preview, job.seen
        return job.progress or None, preview

    def forget(self, prompt_id: str) -> None:
        self.jobs.pop(prompt_id, None)

    # ------------------------------------------------------------------ connection

    async def _run(self) -> None:
        while self._expire():
            try:
                async with websockets.connect(self.url, max_size=32 * 2**20, open_timeout=10) as ws:
                    await ws.send(json.dumps({"type": "feature_flags", "data": {"supports_preview_metadata": True}}))
                    while self._expire():
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30)
                        except TimeoutError:
                            continue
                        self.handle(message)
            except (OSError, TimeoutError, websockets.WebSocketException) as exc:
                log.debug("ComfyUI events at %s: %r; retrying", self.url, exc)
                await asyncio.sleep(RETRY_S)

    def _expire(self) -> bool:
        cutoff = time.monotonic() - IDLE_S
        for pid in [p for p, job in self.jobs.items() if job.touched < cutoff]:
            del self.jobs[pid]
        return bool(self.jobs)

    # ------------------------------------------------------------------ events

    def handle(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            self._preview(message)
            return
        try:
            msg = json.loads(message)
        except ValueError:
            return
        kind, data = msg.get("type"), msg.get("data") or {}
        pid = data.get("prompt_id")
        if kind in ("execution_start", "executing") and pid:
            self.current = pid if data.get("node", pid) is not None else None
        job = self.jobs.get(pid)
        if job is None:
            return
        if kind == "progress" and data.get("max"):
            self._step(job, str(data.get("node")), data.get("value", 0) / data["max"])
        elif kind == "progress_state":
            for nid, state in (data.get("nodes") or {}).items():
                if state.get("max"):
                    self._step(job, str(nid), state.get("value", 0) / state["max"])
        elif kind == "execution_cached":
            for nid in data.get("nodes") or []:
                if str(nid) in job.samplers:
                    self._step(job, str(nid), 1.0)

    def _step(self, job: _Job, node: str, done: float) -> None:
        if job.samplers and node not in job.samplers:
            return
        job.steps[node] = max(job.steps.get(node, 0.0), min(done, 1.0))
        share = sum(job.steps.values()) / (len(job.samplers) or len(job.steps))
        job.progress = max(job.progress, round(SAMPLING_SHARE * share, 3))

    def _preview(self, data: bytes) -> None:
        if len(data) < 8:
            return
        event, word = struct.unpack(">II", data[:8])
        if event == PREVIEW_IMAGE:  # image type (1 JPEG, 2 PNG), then the image
            image, mime, pid = data[8:], "image/png" if word == 2 else "image/jpeg", self.current
        elif event == PREVIEW_IMAGE_WITH_METADATA:  # metadata length, its JSON, then the image
            try:
                meta = json.loads(data[8:8 + word])
            except ValueError:
                return
            image, mime, pid = data[8 + word:], meta.get("image_type") or "image/jpeg", meta.get("prompt_id")
        else:
            return
        job = self.jobs.get(pid) if pid else None
        if job is not None and image:
            job.preview = (image, mime)
            job.seen += 1


_listeners: dict[str, Listener] = {}


def listener(base_url: str) -> Listener:
    return _listeners.setdefault(base_url, Listener(base_url))
