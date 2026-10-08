"""Projects (Cinema Studio films): a versioned document with revisions, a Claude shot plan, and each shot's takes.

Saves name the version they started from; a stale save gets 409 with the current version, so two tabs never
overwrite each other silently. Every save is kept as a revision that can be restored. Takes are ordinary
generations tagged with the project and shot (`project_id`, `shot_key`); the editor picks them into the document.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import AssetOut
from studio.api.serializers import asset_out
from studio.budget.guard import BudgetBlocked
from studio.cinema.document import CinemaDocument
from studio.cinema.planner import MAX_SHOTS, plan_shots
from studio.db.models import (
    Asset,
    Element,
    Generation,
    GenerationAttempt,
    PipelineRun,
    Project,
    ProjectRevision,
    SpendLedger,
    User,
)
from studio.domain.enums import AssetKind, LedgerEntry
from studio.jutsu3d.document import Jutsu3dDocument, starter_scene
from studio.llm.client import LLMError, LLMUnavailable
from studio.media.ffmpeg import probe
from studio.pipelines.recipes.cinema import frame_kind
from studio.registry.loader import get_registry
from studio.storage.local import get_storage

router = APIRouter(prefix="/projects", tags=["projects"])

KINDS = {"cinema": CinemaDocument, "jutsu3d": Jutsu3dDocument}


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "cinema"
    title: str = Field(min_length=1, max_length=200)
    document: dict[str, Any] = Field(default_factory=dict)


class ProjectSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(description="The version this edit started from")
    title: str | None = Field(None, min_length=1, max_length=200)
    document: dict[str, Any]
    note: str = Field("", max_length=300)


class ProjectOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    version: int
    document: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProjectSummary(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    version: int
    scenes: int
    shots: int
    shots_with_takes: int
    objects: int = Field(0, description="3D scenes: how many objects")
    cover: AssetOut | None
    updated_at: datetime


class RevisionOut(BaseModel):
    version: int
    title: str
    note: str
    shots: int
    created_at: datetime


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script: str = Field("", max_length=40000, description="Defaults to the project's saved script")
    logline: str = Field("", max_length=600, description="Defaults to the project's saved logline")
    cast: list[str] | None = Field(None, max_length=12, description="@mention slugs; defaults to the saved cast")
    notes: str = Field("", max_length=2000)
    max_shots: int = Field(12, ge=1, le=MAX_SHOTS)


class PlanOut(BaseModel):
    scenes: list[dict[str, Any]]
    warnings: list[str]
    model: str
    mock: bool
    cost_usd: float


class TakeOut(BaseModel):
    generation_id: uuid.UUID | None = Field(description="None for a take cut or joined from others")
    frame: Literal["start", "end"] | None = Field(None, description="For hero frames: made as an opening frame or a "
                                                                    "closing one")
    shot_key: str
    made_by: str = Field("model", description="model; multi_shot (cut from a scene take); extend_after or "
                                              "extend_before (joined with a new part); edit; lipsync; region_edit")
    parent_asset_id: uuid.UUID | None = Field(None, description="The take this one was made from")
    output_type: str
    status: str
    progress: float | None
    error: str | None
    prompt: str
    asset: AssetOut | None
    created_at: datetime


# ---------------------------------------------------------------- helpers


def _validate(kind: str, document: dict[str, Any]) -> dict[str, Any]:
    model = KINDS.get(kind)
    if model is None:
        raise HTTPException(422, f"unknown project kind {kind!r}")
    try:
        return model.model_validate(document).model_dump(mode="json")
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                                           for e in exc.errors())[:2000]) from None


async def _available(session: AsyncSession, user: User, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Which of these assets the user still has."""
    if not ids:
        return set()
    return set((await session.execute(select(Asset.id).where(
        Asset.id.in_(ids), Asset.user_id == user.id, Asset.deleted_at.is_(None)))).scalars())


PICKS = ("hero_asset_id", "end_asset_id", "take_asset_id")  # what a shot can pick: opening and closing frames, a take


