"use client";

import { ArrowDown, Plus, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, type MediaSlot } from "@/lib/api";
import { usd } from "@/lib/cn";
import type { SlotAsset } from "@/lib/draft";
import { useDebounced } from "@/lib/hooks";
import { useModel, useRegistry } from "@/lib/queries";
import { type RunCreate, useEstimateRun, useStartRun } from "@/lib/runs";
import { type Direction, DirectorPanel, mentionSlugs } from "@/components/create/DirectorPanel";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { ModelPicker } from "@/components/create/ModelPicker";
import { Button, Switch } from "@/components/ui/controls";

type Shot = { id: number; prompt: string; duration: number | null };

const START_SLOT: MediaSlot = { role: "start_image", kinds: ["image"], label: "Start frame for shot 1", max: 1, min: 0, required: false, auto: false };
const MAX_SHOTS = 12;
let nextId = 3;

/** Build a run of shots where each one starts from the last frame of the one before. */
export function Sequence() {
  const router = useRouter();
  const { data: registry } = useRegistry();
  const models = (registry?.models ?? []).filter(
    (m) => m.output_type === "video" && m.modes.some((mode) => mode.ops.includes("i2v")),
  );
  const [modelId, setModelId] = useState<string | undefined>();
  const model = modelId ?? models.find((m) => m.id === "kling-3.0")?.id ?? models[0]?.id;
  const { data: detail } = useModel(model);
  const [shots, setShots] = useState<Shot[]>([
    { id: 1, prompt: "", duration: null },
    { id: 2, prompt: "", duration: null },
  ]);
  const [start, setStart] = useState<SlotAsset[]>([]);
  const [direction, setDirection] = useState<Direction>({ camera_moves: [] } as unknown as Direction);
  const [assemble, setAssemble] = useState(true);
  const [confirm, setConfirm] = useState<number | null>(null);
  const startRun = useStartRun();

  const durationParam = detail?.parameters.find((p) => p.name === "duration");
  const durations: number[] =
    durationParam?.type === "enum"
      ? durationParam.options.map(Number)
      : durationParam?.type === "int"
        ? Array.from({ length: durationParam.max - durationParam.min + 1 }, (_, i) => durationParam.min + i)
        : [];
  const hasDirection = (direction.camera_moves?.length ?? 0) > 0 ||
    Object.entries(direction).some(([k, v]) => k !== "camera_moves" && Boolean(v));

  const ready = model && shots.every((s) => s.prompt.trim());
  const body: RunCreate | null = ready
    ? {
        recipe: "sequence",
        input: {
          model_id: model,
          shots: shots.map((s) => ({ prompt: s.prompt.trim(), duration: s.duration })),
          start_image_asset_id: start[0]?.id ?? null,
          direction: hasDirection ? direction : null,
          assemble,
        },
      }
    : null;
  const estimate = useEstimateRun(useDebounced(body ? { ...body, input: { ...body.input, shots: shots.map((s) => ({ prompt: "x", duration: s.duration })) } } : null, 300));
  const problem = estimate.error instanceof ApiError
    ? ((estimate.error.body as { errors?: string[] })?.errors ?? [estimate.error.message]).join(" ")
    : null;

  async function go(confirmCost?: number) {
    if (!body) return;
    try {
      const run = await startRun.mutateAsync({ ...body, confirm_cost_usd: confirmCost ?? null });
      router.push(`/runs/${run.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConfirm((err.body as { estimate_usd: number }).estimate_usd);
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start the sequence");
    }
  }

  const patch = (id: number, p: Partial<Shot>) => setShots((all) => all.map((s) => (s.id === id ? { ...s, ...p } : s)));

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-h-0 overflow-y-auto p-6 scrollbar-quiet">
        <ol className="flex max-w-2xl flex-col">
          {shots.map((shot, i) => (
            <li key={shot.id}>
              {i > 0 && (
                <p className="flex items-center gap-2 py-2 pl-3 text-2xs text-faint">
                  <ArrowDown className="size-3" aria-hidden />
                  starts from the last frame of shot {i}
                </p>
              )}
              <div className="rounded-[var(--radius-panel)] bg-panel p-4">
                <div className="mb-2 flex items-center gap-3">
                  <span className="type-title text-sm">Shot {i + 1}</span>
                  {durations.length > 0 && (
                    <label className="ml-auto flex items-center gap-2 text-2xs text-faint">
                      Length
                      <select
                        value={shot.duration ?? ""}
                        onChange={(e) => patch(shot.id, { duration: e.target.value ? Number(e.target.value) : null })}
                        className="h-7 rounded-[4px] border border-line-soft bg-bay px-1.5 text-xs text-chalk"
                      >
                        <option value="">Default</option>
                        {durations.map((d) => (
                          <option key={d} value={d}>
                            {d} s
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {shots.length > 1 && (
                    <button
                      type="button"
                      onClick={() => setShots((all) => all.filter((s) => s.id !== shot.id))}
                      aria-label={`Remove shot ${i + 1}`}
                      className="grid size-7 place-items-center rounded-full text-muted hover:bg-raised hover:text-chalk"
                    >
                      <X className="size-3.5" aria-hidden />
                    </button>
                  )}
                </div>
                <textarea
                  value={shot.prompt}
                  onChange={(e) => patch(shot.id, { prompt: e.target.value })}
                  rows={3}
                  aria-label={`Shot ${i + 1} prompt`}
                  placeholder={i === 0 ? "How it opens: who, where, what happens." : "What happens next."}
                  className="w-full resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm leading-relaxed text-chalk outline-none placeholder:text-faint focus:border-tungsten/60"
                />
              </div>
            </li>
          ))}
        </ol>
        {shots.length < MAX_SHOTS && (
          <Button
            tone="ghost"
            size="sm"
            className="mt-3"
            onClick={() => setShots((all) => [...all, { id: nextId++, prompt: "", duration: null }])}
          >
            <Plus className="size-4" aria-hidden />
            Add a shot
          </Button>
        )}
      </div>

      <aside className="flex min-h-0 flex-col gap-5 overflow-y-auto border-t border-line-soft p-5 scrollbar-quiet lg:border-t-0 lg:border-l">
        <ModelPicker models={models} value={model} onChange={setModelId} />
        <MediaSlotField
          slot={START_SLOT}
          value={start}
          onAdd={(a) => setStart([a])}
          onRemove={() => setStart([])}
        />
        <DirectorPanel direction={direction} onChange={setDirection} stills={false}
                       cast={mentionSlugs(shots.map((s) => s.prompt).join(" "))} />
        <label className="flex items-center justify-between gap-4 text-sm">
          <span>
            Join into one video
            <span className="block text-2xs text-faint">Each shot also stays in your library on its own.</span>
          </span>
          <Switch label="Join into one video" checked={assemble} onChange={setAssemble} />
        </label>
        <div className="mt-auto space-y-3">
          <p className="text-xs leading-relaxed text-muted">
            {problem ? (
              <span className="text-fault">{problem}</span>
            ) : estimate.data ? (
              <>
                {shots.length} takes
                {estimate.data.estimate_usd === 0 ? ", free on mock or local routes." : `, about ${usd(estimate.data.estimate_usd)}.`}
                {estimate.data.live_estimate_usd != null && estimate.data.estimate_usd === 0 &&
                  ` Live it would cost about ${usd(estimate.data.live_estimate_usd)}.`}
              </>
            ) : (
              "Describe every shot to see the cost."
            )}
          </p>
          {confirm != null ? (
            <div className="flex gap-2">
              <Button tone="ghost" onClick={() => setConfirm(null)}>
                Cancel
              </Button>
              <Button tone="primary" className="flex-1" onClick={() => void go(confirm)} disabled={startRun.isPending}>
                Start for {usd(confirm)}
              </Button>
            </div>
          ) : (
            <Button tone="primary" size="lg" className="w-full" onClick={() => void go()}
                    disabled={!body || Boolean(problem) || startRun.isPending}>
              Shoot the sequence
            </Button>
          )}
        </div>
      </aside>
    </div>
  );
}
