"""Generation lifecycle tasks: submit -> poll (self-rescheduling) -> ingest.

Each task does short DB transactions around provider calls; no transaction is held open while talking
to a provider. Status changes go through fsm.transition() only.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import shutil
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from studio.budget import guard
from studio.config import get_settings
from studio.db.base import uuid7
from studio.db.models import Asset, AssetLineage, Generation, GenerationAttempt
from studio.db.session import session_scope
from studio.domain import fsm
from studio.domain.enums import AssetKind, AssetSource, AssetStatus, AttemptStatus, ErrorClass, GenStatus
from studio.domain.generations import routing_env
from studio.domain.health import record_failure, record_success
from studio.media.ffmpeg import Probe, probe
from studio.providers.base import (
    AmbiguousSubmitError,
    PollResult,
    ProviderError,
    ProviderOutput,
    ResolvedMedia,
)
from studio.providers.factory import get_adapter
from studio.registry.loader import get_registry
from studio.registry.mapping import build_payload, load_hook
from studio.registry.pricing import eta_seconds
from studio.registry.routing import candidates
from studio.registry.schema import ModelSpec, Route
from studio.registry.validate import MediaRef, NormalizedRequest
from studio.storage.base import asset_key
from studio.storage.local import get_storage
from studio.worker.app import Q_INGEST, Q_POLL, Q_SUBMIT, app

log = logging.getLogger(__name__)

INFLIGHT = (AttemptStatus.submitting, AttemptStatus.submitted, AttemptStatus.running)
MAX_TRANSIENT_RETRIES = 3
MAX_INGEST_TRIES = 5
# preparing (uploading inputs, re-timing a clip) and submitting each get a limit; a submit must be allowed as long as
# the slowest adapter's own call (ElevenLabs renders a long song in one call of up to 300 s)
PREPARE_TIMEOUT_S = 900
SUBMIT_TIMEOUT_S = 330
POLL_TIMEOUT_S = 60
EXT = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov", "image/png": ".png",
       "image/jpeg": ".jpg", "image/webp": ".webp", "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/flac": ".flac",
       "model/gltf-binary": ".glb", "application/x-ply": ".ply", "application/json": ".json"}
# outputs ffmpeg can't read: kept as files, with the extension their kind implies when the mime type is vague
NOT_MEDIA = {"model3d": ".glb", "splat": ".spz", "lora": ".safetensors", "archive": ".zip", "subtitle": ".json"}


def _untitled(model_id: str, mode_id: str, gen_id: uuid.UUID) -> str:
    """A title for an output made without a prompt (a 3D model from a picture): the model and what it did."""
    spec = get_registry().model(model_id)
    mode = spec.mode(mode_id) if spec else None
    return f"{spec.name}: {mode.label}" if spec and mode else gen_id.hex


# ------------------------------------------------------------------ deferral helpers (API + worker)


async def _defer(task, lock: str, delay: float, **kwargs: Any) -> None:
    try:
        await task.configure(
            queueing_lock=lock, schedule_in={"seconds": delay} if delay > 0 else None
        ).defer_async(**kwargs)
    except AlreadyEnqueued:
        pass  # an identical job is already waiting; that one will do the work


async def defer_submit(generation_id: uuid.UUID, delay: float = 0) -> None:
    await _defer(gen_submit, f"submit:{generation_id}", delay, generation_id=str(generation_id))


async def defer_poll(attempt_id: uuid.UUID, delay: float = 0) -> None:
    await _defer(gen_poll, f"poll:{attempt_id}", delay, attempt_id=str(attempt_id))


async def defer_ingest(attempt_id: uuid.UUID, delay: float = 0) -> None:
    await _defer(gen_ingest, f"ingest:{attempt_id}", delay, attempt_id=str(attempt_id))


async def defer_cancel_remote(attempt_id: uuid.UUID) -> None:
    await gen_cancel_remote.defer_async(attempt_id=str(attempt_id))


# ------------------------------------------------------------------ helpers


def request_from(gen: Generation) -> NormalizedRequest:
    r = gen.request
    # The director's compiled prompt is what the provider gets; the user's own words stay in r["prompt"].
    return NormalizedRequest(
        model_id=r["model_id"], mode=r["mode"], prompt=r.get("compiled_prompt") or r.get("prompt", ""),
        negative_prompt=r.get("negative_prompt"), params=dict(r.get("params", {})),
        media=[MediaRef(role=m["role"], asset_id=m["asset_id"], kind=m.get("kind"), duration_ms=m.get("duration_ms"),
                        fps=m.get("fps"), width=m.get("width"), height=m.get("height")) for m in r.get("media", [])],
        warnings=list(r.get("warnings", [])),
    )


def _route_for(spec: ModelSpec | None, route_id: str) -> Route | None:
    return spec.route(route_id) if spec else None


def _scrub(value: Any) -> Any:
    """Keep provider payloads loggable: drop inline file data and long blobs."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str) and (value.startswith("data:") or len(value) > 2000):
        return f"<{len(value)} chars>"
    return value


