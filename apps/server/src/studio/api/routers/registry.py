from __future__ import annotations

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
from studio.config import get_settings
from studio.db.models import User
from studio.domain.generations import CreateRequest, plan, routing_env
from studio.registry.loader import MOCK_PROVIDER, Registry, get_registry
from studio.registry.pricing import estimate
from studio.registry.routing import candidates
from studio.registry.schema import ModelSpec
from studio.registry.validate import NormalizedRequest

router = APIRouter(prefix="/registry", tags=["registry"])


def _defaults(spec: ModelSpec, mode_id: str) -> dict:
    return {p.name: p.default for p in spec.params_for(mode_id) if p.default is not None}


def _from_price(spec: ModelSpec) -> float | None:
    prices = []
    for mode in spec.modes:
        params = _defaults(spec, mode.id)
        for route in spec.routes:
            if route.provider == MOCK_PROVIDER or not route.enabled or mode.id not in route.modes:
                continue
            try:
                prices.append(float(estimate(route.pricing, params).usd))
            except ValueError:
                continue
    return min(prices) if prices else None


async def _runnable(session: AsyncSession, reg: Registry, spec: ModelSpec) -> bool:
    env = await routing_env(session, reg)
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
    etag = f'"{reg.hash}-{get_settings().provider_mode}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    response.headers["ETag"] = etag
    models = []
    for spec in sorted(reg.models.values(), key=lambda m: (m.output_type != "video", m.name)):
        models.append(ModelSummary(
            id=spec.id, name=spec.name, family=spec.family, vendor=spec.vendor, description=spec.description,
            output_type=spec.output_type, status=spec.status, license_tier=spec.license_tier, tags=spec.tags,
            modes=[ModeSummary(id=m.id, label=m.label, ops=list(m.ops)) for m in spec.modes],
            from_price_usd=_from_price(spec), runnable=await _runnable(session, reg, spec),
        ))
    return RegistryOut(hash=reg.hash, provider_mode=get_settings().provider_mode, models=models,
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
        elements=spec.elements, routes=routes,
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
    ))
    return EstimateOut.model_validate(p.as_dict())
