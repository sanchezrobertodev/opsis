"use client";

import { useQueries } from "@tanstack/react-query";
import { LoaderCircle, PersonStanding } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { type Asset, isActive, mediaUrl } from "@/lib/api";
import { type Animation, patchObject, type SceneObject } from "@/lib/jutsu3d";
import { useAsset, useGeneration } from "@/lib/queries";
import { QuickGenerate } from "@/components/create/QuickGenerate";
import { Button, Field, Select, Slider, Switch } from "@/components/ui/controls";

const SOURCE_LABEL: Record<string, string> = { walking: "Walking", running: "Running", library: "Library clip" };
type GlbClip = { name: string; seconds: number };

/** The GLBs a model can borrow clips from: its own, and the others its rigging job made on the same skeleton. */
function useClipSources(model: Asset | undefined) {
  const { data: gen } = useGeneration(model?.generation_id ?? undefined);
  const siblings = (gen?.assets ?? []).filter((a) => a.kind === "model3d" && a.id !== model?.id);
  const sources = model ? [model, ...siblings] : [];
  const clips = useQueries({
    queries: sources.map((a) => ({
      queryKey: ["glb-clips", mediaUrl(a.url)],
      queryFn: async (): Promise<GlbClip[]> => {
        const gltf = await new GLTFLoader().loadAsync(mediaUrl(a.url) ?? a.url);
        return gltf.animations.map((c) => ({ name: c.name, seconds: Math.round(c.duration * 100) / 100 }));
      },
      staleTime: Infinity,
    })),
  });
  const loading = clips.some((q) => q.isLoading);
  const options = sources.flatMap((a, i) => (clips[i]?.data ?? []).map((c) => {
    const from = a.id === model?.id ? "" : `${SOURCE_LABEL[String(a.metadata?.clip_source ?? "")] ?? a.title ?? "Clip"}: `;
    return { value: JSON.stringify([a.id, c.name]), label: `${from}${clipLabel(c.name)} (${c.seconds} s)` };
  }));
  return { options, loading };
}

/** Blender exports name clips "Armature|walking_man|baselayer": the action's own name is the middle part. */
function clipLabel(name: string): string {
  const parts = name.split("|");
  return (parts.length === 3 ? parts[1] : name).replaceAll("_", " ") || "Clip";
}

/** A model's clip on the timeline, or a way to rig it when it has none. */
export function AnimationEditor({ o }: { o: SceneObject }) {
  const { data: model } = useAsset(o.asset_id ?? undefined);
  const { options, loading } = useClipSources(model);
  const a = o.animation ?? null;
  const set = (patch: Partial<Animation>) => a && patchObject(o.id, { animation: { ...a, ...patch } });
  const current = a ? JSON.stringify([a.source_asset_id ?? o.asset_id, a.clip]) : "";

  if (loading) return <p className="flex items-center gap-2 text-2xs text-faint"><LoaderCircle className="size-3 animate-spin" aria-hidden /> Reading its clips…</p>;
  if (!options.length) return model ? <RigIt o={o} model={model} /> : null;
  return (
    <div className="flex flex-col gap-3">
      <Field label="Animation" htmlFor="clip">
        <Select id="clip" value={current}
                options={[{ value: "", label: "None (stands still)" }, ...options]}
                onChange={(v) => {
                  if (!v) return patchObject(o.id, { animation: null });
                  const [source, clip] = JSON.parse(v) as [string, string];
                  patchObject(o.id, { animation: { clip, source_asset_id: source === o.asset_id ? null : source,
                                                   start: a?.start ?? 0, offset: a?.offset ?? 0, speed: a?.speed ?? 1,
                                                   loop: a?.loop ?? true } });
                }} />
      </Field>
      {a && (
        <>
          <Field label="Starts at" hint="Seconds into the timeline; it holds its first pose until then.">
            <Slider value={a.start} min={0} max={60} step={0.1} format={(v) => `${v.toFixed(1)} s`} onChange={(start) => set({ start })} />
          </Field>
          <Field label="Speed">
            <Slider value={a.speed} min={0.25} max={3} step={0.05} format={(v) => `${v.toFixed(2)}×`} onChange={(speed) => set({ speed })} />
          </Field>
          <Field label="From" hint="Seconds into the clip it begins at.">
            <Slider value={a.offset} min={0} max={10} step={0.1} format={(v) => `${v.toFixed(1)} s`} onChange={(offset) => set({ offset })} />
          </Field>
          <label className="flex items-center justify-between gap-4 text-sm">
            <span>
              Loop
              <span className="block text-2xs text-faint">Off: it holds its last pose once it ends.</span>
            </span>
            <Switch label="Loop" checked={a.loop} onChange={(loop) => set({ loop })} />
          </label>
        </>
      )}
    </div>
  );
}

/** Rig a model without clips (Meshy): when it's done, the rigged model takes its place, with its clips to pick. */
function RigIt({ o, model }: { o: SceneObject; model: Asset }) {
  const [open, setOpen] = useState(false);
  const [genId, setGenId] = useState<string>();
  const { data: gen } = useGeneration(genId);
  const rigged = gen?.assets?.find((x) => x.kind === "model3d" && !x.metadata?.clip_source);

  useEffect(() => {
    if (!rigged || o.asset_id === rigged.id) return;
    patchObject(o.id, { asset_id: rigged.id, animation: null });
    toast("Rigged. Pick a clip under Animation.");
  }, [rigged, o.id, o.asset_id]);

  if (genId) {
    const failed = gen && !isActive(gen.status) && gen.status !== "succeeded";
    return (
      <p className={failed ? "text-2xs text-fault" : "flex items-center gap-2 text-2xs text-muted"} aria-live="polite">
        {failed ? gen?.error_message ?? "It wasn't rigged." : <><LoaderCircle className="size-3 animate-spin text-tungsten" aria-hidden /> Rigging the character…</>}
      </p>
    );
  }
  if (!open) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-2xs text-faint">This model has no animation clips.</p>
        <Button size="sm" onClick={() => setOpen(true)}>
          <PersonStanding className="size-4" aria-hidden /> Rig it for animation
        </Button>
      </div>
    );
  }
  return (
    <div className="rounded-[var(--radius-panel)] bg-panel p-3">
      <QuickGenerate op="rig" submitLabel="Rig the character" onStarted={(g) => setGenId(g.id)} onCancel={() => setOpen(false)}
                     media={{ input_model: [{ id: model.id, kind: model.kind, url: model.url, thumb: model.thumb_url, title: model.title }] }} />
    </div>
  );
}
