"use client";

import { useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import type { Asset } from "@/lib/api";
import { type AssetFilter, useAssets } from "@/lib/queries";
import { redrawModelThumbnails } from "@/lib/thumbs";
import { Button, Segmented } from "@/components/ui/controls";
import { AssetTile } from "./AssetTile";
import { AssetViewer } from "./AssetViewer";

const FILTERS = {
  all: { label: "Everything", filter: {} },
  video: { label: "Videos", filter: { kind: "video" } },
  image: { label: "Images", filter: { kind: "image" } },
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

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-quiet">
      <div className="mb-6 flex flex-wrap items-center gap-4">
        <div className="w-full max-w-xl">
          <Segmented
            label="Show"
            value={key}
            options={Object.keys(FILTERS) as FilterKey[]}
            render={(k) => FILTERS[k].label}
            onChange={setKey}
          />
        </div>
        {key === "models" && items.length > 0 && <RedrawModels />}
      </div>

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
              <AssetTile asset={a} onOpen={setOpen} />
            </li>
          ))}
        </ul>
      )}

      <AssetViewer asset={open} onClose={() => setOpen(null)} />
    </div>
  );
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
