"use client";

import Link from "next/link";
import { mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { RUN_STATUS_TEXT, type RunSummary, useRuns } from "@/lib/runs";

export const WHEN = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export function statusTone(status: string) {
  return {
    running: "text-tungsten",
    waiting: "text-tungsten",
    succeeded: "text-done",
    failed: "text-fault",
    canceled: "text-faint",
  }[status] ?? "text-muted";
}

/** Every recipe run, newest first. */
export function Runs() {
  const { data, isLoading } = useRuns();
  const runs = data ?? [];
  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-quiet">
      {isLoading ? null : runs.length === 0 ? (
        <div className="mt-20 max-w-md">
          <p className="type-title text-xl">No runs yet</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Runs are multi-step jobs: a <Link href="/sequence" className="text-chalk underline">shot sequence</Link>{" "}
            that continues each shot from the last frame and joins the cut, or a model comparison started from
            Create video.
          </p>
        </div>
      ) : (
        <ul className="grid max-w-5xl gap-2">
          {runs.map((r) => (
            <li key={r.id}>
              <RunRow run={r} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RunRow({ run }: { run: RunSummary }) {
  const thumb = run.output_asset?.thumb_url ?? run.output_asset?.poster_url;
  const pct = run.steps_total ? Math.round((run.steps_done / run.steps_total) * 100) : 0;
  return (
    <Link
      href={`/runs/${run.id}`}
      className="flex items-center gap-4 rounded-[var(--radius-panel)] bg-panel p-3 transition-colors hover:bg-raised"
    >
      <span className="grid h-14 w-24 shrink-0 place-items-center overflow-hidden rounded-[var(--radius-frame)] bg-bay">
        {thumb ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={mediaUrl(thumb)} alt="" className="size-full object-cover" />
        ) : (
          <span className="tabular text-2xs text-faint">{pct}%</span>
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-chalk">{run.title}</span>
        <span className="tabular mt-0.5 block text-2xs text-faint">
          {run.recipe_name}, {run.steps_done} of {run.steps_total} steps, {WHEN.format(new Date(run.created_at))}
        </span>
      </span>
      <span className={cn("shrink-0 text-xs", statusTone(run.status))}>{RUN_STATUS_TEXT[run.status] ?? run.status}</span>
    </Link>
  );
}