def _poll_delay(attempt: GenerationAttempt, output_type: str, floor: float | None = None) -> float:
    s = get_settings()
    cap = 5.0 if output_type == "image" else s.poll_max_seconds
    delay = min(s.poll_initial_seconds * (1.5 ** max(0, attempt.poll_count - 1)), cap)
    return max(delay, floor or 0.0)  # some providers (Runway: 5 s) ask not to be polled faster


async def _resolve_media(req: NormalizedRequest) -> list[ResolvedMedia]:
    if not req.media:
        return []
    storage = get_storage()
    async with session_scope() as s:
        rows = {
            str(a.id): a for a in (await s.execute(
                select(Asset).where(Asset.id.in_([uuid.UUID(m.asset_id) for m in req.media]))
            )).scalars()
        }
    out = []
    for m in req.media:
        a = rows.get(m.asset_id)
        if a is None:
            raise ProviderError(ErrorClass.invalid_request, f"input asset {m.asset_id} disappeared")
        out.append(ResolvedMedia(role=m.role, asset_id=m.asset_id, path=storage.path_for(a.storage_key),
                                 mime=a.mime, kind=a.kind.value))
    return out


def _reported_cost(usage: dict[str, Any]) -> Decimal | None:
    cost = (usage or {}).get("cost_usd")
    return Decimal(str(cost)) if isinstance(cost, int | float) and cost > 0 else None


async def _handle_failure(
    s, gen: Generation, attempt: GenerationAttempt, route: Route | None,
    error: ErrorClass, message: str, *, accepted: bool, reported: Decimal | None = None,
) -> float | None:
    """Record a failed attempt and decide: retry same route, fail over, or fail. Returns resubmit delay.
    `reported` is what the provider says the failed run cost (GPU time is billed even when a job fails)."""
    attempt.status = AttemptStatus.failed
    attempt.error_class = error
    attempt.error_message = message[:4000]
    attempt.finished_at = datetime.now(UTC)
    charge = attempt.estimated_cost_usd if (route and route.charges_on_failure and accepted) else None
    if reported is not None:
        charge = reported
    await guard.settle(s, gen.user_id, attempt, charge, estimated=True)
    await record_failure(s, attempt.route_id, attempt.provider, error, message)

    routing = dict(gen.routing or {})
    retries = dict(routing.get("retries", {}))
    tried = list(routing.get("tried", []))
    allow_failover = bool(gen.request.get("allow_failover", True))

    if error == ErrorClass.transient and not accepted and retries.get(attempt.route_id, 0) < MAX_TRANSIENT_RETRIES:
        n = retries.get(attempt.route_id, 0) + 1
        retries[attempt.route_id] = n
        await fsm.transition(s, gen, GenStatus.queued, reason=f"retrying ({n}/{MAX_TRANSIENT_RETRIES})",
                             routing={**routing, "retries": retries})
        return float(2 ** n)

    failover_ok = error in (ErrorClass.transient, ErrorClass.route_unavailable, ErrorClass.timeout) and (
        not accepted or (allow_failover and not (route and route.charges_on_failure))
    )
    if failover_ok:
        tried.append(attempt.route_id)
        await fsm.transition(s, gen, GenStatus.queued, reason=f"failover after {error.value} on {attempt.route_id}",
                             routing={**routing, "tried": tried, "retries": retries})
        return 0.0

    await fsm.transition(s, gen, GenStatus.failed, error=(error, message))
    return None


