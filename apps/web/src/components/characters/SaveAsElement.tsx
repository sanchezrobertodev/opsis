"use client";

import { useRef, useState } from "react";
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
import { claudeSummary } from "@/lib/styles";
import { DescribeStyleButton, LOOK_MAX } from "@/components/create/director/StylePicker";
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

/** The description in the trait builder's portrait prompt; any other prompt names a whole take, so none. */
function descriptionFrom(prompt: string | undefined): string {
  const m = /character portrait of (.+?)\. (?:Waist-up|The whole body)/i.exec(prompt ?? "");
  return m ? m[1].slice(0, 600) : "";
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
  // Claude's answer for a style: its gallery category, its look and the gallery line for that look
  const [style, setStyle] = useState<{ category: string; look: string; summary: string } | null>(null);
  const typed = useRef({ name: false, description: false }); // Claude's answer leaves what you wrote
  const busy = create.isPending || addImages.isPending;
  // a style's picture shows its look, not an angle
  const styled = (target === "new" ? kind : elements?.find((e) => e.id === target)?.kind) === "style";
  const shows: ElementView = styled ? "style" : view;
  const tooLong = kind === "style" && description.length > LOOK_MAX; // a longer look is cut short in prompts

  function changeKind(k: ElementKind) {
    setKind(k);
    // the prompt names the take's subjects, not a look: a style starts from Claude's look or nothing, the others from
    // the designer's description in the prompt or nothing, unless you've written your own
    if (!(typed.current.description && description.trim())) {
      setDescription(k === "style" ? (style?.look ?? "") : descriptionFrom(prompt));
    }
  }

  async function save() {
    try {
      if (target === "new") {
        const e = await create.mutateAsync({
          name: name.trim(), kind, description: description.trim(),
          // the gallery line only while the look is still the one it was written for
          attributes: kind === "style" && style
            ? { style: { category: style.category, summary: claudeSummary(description, style) } } : {},
          images: [{ asset_id: asset.id, view: shows }],
        });
        toast(`Saved as ${e.handle}. Use it in any prompt.`);
      } else {
        const e = await addImages.mutateAsync({ id: target, images: [{ asset_id: asset.id, view: shows }] });
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
            {(elements ?? []).filter((e) => e.kind !== "voice").map((e) => (
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
            <input value={name} onChange={(e) => { typed.current.name = true; setName(e.target.value); }} maxLength={80}
                   autoFocus placeholder="e.g. Maya Ortiz" className={`h-10 ${field}`} />
          </label>
          <Segmented label="Type" value={kind} options={["character", "location", "prop", "style"] as const}
                     render={(k) => KIND_LABEL[k]} onChange={changeKind} />
          {kind === "style" && (
            <DescribeStyleButton assetIds={[asset.id]} onAnswer={(r) => {
              setName((n) => (typed.current.name && n.trim() ? n : r.name));
              setDescription((d) => (typed.current.description && d.trim() ? d : r.look));
              setStyle({ category: r.category, look: r.look, summary: r.summary });
            }} />
          )}
          <label className="flex flex-col gap-1.5 text-xs text-muted">
            {kind === "style" ? (
              <span className="flex items-baseline justify-between">
                Look
                <span className={`readout text-2xs ${tooLong ? "text-fault" : "text-faint"}`}>{description.length} / {LOOK_MAX}</span>
              </span>
            ) : "Description"}
            <textarea value={description} maxLength={kind === "style" ? LOOK_MAX : 600} rows={3}
                      onChange={(e) => { typed.current.description = true; setDescription(e.target.value); }}
                      placeholder={kind === "style" ? "The look only: medium, palette, light, line, texture, era."
                        : "What makes them recognisable."}
                      className={`resize-y py-2 leading-relaxed ${field}`} />
          </label>
        </>
      )}

      {!styled && (
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
      )}

      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" tone="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="button" tone="primary" onClick={() => void save()} disabled={busy || (target === "new" && (!name.trim() || tooLong))}>
          {target === "new" ? `Save ${KIND_LABEL[kind].toLowerCase()}` : "Add image"}
        </Button>
      </div>
    </div>
  );
}
