"""Resolve `@mentions` of saved characters, locations, props and styles in a prompt.

How a mention becomes something the model understands depends on the model's `elements` strategy:
- refs_native: the element's images are attached as reference images (in the model's ref role, within the
  slot's limit) and the mention becomes its name plus the model's reference token, e.g. "Maya ([Image 1])".
- lora: a character with a trained identity for the model's LoRA base (`element.lora` keeps one per base, from the
  lora_train recipe) attaches its weights in the auto `lora` / `lora_high` slots and the mention carries its trigger
  word, e.g. "Maya (mayaxq)".
- keyframe_first / text_only: the mention becomes the name plus its short description, with a hint on how
  to get a consistent look.
A model that also takes reference audio (`elements.voice_role`, e.g. MiniMax H3's reference mode) gets a character's
voice sample there (`voice.sample_asset_id`), named with `voice_token`: "Maya (Image 1; voice Audio 1)". So do the
speakers of a shot's dialogue that the prompt doesn't mention (`speakers`).
The first mention of each element carries its description; later ones just the name.
A look (`@maya:winter`: an outfit, or the character in another picture style) attaches its own pictures first and adds
its description after the character's; a plain mention attaches only the main pictures. A cast (a saved group,
`@crew`) stands for its characters: each attaches a share of the room and the mention lists them by name, with the
description of any that no picture or identity shows.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import Asset, Element, ElementAsset, ElementKind, ElementMember
from studio.domain.enums import AssetStatus
from studio.registry.schema import MediaSlot, ModelSpec

MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_-]{0,39})")
# the same with a look: a colon right before a letter or digit, so "@maya: she says" stays a plain mention. Only
# resolve_mentions reads looks; everything else wants the handles alone (`mentions()`).
MENTION_LOOK = re.compile(MENTION.pattern + r"(?::([A-Za-z0-9][A-Za-z0-9-]{0,23}))?")
LOOK_SLUG = r"^[a-z0-9][a-z0-9-]{0,23}$"  # a look's handle, as written after @handle:
SLUG = r"^[a-z0-9][a-z0-9-]{0,39}$"  # an Element's handle, as written after @
TRIGGER = r"^[a-z][a-z0-9]{3,23}$"  # a trained identity's made-up word, which a mention writes into the prompt
KIND_ORDER = {"character": 0, "cast": 1, "location": 2, "prop": 3, "style": 4, "voice": 5}
IMAGES_PER_KIND = {"character": 2, "cast": 2, "location": 1, "prop": 1, "style": 2}  # a cast's: at most, per member
# sheets a mention never attaches: a grid given as a reference tends to come back as a collage
UNATTACHED_VIEWS = ("expressions", "poses")
# the one picture that stands for a character (a talking shot speaks from it, a face swap takes its face): any picture
# of a single view before a reference sheet (several views on one picture), even a sheet marked primary
PORTRAIT_ORDER = ((ElementAsset.view == "sheet").asc(), ElementAsset.is_primary.desc(), ElementAsset.sort)
# what a picture of an element shows; `expressions` and `poses` are grid sheets kept for people to see
View = Literal["front", "three_quarter", "side", "back", "face", "full_body", "sheet", "wide", "detail", "style",
               "expressions", "poses"]
CAST_SIZE = (2, 8)
# what a first mention says of an element: room for the character designer's longest description (about 255
# characters for a person, 295 for a creature with every feature; web lib/designer.ts), mirrored by the web's LOOK_MAX
DESCRIPTION_LIMIT = 300


class ElementLook(BaseModel):
    """A character's look: an outfit, or the character in another picture style. `@handle:look` in a prompt attaches
    its pictures first and reads its description after the character's."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(max_length=160, description='Read after the character\'s description: "wearing a red '
                                                         'wool coat and boots", "drawn as a 2D anime character"')


