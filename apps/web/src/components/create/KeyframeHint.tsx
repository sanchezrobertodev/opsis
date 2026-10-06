"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, type ModelDetail } from "@/lib/api";
import { usd } from "@/lib/cn";
import { useElements } from "@/lib/elements";
import { useStartRun } from "@/lib/runs";
import { Button } from "@/components/ui/controls";

const MENTION = /(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_-]{0,39})/g;

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
  const [confirm, setConfirm] = useState<number | null>(null);
  const strategy = (model.elements as { strategy?: string } | null)?.strategy;
  const slugs = new Set([...prompt.matchAll(MENTION)].map((m) => m[1].toLowerCase()));
  const characters = (elements ?? []).filter((e) => slugs.has(e.slug) && e.images.length > 0);
  if (strategy !== "keyframe_first" || hasStartFrame || characters.length === 0) return null;

  async function go(confirmCost?: number) {
    try {
      const run = await start.mutateAsync({
        recipe: "keyframe",
        confirm_cost_usd: confirmCost ?? null,
        input: { model_id: model.id, prompt, params, direction },
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

  const names = characters.map((c) => c.handle).join(" and ");
  return (
    <div className="rounded-[var(--radius-control)] border border-tungsten/30 bg-tungsten/5 p-3 text-xs leading-relaxed">
      <p className="text-chalk">
        {model.name} can&rsquo;t take reference images, so {names} would only be described in words. For a
        consistent look, render the opening frame from their pictures first, then animate it.
      </p>
      <Button
        type="button"
        size="sm"
        className="mt-2"
        onClick={() => void go(confirm ?? undefined)}
        disabled={start.isPending || !prompt.trim()}
      >
        {confirm != null ? `Start for ${usd(confirm)}` : "Make the start frame, then animate"}
      </Button>
    </div>
  );
}