def _referenced(kind: str, doc: dict[str, Any]) -> set[str]:
    """The assets a document points at: a film's picked frames and takes, a 3D scene's models and cover."""
    if kind == "jutsu3d":
        return Jutsu3dDocument.model_validate(doc).asset_ids()
    picks = {v for scene in doc.get("scenes", []) for shot in scene.get("shots", [])
             for v in (shot.get(field) for field in PICKS) if v}
    return picks | {a["asset_id"] for a in (doc.get("cut") or {}).get("audio", []) if a.get("asset_id")}


async def _check_assets(session: AsyncSession, user: User, doc: dict[str, Any], kind: str = "cinema") -> None:
    """Assets a document points at must be the user's own."""
    ids = _referenced(kind, doc)
    try:
        wanted = {uuid.UUID(i) for i in ids}
    except ValueError:
        raise HTTPException(422, "picked frames and takes must be asset ids") from None
    if missing := wanted - await _available(session, user, wanted):
        raise HTTPException(422, f"assets not found: {', '.join(sorted(map(str, missing)))}")


async def _drop_gone_picks(session: AsyncSession, user: User, doc: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """An old version may pick frames or takes, or use sounds, deleted since; unpick or remove those. Returns the
    document and how many."""
    sounds = (doc.get("cut") or {}).get("audio", [])
    picked = _picked(doc, None) | {uuid.UUID(a["asset_id"]) for a in sounds if _is_uuid(a.get("asset_id"))}
    gone = {str(a) for a in picked - await _available(session, user, picked)}
    cleared = 0

    def clean(shot: dict[str, Any]) -> dict[str, Any]:
        nonlocal cleared
        shot = dict(shot)
        for field in PICKS:
            value = shot.get(field)
            if value and (str(value) in gone or not _is_uuid(value)):
                shot[field] = None
                cleared += 1
        return shot

    scenes = [{**scene, "shots": [clean(s) for s in scene.get("shots", [])]} for scene in doc.get("scenes", [])]
    kept = [a for a in sounds if _is_uuid(a.get("asset_id")) and str(a["asset_id"]) not in gone]
    cleared += len(sounds) - len(kept)
    out = {**doc, "scenes": scenes}
    if "cut" in doc:
        out["cut"] = {**doc["cut"], "audio": kept}
    return out, cleared


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _out(p: Project) -> ProjectOut:
    return ProjectOut(id=p.id, kind=p.kind, title=p.title, version=p.version, document=p.document,
                      created_at=p.created_at, updated_at=p.updated_at)


async def _project(session: AsyncSession, user: User, project_id: uuid.UUID, *, lock: bool = False) -> Project:
    p = await session.get(Project, project_id, with_for_update=lock)
    if p is None or p.user_id != user.id or p.deleted_at is not None:
        raise HTTPException(404, "project not found")
    return p


def _shots(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [shot for scene in doc.get("scenes", []) for shot in scene.get("shots", [])]


def _revision(p: Project, note: str) -> ProjectRevision:
    return ProjectRevision(project_id=p.id, user_id=p.user_id, version=p.version, title=p.title,
                           document=p.document, note=note)


# ---------------------------------------------------------------- CRUD


@router.get("", response_model=list[ProjectSummary])
async def list_projects(kind: str | None = None, session: AsyncSession = Depends(get_session),
                        user: User = Depends(current_user)) -> list[ProjectSummary]:
    q = select(Project).where(Project.user_id == user.id, Project.deleted_at.is_(None))
    if kind:
        q = q.where(Project.kind == kind)
    projects = (await session.execute(q.order_by(Project.updated_at.desc()))).scalars().all()
    covers: dict[uuid.UUID, str] = {}
    for p in projects:
        picked = p.document.get("cover_asset_id") or next(
            (s.get("take_asset_id") or s.get("hero_asset_id") for s in _shots(p.document)
             if s.get("take_asset_id") or s.get("hero_asset_id")), None)
        if picked:
            covers[p.id] = picked
    assets = {str(a.id): a for a in (await session.execute(select(Asset).where(
        Asset.id.in_([uuid.UUID(c) for c in covers.values()]), Asset.user_id == user.id,
        Asset.deleted_at.is_(None)))).scalars()} if covers else {}
    return [ProjectSummary(
        id=p.id, kind=p.kind, title=p.title, version=p.version, scenes=len(p.document.get("scenes", [])),
        shots=len(_shots(p.document)), shots_with_takes=sum(1 for s in _shots(p.document) if s.get("take_asset_id")),
        objects=len(p.document.get("objects", [])),
        cover=asset_out(assets[covers[p.id]]) if covers.get(p.id) in assets else None, updated_at=p.updated_at,
    ) for p in projects]


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectIn, session: AsyncSession = Depends(get_session),
                         user: User = Depends(current_user)) -> ProjectOut:
    # a new 3D scene starts with a camera, a figure and the sun
    doc = _validate(body.kind, body.document or (starter_scene() if body.kind == "jutsu3d" else {}))
    await _check_assets(session, user, doc, body.kind)
    p = Project(user_id=user.id, kind=body.kind, title=body.title.strip(), document=doc, version=1)
    session.add(p)
    await session.flush()
    session.add(_revision(p, "Created"))
    await session.commit()
    return _out(p)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                      user: User = Depends(current_user)) -> ProjectOut:
    return _out(await _project(session, user, project_id))


