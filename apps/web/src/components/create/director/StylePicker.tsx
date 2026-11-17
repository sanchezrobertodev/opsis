"use client";

import { ChevronsUpDown, LoaderCircle, Plus, Search, Sparkles } from "lucide-react";
import { type Dispatch, type SetStateAction, useMemo, useState } from "react";
import { flushSync } from "react-dom";
import type { Schemas } from "@opsis/api-client";
import { type MediaSlot, mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { SlotAsset } from "@/lib/draft";
import { type Element, useCreateElement, useDescribeStyle, useElements } from "@/lib/elements";
import {
  claudeSummary,
  filterStyles,
  SAMPLE_SUBJECTS,
  type SampleSubject,
  STYLE_CATEGORIES,
  type StyleCategory,
  stylePicture,
} from "@/lib/styles";
import { useDirectorLibrary } from "@/components/create/DirectorPanel";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { useClaudeCost } from "@/components/llm/ClaudeCost";
import { Button, Dialog, Segmented } from "@/components/ui/controls";
import { ZoomButton } from "@/components/ui/PictureViewer";

/** A style to pick: one of the catalog's (sample pictures by subject) or a saved one (id `@slug`, its cover). */
type Tile = Pick<Schemas["StyleItem"], "id" | "label" | "summary" | "prompt"> & {
  category: string;
  cover?: { thumb?: string; full?: string };
};
type Category = StyleCategory | "all" | "mine";

const TILE = "flex h-full w-full flex-col gap-1.5 rounded-[var(--radius-control)] border p-1.5 text-left";
const tileTone = (on: boolean) => (on ? "border-tungsten/60 bg-raised" : "border-line-soft hover:border-tungsten/50");
const NO_STYLE = "The prompt and the model decide the look.";
const FIELD = "rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60";
/** How much of a saved style's look reaches a prompt (the server's mention description limit,
 * `director/elements.py:DESCRIPTION_LIMIT`). */
export const LOOK_MAX = 300;
const STYLE_SLOT: MediaSlot = {
  role: "style_image", kinds: ["image"], label: "Pictures in the style", max: 4, min: 1, required: true, auto: false,
};

const tilePicture = (t: Tile, subject: SampleSubject, full = false) =>
  t.cover ? (full ? t.cover.full : t.cover.thumb) : stylePicture(t.id, subject, full);

/** A saved style (a style Element): its handle is what a direction names, its description the look. */
function savedTile(e: Element): Tile {
  const meta = (e.attributes.style ?? {}) as { category?: string; summary?: string };
  return {
    id: e.handle, label: e.name, category: meta.category ?? "", summary: meta.summary || e.description,
    prompt: e.description, cover: { thumb: mediaUrl(e.cover?.thumb_url ?? e.cover?.url), full: mediaUrl(e.cover?.url) },
  };
}

/** The styles a picker offers: the catalog's and your saved ones. */
export function useStyleTiles(): { catalog: Tile[]; mine: Tile[] } {
  const { data: lib } = useDirectorLibrary();
  const { data: saved } = useElements("style");
  const catalog = useMemo(() => lib?.styles ?? [], [lib]);
  const mine = useMemo(() => (saved ?? []).map(savedTile), [saved]);
  return { catalog, mine };
}

/** The picture style (a catalog id or a saved style's `@slug`; null for none): a button showing the pick, opening a
 * gallery of every style with sample pictures of a person, a place and an object, and your saved styles. */
export function StylePicker({ value, onChange, label = "Style", className, aside }: {
  value: string | null | undefined; onChange: (id: string | null) => void; label?: string; className?: string;
  /** Beside the label, e.g. a related action. */
  aside?: React.ReactNode;
}) {
  const { catalog, mine } = useStyleTiles();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState(NO_DRAFT);
  if (!catalog.length) return null;
  const chosen = [...mine, ...catalog].find((s) => s.id === value);
  // a saved style still loading, or deleted since (Generate then says it's unknown), shows its handle
  const handle = !chosen && value?.startsWith("@") ? value : null;
  const name = chosen?.label ?? handle ?? "No style";
  const picture = chosen && tilePicture(chosen, "person");
  const pick = (id: string | null) => {
    onChange(id);
    setOpen(false);
  };

  return (
    <div className={cn("flex flex-col gap-2 text-xs text-muted", className)}>
      <span className="flex items-baseline justify-between gap-3">
        {label}
        {aside}
      </span>
      <button type="button" aria-haspopup="dialog" aria-label={`${label}: ${name}`} onClick={() => setOpen(true)}
              className="flex w-full items-center gap-3 rounded-[var(--radius-control)] border border-line-soft bg-bay p-1.5 pr-3 text-left hover:border-line">
        {picture ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img key={picture} src={picture} alt="" className="size-9 shrink-0 rounded-[3px] bg-raised object-cover"
               onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />
        ) : (
          <span className="size-9 shrink-0 rounded-[3px] border border-dashed border-line" aria-hidden />
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-chalk">{name}</span>
          <span className="block truncate text-2xs text-faint">{chosen?.summary ?? (handle ? "A saved style" : NO_STYLE)}</span>
        </span>
        <ChevronsUpDown className="size-4 shrink-0 text-faint" aria-hidden />
      </button>

      <Dialog open={open} onClose={() => setOpen(false)} title="Picture style" wide="full">
        <StyleGallery catalog={catalog} mine={mine} isOn={(id) => id === chosen?.id} onTile={pick}
                      none={{ on: !chosen && !handle, onPick: () => pick(null) }} onNew={() => setCreating(true)} />
      </Dialog>
      <Dialog open={creating} onClose={() => setCreating(false)} title="New style">
        <NewStyleForm draft={draft} onDraft={setDraft} onCancel={() => { setDraft(NO_DRAFT); setCreating(false); }}
                      onSaved={(id) => {
                        setDraft(NO_DRAFT);
                        // this dialog closes before the gallery under it, so focus goes back to the style button
                        flushSync(() => setCreating(false));
                        pick(id);
                      }} />
      </Dialog>
    </div>
  );
}

/** The styles to choose from (search, sections, the samples' subject), your saved ones first. One pick, with
 * `none` adding "No style", or several, with `full` turning off every tile not picked; `onNew` offers a new saved
 * style under My styles. */
export function StyleGallery({ catalog, mine, isOn, onTile, none, full = false, onNew }: {
  catalog: Tile[]; mine: Tile[]; isOn: (id: string) => boolean; onTile: (id: string) => void;
  none?: { on: boolean; onPick: () => void }; full?: boolean; onNew?: () => void;
}) {
  const [category, setCategory] = useState<Category>("all");
  const [query, setQuery] = useState("");
  const [subject, setSubject] = useState<SampleSubject>("person");
  const shown = useMemo(() => (category === "mine" ? filterStyles(mine, "all", query)
    : filterStyles([...mine, ...catalog], category, query)), [mine, catalog, category, query]);
  const sections = [{ id: "all" as const, label: "All" }, ...STYLE_CATEGORIES,
                    ...(mine.length || onNew ? [{ id: "mine" as const, label: "My styles" }] : [])];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex h-9 min-w-48 flex-1 items-center gap-2 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 focus-within:border-tungsten/60">
          <Search className="size-4 text-faint" aria-hidden />
          <input value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search styles"
                 placeholder="Search styles, e.g. watercolor, neon, clay"
                 className="min-w-0 flex-1 bg-transparent text-sm text-chalk outline-none placeholder:text-faint" />
        </label>
        <Segmented label="Sample pictures" value={subject} options={SAMPLE_SUBJECTS} onChange={setSubject}
                   render={(s) => s.charAt(0).toUpperCase() + s.slice(1)} />
      </div>
      <div className="flex flex-wrap gap-1">
        {sections.map((c) => (
          <button key={c.id} type="button" aria-pressed={category === c.id} onClick={() => setCategory(c.id)}
                  className={cn("rounded-full px-3 py-1 text-xs", category === c.id ? "bg-raised text-chalk" : "text-muted hover:text-chalk")}>
            {c.label}
          </button>
        ))}
      </div>
      <ul className="grid max-h-[62vh] grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-2 overflow-y-auto -m-1 p-1 scrollbar-quiet">
        {none && (
          <li>
            <button type="button" onClick={none.onPick} aria-pressed={none.on} data-autofocus={none.on || undefined}
                    className={cn(TILE, tileTone(none.on))}>
              <span className="aspect-square w-full rounded-[3px] border border-dashed border-line" aria-hidden />
              <span className="px-0.5 text-xs text-chalk">No style</span>
              <span className="px-0.5 pb-0.5 text-2xs leading-snug text-faint">{NO_STYLE}</span>
            </button>
          </li>
        )}
        {onNew && category === "mine" && (
          <li>
            <button type="button" onClick={onNew} className={cn(TILE, tileTone(false))}>
              <span className="grid aspect-square w-full place-items-center rounded-[3px] border border-dashed border-line text-faint" aria-hidden>
                <Plus className="size-6" strokeWidth={1.5} />
              </span>
              <span className="px-0.5 text-xs text-chalk">New style</span>
              <span className="px-0.5 pb-0.5 text-2xs leading-snug text-faint">From one to four pictures; Claude describes the look.</span>
            </button>
          </li>
        )}
        {shown.map((s) => (
          <StyleTile key={`${s.id}-${subject}`} style={s} subject={subject} selected={isOn(s.id)}
                     disabled={full && !isOn(s.id)} onPick={() => onTile(s.id)} />
        ))}
        {shown.length === 0 && (query.trim() || category !== "mine") && (
          <li className="col-span-full text-sm text-muted">No style matches that.</li>
        )}
      </ul>
    </div>
  );
}

/** A style's tile: its sample picture or cover (a plain frame while it's missing), name and summary, with the full-size
 * view beside the button. */
function StyleTile({ style, subject, selected, disabled, onPick }: {
  style: Tile; subject: SampleSubject; selected: boolean; disabled: boolean; onPick: () => void;
}) {
  const [missing, setMissing] = useState(false);
  const src = tilePicture(style, subject);
  const full = tilePicture(style, subject, true);
  return (
    <li className="group relative">
      <button type="button" onClick={onPick} aria-pressed={selected} disabled={disabled} data-autofocus={selected || undefined}
              title={style.prompt} className={cn(TILE, tileTone(selected), "disabled:cursor-not-allowed disabled:opacity-45")}>
        <span className="block aspect-square w-full overflow-hidden rounded-[3px] bg-bay">
          {src && !missing && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={src} alt="" loading="lazy" onError={() => setMissing(true)}
                 className="size-full object-cover motion-safe:transition-transform motion-safe:duration-300 motion-safe:group-hover:scale-[1.04]" />
          )}
        </span>
        <span className="px-0.5 text-xs text-chalk">
          {style.label}
          {style.cover && <span className="ml-1.5 text-2xs text-tungsten">{style.id}</span>}
        </span>
        <span className="px-0.5 pb-0.5 text-2xs leading-snug text-faint">{style.summary}</span>
      </button>
      {full && !missing && (
        <ZoomButton src={full} title={style.cover ? style.label : `${style.label}, ${subject}`} className="right-3 top-3" />
      )}
    </li>
  );
}

