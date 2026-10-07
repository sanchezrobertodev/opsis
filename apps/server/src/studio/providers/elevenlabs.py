"""ElevenLabs adapter: text to speech with word timings, sound effects and music.

The API is synchronous: one POST returns the audio (and, for `.../with-timestamps`, a per-character
alignment). Submit makes that call and parks the result in a ResultStore, so poll and ingest work as for any
async provider. A timeout mid-call is an ambiguous paid submit and is never retried automatically.

Route endpoints are API paths under /v1, e.g. `text-to-speech/{voice_id}/with-timestamps`, `sound-generation`
and `music`. The payload is the JSON body; `output_format` always asks for 128 kbps MP3.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import anyio
import httpx

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.media.speech import words_from_characters
from studio.providers.base import (
    AmbiguousSubmitError,
    PollResult,
    ProviderAdapter,
    ProviderError,
    ProviderOutput,
    ResolvedMedia,
    SubmitResult,
)
from studio.providers.common import ResultStore, http_error, require_key
from studio.registry.schema import Route

BASE_URL = "https://api.elevenlabs.io/v1"
OUTPUT_FORMAT = "mp3_44100_128"
TIMEOUT = httpx.Timeout(300.0, connect=15.0)  # long narrations take a while to render
SYNC_PREFIX = "sync:"


class ElevenLabsAdapter(ProviderAdapter):
    key = "elevenlabs"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self.store = ResultStore("elevenlabs")

    def _client(self) -> httpx.AsyncClient:
        key = require_key("ElevenLabs", get_settings().elevenlabs_api_key)
        return httpx.AsyncClient(base_url=BASE_URL, headers={"xi-api-key": key}, timeout=TIMEOUT,
                                 transport=self._transport)

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        if media:
            raise ProviderError(ErrorClass.invalid_request, "ElevenLabs routes here take text only")
        return {}

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        try:
            async with self._client() as c:
                r = await c.post(f"/{endpoint}", params={"output_format": OUTPUT_FORMAT}, json=payload)
        except httpx.TimeoutException as exc:
            raise AmbiguousSubmitError(f"ElevenLabs call timed out: {exc!r}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, f"ElevenLabs call failed: {exc!r}") from exc
        if r.status_code != 200:
            raise http_error("ElevenLabs", r)

        job_id, folder = self.store.new_job()
        meta: dict[str, Any] = {"request_id": r.headers.get("request-id"),
                                "characters": _int(r.headers.get("x-character-count"))}
        if r.headers.get("content-type", "").startswith("application/json"):
            body = r.json()
            audio = base64.b64decode(body.get("audio_base64") or "")
            align = body.get("alignment") or body.get("normalized_alignment") or {}
            if align.get("characters"):
                meta["alignment"] = {"words": words_from_characters(
                    align["characters"], align.get("character_start_times_seconds") or [],
                    align.get("character_end_times_seconds") or [])}
        else:
            audio = r.content
        if not audio:
            self.store.discard(job_id)
            raise ProviderError(ErrorClass.internal, "ElevenLabs returned no audio")
        await anyio.Path(folder / "00.mp3").write_bytes(audio)
        self.store.write_meta(job_id, meta)
        return SubmitResult(job_id=f"{SYNC_PREFIX}{job_id}", raw={"endpoint": endpoint, "request_id": meta["request_id"]})

    async def poll(self, route: Route, job_id: str) -> PollResult:
        store_id = job_id.removeprefix(SYNC_PREFIX)
        outputs = self.store.outputs(store_id, route.output.kind)
        if not outputs:
            return PollResult(status="failed", error_class=ErrorClass.internal,
                              error_message="ElevenLabs audio is missing from the local result store")
        meta = self.store.meta(store_id)
        if meta.get("alignment"):
            outputs[0].meta = {"alignment": meta["alignment"]}
        usage = {"characters": meta["characters"]} if meta.get("characters") is not None else {}
        return PollResult(status="succeeded", progress=1.0, outputs=outputs, usage=usage)

    async def cancel(self, route: Route, job_id: str) -> bool:
        return False  # the call already finished (and billed) when submit returned

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        self.store.copy_out(output, dest)
        return "audio/mpeg"


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
