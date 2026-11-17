"use client";

import { Plus, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { type Element, useCreateElement, useElements } from "@/lib/elements";
import { Button, Dialog } from "@/components/ui/controls";
import { useReferenceShot } from "./MakePanels";

// A cast is a saved group of characters with one handle (`@crew`), not a film's cast in Cinema Studio.

/** How many characters a cast holds (the server's CAST_SIZE). */
export const CAST_SIZE = [2, 8] as const;

type Member = Element["members"][number];

const listed = (names: string[]) => (names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0] ?? "");

/** A cast's card picture: its characters' covers, four at most. */
export function CastCovers({ members }: { members: Member[] }) {
  const shown = members.slice(0, 4);
  if (!shown.length) return <span className="grid size-full place-items-center text-xs text-faint">No characters left</span>;
  return (
    <span className={cn("grid size-full gap-0.5", shown.length > 1 && "grid-cols-2")}>
      {shown.map((m, i) => (
        <span key={m.id} className={cn("relative overflow-hidden bg-bay", i === 0 && shown.length === 3 && "row-span-2")}>
          {m.cover && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={mediaUrl(m.cover.thumb_url ?? m.cover.url)} alt="" className="size-full object-cover" />
          )}
          {i === 3 && members.length > 4 && (
            <span className="absolute inset-0 grid place-items-center bg-black/55 text-sm text-chalk">+{members.length - 4}</span>
          )}
        </span>
      ))}
    </span>
  );
}

/** A cast's characters in order: take one out, or add another of yours at the end. */
export function CastMembers({ value, onChange, known }: { value: string[]; onChange: (ids: string[]) => void; known: Member[] }) {
  const { data: characters } = useElements("character");
  const byId = new Map([...known, ...(characters ?? []).map((c) => ({ id: c.id, name: c.name, handle: c.handle, cover: c.cover }))]
    .map((m) => [m.id, m]));
  const others = (characters ?? []).filter((c) => !value.includes(c.id));
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">
        Characters <span className="text-faint">· {CAST_SIZE[0]} to {CAST_SIZE[1]}, in this order</span>
      </p>
      <ul className="space-y-1.5">
        {value.map((memberId) => {
          const m = byId.get(memberId);
          return (
            <li key={memberId} className="flex items-center gap-3 rounded-[var(--radius-control)] border border-line-soft p-1.5 pr-2">
              <span className="size-10 shrink-0 overflow-hidden rounded-[4px] bg-bay">
                {m?.cover && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={mediaUrl(m.cover.thumb_url ?? m.cover.url)} alt="" className="size-full object-cover" />
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-chalk">{m?.name ?? "A character"}</span>
                <span className="block truncate text-2xs text-tungsten">{m?.handle}</span>
              </span>
              <button type="button" aria-label={`Take ${m?.name ?? "them"} out of the cast`}
                      onClick={() => onChange(value.filter((x) => x !== memberId))}
                      className="grid size-7 place-items-center rounded-full text-muted hover:bg-raised hover:text-chalk">
                <X className="size-3.5" aria-hidden />
              </button>
            </li>
          );
        })}
      </ul>
      {value.length < CAST_SIZE[1] && others.length > 0 && (
        // buttons, not a select: a closed select's arrow keys would add every character they pass
        <fieldset className="flex flex-wrap gap-1.5">
          <legend className="mb-1.5 text-xs text-muted">Add a character</legend>
          {others.map((c) => (
            <button key={c.id} type="button" onClick={() => onChange([...value, c.id])} title={c.handle}
                    className="inline-flex h-8 items-center gap-1 rounded-full border border-line-soft px-3 text-xs text-muted transition-colors hover:text-chalk">
              <Plus className="size-3.5" aria-hidden />
              {c.name}
            </button>
          ))}
        </fieldset>
      )}
    </div>
  );
}

/** Names a new cast of the picked characters (in the order they were picked). */
export function MakeCastDialog({ members, open, onClose, onMade }: {
  members: Element[]; open: boolean; onClose: () => void; onMade: () => void;
}) {
  return (
    <Dialog open={open} onClose={onClose} title="Make a cast">
      {open && <MakeCastForm members={members} onCancel={onClose} onMade={onMade} />}
    </Dialog>
  );
}

function MakeCastForm({ members, onCancel, onMade }: { members: Element[]; onCancel: () => void; onMade: () => void }) {
  const create = useCreateElement();
  const [name, setName] = useState("");
  const who = listed(members.map((m) => m.name));
  function make() {
    create.mutate({ kind: "cast", name: name.trim(), description: "", attributes: {}, images: [],
                    members: members.map((m) => m.id) }, {
      onSuccess: (e) => {
        toast(`Made ${e.handle}. Write it in a prompt and ${who} come along.`);
        onMade();
      },
    }); // a refusal shows below
  }
  return (
    <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); make(); }}>
      <p className="leading-relaxed">
        {who} under one handle: a prompt that mentions it names each of them, and models that take pictures get a
        picture or two of each. A group to reuse, not a film&rsquo;s cast in Cinema Studio.
      </p>
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="e.g. The crew"
               data-autofocus
               className="h-10 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60" />
      </label>
      {create.error && <p role="alert" className="text-xs leading-relaxed text-fault">{create.error.message}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" tone="ghost" onClick={onCancel}>Cancel</Button>
        <Button type="submit" tone="primary" disabled={!name.trim() || create.isPending}>
          {create.isPending ? "Making…" : "Make the cast"}
        </Button>
      </div>
    </form>
  );
}

/** Opens Create image with a group shot of the cast drafted in a reference mode, so its characters' pictures come
 * along with the mention. */
export function useGroupShot() {
  const shot = useReferenceShot();
  return (cast: Pick<Element, "handle">) =>
    shot.open(`Group shot of ${cast.handle}, standing side by side and facing the camera.`,
              `Their pictures come along with ${cast.handle}. Describe the scene, then generate.`);
}
