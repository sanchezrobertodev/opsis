"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap, type ProviderKey as Key } from "@/lib/api";
import { cn } from "@/lib/cn";
import { keys as queryKeys } from "@/lib/queries";
import { Button } from "@/components/ui/controls";

/** Provider keys, pasted here instead of .env. The server keeps them encrypted and never sends them back. */
export function Keys() {
  const { data } = useQuery({ queryKey: ["keys"], queryFn: () => unwrap(api.GET("/keys")) });
  return (
    <section className="space-y-4 lg:col-span-2" aria-label="Provider keys">
      <div>
        <h2 className="type-title text-xl">Keys</h2>
        <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted">
          Paste the keys you have. They&rsquo;re stored encrypted on the server and can&rsquo;t be read back, only
          replaced. A key saved here is used instead of the same key in .env, by new jobs within a few seconds, with no
          restart.
        </p>
      </div>
      {!data ? (
        <p className="text-sm text-muted">Loading&hellip;</p>
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2">
          {data.map((k) => (
            <KeyRow key={k.setting} k={k} />
          ))}
        </ul>
      )}
    </section>
  );
}

function KeyRow({ k }: { k: Key }) {
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
    <li className="space-y-3 rounded-[var(--radius-panel)] bg-panel px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm">{k.label}</p>
          <p className="mt-0.5 text-2xs text-faint">{k.what}</p>
        </div>
        <a href={k.link} target="_blank" rel="noreferrer"
           className="flex shrink-0 items-center gap-1 text-2xs text-muted hover:text-chalk">
          Get a key <ExternalLink className="size-3" aria-hidden />
        </a>
      </div>
      <p className={cn("text-xs", k.unreadable ? "text-fault" : k.source ? "text-chalk" : "text-muted")}>
        <StatusText k={k} />
      </p>
      {editing ? (
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
      ) : (
        <div className="flex gap-2">
          <Button size="sm" onClick={() => setEditing(true)} disabled={busy}>
            {k.source === "app" ? "Replace" : "Add key"}
          </Button>
          {(k.source === "app" || k.unreadable) && (
            <Button size="sm" tone="ghost" onClick={remove} disabled={busy}>
              Remove
            </Button>
          )}
        </div>
      )}
    </li>
  );
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
  if (k.source === "app") return <>Saved here{ending}</>;
  if (k.source === "env") return <>From .env{ending}</>;
  return <>Not set. Its models are unavailable until you add one.</>;
}
