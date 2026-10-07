"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Box, Globe, LoaderCircle, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api, type Asset, isActive, mediaUrl, unwrap } from "@/lib/api";
import { cn } from "@/lib/cn";
import { addObject, newObject } from "@/lib/jutsu3d";
import { uploadFile, useAssets, useGeneration } from "@/lib/queries";
import { modelThumbnail, saveThumbnail } from "@/lib/thumbs";
import { worldObject } from "@/lib/worlds";
import { QuickGenerate } from "@/components/create/QuickGenerate";
import { Button, Dialog, Segmented } from "@/components/ui/controls";

type Source = "library" | "file" | "generate";

/** Give a model a library picture if it has none (drawn here: the server can't render 3D). */
async function ensureThumb(asset: Asset): Promise<void> {
  if (asset.thumb_url) return;
  try {
    await saveThumbnail(asset.id, await modelThumbnail(mediaUrl(asset.url) ?? asset.url));
  } catch {
    /* a missing picture is cosmetic */
  }
}

function place(asset: Asset) {
  addObject(newObject("model", { asset_id: asset.id, name: asset.title || "Model" }));
}

/** Put a 3D model in the scene: one from the library, a GLB file, or one generated from a picture. */
export function AddModel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [source, setSource] = useState<Source>("library");
  return (
    <Dialog open={open} onClose={onClose} title="Add a model" wide>
      <div className="flex flex-col gap-5">
        <Segmented label="From" value={source} options={["library", "file", "generate"] as const} onChange={setSource}
                   render={(s) => (s === "library" ? "Your models" : s === "file" ? "A GLB file" : "A picture")} />
        {source === "library" && <FromLibrary onPlaced={onClose} />}
        {source === "file" && <FromFile onPlaced={onClose} />}
        {source === "generate" && <FromPicture onPlaced={onClose} onCancel={onClose} />}
      </div>
    </Dialog>
  );
}