# ------------------------------------------------------------------ submit


@app.task(name="gen.submit", queue=Q_SUBMIT)
async def gen_submit(generation_id: str) -> None:
    await run_submit(uuid.UUID(generation_id))


async def run_submit(gen_id: uuid.UUID) -> None:
    reg = get_registry()
    requeue_in: float | None = None

    # Phase 1: choose a route, reserve budget, create the attempt.
    async with session_scope() as s:
        gen = await s.get(Generation, gen_id, with_for_update=True)
        if gen is None or gen.status != GenStatus.queued:
            return
        spec = reg.model(gen.model_id)
        if spec is None:
            await fsm.transition(s, gen, GenStatus.failed,
                                 error=(ErrorClass.invalid_request, f"model {gen.model_id} is no longer in the registry"))
            return
        req = request_from(gen)
        env = await routing_env(s, reg, gen.user_id, at_most=gen.routing.get("provider_mode"))
        cands = candidates(reg, spec, req, env, gen.routing.get("policy", "cheapest_healthy"))
        tried = set(gen.routing.get("tried", []))
        eligible = [c for c in cands if c.eligible and c.route.id not in tried]
        limit = gen.routing.get("max_cost_usd")
        if limit is not None and eligible:  # a failover or reroute must still fit in what's left of the run's cap
            affordable = [c for c in eligible if (c.estimate.usd if c.estimate else Decimal("0")) <= Decimal(str(limit))]
            if not affordable:
                await fsm.transition(s, gen, GenStatus.failed, error=(
                    ErrorClass.invalid_request, f"every route left costs more than the ${limit:.2f} left in the run's cap"))
                return
            eligible = affordable
        if not eligible:
            why = "; ".join(f"{c.route.id}: {c.reason or 'already tried'}" for c in cands)
            await fsm.transition(s, gen, GenStatus.failed,
                                 error=(ErrorClass.route_unavailable, f"no eligible route left ({why})"))
            return
        chosen = eligible[0]
        route = chosen.route
        provider = reg.providers[route.provider]

        inflight = (await s.execute(
            select(func.count()).select_from(GenerationAttempt).where(
                GenerationAttempt.provider == route.provider, GenerationAttempt.status.in_(INFLIGHT))
        )).scalar_one()
        if inflight >= provider.concurrency:
            if gen.status_reason != "provider_concurrency":
                await fsm.transition(s, gen, GenStatus.queued, reason="provider_concurrency")
            requeue_in = 5.0
        else:
            attempt_no = (await s.execute(
                select(func.coalesce(func.max(GenerationAttempt.attempt_no), 0))
                .where(GenerationAttempt.generation_id == gen.id)
            )).scalar_one() + 1
            estimate = chosen.estimate.usd if chosen.estimate else Decimal("0")
            attempt = GenerationAttempt(
                id=uuid7(), generation_id=gen.id, attempt_no=attempt_no, route_id=route.id,
                provider=route.provider, status=AttemptStatus.submitting, estimated_cost_usd=estimate,
                cost_basis=chosen.estimate.as_dict() if chosen.estimate else {},
            )
            s.add(attempt)
            await s.flush()
            try:
                await guard.reserve(s, gen.user_id, attempt, estimate)
            except guard.BudgetBlocked as exc:
                await s.delete(attempt)
                await fsm.transition(s, gen, GenStatus.blocked_budget, reason=str(exc))
                return
            routing = {**gen.routing, "chosen": route.id, "candidates": [c.as_dict() for c in cands]}
            await fsm.transition(s, gen, GenStatus.submitting, routing=routing,
                                 estimated_cost_usd=estimate)
            attempt_id = attempt.id

    if requeue_in is not None:
        await defer_submit(gen_id, requeue_in)
        return

    # Phase 2: talk to the provider with no transaction open.
    adapter = get_adapter(provider.adapter)
    result = None
    endpoint: str | None = None
    payload: dict[str, Any] = {}
    warnings: list[str] = []
    failure: tuple[ErrorClass, str] | None = None
    ambiguous: str | None = None
    try:
        async def prepare() -> tuple[str, dict[str, Any], list[str]]:
            staged = await adapter.stage_inputs(route, await _resolve_media(req))
            return build_payload(spec, route, req, staged)

        endpoint, payload, warnings = await asyncio.wait_for(prepare(), PREPARE_TIMEOUT_S)
    except ProviderError as exc:
        failure = (exc.error_class, exc.message)
    except Exception as exc:  # staging/payload errors happen before the provider sees anything
        log.exception("preparing %s failed", gen_id)
        failure = (adapter.classify(exc), f"preparing request failed: {exc}")
    if failure is None:
        try:
            result = await asyncio.wait_for(
                adapter.submit(route, endpoint or "", payload, client_ref=str(attempt_id)), SUBMIT_TIMEOUT_S
            )
        except AmbiguousSubmitError as exc:
            ambiguous = exc.message
        except ProviderError as exc:
            failure = (exc.error_class, exc.message)
        except (TimeoutError, OSError) as exc:
            ambiguous = f"submit did not complete: {exc!r}"
        except Exception as exc:
            log.exception("submit %s raised", gen_id)
            ambiguous = f"submit raised unexpectedly: {exc!r}"

    # Phase 3: record the outcome.
    resubmit: float | None = None
    poll_after: float | None = None
    cancel_remote = False
    async with session_scope() as s:
        gen = await s.get(Generation, gen_id, with_for_update=True)
        attempt = await s.get(GenerationAttempt, attempt_id, with_for_update=True)
        assert gen is not None and attempt is not None
        attempt.payload = _scrub({"endpoint": endpoint, "body": payload})
        if gen.status in (GenStatus.canceled, GenStatus.failed):  # canceled, or marked failed while it was sending
            if ambiguous is not None:
                # it may have gone through and be billed: its money stays held and the take says to check
                attempt.status, attempt.error_message = AttemptStatus.unknown, ambiguous
                gen.error_message = (f"stopped while it was being sent, and the provider may have taken it (and may "
                                     f"bill it): check {route.provider}'s dashboard, then mark it checked. "
                                     f"{ambiguous}")[:2000]
            else:
                if result is not None:  # it was taken: cancel it there
                    attempt.provider_job_id = result.job_id
                    cancel_remote = True
                attempt.status = AttemptStatus.canceled
                await guard.settle(s, gen.user_id, attempt, None, estimated=True)
        elif result is not None:
            attempt.provider_job_id = result.job_id
            attempt.status = AttemptStatus.submitted
            attempt.submitted_at = datetime.now(UTC)
            attempt.last_response = _scrub(result.raw)
            attempt.next_poll_at = datetime.now(UTC) + timedelta(seconds=get_settings().poll_initial_seconds)
            await fsm.transition(s, gen, GenStatus.in_progress, reason="provider_queue",
                                 compiled={**(gen.compiled or {}), "endpoint": endpoint,
                                           # a multi-shot take sends its shots, not a prompt; keep their text
                                           "prompt": req.prompt or (gen.compiled or {}).get("prompt", ""),
                                           "payload_warnings": warnings})
            poll_after = get_settings().poll_initial_seconds
        elif ambiguous is not None:
            # The provider may have accepted (and may bill) this job. Keep the reservation; a human decides.
            attempt.status = AttemptStatus.unknown
            attempt.error_message = ambiguous
            await fsm.transition(s, gen, GenStatus.unknown, reason="submit outcome unknown",
                                 error=(ErrorClass.transient, ambiguous))
        else:
            assert failure is not None
            resubmit = await _handle_failure(s, gen, attempt, route, failure[0], failure[1], accepted=False)

    if cancel_remote:
        await defer_cancel_remote(attempt_id)
    if poll_after is not None:
        await defer_poll(attempt_id, poll_after)
    if resubmit is not None:
        await defer_submit(gen_id, resubmit)


