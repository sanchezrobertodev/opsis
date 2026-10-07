"use client";

import { History } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { type CinemaDoc, useProject, useRestoreRevision, useRevisions, useProjectRunsActive, useRunsActive, useSaveProject, useTakes } from "@/lib/cinema";
import { type RunCreate, useStartRun } from "@/lib/runs";
import { Button, Dialog, Segmented } from "@/components/ui/controls";
import { WHEN } from "@/components/runs/Runs";
import { CutTab } from "./CutTab";
import { ScriptTab } from "./ScriptTab";
import { Storyboard } from "./Storyboard";

type Tab = "script" | "storyboard" | "cut";
const TAB_LABEL: Record<Tab, string> = { script: "Script", storyboard: "Storyboard", cut: "Cut" };
const SAVE_AFTER_MS = 1200;

export type Launch = (body: RunCreate, label: string) => Promise<string | null>;

/** One film: the script, the storyboard of shots with their frames and takes, and the cut. Saves as you go. */
export function CinemaEditor({ id }: { id: string }) {
  const { data: project, isLoading, refetch } = useProject(id);
  const save = useSaveProject(id);
  const startRun = useStartRun();
  const [edited, setEdited] = useState<{ doc: CinemaDoc; title: string } | null>(null);
  const [tab, setTab] = useState<Tab>("storyboard");
  const [runIds, setRunIds] = useState<string[]>([]);
  const [history, setHistory] = useState(false);
  const { active: mine, runs } = useRunsActive(runIds);
  const elsewhere = useProjectRunsActive(id);
  const active = mine || elsewhere;
  const takes = useTakes(id, active);

  const doc = edited?.doc ?? (project?.document as CinemaDoc | undefined);
  const title = edited?.title ?? project?.title ?? "";

  // save a moment after the last edit; a newer edit waits for the save in flight
  useEffect(() => {
    if (!edited || !project || save.isPending) return;
    const timer = setTimeout(() => {
      save.mutate({ version: project.version, document: edited.doc, title: edited.title || project.title }, {
        onSuccess: () => setEdited((cur) => (cur && cur.doc === edited.doc && cur.title === edited.title ? null : cur)),
        onError: (err) => {
          if (err instanceof ApiError && err.status === 409) {
            toast.error("This film was saved somewhere else. Reloaded the latest version.");
            setEdited(null);
            void refetch();
          } else {
            toast.error(err instanceof Error ? err.message : "Couldn't save the film");
          }
        },
      });
    }, SAVE_AFTER_MS);
    return () => clearTimeout(timer);
  }, [edited, project, save, refetch]);

  if (isLoading) return null;
  if (!project || !doc) return <div className="p-6 text-sm text-muted">This film doesn&rsquo;t exist or was removed.</div>;

  const update = (fn: (d: CinemaDoc) => CinemaDoc) => setEdited((cur) => ({ doc: fn(cur?.doc ?? doc), title: cur?.title ?? title }));

  const launch: Launch = async (body, label) => {
    try {
      const run = await startRun.mutateAsync(body);
      setRunIds((ids) => [...ids, run.id]);
      toast(`${label} started`);
      return run.id;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const estimate = (err.body as { estimate_usd: number }).estimate_usd;
        toast(`${label} is estimated at ${usd(estimate)}`, {
          action: { label: `Start for ${usd(estimate)}`, onClick: () => void launch({ ...body, confirm_cost_usd: estimate }, label) },
          duration: 15000,
        });
        return null;
      }
      toast.error(err instanceof Error ? err.message : `${label} couldn't start`);
      return null;
    }
  };

  const status = save.isPending ? "Saving…" : edited ? "Unsaved changes" : `Saved, version ${project.version}`;
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-line-soft px-6 py-3">
        <input
          value={title}
          onChange={(e) => setEdited({ doc: edited?.doc ?? doc, title: e.target.value })}
          aria-label="Film title"
          className="type-title min-w-0 flex-1 bg-transparent text-lg outline-none focus:text-chalk"
        />
        <span className={cn("tabular text-2xs", edited ? "text-tungsten" : "text-faint")} aria-live="polite">{status}</span>
        {active && <span className="text-2xs text-tungsten">Rendering…</span>}
        <Button size="sm" tone="ghost" onClick={() => setHistory(true)}>
          <History className="size-4" aria-hidden />
          History
        </Button>
        <div className="w-80">
          <Segmented label="View" value={tab} options={["script", "storyboard", "cut"] as const} render={(t) => TAB_LABEL[t]}
                     onChange={setTab} />
        </div>
      </div>
      <div className="min-h-0 flex-1">
        {tab === "script" && <ScriptTab projectId={id} doc={doc} update={update} onPlanned={() => setTab("storyboard")} />}
        {tab === "storyboard" && (
          <Storyboard projectId={id} doc={doc} update={update} takes={takes.data ?? []} launch={launch} busy={active} />
        )}
        {tab === "cut" && <CutTab projectId={id} title={title} doc={doc} update={update} takes={takes.data ?? []}
                                  launch={launch} runs={runs} />}
      </div>
      <HistoryDialog id={id} open={history} onClose={() => setHistory(false)} onRestored={() => setEdited(null)} />
    </div>
  );
}

export function HistoryDialog({ id, open, onClose, onRestored }: { id: string; open: boolean; onClose: () => void;
                                                             onRestored: () => void }) {
  const { data: revisions } = useRevisions(id, open);
  const restore = useRestoreRevision(id);
  return (
    <Dialog open={open} onClose={onClose} title="History" wide>
      <p className="mb-3">Every save is kept. Restoring makes an older version current again, as a new version.</p>
      <ul className="flex max-h-[60vh] flex-col gap-1 overflow-y-auto">
        {revisions?.map((r, i) => (
          <li key={r.version} className="flex items-center gap-3 rounded-[4px] px-2 py-1.5 hover:bg-raised">
            <span className="tabular w-10 text-faint">v{r.version}</span>
            <span className="min-w-0 flex-1 truncate text-chalk">{r.note || r.title}</span>
            <span className="tabular text-2xs text-faint">{r.shots ? `${r.shots} shots, ` : ""}{WHEN.format(new Date(r.created_at))}</span>
            {i > 0 && (
              <Button size="sm" tone="ghost" disabled={restore.isPending}
                      onClick={() => restore.mutate(r.version, { onSuccess: () => { onRestored(); onClose(); toast(`Restored version ${r.version}`); } })}>
                Restore
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Dialog>
  );
}
