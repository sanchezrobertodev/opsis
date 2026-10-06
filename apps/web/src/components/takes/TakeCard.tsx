"use client";

import { Download, ImageUp, RotateCcw, Star, StepForward, UserRoundPlus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { type Asset, type Generation, isActive, mediaUrl } from "@/lib/api";
import { cn, seconds, timecode, usd } from "@/lib/cn";
import { useDraft } from "@/lib/draft";
import {
  useCancelGeneration,
  useContinueFromLastFrame,
  useCreateGeneration,
  usePatchAsset,
  useResolveUnknown,
} from "@/lib/queries";
import { SaveAsElement } from "@/components/characters/SaveAsElement";

const STATUS_TEXT: Record<string, string> = {
  queued: "Waiting for a worker",
  submitting: "Sending to the provider",
  in_progress: "Rendering",
  ingesting: "Saving to your library",
  blocked_budget: "Paused by your spending limit",
  unknown: "Outcome unknown",
};

function aspectOf(gen: Generation, asset?: Asset): string {
  if (asset?.width && asset.height) return `${asset.width} / ${asset.height}`;
  const ar = (gen.request.params as Record<string, unknown> | undefined)?.aspect_ratio;
  if (typeof ar === "string" && ar.includes(":")) return ar.replace(":", " / ");
  return "16 / 9";
}

/** Current time, refreshed every second while `active`; keeps render pure (no Date.now() in render). */
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

export function TakeCard({
  gen,
  takeNumber,
  modelName,
  onOpen,
}: {
  gen: Generation;
  takeNumber: number;
  modelName: string;
  onOpen?: (asset: Asset) => void;
}) {
  const active = isActive(gen.status);
  const now = useNow(active);
  const assets = gen.assets ?? [];
  const prompt = String(gen.request.prompt ?? "");

  return (
    <article
      className="flex flex-col gap-2"
      aria-label={`Take ${takeNumber}, ${modelName}, ${gen.status.replace("_", " ")}`}
    >
      {gen.status === "succeeded" && assets.length > 0 ? (
        <div className={cn("grid gap-1", assets.length > 1 && "grid-cols-2")}>
          {assets.map((a) => (
            <Frame key={a.id} asset={a} aspect={aspectOf(gen, a)} onOpen={onOpen} />
          ))}
        </div>
      ) : active ? (
        <Slate gen={gen} takeNumber={takeNumber} modelName={modelName} aspect={aspectOf(gen)} now={now} />
      ) : (
        <Fault gen={gen} aspect={aspectOf(gen)} />
      )}
      <TakeFooter gen={gen} takeNumber={takeNumber} modelName={modelName} prompt={prompt} assets={assets} />
    </article>
  );
}

/** In-progress take, drawn as a clapperboard slate. */
function Slate({
  gen,
  takeNumber,
  modelName,
  aspect,
  now,
}: {
  gen: Generation;
  takeNumber: number;
  modelName: string;
  aspect: string;
  now: number;
}) {
  const cancel = useCancelGeneration();
  const resolve = useResolveUnknown();
  const pct = Math.round((gen.progress ?? 0) * 100);
  const routing = gen.routing as { chosen?: string; candidates?: { route_id: string; eta_seconds?: number | null; provider_kind?: string | null }[] };
  const chosen = routing.candidates?.find((c) => c.route_id === routing.chosen);
  const local = chosen?.provider_kind === "local";
  const remaining =
    chosen?.eta_seconds && gen.started_at
      ? chosen.eta_seconds - (now - new Date(gen.started_at).getTime()) / 1000
      : null;
  const waiting = gen.status_reason === "provider_queue" || gen.status === "queued";
  const statusText =
    gen.status === "in_progress" && !waiting
      ? local
        ? `Rendering on your GPU${remaining != null && remaining > 5 ? `, about ${remainingText(remaining)} left` : ""}`
        : `${STATUS_TEXT.in_progress}${pct > 0 ? ` ${pct}%` : ""}`
      : gen.status_reason === "provider_concurrency"
        ? "Waiting for a free provider slot"
        : waiting && gen.status === "in_progress"
          ? local
            ? "Waiting for your GPU"
            : "In the provider's queue"
          : (STATUS_TEXT[gen.status] ?? gen.status);

  return (
    <div
      className="relative flex flex-col justify-between overflow-hidden rounded-[var(--radius-frame)] bg-panel p-4"
      style={{ aspectRatio: aspect, minHeight: 180 }}
    >
      {/* clapper stripes */}
      <div aria-hidden className="absolute inset-x-0 top-0 h-2.5 bg-[repeating-linear-gradient(115deg,var(--color-chalk)_0_14px,var(--color-bay)_14px_28px)] opacity-80" />
      <div className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        <span className="text-faint">Take</span>
        <span className="type-title tabular text-chalk">{String(takeNumber).padStart(2, "0")}</span>
        <span className="text-faint">Model</span>
        <span className="type-title truncate text-chalk">{modelName}</span>
        <span className="text-faint">Mode</span>
        <span className="truncate text-muted">{gen.mode}</span>
      </div>

      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-xs text-muted" aria-live="polite">
            {statusText}
          </p>
          <p className="type-title tabular text-3xl leading-none text-chalk">{timecode(gen.created_at, now)}</p>
        </div>
        {gen.status === "unknown" ? (
          <button
            type="button"
            onClick={() => resolve.mutate(gen.id)}
            className="shrink-0 rounded-[var(--radius-control)] bg-raised px-3 py-1.5 text-xs hover:bg-line"
            title="The provider may still be working on this. Check its dashboard first; marking it failed releases the reserved budget."
          >
            Mark failed
          </button>
        ) : (
          gen.status !== "ingesting" && (
            <button
              type="button"
              onClick={() =>
                cancel.mutate(gen.id, { onError: (e) => toast.error(e instanceof Error ? e.message : "Cancel failed") })
              }
              aria-label="Cancel this take"
              className="grid size-8 shrink-0 place-items-center rounded-full bg-raised text-muted hover:text-chalk"
            >
              <X className="size-4" aria-hidden />
            </button>
          )
        )}
      </div>

      <div aria-hidden className="absolute inset-x-0 bottom-0 h-0.5 bg-line-soft">
        <div
          className={cn("h-full bg-tungsten transition-[width] duration-700", waiting && "animate-pulse")}
          style={{ width: `${Math.max(waiting ? 4 : pct, 2)}%` }}
        />
      </div>
    </div>
  );
}

function remainingText(seconds: number) {
  return seconds < 90 ? `${Math.max(5, Math.round(seconds / 5) * 5)} s` : `${Math.round(seconds / 60)} min`;
}

function Fault({ gen, aspect }: { gen: Generation; aspect: string }) {
  const canceled = gen.status === "canceled";
  return (
    <div
      className="flex flex-col justify-end gap-1 rounded-[var(--radius-frame)] border border-line-soft p-4"
      style={{ aspectRatio: aspect, minHeight: 140 }}
    >
      <p className={cn("text-sm", canceled ? "text-muted" : "text-fault")}>
        {canceled ? "Canceled" : gen.error_code === "moderation" ? "Blocked by the provider's content rules" : "Failed"}
      </p>
      {!canceled && gen.error_message && <p className="line-clamp-3 text-xs text-muted">{gen.error_message}</p>}
    </div>
  );
}

function Frame({ asset, aspect, onOpen }: { asset: Asset; aspect: string; onOpen?: (a: Asset) => void }) {
  const src = mediaUrl(asset.proxy_url ?? asset.url);
  if (asset.kind === "video") {
    return (
      <video
        src={src}
        poster={mediaUrl(asset.poster_url)}
        controls
        playsInline
        preload="metadata"
        className="w-full rounded-[var(--radius-frame)] bg-black"
        style={{ aspectRatio: aspect }}
      />
    );
  }
  return (
    <button
      type="button"
      onClick={() => onOpen?.(asset)}
      className="block overflow-hidden rounded-[var(--radius-frame)] bg-raised"
      style={{ aspectRatio: aspect }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={asset.title ?? "Generated image"} className="size-full object-cover" />
    </button>
  );
}

function TakeFooter({
  gen,
  takeNumber,
  modelName,
  prompt,
  assets,
}: {
  gen: Generation;
  takeNumber: number;
  modelName: string;
  prompt: string;
  assets: Asset[];
}) {
  const studio = gen.output_type === "image" ? "image" : "video";
  const setDraft = useDraft((s) => s.set);
  const addMedia = useDraft((s) => s.addMedia);
  const create = useCreateGeneration();
  const patch = usePatchAsset();
  const [saving, setSaving] = useState(false);
  const continueShot = useContinueFromLastFrame();
  const first = assets[0];
  const cost = gen.actual_cost_usd ?? gen.estimated_cost_usd;
  const duration = first?.duration_ms ? seconds(first.duration_ms) : null;

  function retry() {
    const r = gen.request as {
      model_id: string;
      mode: string;
      prompt: string;
      negative_prompt?: string | null;
      params: Record<string, unknown>;
      media: { role: string; asset_id: string }[];
    };
    // a fresh seed per take, so "another take" actually varies
    const params = Object.fromEntries(Object.entries(r.params ?? {}).filter(([k]) => k !== "seed"));
    create.mutate(
      { model_id: r.model_id, mode: r.mode, prompt: r.prompt, negative_prompt: r.negative_prompt, params,
        media: (r.media ?? []).map((m) => ({ role: m.role, asset_id: m.asset_id })) },
      { onError: (e) => toast.error(e instanceof Error ? e.message : "Could not start the take") },
    );
  }

  return (
    <div className="flex items-start gap-3 px-0.5">
      <div className="min-w-0 flex-1">
        <p className="line-clamp-2 text-xs leading-relaxed text-chalk" title={prompt}>
          {prompt || <span className="text-faint">No prompt</span>}
        </p>
        <p className="tabular mt-1 text-2xs text-faint">
          Take {takeNumber}, {modelName}
          {duration && `, ${duration}`}
          {cost != null && gen.status !== "canceled" && `, ${usd(cost, { free: "free" })}`}
        </p>
      </div>
      <div className="flex shrink-0 gap-0.5">
        {gen.status === "succeeded" && first && (
          <>
            <IconButton
              label={first.favorite ? "Remove from favorites" : "Add to favorites"}
              onClick={() => patch.mutate({ id: first.id, favorite: !first.favorite })}
            >
              <Star className={cn("size-4", first.favorite && "fill-tungsten text-tungsten")} aria-hidden />
            </IconButton>
            {first.kind === "image" && (
              <IconButton
                label="Use as start frame for a video"
                onClick={() => {
                  addMedia("video", "start_image", { id: first.id, kind: "image", thumb: first.thumb_url, url: first.url, title: first.title }, 1);
                  setDraft("video", { mode: "i2v" });
                  toast("Added as the start frame in Create video");
                }}
              >
                <ImageUp className="size-4" aria-hidden />
              </IconButton>
            )}
            {first.kind === "video" && (
              <IconButton
                label="Continue from the last frame"
                onClick={() =>
                  void continueShot
                    .run(first.id)
                    .then(() => toast("Last frame set as the start frame. Describe what happens next."))
                    .catch((e: unknown) => toast.error(e instanceof Error ? e.message : "Could not grab the frame"))
                }
              >
                <StepForward className="size-4" aria-hidden />
              </IconButton>
            )}
            {first.kind === "image" && (
              <IconButton label="Save as character" onClick={() => setSaving(true)}>
                <UserRoundPlus className="size-4" aria-hidden />
              </IconButton>
            )}
            <a
              href={mediaUrl(first.url)}
              download
              aria-label="Download"
              title="Download"
              className="grid size-8 place-items-center rounded-full text-muted hover:bg-raised hover:text-chalk"
            >
              <Download className="size-4" aria-hidden />
            </a>
          </>
        )}
        {!isActive(gen.status) && (
          <IconButton label="Generate another take with the same settings" onClick={retry}>
            <RotateCcw className="size-4" aria-hidden />
          </IconButton>
        )}
        <IconButton
          label="Reuse this prompt"
          onClick={() => setDraft(studio, { prompt, modelId: gen.model_id, mode: gen.mode })}
        >
          <span className="text-2xs">Aa</span>
        </IconButton>
      </div>
      {first?.kind === "image" && (
        <SaveAsElement asset={first} prompt={prompt} open={saving} onClose={() => setSaving(false)} />
      )}
    </div>
  );
}

function IconButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="grid size-8 place-items-center rounded-full text-muted hover:bg-raised hover:text-chalk"
    >
      {children}
    </button>
  );
}
