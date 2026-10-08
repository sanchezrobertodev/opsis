"use client";

import {
  closestCenter, DndContext, type DragEndEvent, type DragOverEvent, DragOverlay, type DragStartEvent, KeyboardSensor,
  PointerSensor, useDroppable, useSensor, useSensors,
} from "@dnd-kit/core";
import { rectSortingStrategy, SortableContext, sortableKeyboardCoordinates, useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  AlertTriangle, ArrowLeft, ArrowRight, Check, Clapperboard, Copy, Film, GripVertical, ImageIcon, Link2, LoaderCircle,
  Plus, Scissors, Trash2,
} from "lucide-react";
import { type PointerEvent as ReactPointerEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { type Asset, isActive, mediaUrl } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import {
  allShots, type CinemaDoc, type CinemaSettings, duplicateShot, type Line, moveShot, newId, newShot, type Scene,
  type Shot, splitShot, type Take, useProjectSpend,
} from "@/lib/cinema";
import { clipSeconds } from "@/lib/cut";
import { useElements } from "@/lib/elements";
import { useDebounced } from "@/lib/hooks";
import { useModel, useRegistry } from "@/lib/queries";
import { useEstimateRun } from "@/lib/runs";
import type { RunCreate } from "@/lib/runs";
import { type Direction, DirectorPanel, mentionSlugs } from "@/components/create/DirectorPanel";
import { AspectPicker, Button, Field, Segmented, Select, Slider } from "@/components/ui/controls";
import type { Launch } from "./Editor";
import { TakeActions } from "./TakeActions";

type Update = (fn: (d: CinemaDoc) => CinemaDoc) => void;

/** The choices a direction makes: unset fields and empty lists (moves, lights, feelings) choose nothing. */
function clean(d: Direction | undefined): Record<string, unknown> {
  return Object.fromEntries(Object.entries(d ?? {}).filter(([, v]) =>
    v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && v.length === 0)));
}

/** The project's look with the shot's own choices on top (mirrors cinema/document.py:merged_direction). */
export function mergedDirection(settings: CinemaSettings, shot: Shot): Record<string, unknown> {
  return { camera_moves: [], ...clean(settings.direction), ...clean(shot.direction) };
}

type TakeShot = {
  key: string; prompt: string; direction: Record<string, unknown>; duration: number | null;
  start_asset_id?: string | null; continue_from?: string; continue_from_asset_id?: string; end_asset_id?: string;
};

/** Whether a model can animate from one picked frame to another (a mode taking a start and an end frame). */
export function endsOnAFrame(model: { modes: { medias: { role: string }[] }[] } | undefined): boolean {
  return Boolean(model?.modes.some((m) => m.medias.some((s) => s.role === "start_image")
                                         && m.medias.some((s) => s.role === "end_image")));
}

/** A shot's lines as the takes run gets them: in its direction, each with its speaker's voice. */
function withDialogue(direction: Record<string, unknown>, shot: Shot, voices: Record<string, string>) {
  const lines = (shot.lines ?? []).filter((l) => l.text.trim() && l.who.trim());
  if (!lines.length) return direction;
  return { ...direction, dialogue: lines.map((l) => ({
    who: l.who, text: l.text, voice_id: l.voice_id || voices[l.who.replace(/^@/, "").toLowerCase()] || null })) };
}

/** Voice ids of saved characters, by slug. */
export function useCastVoices(): Record<string, string> {
  const { data: elements } = useElements();
  return Object.fromEntries((elements ?? []).flatMap((e) => {
    const id = (e.voice as { params?: { voice_id?: string } } | null)?.params?.voice_id;
    return id ? [[e.slug, id]] : [];
  }));
}

/**
 * The takes-run shots for these shot ids: hero frames, or chaining from the shot before; a picked closing frame
 * goes along when the shot starts on a frame and the model can end on one (`endFrames`).
 */
export function takeShots(doc: CinemaDoc, ids: string[], voices: Record<string, string> = {},
                          endFrames = false): TakeShot[] {
  const order = allShots(doc).map((x) => x.shot);
  const batch = new Set(ids);
  return order.flatMap((shot, i): TakeShot[] => {
    if (!batch.has(shot.id) || !shot.action.trim()) return [];
    const prev = order[i - 1];
    const base = { key: shot.id, prompt: shot.action.trim(), duration: shot.duration,
                   direction: withDialogue(mergedDirection(doc.settings, shot), shot, voices) };
    const end = endFrames && shot.end_asset_id ? { end_asset_id: shot.end_asset_id } : {};
    if (shot.continuity === "continue" && prev) {
      if (batch.has(prev.id) && prev.action.trim()) return [{ ...base, ...end, continue_from: prev.id }];
      if (prev.take_asset_id) return [{ ...base, ...end, continue_from_asset_id: prev.take_asset_id }];
      return [];
    }
    return [{ ...base, start_asset_id: shot.hero_asset_id, ...(shot.hero_asset_id ? end : {}) }];
  });
}

/** A takes run for these shots, on the film's settings. */
function takesRun(projectId: string, doc: CinemaDoc, shots: TakeShot[]): RunCreate {
  return { recipe: "cinema_takes", input: {
    project_id: projectId, model_id: doc.settings.model_id, takes: doc.settings.takes_per_shot,
    lipsync_model_id: doc.settings.lipsync_model_id ?? "sync-lipsync-3",
    params: { ...doc.settings.model_params, aspect_ratio: doc.settings.aspect_ratio, resolution: doc.settings.resolution },
    shots } };
}

