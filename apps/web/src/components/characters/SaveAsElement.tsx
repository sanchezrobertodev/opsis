"use client";

import { useState } from "react";
import { toast } from "sonner";
import { type Asset, mediaUrl } from "@/lib/api";
import {
  type ElementKind,
  type ElementView,
  KIND_LABEL,
  useAddElementImages,
  useCreateElement,
  useElements,
} from "@/lib/elements";
import { Button, Dialog, Segmented } from "@/components/ui/controls";

const VIEWS: { value: ElementView; label: string }[] = [
  { value: "front", label: "Front" },
  { value: "three_quarter", label: "Three-quarter" },
  { value: "side", label: "Side" },
  { value: "back", label: "Back" },
  { value: "face", label: "Face close-up" },
  { value: "full_body", label: "Full body" },
  { value: "sheet", label: "Character sheet" },
  { value: "wide", label: "Wide (locations)" },
  { value: "detail", label: "Detail" },
];

/** The trait builder's portrait prompt carries the description; anything else is offered as-is. */
function descriptionFrom(prompt: string | undefined): string {
  if (!prompt) return "";
  const m = /character portrait of (.+?)\. Waist-up/.exec(prompt);
  return (m ? m[1] : prompt).slice(0, 600);
}

export function SaveAsElement({
  asset,
  prompt,
  open,
  onClose,
}: {
  asset: Asset;
  prompt?: string;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Dialog open={open} onClose={onClose} title="Save to Characters">
      {open && <SaveForm asset={asset} prompt={prompt} onDone={onClose} />}
    </Dialog>
  );
}

function SaveForm({ asset, prompt, onDone }: { asset: Asset; prompt?: string; onDone: () => void }) {
  const { data: elements } = useElements();
  const create = useCreateElement();
  const addImages = useAddElementImages();
  const [target, setTarget] = useState<string>("new");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ElementKind>("character");
  const [description, setDescription] = useState(() => descriptionFrom(prompt));
  const [view, setView] = useState<ElementView>("front");
  const busy = create.isPending || addImages.isPending;

  async function save() {
    try {
      if (target === "new") {
        const e = await create.mutateAsync({
          name: name.trim(), kind, description: description.trim(), attributes: {},
          images: [{ asset_id: asset.id, view }],
        });
        toast(`Saved as ${e.handle}. Use it in any prompt.`);
      } else {
        const e = await addImages.mutateAsync({ id: target, images: [{ asset_id: asset.id, view }] });
        toast(`Added to ${e.name}`);
      }
      onDone();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save");
    }
  }

  const field =
    "rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-4">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={mediaUrl(asset.thumb_url ?? asset.url)} alt="" className="size-24 shrink-0 rounded-[var(--radius-frame)] object-cover" />
        <label className="flex flex-1 flex-col gap-1.5 text-xs text-muted">
          Save to
          <select value={target} onChange={(e) => setTarget(e.target.value)} className={`h-10 ${field}`}>
            <option value="new">A new character, location or prop</option>
            {(elements ?? []).map((e) => (
              <option key={e.id} value={e.id}>
                {e.name} ({KIND_LABEL[e.kind].toLowerCase()}, {e.images.length} image{e.images.length === 1 ? "" : "s"})
              </option>
            ))}
          </select>
        </label>
      </div>

      {target === "new" && (
        <>
          <label className="flex flex-col gap-1.5 text-xs text-muted">
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoFocus
                   placeholder="e.g. Maya Ortiz" className={`h-10 ${field}`} />
          </label>
          <Segmented label="Type" value={kind} options={["character", "location", "prop", "style"] as const}
                     render={(k) => KIND_LABEL[k]} onChange={setKind} />
          <label className="flex flex-col gap-1.5 text-xs text-muted">
            Description
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} maxLength={600} rows={3}
                      placeholder="What makes them recognisable." className={`resize-y py-2 leading-relaxed ${field}`} />
          </label>
        </>
      )}

      <label className="flex flex-col gap-1.5 text-xs text-muted">
        This image shows
        <select value={view} onChange={(e) => setView(e.target.value as ElementView)} className={`h-10 ${field}`}>
          {VIEWS.map((v) => (
            <option key={v.value} value={v.value}>
              {v.label}
            </option>
          ))}
        </select>
      </label>

      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" tone="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="button" tone="primary" onClick={() => void save()} disabled={busy || (target === "new" && !name.trim())}>
          {target === "new" ? "Save character" : "Add image"}
        </Button>
      </div>
    </div>
  );
}