# ------------------------------------------------------------------ poll


@app.task(name="gen.poll", queue=Q_POLL)
async def gen_poll(attempt_id: str) -> None:
    await run_poll(uuid.UUID(attempt_id))


def _estimated_progress(elapsed_s: float, output_type: str, route: Route, request: dict[str, Any]) -> float:
    """Providers that report no percentage (ComfyUI over HTTP, many APIs): elapsed time against the route ETA."""
    expected = eta_seconds(route.eta, request.get("params", {}), request.get("media"), request.get("mode")) or (
        15.0 if output_type == "image" else 90.0)
    return round(min(0.95, elapsed_s / max(expected, 1.0)), 3)


async def run_poll(attempt_id: uuid.UUID) -> None:
    reg = get_registry()
    async with session_scope() as s:
        attempt = await s.get(GenerationAttempt, attempt_id)
        if attempt is None or attempt.status not in (AttemptStatus.submitted, AttemptStatus.running):
            return
        gen = await s.get(Generation, attempt.generation_id)
        if gen is None or gen.status != GenStatus.in_progress:
            return
        route = _route_for(reg.model(gen.model_id), attempt.route_id)
        provider = reg.providers.get(attempt.provider)
        job_id = attempt.provider_job_id
    if route is None or provider is None or job_id is None:
        async with session_scope() as s:
            gen = await s.get(Generation, attempt.generation_id, with_for_update=True)
            attempt = await s.get(GenerationAttempt, attempt_id, with_for_update=True)
            if gen and attempt and gen.status == GenStatus.in_progress:
                await _handle_failure(s, gen, attempt, None, ErrorClass.internal,
                                      f"route {attempt.route_id} vanished from the registry", accepted=True)
        return

    adapter = get_adapter(provider.adapter)
    res: PollResult | None = None
    transient_error: str | None = None
    unreadable: ProviderError | None = None
    try:
        res = await asyncio.wait_for(adapter.poll(route, job_id), POLL_TIMEOUT_S)
    except ProviderError as exc:
        if exc.error_class == ErrorClass.transient:
            transient_error = exc.message
        else:  # the status call itself failed (a rotated key, a 404): the accepted job may still run and bill
            unreadable = exc
    except Exception as exc:  # network hiccup while polling: try again later
        transient_error = repr(exc)

    next_poll: float | None = None
    ingest = False
    resubmit: float | None = None
    cancel_remote = False
    async with session_scope() as s:
        gen = await s.get(Generation, attempt.generation_id, with_for_update=True)
        attempt = await s.get(GenerationAttempt, attempt_id, with_for_update=True)
        assert gen is not None and attempt is not None
        if gen.status != GenStatus.in_progress or attempt.status not in (AttemptStatus.submitted, AttemptStatus.running):
            return
        now = datetime.now(UTC)
        attempt.poll_count += 1
        elapsed = (now - (attempt.submitted_at or now)).total_seconds()
        timed_out = elapsed > route.timeout_s

        if unreadable is not None:
            # never fail over (a second paid job) while the first may still be running: a human decides
            attempt.status = AttemptStatus.unknown
            attempt.error_message = unreadable.message
            await fsm.transition(s, gen, GenStatus.unknown, reason="job status unreadable",
                                 error=(unreadable.error_class, unreadable.message))
        elif res is None or res.status in ("queued", "running"):
            if timed_out:
                cancel_remote = True
                resubmit = await _handle_failure(s, gen, attempt, route, ErrorClass.timeout,
                                                 f"no result after {int(elapsed)} s", accepted=True)
            else:
                if res is not None:
                    attempt.status = AttemptStatus.running if res.status == "running" else AttemptStatus.submitted
                    attempt.last_response = _scrub(res.raw)
                    reason = "provider_queue" if res.status == "queued" else None
                    if reason != gen.status_reason:
                        await fsm.transition(s, gen, GenStatus.in_progress, reason=reason)
                    if res.progress is not None:
                        if abs(res.progress - gen.progress) >= 0.01:
                            await fsm.report_progress(s, gen, res.progress, estimated=False)
                    elif res.status == "running":
                        await fsm.report_progress(
                            s, gen, _estimated_progress(elapsed, gen.output_type, route, gen.request),
                            estimated=True,
                        )
                else:
                    log.warning("poll %s transient error: %s", attempt_id, transient_error)
                next_poll = _poll_delay(attempt, gen.output_type, provider.poll_min_seconds)
                attempt.next_poll_at = now + timedelta(seconds=next_poll)
        elif res.status == "succeeded":
            attempt.status = AttemptStatus.succeeded
            attempt.finished_at = now
            attempt.latency_ms = int(elapsed * 1000)
            attempt.last_response = {"outputs": [asdict(o) for o in res.outputs], "usage": res.usage,
                                     "raw": _scrub(res.raw)}
            await fsm.transition(s, gen, GenStatus.ingesting, reason=None)
            ingest = True
        elif res.status == "canceled":
            attempt.status = AttemptStatus.canceled
            await guard.settle(s, gen.user_id, attempt, _reported_cost(res.usage), estimated=True)
            await fsm.transition(s, gen, GenStatus.canceled, reason="canceled by provider")
        else:
            resubmit = await _handle_failure(
                s, gen, attempt, route, res.error_class or ErrorClass.internal,
                res.error_message or "provider reported failure", accepted=True, reported=_reported_cost(res.usage),
            )

    if cancel_remote:
        await defer_cancel_remote(attempt_id)
    if next_poll is not None:
        await defer_poll(attempt_id, next_poll)
    if ingest:
        await defer_ingest(attempt_id)
    if resubmit is not None:
        await defer_submit(attempt.generation_id, resubmit)


