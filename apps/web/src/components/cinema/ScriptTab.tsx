"use client";

import { LoaderCircle, Sparkles, UserRound } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { cn, usd } from "@/lib/cn";
import { type CinemaDoc, type ProposedCharacter, type Scene, unmention, usePlanShots, useSaveCast } from "@/lib/cinema";
import { useElements } from "@/lib/elements";
import { useEstimateRun } from "@/lib/runs";
import { Button, Field, Slider } from "@/components/ui/controls";
import type { Launch } from "./Editor";

const NO_ID = "00000000-0000-0000-0000-000000000000"; // pricing only: the characters don't exist yet
const MAX_CAST = 12; // CinemaDocument.cast

/** Logline, script and cast, and Claude's proposed shot list to apply to the storyboard. */
export function ScriptTab({ projectId, doc, update, onPlanned, launch }: {
  projectId: string; doc: CinemaDoc; update: (fn: (d: CinemaDoc) => CinemaDoc) => void; onPlanned: () => void;
  launch: Launch;
}) {
  const { data: elements } = useElements();
  const plan = usePlanShots(projectId);
  const saveCast = useSaveCast(projectId);
  const [maxShots, setMaxShots] = useState(12);
  const [notes, setNotes] = useState("");
  const [proposal, setProposal] = useState<{ scenes: Scene[]; characters: ProposedCharacter[]; warnings: string[] } | null>(null);
  const [dropped, setDropped] = useState<string[]>([]); // proposed characters the user won't keep
  const kept = proposal?.characters.filter((c) => !dropped.includes(c.handle)) ?? [];
  const { data: estimate } = useEstimateRun(kept.length ? { recipe: "cinema_cast", input: {
    characters: kept.map((c) => ({ element_id: NO_ID, slug: c.handle })) } } : null);
  const portraitsUsd = estimate ? (estimate.estimate_usd || estimate.live_estimate_usd) : null;
  const room = MAX_CAST - doc.cast.length;
  const tooMany = kept.length > room; // the film couldn't save a cast that big

  async function ask() {
    try {
      // send what's on screen, which autosave may not have stored yet
      const res = await plan.mutateAsync({ max_shots: maxShots, notes, script: doc.script, logline: doc.logline, cast: doc.cast });
      setProposal({ scenes: res.scenes as unknown as Scene[], characters: res.characters, warnings: res.warnings });
      setDropped([]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Claude couldn't plan the shots");
    }
  }

  async function apply(mode: "replace" | "append") {
    if (!proposal || tooMany) return;
    // the ones not kept are written as plain names; the kept ones become saved characters with a portrait each
    let scenes = unmention(proposal.scenes, Object.fromEntries(
      proposal.characters.filter((c) => dropped.includes(c.handle)).map((c) => [c.handle, c.name])));
    let slugs: string[] = [];
    let pictures: { element_id: string; slug: string }[] = [];
    if (kept.length) {
      try {
        const res = await saveCast.mutateAsync({ characters: kept, scenes, cast: doc.cast });
        scenes = res.scenes as unknown as Scene[];
        slugs = res.cast.map((m) => m.slug);
        pictures = res.cast.map((m) => ({ element_id: m.id, slug: m.slug }));
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Couldn't save the characters");
        return;
      }
    }
    update((d) => ({ ...d, cast: [...d.cast, ...slugs.filter((s) => !d.cast.includes(s))],
                     scenes: mode === "replace" ? scenes : [...d.scenes, ...scenes] }));
    setProposal(null);
    if (pictures.length) void launch({ recipe: "cinema_cast", input: { project_id: projectId, characters: pictures } },
                                     "Character portraits");
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
            {proposal.characters.length > 0 && (
              <div className="flex flex-col gap-2 border-t border-line-soft pt-3">
                <p className="text-xs text-chalk">Recurring characters</p>
                <ul className="flex flex-col gap-2">
                  {proposal.characters.map((c) => (
                    <li key={c.handle}>
                      <label className="flex items-start gap-2 text-xs">
                        <input type="checkbox" className="mt-0.5 accent-tungsten" checked={!dropped.includes(c.handle)}
                               onChange={(e) => setDropped((d) => e.target.checked ? d.filter((h) => h !== c.handle) : [...d, c.handle])} />
                        <span>
                          <span className="flex items-center gap-1 text-chalk"><UserRound className="size-3" aria-hidden />{c.name}</span>
                          <span className="text-muted">{c.description}</span>
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
                <p className={cn("text-2xs", tooMany ? "text-tungsten" : "text-faint")}>
                  {tooMany
                    ? `A film's cast holds ${MAX_CAST} and this one has ${doc.cast.length}: keep at most ${Math.max(0, room)}.`
                    : kept.length
                    ? `Kept ones are saved as characters with a front portrait${portraitsUsd != null ? ` (${usd(portraitsUsd)})` : ""}, so their face stays the same in every shot.`
                    : "None kept: they're written into the shots by name."}
                </p>
              </div>
            )}
            <div className="flex gap-2">
              <Button size="sm" tone="primary" disabled={saveCast.isPending || tooMany} onClick={() => void apply("replace")}>
                {doc.scenes.length ? "Replace the storyboard" : "Use this plan"}
              </Button>
              {doc.scenes.length > 0 && <Button size="sm" disabled={saveCast.isPending || tooMany} onClick={() => void apply("append")}>Add after it</Button>}
              <Button size="sm" tone="ghost" onClick={() => setProposal(null)}>Discard</Button>
            </div>
          </section>
        )}
      </aside>
    </div>
  );
}
