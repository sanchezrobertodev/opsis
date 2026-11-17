"use client";

import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { usd } from "@/lib/cn";
import type { Element } from "@/lib/elements";
import { type RunCreate, useStartRun } from "@/lib/runs";
import { designerStyle } from "@/lib/styles";
import { useModelsFor } from "@/components/create/QuickGenerate";
import { ModelSelect, quoteText, useRunQuote } from "@/components/runs/RunQuote";

/** Draws a character's front portrait from its name and description and keeps it as its picture (the same run that
 * draws a film's new cast). The card shows it when the run is done. */
export const PORTRAIT_MODEL = "nano-banana-2"; // cinema_cast's own default

function portraitRun(e: Pick<Element, "id" | "slug"> & Partial<Pick<Element, "attributes">>, modelId: string): RunCreate {
  // in the picture style and with the expression the designer gave it, which stay out of its description
  const traits = e.attributes?.traits as { style?: string; expression?: string } | undefined;
  const style = designerStyle(traits?.style);
  const expression = traits?.expression;
  return { recipe: "cinema_cast", input: { characters: [{ element_id: e.id, slug: e.slug, ...(style ? { style } : {}),
                                                          ...(expression ? { expression } : {}) }],
                                           image_model_id: modelId } };
}

/** Which image model draws a portrait, and what it costs on it. */
export function PortraitModel({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const models = useModelsFor("t2i").filter((m) => m.output_type === "image");
  // the estimate needs no saved character: any id prices the same
  const quote = useRunQuote(portraitRun({ id: "00000000-0000-0000-0000-000000000000", slug: "preview" }, value));
  return (
    <div className="space-y-1">
      <ModelSelect id="portrait-model" label="Portrait drawn with" models={models} recommended={PORTRAIT_MODEL}
                   value={value} onChange={onChange}
                   tradeoff="Nano Banana 2 follows a description closely at a low price; others differ in look and cost." />
      <p className="text-2xs text-faint">{quoteText(quote, ["portrait", "portraits"])}</p>
    </div>
  );
}

export function useDrawPortrait() {
  const start = useStartRun();
  async function draw(e: Pick<Element, "id" | "slug" | "handle" | "attributes">, modelId: string, confirmCost?: number): Promise<void> {
    try {
      await start.mutateAsync({ ...portraitRun(e, modelId), confirm_cost_usd: confirmCost ?? null });
      toast.success(`Drawing ${e.handle}. The picture shows up on its card in about a minute.`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const cost = (err.body as { estimate_usd: number }).estimate_usd;
        toast(`Drawing ${e.handle} costs about ${usd(cost)}.`, { action: { label: "Draw it", onClick: () => void draw(e, modelId, cost) } });
        return;
      }
      toast.error(err instanceof Error ? err.message : `Couldn't start drawing ${e.handle}`);
    }
  }
  return { draw, drawing: start.isPending };
}
