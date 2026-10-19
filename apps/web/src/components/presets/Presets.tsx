"use client";

import { BookOpen, Package, Palette, Scissors, Smartphone, Sparkles, UserRound } from "lucide-react";
import type { Schemas } from "@studio/api-client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { type SlotAsset, useDraft } from "@/lib/draft";
import {
  CATEGORY_LABEL,
  type Preset,
  type PresetCategory,
  type Template,
  usePresets,
  useRenderPreset,
  useRenderTemplate,
  useTemplates,
} from "@/lib/presets";
import { useCreateGeneration } from "@/lib/queries";
import { useElements } from "@/lib/elements";
import { useEstimateRun, useStartRun } from "@/lib/runs";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { Button, Dialog, Segmented, Select } from "@/components/ui/controls";

const FILTERS = ["all", "guided", "camera", "effects", "look", "character", "product", "social", "edit"] as const;
type Filter = (typeof FILTERS)[number];

const CATEGORY_ICON: Record<PresetCategory, typeof Sparkles> = {
  camera: Sparkles,
  effects: Sparkles,
  look: Palette,
  character: UserRound,
  product: Package,
  social: Smartphone,
  edit: Scissors,
};

function aspectOf(p: Preset): [number, number] {
  const [w, h] = p.aspect_ratio.split(":").map(Number);
  return [w || 16, h || 9];
}

function priceText(p: Preset) {
  if (p.local) return "Free on this computer";
  if (p.live_price_usd != null) return `from ${usd(p.live_price_usd)}`;
  return "";
}

export function Presets() {
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<Preset | null>(null);
  const [openTemplate, setOpenTemplate] = useState<Template | null>(null);
  const { data, isLoading } = usePresets();
  const { data: templates } = useTemplates();
  const items = (data ?? []).filter((p) => filter === "all" || p.category === filter);
  const guided = filter === "all" || filter === "guided" ? (templates ?? []) : [];

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-quiet">
      <div className="mb-6 max-w-3xl">
        <Segmented
          label="Show"
          value={filter}
          options={FILTERS}
          render={(f) => (f === "all" ? "Everything" : f === "guided" ? "Stories" : CATEGORY_LABEL[f])}
          onChange={setFilter}
        />
      </div>

      {isLoading ? null : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-x-5 gap-y-7">
          {guided.map((t) => (
            <li key={`t-${t.id}`}>
              <TemplateCard template={t} onOpen={() => setOpenTemplate(t)} />
            </li>
          ))}
          {items.map((p) => (
            <li key={p.id}>
              <PresetCard preset={p} onOpen={() => setOpen(p)} />
            </li>
          ))}
        </ul>
      )}

      <Dialog open={open !== null} onClose={() => setOpen(null)} title={open?.name ?? ""} wide>
        {open && <PresetForm key={open.id} preset={open} onDone={() => setOpen(null)} />}
      </Dialog>
      <Dialog open={openTemplate !== null} onClose={() => setOpenTemplate(null)} title={openTemplate?.name ?? ""} wide>
        {openTemplate && <TemplateForm key={openTemplate.id} template={openTemplate} />}
      </Dialog>
    </div>
  );
}

/** A card's picture: its cover (`public/covers/<id>.webp`, an example of what it makes) when there is one, else the
 * drawn stand-in it wraps. */
function Cover({ id, children }: { id: string; children: React.ReactNode }) {
  const [missing, setMissing] = useState(false);
  if (missing) return <>{children}</>;
  return (
    <div className="h-40 overflow-hidden rounded-[var(--radius-frame)] bg-panel">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`/covers/${id}.webp`}
        alt=""
        loading="lazy"
        onError={() => setMissing(true)}
        className="size-full object-cover transition-transform group-hover:scale-[1.02]"
      />
    </div>
  );
}

