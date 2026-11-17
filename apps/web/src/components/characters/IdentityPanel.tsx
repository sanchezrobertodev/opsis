"use client";

import { Images, Sparkles } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type Element, usePatchElement } from "@/lib/elements";
import type { RunCreate } from "@/lib/runs";
import { quotePrice, quoteText, useRunQuote, useStartQuoted } from "@/components/runs/RunQuote";
import { Button, Segmented } from "@/components/ui/controls";

// ---------------------------------------------------------------- trained identity (Soul ID)

const MIN_PHOTOS = 10;
const MAX_PHOTOS = 80;
const DEPTHS = [600, 1000, 1500] as const;
const DEPTH_LABEL: Record<(typeof DEPTHS)[number], string> = { 600: "Quick", 1000: "Standard", 1500: "Thorough" };

type Identity = { trigger?: string; steps?: number; trained_at?: string; run_id?: string };

export function IdentityPanel({ element, onMore }: { element: Element; onMore: (count: number) => void }) {
  const identity = (element.lora ?? null) as Identity | null;
  const patch = usePatchElement();
  const [retrain, setRetrain] = useState(false);
  const [steps, setSteps] = useState<(typeof DEPTHS)[number]>(1000);
  const photos = element.images.map((i) => i.asset.id).slice(0, MAX_PHOTOS);
  const training = !identity || retrain;
  const body: RunCreate | null = training && photos.length >= MIN_PHOTOS
    ? { recipe: "lora_train", input: { element_id: element.id, slug: element.slug, photos, steps } }
    : null;
  const quote = useRunQuote(body);
  const start = useStartQuoted(`Training ${element.handle}. Their identity is ready when the run finishes.`);

  return (
    <section aria-labelledby="identity-title" className="space-y-3 rounded-[var(--radius-control)] border border-line-soft p-4">
      <h3 id="identity-title" className="flex items-center gap-2 text-sm text-chalk">
        <Sparkles className="size-4 text-tungsten" aria-hidden />
        Trained identity
      </h3>
      {identity && !retrain ? (
        <>
          <p className="text-xs leading-relaxed text-muted">
            Trained{identity.trained_at ? ` on ${new Date(identity.trained_at).toLocaleDateString()}` : ""}
            {identity.steps ? ` with ${identity.steps.toLocaleString()} steps` : ""}. Write {element.handle} on Wan 2.2
            stills or Wan 2.2 with characters and this face comes along; the trigger word{" "}
            <span className="text-chalk">{identity.trigger}</span> goes into the prompt for you.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" onClick={() => setRetrain(true)}>Train again</Button>
            <Button type="button" size="sm" tone="ghost" disabled={patch.isPending}
                    onClick={() => patch.mutate({ id: element.id, clear_voice: false, clear_lora: true },
                                                { onSuccess: () => toast(`${element.name} is back to pictures and words`) })}>
              Forget identity
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="text-xs leading-relaxed text-muted">
            Teach the studio this face from {MIN_PHOTOS} to {MAX_PHOTOS} photos: different angles, expressions and light,
            one person per photo. Afterwards, Wan 2.2 keeps it in every picture and clip that mentions {element.handle}.
          </p>
          {photos.length < MIN_PHOTOS ? (
            <div className="space-y-2">
              <p className="text-xs text-faint">
                {photos.length} of {MIN_PHOTOS} photos so far. Add {MIN_PHOTOS - photos.length} more above and save, or
                draw them from the ones it has.
              </p>
              {photos.length > 0 && (
                <Button type="button" size="sm" onClick={() => onMore(Math.max(4, MIN_PHOTOS - photos.length))}>
                  <Images className="size-4" aria-hidden />
                  Make {Math.max(4, MIN_PHOTOS - photos.length)} more pictures
                </Button>
              )}
            </div>
          ) : (
            <>
              <div className="flex flex-col gap-1.5 text-xs text-muted">
                How closely to learn it
                <Segmented label="Training depth" value={steps} options={DEPTHS} render={(d) => DEPTH_LABEL[d]}
                           onChange={setSteps} />
              </div>
              <p className="text-2xs text-faint">
                {photos.length} photos, {steps.toLocaleString()} steps on fal&rsquo;s Wan 2.2 trainer:{" "}
                {quoteText(quote, ["training run", "training runs"])}.
              </p>
              <div className="flex gap-2">
                {retrain && <Button type="button" size="sm" tone="ghost" onClick={() => setRetrain(false)}>Keep the current one</Button>}
                <Button type="button" size="sm" tone="primary" disabled={!body || quote.count == null || start.pending}
                        onClick={() => body && void start.go(body, quote)}>
                  Train identity{quotePrice(quote) && ` · ${quotePrice(quote)}`}
                </Button>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
