"use client";

import { useState } from "react";
import { toast } from "sonner";
import { type Run, type Step, useAnswerCheckpoint, useReviseRun } from "@/lib/runs";
import { Button } from "@/components/ui/controls";

const WORDS_PER_MINUTE = 150;

type FacelessRunInput = { script?: string | null; title?: string | null };

/** The faceless `review` checkpoint: read the script, edit it if you like, then approve it. */
export function ScriptReview({ run, step }: { run: Run; step: Step }) {
  const revise = useReviseRun();
  const answer = useAnswerCheckpoint();
  const input = run.input as FacelessRunInput;
  const written = run.steps.find((s) => s.key === "script")?.output as
    { script?: string; title?: string; pace?: Pace } | undefined;
  const over = step.key === "length";
  const current = input.script ?? written?.script ?? "";
  const currentTitle = input.title ?? written?.title ?? "";
  // unsaved edits; null shows the run's current script
  const [draft, setDraft] = useState<{ text: string; title: string } | null>(null);
  const text = draft?.text ?? current;
  const title = draft?.title ?? currentTitle;
  const setText = (v: string) => setDraft({ text: v, title });
  const setTitle = (v: string) => setDraft({ text, title: v });

  const dirty = text.trim() !== current.trim() || title.trim() !== currentTitle.trim();
  const words = text.trim().split(/\s+/).filter(Boolean).length;

  function save() {
    revise.mutate({ id: run.id, input: { ...run.input, script: text.trim(), title: title.trim() || null } }, {
      onSuccess: () => {
        setDraft(null);
        toast("Script saved. Approve it when you're happy.");
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : "Could not save the script"),
    });
  }

  return (
    <div className="mt-3 flex flex-col gap-3 pl-6">
      <p className="text-xs text-muted">
        {over
          ? `${step.label}. Shorten the script and save it to voice it again (its pictures are drawn again too), or make it anyway.`
          : "Read it through. Nothing is narrated or illustrated until you approve it."}
      </p>
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        aria-label="Title"
        className="type-title h-10 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-base outline-none focus:border-tungsten/60"
      />
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={Math.min(18, Math.max(6, Math.ceil(text.length / 90)))}
        aria-label="Script"
        className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm leading-relaxed outline-none focus:border-tungsten/60"
      />
      <div className="flex flex-wrap items-center gap-3">
        <span className="tabular text-2xs text-faint">
          {words} words, {written?.pace ? `about ${duration(runtime(words, written.pace))} at the narrator's pace`
                                        : `about ${(words / WORDS_PER_MINUTE).toFixed(1)} min`}
        </span>
        <div className="ml-auto flex gap-2">
          {dirty && (
            <Button size="sm" onClick={save} disabled={revise.isPending || words < 5}>
              Save changes
            </Button>
          )}
          <Button
            size="sm"
            tone="primary"
            onClick={() => answer.mutate({ id: run.id, key: step.key, choice: "approve" })}
            disabled={dirty || answer.isPending}
            title={dirty ? "Save your changes first" : undefined}
          >
            {over ? "Make it anyway" : "Use this script"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** The narrator's measured pace and the pauses the cut adds (the script step's `pace`). */
type Pace = { wpm: number; block_s: number; gap_s: number; tail_s: number };

/** How long an episode of this many words runs, like the server's `writer.episode_seconds`. */
function runtime(words: number, pace: Pace): number {
  const speech = (words / pace.wpm) * 60;
  return speech + pace.gap_s * (Math.max(1, Math.ceil(speech / pace.block_s)) - 1) + pace.tail_s;
}

function duration(seconds: number): string {
  return seconds < 120 ? `${Math.round(seconds)} s` : `${(seconds / 60).toFixed(1)} min`;
}

type PlanBlock = { narration: string; visual: string; motion?: string };

/** What Claude planned: each block's narration and what the viewer sees during it. */
export function PlanBlocks({ step }: { step: Step }) {
  const blocks = (step.output.blocks ?? []) as PlanBlock[];
  if (!blocks.length) return null;
  return (
    <details className="mt-2 pl-6 text-xs">
      <summary className="cursor-pointer text-muted hover:text-chalk">
        {blocks.length} blocks{step.output.world ? `: ${String(step.output.world)}` : ""}
      </summary>
      <ol className="mt-2 flex flex-col gap-2">
        {blocks.map((b, i) => (
          <li key={i} className="grid grid-cols-[2rem_1fr] gap-x-2">
            <span className="tabular text-faint">{String(i + 1).padStart(2, "0")}</span>
            <span className="text-chalk">{b.narration}</span>
            <span />
            <span className="text-faint">{b.visual}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}
