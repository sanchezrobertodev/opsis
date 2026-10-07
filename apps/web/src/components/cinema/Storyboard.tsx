"use client";

import { ArrowLeft, ArrowRight, Check, Clapperboard, Film, ImageIcon, Link2, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { type Asset, isActive, mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { allShots, type CinemaDoc, type CinemaSettings, type Line, newId, newShot, type Scene, type Shot, type Take } from "@/lib/cinema";
import { useElements } from "@/lib/elements";
import { useModel, useRegistry } from "@/lib/queries";
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
  start_asset_id?: string | null; continue_from?: string; continue_from_asset_id?: string;
};

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

/** The takes-run shots for these shot ids: hero frames, or chaining from the shot before. */
export function takeShots(doc: CinemaDoc, ids: string[], voices: Record<string, string> = {}): TakeShot[] {
  const order = allShots(doc).map((x) => x.shot);
  const batch = new Set(ids);
  return order.flatMap((shot, i): TakeShot[] => {
    if (!batch.has(shot.id) || !shot.action.trim()) return [];
    const prev = order[i - 1];
    const base = { key: shot.id, prompt: shot.action.trim(), duration: shot.duration,
                   direction: withDialogue(mergedDirection(doc.settings, shot), shot, voices) };
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
  const { data: filmModel } = useModel(doc.settings.model_id);
  const voices = useCastVoices();
  const multiShot = Boolean(filmModel?.modes.some((m) => m.ops.includes("multi_shot")));
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
  // a whole scene in one multi-shot call; each shot then gets its piece as a take
  const sceneShots = (scene: Scene) => scene.shots.filter((s) => s.action.trim());
  const filmScene = (scene: Scene) => launch({ recipe: "cinema_scene", input: {
    project_id: projectId, model_id: doc.settings.model_id, scene_key: scene.id, takes: 1,
    params: { ...doc.settings.model_params, aspect_ratio: doc.settings.aspect_ratio, resolution: doc.settings.resolution },
    start_asset_id: scene.shots[0]?.hero_asset_id ?? null,
    shots: sceneShots(scene).map((s) => ({ key: s.id, prompt: s.action.trim(), duration: s.duration ?? 5,
                                          direction: withDialogue(mergedDirection(doc.settings, s), s, voices) })) } },
    "Filming the scene");
  const filmFor = (ids: string[]) => launch({ recipe: "cinema_takes", input: {
    project_id: projectId, model_id: doc.settings.model_id, takes: doc.settings.takes_per_shot,
    lipsync_model_id: doc.settings.lipsync_model_id ?? "sync-lipsync-3",
    params: { ...doc.settings.model_params, aspect_ratio: doc.settings.aspect_ratio, resolution: doc.settings.resolution },
    shots: takeShots(doc, ids, voices) } }, "Filming");

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
                {multiShot && sceneShots(scene).length >= 2 && (
                  <Button size="sm" tone="ghost" onClick={() => void filmScene(scene)}
                          title="Renders the scene's shots in one call, then gives each shot its piece as a take">
                    <Clapperboard className="size-4" aria-hidden />
                    Film as one take, {sceneShots(scene).reduce((t, s) => t + (s.duration ?? 5), 0)} s
                  </Button>
                )}
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
          <ShotInspector key={current.shot.id} projectId={projectId} launch={launch} doc={doc} number={current.index}
                         scene={current.scene} shot={current.shot}
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

function ShotInspector({ projectId, launch, doc, number, scene, shot, takes, patch, update, onFrames, onFilm, onRemoved }: {
  projectId: string; launch: Launch; doc: CinemaDoc; number: number; scene: Scene; shot: Shot; takes: Take[];
  patch: (p: Partial<Shot>) => void; update: Update; onFrames: () => void; onFilm: () => void; onRemoved: () => void;
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
      <DialogueEditor shot={shot} cast={[...new Set([...mentionSlugs(shot.action), ...doc.cast])]}
                      onChange={(lines) => patch({ lines })} />
      <DirectorPanel direction={shot.direction} onChange={(d) => patch({ direction: d })} stills={false}
                     cast={mentionSlugs(shot.action)} />

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

function TakeGrid({ takes, picked, onPick, empty, video }: {
  takes: Take[]; picked: string | null; onPick: (assetId: string) => void; empty: string; video?: boolean;
}) {
  if (!takes.length) return <p className="text-2xs text-faint">{empty}</p>;
  const number = new Map(takes.flatMap((t, i) => (t.asset ? [[t.asset.id, takes.length - i]] : [])));
  return (
    <ul className="grid grid-cols-2 gap-2">
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
                <img src={mediaUrl(t.asset.thumb_url ?? t.asset.url)} alt="Opening frame option" className="aspect-video w-full object-cover" />
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
      <Field label="Opening frames per shot">
        <Slider value={s.hero_frames} min={1} max={4} step={1} onChange={(v) => set({ hero_frames: v })} />
      </Field>
      <div className="flex flex-col gap-2">
        <h3 className="text-xs text-muted">The film&rsquo;s look</h3>
        <p className="text-2xs text-faint">Applies to every shot; a shot&rsquo;s own choices win.</p>
        <DirectorPanel direction={s.direction} onChange={(d) => set({ direction: d })} stills={false} cast={doc.cast} />
      </div>
    </div>
  );
}