type ShotEstimate = { usd: number; live: boolean };

/**
 * What filming each shot would cost: one estimate of filming every described shot, split by shot. With mock
 * providers on, that's the price on the paid routes.
 */
function useShotEstimates(projectId: string, doc: CinemaDoc, voices: Record<string, string>, endFrames: boolean) {
  const ids = allShots(doc).filter((x) => x.shot.action.trim()).map((x) => x.shot.id);
  // the wording doesn't change the price, so typing doesn't ask again
  const shots = takeShots(doc, ids, voices, endFrames).map((s) => ({ ...s, prompt: "x" }));
  const { data } = useEstimateRun(useDebounced(shots.length ? takesRun(projectId, doc, shots) : null, 600));
  return (id: string): ShotEstimate | undefined => {
    const now = data?.by_key[id];
    if (now === undefined) return undefined;
    const live = data?.live_by_key?.[id];
    return now === 0 && live != null ? { usd: live, live: true } : { usd: now, live: false };
  };
}

export function Storyboard({ projectId, doc, update, takes, launch, busy }: {
  projectId: string; doc: CinemaDoc; update: Update; takes: Take[]; launch: Launch; busy: boolean;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const shots = allShots(doc);
  const current = shots.find((x) => x.shot.id === selected);
  const { data: filmModel } = useModel(doc.settings.model_id);
  const voices = useCastVoices();
  const multiShot = Boolean(filmModel?.modes.some((m) => m.ops.includes("multi_shot")));
  const assets = new Map<string, Asset>(takes.flatMap((t) => (t.asset ? [[t.asset.id, t.asset as Asset]] : [])));
  const byShot = (key: string) => takes.filter((t) => t.shot_key === key);

  const spend = useProjectSpend(projectId, busy).data;
  const endFrames = endsOnAFrame(filmModel);
  const estimate = useShotEstimates(projectId, doc, voices, endFrames);
  const [dragging, setDragging] = useState<string | null>(null);
  const [overScene, setOverScene] = useState<string | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
                             useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));
  const sceneOf = (id: string) => (id.startsWith("scene:") ? id.slice(6)
    : doc.scenes.find((sc) => sc.shots.some((s) => s.id === id))?.id ?? null);
  const endDrag = () => { setDragging(null); setOverScene(null); };
  const onDragStart = (e: DragStartEvent) => setDragging(String(e.active.id));
  const onDragOver = (e: DragOverEvent) => setOverScene(e.over ? sceneOf(String(e.over.id)) : null);
  const onDragEnd = (e: DragEndEvent) => {
    endDrag();
    const id = String(e.active.id);
    const over = e.over ? String(e.over.id) : null;
    const target = over ? sceneOf(over) : null;
    if (!over || !target || over === id) return;
    const there = doc.scenes.find((sc) => sc.id === target)!.shots;
    // onto a shot: take its place; onto the scene's free space: go last
    const index = over.startsWith("scene:") ? there.filter((s) => s.id !== id).length : there.findIndex((s) => s.id === over);
    update((d) => moveShot(d, id, target, index));
  };
  const dragged = dragging ? shots.find((x) => x.shot.id === dragging) : undefined;
  const face = (shot: Shot, n: number): FaceProps => ({
    shot, n, pic: assets.get(shot.take_asset_id ?? "") ?? assets.get(shot.hero_asset_id ?? ""),
    takes: byShot(shot.id), spent: spend?.shots[shot.id], estimate: estimate(shot.id),
  });

  const patchShot = (id: string, p: Partial<Shot>) => update((d) => ({
    ...d, scenes: d.scenes.map((sc) => ({ ...sc, shots: sc.shots.map((s) => (s.id === id ? { ...s, ...p } : s)) })) }));
  const patchScene = (id: string, p: Partial<Scene>) => update((d) => ({
    ...d, scenes: d.scenes.map((sc) => (sc.id === id ? { ...sc, ...p } : sc)) }));
  const addShot = (sceneId: string) => {
    const shot = newShot();
    update((d) => ({ ...d, scenes: d.scenes.map((sc) => (sc.id === sceneId ? { ...sc, shots: [...sc.shots, shot] } : sc)) }));
    setSelected(shot.id);
  };
  const addScene = () => {
    const shot = newShot();
    update((d) => ({ ...d, scenes: [...d.scenes, { id: newId("c"), heading: "", summary: "", shots: [shot] }] }));
    setSelected(shot.id);
  };

  const noFrames = shots.filter((x) => x.shot.action.trim() && !x.shot.hero_asset_id && x.shot.continuity === "cut");
  const unfilmed = shots.filter((x) => x.shot.action.trim() && !x.shot.take_asset_id);
  const framesFor = (ids: string[], which: "start" | "end" = "start") => launch({ recipe: "cinema_frames", input: {
    project_id: projectId, image_model_id: doc.settings.image_model_id, aspect_ratio: doc.settings.aspect_ratio,
    count: doc.settings.hero_frames, which,
    shots: shots.filter((x) => ids.includes(x.shot.id)).map((x) => ({
      key: x.shot.id, prompt: x.shot.action.trim(), direction: mergedDirection(doc.settings, x.shot) })) } },
    which === "end" ? "Closing frames" : "Hero frames");
  // a whole scene in one multi-shot call; each shot then gets its piece as a take
  const sceneShots = (scene: Scene) => scene.shots.filter((s) => s.action.trim());
  const filmScene = (scene: Scene) => launch({ recipe: "cinema_scene", input: {
    project_id: projectId, model_id: doc.settings.model_id, scene_key: scene.id, takes: 1,
    params: { ...doc.settings.model_params, aspect_ratio: doc.settings.aspect_ratio, resolution: doc.settings.resolution },
    start_asset_id: scene.shots[0]?.hero_asset_id ?? null,
    shots: sceneShots(scene).map((s) => ({ key: s.id, prompt: s.action.trim(), duration: s.duration ?? 5,
                                          direction: withDialogue(mergedDirection(doc.settings, s), s, voices) })) } },
    "Filming the scene");
  const filmFor = (ids: string[]) => launch(takesRun(projectId, doc, takeShots(doc, ids, voices, endFrames)), "Filming");

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_380px]">
      <div className="min-h-0 overflow-y-auto p-6 scrollbar-quiet">
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => void framesFor(noFrames.map((x) => x.shot.id))} disabled={!noFrames.length}>
            <ImageIcon className="size-4" aria-hidden />
            Hero frames for {noFrames.length} shot{noFrames.length === 1 ? "" : "s"}
          </Button>
          <Button size="sm" tone="primary" onClick={() => void filmFor(unfilmed.map((x) => x.shot.id))} disabled={!unfilmed.length}>
            <Film className="size-4" aria-hidden />
            Film {unfilmed.length} unfilmed shot{unfilmed.length === 1 ? "" : "s"}
          </Button>
          {busy && <span className="flex items-center gap-1.5 text-2xs text-tungsten"><LoaderCircle className="size-3 animate-spin" aria-hidden />Rendering</span>}
          <Button size="sm" tone="ghost" className="ml-auto" onClick={() => setSelected(null)} aria-pressed={selected === null}>
            Film settings
          </Button>
        </div>
        {doc.scenes.length === 0 && (
          <p className="mb-4 max-w-xl text-sm text-muted">
            No shots yet. Write a script and plan it with Claude, or add a scene and describe its shots yourself.
          </p>
        )}
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragStart={onDragStart} onDragOver={onDragOver}
                    onDragEnd={onDragEnd} onDragCancel={endDrag} accessibility={{ announcements: announcements(doc) }}>
        <div className="flex flex-col gap-8">
          {doc.scenes.map((scene) => (
            <section key={scene.id} aria-label={scene.heading || "Untitled scene"} className="flex flex-col gap-3">
              <div className="flex flex-wrap items-baseline gap-3">
                <input value={scene.heading} onChange={(e) => patchScene(scene.id, { heading: e.target.value })}
                       placeholder="INT. / EXT. PLACE - TIME" aria-label="Scene heading"
                       className="type-title min-w-56 bg-transparent text-base outline-none placeholder:text-faint" />
                <input value={scene.summary} onChange={(e) => patchScene(scene.id, { summary: e.target.value })}
                       placeholder="What this scene is for" aria-label="Scene summary"
                       className="min-w-0 flex-1 bg-transparent text-xs text-muted outline-none placeholder:text-faint" />
                {multiShot && sceneShots(scene).length >= 2 && (
                  <Button size="sm" tone="ghost" onClick={() => void filmScene(scene)}
                          title="Renders the scene's shots in one call, then gives each shot its piece as a take">
                    <Clapperboard className="size-4" aria-hidden />
                    Film as one take, {sceneShots(scene).reduce((t, s) => t + (s.duration ?? 5), 0)} s
                  </Button>
                )}
              </div>
              <SceneShots sceneId={scene.id} ids={scene.shots.map((s) => s.id)} highlight={Boolean(dragging) && overScene === scene.id}>
                {scene.shots.map((shot) => (
                  <SortableShot key={shot.id} {...face(shot, shots.find((x) => x.shot.id === shot.id)?.index ?? 0)}
                                selected={selected === shot.id} onSelect={() => setSelected(shot.id)} />
                ))}
                <li>
                  <button type="button" onClick={() => addShot(scene.id)}
                          className="flex aspect-video w-full flex-col items-center justify-center gap-1 rounded-[var(--radius-panel)] border border-dashed border-line-soft text-xs text-faint hover:border-line hover:text-chalk">
                    <Plus className="size-4" aria-hidden />
                    Add a shot
                  </button>
                </li>
              </SceneShots>
            </section>
          ))}
        </div>
        <DragOverlay>
          {dragged && <div className="rotate-1 shadow-2xl"><ShotFace {...face(dragged.shot, dragged.index)} selected /></div>}
        </DragOverlay>
        </DndContext>
        <Button size="sm" tone="ghost" className="mt-6" onClick={addScene}>
          <Plus className="size-4" aria-hidden />
          Add a scene
        </Button>
      </div>
      <aside className="min-h-0 overflow-y-auto border-t border-line-soft p-5 scrollbar-quiet lg:border-t-0 lg:border-l">
        {current ? (
          <ShotInspector key={current.shot.id} projectId={projectId} launch={launch} doc={doc} number={current.index}
                         scene={current.scene} shot={current.shot}
                         takes={byShot(current.shot.id)} patch={(p) => patchShot(current.shot.id, p)} update={update}
                         onFrames={(which) => void framesFor([current.shot.id], which)}
                         onFilm={() => void filmFor([current.shot.id])} endFrames={endFrames}
                         onRemoved={() => setSelected(null)} onAdded={setSelected}
                         take={current.shot.take_asset_id ? assets.get(current.shot.take_asset_id) : undefined} />
        ) : (
          <FilmSettings doc={doc} update={update} />
        )}
      </aside>
    </div>
  );
}

