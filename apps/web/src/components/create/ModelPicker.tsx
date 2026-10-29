"use client";

import { ChevronsUpDown, Search } from "lucide-react";
import { useState } from "react";
import type { ModelSummary } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { Dialog } from "@/components/ui/controls";

const VENDORS: Record<string, string> = {
  google: "Google",
  kuaishou: "Kuaishou",
  bytedance: "ByteDance",
  alibaba: "Alibaba",
  minimax: "MiniMax",
  runway: "Runway",
  luma: "Luma",
  bfl: "Black Forest Labs",
  lightricks: "Lightricks",
  xai: "xAI",
  "ai-media-studio": "Studio layer",
};

export function vendorName(id: string) {
  return VENDORS[id] ?? id.charAt(0).toUpperCase() + id.slice(1);
}

/** Where a model runs, in the order the picker lists them. */
const PLACES: { id: ModelSummary["runs_on"]; title: string; note: string }[] = [
  { id: "local", title: "On this computer", note: "Free. Runs on your own GPU through ComfyUI." },
  { id: "cloud_gpu", title: "Your cloud GPU", note: "Open models on your own RunPod endpoint, billed by the GPU second." },
  { id: "hosted", title: "Hosted APIs", note: "Run by the provider and billed per generation." },
];

const UNIT: Record<string, string> = { image: "image", video: "clip", audio: "sound", "3d": "model" };

function price(m: ModelSummary): string | null {
  if (m.runs_on === "local" || m.from_price_usd === 0) return "Free";
  return m.from_price_usd != null ? `from ${usd(m.from_price_usd)} / ${UNIT[m.output_type] ?? "run"}` : null;
}

const isOpen = (m: ModelSummary) => m.license_tier.startsWith("open");

export function ModelPicker({
  models,
  value,
  onChange,
}: {
  models: ModelSummary[];
  value: string | undefined;
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const current = models.find((m) => m.id === value);
  const q = query.trim().toLowerCase();
  const shown = q
    ? models.filter((m) =>
        [m.name, vendorName(m.vendor), m.description, ...m.modes.map((x) => x.label)].join(" ").toLowerCase().includes(q),
      )
    : models;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex w-full items-center gap-3 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2.5 text-left hover:border-line"
        aria-haspopup="dialog"
      >
        <span className="min-w-0 flex-1">
          <span className="type-title block truncate text-[15px]">{current?.name ?? "Choose a model"}</span>
          {current && (
            <span className="block truncate text-2xs text-faint">
              {[vendorName(current.vendor), PLACES.find((p) => p.id === current.runs_on)?.title.toLowerCase(), price(current)]
                .filter(Boolean)
                .join(", ")}
            </span>
          )}
        </span>
        <ChevronsUpDown className="size-4 shrink-0 text-faint" aria-hidden />
      </button>

      <Dialog open={open} onClose={() => setOpen(false)} title="Choose a model" wide>
        {models.length > 8 && (
          <label className="mb-4 flex h-9 items-center gap-2 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 focus-within:border-tungsten/60">
            <Search className="size-4 text-faint" aria-hidden />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name, maker or what it does"
              aria-label="Search models"
              className="min-w-0 flex-1 bg-transparent text-sm text-chalk outline-none placeholder:text-faint"
            />
          </label>
        )}
        <div className="flex flex-col gap-6">
          {PLACES.map((place) => {
            const group = shown.filter((m) => m.runs_on === place.id);
            if (!group.length) return null;
            return (
              <section key={place.id} aria-labelledby={`place-${place.id}`}>
                <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                  <h3 id={`place-${place.id}`} className="type-title text-sm text-chalk">
                    {place.title}
                  </h3>
                  <p className="text-2xs text-faint">{place.note}</p>
                </div>
                <ul className="grid gap-2 sm:grid-cols-2">
                  {group.map((m) => (
                    <li key={m.id}>
                      <ModelCard
                        model={m}
                        selected={m.id === value}
                        onPick={() => {
                          onChange(m.id);
                          setOpen(false);
                        }}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
          {shown.length === 0 && <p className="text-sm text-muted">No model matches that.</p>}
        </div>
      </Dialog>
    </>
  );
}

function ModelCard({ model: m, selected, onPick }: { model: ModelSummary; selected: boolean; onPick: () => void }) {
  const cost = price(m);
  return (
    <button
      type="button"
      onClick={onPick}
      aria-pressed={selected}
      data-autofocus={selected || undefined}
      className={cn(
        "flex h-full w-full flex-col gap-2 rounded-[var(--radius-control)] border p-4 text-left transition-colors",
        selected ? "border-tungsten/60 bg-raised" : "border-line-soft hover:border-line hover:bg-raised/60",
      )}
    >
      <span className="flex items-start justify-between gap-3">
        <span className="min-w-0">
          <span className="type-title block text-[15px] text-chalk">{m.name}</span>
          <span className="block text-2xs text-faint">{vendorName(m.vendor)}</span>
        </span>
        {cost && (
          <span
            className={cn(
              "tabular shrink-0 rounded-full px-2 py-0.5 text-2xs",
              cost === "Free" ? "bg-done/15 text-done" : "bg-bay text-muted",
            )}
          >
            {cost}
          </span>
        )}
      </span>
      <span className="line-clamp-3 text-xs leading-relaxed text-muted">{m.description}</span>
      <span className="mt-auto flex flex-wrap gap-1 pt-1">
        {isOpen(m) && (
          <span className="rounded-full border border-tungsten/40 px-2 py-0.5 text-2xs text-tungsten">Open weights</span>
        )}
        {m.modes.map((mode) => (
          <span key={mode.id} className="rounded-full bg-bay px-2 py-0.5 text-2xs text-muted">
            {mode.label}
          </span>
        ))}
      </span>
    </button>
  );
}
