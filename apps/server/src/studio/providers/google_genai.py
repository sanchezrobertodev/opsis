"""Google Gemini API adapter (Veo video, Nano Banana images) via the official `google-genai` SDK.

- Video routes (`output.kind: video`): `generate_videos` starts a long-running operation; the job id is the
  operation name, polled with `operations.get`. Outputs are deleted by Google after 2 days.
- Image routes: `generate_content` is synchronous, so submit does the work and parks the bytes in a
  ResultStore; poll then reports success like any async job.
Inputs are sent inline (bytes), so nothing is uploaded ahead of time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import anyio
import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

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
from studio.providers.common import (
    ResultStore,
    classify_http,
    looks_like_moderation,
    require_key,
    submit_failure,
)
from studio.registry.schema import Route

VIDEO_CONFIG_KEYS = ("duration_seconds", "aspect_ratio", "resolution", "seed", "negative_prompt",
                     "person_generation", "number_of_videos", "enhance_prompt", "fps", "generate_audio")
IMAGE_SIZES = {"1k": "1K", "2k": "2K", "4k": "4K"}
SYNC_PREFIX = "sync:"
BLOCKED_REASONS = ("SAFETY", "PROHIBITED_CONTENT", "IMAGE_SAFETY", "BLOCKLIST", "SPII", "RECITATION")


class GoogleGenAIAdapter(ProviderAdapter):
    key = "google_genai"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client
        self._client_key: str | None = None  # the key it was made with; None for a client passed in
        self.store = ResultStore("google")

    @property
    def client(self) -> Any:
        key = get_settings().gemini_api_key
        if self._client is None or self._client_key not in (None, key):  # a key saved in Settings since
            self._client = genai.Client(api_key=require_key("Google Gemini API", key))
            self._client_key = key
        return self._client

    def _error(self, exc: Exception, action: str) -> ProviderError:
        if isinstance(exc, ProviderError):
            return exc
        if isinstance(exc, genai_errors.APIError):
            text = f"{exc.status or ''} {exc.message or ''}".strip()
            cls = ErrorClass.moderation if looks_like_moderation(text) else classify_http(exc.code or 500, text)
            return ProviderError(cls, f"Google {action} failed ({exc.code}): {text[:800]}")
        if isinstance(exc, ValueError):  # SDK-side validation (unsupported parameter for this API, etc.)
            return ProviderError(ErrorClass.invalid_request, f"Google {action}: {exc}")
        return ProviderError(ErrorClass.transient, f"Google {action} failed: {exc!r}")

    # ------------------------------------------------------------------ inputs

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        staged: dict[str, Any] = {}
        for m in media:
            if m.kind != "image":
                raise ProviderError(ErrorClass.invalid_request, f"Google routes here take images only, not {m.kind}")
            staged.setdefault(m.role, []).append({"inline": str(m.path), "mime": m.mime})
        return staged

    @staticmethod
    async def _image(ref: Any) -> types.Image | None:
        if not ref:
            return None
        if isinstance(ref, list):
            ref = ref[0]
        data = await anyio.Path(ref["inline"]).read_bytes()
        return types.Image(image_bytes=data, mime_type=ref["mime"])

    # ------------------------------------------------------------------ submit

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        if route.output.kind == "image":
            return await self._generate_images(endpoint, payload)
        config: dict[str, Any] = {k: payload[k] for k in VIDEO_CONFIG_KEYS if k in payload}
        last = await self._image(payload.get("last_frame"))
        if last is not None:
            config["last_frame"] = last
        refs = payload.get("reference_images") or []
        if refs:
            config["reference_images"] = [
                types.VideoGenerationReferenceImage(image=await self._image(r),
                                                    reference_type=types.VideoGenerationReferenceType.ASSET) for r in refs
            ]
        try:
            op = await self.client.aio.models.generate_videos(
                model=endpoint, prompt=payload.get("prompt"), image=await self._image(payload.get("image")),
                config=types.GenerateVideosConfig(**config),
            )
        except TimeoutError as exc:
            raise AmbiguousSubmitError(f"Google submit timed out: {exc!r}") from exc
        except httpx.HTTPError as exc:
            raise submit_failure("Google", exc) from exc
        except Exception as exc:
            raise self._error(exc, "submit") from exc
        return SubmitResult(job_id=op.name, raw={"model": endpoint})

    async def _generate_images(self, model: str, payload: dict[str, Any]) -> SubmitResult:
        contents: list[Any] = [payload.get("prompt", "")]
        for ref in payload.get("images") or []:
            data = await anyio.Path(ref["inline"]).read_bytes()
            contents.append(types.Part.from_bytes(data=data, mime_type=ref["mime"]))
        image_config: dict[str, Any] = {}
        if payload.get("aspect_ratio"):
            image_config["aspect_ratio"] = payload["aspect_ratio"]
        if payload.get("resolution") in IMAGE_SIZES:
            image_config["image_size"] = IMAGE_SIZES[payload["resolution"]]
        config = types.GenerateContentConfig(response_modalities=["IMAGE"],
                                             image_config=types.ImageConfig(**image_config))
        job_id, folder = self.store.new_job()
        count = int(payload.get("num_images") or 1)
        # all at once, so four pictures take as long as one (they used to queue up past the worker's submit limit)
        calls = await asyncio.gather(*(self.client.aio.models.generate_content(model=model, contents=contents,
                                                                               config=config) for _ in range(count)),
                                     return_exceptions=True)
        written, blocked = 0, None
        for i, resp in enumerate(calls):
            if isinstance(resp, BaseException):
                continue
            image_part = None
            for cand in resp.candidates or []:
                for part in (cand.content.parts if cand.content else None) or []:
                    if getattr(part, "thought", False):
                        continue  # interim "thought" images, not the answer
                    if part.inline_data and part.inline_data.data:
                        image_part = part
                        break
                if image_part is None and cand.finish_reason and str(cand.finish_reason).split(".")[-1] in BLOCKED_REASONS:
                    blocked = str(cand.finish_reason)
            if image_part is None:
                fb = getattr(resp, "prompt_feedback", None)
                if fb and getattr(fb, "block_reason", None):
                    blocked = str(fb.block_reason)
                continue
            mime = image_part.inline_data.mime_type or "image/png"
            ext = ".jpg" if "jpeg" in mime else ".webp" if "webp" in mime else ".png"
            await anyio.Path(folder / f"{i:02d}{ext}").write_bytes(image_part.inline_data.data)
            written += 1
        errors = [r for r in calls if isinstance(r, BaseException)]
        if errors and not written:  # nothing paid for to keep: say why the first call failed
            self.store.discard(job_id)
            exc = errors[0]
            if isinstance(exc, TimeoutError):
                raise AmbiguousSubmitError(f"Google image call timed out: {exc!r}") from exc
            if isinstance(exc, httpx.HTTPError):
                raise submit_failure("Google", exc) from exc
            raise self._error(exc, "image generation") from exc  # type: ignore[arg-type]
        self.store.write_meta(job_id, {"written": written, "blocked": blocked, "requested": count})
        return SubmitResult(job_id=f"{SYNC_PREFIX}{job_id}", raw={"model": model, "images": written})

    # ------------------------------------------------------------------ poll / cancel / download

    async def poll(self, route: Route, job_id: str) -> PollResult:
        if job_id.startswith(SYNC_PREFIX):
            store_id = job_id.removeprefix(SYNC_PREFIX)
            outputs = self.store.outputs(store_id, route.output.kind)
            if outputs:
                return PollResult(status="succeeded", progress=1.0, outputs=outputs)
            meta = self.store.meta(store_id)
            if meta.get("blocked"):
                return PollResult(status="failed", error_class=ErrorClass.moderation,
                                  error_message=f"Google blocked the image ({meta['blocked']})")
            return PollResult(status="failed", error_class=ErrorClass.internal, error_message="Google returned no image")
        try:
            op = await self.client.aio.operations.get(types.GenerateVideosOperation(name=job_id))
        except Exception as exc:
            raise self._error(exc, "status") from exc
        if not op.done:
            return PollResult(status="running", raw={"done": False})
        if op.error:
            text = str(op.error.get("message") if isinstance(op.error, dict) else op.error)
            cls = ErrorClass.moderation if looks_like_moderation(text) else ErrorClass.internal
            return PollResult(status="failed", error_class=cls, error_message=f"Google: {text[:800]}")
        response = op.response or op.result
        videos = (response.generated_videos if response else None) or []
        outputs = [ProviderOutput(index=i, kind="video", ref=v.video.uri, mime=v.video.mime_type or "video/mp4")
                   for i, v in enumerate(videos) if v.video and v.video.uri]
        if not outputs:
            reasons = (response.rai_media_filtered_reasons if response else None) or []
            if reasons or (response and response.rai_media_filtered_count):
                return PollResult(status="failed", error_class=ErrorClass.moderation,
                                  error_message=f"Google filtered the video: {'; '.join(reasons)[:600]}")
            return PollResult(status="failed", error_class=ErrorClass.internal, error_message="Google returned no video")
        return PollResult(status="succeeded", progress=1.0, outputs=outputs)

    async def cancel(self, route: Route, job_id: str) -> bool:
        # The Gemini API has no cancel for video operations; the job finishes (and bills) on its own.
        return False

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        if output.ref and not output.ref.startswith(("http", "files/", "gs://")) and Path(output.ref).exists():
            return self.store.copy_out(output, dest)
        try:
            data = await self.client.aio.files.download(file=types.Video(uri=output.ref))
        except Exception as exc:
            err = self._error(exc, "download")
            raise ProviderError(ErrorClass.ingest_error, err.message) from exc
        await anyio.Path(dest).write_bytes(data)
        return output.mime or "video/mp4"
