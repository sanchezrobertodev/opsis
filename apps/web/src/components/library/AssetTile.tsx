"use client";

import { Star } from "lucide-react";
import { useState } from "react";
import { type Asset, mediaUrl } from "@/lib/api";
import { seconds } from "@/lib/cn";

/** Library tile. Videos scrub through their filmstrip sprite as the pointer moves across them. */
export function AssetTile({ asset, onOpen }: { asset: Asset; onOpen: (a: Asset) => void }) {
  const [frame, setFrame] = useState<number | null>(null);
  const frames = asset.filmstrip_frames ?? 0;
  const canScrub = asset.kind === "video" && asset.filmstrip_url && frames > 1;
  const aspect = asset.width && asset.height ? `${asset.width} / ${asset.height}` : "16 / 9";

  return (
    <button
      type="button"
      onClick={() => onOpen(asset)}
      onPointerMove={(e) => {
        if (!canScrub) return;
        const r = e.currentTarget.getBoundingClientRect();
        setFrame(Math.min(frames - 1, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * frames))));
      }}
      onPointerLeave={() => setFrame(null)}
      className="group relative block w-full overflow-hidden rounded-[var(--radius-frame)] bg-raised text-left"
      style={{ aspectRatio: aspect }}
      aria-label={`${asset.kind} ${asset.title ?? ""}`.trim()}
    >
      {canScrub && frame !== null ? (
        <span
          aria-hidden
          className="absolute inset-0 bg-no-repeat"
          style={{
            backgroundImage: `url(${mediaUrl(asset.filmstrip_url)})`,
            backgroundSize: `${frames * 100}% 100%`,
            backgroundPosition: `${(frame / (frames - 1)) * 100}% 0`,
          }}
        />
      ) : asset.thumb_url || asset.kind === "image" ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={mediaUrl(asset.thumb_url ?? asset.url)}
          alt=""
          loading="lazy"
          className="absolute inset-0 size-full object-cover"
        />
      ) : (
        <span className="absolute inset-0 grid place-items-center text-xs text-faint">Processing…</span>
      )}

      {canScrub && frame !== null && (
        <span aria-hidden className="absolute inset-x-0 bottom-0 h-0.5 bg-black/40">
          <span className="block h-full bg-tungsten" style={{ width: `${((frame + 1) / frames) * 100}%` }} />
        </span>
      )}
      <span className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-2 bg-gradient-to-t from-black/70 to-transparent p-2 pt-6 text-2xs text-chalk opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
        <span className="truncate">{asset.title}</span>
        {asset.duration_ms ? <span className="tabular shrink-0">{seconds(asset.duration_ms)}</span> : null}
      </span>
      {asset.favorite && (
        <Star className="absolute top-2 right-2 size-3.5 fill-tungsten text-tungsten" aria-label="Favorite" />
      )}
    </button>
  );
}
