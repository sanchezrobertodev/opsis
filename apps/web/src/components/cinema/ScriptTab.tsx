"use client";

import { LoaderCircle, Sparkles } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/cn";
import { type CinemaDoc, type Scene, usePlanShots } from "@/lib/cinema";
import { useElements } from "@/lib/elements";
import { Button, Field, Slider } from "@/components/ui/controls";

/** Logline, script and cast, and Claude's proposed shot list to apply to the storyboard. */
export function ScriptTab({ projectId, doc, update, onPlanned }: {
  projectId: string; doc: CinemaDoc; update: (fn: (d: CinemaDoc) => CinemaDoc) => void; onPlanned: () => void;
}) {
  const { data: elements } = useElements();
  const plan = usePlanShots(projectId);
  const [maxShots, setMaxShots] = useState(12);
  const [notes, setNotes] = useState("");
  const [proposal, setProposal] = useState<{ scenes: Scene[]; warnings: string[] } | null>(null);

  async function ask() {
    try {
      // send what's on screen, which autosave may not have stored yet
      const res = await plan.mutateAsync({ max_shots: maxShots, notes, script: doc.script, logline: doc.logline, cast: doc.cast });
      setProposal({ scenes: res.scenes as unknown as Scene[], warnings: res.warnings });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Claude couldn't plan the shots");
    }
  }

  function apply(mode: "replace" | "append") {
    if (!proposal) return;
    update((d) => ({ ...d, scenes: mode === "replace" ? proposal.scenes : [...d.scenes, ...proposal.scenes] }));
    setProposal(null);
    onPlanned();
  }

  const toggleCast = (slug: string) => update((d) => ({
    ...d, cast: d.cast.includes(slug) ? d.cast.filter((c) => c !== slug) : [...d.cast, slug] }));
  const shotCount = proposal?.scenes.reduce((n, s) => n + s.shots.length, 0) ?? 0;

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-h-0 overflow-y-auto p-6 scrollbar-quiet">
        <div className="flex max-w-3xl flex-col gap-5">
          <Field label="Logline" htmlFor="logline">
            <input id="logline" value={doc.logline} onChange={(e) => update((d) => ({ ...d, logline: e.target.value }))}
                   placeholder="One sentence: who wants what, and what stands in the way"
                   className="h-10 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm outline-none placeholder:text-faint focus:border-tungsten/60" />
          </Field>
          <Field label="Script" htmlFor="script" hint="Scene headings, action and dialogue. Plain prose works too.">
            <textarea id="script" value={doc.script} onChange={(e) => update((d) => ({ ...d, script: e.target.value }))}
                      rows={20} placeholder={"EXT. HARBOUR - DAWN\n\nA fishing boat slips out past the breakwater..."}
                      className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 font-mono text-[13px] leading-relaxed outline-none placeholder:text-faint focus:border-tungsten/60" />
          </Field>
        </div>
      </div>
      <aside className="flex min-h-0 flex-col gap-5 overflow-y-auto border-t border-line-soft p-5 scrollbar-quiet lg:border-t-0 lg:border-l">
        <Field label="Cast" hint="Saved characters, places and props. Claude writes them into shots as @mentions.">
          {elements?.length ? (
            <ul className="flex flex-wrap gap-1.5">
              {elements.map((e) => (
                <li key={e.id}>
                  <button type="button" onClick={() => toggleCast(e.slug)} aria-pressed={doc.cast.includes(e.slug)}
                          className={cn("rounded-full border px-2.5 py-1 text-xs transition-colors",
                                        doc.cast.includes(e.slug) ? "border-tungsten/60 bg-raised text-chalk" : "border-line-soft text-muted hover:text-chalk")}>
                    @{e.slug}
                  </button>
                </li>
              ))}
            </ul>
          ) : <p className="text-xs text-faint">No saved characters yet. Make some in Characters.</p>}
        </Field>
        <Field label="Shot budget">
          <Slider value={maxShots} min={2} max={40} step={1} onChange={setMaxShots} format={(v) => `${v} shots`} />
        </Field>
        <Field label="Notes for the director (optional)" htmlFor="plan-notes">
          <textarea id="plan-notes" value={notes} onChange={(e) => setNotes(e.target.value)} rows={3}
                    placeholder="Mostly handheld; keep the lighthouse in frame"
                    className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm outline-none placeholder:text-faint focus:border-tungsten/60" />
        </Field>
        <Button tone="primary" onClick={() => void ask()} disabled={plan.isPending || !(doc.script.trim() || doc.logline.trim())}>
          {plan.isPending ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <Sparkles className="size-4" aria-hidden />}
          Plan shots with Claude
        </Button>

        {proposal && (
          <section className="flex flex-col gap-3 rounded-[var(--radius-panel)] bg-panel p-3">
            <p className="text-sm text-chalk">{proposal.scenes.length} scenes, {shotCount} shots proposed</p>
            {proposal.warnings.map((w) => <p key={w} className="text-2xs text-tungsten">{w}</p>)}
            <ol className="flex max-h-72 flex-col gap-2 overflow-y-auto text-xs">
              {proposal.scenes.map((s) => (
                <li key={s.id}>
                  <p className="text-chalk">{s.heading}</p>
                  <ol className="mt-1 flex flex-col gap-1 pl-3 text-muted">
                    {s.shots.map((shot) => <li key={shot.id}>{shot.action}</li>)}
                  </ol>
                </li>
              ))}
            </ol>
            <div className="flex gap-2">
              <Button size="sm" tone="primary" onClick={() => apply("replace")}>
                {doc.scenes.length ? "Replace the storyboard" : "Use this plan"}
              </Button>
              {doc.scenes.length > 0 && <Button size="sm" onClick={() => apply("append")}>Add after it</Button>}
              <Button size="sm" tone="ghost" onClick={() => setProposal(null)}>Discard</Button>
            </div>
          </section>
        )}
      </aside>
    </div>
  );
}
