"use client";

import { ChevronsUpDown } from "lucide-react";
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
};

export function vendorName(id: string) {
  return VENDORS[id] ?? id.charAt(0).toUpperCase() + id.slice(1);
}

function priceNote(m: ModelSummary) {
  if (m.tags.includes("local")) return ", free on this computer";
  const unit = m.output_type === "image" ? "an image" : "a clip";
  return m.from_price_usd != null ? `, live from ${usd(m.from_price_usd)} ${unit}` : "";
}

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
  const current = models.find((m) => m.id === value);

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
              {vendorName(current.vendor)}
              {priceNote(current)}
            </span>
          )}
        </span>
        <ChevronsUpDown className="size-4 shrink-0 text-faint" aria-hidden />
      </button>

      <Dialog open={open} onClose={() => setOpen(false)} title="Choose a model" wide>
        <ul className="grid gap-2 sm:grid-cols-2">
          {models.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                onClick={() => {
                  onChange(m.id);
                  setOpen(false);
                }}
                aria-pressed={m.id === value}
                className={cn(
                  "flex h-full w-full flex-col gap-2 rounded-[var(--radius-control)] border p-4 text-left transition-colors",
                  m.id === value ? "border-tungsten/60 bg-raised" : "border-line-soft hover:border-line hover:bg-raised/60",
                )}
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="type-title text-[15px] text-chalk">{m.name}</span>
                  <span className="tabular text-2xs text-faint">
                    {m.tags.includes("local") ? "free on this computer" : m.from_price_usd != null ? `from ${usd(m.from_price_usd)}` : ""}
                  </span>
                </span>
                <span className="text-xs leading-relaxed text-muted">{m.description}</span>
                <span className="mt-auto text-2xs text-faint">
                  {m.modes.map((mode) => mode.label).join(", ")}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </Dialog>
    </>
  );
}
