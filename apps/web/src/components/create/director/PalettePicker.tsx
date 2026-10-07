"use client";

import { useMemo, useState } from "react";
import type { Schemas } from "@studio/api-client";
import { cn } from "@/lib/cn";
import { Dialog } from "@/components/ui/controls";

type LookItem = Schemas["LookItem"];

export function Swatch({ colors, className }: { colors: string[]; className?: string }) {
  return (
    <span aria-hidden className={cn("flex h-4 w-9 shrink-0 overflow-hidden rounded-[3px] border border-line", className)}>
      {colors.map((c) => <span key={c} className="flex-1" style={{ background: c }} />)}
    </span>
  );
}

/** The color grade: a button showing the chosen palette, opening a grid of every palette as swatches. */
export function PalettePicker({ palettes, value, onChange }: {
  palettes: LookItem[]; value: string | null | undefined; onChange: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const chosen = palettes.find((p) => p.id === value);
  const shown = useMemo(() => palettes.filter((p) => !query || `${p.label} ${p.prompt}`.toLowerCase()
    .includes(query.toLowerCase())), [palettes, query]);

  return (
    <div className="flex flex-col gap-1 text-2xs text-faint">
      Color grade
      <button type="button" onClick={() => setOpen(true)} aria-haspopup="dialog"
              className="flex h-9 items-center gap-2 rounded-[var(--radius-control)] border border-line-soft bg-bay px-2 text-left text-xs text-chalk hover:border-tungsten/50">
        {chosen ? <Swatch colors={chosen.swatch ?? []} /> : <span className="h-4 w-9 rounded-[3px] border border-dashed border-line" aria-hidden />}
        <span className="truncate">{chosen?.label ?? "Any"}</span>
      </button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Color grade" wide>
        <div className="flex flex-col gap-3">
          <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search, e.g. warm, neon, monochrome"
                 className="h-9 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60" />
          <ul className="grid max-h-[55vh] grid-cols-2 gap-2 overflow-y-auto pr-1 scrollbar-quiet sm:grid-cols-4">
            <li>
              <button type="button" onClick={() => { onChange(null); setOpen(false); }} aria-pressed={!value}
                      className={cn("flex h-full w-full flex-col gap-2 rounded-[var(--radius-control)] border p-2 text-left",
                                    !value ? "border-tungsten/60 bg-raised" : "border-line-soft hover:border-tungsten/50")}>
                <span className="h-8 w-full rounded-[3px] border border-dashed border-line" aria-hidden />
                <span className="text-xs text-chalk">Any</span>
              </button>
            </li>
            {shown.map((p) => (
              <li key={p.id}>
                <button type="button" onClick={() => { onChange(p.id); setOpen(false); }} aria-pressed={p.id === value}
                        title={p.prompt}
                        className={cn("flex h-full w-full flex-col gap-2 rounded-[var(--radius-control)] border p-2 text-left",
                                      p.id === value ? "border-tungsten/60 bg-raised" : "border-line-soft hover:border-tungsten/50")}>
                  <Swatch colors={p.swatch ?? []} className="h-8 w-full" />
                  <span className="text-xs text-chalk">{p.label}</span>
                </button>
              </li>
            ))}
            {shown.length === 0 && <li className="col-span-full text-sm text-muted">No grade matches that.</li>}
          </ul>
        </div>
      </Dialog>
    </div>
  );
}
