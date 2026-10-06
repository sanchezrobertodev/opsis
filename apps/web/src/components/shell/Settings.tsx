"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "@/lib/api";
import { usd } from "@/lib/cn";
import { useBudget, useHealth, useRegistry, useUpdateBudget } from "@/lib/queries";
import { Button, Field, Switch } from "@/components/ui/controls";

const MODE_TEXT: Record<string, string> = {
  mock: "Every generation runs on the local mock provider. Nothing is sent to a provider and nothing is billed.",
  mixed: "Routes listed in LIVE_ROUTE_ALLOWLIST run for real; everything else stays on the mock provider.",
  live: "Generations call real providers and are billed to your accounts.",
};

export function Settings() {
  const { data: budget } = useBudget();
  const { data: registry } = useRegistry();
  const { data: health } = useHealth();
  const update = useUpdateBudget();

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-quiet">
      <div className="grid max-w-4xl gap-10 lg:grid-cols-2">
        <section className="space-y-5">
          <h2 className="type-title text-xl">Spending</h2>
          {budget && (
            <dl className="tabular grid grid-cols-3 gap-3">
              <Stat label="Today" value={usd(budget.today_usd)} />
              <Stat label="This month" value={usd(budget.month_usd)} />
              <Stat label="Reserved for running jobs" value={usd(budget.reserved_usd)} />
            </dl>
          )}
          {budget && (
            <BudgetForm
              key={`${budget.daily_cap_usd}-${budget.monthly_cap_usd}-${budget.confirm_over_usd}`}
              initial={{
                daily: String(budget.daily_cap_usd),
                monthly: String(budget.monthly_cap_usd),
                confirm: String(budget.confirm_over_usd),
              }}
            />
          )}

          {budget && (
            <div className="flex items-center justify-between gap-4 rounded-[var(--radius-panel)] bg-panel p-4">
              <div>
                <p className="text-sm">Allow paid generations</p>
                <p className="mt-0.5 text-xs text-muted">Turn off to stop every paid job immediately.</p>
              </div>
              <Switch
                label="Allow paid generations"
                checked={budget.live_enabled}
                onChange={(v) => update.mutate({ live_enabled: v })}
              />
            </div>
          )}
        </section>

        <section className="space-y-5">
          <h2 className="type-title text-xl">Providers</h2>
          {registry && (
            <div className="rounded-[var(--radius-panel)] bg-panel p-4">
              <p className="text-sm">
                Provider mode: <span className="text-tungsten">{registry.provider_mode}</span>
              </p>
              <p className="mt-1 text-xs leading-relaxed text-muted">{MODE_TEXT[registry.provider_mode]}</p>
              <p className="mt-3 text-2xs text-faint">Change it with PROVIDER_MODE in .env, then restart the API and worker.</p>
            </div>
          )}
          {health && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
              <dt className="text-muted">Database</dt>
              <dd>{health.database ? "Connected" : "Unreachable"}</dd>
              <dt className="text-muted">ffmpeg</dt>
              <dd>{health.ffmpeg ? "Found" : "Missing (install it and restart)"}</dd>
              <dt className="text-muted">Model registry</dt>
              <dd className="tabular">
                {registry?.models.length ?? 0} models, version {health.registry_hash.slice(0, 8)}
              </dd>
              <dt className="text-muted">Local engine</dt>
              <dd>
                {!health.local_engine.configured
                  ? "Off (set COMFYUI_URL in .env)"
                  : health.local_engine.online
                    ? `Running${health.local_engine.gpu ? ` on ${health.local_engine.gpu.replace(/^cuda:\d+\s*/, "").split(" : ")[0]}` : ""}${
                        health.local_engine.vram_free_gb != null ? `, ${health.local_engine.vram_free_gb} GB free` : ""
                      }`
                    : "Not running. Start Local Video Studio to use the local models."}
              </dd>
              <dt className="text-muted">Claude</dt>
              <dd>
                {health.llm_mode === "mock"
                  ? "Mock writer (set LLM_MODE=live and ANTHROPIC_API_KEY in .env for Claude)"
                  : health.llm_key_set
                    ? "Live. Enhancements are billed to your budget."
                    : "Live mode, but ANTHROPIC_API_KEY is missing from .env"}
              </dd>
              <dt className="text-muted">Media folder</dt>
              <dd className="truncate text-xs" title={health.media_root}>
                {health.media_root}
              </dd>
            </dl>
          )}
          {health?.registry_errors && (
            <div className="rounded-[var(--radius-panel)] border border-fault/40 p-4 text-xs text-fault">
              The last registry edit was rejected, so the previous version is still active:
              <ul className="mt-2 list-disc pl-4">
                {health.registry_errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </div>
          )}
        </section>

        <SpendHistory />
      </div>
    </div>
  );
}

