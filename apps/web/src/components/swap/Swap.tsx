"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, type MediaSlot } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import type { SlotAsset } from "@/lib/draft";
import { useDebounced } from "@/lib/hooks";
import { useAsset, useGeneration, useRegistry } from "@/lib/queries";
import { type RunCreate, useEstimateRun, useStartRun } from "@/lib/runs";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { Button, Field, Segmented, Slider, Switch } from "@/components/ui/controls";
import { MaskEditor, type Selection } from "./MaskEditor";

const CLIP: MediaSlot = { role: "input_video", kinds: ["video"], label: "Clip", min: 1, max: 1, required: true, auto: false };
const REFS: MediaSlot = { role: "image_references", kinds: ["image"], label: "What it becomes (optional images)",
                          min: 0, max: 4, required: false, auto: false };
const MAX_MODELS = 3;

/** Select one object in a clip, then replace or remove it while everything else stays as filmed. */
export function Swap() {
  const router = useRouter();
  const { data: registry } = useRegistry();
  const startRun = useStartRun();
  // opened from a Cinema shot: its take is preloaded and the result becomes a new take of that shot
  const search = useSearchParams();
  const [picked, setClip] = useState<SlotAsset[]>([]);
  const [cleared, setCleared] = useState(false);
  const { data: given } = useAsset(!picked.length && !cleared ? search.get("video") ?? undefined : undefined);
  const clip: SlotAsset[] = picked.length ? picked
    : given ? [{ id: given.id, kind: given.kind, thumb: given.thumb_url, url: given.url, title: given.title }] : [];
  const film = search.get("project") && search.get("shot") && clip[0]?.id === search.get("video")
    ? { project: search.get("project")!, shot: search.get("shot")! } : null;
  const { data: video } = useAsset(clip[0]?.id);
  const [selection, setSelection] = useState<Selection | null>(null);
  const { data: segment } = useGeneration(selection?.jobId);
  const maskId = segment?.status === "succeeded" ? segment.assets?.[0]?.id : undefined;
  // the clicks that made the mask: the tracker starts from them as well as the mask
  const clicks = (segment?.request as { params?: { points?: number[][] } } | undefined)?.params?.points ?? [];
  const [action, setAction] = useState<"swap" | "remove">("swap");
  const [prompt, setPrompt] = useState("");
  const [refs, setRefs] = useState<SlotAsset[]>([]);
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [keepBackground, setKeepBackground] = useState(true);
  const [reviewMask, setReviewMask] = useState(false);
  const [feather, setFeather] = useState(4);
  const [confirm, setConfirm] = useState<number | null>(null);

  // models that change one object in a clip by instruction
  const editors = (registry?.models ?? []).filter((m) => m.output_type === "video" && m.modes.some(
    (x) => x.ops.includes("object_replace") && x.ops.includes("instruction_edit")));
  const models = chosen ?? editors.slice(0, 1).map((m) => m.id);

  const ready = video && maskId && selection && models.length > 0 && (action === "remove" || prompt.trim());
  const body: RunCreate | null = ready
    ? {
        recipe: "object_swap",
        input: {
          video_asset_id: video.id, mask_asset_id: maskId, frame_index: selection.frameIndex, action,
          points: clicks,
          prompt: prompt.trim(), reference_asset_ids: action === "swap" ? refs.map((r) => r.id) : [],
          models: models.map((model_id) => ({ model_id })), keep_background: keepBackground, feather,
          review_mask: reviewMask, ...(film ? { project_id: film.project, shot_key: film.shot } : {}),
        },
      }
    : null;
  const estimate = useEstimateRun(useDebounced(body, 300));
  const problem = estimate.error instanceof ApiError
    ? ((estimate.error.body as { errors?: string[] })?.errors ?? [estimate.error.message]).join(" ")
    : null;

  async function go(confirmCost?: number) {
    if (!body) return;
    try {
      const run = await startRun.mutateAsync({ ...body, confirm_cost_usd: confirmCost ?? null });
      if (film) {
        toast.success("Started. The changed take lands on the shot when it's done.");
        router.push(`/cinema/${film.project}`);
      } else {
        router.push(`/runs/${run.id}`);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConfirm((err.body as { estimate_usd: number }).estimate_usd);
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start the swap");
    }
  }

  function toggle(id: string) {
    const next = models.includes(id) ? models.filter((m) => m !== id) : [...models, id].slice(-MAX_MODELS);
    setChosen(next);
  }

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-h-0 overflow-y-auto p-6 scrollbar-quiet">
        <div className="flex max-w-3xl flex-col gap-5">
          {film && (
            <p className="text-xs text-muted">
              Changing one object in a take from your film. The result becomes a new take of that shot.{" "}
              <Link href={`/cinema/${film.project}`} className="text-chalk underline-offset-2 hover:underline">Back to the film</Link>
            </p>
          )}
          <MediaSlotField slot={CLIP} value={clip} onAdd={(a) => { setClip([a]); setSelection(null); }}
                          onRemove={() => { setClip([]); setCleared(true); setSelection(null); }} />
          {video ? (
            <MaskEditor key={video.id} video={video} onChange={setSelection} />
          ) : (
            <p className="text-sm text-muted">
              Add a clip. Edit models take 15 to 30 seconds depending on the model; the cost line says if it&rsquo;s
              too long. You&rsquo;ll click the object on one frame and it&rsquo;s followed through the clip.
            </p>
          )}
        </div>
      </div>

      <aside className="flex min-h-0 flex-col gap-5 overflow-y-auto border-t border-line-soft p-5 scrollbar-quiet lg:border-t-0 lg:border-l">
        <Field label="Do what">
          <Segmented label="Do what" value={action} options={["swap", "remove"] as const}
                     render={(v) => (v === "swap" ? "Replace it" : "Remove it")} onChange={setAction} />
        </Field>
        <Field label={action === "swap" ? "Replace it with" : "What it is (optional)"} htmlFor="swap-prompt">
          <textarea id="swap-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3}
                    placeholder={action === "swap" ? "Replace the mug with a small potted cactus" : "the mug on the desk"}
                    className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-faint focus:border-tungsten/60" />
        </Field>
        {action === "swap" && (
          <MediaSlotField slot={REFS} value={refs} onAdd={(a) => setRefs((r) => [...r, a])}
                          onRemove={(id) => setRefs((r) => r.filter((x) => x.id !== id))} />
        )}
        <Field label={`Models (up to ${MAX_MODELS}, compared side by side)`}>
          <ul className="flex flex-col gap-1">
            {editors.map((m) => (
              <li key={m.id}>
                <label className={cn("flex cursor-pointer items-center gap-3 rounded-[var(--radius-control)] px-2 py-1.5 text-sm",
                                     models.includes(m.id) ? "bg-raised text-chalk" : "text-muted hover:text-chalk")}>
                  <input type="checkbox" checked={models.includes(m.id)} onChange={() => toggle(m.id)}
                         className="accent-tungsten" />
                  <span className="flex-1">{m.name}</span>
                  {m.from_price_usd != null && <span className="tabular text-2xs text-faint">from {usd(m.from_price_usd)}</span>}
                </label>
              </li>
            ))}
          </ul>
        </Field>
        <label className="flex items-center justify-between gap-4 text-sm">
          <span>
            Keep the rest exactly as filmed
            <span className="block text-2xs text-faint">Only pixels inside the selection can change.</span>
          </span>
          <Switch label="Keep the rest exactly as filmed" checked={keepBackground} onChange={setKeepBackground} />
        </label>
        {keepBackground && (
          <Field label="Edge softness">
            <Slider value={feather} min={0} max={12} step={1} onChange={setFeather} format={(v) => `${v} px`} />
          </Field>
        )}
        <label className="flex items-center justify-between gap-4 text-sm">
          <span>
            Check the tracking first
            <span className="block text-2xs text-faint">Stop after the selection is followed through the clip.</span>
          </span>
          <Switch label="Check the tracking first" checked={reviewMask} onChange={setReviewMask} />
        </label>

        <div className="mt-auto space-y-3">
          <p className="text-xs leading-relaxed text-muted">
            {problem ? (
              <span className="text-fault">{problem}</span>
            ) : !video ? "Add a clip to start."
              : !maskId ? "Select the object on a frame."
                : action === "swap" && !prompt.trim() ? "Say what it should become."
                  : estimate.data ? (
                    <>
                      {estimate.data.generations} takes
                      {estimate.data.estimate_usd === 0 ? ", free on mock routes." : `, about ${usd(estimate.data.estimate_usd)}.`}
                      {estimate.data.live_estimate_usd != null && estimate.data.estimate_usd === 0 &&
                        ` Live it would cost about ${usd(estimate.data.live_estimate_usd)}.`}
                    </>
                  ) : "Estimating…"}
          </p>
          {confirm != null ? (
            <div className="flex gap-2">
              <Button tone="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
              <Button tone="primary" className="flex-1" onClick={() => void go(confirm)} disabled={startRun.isPending}>
                Start for {usd(confirm)}
              </Button>
            </div>
          ) : (
            <Button tone="primary" size="lg" className="w-full" onClick={() => void go()}
                    disabled={!body || Boolean(problem) || startRun.isPending}>
              {action === "swap" ? "Swap it" : "Remove it"}
            </Button>
          )}
        </div>
      </aside>
    </div>
  );
}
