"use client";

import { AudioLines, Film, ImagePlus, LoaderCircle, Library, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError, type MediaSlot, mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { SlotAsset } from "@/lib/draft";
import { uploadFile, useAsset, useAssets, usePatchAsset } from "@/lib/queries";
import { Button, Dialog, Segmented } from "@/components/ui/controls";
import { OpensFullSize } from "@/components/ui/PictureViewer";

const ACCEPT: Record<string, string> = { image: "image/*", video: "video/*", audio: "audio/*" };

/** The upload icon says what the slot takes: a picture, a clip, a sound, or anything else. */
function UploadIcon({ kinds }: { kinds: string[] }) {
  const main = new Set(kinds.map((k) => (k === "mask" ? "image" : k === "mask_video" ? "video" : k)));
  const only = main.size === 1 ? [...main][0] : "";
  if (only === "image") return <ImagePlus className="size-4" aria-hidden />;
  if (only === "video") return <Film className="size-4" aria-hidden />;
  if (only === "audio") return <AudioLines className="size-4" aria-hidden />;
  return <Upload className="size-4" aria-hidden />;
}

/** A file picked for a slot. One deleted since (a saved draft outlives it) leaves the slot, so it can't block a run. */
function Chosen({ asset: a, label, onRemove }: { asset: SlotAsset; label: string; onRemove: (id: string) => void }) {
  const { error } = useAsset(a.id);
  const gone = error instanceof ApiError && error.status === 404;
  const dropped = useRef(false);
  useEffect(() => {
    if (!gone || dropped.current) return;
    dropped.current = true;
    onRemove(a.id);
    toast(`${a.title ?? "A file"} was deleted, so it was taken out of ${label}.`);
  }, [gone, a.id, a.title, label, onRemove]);
  const picture = a.kind === "image" || a.thumb ? mediaUrl(a.thumb ?? a.url) : undefined;
  return (
    <div className="lumen">
      {picture && <div aria-hidden className="lumen-glow lumen-glow-sm" style={{ backgroundImage: `url("${picture}")` }} />}
      <div className="group relative size-20 overflow-hidden rounded-[var(--radius-frame)] bg-raised">
        {picture && (a.kind === "image" || a.kind === "mask") ? (
          <OpensFullSize src={mediaUrl(a.url) ?? picture} title={a.title ?? label} className="size-full">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={picture} alt="" className="size-full object-cover" />
          </OpensFullSize>
        ) : picture ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={picture} alt={a.title ?? label} className="size-full object-cover" />
        ) : (
          <span className="grid size-full place-items-center text-2xs text-muted">{a.kind}</span>
        )}
        <button
          type="button"
          onClick={() => onRemove(a.id)}
          aria-label={`Remove ${a.title ?? "media"}`}
          className="absolute top-1 right-1 grid size-6 place-items-center rounded-full bg-bay/85 text-chalk opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
        >
          <X className="size-3.5" aria-hidden />
        </button>
      </div>
    </div>
  );
}

/**
 * Ctrl+V anywhere on the page fills a slot: the one holding focus if it takes the file, else the first visible
 * open slot that does. Every slot listens; only that one takes it.
 */
function pasteTarget(kind: string): Element | null {
  const takes = (el: Element) => (el.getAttribute("data-paste-kinds") ?? "").split(" ").includes(kind);
  const open = [...document.querySelectorAll("[data-paste-kinds]")].filter(
    (el) => takes(el) && (el as HTMLElement).offsetParent !== null,
  );
  return open.find((el) => el.contains(document.activeElement)) ?? open[0] ?? null;
}

/** Inputs worth keeping a library of: clips used as movement are tagged so they're easy to reuse. */
const ROLE_LIBRARY: Record<string, { tag: string; label: string }> = {
  driving_video: { tag: "driving", label: "Movement clips" },
};

