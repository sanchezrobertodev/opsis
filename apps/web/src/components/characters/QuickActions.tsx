"use client";

import { WandSparkles } from "lucide-react";
import { useState } from "react";
import { mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { CREATURE } from "@/lib/designer";
import { useDraft } from "@/lib/draft";
import { type Element, useElements } from "@/lib/elements";
import { hasPictures } from "@/lib/mentions";
import type { RunCreate } from "@/lib/runs";
import { useModelsFor } from "@/components/create/QuickGenerate";
import { ModelSelect, quotePrice, quoteText, useRunQuote, useStartQuoted, useTakeQuote } from "@/components/runs/RunQuote";
import { Button } from "@/components/ui/controls";
import { GeneratePanel, SAVE_FIRST, useReferenceShot } from "./MakePanels";

export type QuickAction = "place" | "figure" | "hello";

const ACTIONS: [QuickAction, string, string][] = [
  ["place", "Place in a location", "Opens Create image with them in one of your locations"],
  ["figure", "3D figure", "A 3D model of them for 3D blocking, with a skeleton to move"],
  ["hello", "Say hello", "A short clip of them saying a line in their voice"],
];

/** Quick things to make with a saved character, each priced in its panel before it starts. `open` is the editor's
 * panel showing, if it's one of these; `unsaved`: the editor holds changes, so none can start. */
export function MakeSection({ element, open, unsaved, onOpen }: {
  element: Element; open?: string; unsaved: boolean; onOpen: (action: QuickAction) => void;
}) {
  const drawable = hasPictures(element); // each one starts from their pictures
  return (
    <section aria-labelledby="make-title" className="space-y-3 rounded-[var(--radius-control)] border border-line-soft p-4">
      <h3 id="make-title" className="flex items-center gap-2 text-sm text-chalk">
        <WandSparkles className="size-4 text-tungsten" aria-hidden />
        Make
      </h3>
      <div className="flex flex-wrap gap-2">
        {ACTIONS.map(([action, label, what]) => (
          <Button key={action} type="button" size="sm" onClick={() => onOpen(action)} disabled={!drawable || unsaved}
                  aria-expanded={open === action} title={!drawable ? "Add a picture first" : unsaved ? SAVE_FIRST : what}>
            {label}
          </Button>
        ))}
      </div>
    </section>
  );
}

/** The character in one of your locations: Create image opens with the prompt in a reference mode, so the mentions
 * bring both their pictures; priced here as Create image will make it. */
export function PlacePanel({ element, unsaved, onClose }: { element: Element; unsaved: boolean; onClose: () => void }) {
  const { data: places, error, refetch } = useElements("location");
  const [placeId, setPlaceId] = useState<string | null>(null);
  const place = places?.find((p) => p.id === placeId);
  const shot = useReferenceShot();
  const prompt = place ? `${element.handle} at ${place.handle}, same face and outfit, in the place's light.` : "";
  // Create image keeps what it was last set to for the model (resolution, pictures per take) and its number of takes
  const create = useDraft((s) => s.image);
  const one = useTakeQuote(place && shot.model && shot.mode ? { model_id: shot.model.id, mode: shot.mode.id, prompt,
    params: create.params[shot.model.id] ?? {}, media: [] } : null);
  const takes = Math.max(1, Math.min(4, create.takes)); // as Create counts them
  const quote = { ...one, usd: one.usd == null ? null : one.usd * takes, count: one.count && takes };
  return (
    <GeneratePanel title="Place in a location" onClose={onClose}>
      <p className="text-xs leading-relaxed text-muted">
        {element.name} in one of your locations, drawn from their pictures and the place&rsquo;s. Create image opens
        with the prompt written{shot.model ? ` for ${shot.model.name}` : ""}; change it as you like, then generate.
      </p>
      {places?.length ? (
        <fieldset className="flex flex-wrap gap-2">
          <legend className="mb-1.5 text-xs text-muted">Where</legend>
          {places.map((p) => (
            <button key={p.id} type="button" aria-pressed={p.id === placeId} onClick={() => setPlaceId(p.id)}
                    className={cn("flex w-28 flex-col gap-1 rounded-[var(--radius-control)] border p-1 text-left",
                                  p.id === placeId ? "border-tungsten bg-raised" : "border-line-soft hover:border-tungsten/40")}>
              <span className="block aspect-video w-full overflow-hidden rounded-[3px] bg-bay">
                {p.cover && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={mediaUrl(p.cover.thumb_url ?? p.cover.url)} alt="" className="size-full object-cover" />
                )}
              </span>
              <span className={cn("truncate text-xs", p.id === placeId ? "text-chalk" : "text-muted")}>{p.name}</span>
            </button>
          ))}
        </fieldset>
      ) : places || !error ? (
        <p className="text-xs text-faint">
          {places ? "No locations yet: add the starter places, or make one from your pictures with New from images."
            : "Loading your locations…"}
        </p>
      ) : (
        <p role="alert" className="text-xs text-fault">
          {error.message}{" "}
          <button type="button" onClick={() => void refetch()} className="text-chalk underline">Try again</button>
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" tone="primary" size="sm" disabled={!place || unsaved} title={unsaved ? SAVE_FIRST : undefined}
                onClick={() => shot.open(prompt, `${element.handle} and ${place?.handle} bring their pictures. Generate when it reads right.`)}>
          Open in Create image
        </Button>
        {place && <span className="text-2xs text-faint">{quoteText(quote, ["take", "takes"])}</span>}
      </div>
    </GeneratePanel>
  );
}

// character_figure's own defaults
const FIGURE_MODEL = "tripo-h3.1";

/** A 3D model of the character: drawn head to feet in an A-pose (or built from their full-body picture), made 3D, and
 * rigged unless you say not to (off by default for a creature). The run page links it to 3D blocking. */
export function FigurePanel({ element, unsaved, onClose }: { element: Element; unsaved: boolean; onClose: () => void }) {
  const models = useModelsFor("to_3d");
  const [model, setModel] = useState(FIGURE_MODEL);
  const creature = (element.attributes.traits as { presentation?: string } | undefined)?.presentation === CREATURE;
  const [rig, setRig] = useState(!creature);
  // one of their own main pictures already head to feet: no picture to draw first
  const full = element.images.find((i) => !i.look && i.view === "full_body");
  const [fromFull, setFromFull] = useState(false);
  const body: RunCreate = { recipe: "character_figure", input: {
    character: element.slug, model_id: model, rig, ...(fromFull && full ? { picture_asset_id: full.asset.id } : {}) } };
  const quote = useRunQuote(body);
  const start = useStartQuoted(`Making ${element.handle} in 3D. The figure is in your library when the run is done.`);
  return (
    <GeneratePanel title="3D figure" onClose={onClose}>
      <p className="text-xs leading-relaxed text-muted">
        {fromFull ? `${element.name}'s full-body picture` : `${element.name} drawn head to feet in an A-pose from their pictures`},
        built as a 3D model{rig ? " and rigged with a skeleton, with walking and running clips" : ""}. It lands in your
        library, ready for 3D blocking. Rigging needs a body shaped like a person&rsquo;s: when it can&rsquo;t find
        one, you keep the figure without a skeleton.
      </p>
      <div className="grid items-start gap-4 sm:grid-cols-2">
        <ModelSelect id="figure-model" label="3D model made with" models={models} recommended={FIGURE_MODEL} value={model}
                     onChange={setModel} tradeoff="Others differ in detail, texture and price." />
        <div className="flex flex-col gap-2 pt-1 text-xs text-muted">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={rig} onChange={(e) => setRig(e.target.checked)} />
            Rig it to move
          </label>
          {full && (
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={fromFull} onChange={(e) => setFromFull(e.target.checked)} />
              Build it from their full-body picture
            </label>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" tone="primary" size="sm" title={unsaved ? SAVE_FIRST : undefined}
                disabled={unsaved || quote.count == null || Boolean(quote.error) || start.pending}
                onClick={() => void start.go(body, quote)}>
          Make the figure{quotePrice(quote) && ` · ${quotePrice(quote)}`}
        </Button>
        <span className="text-2xs text-faint">{quoteText(quote, ["take", "takes"])}</span>
      </div>
    </GeneratePanel>
  );
}

// the Talking photo template's model
const HELLO_MODEL = "kling-avatar-2";

/** A short clip of the character saying a line from their main picture, in their saved voice: a catchphrase of
 * theirs to start with, on one talking-portrait model. */
export function HelloPanel({ element, unsaved, onClose }: { element: Element; unsaved: boolean; onClose: () => void }) {
  const models = useModelsFor("lipsync").filter((m) => m.modes.some((x) => x.ops.includes("lipsync") && x.ops.includes("avatar")));
  const [model, setModel] = useState(HELLO_MODEL);
  const [script, setScript] = useState(element.profile.catchphrases[0] ?? `Hi, I'm ${element.name}.`);
  const voiced = Boolean((element.voice as { params?: { voice_id?: string } } | null)?.params?.voice_id);
  const body: RunCreate | null = script.trim()
    ? { recipe: "talking_shot", input: { character: element.slug, script: script.trim(), models: [{ model_id: model }] } }
    : null;
  const quote = useRunQuote(body);
  const start = useStartQuoted(`${element.handle} is saying hello. The clip is in your library when the run is done.`);
  return (
    <GeneratePanel title="Say hello" onClose={onClose}>
      <p className="text-xs leading-relaxed text-muted">
        {element.name}&rsquo;s main picture says a line {voiced ? "in their voice"
          : "in the default voice: pick theirs under Voice and save to hear them instead"}.
      </p>
      <div className="grid items-start gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          What they say
          <textarea value={script} onChange={(e) => setScript(e.target.value)} maxLength={500} rows={2}
                    className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm leading-relaxed text-chalk outline-none focus:border-tungsten/60" />
        </label>
        <ModelSelect id="hello-model" label="Animated with" models={models} recommended={HELLO_MODEL} value={model}
                     onChange={setModel} tradeoff="Others move the head and face differently and cost more or less." />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" tone="primary" size="sm" title={unsaved ? SAVE_FIRST : undefined}
                disabled={unsaved || !body || quote.count == null || Boolean(quote.error) || start.pending}
                onClick={() => body && void start.go(body, quote)}>
          Make the clip{quotePrice(quote) && ` · ${quotePrice(quote)}`}
        </Button>
        <span className="text-2xs text-faint">{body ? quoteText(quote, ["take", "takes"]) : "Write what they say."}</span>
      </div>
    </GeneratePanel>
  );
}
