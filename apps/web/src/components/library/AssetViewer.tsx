"use client";

import { Download, ImageUp, PersonStanding, Star, StepForward, Trash2, UserRoundPlus, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { type Asset, mediaUrl } from "@/lib/api";
import { cn, seconds } from "@/lib/cn";
import { useDraft } from "@/lib/draft";
import { useAsset, useContinueFromLastFrame, useDeleteAsset, useLineage, usePatchAsset } from "@/lib/queries";
import { Button } from "@/components/ui/controls";
import { SaveAsElement } from "@/components/characters/SaveAsElement";

const RELATION_LABEL: Record<string, string> = {
  start_image: "Start frame",
  end_image: "End frame",
  image_references: "Reference",
  video_references: "Video reference",
  audio_references: "Audio reference",
  input_video: "Source video",
};

/** Full view of one asset: player, the settings that made it, and what it was made from. */
export function AssetViewer({ asset: initial, onClose }: { asset: Asset | null; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const { data: fresh } = useAsset(initial?.id);
  const asset = fresh ?? initial;
  const { data: lineage } = useLineage(asset?.id);
  const patch = usePatchAsset();
  const remove = useDeleteAsset();
  const addMedia = useDraft((s) => s.addMedia);
  const setDraft = useDraft((s) => s.set);
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const continueShot = useContinueFromLastFrame();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (initial && !el.open) el.showModal();
    if (!initial && el.open) el.close();
  }, [initial]);

  const meta = (asset?.metadata ?? {}) as {
    prompt?: string;
    model_id?: string;
    mode?: string;
    params?: Record<string, unknown>;
    original_filename?: string;
  };

  return (
    <dialog
      ref={ref}
      onClose={(e) => e.target === e.currentTarget && onClose()}
      onClick={(e) => e.target === ref.current && onClose()}
      aria-label={asset?.title ?? "Asset"}
      className="m-auto h-[min(90dvh,860px)] w-[min(96vw,1280px)] max-w-none overflow-hidden rounded-[var(--radius-panel)] border border-line bg-panel p-0 text-chalk backdrop:bg-black/70"
    >
      {asset && (
        <div className="grid h-full grid-cols-1 md:grid-cols-[1fr_340px]">
          <div className="flex min-h-0 items-center justify-center bg-black">
            {asset.kind === "video" ? (
              <video
                key={asset.id}
                src={mediaUrl(asset.proxy_url ?? asset.url)}
                poster={mediaUrl(asset.poster_url)}
                controls
                autoPlay
                playsInline
                className="max-h-full max-w-full"
              />
            ) : asset.kind === "audio" ? (
              <audio src={mediaUrl(asset.url)} controls className="w-4/5" />
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={mediaUrl(asset.url)} alt={asset.title ?? ""} className="max-h-full max-w-full object-contain" />
            )}
          </div>

          <aside className="flex min-h-0 flex-col border-l border-line-soft">
            <div className="flex items-start gap-3 border-b border-line-soft p-5">
              <h2 className="type-title line-clamp-2 flex-1 text-base">{asset.title ?? "Untitled"}</h2>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="grid size-8 shrink-0 place-items-center rounded-full text-muted hover:bg-raised hover:text-chalk"
              >
                <X className="size-4" aria-hidden />
              </button>
            </div>

            <div className="flex-1 space-y-6 overflow-y-auto p-5 text-sm scrollbar-quiet">
              {meta.prompt && (
                <section>
                  <h3 className="mb-1.5 text-xs text-muted">Prompt</h3>
                  <p className="leading-relaxed">{meta.prompt}</p>
                </section>
              )}

              <section>
                <h3 className="mb-1.5 text-xs text-muted">Details</h3>
                <dl className="tabular grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
                  {meta.model_id && (
                    <>
                      <dt className="text-faint">Model</dt>
                      <dd>{meta.model_id}</dd>
                    </>
                  )}
                  {meta.mode && (
                    <>
                      <dt className="text-faint">Mode</dt>
                      <dd>{meta.mode}</dd>
                    </>
                  )}
                  {asset.width && (
                    <>
                      <dt className="text-faint">Size</dt>
                      <dd>
                        {asset.width} × {asset.height}
                      </dd>
                    </>
                  )}
                  {asset.duration_ms ? (
                    <>
                      <dt className="text-faint">Length</dt>
                      <dd>
                        {seconds(asset.duration_ms)}
                        {asset.fps ? ` at ${asset.fps} fps` : ""}
                        {asset.has_audio ? ", with sound" : ""}
                      </dd>
                    </>
                  ) : null}
                  {Object.entries(meta.params ?? {}).map(([k, v]) => (
                    <FragmentRow key={k} label={k.replace(/_/g, " ")} value={String(v)} />
                  ))}
                  {meta.original_filename && <FragmentRow label="File" value={meta.original_filename} />}
                </dl>
              </section>

              {lineage && lineage.parents.length > 0 && (
                <section>
                  <h3 className="mb-1.5 text-xs text-muted">Made from</h3>
                  <ul className="flex flex-wrap gap-2">
                    {lineage.parents.map((e) => (
                      <LineageThumb key={`${e.parent_asset_id}-${e.relation}`} id={e.parent_asset_id} relation={e.relation} />
                    ))}
                  </ul>
                </section>
              )}
              {lineage && lineage.children.length > 0 && (
                <section>
                  <h3 className="mb-1.5 text-xs text-muted">Used in</h3>
                  <ul className="flex flex-wrap gap-2">
                    {lineage.children.map((e) => (
                      <LineageThumb key={`${e.child_asset_id}-${e.relation}`} id={e.child_asset_id} relation={e.relation} />
                    ))}
                  </ul>
                </section>
              )}
            </div>

            <div className="flex flex-wrap gap-2 border-t border-line-soft p-4">
              {asset.kind === "image" && (
                <Button
                  tone="primary"
                  size="sm"
                  onClick={() => {
                    addMedia("video", "start_image", { id: asset.id, kind: "image", thumb: asset.thumb_url, url: asset.url, title: asset.title }, 1);
                    setDraft("video", { mode: "i2v" });
                    onClose();
                    router.push("/create/video");
                  }}
                >
                  <ImageUp className="size-4" aria-hidden />
                  Animate this image
                </Button>
              )}
              {asset.kind === "video" && (
                <Button
                  tone="primary"
                  size="sm"
                  disabled={continueShot.isPending}
                  onClick={() =>
                    void continueShot
                      .run(asset.id)
                      .then(() => {
                        onClose();
                        router.push("/create/video");
                      })
                      .catch((e: unknown) => toast.error(e instanceof Error ? e.message : "Could not grab the frame"))
                  }
                >
                  <StepForward className="size-4" aria-hidden />
                  Continue from the last frame
                </Button>
              )}
              {asset.kind === "video" && (
                <Button
                  size="sm"
                  onClick={() => {
                    patch.mutate({ id: asset.id, add_tags: ["driving"] });
                    addMedia("motion", "driving_video", { id: asset.id, kind: "video", thumb: asset.thumb_url, url: asset.url, title: asset.title }, 1);
                    onClose();
                    router.push("/motion-transfer");
                  }}
                >
                  <PersonStanding className="size-4" aria-hidden />
                  Copy this movement
                </Button>
              )}
              {asset.kind === "image" && (
                <Button size="sm" onClick={() => setSaving(true)}>
                  <UserRoundPlus className="size-4" aria-hidden />
                  Save as character
                </Button>
              )}
              <Button size="sm" onClick={() => patch.mutate({ id: asset.id, favorite: !asset.favorite })}>
                <Star className={cn("size-4", asset.favorite && "fill-tungsten text-tungsten")} aria-hidden />
                {asset.favorite ? "Favorited" : "Favorite"}
              </Button>
              <a
                href={mediaUrl(asset.url)}
                download
                className="inline-flex h-8 items-center gap-2 rounded-[var(--radius-control)] bg-raised px-3 text-xs font-medium hover:bg-line"
              >
                <Download className="size-4" aria-hidden />
                Download
              </a>
              <Button
                size="sm"
                tone="danger"
                className="ml-auto"
                onClick={() =>
                  remove.mutate(asset.id, {
                    onSuccess: () => {
                      toast("Removed from your library");
                      onClose();
                    },
                  })
                }
              >
                <Trash2 className="size-4" aria-hidden />
                Remove
              </Button>
            </div>
          </aside>
          {asset.kind === "image" && (
            <SaveAsElement asset={asset} prompt={meta.prompt} open={saving} onClose={() => setSaving(false)} />
          )}
        </div>
      )}
    </dialog>
  );
}

function FragmentRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-faint">{label.charAt(0).toUpperCase() + label.slice(1)}</dt>
      <dd className="truncate">{value}</dd>
    </>
  );
}

function LineageThumb({ id, relation }: { id: string; relation: string }) {
  const { data } = useAsset(id);
  return (
    <li className="w-20">
      <div className="aspect-square overflow-hidden rounded-[var(--radius-frame)] bg-raised">
        {data && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={mediaUrl(data.thumb_url ?? data.url)} alt="" className="size-full object-cover" />
        )}
      </div>
      <p className="mt-1 truncate text-2xs text-faint">{RELATION_LABEL[relation] ?? relation}</p>
    </li>
  );
}
