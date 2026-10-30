"use client";

import { Check, ListChecks, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/cn";
import type { Removal } from "@/lib/queries";
import { Button, Dialog } from "./controls";

/** Picking several items of a grid at once (to delete them together). */
export function useSelection() {
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  return {
    selecting,
    picked,
    start: () => setSelecting(true),
    stop: () => {
      setSelecting(false);
      setPicked(new Set());
    },
    toggle: (id: string) =>
      setPicked((was) => {
        const next = new Set(was);
        if (!next.delete(id)) next.add(id);
        return next;
      }),
    set: (ids: string[]) => setPicked(new Set(ids)),
  };
}

export function SelectButton({ onClick }: { onClick: () => void }) {
  return (
    <Button size="sm" tone="ghost" onClick={onClick}>
      <ListChecks className="size-4" aria-hidden />
      Select
    </Button>
  );
}

/** Sits at the top of the grid while selecting: how many, all/none, delete, done. */
export function SelectionBar({
  count,
  selectable,
  more = false,
  onAll,
  onNone,
  onDelete,
  onDone,
}: {
  count: number;
  selectable: number;
  /** the grid shows only the newest page, so "all" means all of those */
  more?: boolean;
  onAll: () => void;
  onNone: () => void;
  onDelete: () => void;
  onDone: () => void;
}) {
  return (
    <div
      role="toolbar"
      aria-label="Selection"
      className="glass sticky top-0 z-20 mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-line-soft px-3 py-2"
    >
      <span className="tabular mr-1 text-sm" aria-live="polite">
        {count} selected
      </span>
      {count < selectable ? (
        <Button size="sm" tone="ghost" onClick={onAll}>
          {more ? `Select all ${selectable} shown` : "Select all"}
        </Button>
      ) : (
        <Button size="sm" tone="ghost" onClick={onNone}>
          Select none
        </Button>
      )}
      <Button size="sm" tone="danger" onClick={onDelete} disabled={count === 0}>
        <Trash2 className="size-4" aria-hidden />
        Delete
      </Button>
      <Button size="sm" tone="ghost" className="ml-auto" onClick={onDone}>
        Done
      </Button>
    </div>
  );
}

/** The check in an item's corner while selecting. */
export function SelectMark({ selected }: { selected: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-6 place-items-center rounded-md border shadow-sm transition-colors",
        selected ? "border-tungsten bg-tungsten text-bay" : "border-chalk/60 bg-black/40 text-transparent",
      )}
    >
      <Check className="size-4" strokeWidth={3} />
    </span>
  );
}

/** Asks before deleting what's selected, which can't be undone. `noun` is [one, many]. */
export function ConfirmDelete({ open, count, noun, pending, onConfirm, onClose, children }: {
  open: boolean;
  count: number;
  noun: [string, string];
  pending: boolean;
  onConfirm: () => void;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`Delete ${count} ${count === 1 ? noun[0] : noun[1]}?`}
      footer={
        <>
          <Button tone="ghost" onClick={onClose}>
            {count === 1 ? "Keep it" : "Keep them"}
          </Button>
          <Button tone="danger" onClick={onConfirm} disabled={pending || count === 0}>
            {pending ? "Deleting…" : `Delete ${count}`}
          </Button>
        </>
      }
    >
      {children}
    </Dialog>
  );
}

export function reportRemoval(r: Removal, noun: [string, string]) {
  if (r.deleted) toast(`Deleted ${r.deleted} ${r.deleted === 1 ? noun[0] : noun[1]}`);
  if (r.kept.length) toast(r.kept.join(" "));
  if (r.failed.length) toast.error(`${r.failed.length} couldn't be deleted: ${r.failed[0]}`);
}

/** A card that opens its page, or is picked while selecting. */
export function PickableLink({ href, selecting, selected, onToggle, className, children }: {
  href: string;
  selecting: boolean;
  selected: boolean;
  onToggle: () => void;
  className: string;
  children: React.ReactNode;
}) {
  if (!selecting) {
    return (
      <Link href={href} className={className}>
        {children}
      </Link>
    );
  }
  return (
    <button type="button" onClick={onToggle} aria-pressed={selected}
            className={cn(className, "relative w-full text-left", selected && "ring-2 ring-tungsten")}>
      <span className="absolute left-2 top-2 z-10">
        <SelectMark selected={selected} />
      </span>
      {children}
    </button>
  );
}
