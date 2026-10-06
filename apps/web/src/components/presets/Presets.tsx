"use client";

import { Package, Palette, Scissors, Smartphone, Sparkles, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { type SlotAsset, useDraft } from "@/lib/draft";
import { CATEGORY_LABEL, type Preset, type PresetCategory, usePresets, useRenderPreset } from "@/lib/presets";
import { useCreateGeneration } from "@/lib/queries";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { Button, Dialog, Segmented } from "@/components/ui/controls";

const FILTERS = ["all", "camera", "effects", "look", "character", "product", "social", "edit"] as const;
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
  if (p.local) return "Free on your GPU";
  if (p.live_price_usd != null) return `from ${usd(p.live_price_usd)}`;
  return "";
}

export function Presets() {
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<Preset | null>(null);
  const { data, isLoading } = usePresets();
  const items = (data ?? []).filter((p) => filter === "all" || p.category === filter);

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-quiet">
      <div className="mb-6 max-w-3xl">
        <Segmented
          label="Show"
          value={filter}
          options={FILTERS}
          render={(f) => (f === "all" ? "Everything" : CATEGORY_LABEL[f])}
          onChange={setFilter}
        />
      </div>

      {isLoading ? null : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-x-5 gap-y-7">
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
      <Viewfinder preset={preset} />
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
            {preset.local ? "Free on your GPU" : preset.live_price_usd != null ? `About ${usd(preset.live_price_usd)} live` : "Depends on your clip"}
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
