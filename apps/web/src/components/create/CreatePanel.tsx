"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { api, ApiError, type ModelDetail, type ModelSummary, unwrap } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { useDraft } from "@/lib/draft";
import { type CreateBody, useCreateGeneration, useModel } from "@/lib/queries";
import { Button, Dialog, Segmented } from "@/components/ui/controls";
import { MediaSlotField } from "./MediaSlots";
import { ModelPicker } from "./ModelPicker";
import { defaultsFor, isVisible, ParamField } from "./ParamField";

type Studio = "video" | "image";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function CreatePanel({ studio, models }: { studio: Studio; models: ModelSummary[] }) {
  const draft = useDraft((s) => s[studio]);
  const setDraft = useDraft((s) => s.set);
  const setParam = useDraft((s) => s.setParam);
  const addMedia = useDraft((s) => s.addMedia);
  const removeMedia = useDraft((s) => s.removeMedia);

  const preferred = studio === "video" ? "veo-3.1" : "nano-banana-2";
  const modelId =
    draft.modelId && models.some((m) => m.id === draft.modelId)
      ? draft.modelId
      : (models.find((m) => m.id === preferred) ?? models[0])?.id;
  const { data: model } = useModel(modelId);

  const mode = model?.modes.find((m) => m.id === draft.mode) ?? model?.modes[0];
  const params = useMemo(
    () => (model && mode ? model.parameters.filter((p) => !p.modes || p.modes.includes(mode.id)) : []),
    [model, mode],
  );
  const values = useMemo(
    () => ({ ...defaultsFor(params), ...(modelId ? draft.params[modelId] : {}) }),
    [params, draft.params, modelId],
  );
  // Only send params the current mode accepts.
  const sendParams = useMemo(
    () => Object.fromEntries(params.filter((p) => isVisible(p, values)).map((p) => [p.name, values[p.name]])),
    [params, values],
  );
  const media = useMemo(
    () =>
      (mode?.medias ?? []).flatMap((slot) =>
        (draft.media[slot.role] ?? []).map((a) => ({ role: slot.role, asset_id: a.id })),
      ),
    [mode, draft.media],
  );

  const body: CreateBody | null =
    model && mode
      ? {
          model_id: model.id,
          mode: mode.id,
          prompt: draft.prompt,
          negative_prompt: model.prompt.supports_negative ? draft.negativePrompt || null : null,
          params: sendParams,
          media,
        }
      : null;

  const estimateBody = useDebounced(body ? { ...body, prompt: body.prompt ? "x" : "" } : null, 300);
  const estimate = useQuery({
    queryKey: ["estimate", estimateBody],
    queryFn: () =>
      unwrap(api.POST("/registry/estimate", { body: { route_policy: "cheapest_healthy", ...estimateBody! } })),
    enabled: Boolean(estimateBody),
    placeholderData: (prev) => prev,
    retry: false,
  });
  const estimateErrors =
    estimate.error instanceof ApiError
      ? ((estimate.error.body as { errors?: string[] })?.errors ?? [estimate.error.message])
      : [];

  const create = useCreateGeneration();
  const [confirm, setConfirm] = useState<{ estimate: number; route: string } | null>(null);
  const [showMore, setShowMore] = useState(false);

  const promptMissing = mode?.prompt === "required" && !draft.prompt.trim();
  const blockers = [...(promptMissing ? ["Describe the shot to generate it."] : []), ...estimateErrors];

  async function submit(confirmCost?: number) {
    if (!body) return;
    const takes = Math.max(1, Math.min(4, draft.takes));
    const group = takes > 1 ? crypto.randomUUID() : null;
    for (let i = 0; i < takes; i++) {
      try {
        await create.mutateAsync({ ...body, group_id: group, confirm_cost_usd: confirmCost ?? null });
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          const b = err.body as { estimate_usd: number; route_id: string };
          setConfirm({ estimate: b.estimate_usd * takes, route: b.route_id });
          return;
        }
        toast.error(err instanceof Error ? err.message : "Could not start the generation");
        return;
      }
    }
  }

  if (!model || !mode) {
    return <div className="p-5 text-sm text-muted">Loading models…</div>;
  }

  const basic = params.filter((p) => p.group !== "advanced" && isVisible(p, values));
  const advanced = params.filter((p) => p.group === "advanced" && isVisible(p, values));
  const chosen = estimate.data?.candidates.find((c) => c.route_id === estimate.data?.chosen_route_id);
  const liveRef = estimate.data?.live_reference;

  return (
    <form
      className="flex h-full flex-col"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="flex-1 space-y-6 overflow-y-auto p-5 scrollbar-quiet">
        <ModelPicker
          models={models}
          value={model.id}
          onChange={(id) => setDraft(studio, { modelId: id, mode: undefined })}
        />

        {model.modes.length > 1 && (
          <Segmented
            label="Mode"
            value={mode.id}
            options={model.modes.map((m) => m.id)}
            render={(id) => model.modes.find((m) => m.id === id)?.label}
            onChange={(id) => setDraft(studio, { mode: id })}
          />
        )}

        {mode.medias.map((slot) => (
          <MediaSlotField
            key={slot.role}
            slot={slot}
            value={draft.media[slot.role] ?? []}
            onAdd={(a) => addMedia(studio, slot.role, a, slot.max ?? 1)}
            onRemove={(id) => removeMedia(studio, slot.role, id)}
          />
        ))}

        {mode.prompt !== "none" && (
          <PromptBox
            model={model}
            value={draft.prompt}
            onChange={(prompt) => setDraft(studio, { prompt })}
            onSubmit={() => void submit()}
          />
        )}

        {basic.map((p) => (
          <ParamField key={p.name} param={p} value={values[p.name]} onChange={(v) => setParam(studio, model.id, p.name, v)} />
        ))}

        {(advanced.length > 0 || model.prompt.supports_negative) && (
          <div>
            <button
              type="button"
              onClick={() => setShowMore((v) => !v)}
              aria-expanded={showMore}
              className="flex items-center gap-1.5 text-xs text-muted hover:text-chalk"
            >
              <ChevronDown className={cn("size-3.5 transition-transform", showMore && "rotate-180")} aria-hidden />
              More settings
            </button>
            {showMore && (
              <div className="mt-4 space-y-6">
                {model.prompt.supports_negative && (
                  <label className="flex flex-col gap-2 text-xs text-muted">
                    Avoid
                    <textarea
                      rows={2}
                      value={draft.negativePrompt}
                      onChange={(e) => setDraft(studio, { negativePrompt: e.target.value })}
                      placeholder="What should not appear, e.g. text overlays, blur"
                      className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60"
                    />
                  </label>
                )}
                {advanced.map((p) => (
                  <ParamField key={p.name} param={p} value={values[p.name]} onChange={(v) => setParam(studio, model.id, p.name, v)} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="space-y-3 border-t border-line-soft bg-panel/60 p-5">
        <div className="flex items-center justify-between gap-3">
          <span className="text-xs text-muted">Takes</span>
          <Segmented
            label="Number of takes"
            value={draft.takes}
            options={[1, 2, 3, 4]}
            onChange={(n) => setDraft(studio, { takes: n })}
          />
        </div>

        <CostLine
          chosenUsd={estimate.data?.estimate_usd ?? null}
          isMock={chosen?.is_mock ?? false}
          liveUsd={liveRef?.estimate_usd ?? null}
          liveProvider={liveRef?.provider_name ?? liveRef?.provider}
          takes={draft.takes}
        />

        {blockers.length > 0 && (
          <ul className="space-y-1 text-xs text-tungsten" aria-live="polite">
            {blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        )}

        <Button
          type="submit"
          tone="primary"
          size="lg"
          className="w-full"
          disabled={blockers.length > 0 || create.isPending}
        >
          {create.isPending ? "Starting…" : draft.takes > 1 ? `Generate ${draft.takes} takes` : "Generate"}
        </Button>
      </div>

      <Dialog
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        title="Confirm this run"
        footer={
          <>
            <Button type="button" tone="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button
              type="button"
              tone="primary"
              onClick={() => {
                const c = confirm;
                setConfirm(null);
                if (c) void submit(c.estimate / Math.max(1, draft.takes));
              }}
            >
              Generate for {usd(confirm?.estimate)}
            </Button>
          </>
        }
      >
        This run is estimated at <strong className="text-chalk">{usd(confirm?.estimate)}</strong> on{" "}
        {confirm?.route}, which is above your confirmation threshold. You can change the threshold in
        Settings.
      </Dialog>
    </form>
  );
}

function PromptBox({
  model,
  value,
  onChange,
  onSubmit,
}: {
  model: ModelDetail;
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
}) {
  const max = model.prompt.max_chars;
  const near = value.length > max * 0.9;
  return (
    <label className="flex flex-col gap-2">
      <span className="flex items-baseline justify-between text-xs text-muted">
        Prompt
        <span className={cn("tabular text-2xs", near ? "text-tungsten" : "text-faint")}>
          {value.length} / {max}
        </span>
      </span>
      <textarea
        value={value}
        maxLength={max}
        rows={5}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            onSubmit();
          }
        }}
        placeholder="Describe the shot: subject, action, setting, camera, light and sound."
        className="min-h-28 resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2.5 text-sm leading-relaxed text-chalk outline-none placeholder:text-faint focus:border-tungsten/60"
      />
    </label>
  );
}

function CostLine({
  chosenUsd,
  isMock,
  liveUsd,
  liveProvider,
  takes,
}: {
  chosenUsd: number | null;
  isMock: boolean;
  liveUsd: number | null;
  liveProvider?: string;
  takes: number;
}) {
  const n = Math.max(1, takes);
  if (isMock) {
    return (
      <p className="tabular text-xs text-muted">
        Free mock run.
        {liveUsd != null && (
          <span className="text-faint">
            {" "}
            Live it would cost about {usd(liveUsd * n)}
            {liveProvider ? ` on ${liveProvider}` : ""}.
          </span>
        )}
      </p>
    );
  }
  return (
    <p className="tabular text-xs text-muted">
      Estimated <span className="text-chalk">{usd(chosenUsd != null ? chosenUsd * n : null)}</span>
      {n > 1 && <span className="text-faint"> for {n} takes</span>}
    </p>
  );
}
