"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useState, useSyncExternalStore } from "react";
import { toast } from "sonner";
import { api, unwrap } from "@/lib/api";
import { usd } from "@/lib/cn";
import { useBudget, useHealth, useRegistry, useUpdateBudget } from "@/lib/queries";
import { Button, Field, Segmented, Select, Switch } from "@/components/ui/controls";

const MODE_TEXT: Record<string, string> = {
  mock: "Every generation runs on the mock provider. Nothing is sent to a provider and nothing is billed.",
  mixed: "Only the routes below run for real; everything else stays on the mock provider.",
  live: "Generations call real providers and are billed to your accounts, within your spending limits.",
};
const CLAUDE_TEXT: Record<string, string> = {
  mock: "Scripts, shot plans and prompt help come from a canned writer. Nothing is billed.",
  live: "Claude writes scripts and shot plans and helps with prompts, billed to your budget.",
};
const MODE_NAME: Record<string, string> = { mock: "Mock", mixed: "Mixed", live: "Live" };
const CLAUDE_ROLES = [
  ["llm_smart_model", "Scripts and plans", "Faceless scripts, vlog and music video plans, Cinema shot plans"],
  ["llm_fast_model", "Prompt help", "Enhance, topic ideas, rewording a refused prompt"],
] as const;

/** Settings › General: which modes generations and Claude run in, the look, and whether the studio's parts are up. */
export function GeneralSettings() {
  const { data: budget } = useBudget();
  const update = useUpdateBudget();

  return (
    <>
      <section className="space-y-4" aria-labelledby="modes-title">
        <div>
          <h2 id="modes-title" className="type-title text-xl">Modes</h2>
          <p className="mt-1 text-xs text-muted">
            Changes apply to the next generation, no restart needed.
            {budget && (budget.provider_modes.length < 3 || budget.llm_modes.length < 2)
              ? " PROVIDER_MODE and LLM_MODE in .env set the highest mode offered here; raise them there and restart for more."
              : ""}
          </p>
        </div>
        {budget && (
          <div className="grid items-start gap-4 lg:grid-cols-2">
            <div className="space-y-2 rounded-[var(--radius-panel)] bg-panel p-4">
              <p className="text-sm">Generation</p>
              <Segmented
                label="Generation mode"
                value={budget.provider_mode}
                options={budget.provider_modes as ("mock" | "mixed" | "live")[]}
                render={(m) => MODE_NAME[m]}
                onChange={(m) => update.mutate({ provider_mode: m })}
              />
              <p className="text-xs leading-relaxed text-muted">{MODE_TEXT[budget.provider_mode]}</p>
              {budget.provider_mode === "mixed" && (
                <p className="text-xs text-muted">
                  {budget.live_route_allowlist.length
                    ? `Live routes: ${budget.live_route_allowlist.join(", ")}`
                    : "No routes are listed in LIVE_ROUTE_ALLOWLIST in .env, so everything stays mocked."}
                </p>
              )}
            </div>
            <div className="space-y-2 rounded-[var(--radius-panel)] bg-panel p-4">
              <p className="text-sm">Claude</p>
              <Segmented
                label="Claude mode"
                value={budget.llm_mode}
                options={budget.llm_modes as ("mock" | "live")[]}
                render={(m) => MODE_NAME[m]}
                onChange={(m) => update.mutate({ llm_mode: m })}
              />
              <p className="text-xs leading-relaxed text-muted">{CLAUDE_TEXT[budget.llm_mode]}</p>
              <div className="grid gap-3 pt-1">
                {CLAUDE_ROLES.map(([key, label, hint]) => (
                  <Field key={key} label={label} hint={hint} htmlFor={key}>
                    <Select
                      id={key}
                      value={budget[key]}
                      options={budget.claude_models.map((m) => ({
                        value: m.id,
                        label: `${m.name} · ${usd(m.input_usd)} in, ${usd(m.output_usd)} out`,
                      }))}
                      onChange={(v) => update.mutate({ [key]: v })}
                    />
                  </Field>
                ))}
              </div>
              <p className="text-2xs text-faint">
                Prices are per million tokens. Run estimates include what Claude&apos;s writing costs.
              </p>
            </div>
          </div>
        )}
      </section>
      <Appearance />
      <Status />
    </>
  );
}