@router.put("/{project_id}", response_model=ProjectOut,
            responses={409: {"description": "Saved meanwhile; reload and reapply"}})
async def save_project(project_id: uuid.UUID, body: ProjectSave, session: AsyncSession = Depends(get_session),
                       user: User = Depends(current_user)) -> ProjectOut:
    p = await _project(session, user, project_id, lock=True)
    if body.version != p.version:
        raise HTTPException(409, f"this project was saved meanwhile (version {p.version}); reload to see it")
    doc = _validate(p.kind, body.document)
    await _check_assets(session, user, doc, p.kind)
    p.document, p.version = doc, p.version + 1
    if body.title:
        p.title = body.title.strip()
    p.updated_at = datetime.now(UTC)
    session.add(_revision(p, body.note))
    await session.commit()
    return _out(p)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                         user: User = Depends(current_user)) -> None:
    """Hide the project. Its takes stay in the library."""
    p = await _project(session, user, project_id, lock=True)
    p.deleted_at = datetime.now(UTC)
    await session.commit()


@router.get("/{project_id}/revisions", response_model=list[RevisionOut])
async def list_revisions(project_id: uuid.UUID, limit: int = Query(50, ge=1, le=200),
                         session: AsyncSession = Depends(get_session),
                         user: User = Depends(current_user)) -> list[RevisionOut]:
    p = await _project(session, user, project_id)
    rows = (await session.execute(select(ProjectRevision).where(ProjectRevision.project_id == p.id)
                                  .order_by(ProjectRevision.version.desc()).limit(limit))).scalars().all()
    return [RevisionOut(version=r.version, title=r.title, note=r.note, shots=len(_shots(r.document)),
                        created_at=r.created_at) for r in rows]


@router.post("/{project_id}/revisions/{version}/restore", response_model=ProjectOut)
async def restore_revision(project_id: uuid.UUID, version: int, session: AsyncSession = Depends(get_session),
                           user: User = Depends(current_user)) -> ProjectOut:
    """Make an earlier version current again (as a new version, so nothing is lost)."""
    p = await _project(session, user, project_id, lock=True)
    rev = (await session.execute(select(ProjectRevision).where(
        ProjectRevision.project_id == p.id, ProjectRevision.version == version))).scalar_one_or_none()
    if rev is None:
        raise HTTPException(404, f"no version {version}")
    # what was deleted since that version can't be referenced any more (a save would refuse it)
    if p.kind == "jutsu3d":
        scene = Jutsu3dDocument.model_validate(rev.document)
        refs = {uuid.UUID(a) for a in scene.asset_ids()}
        gone = {str(a) for a in refs - await _available(session, user, refs)}
        restored, cleared = scene.without_assets(gone)
        document = restored.model_dump(mode="json")
    else:
        document, cleared = await _drop_gone_picks(session, user, rev.document)
    p.document, p.title, p.version = document, rev.title, p.version + 1
    p.updated_at = datetime.now(UTC)
    note = f"Restored version {version}"
    if cleared:
        note += f"; {cleared} pick deleted since was cleared" if cleared == 1 else f"; {cleared} picks deleted since were cleared"
    session.add(_revision(p, note))
    await session.commit()
    return _out(p)


