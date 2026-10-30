"use client";

import { ArrowLeftToLine, ArrowRightToLine, Scissors, Wand2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { type CinemaDoc, type Shot } from "@/lib/cinema";
import { useModel, useRegistry } from "@/lib/queries";
import type { RunCreate } from "@/lib/runs";
import { quotePrice, quoteText, useRunQuote } from "@/components/runs/RunQuote";
import { Button, Field, Segmented, Select, Slider } from "@/components/ui/controls";
import type { Launch } from "./Editor";
import { mergedDirection } from "./Storyboard";

type What = "after" | "before" | "edit";

const COPY: Record<What, { label: string; placeholder: string; go: string; hint: string }> = {
  after: { label: "What happens next", placeholder: "She lifts the lamp and turns toward the window",
           go: "Continue the take", hint: "Picks up from the take's last frame; the new take is both parts joined." },
  before: { label: "What happens just before", placeholder: "She comes up the last steps, out of breath",
            go: "Lead into the take", hint: "Draws a frame from a moment earlier, then animates from it into the take's first frame." },
  edit: { label: "What to change", placeholder: "Make it rain outside the windows",
          go: "Edit the take", hint: "An Edit Video model changes the whole take by instruction." },
};

/** More of a picked take, or a changed version of it. Each result is a new take of the shot, made from this one. */
export function TakeActions({ projectId, doc, shot, takeId, launch }: {
  projectId: string; doc: CinemaDoc; shot: Shot; takeId: string; launch: Launch;
}) {
  const { data: registry } = useRegistry();
  const [what, setWhat] = useState<What>("after");
  const [prompt, setPrompt] = useState("");
  const [seconds, setSeconds] = useState(4);
  const editors = (registry?.models ?? []).filter((m) => m.output_type === "video" && m.kind !== "virtual"
    && m.modes.some((x) => x.ops.includes("instruction_edit")));
  const [editor, setEditor] = useState<string | null>(null);
  const editModel = editor ?? editors[0]?.id ?? "";
  const params = { ...doc.settings.model_params, aspect_ratio: doc.settings.aspect_ratio, resolution: doc.settings.resolution };
  const film = useModel(doc.settings.model_id).data?.name ?? doc.settings.model_id;
  const image = useModel(doc.settings.image_model_id).data?.name ?? doc.settings.image_model_id;

  // priced before anything is written: the words don't change what it costs
  const body = (text: string): RunCreate => {
    const common = { project_id: projectId, shot_key: shot.id, take_asset_id: takeId, prompt: text };
    return what === "edit" ? { recipe: "cinema_edit", input: { ...common, model_id: editModel } }
      : { recipe: "cinema_extend", input: {
          ...common, side: what, direction: mergedDirection(doc.settings, shot), model_id: doc.settings.model_id,
          params, duration: seconds, image_model_id: doc.settings.image_model_id } };
  };
  const quote = useRunQuote(what === "edit" && !editModel ? null : body(prompt.trim() || "x"));

  async function go() {
    const text = prompt.trim();
    if (!text) return;
    await launch({ ...body(text), confirm_cost_usd: quote.charged },
                 what === "edit" ? "Editing" : what === "after" ? "Continuing" : "Leading in");
    setPrompt("");
  }

  return (
    <section className="flex flex-col gap-3 rounded-[var(--radius-panel)] bg-panel p-3">
      <h3 className="text-xs text-muted">Change the take in the cut</h3>
      <Segmented label="Change" value={what} options={["after", "before", "edit"] as const} onChange={setWhat}
                 render={(v) => (v === "after" ? <><ArrowRightToLine className="mr-1 inline size-3.5" aria-hidden />After</>
                   : v === "before" ? <><ArrowLeftToLine className="mr-1 inline size-3.5" aria-hidden />Before</>
                     : <><Wand2 className="mr-1 inline size-3.5" aria-hidden />Edit</>)} />
      <Field label={COPY[what].label} htmlFor="take-change" hint={COPY[what].hint}>
        <textarea id="take-change" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={2} placeholder={COPY[what].placeholder}
                  className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm outline-none placeholder:text-faint focus:border-tungsten/60" />
      </Field>
      {what === "edit" ? (
        <Field label="Edit with" htmlFor="take-editor">
          <Select id="take-editor" value={editModel} onChange={setEditor} options={editors.map((m) => ({ value: m.id, label: m.name }))} />
        </Field>
      ) : (
        <Field label="New part">
          <Slider value={seconds} min={2} max={10} step={1} onChange={setSeconds} format={(v) => `${v} s`} />
        </Field>
      )}
      {what !== "edit" && (
        <p className="text-2xs text-faint">
          On {film}{what === "before" && `, with the earlier frame drawn by ${image}`}. Change them in Film settings.
        </p>
      )}
      <Button size="sm" tone="primary" onClick={() => void go()} disabled={!prompt.trim() || (what === "edit" && !editModel)}>
        {COPY[what].go}{quotePrice(quote) && ` · ${quotePrice(quote)}`}
      </Button>
      <p className="-mt-1 text-2xs text-faint">{quoteText(quote, ["take", "takes"])}</p>
      <Link href={`/swap?video=${takeId}&project=${projectId}&shot=${shot.id}`}
            className="flex items-center gap-1.5 text-2xs text-muted hover:text-chalk">
        <Scissors className="size-3.5" aria-hidden />
        Change one object instead, in Object swap
      </Link>
    </section>
  );
}
