"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/cn";
import { useDraft } from "@/lib/draft";
import type { Element } from "@/lib/elements";
import { lookSlug, styleLook } from "@/lib/mentions";
import type { RunCreate } from "@/lib/runs";
import { useModelsFor } from "@/components/create/QuickGenerate";
import { StylePicker, useStyleTiles } from "@/components/create/director/StylePicker";
import { ModelSelect, quotePrice, quoteText, useRunQuote, useStartQuoted } from "@/components/runs/RunQuote";
import { Button, Segmented } from "@/components/ui/controls";

// character_sheet's, character_photos' and character_look's own default
const REFS_MODEL = "nano-banana-pro";
const REFS_TRADEOFF = "Nano Banana Pro keeps the face closest to the references; other models cost less or more but "
  + "can drift from the likeness.";
const FIELD = "rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60";
/** Every run here reads the saved character and leaves the editor, which would drop its edits. */
export const SAVE_FIRST = "Save your changes first";

/** Models that draw from a character's pictures (their reference mode). */
function useRefModels() {
  return useModelsFor("multi_ref").filter((m) => m.output_type === "image");
}

/** Opens Create image with a prompt drafted in a reference mode (Create's model when it has one, else REFS_MODEL), so
 * the pictures of what it mentions come along. `model` and `mode` are the ones it uses, to price a take first. */
export function useReferenceShot() {
  const router = useRouter();
  const current = useDraft((s) => s.image.modelId);
  const setDraft = useDraft((s) => s.set);
  const models = useRefModels();
  const model = models.find((m) => m.id === current) ?? models.find((m) => m.id === REFS_MODEL) ?? models[0];
  const mode = model?.modes.find((m) => m.ops.includes("multi_ref"));
  function open(prompt: string, note: string) {
    if (!model || !mode) {
      toast.error("No image model here can draw from reference pictures.");
      return;
    }
    // the mentions bring the pictures, so earlier references would only take their room
    setDraft("image", { modelId: model.id, mode: mode.id, media: {}, prompt });
    router.push("/create/image");
    toast(note);
  }
  return { model, mode, open };
}

