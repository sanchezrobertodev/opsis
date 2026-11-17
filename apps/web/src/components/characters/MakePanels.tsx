"use client";

import { useState } from "react";
import { cn } from "@/lib/cn";
import type { Element } from "@/lib/elements";
import type { RunCreate } from "@/lib/runs";
import { useModelsFor } from "@/components/create/QuickGenerate";
import { ModelSelect, quotePrice, quoteText, useRunQuote, useStartQuoted } from "@/components/runs/RunQuote";
import { Button, Segmented } from "@/components/ui/controls";

const REFS_MODEL = "nano-banana-pro"; // character_sheet's and character_photos' own default
const REFS_TRADEOFF = "Nano Banana Pro keeps the face closest to the references; other models cost less or more but "
  + "can drift from the likeness.";

/** Models that draw from a character's pictures (their reference mode). */
function useRefModels() {
  return useModelsFor("multi_ref").filter((m) => m.output_type === "image");
}

function GeneratePanel({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <section aria-label={title}
             className="space-y-3 rounded-[var(--radius-control)] border border-tungsten/30 bg-bay/60 p-4 md:col-span-2">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm text-chalk">{title}</h3>
        <button type="button" onClick={onClose} className="text-2xs text-muted hover:text-chalk">Close</button>
      </div>
      {children}
    </section>
  );
}

/** One picture of the character from every side, kept right after its main picture so a mention attaches both. */
export function SheetPanel({ element, onClose }: { element: Element; onClose: () => void }) {
  const models = useRefModels();
  const [model, setModel] = useState(REFS_MODEL);
  const body: RunCreate = { recipe: "character_sheet",
                            input: { element_id: element.id, slug: element.slug, image_model_id: model } };
  const quote = useRunQuote(body);
  const start = useStartQuoted(`Drawing ${element.handle} from every side. The sheet becomes their second picture.`);
  return (
    <GeneratePanel title="Reference sheet" onClose={onClose}>
      <p className="text-xs leading-relaxed text-muted">
        One wide picture of {element.handle} five times, drawn from its pictures: side, front, three-quarter from
        behind, limbs raised, and a close-up of the head. It&rsquo;s kept as their second picture, so every prompt
        that mentions them shows the model their whole shape.
      </p>
      <div className="max-w-sm">
        <ModelSelect id="sheet-model" models={models} recommended={REFS_MODEL} value={model} onChange={setModel}
                     tradeoff={REFS_TRADEOFF} />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" tone="primary" size="sm"
                disabled={quote.count == null || Boolean(quote.error) || start.pending}
                onClick={() => void start.go(body, quote)}>
          Make the sheet{quotePrice(quote) && ` · ${quotePrice(quote)}`}
        </Button>
        <span className="text-2xs text-faint">{quoteText(quote, ["picture", "pictures"])}</span>
      </div>
    </GeneratePanel>
  );
}

const VARY = [["angle", "Angles"], ["expression", "Expressions"], ["light", "Lighting"], ["framing", "Framing"]] as const;
const COUNTS = [4, 8, 12, 16] as const;

/** New pictures of the character from other angles, with other expressions, light and framing, added to its
 * pictures: the varied photos a trained identity needs, made from the few it has. */
export function MorePictures({ element, count: wanted, onClose }: { element: Element; count?: number; onClose: () => void }) {
  const models = useRefModels();
  const [model, setModel] = useState(REFS_MODEL);
  const [count, setCount] = useState<(typeof COUNTS)[number]>(COUNTS.find((c) => c >= (wanted ?? 8)) ?? 16);
  const [vary, setVary] = useState<string[]>(VARY.map(([v]) => v));
  const body: RunCreate = { recipe: "character_photos",
                            input: { element_id: element.id, slug: element.slug, count, vary, image_model_id: model } };
  const quote = useRunQuote(body);
  const start = useStartQuoted(`Drawing ${count} more pictures of ${element.handle}. Each joins their pictures when it's done.`);
  return (
    <GeneratePanel title="More pictures" onClose={onClose}>
      <p className="text-xs leading-relaxed text-muted">
        {element.name} drawn from their pictures in new poses, each added to their pictures. A trained identity wants
        10 or more varied photos. Remove any that don&rsquo;t look right afterwards.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5 text-xs text-muted">
          How many
          <Segmented label="How many pictures" value={count} options={COUNTS} render={(c) => String(c)} onChange={setCount} />
        </div>
        <ModelSelect id="more-model" models={models} recommended={REFS_MODEL} value={model} onChange={setModel}
                     tradeoff={REFS_TRADEOFF} />
      </div>
      <fieldset className="flex flex-wrap items-center gap-2">
        <legend className="mb-1.5 text-xs text-muted">Vary</legend>
        {VARY.map(([v, label]) => {
          const on = vary.includes(v);
          return (
            <button key={v} type="button" aria-pressed={on}
                    onClick={() => setVary(on ? vary.filter((x) => x !== v) : [...vary, v])}
                    className={cn("h-8 rounded-full border px-3 text-xs transition-colors",
                                  on ? "border-tungsten/60 bg-tungsten/15 text-chalk" : "border-line-soft text-muted hover:text-chalk")}>
              {label}
            </button>
          );
        })}
        {!vary.length && <span className="text-2xs text-faint">Nothing varied: every picture comes out alike.</span>}
      </fieldset>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" tone="primary" size="sm"
                disabled={quote.count == null || Boolean(quote.error) || start.pending}
                onClick={() => void start.go(body, quote)}>
          Make {count} pictures{quotePrice(quote) && ` · ${quotePrice(quote)}`}
        </Button>
        <span className="text-2xs text-faint">{quoteText(quote, ["picture", "pictures"])}</span>
      </div>
    </GeneratePanel>
  );
}
