"""Generation service: the one path that creates and cancels generations (API and pipelines share it)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.budget import guard, modes
from studio.config import get_settings
from studio.db.base import uuid7
from studio.db.models import Asset, Generation, GenerationAttempt, Project
from studio.director.compiler import Compiled, ShotDirection, compile_prompt
from studio.director.elements import resolve_mentions
from studio.domain import fsm
from studio.domain.enums import AssetStatus, AttemptStatus, GenStatus
from studio.domain.health import load_health
from studio.providers.factory import adapter_implemented
from studio.providers.local_status import local_reachability
from studio.registry.loader import Registry, get_registry
from studio.registry.routing import Candidate, RoutingEnv, candidates, cheapest_live_estimate
from studio.registry.schema import Mode, ModelSpec, ShotsParam
from studio.registry.validate import MediaRef, NormalizedRequest, RequestError, normalize
from studio.registry.virtual import resolve as resolve_virtual


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
    direction: dict[str, Any] | None = None
    pipeline_step_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None  # a Cinema Studio project, with the shot this take belongs to
    shot_key: str | None = None


@dataclass
class Plan:
    """What would happen if this request ran now (also powers the estimate endpoint)."""

    request: NormalizedRequest
    candidates: list[Candidate]
    chosen: Candidate | None
    live_reference: Candidate | None
    compiled: Compiled
    mentions: list[dict[str, Any]] = field(default_factory=list)
    spec: ModelSpec | None = None  # the model that runs (a virtual model's engine)
    direction: dict[str, Any] | None = None  # with a virtual model's director params folded in
    via: dict[str, Any] | None = None  # the virtual model asked for, if any

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.request.model_id,
            "mode": self.request.mode,
            "via": self.via,
            "chosen_route_id": self.chosen.route.id if self.chosen else None,
            "estimate_usd": float(self.chosen.estimate.usd) if self.chosen and self.chosen.estimate else None,
            "live_reference": self.live_reference.as_dict() if self.live_reference else None,
            "candidates": [c.as_dict() for c in self.candidates],
            "warnings": self.request.warnings + self.compiled.warnings,
            "params": self.request.params,
            "compiled_prompt": self.compiled.prompt,
        }


async def routing_env(session: AsyncSession, reg: Registry, user_id: uuid.UUID | None = None) -> RoutingEnv:
    settings = get_settings()
    mode = (await modes.current(session, user_id)).provider  # the Settings switch, capped by PROVIDER_MODE

    def has_key(provider_id: str) -> bool:
        provider = reg.providers.get(provider_id)
        return bool(provider and provider.key_setting and provider.configured(settings))

    reach = await local_reachability(reg)
    return RoutingEnv(
        provider_mode=mode,
        live_allowlist=settings.live_routes,
        has_key=has_key,
        has_adapter=adapter_implemented,
        health=await load_health(session),
        reachable=lambda provider_id: reach.get(provider_id, True),
        setting_set=lambda name: bool(getattr(settings, name, "")),
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
        refs.append(MediaRef(role=m.get("role", ""), asset_id=str(asset_id), kind=asset.kind.value,
                             duration_ms=asset.duration_ms, fps=asset.fps, width=asset.width, height=asset.height,
                             size_bytes=asset.size_bytes))
    if errors:
        raise GenerationRequestError(errors)
    return refs


def merge_native(spec: Any, params: dict[str, Any] | None, native: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Apply the director's native controls (a camera move Fun Camera has a preset for) over the form's params.

    The camera move wins: the form always sends its fields, so the model's own setting usually just holds its
    default. A warning notes when it replaces a value the user picked (one that isn't the default).
    """
    merged = dict(params or {})
    warnings: list[str] = []
    for name, value in native.items():
        param = spec.param(name)
        if param is None or value not in (getattr(param, "options", None) or [value]):
            continue  # this model has no such control, or no preset for this move: the prompt carries it
        given = merged.get(name)
        if given not in (None, "", value, param.default):
            warnings.append(f"the camera move sets {name} to {value}, replacing {given}")
        merged[name] = value
    return merged, warnings


SHOT_BREAK = "\n\u241e\n"  # joins multi-shot prompts so @mentions resolve across all of them at once


def _chosen(direction: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (direction or {}).items() if v not in (None, "", [])}


