"use client";

import { Check, Circle, LoaderCircle, Minus, X } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { type Asset, mediaUrl } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import {
  RUN_STATUS_TEXT,
  type Run,
  type Step,
  useAnswerCheckpoint,
  useCancelRun,
  useRetryRun,
  useRun,
} from "@/lib/runs";
import { PlanBlocks, ScriptReview } from "@/components/faceless/ScriptReview";
import { Button } from "@/components/ui/controls";
import { statusTone, WHEN } from "./Runs";

const OUTPUT_TEXT: Record<string, string> = {
  sequence: "The joined cut.",
  compare: "The take you kept.",
  keyframe: "The animated shot; its opening frame is in the library too.",
  object_swap: "The take you kept. The other takes and the tracked mask are in the library too.",
  faceless: "The finished episode. The clean master, the captions file and the thumbnail are in the library too.",
};

const KIND_TEXT: Record<string, string> = {
  generate: "Generation",
  local: "Local step",
  llm: "Claude",
  checkpoint: "Your call",
};

export function RunDetail({ id }: { id: string }) {
  const { data: run, isLoading, error } = useRun(id);
  if (isLoading) return null;
  if (error || !run) {
    return <div className="p-6 text-sm text-muted">This run doesn&rsquo;t exist or was removed.</div>;
  }
  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[1fr_380px]">
      <div className="min-h-0 overflow-y-auto p-6 scrollbar-quiet">
        <RunHeader run={run} />
        <Output run={run} />
        <ol className="mt-8 flex max-w-3xl flex-col gap-2">
          {run.steps.map((s) => (
            <StepRow key={s.key} run={run} step={s} />
          ))}
        </ol>
      </div>
      <aside className="border-t border-line-soft p-5 text-xs lg:border-t-0 lg:border-l">
        <dl className="tabular grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
          <dt className="text-faint">Recipe</dt>
          <dd>{run.recipe_name}</dd>
          <dt className="text-faint">Started</dt>
          <dd>{WHEN.format(new Date(run.created_at))}</dd>
          {run.finished_at && (
            <>
              <dt className="text-faint">Finished</dt>
              <dd>{WHEN.format(new Date(run.finished_at))}</dd>
            </>
          )}
          <dt className="text-faint">Spent</dt>
          <dd>{usd(run.spent_usd, { free: "Nothing (free routes)" })}</dd>
          <dt className="text-faint">Estimate</dt>
          <dd>{usd(run.estimate_usd, { free: "Free" })}</dd>
          <dt className="text-faint">Run cap</dt>
          <dd>{usd(run.cap_usd)}</dd>
        </dl>
        <p className="mt-4 leading-relaxed text-faint">
          Every take in this run is also in your library and in Create, where you can retry or animate it further.
        </p>
      </aside>
    </div>
  );
}

function RunHeader({ run }: { run: Run }) {
  const cancel = useCancelRun();
  const retry = useRetryRun();
  const active = run.status === "running" || run.status === "waiting";
  return (
    <div className="mb-6 flex max-w-3xl flex-wrap items-start gap-4">
      <div className="min-w-0 flex-1">
        <h2 className="type-title text-xl">{run.title}</h2>
        <p className={cn("mt-1 text-sm", statusTone(run.status))}>
          {RUN_STATUS_TEXT[run.status] ?? run.status}
          <span className="tabular text-faint">
            , {run.steps_done} of {run.steps_total} steps
          </span>
        </p>
        {run.error && <p className="mt-2 text-sm leading-relaxed text-fault">{run.error}</p>}
      </div>
      {active && (
        <Button size="sm" tone="danger" onClick={() => cancel.mutate(run.id)} disabled={cancel.isPending}>
          Cancel run
        </Button>
      )}
      {(run.status === "failed" || run.status === "canceled") && (
        <Button
          size="sm"
          tone="primary"
          onClick={() => retry.mutate(run.id, { onSuccess: () => toast("Retrying the steps that didn't finish") })}
          disabled={retry.isPending}
        >
          Retry what failed
        </Button>
      )}
    </div>
  );
}

function Media({ asset, className }: { asset: Asset; className?: string }) {
  return asset.kind === "video" ? (
    <video
      src={mediaUrl(asset.proxy_url ?? asset.url)}
      poster={mediaUrl(asset.poster_url)}
      controls
      playsInline
      className={cn("bg-black", className)}
    />
  ) : (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={mediaUrl(asset.url)} alt={asset.title ?? ""} className={className} />
  );
}