def identities(lora: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """A character's trained identities by LoRA base: {base: {base, trigger, low, high, steps, run_id, trained_at}}.
    One kept before they were per base (an old worker may still attach one while a deploy migrates) reads as its
    base's."""
    lora = lora or {}
    if "low" in lora:
        return {str(lora["base"]): lora} if lora.get("base") else {}
    return {str(base): ident for base, ident in lora.items() if isinstance(ident, dict)}


def clip(text: str, limit: int, *, at_comma: bool = False) -> str:
    """One line of at most `limit` characters, cut at the last whole cue (`at_comma`) or word that fits, without
    the trailing punctuation a cut leaves."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text.rstrip(" ,;")
    head = text[: limit + 1]  # a comma or space right at the limit still counts as a boundary
    cut = head.rfind(",") if at_comma else -1
    if cut < limit // 2:
        cut = head.rfind(" ")
    return (head[:cut] if cut > 0 else text[:limit]).rstrip(" ,;.")


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or "element")[:40].rstrip("-")


async def unique_slug(session: AsyncSession, user_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None) -> str:
    """An @mention name from `name` that none of the user's other elements has: the name, else the name numbered, cut
    short where it's long so the number fits in 40 characters."""
    base = slugify(name)
    taken = set((await session.execute(select(Element.slug).where(
        Element.user_id == user_id, Element.slug.like(f"{base[:30]}%"),  # what a numbered one still starts with
        *( [Element.id != exclude] if exclude else []),
    ))).scalars())
    slug, n = base, 1
    while slug in taken:
        n += 1
        slug = f"{base[:39 - len(str(n))].rstrip('-')}-{n}"
    return slug


def style_slug(style: Any) -> str | None:
    """The handle of a saved style a direction names (`"@slug"`), or None for a catalog style id."""
    return style[1:].lower() if isinstance(style, str) and style.startswith("@") else None


def with_style(prompt: str, slug: str) -> str:
    """The prompt naming a saved style at its end, so mention resolution phrases its look in (and attaches its
    pictures where the mode takes references)."""
    text = (prompt or "").rstrip()
    if text and text[-1] not in ".!?":
        text += "."
    return f"{text} In the style of @{slug}.".lstrip()


async def unknown_styles(session: AsyncSession, user_id: uuid.UUID, slugs: Any) -> list[str]:
    """The handles in `slugs` that aren't saved styles of the user's."""
    wanted = list(dict.fromkeys(s.lower() for s in slugs))
    if not wanted:
        return []
    have = set((await session.execute(select(Element.slug).where(
        Element.user_id == user_id, Element.kind == ElementKind.style, Element.slug.in_(wanted),
        Element.deleted_at.is_(None)))).scalars())
    return [s for s in wanted if s not in have]


def mentions(prompt: str) -> list[str]:
    seen: list[str] = []
    for m in MENTION.finditer(prompt or ""):
        slug = m.group(1).lower()
        if slug not in seen:
            seen.append(slug)
    return seen


def worn(prompt: str) -> list[str]:
    """Each handle with the look it wears (`maya:winter`, or `maya`; a prompt wears the first look it names for a
    character), once: what a rewording must keep."""
    looks: dict[str, str | None] = {}
    for m in MENTION_LOOK.finditer(prompt or ""):
        slug = m.group(1).lower()
        if looks.get(slug) is None:
            looks[slug] = (m.group(2) or "").lower() or None
    return [f"{slug}:{look}" if look else slug for slug, look in looks.items()]


@dataclass
class Resolution:
    prompt: str
    media: list[dict[str, str]] = field(default_factory=list)  # [{role, asset_id}] to attach
    warnings: list[str] = field(default_factory=list)
    elements: list[dict[str, Any]] = field(default_factory=list)  # [{id, slug, name, attached, look}]
    # every @slug in the prompt (and a mentioned cast's characters) -> how the prompt names it now (for direction
    # that refers to cast members)
    names: dict[str, str] = field(default_factory=dict)


def per_member(capacity: int, members: int) -> int:
    """How many pictures each character of a cast attaches: an even share of the room left, two at most and one at
    least (so with too little room the last ones get none and are described in words)."""
    return max(1, min(IMAGES_PER_KIND["cast"], capacity // max(1, members)))


async def cast_members(session: AsyncSession, cast_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Element]]:
    """Each cast (group)'s characters that are still there, in order."""
    if not cast_ids:
        return {}
    rows = await session.execute(
        select(ElementMember.group_id, Element).join(Element, Element.id == ElementMember.member_id)
        .where(ElementMember.group_id.in_(cast_ids), Element.kind == ElementKind.character,
               Element.deleted_at.is_(None)).order_by(ElementMember.sort))
    out: dict[uuid.UUID, list[Element]] = {}
    for group_id, element in rows:
        out.setdefault(group_id, []).append(element)
    return out


async def resolve_mentions(
    session: AsyncSession,
    user_id: uuid.UUID,
    spec: ModelSpec,
    mode_id: str,
    prompt: str,
    existing_media: list[dict[str, str]],
    speakers: list[str] | tuple[str, ...] = (),
) -> Resolution:
    slugs = mentions(prompt)
    slugs += [s for s in dict.fromkeys(x.lower() for x in speakers) if s not in slugs]
    if not slugs:
        return Resolution(prompt=prompt)

    rows = (await session.execute(select(Element).where(
        Element.user_id == user_id, Element.slug.in_(slugs), Element.deleted_at.is_(None)))).scalars().all()
    by_slug = {e.slug: e for e in rows}
    res = Resolution(prompt=prompt, names={s: f"@{s}" for s in slugs})
    for slug in slugs:
        if slug not in by_slug:
            res.warnings.append(f"no saved character, location or prop called @{slug}; left as written")
    members = await cast_members(session, [e.id for e in rows if e.kind == ElementKind.cast])
    people = list({e.id: e for e in [*rows, *(m for crew in members.values() for m in crew)]}.values())

    # (element, look) -> its pictures in order; look None: the main ones, all a plain mention attaches
    images: dict[tuple[uuid.UUID, str | None], list[uuid.UUID]] = {}
    if people:
        links = (await session.execute(
            select(ElementAsset.element_id, ElementAsset.look, ElementAsset.asset_id)
            .join(Asset, Asset.id == ElementAsset.asset_id)
            .where(ElementAsset.element_id.in_([e.id for e in people]), Asset.status == AssetStatus.ready,
                   Asset.deleted_at.is_(None), Asset.kind == "image", ElementAsset.view.not_in(UNATTACHED_VIEWS))
            .order_by(ElementAsset.is_primary.desc(), ElementAsset.sort, Asset.created_at)
        )).all()
        for element_id, look, asset_id in links:
            images.setdefault((element_id, look), []).append(asset_id)

    looks: dict[str, str] = {}  # slug -> the look its mentions wear: the first one it has
    for m in MENTION_LOOK.finditer(prompt or ""):
        slug, word = m.group(1).lower(), (m.group(2) or "").lower()
        if not word or slug not in by_slug:
            continue
        if word not in (by_slug[slug].looks or {}):
            warning = f"@{slug} has no look called {word}, so it's shown as it is"
        elif looks.setdefault(slug, word) != word:
            warning = f"@{slug} wears one look in a prompt: {looks[slug]}, not {word}"
        else:
            continue
        if warning not in res.warnings:
            res.warnings.append(warning)

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
    lora_base = str((spec.elements or {}).get("lora_base") or "")
    trained = await _ready_loras(session, people, lora_base) if strategy == "lora" else set()
    voice_role = (spec.elements or {}).get("voice_role")
    voice_token = (spec.elements or {}).get("voice_token") or "Audio {n}"
    vslot = mode.slot(voice_role) if mode and voice_role else None
    vused = sum(1 for m in existing_media if m.get("role") == voice_role) if vslot else 0
    vroom, vnext, vleft = max(0, (vslot.max if vslot else 0) - vused), vused + 1, seconds_cap(spec, mode_id, voice_role)
    # recordings already on the request, by asset id: speakers sharing one get its token, not a second copy
    vtokens = {str(m.get("asset_id")): voice_token.format(n=n) for n, m in enumerate(
        (m for m in existing_media if m.get("role") == voice_role), start=1)} if vslot else {}
    if vtokens and vleft != float("inf"):  # what's left of the mode's seconds of reference audio, counted per file
        ids = [a for a in vtokens if _is_uuid(a)]  # anything else is refused when the request is checked
        have = {str(i): ms for i, ms in (await session.execute(select(Asset.id, Asset.duration_ms).where(
            Asset.id.in_([uuid.UUID(a) for a in ids]), Asset.user_id == user_id))).all()} if ids else {}
        vleft -= sum((have.get(str(m.get("asset_id"))) or 0) / 1000 for m in existing_media if m.get("role") == voice_role)
    samples = await voice_samples(session, user_id, people) if vslot else {}
    ordered = sorted((by_slug[s] for s in slugs if s in by_slug), key=lambda e: KIND_ORDER.get(e.kind.value, 9))
    # a cast's characters stand in its place: pictures, identities and voices are handed out in this order
    everyone = list({e.id: e for x in ordered for e in (members.get(x.id) or [x])}.values())
    talking = {s.lower() for s in speakers}
    # the dialogue's speakers first, so someone who only appears in the shot can't take the room their voices need
    chosen, tried, vroom = pick_voices([e.id for e in sorted(everyone, key=lambda e: e.slug not in talking)], samples,
                                       vslot, vroom, vleft, set(vtokens)) if vslot else (set(), set(), vroom)
    # lora_list pairs the two roles by position, so two-expert characters go first and single files after them
    lora_pairs: list[tuple[str, str]] = []
    lora_singles: list[str] = []

    def attach(element: Element, want: int, look: str | None, attached: list[str]) -> tuple[str, bool]:
        """Give one element up to `want` pictures (a look's first, then its main ones), its trained identity and
        its voice, adding what it attached to `attached`. Returns how the prompt names it, and whether a picture or
        an identity shows it (if not, it's described in words)."""
        nonlocal capacity, next_index, vnext
        label, shown = element.name, False
        pictures = [*(images.get((element.id, look), []) if look else []), *images.get((element.id, None), [])]
        if strategy == "refs_native" and slot is not None and capacity > 0:
            mine = [str(a) for a in pictures[: min(want, capacity)]]
            res.media.extend({"role": ref_role, "asset_id": a} for a in mine)
            attached += mine
            capacity -= len(mine)
            shown = bool(mine)
            if mine and token:
                label = f"{element.name} ({', '.join(token.format(n=next_index + i) for i in range(len(mine)))})"
            next_index += len(mine)
            if not pictures:
                res.warnings.append(f"@{element.slug} has no reference images yet; described in words")
        elif strategy == "refs_native" and slot is None:
            res.warnings.append(f"{spec.name} {mode.label if mode else mode_id} can't take reference images, so "
                                f"@{element.slug} is described in words. Use a mode with reference images for its look.")
        elif strategy == "refs_native" and capacity <= 0:
            res.warnings.append(f"no reference slots left for @{element.slug}; described in words")
        elif strategy == "lora":
            lora = identities(element.lora).get(lora_base) or {}
            low, high = str(lora.get("low") or ""), str(lora.get("high") or "")
            if low in trained and lora_room["lora"] > 0:
                shown = True
                lora_room["lora"] -= 1
                if high in trained and lora_room["lora_high"] > 0:
                    lora_room["lora_high"] -= 1
                    lora_pairs.append((low, high))
                    attached += [low, high]
                else:
                    lora_singles.append(low)
                    attached.append(low)
                if lora.get("trigger"):
                    label = f"{element.name} ({lora['trigger']})"
            elif low in trained:
                res.warnings.append(f"no room for another character's identity, so @{element.slug} is described in words")
            elif element.kind.value == "character":
                res.warnings.append(f"@{element.slug} has no trained identity for {spec.name}, so it's described in "
                                    "words. Train one on the character's page for a face that holds.")
        elif strategy == "keyframe_first" and element.kind.value == "character":
            res.warnings.append(f"{spec.name} doesn't take reference images, so @{element.slug} is described in "
                                f"words. For a consistent look, make a start frame from its pictures first.")
        voiced = bool((element.voice or {}).get("params") or (element.voice or {}).get("sample_asset_id"))
        if element.id in tried:
            asset_id, secs = samples[element.id]
            if asset_id in chosen and asset_id not in vtokens:  # numbered in the prompt's order
                res.media.append({"role": voice_role, "asset_id": asset_id})
                vtokens[asset_id] = voice_token.format(n=vnext)
                vnext += 1
            if asset_id in vtokens:  # attached now, or before for another speaker with the same voice
                attached.append(asset_id)
                spoken = f"voice {vtokens[asset_id]}"
                label = f"{label[:-1]}; {spoken})" if label.endswith(")") else f"{element.name} ({spoken})"
            else:
                res.warnings.append(f"@{element.slug}'s voice sample is {secs:g} s, which {spec.name} can't take "
                                    f"here, so it speaks in the model's own voice")
        elif vslot is not None and voiced and vroom > 0:
            res.warnings.append(f"@{element.slug} has no voice sample yet, so {spec.name} speaks in its own voice. "
                                "Add one on its page.")
        elif voiced and spec.prompt.dialogue and vslot is None:
            other = next((m.label for m in spec.modes if voice_role and m.slot(voice_role)), None)
            res.warnings.append(f"{spec.name} {mode.label if mode else mode_id} speaks in its own voice, so "
                                f"@{element.slug}'s voice isn't used" + (f" ({other} can take it)." if other else "."))
        return label, shown

    done: dict[uuid.UUID, str] = {}  # element -> how the prompt names it, once it has had its share
    replacements: dict[str, tuple[str, str]] = {}  # slug -> (first mention text, later mention text)
    for element in ordered:
        attached: list[str] = []
        look = looks.get(element.slug)
        if crew := members.get(element.id):
            # one of them mentioned on their own already has their pictures (characters go first): named the same
            todo = [who for who in crew if who.id not in done]
            share = per_member(capacity, len(todo))
            told: dict[uuid.UUID, str] = {}  # those nothing shows are described after their name, as on their own
            for who in todo:
                done[who.id], shown = attach(who, share, None, attached)
                if not shown:
                    told[who.id] = clip(who.description or "", DESCRIPTION_LIMIT, at_comma=True).rstrip(" .")
            label = ", ".join(done[who.id] for who in crew)
            intro = ", ".join(x for who in crew for x in (done[who.id], told.get(who.id)) if x)
            for who in crew:  # a shot's direction may name one of them (a line, a feeling)
                res.names.setdefault(who.slug, done[who.id])
        else:
            if element.kind == ElementKind.cast:
                res.warnings.append(f"@{element.slug} has no characters in it yet; described in words")
            label, _ = attach(element, IMAGES_PER_KIND.get(element.kind.value, 1), look, attached)
            done[element.id] = intro = label
        # a long one ends after its last whole phrase that fits, never mid-word; a look's words come after it
        description = clip(element.description or "", DESCRIPTION_LIMIT, at_comma=True).rstrip(" .")
        worn = str(((element.looks or {}).get(look) or {}).get("description") or "") if look else ""
        said = ", ".join(x for x in (description, " ".join(worn.split()).rstrip(" ,;.")) if x)
        first = ", ".join(x for x in (intro, said) if x)
        if first != label:  # words after the name end with a comma, so the sentence goes on: "Maya, a dancer, waves"
            first += ","
        replacements[element.slug] = (first, label)
        res.names[element.slug] = label
        res.elements.append({"id": str(element.id), "slug": element.slug, "name": element.name, "attached": attached,
                             "look": look})

    for low, high in lora_pairs:
        res.media += [{"role": "lora", "asset_id": low}, {"role": "lora_high", "asset_id": high}]
    res.media += [{"role": "lora", "asset_id": low} for low in lora_singles]

    seen: set[str] = set()

    def substitute(m: re.Match[str]) -> str:
        slug, word = m.group(1).lower(), m.group(2)
        if slug not in replacements:
            return m.group(0)
        first, later = replacements[slug]
        text = later if slug in seen else first
        seen.add(slug)
        # a look it doesn't have (or a second one) stays readable, as the word it was
        return f"{text} {word}" if word and word.lower() != looks.get(slug) else text

    res.prompt = MENTION_LOOK.sub(substitute, prompt).replace(",,", ",").replace(", .", ".").replace(",.", ".")
    return res


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def pick_voices(order: list[uuid.UUID], samples: dict[uuid.UUID, tuple[str, float]], slot: MediaSlot, room: int,
                seconds: float, have: set[str]) -> tuple[set[str], set[uuid.UUID], int]:
    """Which voice samples go to the model, taking elements in `order`: each sample while the slot has room for
    another file and `seconds` left for it, within the slot's per-file limits. A recording already going (`have`, or
    another element's) is shared. Returns the recordings going, the elements whose sample was offered (those with
    none, or that found no room for one, aren't), and the room left."""
    chosen, tried = set(have), set()
    for eid in order:
        sample = samples.get(eid)
        if not sample or not (sample[0] in chosen or room > 0):
            continue
        tried.add(eid)
        asset_id, secs = sample
        if (asset_id not in chosen and (slot.min_duration_s or 0) <= secs <= (slot.max_duration_s or secs)
                and secs <= seconds + 0.05):
            chosen.add(asset_id)
            room, seconds = room - 1, seconds - secs
    return chosen, tried, room


def seconds_cap(spec: ModelSpec, mode_id: str, role: str | None) -> float:
    """How many seconds of `role` inputs the mode takes in all, from rules that apply to the mode alone
    (H3: 15 s of reference audio)."""
    caps = [r.max_input_seconds[role] for r in spec.rules if role in r.max_input_seconds and set(r.when) <= {"mode"}
            and mode_id in (r.when["mode"] if isinstance(r.when.get("mode"), list) else [r.when.get("mode", mode_id)])]
    return min(caps, default=float("inf"))


async def voice_samples(session: AsyncSession, user_id: uuid.UUID, elements: Any) -> dict[uuid.UUID, tuple[str, float]]:
    """Each element's voice sample (a recording of its voice, `voice.sample_asset_id`) as (asset id, seconds), if
    it's still a ready recording. A character whose voice is one of your saved voices and has no sample of its own
    uses that voice's."""
    def voice_id(e: Any) -> str | None:
        return ((e.voice or {}).get("params") or {}).get("voice_id")

    own: dict[uuid.UUID, str | None] = {e.id: (e.voice or {}).get("sample_asset_id") for e in elements}
    if any(not own[e.id] and voice_id(e) for e in elements):
        saved = (await session.execute(select(Element).where(
            Element.user_id == user_id, Element.kind == ElementKind.voice, Element.deleted_at.is_(None)))).scalars()
        by_voice = {voice_id(v): (v.voice or {}).get("sample_asset_id") for v in saved}
        own.update({e.id: by_voice.get(voice_id(e)) for e in elements if not own[e.id] and voice_id(e)})
    ids = [uuid.UUID(str(s)) for s in own.values() if s]
    if not ids:
        return {}
    ready = {str(a.id): (a.duration_ms or 0) / 1000 for a in (await session.execute(select(Asset).where(
        Asset.id.in_(ids), Asset.user_id == user_id, Asset.status == AssetStatus.ready, Asset.deleted_at.is_(None),
        Asset.kind == "audio"))).scalars()}
    return {eid: (str(s), ready[str(s)]) for eid, s in own.items() if s and str(s) in ready}


async def _ready_loras(session: AsyncSession, rows: Any, base: str) -> set[str]:
    """The LoRA files of the mentioned elements' identities for `base` that still exist and are ready."""
    ids = [uuid.UUID(str(v)) for e in rows for k, v in (identities(e.lora).get(base) or {}).items()
           if k in ("low", "high") and v]
    if not ids:
        return set()
    found = (await session.execute(select(Asset.id).where(
        Asset.id.in_(ids), Asset.status == AssetStatus.ready, Asset.deleted_at.is_(None), Asset.kind == "lora"))).scalars()
    return {str(i) for i in found}
