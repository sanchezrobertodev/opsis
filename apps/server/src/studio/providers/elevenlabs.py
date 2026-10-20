"""ElevenLabs adapter: text to speech with word timings, sound effects and music.

The API is synchronous: one POST returns the audio (and, for `.../with-timestamps`, a per-character
alignment). Submit makes that call and parks the result in a ResultStore, so poll and ingest work as for any
async provider. A timeout mid-call is an ambiguous paid submit and is never retried automatically.

Route endpoints are API paths under /v1, e.g. `text-to-speech/{voice_id}/with-timestamps`, `sound-generation`,
`music` and `speech-to-speech/{voice_id}`. The payload is the JSON body; `output_format` always asks for 128 kbps MP3.
Routes that take a recording (the voice changer) get it staged as a local file and sent as a multipart upload.

Dubbing (`dubbing/project`) is the one asynchronous call: submit starts a project with its first target language and
poll follows that language until its dubbed audio (FLAC, a signed link valid an hour) is ready; download re-reads the
language for a fresh link. It can't be canceled: the project is charged when it's made.

Voice cloning (`clone_voice`, `delete_voice`) isn't a generation: it makes a voice on the account and returns its id.
"""

from __future__ import annotations

import base64
import contextlib
import json
import mimetypes
import uuid
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
from studio.providers.common import ResultStore, best_mime, download_url, http_error, require_key
from studio.registry.schema import Route

BASE_URL = "https://api.elevenlabs.io/v1"
OUTPUT_FORMAT = "mp3_44100_128"
TIMEOUT = httpx.Timeout(300.0, connect=15.0)  # long narrations take a while to render
SYNC_PREFIX = "sync:"
DUB_ENDPOINT = "dubbing/project"
DUB_PREFIX = "dub:"
STAGED_PREFIX = "staged-file:"  # a staged input: a random token this adapter issued for a local file it staged


def _key() -> str:
    return require_key("ElevenLabs", get_settings().elevenlabs_api_key)


async def clone_voice(name: str, files: list[Path], *, remove_background_noise: bool = False,
                      transport: httpx.AsyncBaseTransport | None = None) -> str:
    """An instant voice clone from recordings; returns its voice id. Free per call, but it takes a voice slot."""
    parts = [("files", (p.name, await anyio.Path(p).read_bytes(), mimetypes.guess_type(p.name)[0] or "audio/mpeg"))
             for p in files]
    async with httpx.AsyncClient(base_url=BASE_URL, headers={"xi-api-key": _key()}, timeout=TIMEOUT,
                                 transport=transport) as c:
        r = await c.post("/voices/add", data={"name": name, "remove_background_noise": str(remove_background_noise).lower()},
                         files=parts)
    if r.status_code != 200:
        raise http_error("ElevenLabs", r)
    return r.json()["voice_id"]