/** A viewfinder drawn at the shot's own aspect ratio, with the camera move playing on hover. */
function Viewfinder({ preset }: { preset: Preset }) {
  const [w, h] = aspectOf(preset);
  const Icon = CATEGORY_ICON[preset.category as PresetCategory] ?? Sparkles;
  const move = preset.moves[0];
  return (
    <div className="relative grid h-40 place-items-center rounded-[var(--radius-frame)] bg-panel transition-colors group-hover:bg-raised">
      <div
        className="relative grid place-items-center overflow-hidden"
        style={{ aspectRatio: `${w} / ${h}`, width: `min(85%, ${Math.round(((h > w ? 124 : 92) * w) / h)}px)` }}
      >
        {/* corner marks */}
        <span aria-hidden className="absolute top-0 left-0 size-3 border-t border-l border-faint" />
        <span aria-hidden className="absolute top-0 right-0 size-3 border-t border-r border-faint" />
        <span aria-hidden className="absolute bottom-0 left-0 size-3 border-b border-l border-faint" />
        <span aria-hidden className="absolute right-0 bottom-0 size-3 border-r border-b border-faint" />
        {move ? (
          <span aria-hidden className={cn("block size-2.5 rounded-full bg-tungsten", `glyph-${move.category}`)} />
        ) : (
          <Icon aria-hidden className="size-5 text-tungsten" />
        )}
      </div>
      <span className="tabular absolute bottom-2 left-3 text-2xs text-faint">
        {[move?.label, ...preset.look].filter(Boolean).slice(0, 2).join(", ") || preset.mode_label}
      </span>
      <span className="tabular absolute right-3 bottom-2 text-2xs text-faint">{preset.aspect_ratio}</span>
    </div>
  );
}

function PresetCard({ preset, onOpen }: { preset: Preset; onOpen: () => void }) {
  const needs = preset.inputs.map((i) => i.label.toLowerCase());
  return (
    <button type="button" onClick={onOpen} className="group block w-full text-left">
      <Cover id={preset.id}>
        <Viewfinder preset={preset} />
      </Cover>
      <p className="type-title mt-3 text-[15px]">{preset.name}</p>
      <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted">{preset.summary}</p>
      <p className="tabular mt-1.5 text-2xs text-faint">
        {preset.model_name}
        {priceText(preset) && <>, <span className={cn(preset.local && "text-done")}>{priceText(preset)}</span></>}
        {needs.length > 0 && <>. Needs {needs.join(" and ")}</>}
      </p>
    </button>
  );
}

