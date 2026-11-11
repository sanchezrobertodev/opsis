"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, LoaderCircle, RefreshCw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap, type Balance, type ProviderKey as Key } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { keys as queryKeys } from "@/lib/queries";
import { Button } from "@/components/ui/controls";

/** Settings › Providers: each provider's key (pasted here instead of .env, kept encrypted, never sent back) and what's
 * left on its account, one row each. */
export function Providers() {
  const qc = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const keys = useQuery({ queryKey: ["keys"], queryFn: () => unwrap(api.GET("/keys")) });
  const balances = useQuery({
    queryKey: ["balances"],
    queryFn: () => unwrap(api.GET("/budget/balances")),
    staleTime: 60_000,
  });
  const refresh = async () => {
    setRefreshing(true);
    try {
      qc.setQueryData(["balances"], await unwrap(api.GET("/budget/balances", { params: { query: { refresh: true } } })));
    } finally {
      setRefreshing(false);
    }
  };
  const balance = (setting: string) => balances.data?.find((b) => b.setting === setting);
  const set = keys.data?.filter((k) => k.source || k.unreadable) ?? [];
  const unset = keys.data?.filter((k) => !k.source && !k.unreadable) ?? [];
  const keyless = balances.data?.filter((b) => !keys.data?.some((k) => k.setting === b.setting)) ?? [];

  return (
    <section className="space-y-4" aria-labelledby="providers-title">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 id="providers-title" className="type-title text-xl">Providers</h2>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted">
            Paste the keys you have. They&rsquo;re stored encrypted on the server and can&rsquo;t be read back, only
            replaced, and new jobs use them within a few seconds. A key saved here wins over the same key in .env.
          </p>
        </div>
        <Button size="sm" tone="ghost" onClick={() => void refresh()} disabled={refreshing}>
          {refreshing ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <RefreshCw className="size-4" aria-hidden />}
          Check balances
        </Button>
      </div>
      {!keys.data ? (
        <p className="text-sm text-muted">Loading&hellip;</p>
      ) : (
        <>
          {(set.length > 0 || keyless.length > 0) && (
            <ul aria-label="Connected providers" className="divide-y divide-line-soft rounded-[var(--radius-panel)] bg-panel">
              {set.map((k) => <KeyRow key={k.setting} k={k} balance={balance(k.setting)} />)}
              {keyless.map((b) => <AccountRow key={b.provider} b={b} />)}
            </ul>
          )}
          {unset.length > 0 && (
            <>
              <h3 className="pt-2 text-xs text-muted">Not set up</h3>
              <ul aria-label="Providers without a key" className="divide-y divide-line-soft rounded-[var(--radius-panel)] bg-panel">
                {unset.map((k) => <KeyRow key={k.setting} k={k} />)}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  );
}

function KeyRow({ k, balance }: { k: Key; balance?: Balance }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  const done = (rows: Key[], message: string) => {
    qc.setQueryData(["keys"], rows);
    // what a key changes: balances, which routes can run, whether Claude is ready
    for (const key of [["balances"], queryKeys.registry, queryKeys.health, queryKeys.budget]) {
      void qc.invalidateQueries({ queryKey: key });
    }
    toast(message);
  };
  const run = async (action: () => Promise<Key[]>, message: string) => {
    setBusy(true);
    try {
      done(await action(), message);
      setEditing(false);
      setValue("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save the key");
    } finally {
      setBusy(false);
    }
  };
  const path = { params: { path: { setting: k.setting } } };
  const save = () =>
    run(() => unwrap(api.PUT("/keys/{setting}", { ...path, body: { value } })), `${k.label} key saved`);
  const remove = () => {
    if (!window.confirm(`Remove the ${k.label} key saved here? If .env has one, that one is used again.`)) return;
    void run(() => unwrap(api.DELETE("/keys/{setting}", path)), `${k.label} key removed`);
  };

  return (
    <li className="space-y-3 px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-56">
          <p className="text-sm">{k.label}</p>
          <p className="mt-0.5 text-2xs text-faint">{k.what}</p>
          <p className={cn("mt-1 text-xs", k.unreadable ? "text-fault" : k.source ? "text-muted" : "text-faint")}>
            <StatusText k={k} />
            {balance?.detail && (balance.status === "unavailable" || balance.status === "error") && (
              <span className={balance.status === "error" ? "text-fault" : undefined}> · {balance.detail}</span>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {balance && <Amount b={balance} />}
          {!editing && (
            <>
              <Button size="sm" tone={k.source ? "ghost" : "quiet"} onClick={() => setEditing(true)} disabled={busy}>
                {k.source ? "Replace" : "Add key"}
              </Button>
              {(k.source === "app" || k.unreadable) && (
                <Button size="sm" tone="ghost" onClick={remove} disabled={busy}>
                  Remove
                </Button>
              )}
            </>
          )}
          <OutLink href={balance ? balance.link : k.link}>{balance ? "Billing" : "Get a key"}</OutLink>
        </div>
      </div>
      {editing && (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (value.trim()) void save();
          }}
        >
          <input
            type="password"
            autoFocus
            autoComplete="off"
            spellCheck={false}
            aria-label={`${k.label} key`}
            placeholder="Paste the key"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="h-8 min-w-0 flex-1 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-xs outline-none focus:border-tungsten/60"
          />
          <Button size="sm" tone="primary" type="submit" disabled={busy || !value.trim()}>
            Save
          </Button>
          <Button size="sm" tone="ghost" type="button" onClick={() => { setEditing(false); setValue(""); }}>
            Cancel
          </Button>
        </form>
      )}
    </li>
  );
}

/** A provider that's set up without a key (Codex, signed in with a ChatGPT plan). */
function AccountRow({ b }: { b: Balance }) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-4 py-3">
      <div className="min-w-0 flex-1 basis-56">
        <p className="text-sm">{b.label}</p>
        {b.detail && (
          <p className={cn("mt-1 text-xs", b.status === "ok" ? "text-muted" : b.status === "error" ? "text-fault" : "text-faint")}>
            {b.detail.charAt(0).toUpperCase() + b.detail.slice(1)}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <Amount b={b} />
        <OutLink href={b.link}>Plans</OutLink>
      </div>
    </li>
  );
}

function OutLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-2xs text-muted hover:text-chalk">
      {children} <ExternalLink className="size-3" aria-hidden />
    </a>
  );
}

function Amount({ b }: { b: Balance }) {
  const text = amountText(b);
  return text ? <span className="tabular text-sm text-chalk">{text}</span> : null;
}

function amountText(b: Balance) {
  if (b.status === "ok" && b.unit == null) return "Ready"; // nothing to read but whether it works (Codex)
  if (b.status !== "ok" || b.amount == null) return null;
  if (b.unit === "usd") return usd(b.amount);
  const credits = `${Math.round(b.amount).toLocaleString()} credits`;
  return b.usd != null ? `${credits} (${usd(b.usd)})` : credits;
}

function StatusText({ k }: { k: Key }) {
  const ending = k.last4 ? ` ending ${k.last4}` : "";
  if (k.unreadable) {
    return (
      <>
        Saved, but this server&rsquo;s key secret can&rsquo;t read it{k.source === "env" ? ", so the one in .env is used" : ""}.
        Paste it again.
      </>
    );
  }
  if (k.source === "app") return <>Key saved here{ending}</>;
  if (k.source === "env") return <>Key from .env{ending}</>;
  return <>No key. Its models are unavailable until you add one.</>;
}