/** Whether the studio's parts are up: the database, ffmpeg, the registry, your ComfyUI and Claude. */
function Status() {
  const { data: registry } = useRegistry();
  const { data: health } = useHealth();
  if (!health) return null;
  const engine = health.local_engine;
  return (
    <section className="space-y-4" aria-labelledby="status-title">
      <h2 id="status-title" className="type-title text-xl">Status</h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 rounded-[var(--radius-panel)] bg-panel p-4 text-sm">
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
          {!engine.configured
            ? "Off (set COMFYUI_URL in .env)"
            : engine.online
              ? `Running${engine.gpu ? ` on ${engine.gpu.replace(/^cuda:\d+\s*/, "").split(" : ")[0]}` : ""}${
                  engine.busy ? ", busy with a job" : engine.vram_free_gb != null ? `, ${engine.vram_free_gb} GB free` : ""
                }. `
              : "Not running. Start ComfyUI to use the local models. "}
          {engine.configured && (
            <Link href="/settings/local" className="text-muted underline hover:text-chalk">Local models</Link>
          )}
        </dd>
        <dt className="text-muted">Claude</dt>
        <dd>
          {health.llm_mode === "mock" ? (
            "Mock writer"
          ) : health.llm_key_set ? (
            "Live. Enhancements are billed to your budget."
          ) : (
            <>
              Live mode, but there is no Claude key yet. Add one under{" "}
              <Link href="/settings/providers" className="underline hover:text-chalk">Providers</Link>.
            </>
          )}
        </dd>
        <dt className="text-muted">Media folder</dt>
        <dd className="truncate text-xs leading-5" title={health.media_root}>{health.media_root}</dd>
      </dl>
      {health.registry_errors && (
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
  );
}

/** Settings › Spending: what's been spent, the switch that stops paid work, the limits, and where the money went. */
export function SpendingSettings() {
  const { data: budget } = useBudget();
  const update = useUpdateBudget();

  return (
    <>
      <section className="space-y-4" aria-labelledby="spending-title">
        <h2 id="spending-title" className="type-title text-xl">Spending</h2>
        {budget && (
          <>
            <dl className="tabular grid grid-cols-3 gap-3">
              <Stat label="Today" value={usd(budget.today_usd)} />
              <Stat label="This month" value={usd(budget.month_usd)} />
              <Stat label="Reserved for running jobs" value={usd(budget.reserved_usd)} />
            </dl>
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
            <BudgetForm
              key={`${budget.daily_cap_usd}-${budget.monthly_cap_usd}-${budget.confirm_over_usd}`}
              initial={{
                daily: String(budget.daily_cap_usd),
                monthly: String(budget.monthly_cap_usd),
                confirm: String(budget.confirm_over_usd),
              }}
            />
          </>
        )}
      </section>
      <WhereItWent />
      <SpendHistory />
    </>
  );
}

// Animations are on unless turned off in this browser: html[data-motion="off"], set before the first paint by the
// root layout's script from localStorage "motion" (see the .aura and .ring-halo rules in globals.css)
const motionListeners = new Set<() => void>();
const subscribeMotion = (l: () => void) => {
  motionListeners.add(l);
  return () => void motionListeners.delete(l);
};

function setMotion(on: boolean) {
  document.documentElement.dataset.motion = on ? "on" : "off";
  try {
    localStorage.setItem("motion", on ? "on" : "off");
  } catch {}
  motionListeners.forEach((l) => l());
}

function Appearance() {
  const motion = useSyncExternalStore(subscribeMotion, () => document.documentElement.dataset.motion !== "off", () => true);
  return (
    <section className="space-y-4">
      <h2 className="type-title text-xl">Appearance</h2>
      <div className="flex items-center justify-between gap-4 rounded-[var(--radius-panel)] bg-panel p-4">
        <div>
          <p className="text-sm">Animations</p>
          <p className="mt-0.5 text-xs text-muted">
            The lights behind the studio drift and the ring on Generate turns. Off keeps them still; your system&apos;s
            reduce-motion setting stills them too. Saved in this browser.
          </p>
        </div>
        <Switch label="Animations" checked={motion} onChange={setMotion} />
      </div>
    </section>
  );
}

type Period = "today" | "month" | "all";
const PERIOD_LABEL: Record<Period, string> = { today: "Today", month: "This month", all: "All time" };

/** Spend split by studio feature and by model, with how much of it is the studio's own estimate. */
const BILL_NAME: Record<string, string> = { fal: "fal", runpod_wan_core: "wan-core" };

function WhereItWent() {
  const qc = useQueryClient();
  const [period, setPeriod] = useState<Period>("month");
  const [checking, setChecking] = useState(false);
  const { data } = useQuery({
    queryKey: ["spend-breakdown", period],
    queryFn: () => unwrap(api.GET("/budget/breakdown", { params: { query: { period } } })),
  });

  // the worker does this every hour; the button is for "right now"
  const checkBills = async () => {
    setChecking(true);
    try {
      const results = await unwrap(api.POST("/budget/reconcile", { params: { query: { days: 3 } } }));
      const booked = results.filter((r) => r.status === "ok" && Math.abs(r.booked_usd) >= 0.0005)
        .map((r) => `${BILL_NAME[r.provider] ?? r.provider} ${r.booked_usd > 0 ? "+" : "−"}${usd(Math.abs(r.booked_usd))}`);
      const problems = results.filter((r) => r.status === "unavailable" || r.status === "error")
        .map((r) => `${BILL_NAME[r.provider] ?? r.provider}: ${r.detail}`);
      toast(booked.length ? `Corrected to the bills: ${booked.join(", ")}` : "Everything matches the bills read so far",
            problems.length ? { description: problems.join(". ") } : undefined);
      await Promise.all(["spend-breakdown", "budget", "spend-history"].map((k) => qc.invalidateQueries({ queryKey: [k] })));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't read the bills");
    } finally {
      setChecking(false);
    }
  };

  return (
    <section className="space-y-4" aria-label="Where it went">
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <h2 className="type-title text-xl">Where it went</h2>
        <div className="flex items-center gap-3">
          <Button size="sm" tone="ghost" onClick={() => void checkBills()} disabled={checking}>
            {checking ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <RefreshCw className="size-4" aria-hidden />}
            Check against bills
          </Button>
          <div className="w-72">
            <Segmented label="Period" value={period} options={["today", "month", "all"] as const}
                       render={(p) => PERIOD_LABEL[p]} onChange={setPeriod} />
          </div>
        </div>
      </div>
      {data && data.total_usd === 0 ? (
        <p className="text-sm text-muted">Nothing billed {period === "all" ? "yet" : PERIOD_LABEL[period].toLowerCase()}.</p>
      ) : data ? (
        <>
        <p className="text-xs text-muted">
          {usd(data.checked_usd ?? 0)} checked against fal and RunPod bills, {usd(data.estimated_usd ?? 0)} still the
          studio&rsquo;s estimate<span className="text-faint">*</span>; the rest the provider reported as it ran.
        </p>
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <SpendTable title="By feature" groups={data.features} total={data.total_usd} />
          <SpendTable title="By model" groups={data.models} total={data.total_usd} />
        </div>
        </>
      ) : null}
    </section>
  );
}

function SpendTable({ title, groups, total }: {
  title: string; groups: { key: string; label: string; usd: number; count: number; estimated_usd: number }[];
  total: number;
}) {
  return (
    <table className="w-full text-xs">
      <caption className="mb-2 text-left text-xs text-muted">{title}</caption>
      <thead className="sr-only">
        <tr><th>Name</th><th>Charges</th><th>Spent</th></tr>
      </thead>
      <tbody className="divide-y divide-line-soft">
        {groups.map((g) => (
          <tr key={g.key}>
            <td className="py-2 pr-3">
              <span className="block truncate text-chalk">{g.label}</span>
              <span className="mt-1 block h-1 rounded-full bg-bay" aria-hidden>
                <span className="block h-1 rounded-full bg-tungsten/70" style={{ width: `${total ? (g.usd / total) * 100 : 0}%` }} />
              </span>
            </td>
            <td className="tabular w-12 py-2 pr-3 text-right text-faint">{g.count}&times;</td>
            <td className="tabular w-20 py-2 text-right text-chalk"
                title={g.estimated_usd ? `${usd(g.estimated_usd)} of it is the studio's estimate` : undefined}>
              {usd(g.usd)}{g.estimated_usd > 0 && <span className="text-faint">*</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const SHOWN_DAYS = 5;
const DAY = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

/** Every charge, newest first, grouped by day, with this month's split by provider. */
function SpendHistory() {
  const { data } = useQuery({ queryKey: ["spend-history"], queryFn: () => unwrap(api.GET("/budget/history")) });
  const [all, setAll] = useState(false);
  if (!data) return null;
  const days = new Map<string, typeof data.entries>();
  for (const e of data.entries) {
    const day = DAY.format(new Date(e.created_at));
    days.set(day, [...(days.get(day) ?? []), e]);
  }
  return (
    <section className="space-y-4">
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
                  <span className="text-chalk">{usd(p.usd)}</span> on {p.name} ({p.count})
                </span>
              ))}
            </p>
          )}
          <div className="space-y-6">
            {[...days].slice(0, all ? undefined : SHOWN_DAYS).map(([day, entries]) => (
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
          {days.size > SHOWN_DAYS && (
            <Button size="sm" tone="ghost" onClick={() => setAll(!all)}>
              {all ? "Show the last few days" : `Show all ${days.size} days`}
            </Button>
          )}
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
      className="space-y-4 rounded-[var(--radius-panel)] bg-panel p-4"
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
      <div className="grid items-start gap-4 sm:grid-cols-3">
        <MoneyField id="daily" label="Daily limit" hint="Jobs that would go over wait until tomorrow." value={form.daily} onChange={(v) => setForm({ ...form, daily: v })} />
        <MoneyField id="monthly" label="Monthly limit" hint="The same, until next month." value={form.monthly} onChange={(v) => setForm({ ...form, monthly: v })} />
        <MoneyField id="confirm" label="Ask before runs over" hint="Anything estimated above this needs a second click." value={form.confirm} onChange={(v) => setForm({ ...form, confirm: v })} />
      </div>
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
      <div className="flex h-10 items-center rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 focus-within:border-tungsten/60">
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
