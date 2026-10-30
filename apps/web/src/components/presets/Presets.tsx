"use client";

import { BookOpen, Package, Palette, Scissors, Smartphone, Sparkles, UserRound } from "lucide-react";
import type { Schemas } from "@studio/api-client";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { api, ApiError, unwrap } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { type SlotAsset, useDraft } from "@/lib/draft";
import { useDebounced } from "@/lib/hooks";
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
import { useClonedVoices, useElements } from "@/lib/elements";
import { useEstimateRun, useStartRun } from "@/lib/runs";
import { glyphClass } from "@/components/create/DirectorPanel";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { type Quote, quotePrice, quoteText, sameRequest, useRunQuote } from "@/components/runs/RunQuote";
import { Button, Dialog, Segmented, Select } from "@/components/ui/controls";
import { GLASS_CARD, Lit } from "@/components/ui/Glass";

const FILTERS = ["all", "guided", "camera", "effects", "look", "character", "product", "social", "edit"] as const;
type Filter = (typeof FILTERS)[number];
type Shelf = "all" | "video" | "image";
const SHELVES: readonly Shelf[] = ["all", "video", "image"];
const SHELF_LABEL: Record<Shelf, string> = { all: "All", video: "Video", image: "Image" };

// every card's picture frame: an amber edge and a slight zoom say it opens
const FRAME =
  "h-40 overflow-hidden rounded-[var(--radius-frame)] bg-panel ring-1 ring-transparent transition-[box-shadow,background-color] group-hover:ring-tungsten/70 group-focus-visible:ring-2 group-focus-visible:ring-tungsten";

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
  const [shelf, setShelf] = useState<Shelf>("all");
  const [chosen, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<Preset | null>(null);
  const [openTemplate, setOpenTemplate] = useState<Template | null>(null);
  const { data, isLoading } = usePresets();
  const { data: templates } = useTemplates();
  const onShelf = (data ?? []).filter((p) => shelf === "all" || (p.output_type === "image" ? "image" : "video") === shelf);
  const stories = (templates ?? []).filter((t) => shelf === "all" || t.makes === shelf);
  // only the categories this shelf has; a choice the other shelf lacks falls back to everything
  const filters = FILTERS.filter(
    (f) => f === "all" || (f === "guided" ? stories.length > 0 : onShelf.some((p) => p.category === f)),
  );
  const filter = filters.includes(chosen) ? chosen : "all";
  const items = onShelf.filter((p) => filter === "all" || p.category === filter);
  const guided = filter === "all" || filter === "guided" ? stories : [];

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-quiet">
      <div className="mb-6 flex max-w-4xl flex-wrap items-center gap-x-5 gap-y-3">
        <Segmented
          label="Makes"
          value={shelf}
          options={SHELVES}
          render={(s) => SHELF_LABEL[s]}
          onChange={setShelf}
        />
        <Segmented
          label="Show"
          value={filter}
          options={filters}
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

/** Whether a card is hovered or has keyboard focus: its cover clip plays meanwhile. */
function useLive() {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  return [hovered || focused, {
    onPointerEnter: () => setHovered(true),
    onPointerLeave: () => setHovered(false),
    onFocus: () => setFocused(true),
    onBlur: () => setFocused(false),
  }] as const;
}

/** A card's picture: its cover (`public/covers/<id>.webp`, an example of what it makes) when there is one, else the
 * drawn stand-in it wraps. A clip next to it (`<id>.mp4`, muted) plays over it while the card is `live`, unless
 * animations are off; a card without one never asks again after the first miss. */
function Cover({ id, live, children }: { id: string; live: boolean; children: React.ReactNode }) {
  const [missing, setMissing] = useState(false);
  const [noClip, setNoClip] = useState(false);
  if (missing) return <>{children}</>;
  const zoom = "size-full object-cover transition-transform duration-300 group-hover:scale-[1.04]";
  return (
    <Lit picture={`/covers/${id}.webp`}>
      <div className={cn(FRAME, "relative")}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={`/covers/${id}.webp`} alt="" loading="lazy" onError={() => setMissing(true)} className={zoom} />
        {live && !noClip && !animationsOff() && (
          // the poster is the picture under it, so nothing changes until the clip starts
          <video src={`/covers/${id}.mp4`} poster={`/covers/${id}.webp`} autoPlay muted loop playsInline aria-hidden
                 onError={() => setNoClip(true)} className={cn(zoom, "absolute inset-0")} />
        )}
      </div>
    </Lit>
  );
}

const animationsOff = () =>
  document.documentElement.dataset.motion === "off" || matchMedia("(prefers-reduced-motion: reduce)").matches;

/** A viewfinder drawn at the shot's own aspect ratio, with the camera move playing on hover. */
function Viewfinder({ preset }: { preset: Preset }) {
  const [w, h] = aspectOf(preset);
  const Icon = CATEGORY_ICON[preset.category as PresetCategory] ?? Sparkles;
  const move = preset.moves[0];
  return (
    <div className={cn(FRAME, "relative grid place-items-center group-hover:bg-raised")}>
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
          <span aria-hidden className={cn("block size-2.5 rounded-full bg-tungsten", glyphClass(move))} />
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
  const [live, liveProps] = useLive();
  return (
    <button type="button" onClick={onOpen} {...liveProps} className={cn(GLASS_CARD, "group block w-full pb-3 text-left outline-none")}>
      <Cover id={preset.id} live={live}>
        <Viewfinder preset={preset} />
      </Cover>
      <p className="type-title mt-3 px-1 text-[15px] transition-colors group-hover:text-tungsten">{preset.name}</p>
      <p className="mt-1 line-clamp-2 px-1 text-xs leading-relaxed text-muted">{preset.summary}</p>
      <p className="tabular mt-1.5 px-1 text-2xs text-faint">
        {preset.model_name}
        {priceText(preset) && <>, <span className={cn(preset.local && "text-done")}>{priceText(preset)}</span></>}
        {needs.length > 0 && <>. Needs {needs.join(" and ")}</>}
      </p>
    </button>
  );
}

/** A rendered request, free, once its answers stop changing; `null` until then. */
function useRendered<A, T>(key: string, answers: A | null, render: (answers: A) => Promise<T>) {
  const asked = useDebounced(answers, 400);
  const q = useQuery({
    queryKey: ["rendered", key, asked],
    queryFn: () => render(asked!),
    enabled: Boolean(asked),
    retry: false,
  });
  const current = Boolean(asked) && sameRequest(asked, answers); // typed since: no answer until it's asked again
  return { data: current ? (q.data ?? null) : null, error: current && q.error instanceof Error ? q.error.message : null };
}

/** What one take of a preset costs where it would run now (mock mode: what it would cost live), as a run's quote. */
function usePresetQuote(req: Schemas["PresetRenderOut"] | null): Quote {
  const est = useQuery({
    queryKey: ["estimate", req],
    queryFn: () => unwrap(api.POST("/registry/estimate", {
      body: { ...req!, direction: req!.direction ?? null, route_policy: "cheapest_healthy" } })),
    enabled: Boolean(req),
    retry: false,
  });
  const d = req ? est.data : undefined;
  const chosen = d?.candidates.find((c) => c.route_id === d.chosen_route_id);
  const noRoute = d && !chosen ? (d.candidates.find((c) => !c.is_mock)?.reason ?? "No provider can run this right now.") : null;
  const error = !req ? null : est.error instanceof Error ? est.error.message : noRoute;
  const mock = chosen?.is_mock ?? false;
  return { usd: chosen ? ((mock ? d?.live_reference?.estimate_usd : d?.estimate_usd) ?? null) : null,
           charged: d?.estimate_usd ?? null, count: chosen ? 1 : null, mock, error };
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
  const answers = {
    fields: values,
    media: Object.entries(media).flatMap(([role, list]) => list.map((a) => ({ role, asset_id: a.id }))),
  };
  const preview = useRendered(`preset:${preset.id}`, missing.length ? null : answers, (body) =>
    unwrap(api.POST("/presets/{preset_id}/render", { params: { path: { preset_id: preset.id } }, body })));
  const priced = usePresetQuote(preview.data);
  const quote = preview.error ? { ...priced, error: preview.error } : priced;

  async function rendered() {
    return renderPreset.mutateAsync({ id: preset.id, ...answers });
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
            {preview.data || quote.error ? quoteText(quote, ["take", "takes"])
              : preset.local ? "Free on this computer"
              : preset.live_price_usd != null ? `From ${usd(preset.live_price_usd)} live, exact once it's filled in`
              : "Depends on your clip; shown once it's filled in"}
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
            <p className="mr-auto text-xs text-chalk">The price changed: this take is now estimated at {usd(confirm)}.</p>
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
            <Button type="button" tone="primary" onClick={() => void generate(quote.charged ?? undefined)}
                    disabled={busy || missing.length > 0 || quote.count == null || Boolean(quote.error)}>
              Generate{quotePrice(quote) && ` · ${quotePrice(quote)}`}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function TemplateCard({ template, onOpen }: { template: Template; onOpen: () => void }) {
  const needs = template.inputs.filter((i) => !i.alternative && !i.optional).map((i) => i.label.toLowerCase());
  const either = template.inputs.filter((i) => i.alternative).map((i) => i.label.toLowerCase().replace(/^or /, ""));
  const [live, liveProps] = useLive();
  return (
    <button type="button" onClick={onOpen} {...liveProps} className={cn(GLASS_CARD, "group block w-full pb-3 text-left outline-none")}>
      <Cover id={template.id} live={live}>
        <div className={cn(FRAME, "grid place-items-center group-hover:bg-raised")}>
          <BookOpen aria-hidden className="size-6 text-tungsten" />
        </div>
      </Cover>
      <p className="type-title mt-3 px-1 text-[15px] transition-colors group-hover:text-tungsten">{template.name}</p>
      <p className="mt-1 line-clamp-2 px-1 text-xs leading-relaxed text-muted">{template.summary}</p>
      <p className="tabular mt-1.5 px-1 text-2xs text-faint">
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
  const { data: characters } = useElements("character");
  const cloned = useClonedVoices(); // listed in fields that take values beyond their options (voices)
  const busy = renderTemplate.isPending || start.isPending;
  // a confirmation was for the old answers and media
  const changed = () => setConfirm(null);
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
    ...template.inputs.filter((i) => !i.alternative && !i.optional && !media[i.key]?.length && !picked[i.key])
      .map((i) => i.label),
    ...(either.length && !either.some((i) => media[i.key]?.length || picked[i.key])
      ? [either.map((i) => i.label.replace(/^Or /, "")).join(" or ")]
      : []),
  ];

  const answers = {
    fields: values,
    media: {
      ...Object.fromEntries(Object.entries(media).flatMap(([key, list]) => (list[0] ? [[key, list[0].id]] : []))),
      ...Object.fromEntries(Object.entries(picked).filter(([, handle]) => handle)),
    },
  };
  const preview = useRendered(`template:${template.id}`, missing.length ? null : answers, (body) =>
    unwrap(api.POST("/templates/{template_id}/render", { params: { path: { template_id: template.id } }, body })));
  const priced = useRunQuote(preview.data);
  const quote = preview.error ? { ...priced, error: preview.error } : priced;
  const est = useEstimateRun(preview.data).data; // the same estimate the quote reads: which models, and the cap
  const models = preview.data ? (est?.models ?? []) : [];

  async function generate(confirmCost?: number) {
    try {
      // rendered afresh, so a quick press never starts the answers from before the last keystroke
      const body = await renderTemplate.mutateAsync({ id: template.id, ...answers });
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

  return (
    <div className="grid gap-6 md:grid-cols-[1.3fr_1fr]">
      <div className="space-y-4">
        <p className="leading-relaxed text-chalk">{template.summary}</p>
        {template.inputs.map((i) =>
          i.kind === "character" ? (
            <label key={i.key} className="flex flex-col gap-1.5 text-xs text-muted">
              <span>
                {i.label}
                {i.optional && <span className="text-faint"> (optional)</span>}
              </span>
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
              slot={{ role: ROLE_FOR_KIND[i.kind], kinds: [i.kind], label: i.label, required: !i.alternative && !i.optional,
                      min: 1, max: 1, auto: false }}
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
            {f.options.length > 0 ? (
              <Select
                value={values[f.key] ?? ""}
                onChange={(v) => {
                  setValues((old) => ({ ...old, [f.key]: v }));
                  changed();
                }}
                options={[
                  { value: "", label: "Pick one" },
                  ...(f.open ? cloned.map(([id, label]) => ({ value: id, label })) : []),
                  ...f.options.map((o) => ({ value: o, label: f.option_labels[o] ?? o })),
                ]}
              />
            ) : (
              <textarea
                rows={f.max_chars > 300 ? 4 : 2}
                value={values[f.key] ?? ""}
                onChange={(e) => {
                  setValues((v) => ({ ...v, [f.key]: e.target.value }));
                  changed();
                }}
                maxLength={f.max_chars}
                placeholder={f.placeholder ? `e.g. ${f.placeholder}` : undefined}
                className={cn(field, "max-h-48 resize-none field-sizing-content", f.max_chars > 300 && "min-h-28")}
              />
            )}
          </label>
        ))}
      </div>

      <aside className="space-y-4 rounded-[var(--radius-control)] bg-bay p-4 text-xs">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
          <dt className="text-faint">Runs</dt>
          <dd className="text-chalk">{template.recipe_name}</dd>
          <dt className="text-faint">Models</dt>
          <dd className={models.length ? "text-chalk" : "text-faint"}>
            {models.length ? models.join(", ")
              : missing.length > 0 ? "Shown once the form is filled in"
              : quote.error ? "Couldn’t work them out" : "Working it out…"}
          </dd>
          {est && preview.data && (
            <>
              <dt className="text-faint">Run cap</dt>
              <dd className="text-chalk">{usd(est.cap_usd)}; it never spends more</dd>
            </>
          )}
        </dl>
        <p className="leading-relaxed text-faint">
          It runs step by step on the Runs page, and stops to ask you where the recipe has a checkpoint, like approving
          the script.
        </p>
      </aside>

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line-soft pt-4 md:col-span-2">
        {missing.length > 0 ? (
          <p className="mr-auto text-xs text-faint">Add {missing.join(", ").toLowerCase()} to start.</p>
        ) : confirm == null && (
          <p className={cn("mr-auto text-xs", quote.error ? "text-fault" : "text-muted")}>
            {quoteText(quote, ["generation", "generations"])}
          </p>
        )}
        {confirm != null ? (
          <>
            <p className="mr-auto text-xs text-chalk">The price changed: this run is now estimated at {usd(confirm)}.</p>
            <Button type="button" tone="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button type="button" tone="primary" onClick={() => void generate(confirm)} disabled={busy}>
              Start for {usd(confirm)}
            </Button>
          </>
        ) : (
          <Button type="button" tone="primary" onClick={() => void generate(quote.charged ?? undefined)}
                  disabled={busy || missing.length > 0 || quote.count == null || Boolean(quote.error)}>
            Start{quotePrice(quote) && ` · ${quotePrice(quote)}`}
          </Button>
        )}
      </div>
    </div>
  );
}