async def delete_voice(voice_id: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Remove a cloned voice from the account, freeing its slot."""
    async with httpx.AsyncClient(base_url=BASE_URL, headers={"xi-api-key": _key()}, timeout=TIMEOUT,
                                 transport=transport) as c:
        r = await c.delete(f"/voices/{voice_id}")
    if r.status_code not in (200, 404):
        raise http_error("ElevenLabs", r)


class ElevenLabsAdapter(ProviderAdapter):
    key = "elevenlabs"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self.store = ResultStore("elevenlabs")
        # token -> file, for inputs staged by this adapter; a payload string is uploaded only if it's one of these,
        # so a prompt that happens to look like a path is never read from disk
        self._staged: dict[str, Path] = {}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=BASE_URL, headers={"xi-api-key": _key()}, timeout=TIMEOUT,
                                 transport=self._transport)

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        staged: dict[str, Any] = {}
        for m in media:
            if m.kind != "audio":
                raise ProviderError(ErrorClass.invalid_request, "ElevenLabs takes recordings as audio files")
            token = f"{STAGED_PREFIX}{uuid.uuid4().hex}"
            self._staged[token] = m.path
            staged.setdefault(m.role, []).append(token)
        return staged

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        try:
            uploads = {k: path for k, v in payload.items() if (path := self._staged_path(v))}
            if endpoint == DUB_ENDPOINT and payload.get("source_language") == "auto":
                payload = {k: v for k, v in payload.items() if k != "source_language"}  # left out, it's detected
            with contextlib.ExitStack() as opened:
                async with self._client() as c:
                    if uploads:  # a recording goes up as a file streamed from disk, the other fields as form values
                        files = {k: (p.name, opened.enter_context(p.open("rb")),
                                     mimetypes.guess_type(p.name)[0] or "application/octet-stream")
                                 for k, p in uploads.items()}
                        data = {k: v if isinstance(v, str) else json.dumps(v) if isinstance(v, (dict, list))
                                else str(v).lower() if isinstance(v, bool) else str(v)
                                for k, v in payload.items() if k not in uploads}
                        params = {} if endpoint == DUB_ENDPOINT else {"output_format": OUTPUT_FORMAT}
                        r = await c.post(f"/{endpoint}", params=params, data=data, files=files)
                    else:
                        r = await c.post(f"/{endpoint}", params={"output_format": OUTPUT_FORMAT}, json=payload)
        except httpx.TimeoutException as exc:
            raise AmbiguousSubmitError(f"ElevenLabs call timed out: {exc!r}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, f"ElevenLabs call failed: {exc!r}") from exc
        if r.status_code not in (200, 201):
            raise http_error("ElevenLabs", r)
        if endpoint == DUB_ENDPOINT:
            project = r.json()
            return SubmitResult(job_id=f"{DUB_PREFIX}{project['project_id']}",
                                raw={"endpoint": endpoint, "project_id": project["project_id"]})

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

    def _staged_path(self, value: Any) -> Path | None:
        first = value[0] if isinstance(value, list) and value else value
        return self._staged.pop(first, None) if isinstance(first, str) else None

    async def poll(self, route: Route, job_id: str) -> PollResult:
        if job_id.startswith(DUB_PREFIX):
            return await self._poll_dub(route, job_id.removeprefix(DUB_PREFIX))
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

    async def _poll_dub(self, route: Route, project_id: str) -> PollResult:
        try:
            async with self._client() as c:
                r = await c.get(f"/{DUB_ENDPOINT}/{project_id}")
                if r.status_code != 200:
                    raise http_error("ElevenLabs", r)
                project = r.json()
                ids = project.get("language_ids") or []
                if project.get("status") == "failed" or not ids:
                    lang: dict[str, Any] = {}
                else:
                    lr = await c.get(f"/{DUB_ENDPOINT}/{project_id}/language/{ids[0]}")
                    if lr.status_code != 200:
                        raise http_error("ElevenLabs", lr)
                    lang = lr.json()
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, f"ElevenLabs dubbing status failed: {exc!r}") from exc
        failed = project if project.get("status") == "failed" else lang if lang.get("status") == "failed" else None
        if failed is not None:
            return PollResult(status="failed", error_class=ErrorClass.internal,
                              error_message=f"ElevenLabs dubbing failed: {_error_text(failed.get('error'))}"[:900])
        link = (lang.get("outputs") or {}).get(route.output.path)
        if lang.get("status") != "completed" or not link:
            running = lang.get("status") == "processing" or project.get("status") == "preparing"
            return PollResult(status="running" if running else "queued",
                              raw={"project": project.get("status"), "language": lang.get("status")})
        out = ProviderOutput(index=0, kind=route.output.kind, url=link, ref=f"{project_id}/{ids[0]}",
                             mime="audio/flac", meta={"language": lang.get("target_language"),
                                                      "source_language": project.get("source_language")})
        seconds = (project.get("media") or {}).get("duration")
        return PollResult(status="succeeded", progress=1.0, outputs=[out],
                          usage={"seconds": seconds} if seconds else {})

    async def cancel(self, route: Route, job_id: str) -> bool:
        return False  # the call already finished (and billed) when submit returned; a dub is charged when it starts

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        if output.ref and output.url:  # a dub: its link lasts an hour, so ask for a fresh one (free)
            project_id, language_id = output.ref.split("/", 1)
            url = output.url
            try:
                async with self._client() as c:
                    r = await c.get(f"/{DUB_ENDPOINT}/{project_id}/language/{language_id}")
                if r.status_code == 200:
                    url = (r.json().get("outputs") or {}).get(route.output.path) or url
            except httpx.HTTPError:
                pass  # try the link we have
            return best_mime(await download_url(url, dest), output.mime, kind=output.kind)
        self.store.copy_out(output, dest)
        return "audio/mpeg"


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        return str(error.get("error") or error.get("message") or error)
    return str(error or "no reason given")


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
