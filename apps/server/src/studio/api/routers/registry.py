from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import (
    EstimateOut,
    GenerationCreate,
    ModelDetail,
    ModelSummary,
    ModeSummary,
    RegistryOut,
    RouteSummary,
)
from studio.budget import modes
from studio.config import get_settings
from studio.db.models import User
from studio.domain.generations import CreateRequest, plan, routing_env
from studio.providers.local_status import local_state
from studio.registry.loader import MOCK_PROVIDER, Registry, get_registry
from studio.registry.pricing import estimate
from studio.registry.routing import RoutingEnv, candidates, local_only, runs_locally
from studio.registry.schema import Mode, ModelSpec
from studio.registry.validate import NormalizedRequest

router = APIRouter(prefix="/registry", tags=["registry"])


def _defaults(spec: ModelSpec, mode_id: str) -> dict:
    return {p.name: p.default for p in spec.params_for(mode_id) if p.default is not None}


def _wrapped(reg: Registry, spec: ModelSpec) -> list[ModelSpec]:
    """The base models a virtual model runs on."""
    return [m for w in spec.wraps if (m := reg.model(w)) is not None and m.kind != "virtual"]


# Part of the summary's ETag: bump it when what the summary computes changes (from-prices), so browsers holding the
# last copy fetch it again even though the registry files didn't change.
SUMMARY_VERSION = 5
STAND_IN_SECONDS = 5  # a typical input clip or sound, for modes priced by input length (video edits)
STAND_IN_PROMPT = "x" * 300  # a short script, for routes priced by the text (speech)


def _stand_ins(mode: Mode) -> list[dict]:
    """The least each required input slot takes, so a mode billed by its inputs isn't priced as if it had none."""
    out = []
    for slot in mode.medias:
        if not slot.required:
            continue
        kind = slot.kinds[0]
        timed = kind in ("video", "audio", "mask_video")
        out += [{"role": slot.role, "kind": "video" if kind == "mask_video" else kind,
                 "duration_ms": STAND_IN_SECONDS * 1000 if timed else None,
                 "width": 1280 if timed else None, "height": 720 if timed else None}] * max(1, slot.min)
    return out


def _from_price(reg: Registry, spec: ModelSpec, env: RoutingEnv | None = None) -> float | None:
    if spec.kind == "virtual":
        prices = [p for m in _wrapped(reg, spec) if (p := _from_price(reg, m, env)) is not None]
        return min(prices) if prices else None
    settings = get_settings()
    prices, usable = [], []
    only_local = local_only(reg, spec)
    for mode in spec.modes:
        params = _defaults(spec, mode.id)
        media = _stand_ins(mode)
        here = env is not None and runs_locally(reg, spec, mode.id, env)
        for route in spec.routes:
            if route.provider == MOCK_PROVIDER or not route.enabled or mode.id not in route.modes:
                continue
            provider = reg.providers.get(route.provider)
            if provider and provider.kind == "local" and not only_local and not here:
                continue  # a free local route for a model that also runs elsewhere counts once it can run here
            try:
                usd = float(estimate(route.pricing, params, STAND_IN_PROMPT, media=media, mode=mode.id).usd)
            except ValueError:
                continue
            prices.append(usd)
            if provider and provider.configured(settings):
                usable.append(usd)
    # routes that are set up, so a free route that isn't (Codex without CODEX_BIN) doesn't make the model look free;
    # every route on a fresh install with no keys yet
    found = usable or prices
    return min(found) if found else None


# Where a model runs, from its routes' provider kinds; the picker groups by it. The most "yours" wins.
RUNS_ON = ("local", "cloud_gpu", "hosted")


def _runs_on(reg: Registry, spec: ModelSpec, env: RoutingEnv) -> str:
    if spec.kind == "virtual":
        found = [_runs_on(reg, m, env) for m in _wrapped(reg, spec)]
        return min(found, key=RUNS_ON.index) if found else "hosted"
    # a model that also runs elsewhere counts as yours once this machine's engine can run it
    if local_only(reg, spec) or any(runs_locally(reg, spec, m.id, env) for m in spec.modes):
        return "local"
    kinds = {p.kind for r in spec.routes if r.provider != MOCK_PROVIDER and (p := reg.providers.get(r.provider))}
    return "cloud_gpu" if "selfhosted" in kinds else "hosted"