export function MediaSlotField({
  slot,
  value,
  onAdd,
  onRemove,
}: {
  slot: MediaSlot;
  value: SlotAsset[];
  onAdd: (asset: SlotAsset) => void;
  onRemove: (id: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const patch = usePatchAsset();
  const library = ROLE_LIBRARY[slot.role];
  const [uploading, setUploading] = useState<number | null>(null);
  const [picking, setPicking] = useState(false);
  const [dragging, setDragging] = useState(false);
  const max = slot.max ?? 1;
  const full = value.length >= max;
  const add = (a: SlotAsset) => {
    onAdd(a);
    if (library) patch.mutate({ id: a.id, add_tags: [library.tag] });
  };
  const label = slot.label ?? slot.role.replace(/_/g, " ");
  const zone = useRef<HTMLDivElement>(null);
  const pastable = slot.kinds.filter((k) => k in ACCEPT);

  useEffect(() => {
    if (full || !pastable.length) return;
    const onPaste = (e: ClipboardEvent) => {
      const files = [...(e.clipboardData?.files ?? [])];
      const kind = files[0]?.type.split("/")[0];
      if (!kind || !zone.current || pasteTarget(kind) !== zone.current) return;
      e.preventDefault();
      void upload(files);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  });

  async function upload(files: FileList | File[]) {
    for (const file of Array.from(files).slice(0, Math.max(1, max - value.length))) {
      const kind = file.type.split("/")[0];
      if (!slot.kinds.includes(kind as never)) {
        toast.error(`${label} takes ${slot.kinds.join(" or ")} files; ${file.name} is ${kind || "unknown"}.`);
        continue;
      }
      setUploading(0);
      try {
        const asset = await uploadFile(file, setUploading);
        add({ id: asset.id, kind: asset.kind, thumb: asset.thumb_url, url: asset.url, title: asset.title });
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Upload failed");
      } finally {
        setUploading(null);
      }
    }
  }

  return (
    <div role="group" aria-label={label.charAt(0).toUpperCase() + label.slice(1)} className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-muted">
          {label.charAt(0).toUpperCase() + label.slice(1)}
          {!slot.required && <span className="text-faint"> (optional)</span>}
        </span>
        {max > 1 && (
          <span className="tabular text-2xs text-faint">
            {value.length} of {max}
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {value.map((a) => (
          <Chosen key={a.id} asset={a} label={label} onRemove={onRemove} />
        ))}
        {!full && (
          <div
            ref={zone}
            data-paste-kinds={pastable.join(" ")}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              void upload(e.dataTransfer.files);
            }}
            className={cn(
              "flex h-20 min-w-40 flex-1 items-stretch overflow-hidden rounded-[var(--radius-frame)] border border-dashed",
              dragging ? "border-tungsten bg-tungsten/10" : "border-line",
            )}
          >
            <button
              type="button"
              onClick={() => input.current?.click()}
              disabled={uploading !== null}
              className="flex flex-1 flex-col items-center justify-center gap-1 px-2 text-center text-2xs text-muted hover:bg-raised hover:text-chalk"
            >
              {uploading !== null ? (
                <>
                  <LoaderCircle className="size-4 animate-spin" aria-hidden />
                  <span className="tabular">{Math.round(uploading * 100)}%</span>
                </>
              ) : (
                <>
                  <UploadIcon kinds={slot.kinds} />
                  Upload, drop or paste
                </>
              )}
            </button>
            <button
              type="button"
              onClick={() => setPicking(true)}
              className="flex flex-1 flex-col items-center justify-center gap-1 border-l border-dashed border-line text-2xs text-muted hover:bg-raised hover:text-chalk"
            >
              <Library className="size-4" aria-hidden />
              From library
            </button>
          </div>
        )}
      </div>
      <input
        ref={input}
        type="file"
        hidden
        multiple={max > 1}
        accept={slot.kinds.map((k) => ACCEPT[k]).join(",")}
        onChange={(e) => {
          if (e.target.files) void upload(e.target.files);
          e.target.value = "";
        }}
      />
      <LibraryPicker
        open={picking}
        kinds={slot.kinds}
        library={library}
        picked={max > 1 ? value.map((v) => v.id) : undefined}
        onClose={() => setPicking(false)}
        onPick={(a) => {
          if (value.some((v) => v.id === a.id)) return onRemove(a.id);
          add(a);
          if (value.length + 1 >= max) setPicking(false);
        }}
      />
    </div>
  );
}

function LibraryPicker({
  open,
  kinds,
  library,
  picked,
  onClose,
  onPick,
}: {
  open: boolean;
  kinds: string[];
  /** A tagged sub-library to show first (e.g. saved movement clips). */
  library?: { tag: string; label: string };
  /** Set for slots that take several items: the picker stays open and marks what's chosen. */
  picked?: string[];
  onClose: () => void;
  onPick: (a: SlotAsset) => void;
}) {
  const kind = kinds.length === 1 ? (kinds[0] as "image" | "video" | "audio") : undefined;
  const [scope, setScope] = useState<"tagged" | "all">("tagged");
  const tagged = useAssets({ kind, tag: library?.tag });
  const all = useAssets({ kind });
  const showTagged = Boolean(library) && scope === "tagged" && (tagged.data?.items.length ?? 0) > 0;
  const { data, isLoading } = showTagged ? tagged : all;
  const items = (data?.items ?? []).filter((a) => kinds.includes(a.kind));
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Choose from your library"
      wide
      footer={
        picked && (
          <Button type="button" tone="primary" onClick={onClose}>
            Done{picked.length ? `, ${picked.length} chosen` : ""}
          </Button>
        )
      }
    >
      {library && (tagged.data?.items.length ?? 0) > 0 && (
        <div className="mb-3 max-w-xs">
          <Segmented
            label="Show"
            value={scope}
            options={["tagged", "all"] as const}
            render={(v) => (v === "tagged" ? library.label : "Everything")}
            onChange={setScope}
          />
        </div>
      )}
      {isLoading ? (
        <p>Loading your library…</p>
      ) : items.length === 0 ? (
        <p>Nothing here yet. Upload a file or generate one first.</p>
      ) : (
        <ul className="grid max-h-[60vh] grid-cols-3 gap-2 overflow-y-auto scrollbar-quiet sm:grid-cols-4">
          {items.map((a) => (
            <li key={a.id}>
              <button
                type="button"
                onClick={() => onPick({ id: a.id, kind: a.kind, thumb: a.thumb_url, url: a.url, title: a.title })}
                aria-pressed={picked ? picked.includes(a.id) : undefined}
                className={cn(
                  "block aspect-square w-full overflow-hidden rounded-[var(--radius-frame)] bg-raised outline-offset-2 hover:opacity-85",
                  picked?.includes(a.id) && "ring-2 ring-tungsten ring-offset-2 ring-offset-panel",
                )}
                title={a.title ?? undefined}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={mediaUrl(a.thumb_url ?? a.url)} alt={a.title ?? "Library item"} className="size-full object-cover" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
