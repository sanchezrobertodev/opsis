"use client";

import { useState } from "react";
import type { Asset } from "@/lib/api";
import { type AssetFilter, useAssets } from "@/lib/queries";
import { Segmented } from "@/components/ui/controls";
import { AssetTile } from "./AssetTile";
import { AssetViewer } from "./AssetViewer";

const FILTERS = {
  all: { label: "Everything", filter: {} },
  video: { label: "Videos", filter: { kind: "video" } },
  image: { label: "Images", filter: { kind: "image" } },
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
      <div className="mb-6 max-w-lg">
        <Segmented
          label="Show"
          value={key}
          options={Object.keys(FILTERS) as FilterKey[]}
          render={(k) => FILTERS[k].label}
          onChange={setKey}
        />
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
