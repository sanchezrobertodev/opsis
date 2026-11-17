"use client";

import { AudioLines, Mic, Plus, Sparkles, Upload, UsersRound } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { type Element, type ElementKind, KIND_LABEL, useElements, useImportElement } from "@/lib/elements";
import { Button, Dialog, Segmented } from "@/components/ui/controls";
import { ZoomButton } from "@/components/ui/PictureViewer";
import { GLASS_CARD, Lit } from "@/components/ui/Glass";
import { SelectButton, SelectionBar, SelectMark, useSelection } from "@/components/ui/Selection";
import { CAST_SIZE, CastCovers, MakeCastDialog } from "./Casts";
import { ElementDialog } from "./ElementEditor";
import { StartersDialog } from "./Starters";
import { TraitBuilder, useDesign } from "./TraitBuilder";
import { AddVoice, VoiceDialog } from "./Voices";

const FILTERS = ["all", "character", "cast", "location", "prop", "style", "voice"] as const;
type Filter = (typeof FILTERS)[number];
const MAX_ZIP = 2 * 1024 ** 3; // what POST /elements/import takes (characters/bundle.py:MAX_BYTES)

export function Characters() {
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<Element | "new" | null>(null);
  const [building, setBuilding] = useState(false);
  const [cloning, setCloning] = useState(false);
  const [starting, setStarting] = useState(false);
  const [making, setMaking] = useState(false);
  const selection = useSelection(); // characters to make a cast of
  const select = useRef<HTMLButtonElement>(null);
  // a cast was just made: the dialog closes onto Select, as the selection bar's button that opened it has gone
  const made = useRef(false);
  const design = useDesign(); // the designer's picks, kept while it's closed; a starter can fill them
  // a starter opens a new designer: an earlier one's photo, sentence and Claude's answer (one still coming too) go
  const [designer, setDesigner] = useState(0);
  const importer = useRef<HTMLInputElement>(null);
  const [sent, setSent] = useState(0); // of the zip being imported
  const importing = useImportElement(setSent);
  const { data, isLoading } = useElements(filter === "all" ? undefined : filter, q);
  const items = data ?? [];
  // the open one as the list has it now, so the editor shows the looks and pictures just made
  const open = editing && editing !== "new" ? (items.find((e) => e.id === editing.id) ?? editing) : editing;
  const characters = items.filter((e) => e.kind === "character");
  const picked = [...selection.picked].flatMap((id) => characters.filter((c) => c.id === id)); // in the order picked
  const castable = picked.length >= CAST_SIZE[0] && picked.length <= CAST_SIZE[1];
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
        {characters.length >= CAST_SIZE[0] && !selection.selecting && (
          <SelectButton ref={select} onClick={selection.start} title="Pick characters to make a cast (a saved group) of them" />
        )}
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
          <Button tone="quiet" size="sm" onClick={() => importer.current?.click()} disabled={importing.isPending}
                  title="A character, location, prop or style from a zip exported here or in another studio">
            <Upload className="size-4" aria-hidden />
            {importing.isPending ? `Importing… ${Math.round(sent * 100)}%` : "Import"}
          </Button>
          <input ref={importer} type="file" accept=".zip,application/zip" hidden
                 onChange={(ev) => {
                   const zip = ev.target.files?.[0];
                   ev.target.value = ""; // the same zip can be picked again
                   if (zip && zip.size > MAX_ZIP) toast.error("A character zip can be up to 2 GB");
                   else if (zip) {
                     setSent(0);
                     importing.mutate(zip, {
                       // shown in the list too, so the editor follows its changes (it reads the list's copy)
                       onSuccess: (e) => { toast(`Imported ${e.handle}`); setFilter("all"); setQ(""); setEditing(e); },
                       onError: (err) => toast.error(err instanceof Error ? err.message : "Couldn't import it"),
                     });
                   }
                 }} />
          <Button tone="primary" size="sm" onClick={() => setEditing("new")}>
            <Plus className="size-4" aria-hidden />
            {newKind ? `New ${KIND_LABEL[newKind].toLowerCase()}` : "New from images"}
          </Button>
        </div>
      </div>

      {selection.selecting && (
        <SelectionBar count={picked.length} selectable={characters.length}
                      onAll={() => selection.set(characters.map((c) => c.id))} onNone={() => selection.set([])}
                      onDone={selection.stop}>
          <Button size="sm" tone="primary" onClick={() => setMaking(true)} disabled={!castable}
                  title={castable ? "One handle that brings all of them" : `Pick ${CAST_SIZE[0]} to ${CAST_SIZE[1]} characters`}>
            <UsersRound className="size-4" aria-hidden />
            Make a cast
          </Button>
        </SelectionBar>
      )}

      {isLoading ? null : items.length === 0 ? (
        <div className="mt-20 max-w-md">
          <p className="type-title text-xl">
            {q ? "No matches" : filter === "cast" ? "No casts yet" : newKind ? `No ${KIND_LABEL[newKind].toLowerCase()}s yet` : "No characters yet"}
          </p>
          {filter === "cast" && !q ? (
            <p className="mt-2 text-sm leading-relaxed text-muted">
              A cast is a group of your characters under one handle, like <span className="text-chalk">@crew</span>: a
              prompt that mentions it brings all of them. Show your characters, select two to eight, then Make a cast.
              It&rsquo;s a group to reuse, not a film&rsquo;s cast in Cinema Studio.
            </p>
          ) : (
            <p className="mt-2 text-sm leading-relaxed text-muted">
              Save a person, place or prop once, then write <span className="text-chalk">@name</span> in any prompt.
              Models that take reference images get its pictures automatically; others get its description.
            </p>
          )}
        </div>
      ) : (
        <ul className="columns-[190px] gap-4">
          {items.map((e) => {
            const pickable = e.kind === "character";
            const on = selection.selecting && selection.picked.has(e.id);
            const cover = e.kind === "voice" ? null : e.kind === "cast" ? (e.members[0]?.cover ?? null) : e.cover;
            return (
              <li key={e.id} className={cn("group relative mb-4 break-inside-avoid", selection.selecting && !pickable && "opacity-40")}>
                {!selection.selecting && e.kind !== "voice" && e.kind !== "cast" && e.cover && (
                  <ZoomButton src={mediaUrl(e.cover.url)} title={e.name} className="right-4 top-4" />
                )}
                <button
                  type="button"
                  onClick={() => (selection.selecting ? selection.toggle(e.id) : setEditing(e))}
                  disabled={selection.selecting && !pickable}
                  role={selection.selecting && pickable ? "checkbox" : undefined}
                  aria-checked={selection.selecting && pickable ? on : undefined}
                  className={cn(GLASS_CARD, "relative block w-full text-left disabled:cursor-not-allowed",
                                on && "ring-2 ring-tungsten ring-offset-2 ring-offset-bay")}
                  aria-label={`${e.name}, ${KIND_LABEL[e.kind]}`}
                >
                  <Lit picture={cover ? mediaUrl(cover.thumb_url ?? cover.url) : undefined}>
                    <div className="overflow-hidden rounded-[var(--radius-frame)] bg-raised"
                         style={{ aspectRatio: e.kind === "cast" ? "1 / 1" : e.kind !== "voice" && e.cover?.width && e.cover.height
                           ? `${e.cover.width} / ${e.cover.height}` : "3 / 4" }}>
                      {e.kind === "voice" ? (
                        <span className="grid size-full place-items-center text-tungsten">
                          <AudioLines className="size-8" aria-hidden />
                        </span>
                      ) : e.kind === "cast" ? (
                        <CastCovers members={e.members} />
                      ) : e.cover ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={mediaUrl(e.cover.thumb_url ?? e.cover.url)} alt="" className="size-full object-cover transition-transform group-hover:scale-[1.02]" />
                      ) : (
                        <span className="grid size-full place-items-center text-xs text-faint">No images yet</span>
                      )}
                    </div>
                  </Lit>
                  {selection.selecting && pickable && (
                    <span className="absolute left-3 top-3">
                      <SelectMark selected={on} />
                    </span>
                  )}
                  <p className="type-title mt-2 truncate px-1 text-[15px]">{e.name}</p>
                  <p className="truncate px-1 pb-1 text-xs text-faint">
                    <span className="text-tungsten">{e.handle}</span>, {KIND_LABEL[e.kind].toLowerCase()}
                    {e.kind === "cast" ? <>, {e.members.length} character{e.members.length === 1 ? "" : "s"}</>
                      : e.kind !== "voice" && <>, {e.images.length} image{e.images.length === 1 ? "" : "s"}</>}
                  </p>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <ElementDialog element={open && open !== "new" && open.kind === "voice" ? null : open}
                     newKind={newKind ?? "character"} onClose={() => setEditing(null)} />
      <VoiceDialog voice={open && open !== "new" && open.kind === "voice" ? open : null}
                   onClose={() => setEditing(null)} />
      <MakeCastDialog members={picked} open={making}
                      onClose={() => {
                        setMaking(false);
                        // the dialog has closed (its focus back to a button that's gone): the page is live again
                        if (made.current) select.current?.focus();
                        made.current = false;
                      }}
                      onMade={() => { made.current = true; setMaking(false); selection.stop(); }} />
      <Dialog open={cloning} onClose={() => setCloning(false)} title="Add a voice">
        {cloning && <AddVoice onDone={() => setCloning(false)} />}
      </Dialog>
      <StartersDialog open={starting} onClose={() => setStarting(false)} design={design}
                      onDesign={() => { setStarting(false); setDesigner((n) => n + 1); setBuilding(true); }} />
      <TraitBuilder key={designer} open={building} onClose={() => setBuilding(false)} design={design} />
    </div>
  );
}
