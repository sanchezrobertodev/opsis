"use client";

import { useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import type { Asset } from "@/lib/api";
import { type AssetFilter, type Removal, useAssets, useDeleteAssets } from "@/lib/queries";
import { redrawModelThumbnails } from "@/lib/thumbs";
import { Button, Dialog, Segmented } from "@/components/ui/controls";
import { SelectButton, SelectionBar, useSelection } from "@/components/ui/Selection";
import { AssetTile } from "./AssetTile";
import { AssetViewer } from "./AssetViewer";

const FILTERS = {
  all: { label: "Everything", filter: {} },
  video: { label: "Videos", filter: { kind: "video" } },
  image: { label: "Images", filter: { kind: "image" } },
  audio: { label: "Sounds", filter: { kind: "audio" } },
  models: { label: "3D models", filter: { kind: "model3d" } },
  uploads: { label: "Uploads", filter: { source: "upload" } },
  favorites: { label: "Favorites", filter: { favorite: true } },
} satisfies Record<string, { label: string; filter: AssetFilter }>;
type FilterKey = keyof typeof FILTERS;

export function Library() {
  const [key, setKey] = useState<FilterKey>("all");
  const [open, setOpen] = useState<Asset | null>(null);
  const { data, isLoading } = useAssets(FILTERS[key].filter);
  const items = data?.items ?? [];
  const selection = useSelection();
  const [confirming, setConfirming] = useState(false);
  const removal = useDeleteAssets();
  const picked = items.filter((a) => selection.picked.has(a.id)).map((a) => a.id);

  function removePicked() {
    removal.mutate(picked, {
      onSuccess: (r) => {
        setConfirming(false);
        selection.stop();
        report(r);
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Could not delete the files"),
    });
  }

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-quiet">
      <div className="mb-6 flex flex-wrap items-center gap-4">
        <div className="w-full max-w-2xl">
          <Segmented
            label="Show"
            value={key}
            options={Object.keys(FILTERS) as FilterKey[]}
            render={(k) => FILTERS[k].label}
            onChange={setKey}
          />
        </div>
        {key === "models" && items.length > 0 && <RedrawModels />}
        {items.length > 0 && !selection.selecting && (
          <div className="ml-auto">
            <SelectButton onClick={selection.start} />
          </div>
        )}
      </div>
      {selection.selecting && items.length > 0 && (
        <SelectionBar
          count={picked.length}
          selectable={items.length}
          more={!!data?.next_cursor}
          onAll={() => selection.set(items.map((a) => a.id))}
          onNone={() => selection.set([])}
          onDelete={() => setConfirming(true)}
          onDone={selection.stop}
        />
      )}

      {isLoading ? null : items.length === 0 ? (
        <div className="mt-20 max-w-sm">
          <p className="type-title text-xl">Nothing here yet</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            {key === "favorites"
              ? "Star a take to keep it here."
              : "Generated takes and uploaded files collect here automatically."}
          </p>
        </div>
      ) : (
        <ul className="columns-[260px] gap-3">
          {items.map((a) => (
            <li key={a.id} className="mb-3 break-inside-avoid">
              <AssetTile
                asset={a}
                onOpen={setOpen}
                selecting={selection.selecting}
                selected={selection.picked.has(a.id)}
                onToggle={() => selection.toggle(a.id)}
              />
            </li>
          ))}
        </ul>
      )}

      <AssetViewer asset={open} onClose={() => setOpen(null)} />
      <Dialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title={`Delete ${picked.length} file${picked.length === 1 ? "" : "s"}?`}
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
        They leave your library and are deleted from disk. This can&apos;t be undone. Files a character, a film or a
        take still rendering uses are kept.
      </Dialog>
    </div>
  );
}

function report(r: Removal) {
  if (r.deleted) toast(`Deleted ${r.deleted} file${r.deleted === 1 ? "" : "s"}`);
  if (r.kept.length) toast(`${r.kept.length === 1 ? "One was" : "Some were"} kept: ${r.kept.join(" ")}`);
  if (r.failed.length) toast.error(`${r.failed.length} couldn't be deleted: ${r.failed[0]}`);
}

/** The library can't draw 3D files, so the browser redraws their pictures, e.g. after the lighting changed. */
function RedrawModels() {
  const qc = useQueryClient();
  const [progress, setProgress] = useState<[number, number] | null>(null);
  async function redraw() {
    setProgress([0, 0]);
    try {
      const { redrawn, failed } = await redrawModelThumbnails((done, total) => setProgress([done, total]));
      if (failed) toast.error(`Redrew ${redrawn} pictures; ${failed} model${failed > 1 ? "s" : ""} wouldn't load`);
      else toast.success(`Redrew ${redrawn} picture${redrawn === 1 ? "" : "s"}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't list your models");
    } finally {
      setProgress(null);
      void qc.invalidateQueries({ queryKey: ["assets"] });
    }
  }
  return (
    <Button size="sm" onClick={() => void redraw()} disabled={progress !== null}
            title="Draw every model's picture again with the current lighting">
      {progress ? <><LoaderCircle className="size-4 animate-spin" aria-hidden /> {progress[0]} of {progress[1] || "…"}</>
        : "Redraw pictures"}
    </Button>
  );
}
