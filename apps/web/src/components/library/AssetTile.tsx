"use client";

import { AudioLines, Star } from "lucide-react";
import { useState } from "react";
import { type Asset, mediaUrl } from "@/lib/api";
import { cn, seconds } from "@/lib/cn";
import { SelectMark } from "@/components/ui/Selection";
import { GLASS_CARD, Lit } from "@/components/ui/Glass";
import { Waveform } from "./Waveform";

const KIND_LABEL: Record<string, string> = {
  audio: "Audio",
  video: "Video",
  image: "Image",
  mask: "Mask",
  mask_video: "Mask clip",
  model3d: "3D model",
  splat: "3D world",
  subtitle: "Transcript",
};

/** What made a sound, by the mode that made it (uploads have none). */
const SOUND_LABEL: Record<string, string> = {
  tts: "Voice", change: "Voice", dub: "Dub", music: "Music", song: "Song", sfx: "Sound effect",
};

/** Library tile, in the glass frame takes wear. Videos scrub through their filmstrip sprite as the pointer moves across them. While selecting,
 * a press picks the tile instead of opening it. Sounds show their waveform and keep their name in view. */
export function AssetTile({
  asset,
  onOpen,
  selecting,
  selected = false,
  onToggle,
}: {
  asset: Asset;
  onOpen: (a: Asset) => void;
  selecting?: boolean;
  selected?: boolean;
  onToggle?: () => void;
}) {
  const [frame, setFrame] = useState<number | null>(null);
  const [missing, setMissing] = useState(false);
  const frames = asset.filmstrip_frames ?? 0;
  const canScrub = asset.kind === "video" && asset.filmstrip_url && frames > 1;
  const aspect = asset.width && asset.height ? `${asset.width} / ${asset.height}` : "16 / 9";
  const sound = asset.kind === "audio";
  const picture = asset.thumb_url ?? (asset.kind === "image" ? asset.url : null);
  const mode = (asset.metadata as { mode?: string } | null)?.mode;

  return (
    <button
      type="button"
      onClick={() => (selecting ? onToggle?.() : onOpen(asset))}
      role={selecting ? "checkbox" : undefined}
      aria-checked={selecting ? selected : undefined}
      onPointerMove={(e) => {
        if (!canScrub) return;
        const r = e.currentTarget.getBoundingClientRect();
        setFrame(Math.min(frames - 1, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * frames))));
      }}
      onPointerLeave={() => setFrame(null)}
      className={cn(GLASS_CARD, "group relative block w-full p-1.5 text-left",
                    selected && "ring-2 ring-tungsten ring-offset-2 ring-offset-bay")}
      aria-label={`${asset.kind} ${asset.title ?? ""}`.trim()}
    >
      <Lit picture={picture && !missing ? mediaUrl(picture) : undefined}>
        <div className="relative overflow-hidden rounded-[var(--radius-frame)] bg-raised" style={{ aspectRatio: aspect }}>
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
        ) : missing ? (
          <span className="absolute inset-0 grid place-items-center text-xs text-faint">File missing</span>
        ) : picture ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={mediaUrl(picture)}
            alt=""
            loading="lazy"
            onError={() => setMissing(true)}
            className="absolute inset-0 size-full object-cover"
          />
        ) : sound && asset.status === "ready" ? (
          <span className="absolute inset-0 bg-gradient-to-br from-raised via-bay to-ember/25">
            <span className="absolute left-2.5 top-2 flex items-center gap-1.5 text-2xs text-muted">
              <AudioLines className="size-3.5 text-tungsten" aria-hidden />
              {SOUND_LABEL[mode ?? ""] ?? "Audio"}
            </span>
            <Waveform src={mediaUrl(asset.url)!} className="absolute inset-x-3 top-[24%] bottom-[34%]" />
          </span>
        ) : (
          // masks and older files have no picture; only a file still being read is "processing"
          <span className="absolute inset-0 grid place-items-center text-xs text-faint">
            {asset.status === "processing" || asset.status === "pending_upload" ? "Processing…" : KIND_LABEL[asset.kind] ?? asset.kind}
          </span>
        )}

        {canScrub && frame !== null && (
          <span aria-hidden className="absolute inset-x-0 bottom-0 h-0.5 bg-black/40">
            <span className="block h-full bg-tungsten" style={{ width: `${((frame + 1) / frames) * 100}%` }} />
          </span>
        )}
        <span className={cn("pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-2 bg-gradient-to-t from-black/70 to-transparent p-2 pt-6 text-2xs text-chalk transition-opacity",
                            sound ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100")}>
          <span className="truncate">{asset.title}</span>
          {asset.duration_ms ? <span className="tabular shrink-0">{seconds(asset.duration_ms)}</span> : null}
        </span>
        {selecting && (
          <span className="absolute top-2 left-2">
            <SelectMark selected={selected} />
          </span>
        )}
        {asset.favorite && (
          <Star className="absolute top-2 right-2 size-3.5 fill-tungsten text-tungsten" aria-label="Favorite" />
        )}
        </div>
      </Lit>
    </button>
  );
}
