"use client";

import { ArrowLeft, ArrowRight, Check, Film, ImageIcon, Link2, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { type Asset, isActive, mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { allShots, type CinemaDoc, type CinemaSettings, newId, newShot, type Scene, type Shot, type Take } from "@/lib/cinema";
import { useModel, useRegistry } from "@/lib/queries";
import { type Direction, DirectorPanel } from "@/components/create/DirectorPanel";
import { AspectPicker, Button, Field, Segmented, Select, Slider } from "@/components/ui/controls";
import type { Launch } from "./Editor";

type Update = (fn: (d: CinemaDoc) => CinemaDoc) => void;

function clean(d: Direction | undefined): Record<string, unknown> {
  return Object.fromEntries(Object.entries(d ?? {}).filter(([, v]) => v !== null && v !== undefined && v !== ""));
}

/** The project's look with the shot's own choices on top (mirrors cinema/document.py:merged_direction). */
export function mergedDirection(settings: CinemaSettings, shot: Shot): Record<string, unknown> {
  const own = clean(shot.direction);
  if (!(own.camera_moves as unknown[] | undefined)?.length) delete own.camera_moves;
  return { ...clean(settings.direction), ...own };
}

type TakeShot = {
  key: string; prompt: string; direction: Record<string, unknown>; duration: number | null;
  start_asset_id?: string | null; continue_from?: string; continue_from_asset_id?: string;
};

/** The takes-run shots for these shot ids: hero frames, or chaining from the shot before. */
export function takeShots(doc: CinemaDoc, ids: string[]): TakeShot[] {
  const order = allShots(doc).map((x) => x.shot);
  const batch = new Set(ids);
  return order.flatMap((shot, i): TakeShot[] => {
    if (!batch.has(shot.id) || !shot.action.trim()) return [];
    const prev = order[i - 1];
    const base = { key: shot.id, prompt: shot.action.trim(), direction: mergedDirection(doc.settings, shot), duration: shot.duration };
    if (shot.continuity === "continue" && prev) {
      if (batch.has(prev.id) && prev.action.trim()) return [{ ...base, continue_from: prev.id }];
      if (prev.take_asset_id) return [{ ...base, continue_from_asset_id: prev.take_asset_id }];
      return [];
    }
    return [{ ...base, start_asset_id: shot.hero_asset_id }];
  });
}

export function Storyboard({ projectId, doc, update, takes, launch, busy }: {
  projectId: string; doc: CinemaDoc; update: Update; takes: Take[]; launch: Launch; busy: boolean;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const shots = allShots(doc);
  const current = shots.find((x) => x.shot.id === selected);
  const assets = new Map<string, Asset>(takes.flatMap((t) => (t.asset ? [[t.asset.id, t.asset as Asset]] : [])));
  const byShot = (key: string) => takes.filter((t) => t.shot_key === key);

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
  const framesFor = (ids: string[]) => launch({ recipe: "cinema_frames", input: {
    project_id: projectId, image_model_id: doc.settings.image_model_id, aspect_ratio: doc.settings.aspect_ratio,
    count: doc.settings.hero_frames,
    shots: shots.filter((x) => ids.includes(x.shot.id)).map((x) => ({
      key: x.shot.id, prompt: x.shot.action.trim(), direction: mergedDirection(doc.settings, x.shot) })) } }, "Hero frames");
  const filmFor = (ids: string[]) => launch({ recipe: "cinema_takes", input: {
    project_id: projectId, model_id: doc.settings.model_id, takes: doc.settings.takes_per_shot,
    params: { aspect_ratio: doc.settings.aspect_ratio, resolution: doc.settings.resolution },
    shots: takeShots(doc, ids) } }, "Filming");

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
              </div>
              <ol className="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-3">
                {scene.shots.map((shot) => {
                  const n = shots.find((x) => x.shot.id === shot.id)?.index ?? 0;
                  const pic = assets.get(shot.take_asset_id ?? "") ?? assets.get(shot.hero_asset_id ?? "");
                  const pending = byShot(shot.id).some((t) => isActive(t.status));
                  return (
                    <li key={shot.id}>
                      <button type="button" onClick={() => setSelected(shot.id)} aria-pressed={selected === shot.id}
                              className={cn("flex w-full flex-col overflow-hidden rounded-[var(--radius-panel)] border bg-panel text-left",
                                            selected === shot.id ? "border-tungsten" : "border-transparent hover:border-line")}>
                        <span className="relative aspect-video w-full bg-bay">
                          {pic && (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img src={mediaUrl(pic.thumb_url ?? pic.poster_url ?? pic.url)} alt="" className="size-full object-cover" />
                          )}
                          <span className="tabular absolute left-2 top-1.5 text-xs text-chalk drop-shadow">{String(n).padStart(2, "0")}</span>
                          <span className="absolute right-2 top-1.5 flex gap-1 text-chalk">
                            {shot.continuity === "continue" && <Link2 className="size-3.5 drop-shadow" aria-label="Continues the previous shot" />}
                            {shot.take_asset_id && <Check className="size-3.5 text-done drop-shadow" aria-label="Filmed" />}
                            {pending && <LoaderCircle className="size-3.5 animate-spin text-tungsten" aria-label="Rendering" />}
                          </span>
                        </span>
                        <span className="line-clamp-2 min-h-[2.5rem] px-2.5 py-2 text-xs leading-snug text-muted">
                          {shot.action || "Describe this shot"}
                        </span>
                      </button>
                    </li>
                  );
                })}
                <li>
                  <button type="button" onClick={() => addShot(scene.id)}
                          className="flex aspect-video w-full flex-col items-center justify-center gap-1 rounded-[var(--radius-panel)] border border-dashed border-line-soft text-xs text-faint hover:border-line hover:text-chalk">
                    <Plus className="size-4" aria-hidden />
                    Add a shot
                  </button>
                </li>
              </ol>
            </section>
          ))}
        </div>
        <Button size="sm" tone="ghost" className="mt-6" onClick={addScene}>
          <Plus className="size-4" aria-hidden />
          Add a scene
        </Button>
      </div>
      <aside className="min-h-0 overflow-y-auto border-t border-line-soft p-5 scrollbar-quiet lg:border-t-0 lg:border-l">
        {current ? (
          <ShotInspector key={current.shot.id} doc={doc} number={current.index} scene={current.scene} shot={current.shot}
                         takes={byShot(current.shot.id)} patch={(p) => patchShot(current.shot.id, p)} update={update}
                         onFrames={() => void framesFor([current.shot.id])} onFilm={() => void filmFor([current.shot.id])}
                         onRemoved={() => setSelected(null)} />
        ) : (
          <FilmSettings doc={doc} update={update} />
        )}
      </aside>
    </div>
  );
}

