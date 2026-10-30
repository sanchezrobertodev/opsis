"use client";

import type { ModelSummary } from "@/lib/api";
import { usd } from "@/lib/cn";
import { useDebounced } from "@/lib/hooks";
import { useRegistry } from "@/lib/queries";
import { type RunCreate, useEstimateRun } from "@/lib/runs";
import { Field, Select } from "@/components/ui/controls";

export type Quote = { usd: number | null; charged: number | null; count: number | null; mock: boolean; error: string | null };

/** What a run will make and cost before it starts: the price where it would run now (a free route is free), or in
 * mock mode what it would cost live, and how many generations it makes. Start it with `confirm_cost_usd: charged`:
 * the button showed the price, so pressing it confirms the spend. */
export function useRunQuote(body: RunCreate | null): Quote {
  const mock = useRegistry().data?.provider_mode === "mock";
  const asked = useDebounced(body, 250);
  const estimate = useEstimateRun(asked);
  // only the answer for what's on screen now: not while a change waits to be asked or the old answer stands in
  const current = body !== null && sameRequest(asked, body) && !estimate.isPlaceholderData;
  const d = current ? estimate.data : undefined;
  return {
    usd: d ? ((mock ? d.live_estimate_usd : d.estimate_usd) ?? null) : null,
    charged: d?.estimate_usd ?? null,
    count: d?.generations ?? null,
    mock,
    error: current && estimate.error instanceof Error ? estimate.error.message : null,
  };
}

/** Whether two requests are the same, field for field (they're rebuilt on every render). */
export const sameRequest = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** "8 pictures, about $1.20"; `noun` is [one, many]. */
export function quoteText(q: Quote, noun: [string, string]): string {
  if (q.error) return q.error;
  if (q.count == null || q.usd == null) return "Working out the cost…";
  const what = `${q.count} ${q.count === 1 ? noun[0] : noun[1]}`;
  if (!q.usd) return `${what}, free on this route`;
  return `${what}, about ${usd(q.usd)}${q.mock ? " when live" : ""}`;
}

/** The price for a button's label: "$1.20", "free", or "" until it's known. */
export function quotePrice(q: Quote): string {
  if (q.usd == null || q.error) return "";
  return q.usd ? usd(q.usd) : "free";
}

/** The model a button makes things with: the recommended one first and marked, and what picking another gives up. */
export function ModelSelect({ id, label = "Model", models, recommended, value, onChange, tradeoff }: {
  id: string;
  label?: string;
  models: ModelSummary[];
  recommended: string;
  value: string;
  onChange: (id: string) => void;
  tradeoff?: string;
}) {
  const options = models
    .map((m) => ({ value: m.id, label: m.id === recommended ? `${m.name} (recommended)` : m.name }))
    .sort((a, b) => Number(b.value === recommended) - Number(a.value === recommended));
  return (
    <Field label={label} htmlFor={id} hint={value !== recommended ? tradeoff : undefined}>
      <Select id={id} value={value} onChange={onChange} options={options} />
    </Field>
  );
}
