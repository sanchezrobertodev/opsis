"use client";

import type { Schemas } from "@studio/api-client";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { api, ApiError, type ModelSummary, unwrap } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { useDraft } from "@/lib/draft";
import { useDebounced } from "@/lib/hooks";
import { type CreateBody, useCreateGeneration, useModel } from "@/lib/queries";
import { KINDS, modeMatches, type StudioKind } from "@/lib/studioKinds";
import { Button, Dialog, Segmented } from "@/components/ui/controls";
import { CompareDialog } from "./CompareDialog";
import { CostPanel } from "./CostPanel";
import { KeyframeHint } from "./KeyframeHint";
import { type Direction, DirectorPanel, mentionSlugs } from "./DirectorPanel";
import { MediaSlotField } from "./MediaSlots";
import { ModelPicker } from "./ModelPicker";
import { MotionPathsField } from "./MotionPathsField";
import { defaultsFor, isVisible, ParamField } from "./ParamField";
import { PromptBox } from "./PromptBox";

const PLACEHOLDER: Partial<Record<StudioKind, string>> = {
  edit: "Describe the change: make it snow, swap the jacket for a red raincoat, relight it as golden hour.",
  motion: "Optional: describe the character, outfit or setting to keep consistent.",
};

/**
 * The shared Create form. `models` is already narrowed to the studio; `ops` narrows which of a model's modes
 * show (Create video shows generation modes, Edit video only the chosen edit).
 */
