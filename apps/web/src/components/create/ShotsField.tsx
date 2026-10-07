"use client";

import { Plus, X } from "lucide-react";
import type { Param } from "@/lib/api";
import { Field, Slider } from "@/components/ui/controls";

type ShotsParam = Extract<Param, { type: "shots" }>;
export type ShotValue = { prompt: string; duration: number };

export function startingShots(param: ShotsParam): ShotValue[] {
  const each = Math.max(param.min_shot_s ?? 1, Math.min(4, Math.floor((param.max_total_s ?? 15) / (param.min_shots ?? 2))));
  return Array.from({ length: param.min_shots ?? 2 }, () => ({ prompt: "", duration: each }));
}

/** Multi-shot: the take's shots in order, each with what happens and how long it lasts. */
export function ShotsField({ param, value, onChange }: {
  param: ShotsParam; value: unknown; onChange: (v: ShotValue[]) => void;
}) {
  const shots = Array.isArray(value) && value.length ? (value as ShotValue[]) : startingShots(param);
  const maxTotal = param.max_total_s ?? 15;
  const minShot = param.min_shot_s ?? 1;
  const total = shots.reduce((s, x) => s + (Number(x.duration) || 0), 0);
  const set = (i: number, patch: Partial<ShotValue>) => onChange(shots.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  return (
    <Field label={param.label ?? "Shots"} hint={param.help ?? "Each shot is described on its own; the take cuts between them."}>
      <ol className="flex flex-col gap-2">
        {shots.map((shot, i) => {
          const room = maxTotal - (total - shot.duration);
          return (
            <li key={i} className="flex flex-col gap-2 rounded-[var(--radius-control)] bg-bay p-2.5">
              <div className="flex items-center gap-2">
                <span className="tabular text-2xs text-faint">Shot {i + 1}</span>
                {shots.length > (param.min_shots ?? 2) && (
                  <button type="button" aria-label={`Remove shot ${i + 1}`} onClick={() => onChange(shots.filter((_, j) => j !== i))}
                          className="ml-auto grid size-6 place-items-center rounded-full text-faint hover:bg-raised hover:text-chalk">
                    <X className="size-3" aria-hidden />
                  </button>
                )}
              </div>
              <textarea value={shot.prompt} onChange={(e) => set(i, { prompt: e.target.value })} rows={2}
                        aria-label={`What happens in shot ${i + 1}`} placeholder="What the camera sees in this shot"
                        className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-panel px-2.5 py-1.5 text-sm outline-none placeholder:text-faint focus:border-tungsten/60" />
              <Slider value={shot.duration} min={minShot} max={Math.max(minShot, Math.min(param.max_shot_s ?? 15, room))} step={1}
                      onChange={(duration) => set(i, { duration })} format={(v) => `${v} s`} />
            </li>
          );
        })}
      </ol>
      <div className="mt-2 flex items-center gap-2">
        {shots.length < (param.max_shots ?? 6) && total + minShot <= maxTotal && (
          <button type="button" onClick={() => onChange([...shots, { prompt: "", duration: minShot }])}
                  className="flex items-center gap-1 rounded-full px-2 py-1 text-xs text-muted hover:text-chalk">
            <Plus className="size-3.5" aria-hidden /> Add a shot
          </button>
        )}
        <span className="tabular ml-auto text-2xs text-faint">{total} of {maxTotal} s</span>
      </div>
    </Field>
  );
}