export function GeneratePanel({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLElement>(null);
  // it opens under both columns, which the button that opened it may be far above
  useEffect(() => { ref.current?.scrollIntoView({ block: "nearest" }); }, []);
  return (
    <section ref={ref} aria-label={title}
             className="space-y-3 rounded-[var(--radius-control)] border border-tungsten/30 bg-bay/60 p-4 md:col-span-2">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm text-chalk">{title}</h3>
        <button type="button" onClick={onClose} className="text-2xs text-muted hover:text-chalk">Close</button>
      </div>
      {children}
    </section>
  );
}

const SHEETS = ["turnaround", "expressions", "poses"] as const;
type Sheet = (typeof SHEETS)[number];
const SHEET_LABEL: Record<Sheet, string> = { turnaround: "From every side", expressions: "Expressions", poses: "Poses" };
const GRID_NOTE = "It joins their pictures for you to look at; prompts never bring it along, since a model given a "
  + "grid tends to draw a grid.";

/** One picture of the character from every side, kept right after its main picture so a mention attaches both; or a
 * grid of its expressions or poses, kept with its pictures. */
export function SheetPanel({ element, unsaved, onClose }: { element: Element; unsaved: boolean; onClose: () => void }) {
  const models = useRefModels();
  const [model, setModel] = useState(REFS_MODEL);
  const [sheet, setSheet] = useState<Sheet>("turnaround");
  const body: RunCreate = { recipe: "character_sheet",
                            input: { element_id: element.id, slug: element.slug, sheet, image_model_id: model } };
  const quote = useRunQuote(body);
  const start = useStartQuoted(sheet === "turnaround"
    ? `Drawing ${element.handle} from every side. The sheet becomes their second picture.`
    : `Drawing ${element.handle}'s ${sheet}. The sheet joins their pictures.`);
  return (
    <GeneratePanel title="Reference sheet" onClose={onClose}>
      <Segmented label="Sheet" value={sheet} options={SHEETS} render={(s) => SHEET_LABEL[s]} onChange={setSheet} />
      <p className="text-xs leading-relaxed text-muted">
        {sheet === "turnaround" ? <>
          One wide picture of {element.handle} five times, drawn from its pictures: side, front, three-quarter from
          behind, limbs raised, and a close-up of the head. It&rsquo;s kept as their second picture, so every prompt
          that mentions them shows the model their whole shape.
        </> : sheet === "expressions" ? <>
          {element.name}&rsquo;s face eight times on one picture, each with another feeling from the emotion wheel.{" "}
          {GRID_NOTE}
        </> : <>
          {element.name} eight times on one picture, head to feet: standing, walking, running, sitting, jumping,
          pointing, arms crossed and waving. {GRID_NOTE}
        </>}
      </p>
      <div className="max-w-sm">
        <ModelSelect id="sheet-model" models={models} recommended={REFS_MODEL} value={model} onChange={setModel}
                     tradeoff={REFS_TRADEOFF} />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" tone="primary" size="sm" title={unsaved ? SAVE_FIRST : undefined}
                disabled={unsaved || quote.count == null || Boolean(quote.error) || start.pending}
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
export function MorePictures({ element, count: wanted, unsaved, onClose }: {
  element: Element; count?: number; unsaved: boolean; onClose: () => void;
}) {
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
        <Button type="button" tone="primary" size="sm" title={unsaved ? SAVE_FIRST : undefined}
                disabled={unsaved || quote.count == null || Boolean(quote.error) || start.pending}
                onClick={() => void start.go(body, quote)}>
          Make {count} pictures{quotePrice(quote) && ` · ${quotePrice(quote)}`}
        </Button>
        <span className="text-2xs text-faint">{quoteText(quote, ["picture", "pictures"])}</span>
      </div>
    </GeneratePanel>
  );
}

const LOOK_COUNTS = [1, 2, 3, 4] as const;
type LookInput = { look: string; name: string; outfit?: string; description?: string; style?: string };

/** The handle a look will have, and that a look already by that handle gets the new pictures. */
function LookHandle({ element, look }: { element: Element; look: string }) {
  if (!look) return null;
  const had = element.looks[look];
  return (
    <p className="text-2xs text-faint">
      Write <span className="text-tungsten">{element.handle}:{look}</span> in a prompt to bring this look.
      {had && ` ${element.name} has a look called ${had.name} by that handle: the new pictures join it.`}
    </p>
  );
}

/** How many pictures of a look and on which model, priced, then the run that draws them and keeps them as the look's. */
function DrawLook({ element, input, ready, unsaved }: { element: Element; input: LookInput; ready: boolean; unsaved: boolean }) {
  const models = useRefModels();
  const [model, setModel] = useState(REFS_MODEL);
  const [count, setCount] = useState<(typeof LOOK_COUNTS)[number]>(1);
  const body: RunCreate = { recipe: "character_look",
                            input: { ...input, character: element.slug, model_id: model, count } };
  // priced on stand-in words, so typing them doesn't ask again; a saved style's pictures can change the price
  const quote = useRunQuote({ recipe: "character_look", input: {
    character: element.slug, look: "look", name: "Look", outfit: "an outfit", style: input.style ?? "", model_id: model,
    count } });
  const start = useStartQuoted(`Drawing ${element.handle}:${input.look}. Each picture joins the look when it's done.`);
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5 text-xs text-muted">
          How many pictures
          <Segmented label="How many pictures" value={count} options={LOOK_COUNTS} render={String} onChange={setCount} />
        </div>
        <ModelSelect id="look-model" models={models} recommended={REFS_MODEL} value={model} onChange={setModel}
                     tradeoff={REFS_TRADEOFF} />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" tone="primary" size="sm" title={unsaved ? SAVE_FIRST : undefined}
                disabled={!ready || unsaved || quote.count == null || Boolean(quote.error) || start.pending}
                onClick={() => void start.go(body, quote)}>
          Draw the look{quotePrice(quote) && ` · ${quotePrice(quote)}`}
        </Button>
        <span className="text-2xs text-faint">{quoteText(quote, ["picture", "pictures"])}</span>
      </div>
    </>
  );
}

/** The character in another outfit, drawn from its pictures and kept as a look (`@handle:look`). */
export function AddLookPanel({ element, unsaved, onClose }: { element: Element; unsaved: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [outfit, setOutfit] = useState("");
  const look = lookSlug(name);
  return (
    <GeneratePanel title="Add a look" onClose={onClose}>
      <p className="text-xs leading-relaxed text-muted">
        {element.name} in another outfit, drawn full body from their pictures. A prompt that mentions the look gets its
        pictures first and says what they wear; a plain {element.handle} stays as they are.
      </p>
      <div className="grid items-start gap-4 sm:grid-cols-[1fr_2fr]">
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} placeholder="e.g. Winter coat"
                 autoFocus className={`h-10 ${FIELD}`} />
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          What they wear
          <textarea value={outfit} onChange={(e) => setOutfit(e.target.value)} maxLength={300} rows={2}
                    placeholder="a long red wool coat, a knitted scarf and snow boots"
                    className={`resize-y py-2 leading-relaxed ${FIELD}`} />
        </label>
      </div>
      <LookHandle element={element} look={look} />
      {name.trim() && !look && (
        <p className="text-2xs text-fault">
          Put a letter from a to z or a digit in the name: it becomes the look&rsquo;s handle, like {element.handle}:winter.
        </p>
      )}
      <DrawLook element={element} ready={Boolean(look && outfit.trim())} unsaved={unsaved}
                input={{ look, name: name.trim(), outfit: outfit.trim() }} />
    </GeneratePanel>
  );
}

/** The character drawn in another picture style (a catalog style or one of yours), kept as a look named after it. */
export function StyleLookPanel({ element, unsaved, onClose }: { element: Element; unsaved: boolean; onClose: () => void }) {
  const { catalog, mine } = useStyleTiles();
  const [style, setStyle] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null); // null: the style's own words
  const name = [...mine, ...catalog].find((s) => s.id === style)?.label.trim().slice(0, 40) ?? "";
  const look = style ? lookSlug(style) : "";
  const description = said ?? (name ? styleLook(name) : "");
  return (
    <GeneratePanel title="Another style" onClose={onClose}>
      <p className="text-xs leading-relaxed text-muted">
        {element.name} drawn from their pictures in another picture style, kept as a look named after it. A prompt
        that mentions the look gets those pictures and says the style; a plain {element.handle} stays as they are.
      </p>
      <div className="grid items-start gap-4 sm:grid-cols-2">
        <StylePicker value={style} onChange={(s) => { setStyle(s); setSaid(null); }} />
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          How prompts say it
          <input value={description} onChange={(e) => setSaid(e.target.value)} maxLength={160} disabled={!style}
                 placeholder="Pick a style first" className={`h-10 disabled:opacity-45 ${FIELD}`} />
        </label>
      </div>
      <LookHandle element={element} look={look} />
      <DrawLook element={element} ready={Boolean(look && name)} unsaved={unsaved}
                input={{ look, name, description: description.trim(), style: style ?? "" }} />
    </GeneratePanel>
  );
}