function ShotInspector({ doc, number, scene, shot, takes, patch, update, onFrames, onFilm, onRemoved }: {
  doc: CinemaDoc; number: number; scene: Scene; shot: Shot; takes: Take[]; patch: (p: Partial<Shot>) => void;
  update: Update; onFrames: () => void; onFilm: () => void; onRemoved: () => void;
}) {
  const { data: model } = useModel(doc.settings.model_id);
  const order = allShots(doc).map((x) => x.shot);
  const prev = order[order.findIndex((s) => s.id === shot.id) - 1];
  const index = scene.shots.findIndex((s) => s.id === shot.id);
  const frames = takes.filter((t) => t.output_type === "image");
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
        <Button size="sm" tone="ghost" aria-label="Delete shot" onClick={remove}><Trash2 className="size-4" aria-hidden /></Button>
      </div>
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
      <DirectorPanel direction={shot.direction} onChange={(d) => patch({ direction: d })} stills={false} />

      {shot.continuity === "cut" && (
        <section className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-xs text-muted">Opening frame</h3>
            <Button size="sm" onClick={onFrames} disabled={!shot.action.trim()}>
              <ImageIcon className="size-4" aria-hidden />
              Make {doc.settings.hero_frames}
            </Button>
          </div>
          <TakeGrid takes={frames} picked={shot.hero_asset_id} onPick={(id) => patch({ hero_asset_id: id === shot.hero_asset_id ? null : id })}
                    empty="Optional. A picked frame is where the shot starts." />
        </section>
      )}
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
        </p>
        <TakeGrid takes={videos} picked={shot.take_asset_id} onPick={(id) => patch({ take_asset_id: id === shot.take_asset_id ? null : id })}
                  empty="No takes yet." video />
      </section>
    </div>
  );
}