function Output({ run }: { run: Run }) {
  if (!run.output_asset) return null;
  return (
    <section className="max-w-3xl">
      <Media asset={run.output_asset} className="w-full rounded-[var(--radius-frame)]" />
      <p className="mt-2 text-xs text-muted">
        {OUTPUT_TEXT[run.recipe] ?? "The result."}{" "}
        <Link href="/library" className="text-chalk underline">
          Open the library
        </Link>{" "}
        to download or reuse it.
      </p>
    </section>
  );
}

function StatusIcon({ step }: { step: Step }) {
  const cls = "size-3.5 shrink-0";
  switch (step.status) {
    case "succeeded":
      return <Check className={cn(cls, "text-done")} aria-hidden />;
    case "failed":
      return <X className={cn(cls, "text-fault")} aria-hidden />;
    case "running":
      return <LoaderCircle className={cn(cls, "animate-spin text-tungsten")} aria-hidden />;
    case "waiting":
      return <Circle className={cn(cls, "fill-tungsten text-tungsten")} aria-hidden />;
    case "canceled":
      return <Minus className={cn(cls, "text-faint")} aria-hidden />;
    default:
      return <Circle className={cn(cls, "text-line")} aria-hidden />;
  }
}

function StepRow({ run, step }: { run: Run; step: Step }) {
  const answer = useAnswerCheckpoint();
  const running = step.status === "running" && step.kind === "generate";
  return (
    <li className="rounded-[var(--radius-panel)] bg-panel p-3">
      <div className="flex items-center gap-3">
        <StatusIcon step={step} />
        <span className="flex-1 text-sm text-chalk">{step.label}</span>
        <span className="text-2xs text-faint">
          {KIND_TEXT[step.kind] ?? step.kind}
          {running && step.progress != null && step.progress > 0 && `, ${Math.round(step.progress * 100)}%`}
          {running && step.generation_status === "queued" && ", waiting for a worker"}
        </span>
      </div>
      {step.error && <p className="mt-2 pl-6 text-xs leading-relaxed text-fault">{step.error}</p>}
      {step.assets.length > 0 && (
        <div className="mt-3 flex gap-2 pl-6">
          {step.assets.slice(0, 4).map((a) => (
            <span key={a.id} className="h-16 w-28 overflow-hidden rounded-[var(--radius-frame)] bg-bay">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={mediaUrl(a.thumb_url ?? a.poster_url ?? a.url)} alt={a.title ?? ""} className="size-full object-cover" />
            </span>
          ))}
        </div>
      )}
      {run.recipe === "faceless" && step.key === "plan" && step.status === "succeeded" && <PlanBlocks step={step} />}
      {step.kind === "checkpoint" && step.status === "waiting" && !(step.options ?? []).some((o) => o.asset) && (
        run.recipe === "faceless" && (step.key === "review" || step.key === "length") ? (
          <ScriptReview run={run} step={step} />
        ) : (
          <div className="mt-3 flex gap-2 pl-6">
            {(step.options ?? []).map((o) => (
              <Button key={String(o.value)} size="sm" tone="primary" disabled={answer.isPending}
                      onClick={() => answer.mutate({ id: run.id, key: step.key, choice: String(o.value) })}>
                {String(o.label)}
              </Button>
            ))}
          </div>
        )
      )}
      {step.kind === "checkpoint" && step.status === "waiting" && (step.options ?? []).some((o) => o.asset) && (
        <div className="mt-3 pl-6">
          <p className="mb-2 text-xs text-muted">Which one do you want to keep? It gets a star in your library.</p>
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">
            {(step.options ?? []).map((o) => {
              const asset = o.asset as Asset | null | undefined;
              return (
                <li key={String(o.value)} className="flex flex-col gap-2">
                  {asset && <Media asset={asset} className="aspect-video w-full rounded-[var(--radius-frame)] object-cover" />}
                  <Button
                    size="sm"
                    onClick={() => answer.mutate({ id: run.id, key: step.key, choice: String(o.value) })}
                    disabled={answer.isPending}
                  >
                    Keep {String(o.label)}
                  </Button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </li>
  );
}
