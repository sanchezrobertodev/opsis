"use client";

import { useMemo, useState } from "react";
import { type Asset, viaName } from "@/lib/api";
import { useGenerations, useRegistry } from "@/lib/queries";
import { KINDS, modelsFor, type StudioKind } from "@/lib/studioKinds";
import { AssetViewer } from "@/components/library/AssetViewer";
import { TakeCard } from "@/components/takes/TakeCard";
import { CreatePanel } from "./CreatePanel";

const EMPTY: Record<StudioKind, string> = {
  video: "Describe a shot on the left and press Generate. Each take lands here while it renders, then plays in place.",
  image: "Describe a frame on the left and press Generate. Use the results as start frames, references or characters.",
  edit: "Pick a clip and what to do with it on the left. The edited version lands here; the original stays untouched.",
  motion: "Add a character image and a clip whose movement it should copy. The result lands here.",
};

/**
 * A studio page: the Create form on the left, its takes on the right. `ops` narrows the models and modes
 * (Edit video passes the chosen operation); `header` sits above the form.
 */
export function CreateStudio({
  studio,
  ops,
  header,
}: {
  studio: StudioKind;
  ops?: string[];
  header?: React.ReactNode;
}) {
  const kind = KINDS[studio];
  const { data: registry, error } = useRegistry();
  // Create video/image list every take of their type; Edit and Motion only takes from their own ops.
  const { data: gens, isLoading } = useGenerations(kind.output, studio === "video" || studio === "image" ? undefined : kind.ops);
  const [open, setOpen] = useState<Asset | null>(null);

  const models = useMemo(() => modelsFor(registry?.models ?? [], studio, ops), [registry, studio, ops]);
  const names = useMemo(() => Object.fromEntries((registry?.models ?? []).map((m) => [m.id, m.name])), [registry]);
  const items = gens?.items ?? [];
  // Take numbers count up from the oldest generation in view, like a shooting log.
  const total = items.length;

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-full max-w-[400px] shrink-0 flex-col border-r border-line-soft bg-panel/40">
        {header}
        <div className="min-h-0 flex-1">
          {error ? (
            <div className="p-5 text-sm text-muted">
              The API isn&apos;t reachable at the configured address. Start everything with <code>pnpm dev</code>.
            </div>
          ) : models.length === 0 && registry ? (
            <div className="p-5 text-sm text-muted">No model in the registry does this yet.</div>
          ) : (
            <CreatePanel studio={studio} models={models} ops={ops} />
          )}
        </div>
      </aside>

      <section aria-label="Takes" className="min-w-0 flex-1 overflow-y-auto p-6 scrollbar-quiet">
        {isLoading ? null : items.length === 0 ? (
          <div className="mx-auto mt-24 max-w-sm text-center">
            <p className="type-title text-xl">No takes yet</p>
            <p className="mt-2 text-sm leading-relaxed text-muted">{EMPTY[studio]}</p>
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] items-start gap-x-5 gap-y-8">
            {items.map((g, i) => (
              <TakeCard
                key={g.id}
                gen={g}
                takeNumber={total - i}
                modelName={viaName(g, names[g.model_id] ?? g.model_id)}
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