type FaceProps = { shot: Shot; n: number; pic?: Asset; takes: Take[]; spent?: number; estimate?: ShotEstimate };

/** Where the shot stands, newest news first. */
function shotStatus(shot: Shot, takes: Take[]): { label: string; tone: string; icon?: ReactNode } {
  if (takes.some((t) => isActive(t.status))) {
    return { label: "Rendering", tone: "text-tungsten", icon: <LoaderCircle className="size-3 animate-spin" aria-hidden /> };
  }
  if (shot.take_asset_id) return { label: "Filmed", tone: "text-done", icon: <Check className="size-3" aria-hidden /> };
  if (!shot.action.trim()) return { label: "No description", tone: "text-faint" };
  const newest = takes.find((t) => t.output_type === "video");
  if (newest && newest.status !== "succeeded" && !takes.some((t) => t.output_type === "video" && t.asset)) {
    return { label: "Take failed", tone: "text-fault", icon: <AlertTriangle className="size-3" aria-hidden /> };
  }
  if (shot.hero_asset_id) return { label: "Frame picked", tone: "text-muted" };
  return { label: "Not filmed", tone: "text-muted" };
}

/** How long the shot plays: the used part of its take, else the length it will be filmed at. */
function shotLength(shot: Shot, pic: Asset | undefined): string {
  if (shot.take_asset_id && pic?.id === shot.take_asset_id && pic.duration_ms) {
    return `${clipSeconds(pic.duration_ms / 1000, shot.trim_in, shot.trim_out)[1].toFixed(1)} s`;
  }
  return shot.duration ? `${shot.duration} s` : "Default length";
}