# ------------------------------------------------------------------ ingest


@app.task(name="gen.ingest", queue=Q_INGEST)
async def gen_ingest(attempt_id: str) -> None:
    aid = uuid.UUID(attempt_id)
    try:
        await run_ingest(aid)
    except Exception as exc:
        log.exception("ingest %s failed", attempt_id)
        await _ingest_failed(aid, exc)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def _ingest_failed(attempt_id: uuid.UUID, exc: Exception) -> None:
    retry_in: float | None = None
    async with session_scope() as s:
        attempt = await s.get(GenerationAttempt, attempt_id, with_for_update=True)
        if attempt is None:
            return
        gen = await s.get(Generation, attempt.generation_id, with_for_update=True)
        if gen is None or gen.status != GenStatus.ingesting:
            return
        tries = int((attempt.last_response or {}).get("ingest_tries", 0)) + 1
        attempt.last_response = {**(attempt.last_response or {}), "ingest_tries": tries, "ingest_error": repr(exc)}
        if tries < MAX_INGEST_TRIES:
            retry_in = float(3 * tries)
        else:
            # Output exists at the provider but we couldn't save it. Charge stands; regenerating would pay twice.
            await guard.settle(s, gen.user_id, attempt, attempt.estimated_cost_usd, estimated=True)
            await fsm.transition(s, gen, GenStatus.failed,
                                 error=(ErrorClass.ingest_error, f"could not save output: {exc!r}"))
    if retry_in is not None:
        await defer_ingest(attempt_id, retry_in)


