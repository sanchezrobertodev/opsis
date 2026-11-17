"use client";

import { ChevronsUpDown, Search } from "lucide-react";
import { useMemo, useState } from "react";
import type { Schemas } from "@opsis/api-client";
import { cn } from "@/lib/cn";
import { filterStyles, SAMPLE_SUBJECTS, type SampleSubject, STYLE_CATEGORIES, stylePicture } from "@/lib/styles";
import { useDirectorLibrary } from "@/components/create/DirectorPanel";
import { Dialog, Segmented } from "@/components/ui/controls";
import { ZoomButton } from "@/components/ui/PictureViewer";

type Style = Schemas["StyleItem"];
type Category = Style["category"] | "all";

const TILE = "flex h-full w-full flex-col gap-1.5 rounded-[var(--radius-control)] border p-1.5 text-left";
const tileTone = (on: boolean) => (on ? "border-tungsten/60 bg-raised" : "border-line-soft hover:border-tungsten/50");
const NO_STYLE = "The prompt and the model decide the look.";

/** The picture style (a catalog id; null for none): a button showing the pick, opening a gallery of every style with
 * sample pictures of a person, a place and an object. */
export function StylePicker({ value, onChange, label = "Style", className }: {
  value: string | null | undefined; onChange: (id: string | null) => void; label?: string; className?: string;
}) {
  const { data: lib } = useDirectorLibrary();
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<Category>("all");
  const [query, setQuery] = useState("");
  const [subject, setSubject] = useState<SampleSubject>("person");
  const styles = useMemo(() => lib?.styles ?? [], [lib]);
  const shown = useMemo(() => filterStyles(styles, category, query), [styles, category, query]);
  if (!styles.length) return null;
  const chosen = styles.find((s) => s.id === value);
  const pick = (id: string | null) => {
    onChange(id);
    setOpen(false);
  };

  return (
    <div className={cn("flex flex-col gap-2 text-xs text-muted", className)}>
      {label}
      <button type="button" aria-haspopup="dialog" aria-label={`${label}: ${chosen?.label ?? "No style"}`}
              // start from every style, so the current pick is there to focus
              onClick={() => { setCategory("all"); setQuery(""); setOpen(true); }}
              className="flex w-full items-center gap-3 rounded-[var(--radius-control)] border border-line-soft bg-bay p-1.5 pr-3 text-left hover:border-line">
        {chosen ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img key={chosen.id} src={stylePicture(chosen.id, "person")} alt="" className="size-9 shrink-0 rounded-[3px] bg-raised object-cover"
               onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />
        ) : (
          <span className="size-9 shrink-0 rounded-[3px] border border-dashed border-line" aria-hidden />
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-chalk">{chosen?.label ?? "No style"}</span>
          <span className="block truncate text-2xs text-faint">{chosen?.summary ?? NO_STYLE}</span>
        </span>
        <ChevronsUpDown className="size-4 shrink-0 text-faint" aria-hidden />
      </button>

      <Dialog open={open} onClose={() => setOpen(false)} title="Picture style" wide="full">
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
            {[{ id: "all" as const, label: "All" }, ...STYLE_CATEGORIES].map((c) => (
              <button key={c.id} type="button" aria-pressed={category === c.id} onClick={() => setCategory(c.id)}
                      className={cn("rounded-full px-3 py-1 text-xs", category === c.id ? "bg-raised text-chalk" : "text-muted hover:text-chalk")}>
                {c.label}
              </button>
            ))}
          </div>
          <ul className="grid max-h-[62vh] grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-2 overflow-y-auto -m-1 p-1 scrollbar-quiet">
            <li>
              <button type="button" onClick={() => pick(null)} aria-pressed={!chosen} data-autofocus={!chosen || undefined}
                      className={cn(TILE, tileTone(!chosen))}>
                <span className="aspect-square w-full rounded-[3px] border border-dashed border-line" aria-hidden />
                <span className="px-0.5 text-xs text-chalk">No style</span>
                <span className="px-0.5 pb-0.5 text-2xs leading-snug text-faint">{NO_STYLE}</span>
              </button>
            </li>
            {shown.map((s) => (
              <StyleTile key={`${s.id}-${subject}`} style={s} subject={subject} selected={s.id === chosen?.id}
                         onPick={() => pick(s.id)} />
            ))}
            {shown.length === 0 && <li className="col-span-full text-sm text-muted">No style matches that.</li>}
          </ul>
        </div>
      </Dialog>
    </div>
  );
}

/** A style's tile: its sample picture (a plain frame while it's missing), name and summary, with the full-size view
 * beside the button. */
function StyleTile({ style, subject, selected, onPick }: {
  style: Style; subject: SampleSubject; selected: boolean; onPick: () => void;
}) {
  const [missing, setMissing] = useState(false);
  return (
    <li className="group relative">
      <button type="button" onClick={onPick} aria-pressed={selected} data-autofocus={selected || undefined} title={style.prompt}
              className={cn(TILE, tileTone(selected))}>
        <span className="block aspect-square w-full overflow-hidden rounded-[3px] bg-bay">
          {!missing && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={stylePicture(style.id, subject)} alt="" loading="lazy" onError={() => setMissing(true)}
                 className="size-full object-cover motion-safe:transition-transform motion-safe:duration-300 motion-safe:group-hover:scale-[1.04]" />
          )}
        </span>
        <span className="px-0.5 text-xs text-chalk">{style.label}</span>
        <span className="px-0.5 pb-0.5 text-2xs leading-snug text-faint">{style.summary}</span>
      </button>
      {!missing && <ZoomButton src={stylePicture(style.id, subject, true)} title={`${style.label}, ${subject}`} className="right-3 top-3" />}
    </li>
  );
}
