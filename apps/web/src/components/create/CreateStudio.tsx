"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { type Asset, isActive, viaName } from "@/lib/api";
import { type Removal, useDeleteGenerations, useGenerations, useRegistry } from "@/lib/queries";
import { KINDS, modelsFor, type StudioKind } from "@/lib/studioKinds";
import { AssetViewer } from "@/components/library/AssetViewer";
import { TakeCard } from "@/components/takes/TakeCard";
import { Button, Dialog } from "@/components/ui/controls";
import { SelectButton, SelectionBar, useSelection } from "@/components/ui/Selection";
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
  const selection = useSelection();
  const [confirming, setConfirming] = useState(false);
  const removal = useDeleteGenerations();

  const models = useMemo(() => modelsFor(registry?.models ?? [], studio, ops), [registry, studio, ops]);
  const modelsById = useMemo(() => Object.fromEntries((registry?.models ?? []).map((m) => [m.id, m])), [registry]);
  const items = gens?.items ?? [];
  // Take numbers count up from the oldest generation in view, like a shooting log.
  const total = items.length;
  // a take still rendering can't be deleted, so it can't be picked either
  const selectable = items.filter((g) => !isActive(g.status)).map((g) => g.id);
  const picked = selectable.filter((id) => selection.picked.has(id));

  function removePicked() {
    removal.mutate(picked, {
      onSuccess: (r) => {
        setConfirming(false);
        selection.stop();
        report(r);
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Could not delete the takes"),
    });
  }

  return (
    <div className="flex h-full min-h-0">
      <aside className="m-3 mr-0 flex w-[min(400px,calc(100%-1.5rem))] shrink-0 flex-col overflow-hidden glass rounded-2xl border border-line-soft">
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
          <>
            {selection.selecting ? (
              <SelectionBar
                count={picked.length}
                selectable={selectable.length}
                onAll={() => selection.set(selectable)}
                onNone={() => selection.set([])}
                onDelete={() => setConfirming(true)}
                onDone={selection.stop}
              />
            ) : (
              <div className="mb-4 flex items-center justify-between gap-3">
                <p className="tabular text-xs text-muted">
                  {total} take{total === 1 ? "" : "s"}
                </p>
                <SelectButton onClick={selection.start} />
              </div>
            )}
            <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] items-start gap-4">
              {items.map((g, i) => (
                <TakeCard
                  key={g.id}
                  gen={g}
                  takeNumber={total - i}
                  modelName={viaName(g, modelsById[g.model_id]?.name ?? g.model_id)}
                  modelFamily={modelsById[g.model_id]?.family}
                  onOpen={setOpen}
                  selecting={selection.selecting}
                  selected={selection.picked.has(g.id)}
                  onToggle={() => selection.toggle(g.id)}
                />
              ))}
            </div>
          </>
        )}
      </section>

      <Dialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title={`Delete ${picked.length} take${picked.length === 1 ? "" : "s"}?`}
        footer={
          <>
            <Button tone="ghost" onClick={() => setConfirming(false)}>
              Keep them
            </Button>
            <Button tone="danger" onClick={removePicked} disabled={removal.isPending || picked.length === 0}>
              {removal.isPending ? "Deleting…" : `Delete ${picked.length}`}
            </Button>
          </>
        }
      >
        They leave your history and their files are deleted from disk. This can&apos;t be undone. What they cost stays
        in your spending records, and files a character or a film still uses are kept.
      </Dialog>

      <AssetViewer asset={open} onClose={() => setOpen(null)} />
    </div>
  );
}

function report(r: Removal) {
  if (r.deleted) {
    const kept = r.kept.length ? `. Kept what ${r.kept.join(", ")} uses.` : "";
    toast(`Deleted ${r.deleted} take${r.deleted === 1 ? "" : "s"}${kept}`);
  }
  if (r.failed.length) toast.error(`${r.failed.length} couldn't be deleted: ${r.failed[0]}`);
}