function ShotFace({ shot, n, pic, takes, spent, estimate, selected }: FaceProps & { selected?: boolean }) {
  const status = shotStatus(shot, takes);
  const filmed = Boolean(shot.take_asset_id);
  const cost = spent || filmed
    ? { text: `${usd(spent ?? 0)} spent`, title: "Spent on this shot's frames, takes and lines so far" }
    : estimate && shot.action.trim()
      ? { text: `about ${usd(estimate.usd)}`,
          title: estimate.live ? "To film it on paid routes (free while mock providers are on)" : "To film it" }
      : null;
  return (
    <span className={cn("flex w-full flex-col overflow-hidden rounded-[var(--radius-panel)] border bg-panel text-left",
                        selected ? "border-tungsten" : "border-transparent hover:border-line")}>
      <span className="relative aspect-video w-full bg-bay">
        {pic && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={mediaUrl(pic.thumb_url ?? pic.poster_url ?? pic.url)} alt="" className="size-full object-cover" />
        )}
        <span className="tabular absolute left-2 top-1.5 text-xs text-chalk drop-shadow">{String(n).padStart(2, "0")}</span>
        {shot.continuity === "continue" && (
          <Link2 className="absolute right-2 top-1.5 size-3.5 text-chalk drop-shadow" aria-label="Continues the previous shot" />
        )}
        <span className={cn("absolute bottom-1.5 left-2 flex items-center gap-1 rounded-full bg-black/60 px-1.5 py-0.5 text-2xs", status.tone)}>
          {status.icon}{status.label}
        </span>
      </span>
      <span className="line-clamp-2 min-h-[2.5rem] px-2.5 pt-2 text-xs leading-snug text-muted">
        {shot.action || "Describe this shot"}
      </span>
      <span className="tabular flex items-center justify-between gap-2 px-2.5 pb-2 pt-1 text-2xs text-faint">
        <span>{shotLength(shot, pic)}</span>
        {cost && <span title={cost.title}>{cost.text}</span>}
      </span>
    </span>
  );
}

/** A shot card that can be dragged: by the whole card with a pointer, or by its handle with the keyboard. */
function SortableShot({ selected, onSelect, ...face }: FaceProps & { selected: boolean; onSelect: () => void }) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id: face.shot.id });
  const onPointerDown = listeners?.onPointerDown as ((e: ReactPointerEvent) => void) | undefined;
  return (
    <li ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition }}
        className={cn("group relative", isDragging && "opacity-40")}>
      <button type="button" onClick={onSelect} onPointerDown={onPointerDown} aria-pressed={selected} className="block w-full">
        <ShotFace {...face} selected={selected} />
      </button>
      <button type="button" ref={setActivatorNodeRef} {...attributes} {...listeners}
              aria-label={`Move shot ${String(face.n).padStart(2, "0")}`}
              className="absolute bottom-[3.6rem] right-1.5 grid size-6 cursor-grab place-items-center rounded-full bg-black/60 text-chalk opacity-0 focus-visible:opacity-100 group-hover:opacity-100 active:cursor-grabbing">
        <GripVertical className="size-3.5" aria-hidden />
      </button>
    </li>
  );
}

/** A scene's row of shots: sortable, and a place to drop a shot from another scene. */
function SceneShots({ sceneId, ids, highlight, children }: { sceneId: string; ids: string[]; highlight: boolean; children: ReactNode }) {
  const { setNodeRef } = useDroppable({ id: `scene:${sceneId}` });
  return (
    <SortableContext id={sceneId} items={ids} strategy={rectSortingStrategy}>
      <ol ref={setNodeRef} className={cn("grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-3 rounded-[var(--radius-panel)] transition-colors",
                                         highlight && "bg-tungsten/5 outline outline-1 outline-tungsten/40 outline-offset-4")}>
        {children}
      </ol>
    </SortableContext>
  );
}