# ---------------------------------------------------------------- shot plan and takes


@router.post("/{project_id}/plan", response_model=PlanOut,
             responses={402: {"description": "Spending limit"}, 502: {"description": "Claude failed"},
                        503: {"description": "No Anthropic key"}})
async def plan(project_id: uuid.UUID, body: PlanIn, session: AsyncSession = Depends(get_session),
               user: User = Depends(current_user)) -> PlanOut:
    """Claude proposes scenes and shots for the script. Nothing is saved; apply the proposal to the document."""
    p = await _project(session, user, project_id)
    doc = CinemaDocument.model_validate(p.document)
    script, logline = body.script or doc.script, body.logline or doc.logline
    slugs = doc.cast if body.cast is None else body.cast
    if not (script.strip() or logline.strip()):
        raise HTTPException(422, "write a script or a logline first")
    cast_rows = (await session.execute(select(Element).where(
        Element.user_id == user.id, Element.slug.in_(slugs), Element.deleted_at.is_(None)))).scalars().all() \
        if slugs else []
    cast = [(e.slug, f"{e.name}. {e.description}".strip()) for e in cast_rows]
    try:
        result, scenes, warnings = await plan_shots(session, user.id, script=script, logline=logline, cast=cast,
                                                    notes=body.notes, max_shots=body.max_shots)
    except BudgetBlocked as exc:
        raise HTTPException(402, f"Planning would go over your spending limit: {exc}") from exc
    except LLMUnavailable as exc:
        raise HTTPException(503, exc.message) from exc
    except LLMError as exc:
        await session.commit()  # a refusal was still billed
        raise HTTPException(502, exc.message) from exc
    await session.commit()
    return PlanOut(scenes=[s.model_dump(mode="json") for s in scenes], warnings=warnings, model=result.model,
                   mock=result.mock, cost_usd=float(result.cost_usd))


TAKES_PAGE = 400  # newest generations and derived clips listed per kind; picked ones are always included


def _generation_take(g: Generation, a: Asset | None) -> TakeOut:
    parent = next((m["asset_id"] for m in g.request.get("media", []) if m.get("role") == "input_video"), None)
    spec = get_registry().model(g.model_id)
    mode = spec.mode(g.mode) if spec else None
    made_by = ("lipsync" if mode and "lipsync" in mode.ops else "edit") if parent else "model"
    prompt = g.request.get("prompt", "")
    frame = frame_kind(prompt) if g.output_type == "image" else None
    return TakeOut(generation_id=g.id, shot_key=g.shot_key or "", output_type=g.output_type, status=g.status.value,
                   progress=g.progress, error=g.error_message, prompt=prompt, frame=frame,
                   created_at=g.created_at, made_by=made_by, parent_asset_id=parent,
                   asset=asset_out(a) if a else None)


def _derived_take(a: Asset) -> TakeOut:
    m = a.metadata_ or {}
    return TakeOut(generation_id=None, shot_key=m["shot_key"], made_by=m.get("made_by", "derived"),
                   parent_asset_id=m.get("parent_asset_id"), output_type=a.kind.value, status="succeeded",
                   progress=1.0, error=None, prompt=m.get("prompt", ""), asset=asset_out(a), created_at=a.created_at)


def _picked(doc: dict[str, Any], shot_key: str | None) -> set[uuid.UUID]:
    """Frames and takes the document has picked (for one shot, or all)."""
    out = set()
    for scene in doc.get("scenes", []):
        for shot in scene.get("shots", []):
            if shot_key and shot.get("id") != shot_key:
                continue
            for v in (shot.get(field) for field in PICKS):
                try:
                    out.add(uuid.UUID(str(v)))
                except ValueError:
                    continue
    return out


