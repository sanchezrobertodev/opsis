"use client";

import { ExternalLink } from "lucide-react";
import type { Schemas } from "@studio/api-client";
import { cn, usd } from "@/lib/cn";
import { useBalances, useBudget } from "@/lib/queries";

type Candidate = Schemas["CandidateOut"];
type Balance = Schemas["BalanceOut"];
type Budget = Schemas["BudgetOut"];

type Verdict = { tone: "ok" | "warn" | "stop" | "unknown"; text: string };

/** Can this run be paid for: the provider's balance where it reports one, then today's and this month's limits. */
export function verdict(cost: number, balance: Balance | undefined, budget: Budget | undefined, provider: string): Verdict {
  if (budget && !budget.live_enabled) return { tone: "stop", text: "Paid generations are switched off in Settings." };
  if (balance?.status === "ok" && balance.usd != null && cost > balance.usd) {
    return { tone: "stop", text: `Not enough on ${provider}: ${usd(balance.usd)} left, this needs ${usd(cost)}.` };
  }
  if (budget) {
    const month = budget.monthly_cap_usd - budget.month_usd - budget.reserved_usd;
    const today = budget.daily_cap_usd - budget.today_usd - budget.reserved_usd;
    if (cost > month) return { tone: "stop", text: `Over this month's limit: ${usd(Math.max(0, month))} left.` };
    if (cost > today) {
      return { tone: "warn", text: `Over today's limit (${usd(Math.max(0, today))} left), so it waits until tomorrow.` };
    }
  }
  if (balance?.status === "ok" && balance.usd != null) return { tone: "ok", text: "You can afford this." };
  return { tone: "unknown", text: `Within your limits. ${provider} doesn't report a balance here, so check it's topped up.` };
}

const TONE = { ok: "bg-done", warn: "bg-tungsten", stop: "bg-fault", unknown: "bg-faint" };

/** The cost of a live run, where it runs and whether it can be paid for. */
export function CostPanel({ cost, takes, chosen }: { cost: number | null; takes: number; chosen: Candidate | undefined }) {
  const { data: budget } = useBudget();
  const { data: balances } = useBalances();
  const n = Math.max(1, takes);
  const total = cost != null ? cost * n : null;
  const provider = chosen?.provider_name ?? chosen?.provider ?? "the provider";
  const balance = balances?.find((b) => b.provider === chosen?.provider);
  const v = total != null ? verdict(total, balance, budget, provider) : null;
  const today = budget ? budget.daily_cap_usd - budget.today_usd - budget.reserved_usd : null;
  const month = budget ? budget.monthly_cap_usd - budget.month_usd - budget.reserved_usd : null;

  return (
    <section aria-label="Cost" className="space-y-3 rounded-[var(--radius-panel)] bg-bay p-3">
      <div className="flex items-baseline justify-between gap-3">
        <p className="tabular text-2xl leading-none text-chalk">{usd(total)}</p>
        <p className="truncate text-xs text-muted">on {provider}</p>
      </div>
      {n > 1 && cost != null && (
        <p className="tabular -mt-1 text-2xs text-faint">
          {n} takes at {usd(cost)} each
        </p>
      )}
      {v && (
        <p className="flex items-start gap-2 text-xs text-chalk" aria-live="polite">
          <span aria-hidden className={cn("mt-1 size-2 shrink-0 rounded-full", TONE[v.tone])} />
          {v.text}
        </p>
      )}
      <dl className="tabular grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        <dt className="text-muted">{provider}</dt>
        <dd className="text-right">
          <BalanceText balance={balance} total={total} />
        </dd>
        {today != null && budget && (
          <>
            <dt className="text-muted">Today</dt>
            <dd className="text-right">
              {usd(Math.max(0, today))} <span className="text-faint">left of {usd(budget.daily_cap_usd)}</span>
            </dd>
          </>
        )}
        {month != null && budget && (
          <>
            <dt className="text-muted">This month</dt>
            <dd className="text-right">
              {usd(Math.max(0, month))} <span className="text-faint">left of {usd(budget.monthly_cap_usd)}</span>
            </dd>
          </>
        )}
      </dl>
    </section>
  );
}

function BalanceText({ balance, total }: { balance: Balance | undefined; total: number | null }) {
  if (!balance) return <span className="text-faint">checking…</span>;
  if (balance.status === "ok" && balance.usd != null) {
    const after = total != null ? balance.usd - total : null;
    return (
      <>
        {usd(balance.usd)}
        {after != null && after >= 0 && <span className="text-faint">, {usd(after)} after this</span>}
      </>
    );
  }
  if (balance.status === "ok" && balance.amount != null) {
    return <>{Math.round(balance.amount).toLocaleString()} {balance.unit === "credits" ? "credits" : ""}</>;
  }
  return (
    <a href={balance.link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-faint hover:text-chalk">
      {balance.status === "no_api" ? "balance not reported" : "couldn't read the balance"}
      <ExternalLink className="size-3" aria-hidden />
    </a>
  );
}