function PresetForm({ preset, onDone }: { preset: Preset; onDone: () => void }) {
  const router = useRouter();
  const draft = useDraft();
  const renderPreset = useRenderPreset();
  const create = useCreateGeneration();
  const [values, setValues] = useState<Record<string, string>>(
    () => Object.fromEntries(preset.fields.map((f) => [f.key, f.default ?? ""])),
  );
  const [media, setMedia] = useState<Record<string, SlotAsset[]>>({});
  const [confirm, setConfirm] = useState<number | null>(null);
  const studio = preset.output_type === "image" ? "image" : "video";
  const busy = renderPreset.isPending || create.isPending;

  const missing = [
    ...preset.fields.filter((f) => f.required && !values[f.key]?.trim()).map((f) => f.label),
    ...preset.inputs.filter((i) => !media[i.role]?.length).map((i) => i.label),
  ];

  async function rendered() {
    return renderPreset.mutateAsync({
      id: preset.id,
      fields: values,
      media: Object.entries(media).flatMap(([role, list]) => list.map((a) => ({ role, asset_id: a.id }))),
    });
  }

  async function generate(confirmCost?: number) {
    try {
      const req = await rendered();
      await create.mutateAsync({ ...req, direction: req.direction ?? null, confirm_cost_usd: confirmCost ?? null });
      toast(`${preset.name} started`);
      onDone();
      router.push(`/create/${studio}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConfirm((err.body as { estimate_usd: number }).estimate_usd);
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start it");
    }
  }

  async function openInCreate() {
    try {
      const req = await rendered();
      draft.set(studio, {
        modelId: req.model_id,
        mode: req.mode,
        prompt: req.prompt,
        params: { ...draft[studio].params, [req.model_id]: req.params },
        media: Object.fromEntries(preset.inputs.map((i) => [i.role, media[i.role] ?? []])),
        direction: (req.direction ?? {}) as Record<string, unknown>,
      });
      onDone();
      router.push(`/create/${studio}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not open it");
    }
  }

  const field =
    "h-10 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60";

  return (
    <div className="grid gap-6 md:grid-cols-[1.3fr_1fr]">
      <div className="space-y-4">
        <p className="leading-relaxed text-chalk">{preset.summary}</p>
        {preset.inputs.map((i) => (
          <MediaSlotField
            key={i.role}
            slot={{ ...i.slot, label: i.label }}
            value={media[i.role] ?? []}
            onAdd={(a) =>
              setMedia((m) => ({ ...m, [i.role]: [...(m[i.role] ?? []).filter((x) => x.id !== a.id), a].slice(-(i.slot.max ?? 1)) }))
            }
            onRemove={(id) => setMedia((m) => ({ ...m, [i.role]: (m[i.role] ?? []).filter((x) => x.id !== id) }))}
          />
        ))}
        {preset.fields.map((f) => (
          <label key={f.key} className="flex flex-col gap-1.5 text-xs text-muted">
            <span>
              {f.label}
              {!f.required && <span className="text-faint"> (optional)</span>}
            </span>
            <input
              value={values[f.key] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              maxLength={f.max_chars}
              placeholder={f.placeholder ? `e.g. ${f.placeholder}` : undefined}
              className={field}
            />
          </label>
        ))}
      </div>

      <aside className="space-y-4 rounded-[var(--radius-control)] bg-bay p-4 text-xs">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
          <dt className="text-faint">Model</dt>
          <dd className="text-chalk">
            {preset.model_name}, {preset.mode_label.toLowerCase()}
          </dd>
          {preset.moves.length > 0 && (
            <>
              <dt className="text-faint">Camera</dt>
              <dd className="text-chalk">{preset.moves.map((m) => m.label).join(", then ")}</dd>
            </>
          )}
          {preset.look.length > 0 && (
            <>
              <dt className="text-faint">Look</dt>
              <dd className="text-chalk">{preset.look.join(", ")}</dd>
            </>
          )}
          <dt className="text-faint">Cost</dt>
          <dd className={cn(preset.local ? "text-done" : "text-chalk")}>
            {preset.local ? "Free on this computer" : preset.live_price_usd != null ? `About ${usd(preset.live_price_usd)} live` : "Depends on your clip"}
          </dd>
        </dl>
        <p className="leading-relaxed text-faint">
          It becomes an ordinary take you can retry, tweak or animate further. Open it in Create to change anything first.
        </p>
      </aside>

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line-soft pt-4 md:col-span-2">
        {missing.length > 0 && <p className="mr-auto text-xs text-faint">Add {missing.join(", ").toLowerCase()} to start.</p>}
        {confirm != null ? (
          <>
            <p className="mr-auto text-xs text-chalk">This run is estimated at {usd(confirm)}.</p>
            <Button type="button" tone="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button type="button" tone="primary" onClick={() => void generate(confirm)} disabled={busy}>
              Generate for {usd(confirm)}
            </Button>
          </>
        ) : (
          <>
            <Button type="button" tone="ghost" onClick={() => void openInCreate()} disabled={busy}>
              Open in Create
            </Button>
            <Button type="button" tone="primary" onClick={() => void generate()} disabled={busy || missing.length > 0}>
              Generate
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function TemplateCard({ template, onOpen }: { template: Template; onOpen: () => void }) {
  const needs = template.inputs.filter((i) => !i.alternative).map((i) => i.label.toLowerCase());
  const either = template.inputs.filter((i) => i.alternative).map((i) => i.label.toLowerCase().replace(/^or /, ""));
  return (
    <button type="button" onClick={onOpen} className="group block w-full text-left">
      <Cover id={template.id}>
        <div className="grid h-40 place-items-center rounded-[var(--radius-frame)] bg-panel transition-colors group-hover:bg-raised">
          <BookOpen aria-hidden className="size-6 text-tungsten" />
        </div>
      </Cover>
      <p className="type-title mt-3 text-[15px]">{template.name}</p>
      <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted">{template.summary}</p>
      <p className="tabular mt-1.5 text-2xs text-faint">
        Guided, {template.recipe_name.toLowerCase()}
        {needs.length > 0 && <>. Needs {needs.join(" and ")}</>}
        {either.length > 0 && <>. Needs {either.join(" or ")}</>}
      </p>
    </button>
  );
}

const ROLE_FOR_KIND = { image: "start_image", video: "input_video", audio: "input_audio" } as const;

/** A guided template: answer its blanks, add its media, and it starts a run you follow on the Runs page. */
function TemplateForm({ template }: { template: Template }) {
  const router = useRouter();
  const renderTemplate = useRenderTemplate();
  const start = useStartRun();
  const [values, setValues] = useState<Record<string, string>>(
    () => Object.fromEntries(template.fields.map((f) => [f.key, f.default ?? ""])),
  );
  const [media, setMedia] = useState<Record<string, SlotAsset[]>>({});
  const [picked, setPicked] = useState<Record<string, string>>({}); // saved characters' handles, by input key
  const [confirm, setConfirm] = useState<number | null>(null);
  const [run, setRun] = useState<Schemas["RunCreate"] | null>(null);
  const estimate = useEstimateRun(run);
  const { data: characters } = useElements("character");
  const busy = renderTemplate.isPending || start.isPending;
  // an estimate or a confirmation was for the old answers and media
  const changed = () => {
    setRun(null);
    setConfirm(null);
  };
  const either = template.inputs.filter((i) => i.alternative);
  /** Set one input. Alternative inputs (a picture or a saved character), so choosing one clears the rest. */
  function choose(key: string, value: SlotAsset[] | string) {
    const others = template.inputs.find((i) => i.key === key)?.alternative
      ? template.inputs.filter((i) => i.alternative && i.key !== key).map((i) => i.key)
      : [];
    setMedia((m) => ({ ...m, ...Object.fromEntries(others.map((k) => [k, []])),
      ...(typeof value === "string" ? {} : { [key]: value }) }));
    setPicked((p) => ({ ...p, ...Object.fromEntries(others.map((k) => [k, ""])),
      ...(typeof value === "string" ? { [key]: value } : {}) }));
    changed();
  }

  const missing = [
    ...template.fields.filter((f) => f.required && !values[f.key]?.trim()).map((f) => f.label),
    ...template.inputs.filter((i) => !i.alternative && !media[i.key]?.length && !picked[i.key]).map((i) => i.label),
    ...(either.length && !either.some((i) => media[i.key]?.length || picked[i.key])
      ? [either.map((i) => i.label.replace(/^Or /, "")).join(" or ")]
      : []),
  ];

  async function rendered() {
    const body = await renderTemplate.mutateAsync({
      id: template.id,
      fields: values,
      media: {
        ...Object.fromEntries(Object.entries(media).flatMap(([key, list]) => (list[0] ? [[key, list[0].id]] : []))),
        ...Object.fromEntries(Object.entries(picked).filter(([, handle]) => handle)),
      },
    });
    setRun(body);
    return body;
  }

  async function generate(confirmCost?: number) {
    try {
      const body = await rendered();
      const started = await start.mutateAsync({ ...body, confirm_cost_usd: confirmCost ?? null });
      toast(`${template.name} started`);
      router.push(`/runs/${started.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConfirm((err.body as { estimate_usd: number }).estimate_usd);
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start it");
    }
  }

  const field =
    "rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2.5 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60";
  const est = run ? estimate.data : undefined; // the query keeps its last data as a placeholder

  return (
    <div className="grid gap-6 md:grid-cols-[1.3fr_1fr]">
      <div className="space-y-4">
        <p className="leading-relaxed text-chalk">{template.summary}</p>
        {template.inputs.map((i) =>
          i.kind === "character" ? (
            <label key={i.key} className="flex flex-col gap-1.5 text-xs text-muted">
              <span>{i.label}</span>
              <Select
                value={picked[i.key] ?? ""}
                onChange={(v) => choose(i.key, v)}
                options={[
                  { value: "", label: characters?.length ? "None" : "No saved characters yet" },
                  ...(characters ?? []).map((c) => ({ value: c.slug, label: `${c.name} (@${c.slug})` })),
                ]}
              />
            </label>
          ) : (
            <MediaSlotField
              key={i.key}
              slot={{ role: ROLE_FOR_KIND[i.kind], kinds: [i.kind], label: i.label, required: !i.alternative, min: 1, max: 1, auto: false }}
              value={media[i.key] ?? []}
              onAdd={(a) => choose(i.key, [a])}
              onRemove={() => choose(i.key, [])}
            />
          ),
        )}
        {template.fields.map((f) => (
          <label key={f.key} className="flex flex-col gap-1.5 text-xs text-muted">
            <span>
              {f.label}
              {!f.required && <span className="text-faint"> (optional)</span>}
            </span>
            <textarea
              rows={f.max_chars > 300 ? 4 : 1}
              value={values[f.key] ?? ""}
              onChange={(e) => {
                setValues((v) => ({ ...v, [f.key]: e.target.value }));
                changed();
              }}
              maxLength={f.max_chars}
              placeholder={f.placeholder ? `e.g. ${f.placeholder}` : undefined}
              className={cn(field, "resize-none")}
            />
          </label>
        ))}
      </div>

      <aside className="space-y-4 rounded-[var(--radius-control)] bg-bay p-4 text-xs">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
          <dt className="text-faint">Runs</dt>
          <dd className="text-chalk">{template.recipe_name}</dd>
          <dt className="text-faint">Cost</dt>
          <dd className="text-chalk">
            {est ? (
              <>
                {usd(est.estimate_usd)} now
                {est.live_estimate_usd != null && est.live_estimate_usd !== est.estimate_usd && (
                  <span className="text-faint">, {usd(est.live_estimate_usd)} live</span>
                )}
                <span className="text-faint">, capped at {usd(est.cap_usd)}</span>
              </>
            ) : (
              <button
                type="button"
                className="text-muted underline-offset-2 hover:underline disabled:opacity-50"
                disabled={busy || missing.length > 0}
                onClick={() => void rendered().catch(() => undefined)}
              >
                Check the cost
              </button>
            )}
          </dd>
        </dl>
        <p className="leading-relaxed text-faint">
          It runs step by step on the Runs page, and stops to ask you where the recipe has a checkpoint, like approving
          the script.
        </p>
      </aside>

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line-soft pt-4 md:col-span-2">
        {missing.length > 0 && <p className="mr-auto text-xs text-faint">Add {missing.join(", ").toLowerCase()} to start.</p>}
        {confirm != null ? (
          <>
            <p className="mr-auto text-xs text-chalk">This run is estimated at {usd(confirm)}.</p>
            <Button type="button" tone="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button type="button" tone="primary" onClick={() => void generate(confirm)} disabled={busy}>
              Start for {usd(confirm)}
            </Button>
          </>
        ) : (
          <Button type="button" tone="primary" onClick={() => void generate()} disabled={busy || missing.length > 0}>
            Start
          </Button>
        )}
      </div>
    </div>
  );
}