async def _runnable(reg: Registry, spec: ModelSpec, env: RoutingEnv) -> bool:
    if spec.kind == "virtual":
        return any([await _runnable(reg, m, env) for m in _wrapped(reg, spec)])
    for mode in spec.modes:
        req = NormalizedRequest(spec.id, mode.id, "", None, _defaults(spec, mode.id), [])
        if any(c.eligible for c in candidates(reg, spec, req, env)):
            return True
    return False


@router.get("", response_model=RegistryOut)
async def get_registry_summary(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> RegistryOut | Response:
    reg = get_registry()
    mode = (await modes.current(session)).provider  # the Settings switch changes what's runnable
    settings = get_settings()
    ready = ",".join(sorted(p.id for p in reg.providers.values() if p.configured(settings)))
    # what the local engines can run changes what's runnable, where models run and their "from" prices
    reach, blocked = await local_state(reg)
    ready += repr((sorted(reach.items()), sorted(blocked)))
    etag = f'"{reg.hash}-{mode}-{SUMMARY_VERSION}-{hashlib.sha1(ready.encode()).hexdigest()[:8]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    response.headers["ETag"] = etag
    env = await routing_env(session, reg)
    models = []
    for spec in sorted(reg.models.values(), key=lambda m: (m.output_type != "video", m.name)):
        models.append(ModelSummary(
            id=spec.id, name=spec.name, kind=spec.kind, family=spec.family, vendor=spec.vendor,
            description=spec.description,
            output_type=spec.output_type, status=spec.status, license_tier=spec.license_tier, tags=spec.tags,
            modes=[ModeSummary(id=m.id, label=m.label, ops=list(m.ops)) for m in spec.modes],
            from_price_usd=_from_price(reg, spec, env), runnable=await _runnable(reg, spec, env),
            runs_on=_runs_on(reg, spec, env),
        ))
    return RegistryOut(hash=reg.hash, provider_mode=mode, models=models,
                       warnings=reg.warnings)


@router.get("/models/{model_id}", response_model=ModelDetail)
async def get_model(model_id: str) -> ModelDetail:
    reg = get_registry()
    spec = reg.model(model_id)
    if spec is None:
        raise HTTPException(404, f"unknown model {model_id}")
    routes = [
        RouteSummary(
            id=r.id, provider=r.provider,
            provider_name=reg.providers[r.provider].name if r.provider in reg.providers else r.provider,
            is_mock=r.provider == MOCK_PROVIDER, enabled=r.enabled, modes=list(r.modes),
            pricing_basis=r.pricing.basis,
            pricing_verified=r.pricing.verified.isoformat() if r.pricing.verified else None,
            pricing_source=r.pricing.source, notes=r.notes,
        )
        for r in spec.routes
    ]
    return ModelDetail(
        id=spec.id, name=spec.name, kind=spec.kind, family=spec.family, vendor=spec.vendor,
        description=spec.description, output_type=spec.output_type, status=spec.status,
        license_tier=spec.license_tier, tags=spec.tags, prompt=spec.prompt, aspect_ratios=spec.aspect_ratios,
        duration=spec.duration, parameters=spec.parameters, rules=spec.rules, modes=spec.modes,
        elements=spec.elements, routes=routes, virtual=spec.virtual,
    )


@router.post("/estimate", response_model=EstimateOut)
async def post_estimate(
    body: GenerationCreate, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
) -> EstimateOut:
    p = await plan(session, user.id, CreateRequest(
        model_id=body.model_id, mode=body.mode, prompt=body.prompt or "estimate",
        negative_prompt=body.negative_prompt, params=body.params,
        media=[{"role": m.role, "asset_id": str(m.asset_id)} for m in body.media],
        route_policy=body.route_policy,
        direction=body.direction.model_dump(exclude_none=True) if body.direction else None,
    ))
    return EstimateOut.model_validate(p.as_dict())