/** What a screen reader hears while a shot is dragged with the keyboard. */
function announcements(doc: CinemaDoc) {
  const shots = allShots(doc);
  const name = (id: string | number) => {
    const x = shots.find((s) => s.shot.id === String(id));
    return x ? `shot ${x.index}` : "the end of the scene";
  };
  return {
    onDragStart: ({ active }: { active: { id: string | number } }) => `Picked up ${name(active.id)}.`,
    onDragOver: ({ active, over }: { active: { id: string | number }; over: { id: string | number } | null }) =>
      over ? `${name(active.id)} is over ${name(over.id)}.` : `${name(active.id)} is no longer over a shot.`,
    onDragEnd: ({ active, over }: { active: { id: string | number }; over: { id: string | number } | null }) =>
      over ? `${name(active.id)} moved to ${name(over.id)}'s place.` : `${name(active.id)} was dropped where it was.`,
    onDragCancel: ({ active }: { active: { id: string | number } }) => `Moving ${name(active.id)} was canceled.`,
  };
}

/**
 * Where to split a shot. A filmed one splits its take at a moment (each part plays its stretch, the second
 * continuing the first); an unfilmed one shares out its length (in lengths the film's model offers) and lines.
 */
function SplitPanel({ shot, take, durations, onSplit, onCancel }: {
  shot: Shot; take?: Asset; durations: number[]; onSplit: (at: number, used?: [number, number]) => void; onCancel: () => void;
}) {
  const filmed = Boolean(shot.take_asset_id);
  const used = filmed && take?.duration_ms ? clipSeconds(take.duration_ms / 1000, shot.trim_in, shot.trim_out) : undefined;
  const length = shot.duration ?? 0;
  const offered = (d: number) => !durations.length || durations.includes(d);
  const choices = length ? Array.from({ length: length - 1 }, (_, i) => i + 1).filter((d) => offered(d) && offered(length - d)) : [];
  const middle = choices.reduce((best, d) => (Math.abs(d - length / 2) < Math.abs(best - length / 2) ? d : best), choices[0] ?? length);
  const [at, setAt] = useState(used ? Math.round(used[1] * 5) / 10 : middle);
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (video.current && used) video.current.currentTime = used[0] + at;
  }, [at, used]);
  const max = used ? Math.max(0.2, Math.floor((used[1] - 0.2) * 10) / 10) : length;
  const canSplit = filmed ? (used?.[1] ?? 0) >= 0.4 : true;
  let control: ReactNode;
  if (filmed && !used) {
    control = <p className="text-2xs text-faint">Loading the take&hellip;</p>;
  } else if (used) {
    control = (
      <Field label={`Split at ${at.toFixed(1)} s`} hint="Both parts play the same take: the first up to here, the second from here on.">
        <Slider value={Math.min(at, max)} min={0.2} max={max} step={0.1} onChange={setAt} format={(v) => `${v.toFixed(1)} s`} />
      </Field>
    );
  } else if (choices.length) {
    control = (
      <Field label="Lengths" htmlFor="split-lengths" hint="The second part continues from the first one's last frame; the lines are shared out between them.">
        <Select id="split-lengths" value={String(at)} onChange={(v) => setAt(Number(v))}
                options={choices.map((d) => ({ value: String(d), label: `${d} s, then ${length - d} s` }))} />
      </Field>
    );
  } else {
    control = (
      <p className="text-2xs text-faint">
        {length ? `The film's model can't make shorter parts, so both keep ${length} s;` : "Both parts keep the model's default length;"}
        {" "}the second continues from the first.
      </p>
    );
  }
  return (
    <section aria-label="Split the shot" className="flex flex-col gap-3 rounded-[var(--radius-panel)] border border-line-soft bg-bay p-3">
      {filmed && take && used && (
        <video ref={video} src={mediaUrl(take.proxy_url ?? take.url)} muted playsInline preload="auto"
               className="aspect-video w-full rounded-[var(--radius-frame)] bg-black object-contain" />
      )}
      {control}
      <div className="flex justify-end gap-2">
        <Button size="sm" tone="ghost" onClick={onCancel}>Cancel</Button>
        <Button size="sm" tone="primary" disabled={!canSplit} onClick={() => onSplit(Math.min(at, max), used)}>
          <Scissors className="size-4" aria-hidden />
          Split
        </Button>
      </div>
    </section>
  );
}

