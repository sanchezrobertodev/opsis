"use client";

import { useMemo, useState } from "react";
import type { Asset } from "@/lib/api";
import { useGenerations, useRegistry } from "@/lib/queries";
import { AssetViewer } from "@/components/library/AssetViewer";
import { TakeCard } from "@/components/takes/TakeCard";
import { CreatePanel } from "./CreatePanel";

export function CreateStudio({ studio }: { studio: "video" | "image" }) {
  const { data: registry, error } = useRegistry();
  const { data: gens, isLoading } = useGenerations(studio);
  const [open, setOpen] = useState<Asset | null>(null);

  const models = useMemo(
    () => (registry?.models ?? []).filter((m) => m.output_type === studio && m.status !== "shutdown"),
    [registry, studio],
  );
  const names = useMemo(() => Object.fromEntries((registry?.models ?? []).map((m) => [m.id, m.name])), [registry]);
  const items = gens?.items ?? [];
  // Take numbers count up from the oldest generation in view, like a shooting log.
  const total = items.length;

  return (
    <div className="flex h-full min-h-0">
      <aside className="w-full max-w-[400px] shrink-0 border-r border-line-soft bg-panel/40">
        {error ? (
          <div className="p-5 text-sm text-muted">
            The API isn&apos;t reachable at the configured address. Start everything with <code>pnpm dev</code>.
          </div>
        ) : (
          <CreatePanel studio={studio} models={models} />
        )}
      </aside>

      <section aria-label="Takes" className="min-w-0 flex-1 overflow-y-auto p-6 scrollbar-quiet">
        {isLoading ? null : items.length === 0 ? (
          <div className="mx-auto mt-24 max-w-sm text-center">
            <p className="type-title text-xl">No takes yet</p>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              Describe a shot on the left and press Generate. Each take lands here while it renders, then
              plays in place.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] items-start gap-x-5 gap-y-8">
            {items.map((g, i) => (
              <TakeCard
                key={g.id}
                gen={g}
                takeNumber={total - i}
                modelName={names[g.model_id] ?? g.model_id}
                onOpen={setOpen}
              />
            ))}
          </div>
        )}
      </section>

      <AssetViewer asset={open} onClose={() => setOpen(null)} />
    </div>
  );
}
