"use client";

import { Copy, Pencil, Shirt, Trash2, X } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { mediaUrl } from "@/lib/api";
import { type Element, useDeleteLook, usePutLook, useRemoveElementImage } from "@/lib/elements";
import { hasPictures } from "@/lib/mentions";
import { Button } from "@/components/ui/controls";
import { OpensFullSize } from "@/components/ui/PictureViewer";

const FIELD = "h-9 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60";
const ICON = "grid size-7 shrink-0 place-items-center rounded-full text-muted hover:bg-raised hover:text-chalk";

/** A character's looks (`@handle:look`: another outfit, or the character in another picture style), each with its
 * pictures (any can be taken off), its handle to copy, and rename and delete; and the two ways to make one. `open`
 * is the editor's panel showing, if it's one of those. */
export function LooksSection({ element, open, onAdd, onStyle }: {
  element: Element; open?: string; onAdd: () => void; onStyle: () => void;
}) {
  const put = usePutLook();
  const remove = useDeleteLook();
  const removeImage = useRemoveElementImage();
  const [editing, setEditing] = useState<{ look: string; name: string; description: string } | null>(null);
  const back = useRef<string | null>(null); // the look whose Rename button gets focus back when its form closes
  const looks = Object.entries(element.looks);
  const drawable = hasPictures(element); // a look is drawn from what a plain mention attaches

  function close(look: string) {
    back.current = look;
    setEditing(null);
  }

  function save() {
    if (!editing) return;
    put.mutate({ id: element.id, look: editing.look, name: editing.name.trim(), description: editing.description.trim() }, {
      onSuccess: () => close(editing.look),
      onError: (err) => toast.error(err instanceof Error ? err.message : "Couldn't save the look"),
    });
  }

  return (
    <section aria-labelledby="looks-title" className="space-y-3 rounded-[var(--radius-control)] border border-line-soft p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="looks-title" className="mr-auto flex items-center gap-2 text-sm text-chalk">
          <Shirt className="size-4 text-tungsten" aria-hidden />
          Looks
        </h3>
        <Button type="button" size="sm" onClick={onAdd} disabled={!drawable} aria-expanded={open === "look"}
                title={drawable ? "Draws them in another outfit" : "Add a picture first"}>
          Add a look
        </Button>
        <Button type="button" size="sm" onClick={onStyle} disabled={!drawable} aria-expanded={open === "style"}
                title={drawable ? "Draws them in another picture style" : "Add a picture first"}>
          Another style
        </Button>
      </div>
      {looks.length === 0 ? (
        <p className="text-xs leading-relaxed text-muted">
          Other outfits, or {element.name} in another picture style. Write {element.handle}:look in a prompt to bring
          one; a plain {element.handle} stays as they are.
        </p>
      ) : (
        <ul className="space-y-3">
          {looks.map(([slug, look]) => {
            const handle = `${element.handle}:${slug}`;
            const pictures = element.images.filter((i) => i.look === slug);
            return (
              <li key={slug} className="space-y-2">
                {editing?.look === slug ? (
                  <form className="flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); save(); }}>
                    <input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                           maxLength={40} aria-label="Name of the look" autoFocus className={FIELD} />
                    <input value={editing.description} maxLength={160} aria-label="How prompts say it"
                           onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                           placeholder="e.g. wearing a red wool coat and boots" className={FIELD} />
                    <div className="flex gap-2">
                      <Button type="submit" tone="primary" size="sm" disabled={!editing.name.trim() || put.isPending}>
                        Save
                      </Button>
                      <Button type="button" tone="ghost" size="sm" onClick={() => close(slug)}>Cancel</Button>
                    </div>
                  </form>
                ) : (
                  <div className="flex items-start gap-1">
                    <span className="mr-2 min-w-0 flex-1">
                      <span className="block truncate text-sm text-chalk">{look.name}</span>
                      <span className="block truncate text-2xs text-faint">
                        <span className="text-tungsten">{handle}</span>, {pictures.length} picture{pictures.length === 1 ? "" : "s"}
                      </span>
                      {look.description && <span className="block truncate text-2xs text-faint">{look.description}</span>}
                    </span>
                    <button type="button" aria-label={`Copy ${handle}`} className={ICON}
                            onClick={() => void navigator.clipboard.writeText(handle).then(() => toast("Copied"))}>
                      <Copy className="size-3.5" aria-hidden />
                    </button>
                    <button type="button" aria-label={`Rename the ${look.name} look`} className={ICON}
                            ref={(el) => { if (el && back.current === slug) { back.current = null; el.focus(); } }}
                            onClick={() => setEditing({ look: slug, name: look.name, description: look.description })}>
                      <Pencil className="size-3.5" aria-hidden />
                    </button>
                    <button type="button" aria-label={`Delete the ${look.name} look`} className={ICON}
                            disabled={remove.isPending}
                            onClick={() => remove.mutate({ id: element.id, look: slug }, {
                              onSuccess: () => toast(`Deleted the ${look.name} look. Its pictures stay in your library.`),
                              onError: (err) => toast.error(err instanceof Error ? err.message : "Couldn't delete it"),
                            })}>
                      <Trash2 className="size-3.5" aria-hidden />
                    </button>
                  </div>
                )}
                {/* every picture a mention of the look attaches first, so a bad one can be taken off on its own */}
                {pictures.length > 0 && <ul className="flex flex-wrap gap-1.5" aria-label={`${look.name} pictures`}>
                  {pictures.map((p, n) => (
                    <li key={p.asset.id} className="group relative size-12 overflow-hidden rounded-[4px] bg-bay">
                      <OpensFullSize src={mediaUrl(p.asset.url) ?? ""} title={`${element.name}, ${look.name}`} className="size-full">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={mediaUrl(p.asset.thumb_url ?? p.asset.url)} alt="" className="size-full object-cover" />
                      </OpensFullSize>
                      <button type="button" aria-label={`Remove ${look.name} picture ${n + 1}`}
                              disabled={removeImage.isPending}
                              onClick={() => removeImage.mutate({ id: element.id, assetId: p.asset.id }, {
                                onError: (err) => toast.error(err instanceof Error ? err.message : "Couldn't remove it"),
                              })}
                              className="absolute right-0.5 top-0.5 grid size-5 place-items-center rounded-full bg-bay/85 text-chalk opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100">
                        <X className="size-3" aria-hidden />
                      </button>
                    </li>
                  ))}
                </ul>}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