export function CreatePanel({ studio, models, ops = KINDS[studio].ops }: {
  studio: StudioKind;
  models: ModelSummary[];
  ops?: string[];
}) {
  const draft = useDraft((s) => s[studio]);
  const setDraft = useDraft((s) => s.set);
  const setParam = useDraft((s) => s.setParam);
  const addMedia = useDraft((s) => s.addMedia);
  const removeMedia = useDraft((s) => s.removeMedia);

  const preferred = KINDS[studio].preferred;
  const modelId =
    draft.modelId && models.some((m) => m.id === draft.modelId)
      ? draft.modelId
      : (models.find((m) => m.id === preferred) ?? models[0])?.id;
  const { data: model } = useModel(modelId);

  const modes = model ? (model.modes.filter((m) => modeMatches(m, ops)).length
    ? model.modes.filter((m) => modeMatches(m, ops))
    : model.modes) : [];
  const mode = modes.find((m) => m.id === draft.mode) ?? modes[0];
  const directed = studio === "video" || studio === "image";
  const params = useMemo(() => {
    // a virtual model's director params (era, lens...) are set in the director panel when it's shown
    const viaDirector = new Set(directed ? model?.virtual?.director ?? [] : []);
    const own = model && mode ? model.parameters.filter((p) => (!p.modes || p.modes.includes(mode.id)) && !viaDirector.has(p.name)) : [];
    // a multi-shot take is as long as its shots together
    return own.some((p) => p.type === "shots") ? own.filter((p) => p.name !== "duration") : own;
  }, [model, mode, directed]);
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
      (mode?.medias ?? []).filter((slot) => !slot.auto).flatMap((slot) =>
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
          direction: directed && hasDirection(draft.direction) ? draft.direction : null,
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

  // The exact prompt the model will get, for the "see the prompt" preview. Debounced, free, no generation.
  const [showCompiled, setShowCompiled] = useState(false);
  const compileBody = useDebounced(
    showCompiled && body
      ? { model_id: body.model_id, mode: body.mode, media: body.media, prompt: body.prompt, direction: body.direction ?? null }
      : null,
    400,
  );
  const compiled = useQuery({
    queryKey: ["compile", compileBody],
    queryFn: () => unwrap(api.POST("/director/compile", { body: compileBody as never })),
    enabled: Boolean(compileBody),
    placeholderData: (prev) => prev,
  });

  const create = useCreateGeneration();
  const [comparing, setComparing] = useState(false);
  const [confirm, setConfirm] = useState<{ estimate: number; route: string } | null>(null);
  const [showMore, setShowMore] = useState(false);

  const promptMissing = mode?.prompt === "required" && !draft.prompt.trim();
  // No route can run it right now (e.g. the local engine is off): say why, using the most relevant reason.
  const noRoute =
    estimate.data && !estimate.data.chosen_route_id
      ? (estimate.data.candidates.find((c) => c.provider_kind === "local")?.reason ??
        estimate.data.candidates.find((c) => !c.is_mock)?.reason ??
        "No provider can run this right now.")
      : null;
  const blockers = [
    ...(promptMissing ? [studio === "edit" ? "Describe the change you want." : "Describe the shot to generate it."] : []),
    ...estimateErrors,
    ...(noRoute ? [noRoute] : []),
  ];

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

  const drawn = params.filter((p) => p.type === "paths" && isVisible(p, values));
  const basic = params.filter((p) => p.type !== "paths" && p.group !== "advanced" && isVisible(p, values));
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

        {modes.length > 1 && (
          <Segmented
            label="Mode"
            value={mode.id}
            options={modes.map((m) => m.id)}
            render={(id) => modes.find((m) => m.id === id)?.label}
            onChange={(id) => setDraft(studio, { mode: id })}
          />
        )}

        {mode.medias.filter((slot) => !slot.auto).map((slot) => (
          <MediaSlotField
            key={slot.role}
            slot={slot}
            value={draft.media[slot.role] ?? []}
            onAdd={(a) => addMedia(studio, slot.role, a, slot.max ?? 1)}
            onRemove={(id) => removeMedia(studio, slot.role, id)}
          />
        ))}

        {/* Drawn on the start frame, so it sits right under it rather than with the other settings. */}
        {drawn.map((p) => (
          <MotionPathsField
            key={p.name}
            param={p}
            value={values[p.name]}
            onChange={(v) => setParam(studio, model.id, p.name, v)}
            frame={(draft.media.start_image ?? [])[0]}
            aspect={String(values.aspect_ratio ?? model.aspect_ratios[0] ?? "16:9")}
          />
        ))}

        {mode.prompt !== "none" && (
          <PromptBox
            model={model}
            mode={mode.id}
            direction={body?.direction ?? null}
            value={draft.prompt}
            onChange={(prompt) => setDraft(studio, { prompt })}
            onSubmit={() => void submit()}
            placeholder={PLACEHOLDER[studio]}
          />
        )}

        {studio === "video" && mode.prompt !== "none" && (
          <KeyframeHint
            model={model}
            prompt={draft.prompt}
            params={sendParams}
            direction={body?.direction ?? null}
            hasStartFrame={(draft.media.start_image ?? []).length > 0 && Boolean(mode.medias.find((m) => m.role === "start_image"))}
          />
        )}

        {directed && (
          <DirectorPanel
            direction={draft.direction as Direction}
            onChange={(direction) => setDraft(studio, { direction: direction as Record<string, unknown> })}
            stills={model.output_type === "image"}
            cast={mentionSlugs(draft.prompt)}
          />
        )}

        {mode.prompt !== "none" && (
          <div>
            <button
              type="button"
              onClick={() => setShowCompiled((v) => !v)}
              aria-expanded={showCompiled}
              className="flex items-center gap-1.5 text-xs text-muted hover:text-chalk"
            >
              <ChevronDown className={cn("size-3.5 transition-transform", showCompiled && "rotate-180")} aria-hidden />
              See the prompt the model receives
            </button>
            {showCompiled && compiled.data && (
              <div className="mt-2 rounded-[var(--radius-control)] border border-line-soft bg-bay p-3">
                <p className="whitespace-pre-wrap text-xs leading-relaxed text-chalk">
                  {compiled.data.prompt || <span className="text-faint">Type a prompt first.</span>}
                </p>
                <p className="tabular mt-2 text-2xs text-faint">
                  {compiled.data.prompt.length} of {compiled.data.max_chars} characters, written for {model.name}
                </p>
                {compiled.data.warnings.map((w) => (
                  <p key={w} className="mt-1 text-2xs text-tungsten">
                    {w}
                  </p>
                ))}
              </div>
            )}
          </div>
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
          isLocal={chosen?.provider_kind === "local"}
          etaSeconds={chosen?.eta_seconds ?? null}
          liveUsd={liveRef?.estimate_usd ?? null}
          liveProvider={liveRef?.provider_name ?? liveRef?.provider}
          takes={draft.takes}
          chosen={chosen}
          pending={estimate.isFetching}
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
        <button
          type="button"
          onClick={() => setComparing(true)}
          disabled={blockers.length > 0 || !body}
          className="w-full text-center text-xs text-muted hover:text-chalk disabled:cursor-not-allowed disabled:opacity-45"
        >
          Compare with other models
        </button>
      </div>
      {body && comparing && (
        <CompareDialog open={comparing} onClose={() => setComparing(false)} body={body} ops={mode.ops}
                       models={models.filter((m) => m.modes.some((x) => modeMatches(x, mode.ops)))} />
      )}

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

function hasDirection(d: Record<string, unknown> | undefined) {
  if (!d) return false;
  return Object.entries(d).some(([k, v]) => (k === "camera_moves" ? Array.isArray(v) && v.length > 0 : Boolean(v)));
}

export function duration(seconds: number) {
  if (seconds < 90) return `${Math.max(5, Math.round(seconds / 5) * 5)} s`;
  return `${Math.round(seconds / 60)} min`;
}

function CostLine({
  chosenUsd,
  isMock,
  isLocal,
  etaSeconds,
  liveUsd,
  liveProvider,
  takes,
  chosen,
  pending,
}: {
  chosen?: Schemas["CandidateOut"];
  pending: boolean;
  chosenUsd: number | null;
  isMock: boolean;
  isLocal: boolean;
  etaSeconds: number | null;
  liveUsd: number | null;
  liveProvider?: string;
  takes: number;
}) {
  const n = Math.max(1, takes);
  if (!chosen) {
    return (
      <p className="text-xs text-faint" aria-live="polite">
        {pending ? "Working out the cost…" : "The cost shows here once the required inputs are in."}
      </p>
    );
  }
  if (isLocal) {
    return (
      <p className="tabular text-xs text-muted">
        Free on this computer.
        {etaSeconds != null && (
          <span className="text-faint">
            {" "}
            About {duration(etaSeconds * n)}
            {n > 1 ? " for all takes, one at a time" : ""}.
          </span>
        )}
      </p>
    );
  }
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
  return <CostPanel cost={chosenUsd} takes={n} chosen={chosen} />;
}
