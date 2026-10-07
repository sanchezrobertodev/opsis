"use client";

import { useQuery } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";
import { toast } from "sonner";
import { api, ApiError, type Generation, type ModelSummary, unwrap } from "@/lib/api";
import { usd } from "@/lib/cn";
import type { SlotAsset } from "@/lib/draft";
import { useDebounced } from "@/lib/hooks";
import { type CreateBody, useCreateGeneration, useModel, useRegistry } from "@/lib/queries";
import { modeMatches } from "@/lib/studioKinds";
import { Button, Field, Segmented, Select } from "@/components/ui/controls";
import { MediaSlotField } from "./MediaSlots";
import { defaultsFor, isVisible, ParamField } from "./ParamField";

/** The models with a mode doing `op`, current ones first. */
export function useModelsFor(op: string): ModelSummary[] {
  const { data } = useRegistry();
  return useMemo(() => (data?.models ?? []).filter((m) => m.status !== "shutdown" && m.kind !== "virtual"
    && m.modes.some((mode) => modeMatches(mode, [op]))), [data, op]);
}

/**
 * One generation from a short form: pick a model doing `op`, fill its inputs and settings, see the estimate,
 * confirm a spend over the threshold, start. Tools use it where the full Create form would be too much
 * (a 3D model from a picture, a world, a panorama).
 */
export function QuickGenerate({ op, media: initialMedia = {}, promptPlaceholder, submitLabel, onStarted, onCancel }: {
  op: string;
  media?: Record<string, SlotAsset[]>;
  promptPlaceholder?: string;
  submitLabel: string;
  onStarted: (gen: Generation) => void;
  onCancel?: () => void;
}) {
  const models = useModelsFor(op);
  const ids = { model: useId(), prompt: useId() };
  const [modelId, setModelId] = useState<string>();
  const current = models.find((m) => m.id === modelId) ?? models[0];
  const { data: model } = useModel(current?.id);
  const modes = model?.modes.filter((m) => modeMatches(m, [op])) ?? [];
  const [modeId, setModeId] = useState<string>();
  const mode = modes.find((m) => m.id === modeId) ?? modes.find((m) => m.medias.every((s) => !s.required
    || (initialMedia[s.role]?.length ?? 0) > 0)) ?? modes[0];
  const [prompt, setPrompt] = useState("");
  // settings per model, so switching models starts each at its own defaults
  const [values, setValues] = useState<Record<string, Record<string, unknown>>>({});
  const [media, setMedia] = useState<Record<string, SlotAsset[]>>(initialMedia);
  const [confirm, setConfirm] = useState<number | null>(null);

  const params = model && mode ? model.parameters.filter((p) => !p.modes || p.modes.includes(mode.id)) : [];
  const all: Record<string, unknown> = { ...defaultsFor(params), ...(model ? values[model.id] : {}) };
  const send = Object.fromEntries(params.filter((p) => isVisible(p, all)).map((p) => [p.name, all[p.name]]));
  const body: CreateBody | null = model && mode ? {
    model_id: model.id, mode: mode.id, prompt: mode.prompt === "none" ? "" : prompt, params: send,
    media: mode.medias.flatMap((s) => (media[s.role] ?? []).map((a) => ({ role: s.role, asset_id: a.id }))),
  } : null;

  const wanted = body ? { ...body, prompt: body.prompt ? "x" : "" } : null;
  const estimateBody = useDebounced(wanted, 300);
  // an error about inputs the form has since changed doesn't count (the new estimate is on its way)
  const settled = JSON.stringify(estimateBody) === JSON.stringify(wanted);
  const estimate = useQuery({
    queryKey: ["estimate", estimateBody],
    queryFn: () => unwrap(api.POST("/registry/estimate", { body: { route_policy: "cheapest_healthy", ...estimateBody! } })),
    enabled: Boolean(estimateBody), placeholderData: (prev) => prev, retry: false,
  });
  const create = useCreateGeneration();
  const missing = mode?.medias.find((s) => s.required && !(media[s.role]?.length));
  const problem = !settled || estimate.isFetching ? null : estimate.error instanceof ApiError
    ? ((estimate.error.body as { errors?: string[] })?.errors ?? [estimate.error.message]).join(" ")
    : estimate.data && !estimate.data.chosen_route_id ? "No provider can run this right now." : null;
  const blocked = !body || Boolean(missing) || (mode?.prompt === "required" && !prompt.trim()) || Boolean(problem);

  async function go(confirmCost?: number) {
    if (!body) return;
    try {
      const gen = await create.mutateAsync({ ...body, confirm_cost_usd: confirmCost ?? null });
      setConfirm(null);
      onStarted(gen);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConfirm((err.body as { estimate_usd: number }).estimate_usd);
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start it");
    }
  }

  if (!models.length) return <p className="text-sm text-muted">No model can do this yet.</p>;
  if (!model || !mode) return <p className="text-sm text-muted">Loading models…</p>;
  const live = estimate.data?.live_reference?.estimate_usd;

  return (
    <form className="flex flex-col gap-5 text-chalk" onSubmit={(e) => { e.preventDefault(); void go(); }}>
      {models.length > 1 && (
        <Field label="Model" hint={current?.description} htmlFor={ids.model}>
          <Select id={ids.model} value={model.id} onChange={(v) => { setModelId(v); setModeId(undefined); }}
                  options={models.map((m) => ({ value: m.id, label: m.name }))} />
        </Field>
      )}
      {modes.length > 1 && (
        <Segmented label="From" value={mode.id} options={modes.map((m) => m.id)}
                   render={(id) => modes.find((m) => m.id === id)?.label} onChange={setModeId} />
      )}
      {mode.medias.map((slot) => (
        <MediaSlotField key={slot.role} slot={slot} value={media[slot.role] ?? []}
                        onAdd={(a) => setMedia((m) => ({ ...m, [slot.role]: [...(m[slot.role] ?? []), a].slice(-(slot.max ?? 1)) }))}
                        onRemove={(id) => setMedia((m) => ({ ...m, [slot.role]: (m[slot.role] ?? []).filter((a) => a.id !== id) }))} />
      ))}
      {mode.prompt !== "none" && (
        <Field label={mode.prompt === "required" ? "Describe it" : "Describe it (optional)"} htmlFor={ids.prompt}>
          <textarea id={ids.prompt} value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} maxLength={2000}
                    placeholder={promptPlaceholder}
                    className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-faint focus:border-tungsten/60" />
        </Field>
      )}
      {params.filter((p) => p.type !== "seed" && isVisible(p, all)).map((p) => (
        <ParamField key={p.name} param={p} value={all[p.name]} onChange={(v) => setValues((s) => ({ ...s, [model.id]: { ...s[model.id], [p.name]: v } }))} />
      ))}
      <p className="text-xs text-muted" aria-live="polite">
        {missing ? `Add ${missing.label?.toLowerCase() ?? missing.role.replace(/_/g, " ")}.`
          : problem ?? (estimate.data ? (estimate.data.estimate_usd ? `About ${usd(estimate.data.estimate_usd)}` : "Free mock run")
            + (live ? `; live it would cost about ${usd(live)}` : "") : "")}
      </p>
      <div className="flex justify-end gap-2">
        {onCancel && <Button type="button" onClick={onCancel}>Cancel</Button>}
        {confirm !== null ? (
          <Button type="button" tone="primary" onClick={() => void go(confirm)} disabled={create.isPending}>
            Start for {usd(confirm)}
          </Button>
        ) : (
          <Button type="submit" tone="primary" disabled={blocked || create.isPending}>{submitLabel}</Button>
        )}
      </div>
    </form>
  );
}
