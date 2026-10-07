"use client";

import { Plus, X } from "lucide-react";
import { useState } from "react";
import type { Schemas } from "@studio/api-client";
import { cn } from "@/lib/cn";
import { Segmented } from "@/components/ui/controls";

type Library = Schemas["DirectorLibraryOut"];
export type Light = Schemas["LightChoice"];

const MAX_LIGHTS = 2;
const ROLE = ["Key light", "Second light"];
// Where each position sits on the stage, seen from above with the camera at the bottom (degrees, 0 = right)
const RING: Record<string, number> = {
  right: 0, front_right: 45, front: 90, front_left: 135, left: 180, back_left: 225, back: 270, back_right: 315,
};
const STAGE = 148;
const RADIUS = 54;

/** Up to two placed lights: drop each around the subject, then set its source, color, softness and strength. */
export function LightingConsole({ lib, lights, onChange }: {
  lib: Library; lights: Light[]; onChange: (lights: Light[]) => void;
}) {
  const [active, setActive] = useState(0);
  const current = lights[Math.min(active, lights.length - 1)];
  const index = Math.min(active, lights.length - 1);
  const colorOf = (l: Light) => lib.light_colors.find((c) => c.id === l.color)?.swatch?.[0] ?? "var(--color-chalk)";
  const positionLabel = (id: string) => lib.light_positions.find((p) => p.id === id)?.label ?? id;

  const set = (patch: Partial<Light>) => onChange(lights.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  const place = (position: string) => {
    if (!lights.length) {
      onChange([{ source: "softbox", position, quality: "soft", intensity: "normal" }]);
      setActive(0);
    } else {
      set({ position });
    }
  };
  const add = () => {
    const second: Light = { source: "practical", position: "back_right", quality: "soft", intensity: "normal" };
    onChange([...lights, second]
      .slice(0, MAX_LIGHTS));
    setActive(lights.length);
  };
  const remove = (i: number) => {
    onChange(lights.filter((_, j) => j !== i));
    setActive(0);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <div className="relative shrink-0" style={{ width: STAGE, height: STAGE + 14 }}>
          <span aria-hidden className="absolute rounded-full border border-dashed border-line"
                style={{ left: STAGE / 2 - RADIUS, top: STAGE / 2 - RADIUS, width: RADIUS * 2, height: RADIUS * 2 }} />
          <span aria-hidden className="absolute grid size-6 place-items-center rounded-full bg-raised text-[9px] text-muted"
                style={{ left: STAGE / 2 - 12, top: STAGE / 2 - 12 }}>
            subj
          </span>
          <span aria-hidden className="absolute rounded-[2px] border border-line bg-bay px-1 text-[9px] text-faint"
                style={{ left: STAGE / 2 - 18, top: STAGE + 1 }}>
            camera
          </span>
          {Object.entries(RING).map(([pos, deg]) => {
            const x = STAGE / 2 + RADIUS * Math.cos((deg * Math.PI) / 180);
            const y = STAGE / 2 + RADIUS * Math.sin((deg * Math.PI) / 180);
            const here = lights.map((l, i) => ({ l, i })).filter(({ l }) => l.position === pos);
            return (
              <button key={pos} type="button" onClick={() => place(pos)}
                      aria-label={`Light from ${positionLabel(pos).toLowerCase()}`}
                      className="group absolute grid size-6 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full"
                      style={{ left: x, top: y }}>
                {here.length ? here.map(({ l, i }) => (
                  <span key={i} className={cn("grid size-5 place-items-center rounded-full text-[10px] font-medium text-black",
                                              i === index && "ring-2 ring-chalk ring-offset-1 ring-offset-panel")}
                        style={{ background: colorOf(l) }}>{i + 1}</span>
                )) : <span className="size-2 rounded-full bg-line group-hover:bg-tungsten" />}
              </button>
            );
          })}
          {(["top", "below"] as const).map((pos, k) => {
            const here = lights.map((l, i) => ({ l, i })).filter(({ l }) => l.position === pos);
            return (
              <button key={pos} type="button" onClick={() => place(pos)} aria-pressed={here.length > 0}
                      className={cn("absolute flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px]",
                                    here.length ? "bg-raised text-chalk" : "text-faint hover:text-chalk")}
                      style={{ left: k === 0 ? 0 : undefined, right: k === 1 ? 0 : undefined, top: 0 }}>
                {here.map(({ l, i }) => <span key={i} className="size-2 rounded-full" style={{ background: colorOf(l) }} />)}
                {pos === "top" ? "Overhead" : "Below"}
              </button>
            );
          })}
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-2">
          {lights.length === 0 && (
            <p className="text-2xs leading-relaxed text-faint">Click a spot around the subject to place a key light.</p>
          )}
          {lights.length > 0 && (
            <div role="tablist" aria-label="Lights" className="flex flex-wrap items-center gap-1">
              {lights.map((l, i) => (
                <span key={i} className={cn("flex items-center rounded-full", i === index ? "bg-raised" : "")}>
                  <button type="button" role="tab" aria-selected={i === index} onClick={() => setActive(i)}
                          className={cn("flex items-center gap-1.5 py-1 pl-2 text-2xs", i === index ? "text-chalk" : "text-muted hover:text-chalk")}>
                    <span className="size-2 rounded-full" style={{ background: colorOf(l) }} aria-hidden />
                    {ROLE[i]}
                  </button>
                  <button type="button" aria-label={`Remove the ${ROLE[i].toLowerCase()}`} onClick={() => remove(i)}
                          className="grid size-6 place-items-center rounded-full text-faint hover:text-chalk">
                    <X className="size-3" aria-hidden />
                  </button>
                </span>
              ))}
              {lights.length < MAX_LIGHTS && (
                <button type="button" onClick={add} className="flex items-center gap-1 rounded-full px-2 py-1 text-2xs text-muted hover:text-chalk">
                  <Plus className="size-3" aria-hidden /> Second light
                </button>
              )}
            </div>
          )}
          {current && (
            <>
              <label className="flex flex-col gap-1 text-2xs text-faint">
                Source
                <select value={current.source} onChange={(e) => set({ source: e.target.value })}
                        className="h-8 rounded-[var(--radius-control)] border border-line-soft bg-bay px-2 text-xs text-chalk outline-none focus:border-tungsten/60">
                  {lib.light_sources.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                </select>
              </label>
              <p className="text-2xs text-faint">From {positionLabel(current.position ?? "front_left").toLowerCase()}</p>
            </>
          )}
        </div>
      </div>

      {current && (
        <div className="flex flex-col gap-2">
          <div role="radiogroup" aria-label={`${ROLE[index]} color`} className="flex flex-wrap gap-1.5">
            <button type="button" role="radio" aria-checked={!current.color} onClick={() => set({ color: null })}
                    title="The source's own color"
                    className={cn("size-6 rounded-full border border-dashed border-line text-[9px] text-faint",
                                  !current.color && "ring-2 ring-chalk ring-offset-1 ring-offset-panel")}>
              <span className="sr-only">Own color</span>
            </button>
            {lib.light_colors.map((c) => (
              <button key={c.id} type="button" role="radio" aria-checked={current.color === c.id} title={c.label}
                      aria-label={c.label} onClick={() => set({ color: c.id })}
                      className={cn("size-6 rounded-full border border-black/30",
                                    current.color === c.id && "ring-2 ring-chalk ring-offset-1 ring-offset-panel")}
                      style={{ background: c.swatch?.[0] }} />
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Segmented label={`${ROLE[index]} softness`} value={current.quality ?? "soft"} options={["soft", "hard"] as const}
                       render={(q) => (q === "soft" ? "Soft" : "Hard")} onChange={(quality) => set({ quality })} />
            <Segmented label={`${ROLE[index]} strength`} value={current.intensity ?? "normal"}
                       options={["dim", "normal", "bright"] as const}
                       render={(v) => v.charAt(0).toUpperCase() + v.slice(1)} onChange={(intensity) => set({ intensity })} />
          </div>
        </div>
      )}
    </div>
  );
}
