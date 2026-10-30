"use client";

import { useQuery } from "@tanstack/react-query";
import { Crosshair, LoaderCircle, Undo2 } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { api, ApiError, type Asset, isActive, mediaUrl, unwrap } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { useCreateGeneration, useGeneration, useGrabFrame, useRegistry } from "@/lib/queries";
import { Button, Segmented } from "@/components/ui/controls";

const SEGMENT_MODEL = "sam-3-image";

type Point = [number, number, 0 | 1];
/** The segmentation job for the object, and the frame it was drawn on. Its mask is the job's output. */
export type Selection = { jobId: string; frameIndex: number };

/** Pick a frame of the clip, click the object (Alt-click what isn't it), and let SAM 3 find its outline. */
export function MaskEditor({ video, onChange }: { video: Asset; onChange: (s: Selection | null) => void }) {
  const player = useRef<HTMLVideoElement>(null);
  const grab = useGrabFrame();
  const segment = useCreateGeneration();
  const [frame, setFrame] = useState<{ asset: Asset; at: number } | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [marking, setMarking] = useState<"this" | "not">("this");
  const [name, setName] = useState("");
  const [jobId, setJobId] = useState<string>();
  const { data: job } = useGeneration(jobId);
  const mask = job?.status === "succeeded" ? job.assets?.[0] : undefined;
  const running = Boolean(jobId) && (!job || isActive(job.status));
  const fps = video.fps ?? 24;
  const modelName = useRegistry().data?.models.find((m) => m.id === SEGMENT_MODEL)?.name ?? SEGMENT_MODEL;
  // one selection's price: the clicks don't change it, so it's asked once per frame
  const priceBody = frame ? { model_id: SEGMENT_MODEL, mode: "segment", prompt: "", params: { points: [[0.5, 0.5, 1]] },
                              media: [{ role: "input_image", asset_id: frame.asset.id }] } : null;
  const { data: estimate } = useQuery({
    queryKey: ["estimate", priceBody],
    queryFn: () => unwrap(api.POST("/registry/estimate", { body: { route_policy: "cheapest_healthy", ...priceBody! } })),
    enabled: Boolean(priceBody), retry: false,
  });
  const live = estimate?.live_reference?.estimate_usd;
  const price = !estimate ? "" : estimate.candidates.find((c) => c.route_id === estimate.chosen_route_id)?.is_mock
    ? `free as a mock run${live ? ` (about ${usd(live)} when live)` : ""}`
    : estimate.estimate_usd ? `about ${usd(estimate.estimate_usd)}` : "free";

  function reset(next: Point[]) {
    setPoints(next);
    setJobId(undefined);
    onChange(null);
  }

  const fpsFrame = (at: number) => Math.round(at * fps);

  async function pickFrame() {
    const at = Math.round((player.current?.currentTime ?? 0) * 100) / 100;
    try {
      const asset = await grab.mutateAsync({ id: video.id, at });
      setFrame({ asset: asset as Asset, at });
      reset([]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't grab that frame");
    }
  }

  function place(e: React.MouseEvent<SVGSVGElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width));
    const y = Math.min(1, Math.max(0, (e.clientY - box.top) / box.height));
    const label = e.altKey || marking === "not" ? 0 : 1;
    reset([...points, [Math.round(x * 1e4) / 1e4, Math.round(y * 1e4) / 1e4, label]]);
  }

  async function find(confirmCost = estimate?.estimate_usd) {
    if (!frame) return;
    try {
      const gen = await segment.mutateAsync({
        model_id: SEGMENT_MODEL, mode: "segment", prompt: name.trim(), params: { points },
        media: [{ role: "input_image", asset_id: frame.asset.id }], confirm_cost_usd: confirmCost ?? null,
      });
      setJobId(gen.id);
      onChange({ jobId: gen.id, frameIndex: fpsFrame(frame.at) });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const now = (err.body as { estimate_usd: number }).estimate_usd;
        toast(`A selection now costs about ${usd(now)}.`, { action: { label: "Select it", onClick: () => void find(now) } });
        return;
      }
      toast.error(err instanceof Error ? err.message : "Couldn't start the selection");
    }
  }

  const positives = points.filter((p) => p[2] === 1).length;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <video ref={player} src={mediaUrl(video.proxy_url ?? video.url)} poster={mediaUrl(video.poster_url)} controls
               playsInline className="max-h-[42vh] w-full rounded-[var(--radius-frame)] bg-black object-contain" />
        <div className="flex items-center gap-3">
          <Button size="sm" className="shrink-0 whitespace-nowrap" onClick={() => void pickFrame()} disabled={grab.isPending}>
            <Crosshair className="size-4" aria-hidden />
            {frame ? "Use this frame instead" : "Select on this frame"}
          </Button>
          <span className="text-2xs text-faint">
            Pause where the object is clearly visible. The selection is followed through the whole clip.
          </span>
        </div>
      </div>

      {frame && (
        <div className="flex flex-col gap-3 rounded-[var(--radius-panel)] bg-panel p-4">
          <div className="flex flex-wrap items-center gap-3">
            <Segmented label="Clicks mark" value={marking} options={["this", "not"] as const}
                       render={(v) => (v === "this" ? "The object" : "Not the object")} onChange={setMarking} />
            <span className="text-2xs text-faint">Alt-click also marks what isn&rsquo;t part of it.</span>
            <div className="ml-auto flex gap-1">
              <Button size="sm" tone="ghost" onClick={() => reset(points.slice(0, -1))} disabled={!points.length}
                      aria-label="Undo the last click">
                <Undo2 className="size-4" aria-hidden />
              </Button>
              <Button size="sm" tone="ghost" onClick={() => reset([])} disabled={!points.length}>Clear</Button>
            </div>
          </div>
          <div className="relative w-full overflow-hidden rounded-[var(--radius-frame)] bg-black"
               style={{ aspectRatio: `${frame.asset.width ?? 16} / ${frame.asset.height ?? 9}` }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={mediaUrl(frame.asset.url)} alt="The frame to select on" className="absolute inset-0 size-full" />
            {mask && (
              <div aria-hidden className="absolute inset-0 bg-tungsten/55"
                   style={{ maskImage: `url(${mediaUrl(mask.url)})`, maskMode: "luminance", maskSize: "100% 100%",
                            WebkitMaskImage: `url(${mediaUrl(mask.url)})`, WebkitMaskSize: "100% 100%" }} />
            )}
            <svg role="img" aria-label="Clicks on the frame" viewBox="0 0 100 100" preserveAspectRatio="none"
                 onClick={place} className="absolute inset-0 size-full cursor-crosshair">
              {points.map(([x, y, label], i) => (
                <circle key={i} cx={x * 100} cy={y * 100} r={1.4} vectorEffect="non-scaling-stroke"
                        className={label ? "fill-tungsten stroke-bay" : "fill-bay stroke-fault"} strokeWidth={2} />
              ))}
            </svg>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="Name it (optional), e.g. the red mug" aria-label="Object name"
                   className="h-9 min-w-0 flex-1 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm outline-none placeholder:text-faint focus:border-tungsten/60" />
            <Button size="sm" tone="primary" onClick={() => void find()} disabled={!positives || running || segment.isPending}>
              {running && <LoaderCircle className="size-4 animate-spin" aria-hidden />}
              {mask ? "Select again" : "Find the object"}
            </Button>
          </div>
          <p className={cn("text-2xs", job?.status === "failed" ? "text-fault" : "text-faint")}>
            {job?.status === "failed" ? (job.error_message ?? "The selection failed. Try other clicks.")
              : mask ? "Highlighted is what will change. Add clicks and select again to refine it."
                : positives ? "Click more of the object if it's large, then find it."
                  : "Click the object you want to change."}
          </p>
          <p className="text-2xs text-faint">
            Each selection is a new job on {modelName}{price && `, ${price}`}.
          </p>
        </div>
      )}
    </div>
  );
}