/** "Describe with Claude", priced: Claude names and describes the look the pictures share and hands its answer to
 * `onAnswer`. Why it couldn't (the spending limit, no key, a picture it can't take) shows under the button. */
export function DescribeStyleButton({ assetIds, onAnswer }: {
  assetIds: string[]; onAnswer: (r: Schemas["DescribeStyleOut"]) => void;
}) {
  const describe = useDescribeStyle();
  const claude = useClaudeCost("style.describe");
  return (
    <div className="flex flex-col items-start gap-1.5">
      <Button type="button" size="sm" disabled={!assetIds.length || describe.isPending}
              onClick={() => describe.mutate(assetIds, { onSuccess: onAnswer })}
              title={`${claude.name} looks at the pictures and writes the name and look they share`}>
        {describe.isPending ? <LoaderCircle className="size-3.5 animate-spin" aria-hidden /> : <Sparkles className="size-3.5" aria-hidden />}
        {describe.isPending ? "Describing…" : "Describe with Claude"}
        {claude.price && <span className="font-normal text-muted">· {claude.price}</span>}
      </Button>
      {describe.error && <p role="alert" className="text-xs leading-relaxed text-fault">{describe.error.message}</p>}
    </div>
  );
}

/** What the New style form holds until it's saved or canceled; closing its dialog keeps it. `yours`: what you set
 * yourself, which Claude's answer leaves alone. `described`: Claude's last answer. */
