"""Bounded, single-clip Reactor sessions, saved locally before normal ingest.

Reactor bills time holding a GPU, not output seconds. Reserve the token's entire lifetime before
submit; never retry any failure after connect was attempted. No browser credentials or live session
survive a successful submit. The optional SDK owns WebRTC and authenticated recording assembly.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import shutil
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.media.ffmpeg import FFMPEG, FFPROBE, ffmpeg, probe
from studio.providers.base import (
    AmbiguousSubmitError,
    PollResult,
    ProviderAdapter,
    ProviderError,
    ProviderOutput,
    ResolvedMedia,
    SubmitResult,
)
from studio.providers.common import ResultStore, require_key
from studio.registry.schema import Route

API = "https://api.reactor.inc"
MODEL = "reactor/fast-h3-omni"
MEDIA_FIELDS = {"reference_images": 9, "reference_audios": 3,
                "first_frame_image": 1, "last_frame_image": 1}


def _sdk():
    try:
        from reactor_sdk import Reactor
    except (ImportError, OSError) as exc:
        raise ProviderError(ErrorClass.route_unavailable,
                            "Install the optional Reactor SDK: uv sync --extra reactor") from exc
    return Reactor


class ReactorAdapter(ProviderAdapter):
    key = "reactor"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._staged: dict[str, Path] = {}
        self.store = ResultStore(self.key)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=API, timeout=10, transport=self._transport)

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        staged: dict[str, Any] = {}
        for m in media:
            if m.kind not in ("image", "audio"):
                raise ProviderError(ErrorClass.invalid_request, "Reactor accepts image and audio references")
            if m.kind == "image" and m.mime not in ("image/jpeg", "image/png", "image/webp"):
                raise ProviderError(ErrorClass.invalid_request, "Reactor images must be JPEG, PNG or WebP")
            token = f"reactor-file:{uuid.uuid4().hex}"
            self._staged[token] = m.path
            staged.setdefault(m.role, []).append(token)
        return staged

    def _inputs(self, payload: dict[str, Any]) -> dict[str, list[Path]]:
        """Only opaque tokens issued by staging can cause a local file read."""
        uploads: dict[str, list[Path]] = {}
        for field, maximum in MEDIA_FIELDS.items():
            value = payload.get(field)
            if value is None:
                continue
            tokens = value if isinstance(value, list) else [value]
            if not 1 <= len(tokens) <= maximum:
                raise ProviderError(ErrorClass.invalid_request, f"Invalid Reactor {field} count")
            paths = [self._staged.get(t) if isinstance(t, str) else None for t in tokens]
            if any(p is None or not p.is_file() for p in paths):
                raise ProviderError(ErrorClass.invalid_request, "Reactor input was not staged or is missing")
            uploads[field] = paths
        if any(k in uploads for k in ("first_frame_image", "last_frame_image", "reference_audios")) \
                and not uploads.get("reference_images"):
            raise ProviderError(ErrorClass.invalid_request, "Reactor keyframes and audio need an image reference")
        return uploads

    async def _authorize(self, route: Route, cap: int) -> tuple[str, Decimal]:
        """Read the live rate and mint ONE session with the exact budgeted server-side lifetime."""
        key = require_key("Reactor", get_settings().reactor_api_key)
        try:
            async with self._client() as c:
                r = await c.get("/pricing")
                r.raise_for_status()
                rate = next(m["rate"] for m in r.json()["models"] if m["name"] == "fast-h3-omni")
                usd = Decimal(rate["amount_per_sec_usd"])
                ceiling = Decimal(str(route.pricing.rates[0].usd))
                if (rate["currency_code"] != "USD" or rate["denomination"] != "second"
                        or not usd.is_finite() or not 0 < usd <= ceiling):
                    raise ValueError("rate changed")
                r = await c.post("/tokens", headers={"Reactor-API-Key": key}, json={
                    "authorization_details": [{"type": "session", "resources": {"models": {"match": [MODEL]}},
                                               "constraints": {"max_sessions": 1,
                                                               "max_session_duration_seconds": cap}}],
                })
                r.raise_for_status()
                token = r.json()["jwt"]
                if not isinstance(token, str) or not token:
                    raise ValueError("missing token")
                return token, usd
        except (httpx.HTTPError, ValueError, KeyError, TypeError, StopIteration, ArithmeticError) as exc:
            # Do not echo bodies, JWTs or SDK error strings into job logs.
            raise ProviderError(ErrorClass.route_unavailable,
                                "Reactor preflight failed: check the key, credit balance and current pricing; "
                                "no session was started") from exc

    async def _terminate(self, reactor: Any, session_id: str | None, token: str) -> bool:
        try:
            await asyncio.wait_for(reactor.disconnect(), timeout=5)
            return True
        except Exception:
            if session_id:
                try:
                    async with self._client() as c:
                        r = await c.delete(f"/sessions/{session_id}", headers={"Authorization": f"Bearer {token}"})
                    return r.status_code in (200, 204, 404, 410)
                except httpx.HTTPError:
                    pass
            return False  # The server token lifetime remains the hard stop, including if this process dies.

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        if endpoint != MODEL:
            raise ProviderError(ErrorClass.invalid_request, "Unsupported Reactor model")
        try:
            cap = int(payload["session_seconds"])
            seconds = float(payload["seconds"])
            if cap not in (30, 60, 120) or seconds not in (5.875, 8, 10.125, 12.25, 14.375):
                raise ValueError
            aspect = payload.get("aspect_ratio", "16:9")
            if aspect not in ("16:9", "1:1", "9:16", "4:3") or not str(payload.get("prompt", "")).strip():
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(ErrorClass.invalid_request, "Invalid Reactor duration, session cap, aspect or prompt") from exc
        uploads = self._inputs(payload)
        if not shutil.which(FFMPEG) or not shutil.which(FFPROBE):
            raise ProviderError(ErrorClass.route_unavailable, "Reactor saving requires ffmpeg and ffprobe")
        factory = _sdk()
        token, rate = await self._authorize(route, cap)
        reactor = factory(MODEL, jwt=token)
        job_id, folder = self.store.new_job()
        scratch = folder / "capture"
        scratch.mkdir()
        raw = scratch / "recording.mp4"
        messages: asyncio.Queue[dict] = asyncio.Queue()
        meta: dict[str, Any] = {"client_ref": client_ref, "session_seconds_cap": cap, "status": "connecting"}
        self.store.write_meta(job_id, meta)

        def on_message(message: dict) -> None:
            if message.get("type") in ("clip_generated", "clip_finished", "clip_failed", "command_error"):
                messages.put_nowait(message)

        def on_session(session_id: str | None) -> None:
            if session_id:
                meta["session_id"] = session_id
                self.store.write_meta(job_id, meta)

        async def wait_clip(event: str, clip_id: str) -> dict:
            while True:
                message = await messages.get()
                data = message.get("data") or {}
                if message["type"] == "command_error":
                    raise RuntimeError("Reactor refused a command")
                if (data.get("clip") or {}).get("clip_id") != clip_id:
                    continue
                if message["type"] == "clip_failed":
                    raise RuntimeError("Reactor generation failed")
                if message["type"] == event:
                    return data

        reactor.on("message", on_message)
        reactor.on("session_id_changed", on_session)
        started = time.monotonic()
        # Measured from connect attempt, conservatively including any free allocation wait. Never a receipt.
        terminated = False
        try:
            try:
                await asyncio.wait_for(reactor.connect(), timeout=60)
                on_session(reactor.session_id)
                async with asyncio.timeout(cap - 5):
                    command = {"prompt": payload["prompt"], "seconds": seconds, "metadata": client_ref}
                    if payload.get("seed") is not None:
                        command["seed"] = payload["seed"]
                    uploaded: dict[Path, Any] = {}
                    for field, paths in uploads.items():
                        refs = []
                        for path in paths:
                            if path not in uploaded:
                                uploaded[path] = await reactor.upload_file(path)
                            refs.append(uploaded[path])
                        command[field] = refs if field.startswith("reference_") else refs[0]
                    await reactor.send_command("set_canvas", {"aspect": aspect})
                    queued = await reactor.send_command("enqueue", command)
                    clip_id = queued["data"]["clip"]["clip_id"]
                    meta.update(status="generating", clip_id=clip_id)
                    self.store.write_meta(job_id, meta)
                    await wait_clip("clip_generated", clip_id)
                    await reactor.send_command("play", {"clip_id": clip_id})
                    await wait_clip("clip_finished", clip_id)
                    # One clip per session: capture its whole media timeline, never the generation wait.
                    recording = await reactor.request_recording()
                    await reactor.download(recording, raw, ready_timeout=10)
            finally:
                terminated = await self._terminate(reactor, meta.get("session_id"), token)
                meta["terminated"] = terminated
                meta["estimated_session_seconds"] = min(cap, max(1, math.ceil(time.monotonic() - started)))
                self.store.write_meta(job_id, meta)
                with contextlib.suppress(Exception):
                    reactor.close()
            if not terminated:
                raise RuntimeError("Session termination could not be confirmed")
            # Both tracks are mandatory: do not silently accept a mute or empty capture.
            await ffmpeg("-i", str(raw), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
                         "-movflags", "+faststart", str(folder / "00.mp4"), timeout=30)
            info = await probe(folder / "00.mp4")
            if not info.has_audio or not info.width or not info.duration_ms or info.duration_ms < (seconds - 0.25) * 1000:
                raise RuntimeError("Incomplete Reactor recording")
            meta.update(status="succeeded", usage={"cost_usd": float(rate * meta["estimated_session_seconds"]),
                                                   "cost_estimated": True})
            self.store.write_meta(job_id, meta)
            raw.unlink(missing_ok=True)
        except Exception as exc:
            # The GPU may have billed even when no clip arrived. Keep reservation and local recovery metadata.
            raise AmbiguousSubmitError(
                f"Reactor session may have billed; no automatic retry. Local recovery ID: {job_id}. "
                f"Session termination {'confirmed' if terminated else 'unconfirmed; server cap applies'}. "
                f"Failure: {type(exc).__name__}.") from exc
        finally:
            for field in MEDIA_FIELDS:
                value = payload.get(field, [])
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, str):
                        self._staged.pop(item, None)
        return SubmitResult(job_id=job_id, raw={"session_id": meta.get("session_id"), "clip_id": meta["clip_id"]})

    async def poll(self, route: Route, job_id: str) -> PollResult:
        meta = self.store.meta(job_id)
        outputs = self.store.outputs(job_id, "video")
        if meta.get("status") != "succeeded" or not outputs:
            raise ProviderError(ErrorClass.route_unavailable, "Reactor recording missing; reconcile in Reactor before retrying")
        return PollResult(status="succeeded", progress=1, outputs=outputs, usage=meta["usage"])

    async def cancel(self, route: Route, job_id: str) -> bool:
        return False  # Submit already terminated the bounded session; cancel during submit waits for its cap.

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        self.store.copy_out(output, dest)
        return "video/mp4"
