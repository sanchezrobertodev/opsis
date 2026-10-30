"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, type ModelDetail } from "@/lib/api";
import { usd } from "@/lib/cn";
import { useElements } from "@/lib/elements";
import { useStartRun } from "@/lib/runs";
import { ModelSelect, quotePrice, quoteText, useRunQuote } from "@/components/runs/RunQuote";
import { Button } from "@/components/ui/controls";
import { useModelsFor } from "./QuickGenerate";

const MENTION = /(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_-]{0,39})/g;
const FRAME_MODEL = "nano-banana-2"; // the keyframe recipe's own default

/**
 * For models that can't take reference images (Kling): when the prompt mentions a saved character with
 * pictures, offer to render the opening frame from those pictures first and animate that instead.
 */
export function KeyframeHint({
  model,
  prompt,
  params,
  direction,
  hasStartFrame,
}: {
  model: ModelDetail;
  prompt: string;
  params: Record<string, unknown>;
  direction: Record<string, unknown> | null;
  hasStartFrame: boolean;
}) {
  const router = useRouter();
  const { data: elements } = useElements();
  const start = useStartRun();
  const imageModels = useModelsFor("multi_ref").filter((m) => m.output_type === "image");
  const [imageModel, setImageModel] = useState(FRAME_MODEL);
  const [confirm, setConfirm] = useState<number | null>(null);
  const strategy = (model.elements as { strategy?: string } | null)?.strategy;
  const slugs = new Set([...prompt.matchAll(MENTION)].map((m) => m[1].toLowerCase()));
  const characters = (elements ?? []).filter((e) => slugs.has(e.slug) && e.images.length > 0);
  const shown = strategy === "keyframe_first" && !hasStartFrame && characters.length > 0;
  const names = characters.map((c) => c.handle);
  // priced on the mentions alone, so typing the rest of the prompt doesn't ask again (its words don't change the price)
  const quote = useRunQuote(shown ? { recipe: "keyframe", input: {
    model_id: model.id, prompt: names.join(" "), params, direction, image_model_id: imageModel } } : null);
  if (!shown) return null;

  async function go(confirmCost?: number) {
    try {
      const run = await start.mutateAsync({
        recipe: "keyframe",
        confirm_cost_usd: confirmCost ?? quote.charged,
        input: { model_id: model.id, prompt, params, direction, image_model_id: imageModel },
      });
      router.push(`/runs/${run.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConfirm((err.body as { estimate_usd: number }).estimate_usd);
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start it");
    }
  }

  const imageName = imageModels.find((m) => m.id === imageModel)?.name ?? imageModel;
  return (
    <div className="space-y-2 rounded-[var(--radius-control)] border border-tungsten/30 bg-tungsten/5 p-3 text-xs leading-relaxed">
      <p className="text-chalk">
        {model.name} can&rsquo;t take reference images, so {names.join(" and ")} would only be described in words. For a
        consistent look, render the opening frame from their pictures first, then animate it.
      </p>
      <ModelSelect id="keyframe-image-model" label="Start frame drawn with" models={imageModels} recommended={FRAME_MODEL}
                   value={imageModel} onChange={(id) => { setImageModel(id); setConfirm(null); }}
                   tradeoff="Nano Banana 2 keeps their look at a low price; others differ in cost and can drift from their pictures." />
      <p className="text-2xs text-faint">
        One start frame on {imageName}, then one clip on {model.name}: {quoteText(quote, ["generation", "generations"])}
      </p>
      <Button
        type="button"
        size="sm"
        onClick={() => void go(confirm ?? undefined)}
        disabled={start.isPending || !prompt.trim() || quote.count == null || Boolean(quote.error)}
      >
        {confirm != null ? `Start for ${usd(confirm)}`
          : `Make the start frame, then animate${quotePrice(quote) && ` · ${quotePrice(quote)}`}`}
      </Button>
    </div>
  );
}