def compile_shots(spec: ModelSpec, param: ShotsParam, shots: list[Any], text: str,
                  direction: dict[str, Any] | None, names: dict[str, str]) -> tuple[list[dict[str, Any]], Compiled]:
    """Multi-shot: each shot's prompt (already @mention-resolved, joined by SHOT_BREAK in `text`) compiled with the
    take's direction under the shot's own. Returns the shots to send and a combined Compiled to store."""
    out, warnings, native, prompts = [], [], {}, []
    if param.prompt_budget == "shared":  # one prompt holds every shot, with a short timing label each
        spec = spec.model_copy(deep=True)
        spec.prompt.max_chars = max(80, (spec.prompt.max_chars - 80) // max(1, len(shots)) - 24)
    for i, (shot, prompt) in enumerate(zip(shots, text.split(SHOT_BREAK), strict=True), 1):
        merged = {**_chosen(direction), **_chosen(shot.get("direction") if isinstance(shot, dict) else None)}
        c = compile_prompt(spec, prompt, ShotDirection.model_validate(merged) if merged else None, names=names)
        warnings += [f"shot {i}: {w}" for w in c.warnings]
        native = native or c.native_params
        out.append({**{k: v for k, v in shot.items() if k != "direction"}, "prompt": c.prompt})
        prompts.append(f"Shot {i}: {c.prompt}")
    return out, Compiled(prompt="\n".join(prompts), warnings=warnings, native_params=native,
                         dialect=spec.prompt.dialect)


def _text_mode_without_refs(spec: ModelSpec, mode_id: str, given: list[dict[str, Any]], attached: list[dict[str, Any]],
                            mentioned: list[dict[str, Any]]) -> Mode | None:
    """The model's text mode, when the mode asked for needs reference images only so @mentions can fill them and none
    of the mentioned has a picture yet (a character whose portrait is still being drawn): the mention is described in
    words either way, and the text mode can still make it. None when the mode should stand as asked."""
    # Only where mentions are meant to fill the slot: on other models a reference slot is the user's own input
    # (Marble's views), and switching to a text mode would make a different thing.
    if (spec.elements or {}).get("strategy") != "refs_native":
        return None
    mode = spec.mode(mode_id)
    role = (spec.elements or {}).get("ref_role", "image_references")
    slot = mode.slot(role) if mode else None
    if (mode is None or slot is None or not (slot.required or slot.min) or not mentioned
            or any(m.get("role") == role for m in [*given, *attached])
            or any(s.required for s in mode.medias if s.role != role)):
        return None
    return next((m for m in spec.modes if m.prompt != "none" and not any(s.required for s in m.medias)), None)


async def plan(session: AsyncSession, user_id: uuid.UUID, body: CreateRequest, reg: Registry | None = None) -> Plan:
    reg = reg or get_registry()
    spec = reg.model(body.model_id)
    if spec is None:
        raise GenerationRequestError([f"unknown model {body.model_id!r}"])
    # A virtual model (Cinema Studio) becomes its engine's base model first; everything after sees only that.
    try:
        virtual = resolve_virtual(reg, spec, body.mode, body.params, body.direction)
    except RequestError as exc:
        raise GenerationRequestError(exc.errors) from None
    spec = virtual.spec
    body = replace(body, model_id=spec.id, mode=virtual.mode, params=virtual.params, direction=virtual.direction)
    # Multi-shot: the shots carry the prompts (the mode itself takes none).
    shots_param = next((p for p in spec.params_for(body.mode) if isinstance(p, ShotsParam)), None)
    shots = body.params.get(shots_param.name) if shots_param else None
    shots = shots if isinstance(shots, list) and all(isinstance(s, dict) for s in shots) and shots else None
    text = SHOT_BREAK.join(str(s.get("prompt") or "") for s in shots) if shots else body.prompt
    # @mentions of saved characters/locations/props may attach reference images, so resolve them before
    # validation: the attached images count against the model's slot limits like any other input.
    mention = await resolve_mentions(session, user_id, spec, body.mode, text, body.media)
    if (instead := _text_mode_without_refs(spec, body.mode, body.media, mention.media, mention.elements)) is not None:
        mention.warnings.append(f"{spec.mode(body.mode).label} needs reference images and none of the mentioned "
                                f"have pictures yet, so this uses {instead.label} instead")
        body = replace(body, mode=instead.id)
    refs = await resolve_media_kinds(session, user_id, [*body.media, *mention.media])
    mode = spec.mode(body.mode)
    promptless = mode is not None and mode.prompt == "none"
    try:
        direction = ShotDirection.model_validate(body.direction) if body.direction else None
        params = body.params
        if shots and shots_param:
            compiled_shots, compiled = compile_shots(spec, shots_param, shots, mention.prompt, body.direction,
                                                     mention.names)
            params = {**params, shots_param.name: compiled_shots}
        else:
            # Modes without a prompt (Act-Two, Wan Animate) get no compiled text either: a prompt left over from
            # another model must not reach the provider.
            compiled = compile_prompt(spec, "" if promptless else mention.prompt,
                                      None if promptless else direction, names=mention.names)
        # A camera move with a native control on this model (Wan Fun Camera) sets that param.
        params, native_warnings = merge_native(spec, params, compiled.native_params)
        req = normalize(spec, body.mode, body.prompt, params, refs, body.negative_prompt)
    except RequestError as exc:
        raise GenerationRequestError(exc.errors) from None
    except ValidationError as exc:
        raise GenerationRequestError(
            [f"direction: {e['msg']} at {'.'.join(map(str, e['loc']))}" for e in exc.errors()]) from None
    req.warnings.extend(virtual.warnings + mention.warnings + native_warnings)
    env = await routing_env(session, reg, user_id)
    cands = candidates(reg, spec, req, env, body.route_policy)
    chosen = next((c for c in cands if c.eligible), None)
    return Plan(request=req, candidates=cands, chosen=chosen, live_reference=cheapest_live_estimate(cands),
                compiled=compiled, mentions=mention.elements, spec=spec, direction=body.direction, via=virtual.via)


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

    if body.project_id is not None:
        project = await session.get(Project, body.project_id)
        if project is None or project.user_id != user_id or project.deleted_at is not None:
            raise GenerationRequestError([f"project {body.project_id} not found"])
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

    spec = p.spec
    assert spec is not None
    gen = Generation(
        id=uuid7(),
        user_id=user_id,
        group_id=body.group_id,
        parent_generation_id=body.parent_generation_id,
        pipeline_step_id=body.pipeline_step_id,
        project_id=body.project_id,
        shot_key=body.shot_key,
        model_id=spec.id,
        mode=p.request.mode,
        output_type=spec.output_type,
        status=GenStatus.draft,
        request={
            **p.request.as_dict(),
            "route_policy": body.route_policy,
            "direction": p.direction,
            "via": p.via,
            # what the provider gets; a mode without a prompt (multi-shot sends its shots) gets none
            "compiled_prompt": "" if (m := spec.mode(p.request.mode)) and m.prompt == "none" else p.compiled.prompt,
            "mentions": p.mentions,
        },
        compiled=p.compiled.as_dict(),
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


async def quick_estimate(
    session: AsyncSession, model_id: str, mode: str, params: dict[str, Any], roles: list[str], reg: Registry | None = None,
    prompt: str = "planned", known: list[MediaRef] | None = None, seconds: dict[str, float] | None = None,
) -> tuple[Decimal, Decimal | None]:
    """(cost on the route that would run now, cost on the cheapest live route) without real inputs.

    For planning runs whose inputs don't exist yet (a later shot's start frame). Media are stand-ins of the
    slot's first kind; pricing that depends on input length (Aleph) uses its minimum.
    """
    reg = reg or get_registry()
    spec = reg.model(model_id)
    if spec is None:
        raise GenerationRequestError([f"unknown model {model_id!r}"])
    try:
        virtual = resolve_virtual(reg, spec, mode, params, None)
    except RequestError as exc:
        raise GenerationRequestError(exc.errors) from None
    spec, mode, params = virtual.spec, virtual.mode, virtual.params
    m = spec.mode(mode)
    if m is None:
        raise GenerationRequestError([f"{spec.name} has no mode {mode!r}"])
    refs = list(known or [])  # inputs that exist already; stand-ins for the rest
    have = {r.role for r in refs}
    refs += [MediaRef(role=r, asset_id=f"planned-{i}", kind=slot.kinds[0],
                      duration_ms=round((seconds or {}).get(r, 0) * 1000) or None)
             for i, r in enumerate(roles) if r not in have and (slot := m.slot(r))]
    try:
        req = normalize(spec, mode, prompt, params, refs)
    except RequestError as exc:
        raise GenerationRequestError([f"{spec.name}: {e}" for e in exc.errors]) from None
    cands = candidates(reg, spec, req, await routing_env(session, reg))
    chosen = next((c for c in cands if c.eligible), None)
    if chosen is None:
        raise GenerationRequestError([f"no route can run {spec.name} {m.label.lower()} right now"])
    live = cheapest_live_estimate(cands)
    return (chosen.estimate.usd if chosen.estimate else Decimal("0"),
            live.estimate.usd if live and live.estimate else None)


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