async def run_ingest(attempt_id: uuid.UUID) -> None:
    reg = get_registry()
    storage = get_storage()
    async with session_scope() as s:
        attempt = await s.get(GenerationAttempt, attempt_id)
        if attempt is None:
            return
        gen = await s.get(Generation, attempt.generation_id)
        if gen is None or gen.status != GenStatus.ingesting:
            return
        route = _route_for(reg.model(gen.model_id), attempt.route_id)
        provider = reg.providers.get(attempt.provider)
        outputs = [ProviderOutput(**o) for o in (attempt.last_response or {}).get("outputs", [])]
        req = request_from(gen)
        user_prompt = gen.request.get("prompt", "")
        direction = gen.request.get("direction")
        user_id, gen_id = gen.user_id, gen.id
    if route is None or provider is None:
        raise RuntimeError(f"route {attempt.route_id} is no longer in the registry")
    if not outputs:
        raise RuntimeError("provider reported success without outputs")
    adapter = get_adapter(provider.adapter)

    created: list[uuid.UUID] = []
    for out in outputs:
        async with session_scope() as s:
            existing = (await s.execute(select(Asset.id).where(
                Asset.generation_id == gen_id, Asset.output_index == out.index))).scalar_one_or_none()
        if existing:
            created.append(existing)
            continue

        asset_id = uuid7()
        workdir = storage.temp_dir() / f"ingest-{attempt_id.hex[:12]}-{out.index}"
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            tmp = workdir / "download.bin"
            if out.data is not None and route.output.decode:
                mime = await load_hook(route.output.decode)(out.data, media=req.media, dest=tmp, workdir=workdir)
            else:
                mime = await adapter.download(route, out, tmp, workdir)
            digest = await anyio.to_thread.run_sync(_sha256, tmp)
            if out.kind in NOT_MEDIA:
                ext = EXT.get(mime or "") or Path(urlsplit(out.url or "").path).suffix or NOT_MEDIA[out.kind]
                mime = mime if mime in EXT else mimetypes.guess_type(f"x{ext}")[0] or "application/octet-stream"
                info = Probe(None, None, None, None, False, None, None, {})
            else:
                ext = EXT.get(mime, ".bin")
                info = await probe(tmp)
            key = asset_key(user_id, asset_id, f"original{ext}")
            size = storage.put_file(key, tmp, move=True)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        kind = AssetKind(out.kind) if out.kind in AssetKind.__members__ else AssetKind.video
        try:
            async with session_scope() as s:
                s.add(Asset(
                    id=asset_id, user_id=user_id, kind=kind, status=AssetStatus.ready,
                    source=AssetSource.generation, mime=mime, storage_key=key, size_bytes=size,
                    content_hash=digest, width=info.width, height=info.height, duration_ms=info.duration_ms,
                    fps=info.fps, has_audio=info.has_audio, codec=info.codec, generation_id=gen_id,
                    output_index=out.index, title=(user_prompt or _untitled(req.model_id, req.mode, gen_id))[:120],
                    # out.meta carries what the provider says about the file itself (TTS word timings)
                    metadata_={**out.meta, "model_id": req.model_id, "mode": req.mode, "prompt": user_prompt,
                               "compiled_prompt": req.prompt, "direction": direction,
                               "params": req.params, "route_id": attempt.route_id},
                ))
                await s.flush()
                for m in req.media:
                    s.add(AssetLineage(child_asset_id=asset_id, parent_asset_id=uuid.UUID(m.asset_id),
                                       relation=m.role, generation_id=gen_id))
                await fsm.emit(s, user_id=user_id, topic="asset", type="asset.created", subject_id=asset_id,
                               payload={"asset_id": str(asset_id), "generation_id": str(gen_id), "kind": kind.value})
        except IntegrityError:
            storage.delete_prefix(f"u/{user_id}/a/{asset_id}")  # a concurrent ingest won the race
            continue
        created.append(asset_id)

    async with session_scope() as s:
        gen = await s.get(Generation, gen_id, with_for_update=True)
        attempt = await s.get(GenerationAttempt, attempt_id, with_for_update=True)
        assert gen is not None and attempt is not None
        if gen.status != GenStatus.ingesting:
            return
        # Providers that report what they charged (RunPod `cost`, Runway credits) set usage.cost_usd; for the
        # rest the estimate from the registry price is the charge.
        # Our own RunPod endpoints price their run time themselves but mark it `cost_estimated`.
        usage = (attempt.last_response or {}).get("usage") or {}
        reported = usage.get("cost_usd")
        cost = Decimal(str(reported)) if reported is not None else attempt.estimated_cost_usd or Decimal("0")
        attempt.actual_cost_usd = cost
        await guard.settle(s, gen.user_id, attempt, cost, estimated=reported is None or bool(usage.get("cost_estimated")),
                           basis=attempt.cost_basis)
        await record_success(s, attempt.route_id, attempt.provider, attempt.latency_ms)
        await fsm.transition(s, gen, GenStatus.succeeded, actual_cost_usd=cost)

    from studio.worker.tasks.assets import defer_derive

    for aid in created:
        await defer_derive(aid)


# ------------------------------------------------------------------ remote cancel


@app.task(name="gen.cancel_remote", queue=Q_POLL)
async def gen_cancel_remote(attempt_id: str) -> None:
    reg = get_registry()
    async with session_scope() as s:
        attempt = await s.get(GenerationAttempt, uuid.UUID(attempt_id))
        if attempt is None or not attempt.provider_job_id:
            return
        gen = await s.get(Generation, attempt.generation_id)
        route = _route_for(reg.model(gen.model_id) if gen else None, attempt.route_id)
        provider = reg.providers.get(attempt.provider)
    if route is None or provider is None:
        return
    try:
        await get_adapter(provider.adapter).cancel(route, attempt.provider_job_id)
    except Exception:
        log.warning("remote cancel of %s failed (job may still finish and bill)", attempt_id, exc_info=True)