function FromLibrary({ onPlaced }: { onPlaced: () => void }) {
  const { data, isLoading } = useAssets({ kind: "model3d" });
  // colliders belong to their worlds; they aren't props
  const models = (data?.items ?? []).filter((a) => a.metadata?.part !== "collider");
  if (isLoading) return <p className="text-sm text-muted">Loading your models…</p>;
  if (!models.length) return <p className="text-sm text-muted">No models yet. Import a GLB file or make one from a picture.</p>;
  return (
    <ul className="grid max-h-[50vh] grid-cols-3 gap-2 overflow-y-auto scrollbar-quiet sm:grid-cols-4">
      {models.map((a) => (
        <li key={a.id}>
          <button type="button" onClick={() => { place(a); onPlaced(); }}
                  className="flex w-full flex-col gap-1 rounded-[var(--radius-control)] border border-line-soft p-1.5 text-left hover:border-tungsten/50">
            {a.thumb_url
              // eslint-disable-next-line @next/next/no-img-element
              ? <img src={mediaUrl(a.thumb_url)} alt="" className="aspect-square w-full rounded-[3px] bg-black object-cover" />
              : <span className="grid aspect-square w-full place-items-center rounded-[3px] bg-black/40"><Box className="size-6 text-faint" aria-hidden /></span>}
            <span className="truncate text-2xs text-muted">{a.title || "Model"}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function FromFile({ onPlaced }: { onPlaced: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const [busy, setBusy] = useState<number | null>(null);
  async function upload(file: File) {
    if (!/\.glb$/i.test(file.name)) {
      toast.error("Pick a .glb file (binary glTF). Export one from Blender, Mixamo or a 3D model site.");
      return;
    }
    setBusy(0);
    try {
      const asset = await uploadFile(file, setBusy);
      await ensureThumb(asset);
      void qc.invalidateQueries({ queryKey: ["assets"] });
      place(asset);
      onPlaced();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "The upload failed");
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="flex flex-col items-start gap-3">
      <p className="text-sm text-muted">A GLB file (binary glTF) with its textures inside. Rigged characters keep their skeleton.</p>
      <input ref={input} type="file" accept=".glb,model/gltf-binary" className="sr-only" aria-label="GLB file"
             onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void upload(f); }} />
      <Button tone="primary" onClick={() => input.current?.click()} disabled={busy !== null}>
        {busy !== null ? <><LoaderCircle className="size-4 animate-spin" aria-hidden /> {Math.round(busy * 100)}%</>
          : <><Upload className="size-4" aria-hidden /> Choose a GLB file</>}
      </Button>
    </div>
  );
}

function FromPicture({ onPlaced, onCancel }: { onPlaced: () => void; onCancel: () => void }) {
  const [genId, setGenId] = useState<string>();
  const { data: gen } = useGeneration(genId);
  const placed = useRef<string | null>(null);
  const model = gen?.assets?.find((a) => a.kind === "model3d");

  useEffect(() => {
    if (!model || placed.current === model.id) return;
    placed.current = model.id;
    void ensureThumb(model);
    place(model);
    toast(`${model.title || "The model"} is in the scene`);
    onPlaced();
  }, [model, onPlaced]);

  if (!genId) {
    return (
      <QuickGenerate op="to_3d" submitLabel="Make the model" onStarted={(g) => setGenId(g.id)} onCancel={onCancel}
                     promptPlaceholder="Optional: what the surface looks like (worn leather, brushed steel)" />
    );
  }
  const failed = gen && !isActive(gen.status) && gen.status !== "succeeded";
  return (
    <div className={cn("flex flex-col gap-2 text-sm", failed ? "text-fault" : "text-muted")} aria-live="polite">
      {failed ? <p>{gen?.error_message ?? "The model wasn't made."}</p> : (
        <p className="flex items-center gap-2">
          <LoaderCircle className="size-4 animate-spin text-tungsten" aria-hidden />
          Making the model{gen?.progress ? ` (${Math.round(gen.progress * 100)}%)` : ""}. It goes into the scene when
          it&apos;s ready; if you close this, it will be under Your models.
        </p>
      )}
    </div>
  );
}

/** Set the scene in one of your worlds (made in World Creation). */
export function AddWorld({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data, isLoading } = useAssets({ kind: "splat" });
  const [busy, setBusy] = useState<string | null>(null);
  const worlds = data?.items ?? [];
  async function pick(splat: Asset) {
    setBusy(splat.id);
    try {
      let collider: Asset | null = null;
      if (splat.generation_id) {
        const gen = await unwrap(api.GET("/generations/{generation_id}", { params: { path: { generation_id: splat.generation_id } } }));
        collider = gen.assets.find((a) => a.kind === "model3d" && a.metadata?.part === "collider") ?? null;
      }
      addObject(worldObject(splat, collider));
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't add the world");
    } finally {
      setBusy(null);
    }
  }
  return (
    <Dialog open={open} onClose={onClose} title="Set the scene in a world" wide>
      {isLoading ? <p>Loading your worlds…</p> : !worlds.length ? (
        <p>No worlds yet. <Link href="/worlds" className="text-chalk underline">Make one in World Creation</Link>.</p>
      ) : (
        <ul className="grid max-h-[50vh] grid-cols-3 gap-2 overflow-y-auto scrollbar-quiet">
          {worlds.map((w) => (
            <li key={w.id}>
              <button type="button" onClick={() => void pick(w)} disabled={busy !== null}
                      className="flex w-full flex-col gap-1 rounded-[var(--radius-control)] border border-line-soft p-1.5 text-left hover:border-tungsten/50">
                {w.thumb_url
                  // eslint-disable-next-line @next/next/no-img-element
                  ? <img src={mediaUrl(w.thumb_url)} alt="" className="aspect-video w-full rounded-[3px] bg-black object-cover" />
                  : <span className="grid aspect-video w-full place-items-center rounded-[3px] bg-black/40"><Globe className="size-6 text-faint" aria-hidden /></span>}
                <span className="flex items-center gap-1 truncate text-2xs text-muted">
                  {busy === w.id && <LoaderCircle className="size-3 animate-spin" aria-hidden />}{w.title || "World"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
