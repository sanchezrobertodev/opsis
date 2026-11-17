"use client";

import { Check } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type StarterCharacter, useCharacterCatalog } from "@/lib/characters";
import { describeCharacter } from "@/lib/designer";
import { useCreateElement, useElements } from "@/lib/elements";
import { uploadFile } from "@/lib/queries";
import { type Starter, STARTERS } from "@/lib/starters";
import { Button, Dialog } from "@/components/ui/controls";
import { OpensFullSize } from "@/components/ui/PictureViewer";
import type { Design } from "./TraitBuilder";

/** A picture in `public/`, uploaded as yours: an Element's pictures are assets. */
async function uploadPicture(path: string, what: string) {
  const blob = await fetch(path).then((r) => {
    if (!r.ok) throw new Error(`The picture for ${what} is missing`);
    return r.blob();
  });
  return uploadFile(new File([blob], path.split("/").pop() ?? "starter.webp", { type: "image/webp" }));
}

/** Ready-made characters to add as they are or to open in the designer, and the built-in locations you don't have
 * yet. `onDesign`: a starter's picks were loaded into `design`. */
export function StartersDialog({ open, onClose, design, onDesign }: {
  open: boolean; onClose: () => void; design: Design; onDesign: () => void;
}) {
  return (
    <Dialog open={open} onClose={onClose} title="Starters" wide>
      {open && <StarterList design={design} onDesign={onDesign} />}
    </Dialog>
  );
}

function StarterList({ design, onDesign }: { design: Design; onDesign: () => void }) {
  const { data: catalog, error } = useCharacterCatalog();
  const { data: elements } = useElements();
  const create = useCreateElement();
  const [adding, setAdding] = useState<string | null>(null);
  const has = (kind: string, name: string) => (elements ?? []).some((e) => e.kind === kind && e.name === name);
  const locations = STARTERS.filter((s) => !has(s.kind, s.name));

  /** The starter as a character: its portrait as the first picture, its picks (style included) and its voice. */
  async function add(s: StarterCharacter) {
    if (!catalog) return;
    setAdding(s.id);
    try {
      const portrait = await uploadPicture(`/starters/characters/${s.id}.webp`, s.name);
      const e = await create.mutateAsync({
        kind: "character", name: s.name, description: describeCharacter(catalog, s.traits, ""),
        attributes: { traits: { ...s.traits, style: s.style } },
        images: [{ asset_id: portrait.id, view: "front" }],
        voice: { model_id: catalog.voice_model, params: { voice_id: s.voice_id } },
      });
      toast(`Added ${e.handle}. Write it in any prompt.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `Couldn't add ${s.name}`);
    } finally {
      setAdding(null);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="starter-characters" className="flex flex-col gap-3">
        <div>
          <h3 id="starter-characters" className="text-sm text-chalk">Characters</h3>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            Each comes with a portrait, a voice and its designer traits. Add one as it is, or open it in the designer
            to change it first.
          </p>
        </div>
        {!catalog ? (
          <p className="text-xs">{error ? `The starters didn't load: ${error.message}` : "Loading…"}</p>
        ) : (
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-3">
            {catalog.starters.map((s) => {
              const added = has("character", s.name);
              const src = `/starters/characters/${s.id}.webp`;
              return (
                <li key={s.id} className="flex flex-col gap-2 rounded-[var(--radius-control)] border border-line-soft p-1.5">
                  <OpensFullSize src={src} title={s.name} className="overflow-hidden rounded-[3px] bg-bay">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={src} alt="" loading="lazy" className="aspect-square w-full object-cover" />
                  </OpensFullSize>
                  <div className="px-0.5">
                    <p className="text-sm text-chalk">{s.name}</p>
                    <p className="text-2xs leading-snug text-faint">{s.summary}</p>
                  </div>
                  <div className="mt-auto flex flex-wrap gap-1">
                    <Button type="button" size="sm" tone={added ? "ghost" : "primary"} disabled={added || adding !== null}
                            aria-label={added ? `${s.name} is added` : `Add ${s.name}`} onClick={() => void add(s)}>
                      {added ? <><Check className="size-3.5" aria-hidden />Added</> : adding === s.id ? "Adding…" : "Add"}
                    </Button>
                    <Button type="button" size="sm" tone="ghost" aria-label={`Open in designer: ${s.name}`}
                            onClick={() => {
                              design.load(catalog, { ...s.traits, style: s.style }, s.name);
                              onDesign();
                            }}>
                      Open in designer
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
      {locations.length > 0 && (
        <section aria-labelledby="starter-locations" className="flex flex-col gap-3">
          <h3 id="starter-locations" className="text-sm text-chalk">Locations</h3>
          <AddLocations starters={locations} />
        </section>
      )}
    </div>
  );
}

/** Adds the built-in locations you don't have yet, each with its picture. */
function AddLocations({ starters }: { starters: Starter[] }) {
  const create = useCreateElement();
  const [busy, setBusy] = useState(false);

  async function add() {
    setBusy(true);
    try {
      for (const s of starters) {
        const asset = await uploadPicture(`/starters/${s.file}.webp`, s.name);
        await create.mutateAsync({ kind: s.kind, name: s.name, description: s.description, attributes: {},
                                   images: [{ asset_id: asset.id, view: "front" }] });
      }
      toast(`Added ${starters.map((s) => s.name).join(", ")}. Write their @handles in any prompt.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't add the locations");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-[var(--radius-panel)] bg-panel p-4">
      <ul className="flex flex-wrap gap-2">
        {starters.map((s) => (
          <li key={s.file}>
            <OpensFullSize src={`/starters/${s.file}.webp`} title={s.name}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`/starters/${s.file}.webp`} alt="" className="h-12 w-20 rounded-[var(--radius-frame)] object-cover" />
            </OpensFullSize>
          </li>
        ))}
      </ul>
      <p className="min-w-48 flex-1 text-xs leading-relaxed text-muted">
        Start with {starters.length} ready-made locations ({starters.map((s) => s.name).join(", ")}). Write one as an
        @handle in any prompt, like a character.
      </p>
      <Button size="sm" onClick={() => void add()} disabled={busy}>
        {busy ? "Adding…" : "Add locations"}
      </Button>
    </div>
  );
}