const DAY = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

/** Every charge, newest first, grouped by day, with this month's split by provider. */
function SpendHistory() {
  const { data } = useQuery({ queryKey: ["spend-history"], queryFn: () => unwrap(api.GET("/budget/history")) });
  if (!data) return null;
  const days = new Map<string, typeof data.entries>();
  for (const e of data.entries) {
    const day = DAY.format(new Date(e.created_at));
    days.set(day, [...(days.get(day) ?? []), e]);
  }
  return (
    <section className="space-y-5 lg:col-span-2">
      <h2 className="type-title text-xl">Spend history</h2>
      {data.entries.length === 0 ? (
        <p className="text-sm text-muted">
          Nothing billed yet. Paid generations and Claude enhancements show up here with what they cost.
        </p>
      ) : (
        <>
          {data.by_provider.length > 0 && (
            <p className="tabular text-xs text-muted">
              This month:{" "}
              {data.by_provider.map((p, i) => (
                <span key={p.provider}>
                  {i > 0 && ", "}
                  <span className="text-chalk">{usd(p.usd)}</span> on {p.provider} ({p.count})
                </span>
              ))}
            </p>
          )}
          <div className="space-y-6">
            {[...days].map(([day, entries]) => (
              <div key={day}>
                <h3 className="mb-2 flex justify-between text-xs text-muted">
                  {day}
                  <span className="tabular">{usd(entries.reduce((sum, e) => sum + e.amount_usd, 0))}</span>
                </h3>
                <ul className="divide-y divide-line-soft rounded-[var(--radius-panel)] bg-panel">
                  {entries.map((e) => (
                    <li key={e.id} className="flex items-baseline gap-4 px-4 py-2.5 text-xs">
                      <span className="tabular w-16 shrink-0 text-faint">{TIME.format(new Date(e.created_at))}</span>
                      <span className="min-w-0 flex-1 truncate text-chalk" title={e.label}>
                        {e.label}
                      </span>
                      <span className="hidden shrink-0 text-faint sm:inline">{e.route_id}</span>
                      <span className="tabular w-16 shrink-0 text-right text-chalk">
                        {e.amount_usd < 0 ? "−" : ""}
                        {usd(Math.abs(e.amount_usd))}
                        {e.estimated && <span className="text-faint" title="Estimated from the provider's price list">*</span>}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <p className="text-2xs text-faint">* Estimated from the provider&rsquo;s published price; the provider didn&rsquo;t report the exact cost.</p>
        </>
      )}
    </section>
  );
}

/** Keyed by the saved values, so it re-initializes after a save instead of syncing in an effect. */
function BudgetForm({ initial }: { initial: { daily: string; monthly: string; confirm: string } }) {
  const update = useUpdateBudget();
  const [form, setForm] = useState(initial);
  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        update.mutate(
          {
            daily_cap_usd: Number(form.daily),
            monthly_cap_usd: Number(form.monthly),
            confirm_over_usd: Number(form.confirm),
          },
          {
            onSuccess: () => toast("Spending limits saved"),
            onError: (err) => toast.error(err instanceof Error ? err.message : "Could not save"),
          },
        );
      }}
    >
      <MoneyField id="daily" label="Daily limit" hint="Jobs that would go over wait until tomorrow." value={form.daily} onChange={(v) => setForm({ ...form, daily: v })} />
      <MoneyField id="monthly" label="Monthly limit" value={form.monthly} onChange={(v) => setForm({ ...form, monthly: v })} />
      <MoneyField id="confirm" label="Ask before runs over" hint="Anything estimated above this needs a second click." value={form.confirm} onChange={(v) => setForm({ ...form, confirm: v })} />
      <Button type="submit" disabled={update.isPending}>
        Save limits
      </Button>
    </form>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[var(--radius-panel)] bg-panel p-3">
      <dt className="text-2xs text-faint">{label}</dt>
      <dd className="type-title mt-1 text-xl">{value}</dd>
    </div>
  );
}

function MoneyField({
  id,
  label,
  hint,
  value,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <Field label={label} htmlFor={id} hint={hint}>
      <div className="flex h-10 max-w-48 items-center rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 focus-within:border-tungsten/60">
        <span className="text-sm text-faint">$</span>
        <input
          id={id}
          inputMode="decimal"
          value={value}
          onChange={(e) => onChange(e.target.value.replace(/[^\d.]/g, ""))}
          className="tabular h-full min-w-0 flex-1 bg-transparent pl-1 text-sm outline-none"
        />
      </div>
    </Field>
  );
}
