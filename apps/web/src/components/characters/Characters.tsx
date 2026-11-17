"use client";

import { AudioLines, Mic, Plus, Sparkles, UsersRound } from "lucide-react";
import { useState } from "react";
import { mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { type Element, type ElementKind, KIND_LABEL, useElements } from "@/lib/elements";
import { Button, Dialog, Segmented } from "@/components/ui/controls";
import { ZoomButton } from "@/components/ui/PictureViewer";
import { GLASS_CARD, Lit } from "@/components/ui/Glass";
import { ElementDialog } from "./ElementEditor";
import { StartersDialog } from "./Starters";
import { TraitBuilder, useDesign } from "./TraitBuilder";
import { AddVoice, VoiceDialog } from "./Voices";

const FILTERS = ["all", "character", "location", "prop", "style", "voice"] as const;
type Filter = (typeof FILTERS)[number];

export function Characters() {
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<Element | "new" | null>(null);
  const [building, setBuilding] = useState(false);
  const [cloning, setCloning] = useState(false);
  const [starting, setStarting] = useState(false);
  const design = useDesign(); // the designer's picks, kept while it's closed; a starter can fill them
  // a starter opens a new designer: an earlier one's photo, sentence and Claude's answer (one still coming too) go
  const [designer, setDesigner] = useState(0);
  const { data, isLoading } = useElements(filter === "all" ? undefined : filter, q);
  const items = data ?? [];
  const newKind: ElementKind | null =
    filter === "character" || filter === "location" || filter === "prop" || filter === "style" ? filter : null;

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-quiet">
      <div className="mb-6 flex flex-wrap items-center gap-3">
        {/* its own width, so the buttons wrap to a row of their own before the filter breaks */}
        <div className="max-w-2xl flex-auto">
          <Segmented
            label="Show"
            value={filter}
            options={FILTERS}
            render={(f) => (f === "all" ? "Everything" : `${KIND_LABEL[f]}s`)}
            onChange={setFilter}
          />
        </div>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search by name"
          aria-label="Search characters"
          className="h-9 w-56 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm outline-none placeholder:text-faint focus:border-tungsten/60"
        />
        <div className="ml-auto flex flex-wrap gap-2">
          <Button tone="quiet" size="sm" onClick={() => setStarting(true)}>
            <UsersRound className="size-4" aria-hidden />
            Add starters
          </Button>
          <Button tone="quiet" size="sm" onClick={() => setCloning(true)}>
            <Mic className="size-4" aria-hidden />
            Add a voice
          </Button>
          <Button tone="quiet" size="sm" onClick={() => setBuilding(true)}>
            <Sparkles className="size-4" aria-hidden />
            Design a character
          </Button>
          <Button tone="primary" size="sm" onClick={() => setEditing("new")}>
            <Plus className="size-4" aria-hidden />
            {newKind ? `New ${KIND_LABEL[newKind].toLowerCase()}` : "New from images"}
          </Button>
        </div>
      </div>

      {isLoading ? null : items.length === 0 ? (
        <div className="mt-20 max-w-md">
          <p className="type-title text-xl">{q ? "No matches" : newKind ? `No ${KIND_LABEL[newKind].toLowerCase()}s yet` : "No characters yet"}</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Save a person, place or prop once, then write <span className="text-chalk">@name</span> in any prompt.
            Models that take reference images get its pictures automatically; others get its description.
          </p>
        </div>
      ) : (
        <ul className="columns-[190px] gap-4">
          {items.map((e) => (
            <li key={e.id} className="group relative mb-4 break-inside-avoid">
              {e.kind !== "voice" && e.cover && <ZoomButton src={mediaUrl(e.cover.url)} title={e.name} className="right-4 top-4" />}
              <button
                type="button"
                onClick={() => setEditing(e)}
                className={cn(GLASS_CARD, "block w-full text-left")}
                aria-label={`${e.name}, ${KIND_LABEL[e.kind]}`}
              >
                <Lit picture={e.kind !== "voice" && e.cover ? mediaUrl(e.cover.thumb_url ?? e.cover.url) : undefined}>
                  <div className="overflow-hidden rounded-[var(--radius-frame)] bg-raised"
                       style={{ aspectRatio: e.kind !== "voice" && e.cover?.width && e.cover.height
                         ? `${e.cover.width} / ${e.cover.height}` : "3 / 4" }}>
                    {e.kind === "voice" ? (
                      <span className="grid size-full place-items-center text-tungsten">
                        <AudioLines className="size-8" aria-hidden />
                      </span>
                    ) : e.cover ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={mediaUrl(e.cover.thumb_url ?? e.cover.url)} alt="" className="size-full object-cover transition-transform group-hover:scale-[1.02]" />
                    ) : (
                      <span className="grid size-full place-items-center text-xs text-faint">No images yet</span>
                    )}
                  </div>
                </Lit>
                <p className="type-title mt-2 truncate px-1 text-[15px]">{e.name}</p>
                <p className="truncate px-1 pb-1 text-xs text-faint">
                  <span className="text-tungsten">{e.handle}</span>, {KIND_LABEL[e.kind].toLowerCase()}
                  {e.kind !== "voice" && <>, {e.images.length} image{e.images.length === 1 ? "" : "s"}</>}
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}

      <ElementDialog element={editing && editing !== "new" && editing.kind === "voice" ? null : editing}
                     newKind={newKind ?? "character"} onClose={() => setEditing(null)} />
      <VoiceDialog voice={editing && editing !== "new" && editing.kind === "voice" ? editing : null}
                   onClose={() => setEditing(null)} />
      <Dialog open={cloning} onClose={() => setCloning(false)} title="Add a voice">
        {cloning && <AddVoice onDone={() => setCloning(false)} />}
      </Dialog>
      <StartersDialog open={starting} onClose={() => setStarting(false)} design={design}
                      onDesign={() => { setStarting(false); setDesigner((n) => n + 1); setBuilding(true); }} />
      <TraitBuilder key={designer} open={building} onClose={() => setBuilding(false)} design={design} />
    </div>
  );
}
