"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, LoaderCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap, type LocalSetup, type LocalSetupJob } from "@/lib/api";
import { useHealth } from "@/lib/queries";
import { Button } from "@/components/ui/controls";
import { ModelStorage } from "@/components/shell/ModelStorage";

const SETUP = "uv run python scripts/setup_local.py";
const gb = (bytes: number) => `${(bytes / 1e9).toFixed(1)} GB`;
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const gpuGb = (s?: string | null) => s?.match(/GPU with (\d+(?:\.\d+)?) GB/)?.[1];

type Plan = NonNullable<LocalSetup["models"]>[number];

/** Settings › Local models: your own ComfyUI, the free models it can run, and where it keeps them. */
export function LocalSettings() {
  const engine = useHealth().data?.local_engine;
  return (
    <>
      {!engine ? null : !engine.configured ? (
        <section className="space-y-4" aria-labelledby="local-models-title">
          <h2 id="local-models-title" className="type-title text-xl">Free models on your GPU</h2>
          <p className="rounded-[var(--radius-panel)] bg-panel p-4 text-xs leading-relaxed text-muted">
            Open models can run free on your own ComfyUI. Set COMFYUI_URL in .env to its address
            (http://127.0.0.1:8188 on this computer) and restart Opsis; the README&rsquo;s &ldquo;Open models on your
            GPU&rdquo; walks through it.
          </p>
        </section>
      ) : (
        <LocalModels online={engine.online} gpu={engine.gpu?.replace(/^cuda:\d+\s*/, "").split(" : ")[0] ?? null} />
      )}
      <ModelStorage />
    </>
  );
}

/** Which free models your own ComfyUI can run now, and setting up the others when its folders are on this computer
 * (what scripts/setup_local.py does, from the same plan); else the command to run on that computer. */
function LocalModels({ online, gpu }: { online: boolean; gpu: string | null }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState<string | null>(null);
  const [closed, setClosed] = useState(false);
  const models = useQuery({
    queryKey: ["local-models"],
    queryFn: () => unwrap(api.GET("/local-engine/models")),
    refetchInterval: 30_000,
    enabled: online,
  });
  const setup = useQuery({ queryKey: ["local-setup"], queryFn: () => unwrap(api.GET("/local-engine/setup")), enabled: online });
  const job = useQuery({
    queryKey: ["local-setup-job"],
    queryFn: async () => {
      const was = qc.getQueryData<LocalSetupJob | null>(["local-setup-job"]);
      const now = (await unwrap(api.GET("/local-engine/setup/job"))) ?? null;
      if (was?.state === "running" && now?.state !== "running") {
        void qc.invalidateQueries({ queryKey: ["local-models"] });
        void qc.invalidateQueries({ queryKey: ["local-setup"] });
      }
      return now;
    },
    enabled: online,
    refetchInterval: (q) => (q.state.data?.state === "running" ? 1500 : false),
  });

  const start = async (ids: string[]) => {
    try {
      qc.setQueryData(["local-setup-job"], await unwrap(api.POST("/local-engine/setup", { body: { model_ids: ids } })));
      setConfirming(null);
      setClosed(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't start the setup");
    }
  };

  const plans = new Map((setup.data?.models ?? []).map((p) => [p.id, p]));
  const list = models.data ?? [];
  const names = new Map(list.map((m) => [m.id, m.name]));
  const ready = list.filter((m) => m.ready);
  const tooBig = list.filter((m) => !m.ready && (plans.get(m.id)?.state === "too_big" || gpuGb(m.reason)));
  const rest = list.filter((m) => !m.ready && !tooBig.includes(m));
  const canSetUp = setup.data?.available === true;
  const running = job.data?.state === "running";

  return (
    <section className="space-y-4" aria-labelledby="local-models-title">
      <div>
        <h2 id="local-models-title" className="type-title text-xl">Free models on your GPU</h2>
        <p className="mt-1 text-xs text-muted">
          {online ? `ComfyUI is running${gpu ? ` on ${gpu}` : ""}.` : "ComfyUI isn't running."}
        </p>
      </div>
      <div className="space-y-5 rounded-[var(--radius-panel)] bg-panel p-4">
        {!online ? (
          <p role="status" className="text-sm text-muted">Start ComfyUI to see which models are ready and set up the others.</p>
        ) : setup.data && !canSetUp ? (
          <div className="space-y-2 text-xs leading-relaxed text-muted">
            <p>
              These run on your own ComfyUI at no cost. {cap(setup.data.reason ?? "it isn't on this computer")}, so set
              them up there: run this in the Opsis folder on that computer while ComfyUI is running. It lists what fits
              the GPU and asks before installing anything.
            </p>
            <Command />
          </div>
        ) : (
          <p className="text-xs leading-relaxed text-muted">
            These run on your own ComfyUI at no cost, and the forms start on one once it&apos;s ready. Set up shows what
            it will install and download before it starts. From a terminal instead: <code className="text-chalk">{SETUP}</code>
          </p>
        )}

        {job.data && !closed && <Progress job={job.data} names={names} onRetry={() => void start(job.data!.model_ids)} onClose={() => setClosed(true)} />}
        {online && models.isPending && <p className="text-sm text-muted">Asking ComfyUI&hellip;</p>}
        {models.error && <p role="alert" className="text-sm text-fault">{models.error.message}</p>}

        {online && models.data && (
          <>
            <p role="status" className="sr-only">{ready.length} of {list.length} ready</p>
            {rest.length > 0 && (
              <Group title="Not set up yet" count={rest.length}>
                {rest.map((m) => {
                  const plan = plans.get(m.id);
                  return (
                    <li key={m.id} className="py-2.5">
                      <div className="flex items-start gap-3">
                        <CircleDashed className="mt-0.5 size-4 shrink-0 text-faint" aria-hidden />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm">{m.name}</p>
                          <p className="break-words text-xs text-muted">
                            {plan?.state === "needs" ? `Needs ${plan.needs}`
                              : plan?.state === "ready" ? "Installed. ComfyUI sees new files within a minute; a new node pack needs a restart."
                              : m.reason ? cap(m.reason) : "Not set up"}
                          </p>
                        </div>
                        {canSetUp && plan?.state === "needs" && confirming !== m.id && (
                          <Button size="sm" tone="quiet" disabled={running} onClick={() => setConfirming(m.id)}>Set up</Button>
                        )}
                      </div>
                      {confirming === m.id && plan && (
                        <Confirm plan={plan} pythonProblem={setup.data?.python_problem} onStart={() => void start([m.id])} onCancel={() => setConfirming(null)} />
                      )}
                    </li>
                  );
                })}
              </Group>
            )}
            {ready.length > 0 && (
              <Group title="Ready" count={ready.length}>
                {ready.map((m) => (
                  <li key={m.id} className="flex items-center gap-3 py-2">
                    <CheckCircle2 className="size-4 shrink-0 text-done" aria-hidden />
                    <p className="text-sm">{m.name}</p>
                  </li>
                ))}
              </Group>
            )}
            {tooBig.length > 0 && (
              <p className="text-xs leading-relaxed text-faint">
                <span className="text-muted">Need a bigger GPU:</span>{" "}
                {tooBig.map((m) => {
                  const need = gpuGb(plans.get(m.id)?.needs) ?? gpuGb(m.reason);
                  return need ? `${m.name} (${need} GB)` : m.name;
                }).join(", ")}
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function Command() {
  return (
    <code className="block overflow-x-auto whitespace-nowrap rounded-[var(--radius-control)] bg-bay px-3 py-2 text-xs text-chalk">
      {SETUP}
    </code>
  );
}

function Group({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-xs text-muted">{title} <span className="tabular text-faint">{count}</span></h3>
      <ul aria-label={title} className="divide-y divide-line-soft">{children}</ul>
    </div>
  );
}

/** What Set up will do for one model, asked before anything changes. */
function Confirm({ plan, pythonProblem, onStart, onCancel }: {
  plan: Plan; pythonProblem?: string | null; onStart: () => void; onCancel: () => void;
}) {
  const packs = plan.packs ?? [];
  const blocked = !plan.room ? "There isn't enough free disk for these files."
    : packs.length && pythonProblem ? `${cap(pythonProblem)}: run ${SETUP} with --python instead.` : null;
  return (
    <div className="ml-7 mt-3 space-y-3 rounded-[var(--radius-control)] bg-bay p-3 text-xs">
      <p className="text-muted">This will:</p>
      <ul className="list-disc space-y-1 pl-5 text-chalk">
        {packs.map((p) => (
          <li key={p}>Install the {p} node pack into ComfyUI, with its Python packages (ComfyUI&rsquo;s torch stays as it is)</li>
        ))}
        {(plan.files ?? 0) > 0 && (
          <li>
            Download {plan.files} model {plan.files === 1 ? "file" : "files"}, {gb(plan.bytes ?? 0)}
            {plan.free_bytes != null && <span className="text-muted"> ({gb(plan.free_bytes)} free)</span>}
          </li>
        )}
      </ul>
      {blocked && <p role="alert" className="text-fault">{blocked}</p>}
      <div className="flex gap-2">
        <Button size="sm" tone="primary" disabled={!!blocked} onClick={onStart}>Set up</Button>
        <Button size="sm" tone="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

function Progress({ job, names, onRetry, onClose }: {
  job: LocalSetupJob; names: Map<string, string>; onRetry: () => void; onClose: () => void;
}) {
  const what = job.model_ids.map((id) => names.get(id) ?? id).join(", ");
  const total = job.total_bytes ?? 0;
  const pct = total ? Math.min(100, Math.round(((job.done_bytes ?? 0) / total) * 100)) : null;
  return (
    <div role="status" className="space-y-3 rounded-[var(--radius-control)] bg-bay p-3 text-xs">
      {job.state === "running" ? (
        <>
          <p className="flex items-start gap-2 text-chalk">
            <LoaderCircle className="mt-0.5 size-3.5 shrink-0 animate-spin" aria-hidden />
            <span>Setting up {what}{job.step && <span className="text-muted"> &middot; {job.step}</span>}</span>
          </p>
          {pct != null && (
            <div className="space-y-1">
              <div role="progressbar" aria-label="Downloaded" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}
                   className="h-1.5 overflow-hidden rounded-full bg-raised">
                <span className="block h-full rounded-full bg-tungsten transition-[width]" style={{ width: `${pct}%` }} />
              </div>
              <p className="tabular text-muted">{gb(job.done_bytes ?? 0)} of {gb(total)}</p>
            </div>
          )}
          <p className="text-faint">You can leave this page; it keeps going.</p>
        </>
      ) : job.state === "done" ? (
        <p className="flex items-start gap-2 text-chalk">
          <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-done" aria-hidden />
          <span>
            Set up {what}.{" "}
            <span className="text-muted">
              {job.restart ? "Restart ComfyUI to load the new node pack." : "ComfyUI sees the new files within a minute."}
            </span>
          </span>
        </p>
      ) : (
        <div className="space-y-1">
          <p className="text-fault">Setting up {what} didn&rsquo;t finish:</p>
          <ul className="list-disc space-y-0.5 pl-5 text-muted">
            {(job.failed ?? []).map((f) => <li key={f} className="break-words">{f}</li>)}
          </ul>
        </div>
      )}
      {job.state !== "running" && (
        <div className="flex gap-2">
          {job.state === "failed" && <Button size="sm" tone="quiet" onClick={onRetry}>Try again</Button>}
          <Button size="sm" tone="ghost" onClick={onClose}>Close</Button>
        </div>
      )}
    </div>
  );
}