class DialogueOut(BaseModel):
    shot_key: str
    asset: AssetOut
    lip_synced: bool = Field(description="The shot's picked take was lip-synced to this track (its own sound is the "
                                         "same speech)")


LINEAGE_HOPS = 6


async def _synced_to(session: AsyncSession, user: User, take_id: uuid.UUID) -> tuple[uuid.UUID | None, bool]:
    """The speech a take was lip-synced to, following a derived take (extended, edited, cut from a scene) back to
    the take it was made from. Also whether that speech still starts where the take starts (False once a part
    was joined before it)."""
    aligned = True
    current = await session.get(Asset, take_id)
    for _ in range(LINEAGE_HOPS):
        if current is None or current.user_id != user.id:
            return None, aligned
        if current.generation_id is not None:
            g = await session.get(Generation, current.generation_id)
            media = ((g.request or {}).get("media") or []) if g is not None else []
            audio = next((m.get("asset_id") for m in media if m.get("role") == "input_audio"), None)
            return (uuid.UUID(audio) if audio and _is_uuid(audio) else None), aligned
        meta = current.metadata_ or {}
        if meta.get("made_by") == "extend_before":
            aligned = False
        parent = meta.get("parent_asset_id")
        if not parent or not _is_uuid(parent):
            return None, aligned
        current = await session.get(Asset, uuid.UUID(parent))
    return None, aligned


@router.get("/{project_id}/dialogue", response_model=list[DialogueOut])
async def list_dialogue(project_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                        user: User = Depends(current_user)) -> list[DialogueOut]:
    """Each shot's speech as its own track, for the cut's dialogue track: the audio the picked take was lip-synced
    to (a take extended or edited from a lip-synced one counts too), or else the newest dialogue track a takes run
    made for the shot (lines voiced and joined). A shot whose take carries its speech somewhere other than its
    start (a part joined before it) is left out: its own sound already has the speech, in the right place."""
    p = await _project(session, user, project_id)
    picks = {shot["id"]: shot.get("take_asset_id") for shot in _shots(p.document) if shot.get("id")}
    found: dict[str, tuple[uuid.UUID, bool]] = {}
    in_take: set[str] = set()
    for shot, take_id in picks.items():
        if not take_id or not _is_uuid(take_id):
            continue
        audio, aligned = await _synced_to(session, user, uuid.UUID(take_id))
        if audio is not None and aligned:
            found[shot] = (audio, True)
        elif audio is not None:
            in_take.add(shot)

    runs = [str(r) for r in (await session.execute(select(PipelineRun.id).where(
        PipelineRun.user_id == user.id, PipelineRun.input["project_id"].astext == str(p.id)))).scalars()]
    meta = Asset.metadata_
    tracks = (await session.execute(select(Asset).where(
        Asset.user_id == user.id, Asset.deleted_at.is_(None), Asset.kind == AssetKind.audio,
        meta["pipeline_run_id"].astext.in_(runs), meta["shot_key"].astext.in_(list(picks)))
        .order_by(Asset.created_at.desc()))).scalars().all() if runs and picks else []
    for t in tracks:
        if t.metadata_["shot_key"] not in in_take:
            found.setdefault(t.metadata_["shot_key"], (t.id, False))

    rows = {a.id: a for a in (await session.execute(select(Asset).where(
        Asset.id.in_([aid for aid, _ in found.values()]), Asset.user_id == user.id,
        Asset.deleted_at.is_(None)))).scalars().all()} if found else {}
    for a in rows.values():
        if a.duration_ms is None:  # tracks joined before derived audio recorded its length
            a.duration_ms = (await probe(get_storage().path_for(a.storage_key))).duration_ms
    await session.commit()
    return [DialogueOut(shot_key=shot, asset=asset_out(rows[aid]), lip_synced=synced)
            for shot, (aid, synced) in found.items() if aid in rows]


class SpendOut(BaseModel):
    total_usd: float = Field(description="Every generation made for the film, including whole-scene takes")
    shots: dict[str, float] = Field(description="Spent per shot id, for generations made for one shot (frames, takes, "
                                                "voiced lines, lip syncs, extends and edits)")