function TakeGrid({ takes, picked, onPick, empty, video }: {
  takes: Take[]; picked: string | null; onPick: (assetId: string) => void; empty: string; video?: boolean;
}) {
  if (!takes.length) return <p className="text-2xs text-faint">{empty}</p>;
  return (
    <ul className="grid grid-cols-2 gap-2">
      {takes.map((t) => (
        <li key={`${t.generation_id}-${t.asset?.id ?? "pending"}`} className="flex flex-col gap-1">
          {t.asset ? (
            video ? (
              <video src={mediaUrl(t.asset.proxy_url ?? t.asset.url)} poster={mediaUrl(t.asset.poster_url)} controls muted playsInline
                     preload="metadata" className={cn("aspect-video w-full rounded-[var(--radius-frame)] bg-black object-cover",
                                                       picked === t.asset.id && "ring-2 ring-tungsten")} />
            ) : (
              <button type="button" onClick={() => onPick(t.asset!.id)} aria-pressed={picked === t.asset.id}
                      className={cn("overflow-hidden rounded-[var(--radius-frame)]", picked === t.asset.id && "ring-2 ring-tungsten")}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={mediaUrl(t.asset.thumb_url ?? t.asset.url)} alt="Opening frame option" className="aspect-video w-full object-cover" />
              </button>
            )
          ) : (
            <span className="grid aspect-video w-full place-items-center rounded-[var(--radius-frame)] bg-bay text-2xs text-faint">
              {isActive(t.status) ? <LoaderCircle className="size-4 animate-spin text-tungsten" aria-label="Rendering" /> : (t.error ?? t.status)}
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

function FilmSettings({ doc, update }: { doc: CinemaDoc; update: Update }) {
  const { data: registry } = useRegistry();
  const s = doc.settings;
  const set = (p: Partial<CinemaSettings>) => update((d) => ({ ...d, settings: { ...d.settings, ...p } }));
  const video = (registry?.models ?? []).filter((m) => m.output_type === "video" && m.modes.some((x) => x.ops.includes("i2v")));
  const image = (registry?.models ?? []).filter((m) => m.output_type === "image" && m.modes.some((x) => x.ops.includes("t2i")));
  return (
    <div className="flex flex-col gap-5">
      <h2 className="type-title text-base">Film settings</h2>
      <Field label="Filmed on" htmlFor="film-model">
        <Select id="film-model" value={s.model_id} onChange={(v) => set({ model_id: v })} options={video.map((m) => ({ value: m.id, label: m.name }))} />
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
      <Field label="Opening frames per shot">
        <Slider value={s.hero_frames} min={1} max={4} step={1} onChange={(v) => set({ hero_frames: v })} />
      </Field>
      <div className="flex flex-col gap-2">
        <h3 className="text-xs text-muted">The film&rsquo;s look</h3>
        <p className="text-2xs text-faint">Applies to every shot; a shot&rsquo;s own choices win.</p>
        <DirectorPanel direction={s.direction} onChange={(d) => set({ direction: d })} stills={false} />
      </div>
    </div>
  );
}
