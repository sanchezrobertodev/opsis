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
from studio.media import mock_media, transcript
from studio.media.ffmpeg import probe
from studio.media.speech import estimate_words, spoken_seconds
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
        outputs = [ProviderOutput(index=i, kind=kind, ref=job_id, meta=dict(route.output.meta)) for i in range(count)]
        outputs += [ProviderOutput(index=count + i, kind=x.kind, ref=job_id, meta=dict(x.meta))
                    for i, x in enumerate(route.output.extras)]
        if kind == "audio" and _speaks(st["endpoint"]):
            # text to speech: time the words as if read aloud, like the real API's alignment
            outputs[0].meta = {"alignment": {"words": estimate_words(strip_tags(payload.get("prompt", "")))}}
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
            pano = output.meta.get("projection") == "equirectangular"
            await anyio.to_thread.run_sync(lambda: mock_media.make_image(
                dest, model_id=model_id, mode=mode, prompt=prompt, resolution=p.get("resolution"),
                aspect="2:1" if pano else p.get("aspect_ratio"), index=output.index, seed=seed,
            ))
            return "image/png"
        if output.kind == "mask":
            image = _first_path(p.get("input_image"))
            await anyio.to_thread.run_sync(lambda: mock_media.make_mask(dest, image=image, points=p.get("points")))
            return "image/png"
        if output.kind == "mask_video":
            await mock_media.make_mask_video(dest, workdir, mask=_first_path(p.get("mask")),
                                             video=_first_path(p.get("input_video")))
            return "video/mp4"
        if output.kind == "model3d":
            await anyio.to_thread.run_sync(lambda: mock_media.make_glb(dest, seed=seed))
            return "model/gltf-binary"
        if output.kind == "lora":  # stand-in weights: never loaded, only passed back to LoRA routes
            await anyio.Path(dest).write_bytes(b"MOCK-LORA " + str(output.meta.get("expert", "")).encode())
            return "application/octet-stream"
        if output.kind == "splat":
            await anyio.to_thread.run_sync(lambda: mock_media.make_splat_ply(dest, seed=seed))
            return "application/x-ply"
        if output.kind == "subtitle":  # a transcript: a sung "la" every half second, with a breath every four
            heard = _first_path(p.get("input_audio"))
            length = ((await probe(heard)).duration_ms or 5000) / 1000 if heard and heard.exists() else 5.0
            await anyio.Path(dest).write_text(json.dumps(transcript.mock(length)), encoding="utf-8")
            return "application/json"
        if output.kind == "audio":
            words = (output.meta.get("alignment") or {}).get("words")
            duration = spoken_seconds(words) if words else float(p.get("duration") or 5)
            await mock_media.make_audio(dest, workdir, duration=duration, seed=seed)
            return "audio/mpeg"
        # lipsync and talking portraits: as long as the speech they were given, playing it
        voice = _first_path(p.get("input_audio"))
        duration = float(p.get("duration") or 5)
        if voice is not None and voice.exists():
            duration = ((await probe(voice)).duration_ms or 5000) / 1000
        await mock_media.make_video(
            dest, workdir, model_id=model_id, mode=mode, prompt=prompt,
            duration=duration, resolution=p.get("resolution"),
            aspect=p.get("aspect_ratio") or "16:9", audio=bool(p.get("generate_audio")), seed=seed,
            voice=voice if voice is not None and voice.exists() else None,
        )
        return "video/mp4"


def _speaks(endpoint: str) -> bool:
    """Whether a mock endpoint (`mock/<model>/<mode>`) stands in for text to speech."""
    from studio.registry.loader import get_registry

    model_id, _, mode_id = endpoint.removeprefix("mock/").partition("/")
    spec = get_registry().model(model_id)
    mode = spec.mode(mode_id) if spec else None
    return bool(mode and "tts" in mode.ops)


def _first_path(value: Any) -> Path | None:
    """Mock inputs are staged as local paths, one list per role."""
    first = value[0] if isinstance(value, list) and value else value
    return Path(first) if isinstance(first, str) and first else None