@router.get("/{project_id}/spend", response_model=SpendOut)
async def project_spend(project_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                        user: User = Depends(current_user)) -> SpendOut:
    """What the film's generations have cost so far: every charge in the spend ledger for their attempts, so
    attempts a provider billed and then failed (or that failed over to another route) count too. Jobs still
    running count once they settle."""
    p = await _project(session, user, project_id)
    rows = (await session.execute(
        select(Generation.shot_key, func.sum(SpendLedger.amount_usd))
        .join(GenerationAttempt, GenerationAttempt.generation_id == Generation.id)
        .join(SpendLedger, SpendLedger.generation_attempt_id == GenerationAttempt.id)
        .where(Generation.project_id == p.id, Generation.user_id == user.id, SpendLedger.user_id == user.id,
               SpendLedger.entry_type == LedgerEntry.charge)
        .group_by(Generation.shot_key))).all()
    shots = {key: float(usd) for key, usd in rows if key and usd}
    return SpendOut(total_usd=float(sum((usd for _, usd in rows if usd), start=0)), shots=shots)


@router.get("/{project_id}/takes", response_model=list[TakeOut])
async def list_takes(project_id: uuid.UUID, shot_key: str | None = None, session: AsyncSession = Depends(get_session),
                     user: User = Depends(current_user)) -> list[TakeOut]:
    """Hero frames and takes made for the project's shots, newest first, finished or not. Besides the generations
    made for a shot, this lists takes cut from a multi-shot scene take and takes extended or edited from another
    (derived clips whose metadata names the project and shot), each with the take it came from. Takes the document
    has picked are always listed, however old, so the storyboard and the cut can show and export them."""
    p = await _project(session, user, project_id)
    q = select(Generation).where(Generation.project_id == p.id, Generation.user_id == user.id,
                                 Generation.shot_key.is_not(None))
    if shot_key:
        q = q.where(Generation.shot_key == shot_key)
    gens = list((await session.execute(q.order_by(Generation.created_at.desc()).limit(TAKES_PAGE))).scalars().all())
    assets = (await session.execute(select(Asset).where(
        Asset.generation_id.in_([g.id for g in gens]), Asset.deleted_at.is_(None))
        .order_by(Asset.output_index))).scalars().all() if gens else []
    by_gen: dict[uuid.UUID, list[Asset]] = {}
    for a in assets:
        by_gen.setdefault(a.generation_id, []).append(a)
    out = [_generation_take(g, a) for g in gens for a in (by_gen.get(g.id) or [None])]

    meta = Asset.metadata_
    dq = select(Asset).where(Asset.user_id == user.id, Asset.deleted_at.is_(None), Asset.generation_id.is_(None),
                             meta["project_id"].astext == str(p.id), meta["shot_key"].astext.is_not(None))
    if shot_key:
        dq = dq.where(meta["shot_key"].astext == shot_key)
    out += [_derived_take(a) for a in
            (await session.execute(dq.order_by(Asset.created_at.desc()).limit(TAKES_PAGE))).scalars().all()]

    # picked takes older than the newest page
    listed = {t.asset.id for t in out if t.asset}
    if missing := _picked(p.document, shot_key) - listed:
        rows = (await session.execute(select(Asset).where(
            Asset.id.in_(missing), Asset.user_id == user.id, Asset.deleted_at.is_(None)))).scalars().all()
        made_by = {g.id: g for g in (await session.execute(select(Generation).where(
            Generation.id.in_([a.generation_id for a in rows if a.generation_id])))).scalars().all()}
        for a in rows:
            g = made_by.get(a.generation_id) if a.generation_id else None
            if g is not None and g.project_id == p.id and g.shot_key:
                out.append(_generation_take(g, a))
            elif g is None and (a.metadata_ or {}).get("project_id") == str(p.id) and (a.metadata_ or {}).get("shot_key"):
                out.append(_derived_take(a))
    return sorted(out, key=lambda t: t.created_at, reverse=True)
