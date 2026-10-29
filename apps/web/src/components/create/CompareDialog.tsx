"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, type ModelSummary } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import type { CreateBody } from "@/lib/queries";
import { useStartRun } from "@/lib/runs";
import { Button, Dialog } from "@/components/ui/controls";

const MAX = 4;

/** Run the current prompt, media and direction on several models at once, then pick the best take. */
export function CompareDialog({
  open,
  onClose,
  body,
  models,
  ops,
}: {
  open: boolean;
  onClose: () => void;
  body: CreateBody;
  models: ModelSummary[];
  /** The current mode's ops: other models use their mode that does the same thing. */
  ops: string[];
}) {
  const router = useRouter();
  const start = useStartRun();
  const current = models.find((m) => m.id === body.model_id);
  const same = models.filter((m) => m.output_type === current?.output_type && m.runnable);
  const [picked, setPicked] = useState<string[]>(() => [body.model_id]);
  const [confirm, setConfirm] = useState<number | null>(null);

  function toggle(id: string) {
    if (id === body.model_id) return;
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < MAX ? [...p, id] : p));
  }

  async function go(confirmCost?: number) {
    try {
      const run = await start.mutateAsync({
        recipe: "compare",
        confirm_cost_usd: confirmCost ?? null,
        input: {
          prompt: body.prompt,
          // the current model keeps its settings and mode; the others use their defaults
          entries: picked.map((id) => (id === body.model_id ? { model_id: id, mode: body.mode, params: body.params } : { model_id: id })),
          media: body.media,
          direction: body.direction ?? null,
          ops,
          pick: true,
        },
      });
      onClose();
      router.push(`/runs/${run.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConfirm((err.body as { estimate_usd: number }).estimate_usd);
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start the comparison");
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Compare models"
      wide
      footer={
        confirm != null ? (
          <>
            <span className="mr-auto self-center text-xs text-chalk">Estimated at {usd(confirm)}.</span>
            <Button type="button" tone="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button type="button" tone="primary" onClick={() => void go(confirm)} disabled={start.isPending}>
              Compare for {usd(confirm)}
            </Button>
          </>
        ) : (
          <>
            <Button type="button" tone="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="button" tone="primary" onClick={() => void go()} disabled={picked.length < 2 || start.isPending}>
              {picked.length < 2 ? "Pick at least 2 models" : `Compare ${picked.length} models`}
            </Button>
          </>
        )
      }
    >
      <p className="mb-4 leading-relaxed">
        The same prompt, media and camera settings run on each model at once. When they&rsquo;re done you pick
        the take to keep. Choose up to {MAX}.
      </p>
      <ul className="grid gap-2 sm:grid-cols-2">
        {same.map((m) => {
          const on = picked.includes(m.id);
          return (
            <li key={m.id}>
              <button
                type="button"
                onClick={() => toggle(m.id)}
                aria-pressed={on}
                disabled={m.id === body.model_id}
                className={cn(
                  "flex w-full items-baseline justify-between gap-3 rounded-[var(--radius-control)] border p-3 text-left",
                  on ? "border-tungsten/70 bg-raised" : "border-line-soft hover:border-line",
                )}
              >
                <span className="text-sm text-chalk">{m.name}</span>
                <span className="tabular text-2xs text-faint">
                  {m.id === body.model_id ? "current" : m.tags.includes("local") ? "free on this computer" : m.from_price_usd != null ? `from ${usd(m.from_price_usd)}` : ""}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}