function ShotInspector({ projectId, launch, doc, number, scene, shot, takes, patch, update, onFrames, onFilm, onRemoved,
                        onAdded, take, endFrames }: {
  projectId: string; launch: Launch; doc: CinemaDoc; number: number; scene: Scene; shot: Shot; takes: Take[];
  patch: (p: Partial<Shot>) => void; update: Update; onFrames: (which: "start" | "end") => void; onFilm: () => void;
  onRemoved: () => void; onAdded: (id: string) => void; take?: Asset; endFrames: boolean;
}) {
  const [splitting, setSplitting] = useState(false);
  const { data: model } = useModel(doc.settings.model_id);
  const order = allShots(doc).map((x) => x.shot);
  const prev = order[order.findIndex((s) => s.id === shot.id) - 1];
  const index = scene.shots.findIndex((s) => s.id === shot.id);
  const frames = takes.filter((t) => t.output_type === "image" && t.frame !== "end");
  const closing = takes.filter((t) => t.output_type === "image" && t.frame === "end");
  const startsOnAFrame = shot.continuity === "continue" ? Boolean(prev) : Boolean(shot.hero_asset_id);
  const columns = doc.settings.hero_frames > 4 ? 3 : 2;  // a 3 x 3 grid or a 2 x 2 one
  const videos = takes.filter((t) => t.output_type === "video");
  const durationParam = model?.parameters.find((p) => p.name === "duration");
  const durations: number[] = durationParam?.type === "enum" ? durationParam.options.map(Number)
    : durationParam?.type === "int" ? Array.from({ length: durationParam.max - durationParam.min + 1 }, (_, i) => durationParam.min + i) : [];
  const canFilm = Boolean(shot.action.trim()) && (shot.continuity === "cut" || Boolean(prev?.take_asset_id));

  function move(delta: -1 | 1) {
    update((d) => ({ ...d, scenes: d.scenes.map((sc) => {
      if (sc.id !== scene.id) return sc;
      const shots = [...sc.shots];
      const [it] = shots.splice(index, 1);
      shots.splice(index + delta, 0, it);
      return { ...sc, shots };
    }) }));
  }
  function remove() {
    update((d) => ({ ...d, scenes: d.scenes.map((sc) => ({ ...sc, shots: sc.shots.filter((s) => s.id !== shot.id) })) }));
    onRemoved();
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-2">
        <h2 className="type-title text-base">Shot {String(number).padStart(2, "0")}</h2>
        <span className="min-w-0 flex-1 truncate text-2xs text-faint">{scene.heading}</span>
        <Button size="sm" tone="ghost" aria-label="Move earlier" disabled={index === 0} onClick={() => move(-1)}><ArrowLeft className="size-4" aria-hidden /></Button>
        <Button size="sm" tone="ghost" aria-label="Move later" disabled={index === scene.shots.length - 1} onClick={() => move(1)}><ArrowRight className="size-4" aria-hidden /></Button>
        <Button size="sm" tone="ghost" aria-label="Duplicate shot" title="A copy right after it, without its take"
                onClick={() => { const id = newId("s"); update((d) => duplicateShot(d, shot.id, id).doc); onAdded(id); }}>
          <Copy className="size-4" aria-hidden />
        </Button>
        <Button size="sm" tone="ghost" aria-label="Split shot" aria-pressed={splitting} onClick={() => setSplitting((v) => !v)}>
          <Scissors className="size-4" aria-hidden />
        </Button>
        <Button size="sm" tone="ghost" aria-label="Delete shot" onClick={remove}><Trash2 className="size-4" aria-hidden /></Button>
      </div>
      {splitting && (
        <SplitPanel shot={shot} take={take} durations={durations} onCancel={() => setSplitting(false)}
                    onSplit={(at, used) => {
                      const id = newId("s");
                      update((d) => splitShot(d, shot.id, at, used, id).doc);
                      setSplitting(false);
                      onAdded(id);
                    }} />
      )}
      <Field label="What happens" htmlFor="shot-action" hint="Present tense, what the camera sees. @mention saved characters.">
        <textarea id="shot-action" value={shot.action} onChange={(e) => patch({ action: e.target.value })} rows={4}
                  className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm leading-relaxed outline-none focus:border-tungsten/60" />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Length" htmlFor="shot-length">
          <Select id="shot-length" value={String(shot.duration ?? "")} onChange={(v) => patch({ duration: v ? Number(v) : null })}
                  options={[{ value: "", label: "Model default" }, ...durations.map((d) => ({ value: String(d), label: `${d} s` }))]} />
        </Field>
        <Field label="Starts">
          <Segmented label="Starts" value={shot.continuity} options={prev ? (["cut", "continue"] as const) : (["cut"] as const)}
                     render={(v) => (v === "cut" ? "On a cut" : "From last")} onChange={(v) => patch({ continuity: v })} />
        </Field>
      </div>
      <DialogueEditor shot={shot} cast={[...new Set([...mentionSlugs(shot.action), ...doc.cast])]}
                      onChange={(lines) => patch({ lines })} />
      <DirectorPanel direction={shot.direction} onChange={(d) => patch({ direction: d })} stills={false}
                     cast={mentionSlugs(shot.action)} />

      {shot.continuity === "cut" && (
        <section className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-xs text-muted">Opening frame</h3>
            <Button size="sm" onClick={() => onFrames("start")} disabled={!shot.action.trim()}>
              <ImageIcon className="size-4" aria-hidden />
              Make {doc.settings.hero_frames}
            </Button>
          </div>
          <TakeGrid takes={frames} picked={shot.hero_asset_id} columns={columns} alt="Opening frame option"
                    onPick={(id) => patch({ hero_asset_id: id === shot.hero_asset_id ? null : id })}
                    empty="Optional. A picked frame is where the shot starts." />
        </section>
      )}
      <section className="flex flex-col gap-2" aria-label="Closing frame">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-xs text-muted">Closing frame</h3>
          <Button size="sm" onClick={() => onFrames("end")} disabled={!shot.action.trim() || !endFrames}>
            <ImageIcon className="size-4" aria-hidden />
            Make {doc.settings.hero_frames}
          </Button>
        </div>
        {!endFrames ? (
          <p className="text-2xs text-faint">
            The film&rsquo;s model can&rsquo;t end on a picked frame{shot.end_asset_id ? ", so the picked one is left out" : ""}.
            Cinema Studio, Kling 3.0, Seedance 2.5 and Veo 3.1 can.
          </p>
        ) : (
          <TakeGrid takes={closing} picked={shot.end_asset_id ?? null} columns={columns} alt="Closing frame option"
                    onPick={(id) => patch({ end_asset_id: id === shot.end_asset_id ? null : id })}
                    empty={startsOnAFrame ? "Optional. A picked frame is where the take ends."
                      : "Optional. Pick an opening frame first: the take animates from it to this one."} />
        )}
      </section>
      <section className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-xs text-muted">Takes</h3>
          <Button size="sm" tone="primary" onClick={onFilm} disabled={!canFilm}>
            <Film className="size-4" aria-hidden />
            Film {doc.settings.takes_per_shot}
          </Button>
        </div>
        <p className="text-2xs text-faint">
          {shot.continuity === "continue"
            ? prev?.take_asset_id ? "Starts from the last frame of the previous shot's take."
              : "Pick a take for the previous shot first, or film them together from the storyboard."
            : shot.hero_asset_id ? "Starts from the picked opening frame." : "Starts from the description alone."}
          {endFrames && shot.end_asset_id && startsOnAFrame && " Ends on the picked closing frame."}
        </p>
        <TakeGrid takes={videos} picked={shot.take_asset_id} onPick={(id) => patch({ take_asset_id: id === shot.take_asset_id ? null : id })}
                  empty="No takes yet." video />
      </section>
      {shot.take_asset_id ? (
        <TakeActions projectId={projectId} doc={doc} shot={shot} takeId={shot.take_asset_id} launch={launch} />
      ) : videos.some((t) => t.asset) ? (
        <p className="text-2xs text-faint">Put a take in the cut to continue it, lead into it or edit it.</p>
      ) : null}
    </div>
  );
}

