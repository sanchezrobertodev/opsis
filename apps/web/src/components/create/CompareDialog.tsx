"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, type ModelSummary } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import type { CreateBody } from "@/lib/queries";
import { type RunCreate, useStartRun } from "@/lib/runs";
import { stylesRun } from "@/lib/styles";
import { quotePrice, quoteText, sameRequest, useRunQuote } from "@/components/runs/RunQuote";
import { Button, Dialog } from "@/components/ui/controls";
import { StyleGallery, useStyleTiles } from "./director/StylePicker";

/** The most takes a comparison makes side by side (the compare recipe's limit). */
const MAX = 6;

/** A comparison's dialog: it prices the run as it's put together, starts it (asking again when it costs more than
 * the confirmation threshold) and opens it. `action` is the start button's words, or what's missing while `run` is
 * null. */
function CompareRunDialog({ open, onClose, title, wide = true, run, action, children }: {
  open: boolean;
  onClose: () => void;
  title: string;
  wide?: boolean | "full";
  run: RunCreate | null;
  action: string;
  children: React.ReactNode;
}) {
  const router = useRouter();
  const start = useStartRun();
  const quote = useRunQuote(run);
  const [asked, setAsked] = useState<{ usd: number; run: RunCreate } | null>(null);
  // the price asked about is that run's: picks changed since, it's priced (and asked) again
  const confirm = asked && sameRequest(asked.run, run) ? asked.usd : null;

  async function go(confirmCost?: number) {
    if (!run) return;
    try {
      const started = await start.mutateAsync({ ...run, confirm_cost_usd: confirmCost ?? quote.charged });
      onClose();
      router.push(`/runs/${started.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setAsked({ usd: (err.body as { estimate_usd: number }).estimate_usd, run });
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start the comparison");
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      wide={wide}
      footer={
        confirm != null ? (
          <>
            <span className="mr-auto self-center text-xs text-chalk">Estimated at {usd(confirm)}.</span>
            <Button type="button" tone="ghost" onClick={() => setAsked(null)}>
              Cancel
            </Button>
            <Button type="button" tone="primary" onClick={() => void go(confirm)} disabled={start.isPending}>
              {action} for {usd(confirm)}
            </Button>
          </>
        ) : (
          <>
            {run && <span className="mr-auto self-center text-xs text-muted">{quoteText(quote, ["take", "takes"])}</span>}
            <Button type="button" tone="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="button" tone="primary" onClick={() => void go()}
                    disabled={!run || quote.count == null || Boolean(quote.error) || start.isPending}>
              {run ? `${action}${quotePrice(quote) && ` · ${quotePrice(quote)}`}` : action}
            </Button>
          </>
        )
      }
    >
      {children}
    </Dialog>
  );
}

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
  const current = models.find((m) => m.id === body.model_id);
  const same = models.filter((m) => m.output_type === current?.output_type && m.runnable);
  const [picked, setPicked] = useState<string[]>(() => [body.model_id]);

  function toggle(id: string) {
    if (id === body.model_id) return;
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < MAX ? [...p, id] : p));
  }

  const run: RunCreate | null = picked.length >= 2 ? {
    recipe: "compare",
    input: {
      prompt: body.prompt,
      // the current model keeps its settings and mode; the others use their defaults
      entries: picked.map((id) => (id === body.model_id ? { model_id: id, mode: body.mode, params: body.params } : { model_id: id })),
      media: body.media,
      direction: body.direction ?? null,
      ops,
      pick: true,
    },
  } : null;

  return (
    <CompareRunDialog open={open} onClose={onClose} title="Compare models" run={run}
                      action={run ? `Compare ${picked.length} models` : "Pick at least 2 models"}>
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
    </CompareRunDialog>
  );
}

/** The current take in several picture styles at once (your saved ones too), each on the same model, mode and
 * settings, then pick the one to keep. */
export function StylesDialog({ open, onClose, body, ops }: {
  open: boolean;
  onClose: () => void;
  body: CreateBody;
  ops: string[];
}) {
  const { catalog, mine } = useStyleTiles();
  const all = [...mine, ...catalog];
  const current = body.direction?.style;
  const [picked, setPicked] = useState<string[]>(() => all.filter((s) => s.id === current).map((s) => s.id));
  const chosen = picked.flatMap((id) => all.filter((s) => s.id === id)); // a style deleted meanwhile drops out
  const run = chosen.length >= 2 ? stylesRun(body, chosen, ops) : null;
  const toggle = (id: string) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < MAX ? [...p, id] : p));

  return (
    <CompareRunDialog open={open} onClose={onClose} title="Try in several styles" wide="full" run={run}
                      action={run ? `Try ${chosen.length} styles` : "Pick at least 2 styles"}>
      <p className="mb-4 leading-relaxed">
        The same prompt, pictures and settings in each style at once, each on the cheapest provider
        {body.negative_prompt ? " and without the Avoid text" : ""}. When they&rsquo;re done you pick the take to
        keep. Choose 2 to {MAX}.
      </p>
      <StyleGallery catalog={catalog} mine={mine} isOn={(id) => picked.includes(id)} onTile={toggle}
                    full={picked.length >= MAX} />
    </CompareRunDialog>
  );
}