type StyleDraft = {
  pictures: SlotAsset[]; name: string; look: string; category?: StyleCategory;
  yours: { name?: boolean; look?: boolean; category?: boolean };
  described?: { look: string; summary: string };
};
const NO_DRAFT: StyleDraft = { pictures: [], name: "", look: "", yours: {} };

/** A saved style from one to four pictures: Claude names and describes their look, you check its words, and it's kept
 * as a style Element (the look is its description, the pictures its references). */
function NewStyleForm({ draft: d, onDraft, onCancel, onSaved }: {
  draft: StyleDraft; onDraft: Dispatch<SetStateAction<StyleDraft>>; onCancel: () => void;
  onSaved: (handle: string) => void;
}) {
  const create = useCreateElement();
  const yours = (key: keyof StyleDraft["yours"], change: Partial<StyleDraft>) =>
    onDraft((x) => ({ ...x, ...change, yours: { ...x.yours, [key]: true } }));

  function save() {
    create.mutate({
      kind: "style", name: d.name.trim(), description: d.look.trim(),
      images: d.pictures.map((p) => ({ asset_id: p.id, view: "style" as const })),
      attributes: { style: { category: d.category, summary: claudeSummary(d.look, d.described) } },
    }, { onSuccess: (e) => onSaved(e.handle) }); // a refusal shows below, with everything kept
  }

  return (
    <div className="flex flex-col gap-4">
      <MediaSlotField slot={STYLE_SLOT} value={d.pictures}
                      onAdd={(a) => onDraft((x) => (x.pictures.some((p) => p.id === a.id) || x.pictures.length >= STYLE_SLOT.max
                        ? x : { ...x, pictures: [...x.pictures, a] }))}
                      onRemove={(id) => onDraft((x) => ({ ...x, pictures: x.pictures.filter((p) => p.id !== id) }))} />
      <div className="flex flex-col gap-1.5">
        <DescribeStyleButton assetIds={d.pictures.map((p) => p.id)} onAnswer={(r) => onDraft((x) => ({
          ...x,
          name: x.yours.name && x.name.trim() ? x.name : r.name,
          look: x.yours.look && x.look.trim() ? x.look : r.look,
          category: x.yours.category && x.category ? x.category : r.category,
          described: { look: r.look, summary: r.summary },
        }))} />
        <p className="text-2xs text-faint">Claude fills in the name, look and category; what you&rsquo;ve set yourself stays.</p>
      </div>
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        Name
        <input value={d.name} maxLength={80} placeholder="e.g. Faded film" className={`h-10 ${FIELD}`}
               onChange={(e) => yours("name", { name: e.target.value })} />
      </label>
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        <span className="flex items-baseline justify-between">
          Look
          <span className="readout text-2xs text-faint">{d.look.length} / {LOOK_MAX}</span>
        </span>
        <textarea value={d.look} maxLength={LOOK_MAX} rows={3} className={`resize-y py-2 leading-relaxed ${FIELD}`}
                  placeholder="The look only: medium, palette, light, line, texture, era."
                  onChange={(e) => yours("look", { look: e.target.value })} />
      </label>
      <div className="flex flex-col gap-1.5 text-xs text-muted">
        Category
        <Segmented label="Category" value={d.category} options={STYLE_CATEGORIES.map((c) => c.id)}
                   render={(id) => STYLE_CATEGORIES.find((c) => c.id === id)?.label}
                   onChange={(c) => yours("category", { category: c })} />
      </div>
      {create.error && <p role="alert" className="text-xs leading-relaxed text-fault">{create.error.message}</p>}
      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" tone="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="button" tone="primary" onClick={save}
                disabled={!d.pictures.length || !d.name.trim() || !d.look.trim() || create.isPending}>
          {create.isPending ? "Saving…" : "Save style"}
        </Button>
      </div>
    </div>
  );
}