/** What's said in the shot: each line's speaker and words. */
function DialogueEditor({ shot, cast, onChange }: { shot: Shot; cast: string[]; onChange: (lines: Line[]) => void }) {
  const lines = shot.lines ?? [];
  const speakers = [...cast.map((c) => `@${c}`), "Narrator"];
  const set = (i: number, p: Partial<Line>) => onChange(lines.map((l, j) => (j === i ? { ...l, ...p } : l)));
  return (
    <Field label="Dialogue" hint="Veo, Seedance and Kling say the lines themselves; on other models each line is voiced in its speaker's voice and the take is lip-synced to it.">
      {lines.length > 0 && (
        <ol className="flex flex-col gap-2">
          {lines.map((l, i) => (
            <li key={i} className="flex flex-col gap-1.5 rounded-[var(--radius-control)] bg-bay p-2">
              <div className="flex items-center gap-2">
                <select value={l.who} aria-label={`Who says line ${i + 1}`} onChange={(e) => set(i, { who: e.target.value })}
                        className="h-8 min-w-0 flex-1 rounded-[var(--radius-control)] border border-line-soft bg-panel px-2 text-xs text-chalk outline-none focus:border-tungsten/60">
                  {[...new Set([...speakers, l.who])].map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
                <button type="button" aria-label={`Remove line ${i + 1}`} onClick={() => onChange(lines.filter((_, j) => j !== i))}
                        className="grid size-7 place-items-center rounded-full text-faint hover:bg-raised hover:text-chalk">
                  <Trash2 className="size-3.5" aria-hidden />
                </button>
              </div>
              <input value={l.text} onChange={(e) => set(i, { text: e.target.value })} maxLength={500}
                     aria-label={`Line ${i + 1}`} placeholder="What they say"
                     className="h-9 rounded-[var(--radius-control)] border border-line-soft bg-panel px-2 text-sm outline-none placeholder:text-faint focus:border-tungsten/60" />
            </li>
          ))}
        </ol>
      )}
      {lines.length < 12 && (
        <button type="button" onClick={() => onChange([...lines, { who: speakers[0], text: "" }])}
                className="mt-1 flex items-center gap-1 self-start rounded-full px-2 py-1 text-xs text-muted hover:text-chalk">
          <Plus className="size-3.5" aria-hidden /> Add a line
        </button>
      )}
    </Field>
  );
}

// how a take came to be: with its parent take in the list ("continued from take 2"), and without
const MADE_BY: Record<string, [string, string]> = {
  multi_shot: ["cut from a scene take", "cut from a scene take"], extend_after: ["continued from take", "continued"],
  extend_before: ["led into take", "with a lead-in"], edit: ["edited from take", "edited"],
  lipsync: ["lip-synced from take", "lip-synced"],
  region_edit: ["object changed in take", "object changed"],
};

function madeBy(t: Take, number: Map<string, number>): string {
  const phrase = MADE_BY[t.made_by];
  if (!phrase) return "";
  const parent = t.parent_asset_id ? number.get(t.parent_asset_id) : undefined;
  return parent && t.made_by !== "multi_shot" ? `, ${phrase[0]} ${parent}` : `, ${phrase[1]}`;
}

function TakeGrid({ takes, picked, onPick, empty, video, columns = 2, alt = "Opening frame option" }: {
  takes: Take[]; picked: string | null; onPick: (assetId: string) => void; empty: string; video?: boolean;
  columns?: 2 | 3; alt?: string;
}) {
  if (!takes.length) return <p className="text-2xs text-faint">{empty}</p>;
  const number = new Map(takes.flatMap((t, i) => (t.asset ? [[t.asset.id, takes.length - i]] : [])));
  return (
    <ul className={cn("grid gap-2", columns === 3 ? "grid-cols-3" : "grid-cols-2")}>
      {takes.map((t, i) => (
        <li key={`${t.generation_id ?? "derived"}-${t.asset?.id ?? i}`} className="flex flex-col gap-1">
          {t.asset ? (
            video ? (
              <video src={mediaUrl(t.asset.proxy_url ?? t.asset.url)} poster={mediaUrl(t.asset.poster_url)} controls muted playsInline
                     preload="metadata" className={cn("aspect-video w-full rounded-[var(--radius-frame)] bg-black object-cover",
                                                       picked === t.asset.id && "ring-2 ring-tungsten")} />
            ) : (
              <button type="button" onClick={() => onPick(t.asset!.id)} aria-pressed={picked === t.asset.id}
                      className={cn("overflow-hidden rounded-[var(--radius-frame)]", picked === t.asset.id && "ring-2 ring-tungsten")}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={mediaUrl(t.asset.thumb_url ?? t.asset.url)} alt={alt} className="aspect-video w-full object-cover" />
              </button>
            )
          ) : (
            <span className="grid aspect-video w-full place-items-center rounded-[var(--radius-frame)] bg-bay text-2xs text-faint">
              {isActive(t.status) ? <LoaderCircle className="size-4 animate-spin text-tungsten" aria-label="Rendering" /> : (t.error ?? t.status)}
            </span>
          )}
          {video && (
            <span className="truncate text-2xs text-faint" title={t.prompt}>
              Take {takes.length - i}{madeBy(t, number)}
            </span>
          )}
          {video && t.asset && (
            <Button size="sm" tone={picked === t.asset.id ? "primary" : "quiet"} onClick={() => onPick(t.asset!.id)}>
              {picked === t.asset.id ? "In the cut" : "Use this take"}
            </Button>
          )}
        </li>
      ))}
    </ul>
  );
}

function engineHint(engine: { modes: Record<string, string> } | undefined): string | undefined {
  if (!engine) return undefined;
  const models = [...new Set(Object.values(engine.modes).map((m) => m.split("/")[0]))];
  return `Runs on ${models.join(" and ")}; prices are that model's own.`;
}

function FilmSettings({ doc, update }: { doc: CinemaDoc; update: Update }) {
  const { data: registry } = useRegistry();
  const s = doc.settings;
  const set = (p: Partial<CinemaSettings>) => update((d) => ({ ...d, settings: { ...d.settings, ...p } }));
  const { data: model } = useModel(s.model_id);
  const video = (registry?.models ?? []).filter((m) => m.output_type === "video" && m.modes.some((x) => x.ops.includes("i2v")))
    .sort((a, b) => Number(b.kind === "virtual") - Number(a.kind === "virtual"));
  const image = (registry?.models ?? []).filter((m) => m.output_type === "image" && m.modes.some((x) => x.ops.includes("t2i")));
  const lipsyncers = (registry?.models ?? []).filter((m) => m.modes.some((x) => x.ops.includes("lipsync") && !x.ops.includes("avatar")));
  const virtual = model?.id === s.model_id ? model.virtual : null;
  const engineParam = virtual?.engine_param ?? "engine";
  const engine = String(s.model_params?.[engineParam] ?? virtual?.engines[0]?.id ?? "");
  return (
    <div className="flex flex-col gap-5">
      <h2 className="type-title text-base">Film settings</h2>
      <Field label="Filmed on" htmlFor="film-model">
        <Select id="film-model" value={s.model_id} onChange={(v) => set({ model_id: v, model_params: {} })}
                options={video.map((m) => ({ value: m.id, label: m.name }))} />
      </Field>
      {virtual && (
        <Field label="Engine" hint={engineHint(virtual.engines.find((e) => e.id === engine))}>
          <Segmented label="Engine" value={engine} options={virtual.engines.map((e) => e.id)}
                     render={(id) => virtual.engines.find((e) => e.id === id)?.label ?? id}
                     onChange={(id) => set({ model_params: { ...s.model_params, [engineParam]: id } })} />
        </Field>
      )}
      <Field label="Dialogue on other models" htmlFor="lipsync-model"
             hint="Models that don't speak get each line voiced and the take lip-synced with this.">
        <Select id="lipsync-model" value={s.lipsync_model_id ?? "sync-lipsync-3"} onChange={(v) => set({ lipsync_model_id: v })}
                options={lipsyncers.map((m) => ({ value: m.id, label: m.name }))} />
      </Field>
      <Field label="Opening frames on" htmlFor="frame-model">
        <Select id="frame-model" value={s.image_model_id} onChange={(v) => set({ image_model_id: v })}
                options={image.map((m) => ({ value: m.id, label: m.name }))} />
      </Field>
      <Field label="Aspect">
        <AspectPicker value={s.aspect_ratio} options={["16:9", "9:16", "1:1", "21:9"]} onChange={(v) => set({ aspect_ratio: v })} />
      </Field>
      <Field label="Resolution">
        <Segmented label="Resolution" value={s.resolution} options={["480p", "720p", "1080p"] as const} onChange={(v) => set({ resolution: v })} />
      </Field>
      <Field label="Takes per shot">
        <Slider value={s.takes_per_shot} min={1} max={4} step={1} onChange={(v) => set({ takes_per_shot: v })} />
      </Field>
      <Field label="Frames per grid" hint="4 makes a 2 x 2 grid, 9 a 3 x 3 one; opening and closing frames alike.">
        <Slider value={s.hero_frames} min={1} max={9} step={1} onChange={(v) => set({ hero_frames: v })} />
      </Field>
      <div className="flex flex-col gap-2">
        <h3 className="text-xs text-muted">The film&rsquo;s look</h3>
        <p className="text-2xs text-faint">Applies to every shot; a shot&rsquo;s own choices win.</p>
        <DirectorPanel direction={s.direction} onChange={(d) => set({ direction: d })} stills={false} cast={doc.cast} />
      </div>
    </div>
  );
}
