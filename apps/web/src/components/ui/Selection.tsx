"use client";

import { Check, ListChecks, Trash2 } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/cn";
import { Button } from "./controls";

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
