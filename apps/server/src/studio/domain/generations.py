"""Generation service: the one path that creates and cancels generations (API and pipelines share it)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.budget import guard
from studio.config import get_settings
from studio.db.base import uuid7
from studio.db.models import Asset, Generation, GenerationAttempt
from studio.domain import fsm
from studio.domain.enums import AssetStatus, AttemptStatus, GenStatus
from studio.domain.health import load_health
from studio.providers.factory import adapter_implemented
from studio.registry.loader import Registry, get_registry
from studio.registry.routing import Candidate, RoutingEnv, candidates, cheapest_live_estimate
from studio.registry.validate import MediaRef, NormalizedRequest, RequestError, normalize


class GenerationRequestError(Exception):
    def __init__(self, errors: list[str], *, candidates: list[dict] | None = None):
        super().__init__("; ".join(errors))
        self.errors = errors
        self.candidates = candidates or []


class ConfirmationRequired(Exception):
    def __init__(self, estimate_usd: Decimal, route_id: str, threshold: Decimal):
        super().__init__(f"estimated ${estimate_usd} exceeds the ${threshold} confirmation threshold")
        self.estimate_usd = estimate_usd
        self.route_id = route_id
        self.threshold = threshold


@dataclass
class CreateRequest:
    model_id: str
    mode: str
    prompt: str = ""
    negative_prompt: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    media: list[dict[str, str]] = field(default_factory=list)  # [{role, asset_id}]
    route_policy: str = "cheapest_healthy"
    confirm_cost_usd: float | None = None
    idempotency_key: str | None = None
    group_id: uuid.UUID | None = None
    parent_generation_id: uuid.UUID | None = None


@dataclass
class Plan:
    """What would happen if this request ran now (also powers the estimate endpoint)."""

    request: NormalizedRequest
    candidates: list[Candidate]
    chosen: Candidate | None
    live_reference: Candidate | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "chosen_route_id": self.chosen.route.id if self.chosen else None,
            "estimate_usd": float(self.chosen.estimate.usd) if self.chosen and self.chosen.estimate else None,
            "live_reference": self.live_reference.as_dict() if self.live_reference else None,
            "candidates": [c.as_dict() for c in self.candidates],
            "warnings": self.request.warnings,
            "params": self.request.params,
        }


async def routing_env(session: AsyncSession, reg: Registry) -> RoutingEnv:
    settings = get_settings()

    def has_key(provider_id: str) -> bool:
        provider = reg.providers.get(provider_id)
        return bool(provider and provider.key_setting and getattr(settings, provider.key_setting, ""))

    return RoutingEnv(
        provider_mode=settings.provider_mode,
        live_allowlist=settings.live_routes,
        has_key=has_key,
        has_adapter=adapter_implemented,
        health=await load_health(session),
    )


async def resolve_media_kinds(session: AsyncSession, user_id: uuid.UUID, media: list[dict[str, str]]) -> list[MediaRef]:
    refs: list[MediaRef] = []
    ids = []
    for m in media:
        try:
            ids.append(uuid.UUID(str(m["asset_id"])))
        except (KeyError, ValueError):
            raise GenerationRequestError([f"invalid media entry {m!r}"]) from None
    rows = {
        a.id: a for a in (await session.execute(
            select(Asset).where(Asset.id.in_(ids), Asset.user_id == user_id, Asset.deleted_at.is_(None))
        )).scalars()
    }
    errors = []
    for m, asset_id in zip(media, ids, strict=True):
        asset = rows.get(asset_id)
        if asset is None:
            errors.append(f"asset {asset_id} not found")
            continue
        if asset.status != AssetStatus.ready:
            errors.append(f"asset {asset_id} is not ready ({asset.status})")
            continue
        refs.append(MediaRef(role=m.get("role", ""), asset_id=str(asset_id), kind=asset.kind.value))
    if errors:
        raise GenerationRequestError(errors)
    return refs


async def plan(session: AsyncSession, user_id: uuid.UUID, body: CreateRequest, reg: Registry | None = None) -> Plan:
    reg = reg or get_registry()
    spec = reg.model(body.model_id)
    if spec is None:
        raise GenerationRequestError([f"unknown model {body.model_id!r}"])
    refs = await resolve_media_kinds(session, user_id, body.media)
    try:
        req = normalize(spec, body.mode, body.prompt, body.params, refs, body.negative_prompt)
    except RequestError as exc:
        raise GenerationRequestError(exc.errors) from None
    env = await routing_env(session, reg)
    cands = candidates(reg, spec, req, env, body.route_policy)
    chosen = next((c for c in cands if c.eligible), None)
    return Plan(request=req, candidates=cands, chosen=chosen, live_reference=cheapest_live_estimate(cands))


async def create_generation(session: AsyncSession, user_id: uuid.UUID, body: CreateRequest) -> tuple[Generation, bool]:
    """Validate, route-check, budget-check and insert a queued generation. Caller commits, then defers submit.

    Returns (generation, created). An existing generation is returned for a repeated idempotency key.
    """
    if body.idempotency_key:
        existing = (await session.execute(select(Generation).where(
            Generation.user_id == user_id, Generation.idempotency_key == body.idempotency_key
        ))).scalar_one_or_none()
        if existing:
            return existing, False

    reg = get_registry()
    p = await plan(session, user_id, body, reg)
    if p.chosen is None:
        raise GenerationRequestError(
            ["no route can run this request: " + "; ".join(f"{c.route.id}: {c.reason}" for c in p.candidates)],
            candidates=[c.as_dict() for c in p.candidates],
        )
    estimate = p.chosen.estimate.usd if p.chosen.estimate else Decimal("0")
    policy = await guard.get_policy(session, user_id)
    if guard.needs_confirmation(estimate, policy.confirm_over_usd, body.confirm_cost_usd):
        raise ConfirmationRequired(estimate, p.chosen.route.id, policy.confirm_over_usd)

    spec = reg.model(body.model_id)
    assert spec is not None
    gen = Generation(
        id=uuid7(),
        user_id=user_id,
        group_id=body.group_id,
        parent_generation_id=body.parent_generation_id,
        model_id=spec.id,
        mode=p.request.mode,
        output_type=spec.output_type,
        status=GenStatus.draft,
        request={**p.request.as_dict(), "route_policy": body.route_policy},
        routing={
            "policy": body.route_policy,
            "planned": p.chosen.route.id,
            "candidates": [c.as_dict() for c in p.candidates],
            "live_reference": p.live_reference.as_dict() if p.live_reference else None,
            "tried": [],
            "retries": {},
        },
        estimated_cost_usd=estimate,
        idempotency_key=body.idempotency_key,
        registry_hash=reg.hash,
    )
    session.add(gen)
    await session.flush()
    await fsm.transition(session, gen, GenStatus.queued)
    return gen, True


async def inflight_attempt(session: AsyncSession, gen_id: uuid.UUID) -> GenerationAttempt | None:
    return (await session.execute(
        select(GenerationAttempt)
        .where(GenerationAttempt.generation_id == gen_id,
               GenerationAttempt.status.in_([AttemptStatus.submitting, AttemptStatus.submitted,
                                             AttemptStatus.running, AttemptStatus.unknown]))
        .order_by(GenerationAttempt.attempt_no.desc())
        .limit(1)
    )).scalar_one_or_none()


async def cancel_generation(session: AsyncSession, gen: Generation) -> GenerationAttempt | None:
    """Cancel locally right away; returns the in-flight attempt the worker should cancel remotely."""
    if gen.status.terminal:
        raise fsm.IllegalTransition(f"generation is already {gen.status.value}")
    attempt = await inflight_attempt(session, gen.id)
    await fsm.transition(session, gen, GenStatus.canceled, reason="canceled by user")
    if attempt is not None:
        remote = attempt.provider_job_id is not None and attempt.status != AttemptStatus.unknown
        attempt.status = AttemptStatus.canceled
        # Providers may bill partial work; P1 adapters report it. Until then release the reservation.
        await guard.settle(session, gen.user_id, attempt, None, estimated=True)
        return attempt if remote else None
    return None
