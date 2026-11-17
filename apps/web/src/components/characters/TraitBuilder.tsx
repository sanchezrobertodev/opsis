"use client";

import { CircleQuestionMark, LoaderCircle, Lock, LockOpen, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { toast } from "sonner";
import type { MediaSlot } from "@/lib/api";
import { useCharacterCatalog, useDraftCharacter } from "@/lib/characters";
import { cn } from "@/lib/cn";
import {
  applyPicks,
  CREATURE,
  describeCharacter,
  expressionOf,
  keptLocks,
  optionsFor,
  picks,
  pickTrait,
  randomizeTraits,
  togglePick,
  type Trait,
  traitPicture,
  type Traits,
  traitsFor,
  type Vocabulary,
} from "@/lib/designer";
import { type SlotAsset, useDraft } from "@/lib/draft";
import { useCreateElement } from "@/lib/elements";
import { designerStyle } from "@/lib/styles";
import { useDirectorLibrary } from "@/components/create/DirectorPanel";
import { StylePicker } from "@/components/create/director/StylePicker";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { useClaudeCost } from "@/components/llm/ClaudeCost";
import { Button, Dialog, Slider } from "@/components/ui/controls";
import { ZoomButton } from "@/components/ui/PictureViewer";
import { PORTRAIT_MODEL, PortraitModel, useDrawPortrait } from "./portrait";

// ---------------------------------------------------------------- trait builder

const PHOTO_SLOT: MediaSlot = { role: "character_image", kinds: ["image"], label: "A photo", max: 1, min: 0, required: true, auto: false };
const FIELD = "rounded-[var(--radius-control)] border border-line-soft bg-bay px-2 text-chalk outline-none placeholder:text-faint focus:border-tungsten/60";

// `typed`: the name is one you wrote, not a draft's or a starter's
const BLANK: { traits: Traits; locked: string[]; name: string; typed: boolean } =
  { traits: {}, locked: [], name: "", typed: false };

/**
 * What the designer holds, kept while its dialog is closed: the picks (the style, `traits.style`, is a catalog id or a
 * saved style's `@slug`), the traits locked against Randomize, a draft and a starter, and the name.
 */
export function useDesign() {
  const [design, setDesign] = useState(BLANK);
  return {
    ...design,
    setTraits: (traits: Traits) => setDesign((d) => ({ ...d, traits, locked: keptLocks(d.traits, traits, d.locked) })),
    setName: (name: string) => setDesign((d) => ({ ...d, name, typed: name.trim() !== "" })),
    lock: (key: string) => setDesign((d) => ({
      ...d, locked: d.locked.includes(key) ? d.locked.filter((k) => k !== key) : [...d.locked, key],
    })),
    /** A draft's or a starter's picks over the traits locked by then, and its name unless you've written one. */
    load: (vocab: Vocabulary, picked: Traits, name: string) => setDesign((d) => ({
      ...d, traits: applyPicks(vocab, d.traits, d.locked, picked), name: d.typed ? d.name : name,
    })),
    reset: () => setDesign(BLANK),
  };
}

export type Design = ReturnType<typeof useDesign>;

/** A trait's lock, in its legend: a locked pick stays as it is through Randomize, a draft and a starter. */
function LockButton({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  const Icon = on ? Lock : LockOpen;
  return (
    <button type="button" aria-pressed={on} aria-label={`Lock ${label.split(" (")[0].toLowerCase()}`} onClick={onClick}
            title={on ? "Locked: Randomize, a draft and a starter leave it as it is" : "Lock it against Randomize, a draft and a starter"}
            className={cn("-my-1 grid size-6 shrink-0 place-items-center rounded-[4px] hover:bg-raised",
                          on ? "text-tungsten" : "text-faint hover:text-chalk")}>
      <Icon className="size-3.5" aria-hidden />
    </button>
  );
}

export function TraitBuilder({ open, onClose, design }: { open: boolean; onClose: () => void; design: Design }) {
  const router = useRouter();
  const setDraft = useDraft((s) => s.set);
  const { traits, locked, name } = design;
  const [heritage, setHeritage] = useState("");
  const [size, setSize] = useState(112); // tile width in px (the pictures are 256 px)
  const [zoom, setZoom] = useState<{ trait: Trait; option: string } | null>(null);
  const [drawWith, setDrawWith] = useState(PORTRAIT_MODEL);
  const [photo, setPhoto] = useState<SlotAsset | null>(null);
  const [keepPhoto, setKeepPhoto] = useState(true);
  const [sentence, setSentence] = useState("");
  const { data: catalog, error } = useCharacterCatalog();
  const { data: lib } = useDirectorLibrary();
  const ids = useId(); // each trait's group is named by its label alone, not its lock too
  // Randomize draws from the catalog's styles, never a saved one
  const vocab: Vocabulary | undefined = catalog && { ...catalog, styles: (lib?.styles ?? []).map((s) => s.id) };
  const description = vocab ? describeCharacter(vocab, traits, heritage.trim()) : "";
  const face = vocab ? expressionOf(vocab, traits) : ""; // for its pictures only, out of the description
  const style = designerStyle(traits.style); // a catalog id or a saved style's @slug; none lets the model choose
  const creature = traits.presentation === CREATURE;
  const first = photo && keepPhoto ? photo : null; // kept as its first picture, so no portrait is drawn
  const create = useCreateElement();
  const portrait = useDrawPortrait();
  const draft = useDraftCharacter();
  const claude = useClaudeCost(photo ? "character.from_photo" : "character.from_text");
  const pick = (key: string, value: string) => {
    if (vocab) design.setTraits(pickTrait(vocab, traits, key, value));
  };
  /** A new photo, or none: kept as the first picture unless you untick it, and Claude's answer about another gone. */
  const choosePhoto = (asset: SlotAsset | null) => {
    setPhoto(asset);
    setKeepPhoto(true);
    draft.reset();
  };

  /** Saves the character (the style and the expression stay in its traits, out of its description) with your photo as
   * its first picture, or draws its portrait here. */
  async function save() {
    try {
      const e = await create.mutateAsync({
        kind: "character", name: name.trim(), attributes: { traits }, description,
        images: first ? [{ asset_id: first.id, view: "front" }] : [],
      });
      onClose();
      design.reset();
      choosePhoto(null);
      setSentence("");
      if (first) toast.success(`Created ${e.handle} with your photo. Use it in any prompt.`);
      else await portrait.draw(e, drawWith);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't save the character");
    }
  }

  /** Claude's picks from the photo, else the sentence, over the traits not locked. */
  function fill() {
    draft.mutate(photo ? { asset_id: photo.id } : { text: sentence.trim() }, {
      onSuccess: (d) => {
        if (vocab) design.load(vocab, { ...d.traits, style: d.style }, d.name);
      },
    });
  }

  function generate() {
    setDraft("image", {
      // the expression after the description, so Save to Characters (SaveAsElement) keeps it out of the one it saves
      prompt: `Character portrait of ${description}. ${creature
        ? "The whole body in frame, three-quarter view, soft studio light, plain background, sharp focus."
        : `Waist-up, facing the camera, ${face ? `${face}, ` : ""}soft studio light, plain background, sharp focus on the face.`}`,
      takes: 2,
      mode: "t2i",
      direction: style ? { style } : {}, // replaced, so an earlier look can't leak into the portraits
    });
    onClose();
    router.push("/create/image");
    toast("Pick the take you like, then use “Save to Characters” on it.");
  }

  const tiles = (t: Trait) => (
    <fieldset key={t.key} aria-labelledby={`${ids}${t.key}`} className="flex flex-col gap-1.5">
      <legend className="mb-1.5 flex items-center gap-1 text-2xs text-faint">
        <LockButton label={t.label} on={locked.includes(t.key)} onClick={() => design.lock(t.key)} />
        <span id={`${ids}${t.key}`}>
          {t.label}
          {traits[t.key] && <span className="text-chalk">: {traits[t.key]}</span>}
        </span>
      </legend>
      <div className="flex flex-wrap gap-2">
        {["", ...optionsFor(t, traits.presentation)].map((o) => {
          const on = o ? picks(traits[t.key]).includes(o) : !traits[t.key];
          const label = o || "Any";
          return (
            <div key={o} className="group relative" style={{ width: size }}>
              <button type="button" aria-pressed={on} title={o || "Any: left out, so the model chooses"}
                      onClick={() => pick(t.key, !o ? "" : t.multi ? togglePick(traits[t.key], o) : on ? "" : o)}
                      className={cn("flex w-full flex-col gap-1 rounded-[var(--radius-control)] border p-1 text-left",
                                    on ? "border-tungsten bg-raised" : "border-line-soft hover:border-tungsten/40")}>
                {o ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={traitPicture(t.key, o, traits.presentation)} alt="" loading="lazy"
                       className="aspect-square w-full rounded-[3px] bg-bay object-cover"
                       onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />
                ) : (
                  <span className="flex aspect-square w-full items-center justify-center rounded-[3px] bg-bay text-faint">
                    <CircleQuestionMark className="h-1/2 w-1/2" strokeWidth={1.25} aria-hidden />
                  </span>
                )}
                <span className={cn("line-clamp-2 leading-tight", size < 96 ? "text-2xs" : "text-xs",
                                    on ? "text-chalk" : "text-muted")}>{label}</span>
              </button>
              {o && <ZoomButton title={o} onClick={() => setZoom({ trait: t, option: o })} />}
            </div>
          );
        })}
      </div>
    </fieldset>
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Design a character"
      wide="full"
      footer={
        <>
          <Button type="button" tone="ghost" onClick={() => vocab && design.setTraits(randomizeTraits(vocab, traits, locked))}
                  disabled={!vocab} title="A random pick for every trait you haven't locked">
            Randomize
          </Button>
          <Button type="button" onClick={generate} disabled={!description}
                  title="Two portrait takes in Create image; save the one you like to Characters">
            Try in Create image
          </Button>
          <Button type="button" tone="primary" onClick={() => void save()}
                  disabled={!description || !name.trim() || create.isPending || portrait.drawing || draft.isPending}>
            Create character
          </Button>
        </>
      }
    >
      {!vocab ? (
        <p>{error ? `The designer's traits didn't load: ${error.message}` : "Loading the designer…"}</p>
      ) : (
        <div className="flex flex-col gap-4">
          <section aria-label="Start from a photo or a sentence"
                   className="grid gap-3 rounded-[var(--radius-control)] border border-line-soft p-3 md:grid-cols-2">
            <MediaSlotField slot={PHOTO_SLOT} value={photo ? [photo] : []} onAdd={choosePhoto} onRemove={() => choosePhoto(null)} />
            <label className="flex flex-col gap-2 text-xs text-muted">
              Or a sentence
              <textarea value={sentence} onChange={(e) => { setSentence(e.target.value); draft.reset(); }} maxLength={500}
                        rows={2} disabled={Boolean(photo) || draft.isPending}
                        placeholder={photo ? "Claude looks at the photo. Remove it to describe them in words instead."
                          : "e.g. an elderly woman with silver hair and round glasses"}
                        className={cn(FIELD, "min-h-20 resize-y py-2 text-sm leading-relaxed disabled:opacity-60")} />
            </label>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 md:col-span-2">
              <Button type="button" size="sm" onClick={fill} disabled={!(photo || sentence.trim()) || draft.isPending}
                      title={`${claude.name} picks the traits that show (never who it is), a name and a style`}>
                {draft.isPending ? <LoaderCircle className="size-3.5 animate-spin" aria-hidden /> : <Sparkles className="size-3.5" aria-hidden />}
                {draft.isPending ? "Filling…" : "Fill the traits"}
                {claude.price && <span className="font-normal text-muted">· {claude.price}</span>}
              </Button>
              {photo && (
                <label className="flex items-center gap-2 text-xs text-muted">
                  <input type="checkbox" checked={keepPhoto} onChange={(e) => setKeepPhoto(e.target.checked)} />
                  Use this photo as the first picture
                </label>
              )}
              <span className="text-2xs text-faint">It fills every trait you haven&rsquo;t locked, and the name if it&rsquo;s empty.</span>
            </div>
            {draft.error && <p role="alert" className="text-xs leading-relaxed text-fault md:col-span-2">{draft.error.message}</p>}
          </section>
          <div className="flex flex-wrap items-start justify-between gap-4">
            {tiles({ key: "presentation", label: "Presentation", options: vocab.presentations })}
            <div className="flex w-64 flex-col gap-4">
              <StylePicker value={style || null} onChange={(id) => pick("style", id ?? "")} className="gap-1 text-2xs text-faint"
                           aside={<LockButton label="Style" on={locked.includes("style")} onClick={() => design.lock("style")} />} />
              <label className="flex flex-col gap-1 text-2xs text-faint">
                Picture size
                <Slider value={size} min={72} max={256} step={8} onChange={setSize} format={(v) => `${v}px`} />
              </label>
            </div>
          </div>
          {/* As many columns as fit about five pictures each, so a wide screen shows more traits at once */}
          <div className="grid gap-x-8 gap-y-4"
               style={{ gridTemplateColumns: `repeat(auto-fill, minmax(min(100%, ${5 * size + 32}px), 1fr))` }}>
            {traitsFor(vocab, traits.presentation).map(tiles)}
          </div>
          {!creature && (
            <label className="flex max-w-xs flex-col gap-1 text-2xs text-faint">
              Heritage (optional)
              <input value={heritage} onChange={(e) => setHeritage(e.target.value)} placeholder="e.g. Korean, Nigerian"
                     className={cn(FIELD, "h-9 text-xs")} />
            </label>
          )}
        </div>
      )}
      <p className={cn("mt-4 rounded-[var(--radius-control)] bg-bay p-3 text-sm leading-relaxed", description ? "text-chalk" : "text-faint")}>
        {description || "Pick a few traits, or press Randomize."}
        {description && face && <span className="text-faint"> · {face} in its pictures</span>}
      </p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-2xs text-faint">
          Name
          <input value={name} onChange={(e) => design.setName(e.target.value)} maxLength={80}
                 placeholder={creature ? "e.g. Bramble" : "e.g. Marco Reyes"} className={cn(FIELD, "h-9 text-sm")} />
        </label>
        {first ? (
          <p className="self-end text-2xs text-faint">Saved with your photo as its first picture, so no portrait is drawn.</p>
        ) : (
          <PortraitModel value={drawWith} onChange={setDrawWith} />
        )}
      </div>
      <Dialog open={zoom !== null} onClose={() => setZoom(null)} title={zoom ? `${zoom.trait.label}: ${zoom.option}` : ""} wide
              footer={zoom && (
                <Button type="button" tone="primary" onClick={() => {
                  const value = traits[zoom.trait.key];
                  if (!picks(value).includes(zoom.option)) pick(zoom.trait.key, zoom.trait.multi ? togglePick(value, zoom.option) : zoom.option);
                  setZoom(null);
                }}>
                  {picks(traits[zoom.trait.key]).includes(zoom.option) ? "Picked" : "Pick this"}
                </Button>
              )}>
        {zoom && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={traitPicture(zoom.trait.key, zoom.option, traits.presentation, true)} alt={zoom.option} width={768} height={768}
               className="mx-auto block h-auto max-h-[70vh] w-auto max-w-full rounded-[var(--radius-frame)]" />
        )}
      </Dialog>
      <p className="mt-2 text-2xs text-faint">
        Click a picture to pick it; Any (or the picture again) leaves the trait for the model to choose. The pictures
        follow the presentation you pick, and a creature gets traits of its own, where Features take several picks.
        The corner button shows a picture full size, and the lock beside a trait keeps it through Randomize, a draft
        and a starter (a locked trait keeps the presentation too). Create character saves it here and draws its
        portrait from these traits, or keeps your photo as its first picture. Try in Create image makes two takes to
        choose from instead. The expression goes into these pictures but not the saved description, so a mention
        leaves the face to each shot.
      </p>
    </Dialog>
  );
}
