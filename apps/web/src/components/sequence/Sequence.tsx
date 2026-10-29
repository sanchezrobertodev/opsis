"use client";

import { ArrowDown, LoaderCircle, Plus, Sparkles, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, api, type MediaSlot, unwrap } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import type { SlotAsset } from "@/lib/draft";
import { useDebounced } from "@/lib/hooks";
import { useModel, useRegistry } from "@/lib/queries";
import { type RunCreate, useEstimateRun, useStartRun } from "@/lib/runs";
import { type Direction, DirectorPanel, mentionSlugs } from "@/components/create/DirectorPanel";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { ModelPicker } from "@/components/create/ModelPicker";
import { Button, Segmented, Select, Slider, Switch } from "@/components/ui/controls";

type Shot = { id: number; prompt: string; duration: number | null };

const START_SLOT: MediaSlot = { role: "start_image", kinds: ["image"], label: "Start frame for shot 1", max: 1, min: 0, required: false, auto: false };
const MAX_SHOTS = 12;
let nextId = 3;

/** Ways to start: short stories whose shots chain well from one frame to the next. */
const IDEAS: { title: string; shots: string[] }[] = [
  {
    title: "Storm at the lighthouse",
    shots: [
      "A lighthouse keeper climbs a spiral staircase at dusk, lantern in hand, wind rattling the windows",
      "At the top he lights the great lamp; its beam sweeps out across a dark, heaving sea",
      "Far out on the water, a small fishing boat turns toward the light and steers for the harbour",
    ],
  },
  {
    title: "Morning in the city",
    shots: [
      "Sunrise on a quiet city street; a café owner rolls up the shutter of her shop",
      "She sets out chairs as the first customers arrive, steam rising from their cups",
      "The street fills with bikes and commuters as the morning rush begins",
    ],
  },
  {
    title: "Product reveal",
    shots: [
      "A matte black wireless headphone rests in shadow on a dark stone plinth",
      "A slow sweep of light glides across its curves, picking out the brushed metal",
      "The headphone turns to face us under a soft spotlight as the background glows warm",
    ],
  },
  {
    title: "Kite on the beach",
    shots: [
      "A child runs along a windy beach pulling a red kite behind her",
      "The kite lifts and climbs into the wind, its long tail snapping",
      "The string snaps and the kite drifts away over the waves as the child watches",
    ],
  },
];

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
  const durationLabels = (durationParam?.type === "enum" ? durationParam.labels ?? {} : {}) as Record<string, string>;
  // an unset length is the model's own default; show it as real seconds
  const fallback = Number(durationParam?.default ?? durations[0] ?? 5);
  const secondsOf = (s: Shot) => s.duration ?? fallback;
  const total = shots.reduce((sum, s) => sum + secondsOf(s), 0);
  const enhanceMode = detail?.modes.find((m) => m.ops.includes("i2v"))?.id;
  const [enhancing, setEnhancing] = useState<number | null>(null);
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

  async function enhance(shot: Shot) {
    if (!model || !enhanceMode) return;
    const before = shot.prompt;
    setEnhancing(shot.id);
    try {
      const r = await unwrap(api.POST("/llm/enhance", {
        body: { model_id: model, mode: enhanceMode, prompt: before, direction: (hasDirection ? direction : null) as never },
      }));
      patch(shot.id, { prompt: r.prompt });
      toast(r.mock ? "Enhanced with the mock writer" : `Enhanced with Claude for ${usd(r.cost_usd)}`, {
        description: [...r.changes, ...r.warnings].join(". ") || undefined,
        action: { label: "Undo", onClick: () => patch(shot.id, { prompt: before }) },
        duration: 10_000,
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not enhance the prompt");
    } finally {
      setEnhancing(null);
    }
  }

  function lengthControl(shot: Shot, n: number) {
    const show = (d: number) => durationLabels[String(d)] ?? `${d} s`;
    const set = (d: number) => patch(shot.id, { duration: d === fallback ? null : d });
    if (durations.length <= 5) {
      return <Segmented label={`Shot ${n} length`} value={secondsOf(shot)} options={durations} onChange={set} render={show} />;
    }
    if (durationParam?.type === "int") {
      return (
        <Slider id={`shot-${shot.id}-length`} value={secondsOf(shot)} min={durations[0]} max={durations[durations.length - 1]}
                onChange={set} format={(v) => `${v} s`} />
      );
    }
    return (
      <Select id={`shot-${shot.id}-length`} value={String(secondsOf(shot))}
              options={durations.map((d) => ({ value: String(d), label: show(d) }))} onChange={(v) => set(Number(v))} />
    );
  }

  const empty = shots.every((s) => !s.prompt.trim());

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-h-0 overflow-y-auto p-6 scrollbar-quiet">
        <div className="mb-5 max-w-2xl">
          <div className="mb-1.5 flex items-baseline justify-between text-2xs text-faint">
            <span>{assemble ? "The joined video" : "The takes"}</span>
            <span className="tabular">
              {shots.length} shots, {Math.round(total)} s
            </span>
          </div>
          <ol className="flex h-9 gap-1" aria-label="Shots by length">
            {shots.map((shot, i) => (
              <li key={shot.id} style={{ flexGrow: secondsOf(shot) }} className="min-w-0 basis-0">
                <a
                  href={`#shot-${shot.id}`}
                  className={cn(
                    "flex h-full items-center gap-1.5 overflow-hidden rounded-[4px] px-2 text-2xs",
                    shot.prompt.trim()
                      ? "bg-raised text-chalk hover:bg-line"
                      : "border border-dashed border-line text-faint hover:text-chalk",
                  )}
                >
                  <span className="type-title">{i + 1}</span>
                  <span className="tabular truncate">{secondsOf(shot)} s</span>
                </a>
              </li>
            ))}
          </ol>
        </div>
        {empty && (
          <div className="mb-5 max-w-2xl rounded-[var(--radius-panel)] border border-dashed border-line p-4">
            <p className="mb-2 text-xs text-muted">Start from an idea, then make it yours:</p>
            <div className="flex flex-wrap gap-2">
              {IDEAS.map((idea) => (
                <button
                  key={idea.title}
                  type="button"
                  onClick={() => setShots(idea.shots.map((prompt) => ({ id: nextId++, prompt, duration: null })))}
                  className="rounded-full border border-line-soft px-3 py-1 text-xs text-chalk hover:border-tungsten/60 hover:bg-raised"
                >
                  {idea.title}
                </button>
              ))}
            </div>
          </div>
        )}
        <ol className="flex max-w-2xl flex-col">
          {shots.map((shot, i) => (
            <li key={shot.id}>
              {i > 0 && (
                <p className="flex items-center gap-2 py-2 pl-3 text-2xs text-faint">
                  <ArrowDown className="size-3" aria-hidden />
                  starts from the last frame of shot {i}
                </p>
              )}
              <div id={`shot-${shot.id}`} className="scroll-mt-6 rounded-[var(--radius-panel)] bg-panel p-4">
                <div className="mb-2 flex items-center gap-3">
                  <span className="type-title text-sm">Shot {i + 1}</span>
                  {shots.length > 1 && (
                    <button
                      type="button"
                      onClick={() => setShots((all) => all.filter((s) => s.id !== shot.id))}
                      aria-label={`Remove shot ${i + 1}`}
                      className="ml-auto grid size-7 place-items-center rounded-full text-muted hover:bg-raised hover:text-chalk"
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
                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
                  {durations.length > 0 && (
                    <div className="flex min-w-0 items-center gap-3">
                      <label htmlFor={`shot-${shot.id}-length`} className="text-2xs text-faint">
                        Length
                      </label>
                      <div className="w-56 max-w-full">{lengthControl(shot, i + 1)}</div>
                    </div>
                  )}
                  {enhanceMode && (
                    <Button size="sm" tone="ghost" className="ml-auto" onClick={() => void enhance(shot)}
                            disabled={!shot.prompt.trim() || enhancing !== null}
                            title="Rewrite this shot in the words the model follows best">
                      {enhancing === shot.id
                        ? <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
                        : <Sparkles className="size-3.5" aria-hidden />}
                      {enhancing === shot.id ? "Enhancing" : "Enhance"}
                    </Button>
                  )}
                </div>
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
