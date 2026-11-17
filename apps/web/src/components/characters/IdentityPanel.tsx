"use client";

import { Images, Sparkles } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import type { ModelSummary } from "@/lib/api";
import { type Element, usePatchElement } from "@/lib/elements";
import { GRID_VIEWS } from "@/lib/mentions";
import { useRegistry } from "@/lib/queries";
import type { RunCreate } from "@/lib/runs";
import { useModelsFor } from "@/components/create/QuickGenerate";
import { ModelSelect, quotePrice, quoteText, useRunQuote, useStartQuoted } from "@/components/runs/RunQuote";
import { Button, Segmented } from "@/components/ui/controls";

// ---------------------------------------------------------------- trained identities (Soul ID), one per LoRA base

const TRAINER = "wan-2.2-lora-trainer"; // lora_train's own default
const MIN_PHOTOS = 10;
const MAX_PHOTOS = 80;
const DEPTHS = [600, 1000, 1500] as const;
const DEPTH_LABEL: Record<(typeof DEPTHS)[number], string> = { 600: "Quick", 1000: "Standard", 1500: "Thorough" };
const OR = new Intl.ListFormat("en", { type: "disjunction" });

type Identity = { trigger?: string; steps?: number; trained_at?: string };

/** The models that load a base's identity for a mentioned character, by name ("Wan 2.2 stills or Wan 2.2 with
 * characters"); "" when no model here does (one imported from another studio's). */
function loaders(models: ModelSummary[], base: string): string {
  return OR.format(models.filter((m) => m.lora_base === base && m.output_type !== "lora" && m.status !== "shutdown")
    .map((m) => m.name));
}

export function IdentityPanel({ element, onMore }: { element: Element; onMore: (count: number) => void }) {
  const trained = Object.entries(element.lora ?? {}) as [string, Identity][];
  const models = useRegistry().data?.models ?? [];
  const trainers = useModelsFor("lora_train");
  const patch = usePatchElement();
  const [again, setAgain] = useState(false);
  // where focus goes when the button that had it goes: into the form it opened, or back to Train an identity
  const focus = useRef<"form" | "train" | null>(null);
  const focusOn = (to: "form" | "train") => (el: HTMLElement | null) => {
    if (!el || focus.current !== to) return;
    focus.current = null;
    (to === "form" ? el.querySelector<HTMLElement>("select, button") : el)?.focus();
  };
  const [trainerId, setTrainerId] = useState(TRAINER);
  const [steps, setSteps] = useState<(typeof DEPTHS)[number]>(1000);
  const trainer = trainers.find((t) => t.id === trainerId) ?? trainers[0];
  const base = trainer?.lora_base;
  const loads = base ? loaders(models, base) : ""; // what loads what it trains
  // not a look's (another outfit, or another picture style) or a grid sheet: they'd teach it the wrong face
  const photos = element.images.filter((i) => !i.look && !GRID_VIEWS.includes(i.view)).map((i) => i.asset.id)
    .slice(0, MAX_PHOTOS);
  const training = trained.length === 0 || again;
  const body: RunCreate | null = training && trainer && photos.length >= MIN_PHOTOS
    ? { recipe: "lora_train",
        input: { element_id: element.id, slug: element.slug, photos, steps, trainer_model_id: trainer.id } }
    : null;
  const quote = useRunQuote(body);
  const start = useStartQuoted(`Training ${element.handle}. Their identity is ready when the run finishes.`);

  function forget(forgotten: string, names: string) {
    patch.mutate({ id: element.id, clear_voice: false, clear_lora: forgotten }, {
      onSuccess: () => {
        focus.current = trained.length > 1 ? "train" : "form"; // the last one gone, the training form shows
        toast(trained.length > 1 ? `Forgot ${element.name}'s identity for ${names}`
          : `${element.name} is back to pictures and words`);
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : "Couldn't forget it"),
    });
  }

  return (
    <section aria-labelledby="identity-title" className="space-y-3 rounded-[var(--radius-control)] border border-line-soft p-4">
      <h3 id="identity-title" className="flex items-center gap-2 text-sm text-chalk">
        <Sparkles className="size-4 text-tungsten" aria-hidden />
        Trained identity
      </h3>
      {trained.length > 0 && (
        <ul className="space-y-2">
          {trained.map(([b, identity]) => {
            const names = loaders(models, b);
            return (
              <li key={b} className="flex items-start gap-3">
                <p className="flex-1 text-xs leading-relaxed text-muted">
                  Trained{identity.trained_at ? ` on ${new Date(identity.trained_at).toLocaleDateString()}` : ""}
                  {identity.steps ? ` with ${identity.steps.toLocaleString()} steps` : ""}.{" "}
                  {names ? (
                    <>
                      Write {element.handle} on <span className="text-chalk">{names}</span> and this face comes along;
                      the trigger word <span className="text-chalk">{identity.trigger}</span> goes into the prompt for
                      you.
                    </>
                  ) : `No model here loads it (it was trained for ${b}).`}
                </p>
                <Button type="button" size="sm" tone="ghost" disabled={patch.isPending}
                        aria-label={`Forget the identity for ${names || b}`} onClick={() => forget(b, names || b)}>
                  Forget
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      {!training ? (
        <Button ref={focusOn("train")} type="button" size="sm" onClick={() => { focus.current = "form"; setAgain(true); }}>
          Train an identity
        </Button>
      ) : (
        <div ref={focusOn("form")} className="space-y-3">
          <p className="text-xs leading-relaxed text-muted">
            Teach the studio this face from {MIN_PHOTOS} to {MAX_PHOTOS} photos: different angles, expressions and light,
            one person per photo.
            {loads && ` Afterwards, ${loads} keeps it wherever a prompt mentions ${element.handle}`
              + `${base && element.lora?.[base] ? ", replacing the one trained before" : ""}.`}
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
              <div className="max-w-sm">
                <ModelSelect id="identity-trainer" label="Trainer" models={trainers} recommended={TRAINER}
                             value={trainer?.id ?? trainerId} onChange={setTrainerId} />
              </div>
              <div className="flex flex-col gap-1.5 text-xs text-muted">
                How closely to learn it
                <Segmented label="Training depth" value={steps} options={DEPTHS} render={(d) => DEPTH_LABEL[d]}
                           onChange={setSteps} />
              </div>
              <p className="text-2xs text-faint">
                {photos.length} photos, {steps.toLocaleString()} steps: {quoteText(quote, ["generation", "generations"])}.
              </p>
            </>
          )}
          {(again || photos.length >= MIN_PHOTOS) && (
            <div className="flex gap-2">
              {again && (
                <Button type="button" size="sm" tone="ghost" onClick={() => { focus.current = "train"; setAgain(false); }}>
                  Cancel
                </Button>
              )}
              {photos.length >= MIN_PHOTOS && (
                <Button type="button" size="sm" tone="primary" disabled={!body || quote.count == null || start.pending}
                        onClick={() => body && void start.go(body, quote)}>
                  Train identity{quotePrice(quote) && ` · ${quotePrice(quote)}`}
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
