"use client";

import { useMemo } from "react";
import { cn } from "@/lib/cn";
import { useDraft } from "@/lib/draft";
import { useRegistry } from "@/lib/queries";
import { EDIT_OPS, modelsFor } from "@/lib/studioKinds";
import { CreateStudio } from "@/components/create/CreateStudio";

/** Edit video: pick what to do, then a model that does it. The source clip carries over between edits. */
export function EditStudio() {
  const { data: registry } = useRegistry();
  const op = useDraft((s) => s.edit.op);
  const setDraft = useDraft((s) => s.set);

  // Only offer edits some model in the registry can do.
  const available = useMemo(
    () => EDIT_OPS.filter((o) => modelsFor(registry?.models ?? [], "edit", [o.op]).length > 0),
    [registry],
  );
  const current = available.find((o) => o.op === op) ?? available[0];

  const header = (
    <div className="border-b border-line-soft p-4">
      <div role="radiogroup" aria-label="Edit" className="flex flex-wrap gap-1.5">
        {available.map((o) => (
          <button
            key={o.op}
            type="button"
            role="radio"
            aria-checked={o.op === current?.op}
            onClick={() => setDraft("edit", { op: o.op, mode: undefined })}
            className={cn(
              "rounded-full border px-3 py-1 text-xs transition-colors",
              o.op === current?.op
                ? "border-tungsten/70 bg-tungsten/10 text-chalk"
                : "border-line-soft text-muted hover:border-line hover:text-chalk",
            )}
          >
            {o.label}
          </button>
        ))}
      </div>
      {current && <p className="mt-3 text-xs leading-relaxed text-muted">{current.summary}</p>}
    </div>
  );

  return <CreateStudio studio="edit" ops={current ? [current.op] : undefined} header={header} />;
}
