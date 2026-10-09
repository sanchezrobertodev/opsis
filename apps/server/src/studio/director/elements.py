"""Resolve `@mentions` of saved characters, locations, props and styles in a prompt.

How a mention becomes something the model understands depends on the model's `elements` strategy:
- refs_native: the element's images are attached as reference images (in the model's ref role, within the
  slot's limit) and the mention becomes its name plus the model's reference token, e.g. "Maya ([Image 1])".
- lora: a character trained for the model's LoRA base (`element.lora`, from the lora_train recipe) attaches its
  weights in the auto `lora` / `lora_high` slots and the mention carries its trigger word, e.g. "Maya (mayaxq)".
- keyframe_first / text_only: the mention becomes the name plus its short description, with a hint on how
  to get a consistent look.
The first mention of each element carries its description; later ones just the name.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import Asset, Element, ElementAsset
from studio.domain.enums import AssetStatus
from studio.registry.schema import ModelSpec

MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_-]{0,39})")
KIND_ORDER = {"character": 0, "location": 1, "prop": 2, "style": 3, "voice": 4}
IMAGES_PER_KIND = {"character": 2, "location": 1, "prop": 1, "style": 1}
DESCRIPTION_LIMIT = 220


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or "element")[:40].rstrip("-")


def mentions(prompt: str) -> list[str]:
    seen: list[str] = []
    for m in MENTION.finditer(prompt or ""):
        slug = m.group(1).lower()
        if slug not in seen:
            seen.append(slug)
    return seen


@dataclass
class Resolution:
    prompt: str
    media: list[dict[str, str]] = field(default_factory=list)  # [{role, asset_id}] to attach
    warnings: list[str] = field(default_factory=list)
    elements: list[dict[str, Any]] = field(default_factory=list)  # [{id, slug, name, attached}]
    # every @slug in the prompt -> how the prompt names it now (for direction that refers to cast members)
    names: dict[str, str] = field(default_factory=dict)


async def resolve_mentions(
    session: AsyncSession,
    user_id: uuid.UUID,
    spec: ModelSpec,
    mode_id: str,
    prompt: str,
    existing_media: list[dict[str, str]],
) -> Resolution:
    slugs = mentions(prompt)
    if not slugs:
        return Resolution(prompt=prompt)

    rows = (await session.execute(select(Element).where(
        Element.user_id == user_id, Element.slug.in_(slugs), Element.deleted_at.is_(None)))).scalars().all()
    by_slug = {e.slug: e for e in rows}
    res = Resolution(prompt=prompt, names={s: f"@{s}" for s in slugs})
    for slug in slugs:
        if slug not in by_slug:
            res.warnings.append(f"no saved character, location or prop called @{slug}; left as written")

    images: dict[uuid.UUID, list[uuid.UUID]] = {}
    if rows:
        links = (await session.execute(
            select(ElementAsset.element_id, ElementAsset.asset_id)
            .join(Asset, Asset.id == ElementAsset.asset_id)
            .where(ElementAsset.element_id.in_([e.id for e in rows]), Asset.status == AssetStatus.ready,
                   Asset.deleted_at.is_(None), Asset.kind == "image")
            .order_by(ElementAsset.is_primary.desc(), ElementAsset.sort, Asset.created_at)
        )).all()
        for element_id, asset_id in links:
            images.setdefault(element_id, []).append(asset_id)

    strategy = (spec.elements or {}).get("strategy", "text_only")
    ref_role = (spec.elements or {}).get("ref_role", "image_references")
    token = (spec.elements or {}).get("token")
    mode = spec.mode(mode_id)
    slot = mode.slot(ref_role) if mode else None
    used = sum(1 for m in existing_media if m.get("role") == ref_role)
    capacity = max(0, (slot.max if slot else 0) - used)
    next_index = used + 1
    lora_room = {r: max(0, (mode.slot(r).max if mode and mode.slot(r) else 0)
                        - sum(1 for m in existing_media if m.get("role") == r)) for r in ("lora", "lora_high")}
    trained = await _ready_loras(session, rows) if strategy == "lora" else set()

    replacements: dict[str, tuple[str, str]] = {}  # slug -> (first mention text, later mention text)
    for element in sorted((by_slug[s] for s in slugs if s in by_slug), key=lambda e: KIND_ORDER.get(e.kind.value, 9)):
        attached: list[str] = []
        label = element.name
        if strategy == "refs_native" and slot is not None and capacity > 0:
            for asset_id in images.get(element.id, [])[: min(IMAGES_PER_KIND.get(element.kind.value, 1), capacity)]:
                res.media.append({"role": ref_role, "asset_id": str(asset_id)})
                attached.append(str(asset_id))
            capacity -= len(attached)
            if attached and token:
                tokens = [token.format(n=next_index + i) for i in range(len(attached))]
                label = f"{element.name} ({', '.join(tokens)})"
            next_index += len(attached)
            if not images.get(element.id):
                res.warnings.append(f"@{element.slug} has no reference images yet; described in words")
        elif strategy == "refs_native" and slot is None:
            res.warnings.append(f"{spec.name} {mode.label if mode else mode_id} can't take reference images, so "
                                f"@{element.slug} is described in words. Use a mode with reference images for its look.")
        elif strategy == "refs_native" and capacity <= 0:
            res.warnings.append(f"no reference slots left for @{element.slug}; described in words")
        elif strategy == "lora":
            lora = element.lora or {}
            base = (spec.elements or {}).get("lora_base")
            low, high = str(lora.get("low") or ""), str(lora.get("high") or "")
            if lora.get("base") == base and low in trained and lora_room["lora"] > 0:
                res.media.append({"role": "lora", "asset_id": low})
                attached.append(low)
                lora_room["lora"] -= 1
                if high in trained and lora_room["lora_high"] > 0:
                    res.media.append({"role": "lora_high", "asset_id": high})
                    attached.append(high)
                    lora_room["lora_high"] -= 1
                if lora.get("trigger"):
                    label = f"{element.name} ({lora['trigger']})"
            elif lora.get("base") == base and low in trained:
                res.warnings.append(f"no room for another character's identity, so @{element.slug} is described in words")
            elif element.kind.value == "character":
                res.warnings.append(f"@{element.slug} has no trained identity for {spec.name}, so it's described in "
                                    "words. Train one on the character's page for a face that holds.")
        elif strategy == "keyframe_first" and element.kind.value == "character":
            res.warnings.append(f"{spec.name} doesn't take reference images, so @{element.slug} is described in "
                                f"words. For a consistent look, make a start frame from its pictures first.")
        description = (element.description or "").strip()[:DESCRIPTION_LIMIT]
        first = f"{label}, {description}," if description else label
        replacements[element.slug] = (first, label)
        res.names[element.slug] = label
        res.elements.append({"id": str(element.id), "slug": element.slug, "name": element.name, "attached": attached})

    seen: set[str] = set()

    def substitute(m: re.Match[str]) -> str:
        slug = m.group(1).lower()
        if slug not in replacements:
            return m.group(0)
        first, later = replacements[slug]
        if slug in seen:
            return later
        seen.add(slug)
        return first

    res.prompt = MENTION.sub(substitute, prompt).replace(",,", ",").replace(", .", ".")
    return res


async def _ready_loras(session: AsyncSession, rows: Any) -> set[str]:
    """The LoRA files the mentioned elements point at that still exist and are ready."""
    ids = [uuid.UUID(str(v)) for e in rows for k, v in (e.lora or {}).items() if k in ("low", "high") and v]
    if not ids:
        return set()
    found = (await session.execute(select(Asset.id).where(
        Asset.id.in_(ids), Asset.status == AssetStatus.ready, Asset.deleted_at.is_(None), Asset.kind == "lora"))).scalars()
    return {str(i) for i in found}
