from __future__ import annotations

from fastapi import APIRouter, HTTPException

from studio.api.schemas import RunCreate, TemplateOut, TemplateRenderIn
from studio.domain.generations import GenerationRequestError
from studio.pipelines.recipes import get_recipe
from studio.registry.loader import get_registry
from studio.registry.presets import PresetError, render_template

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateOut])
async def list_templates() -> list[TemplateOut]:
    """Guided templates: recipe runs with most of their input chosen."""
    reg = get_registry()
    return [TemplateOut(id=t.id, name=t.name, summary=t.summary, tags=t.tags, recipe=t.recipe,
                        recipe_name=recipe.name, makes=t.makes, fields=t.fields, inputs=t.inputs)
            for t in sorted(reg.templates.values(), key=lambda t: t.name)
            if t.status == "active" and (recipe := get_recipe(t.recipe))]


@router.post("/{template_id}/render", response_model=RunCreate,
             responses={422: {"description": "Missing answers or inputs"}})
async def render(template_id: str, body: TemplateRenderIn) -> RunCreate:
    """The run this template starts with these answers and inputs: post it to /pipelines. Free."""
    template = get_registry().templates.get(template_id)
    if template is None or template.status != "active":
        raise HTTPException(404, f"unknown template {template_id}")
    try:
        out = render_template(template, body.fields, body.media)
    except PresetError as exc:
        raise GenerationRequestError(exc.errors) from exc
    return RunCreate.model_validate(out)
