/** The character designer's picks and words. Its vocabulary is registry data (`registry/characters/traits.yaml`, served
 * as `GET /characters/catalog`) and comes in as an argument, so this file has no imports and `node --test` loads it. */

/** A trait: its options (`by_presentation` adds a presentation's own after them), whether it takes several picks, and
 * how a pick reads in a description: an option's `overrides` entry, else `phrase` ({o} the option, {a} it with a/an),
 * else the option itself. A few traits without a phrase are worded with others here (age, hair, a creature's noun). */
export type Trait = {
  key: string;
  label: string;
  options: string[];
  multi?: boolean;
  by_presentation?: Record<string, string[]>;
  phrase?: string;
  overrides?: Record<string, string>;
};

/** The presentations, a person's and a creature's traits, and the catalog style ids Randomize picks from. */
export type Vocabulary = { presentations: string[]; person: Trait[]; creature: Trait[]; styles?: string[] };

export type Traits = Record<string, string>;

/** "a"/"an" and the phrase, or the phrase alone when it ends in a plural or mass noun ("work overalls", "armor"). */
export function withArticle(phrase: string): string {
  const last = phrase.split(/\s+/).pop() ?? "";
  if (/[^siu]s$/i.test(last) || /^(armou?r|attire|clothing|gear|denim|chainmail)$/i.test(last)) return phrase;
  return `${/^(?!eu|uni)[aeiou]/i.test(phrase) ? "an" : "a"} ${phrase}`;
}

/** A pick in its trait's words. */
function worded(t: Trait, option: string): string {
  const words = t.overrides?.[option] ?? t.phrase ?? "";
  return words ? words.replace(/\{([oa])\}/g, (_, k: string) => (k === "o" ? option : withArticle(option))) : option;
}

/** Design a character's picks as one description: "a young woman, athletic build, ..., wearing a tailored suit", in the
 * vocabulary's order. It's what a character is saved with and every @mention carries. The age, heritage and
 * presentation open it; the hair is worded with its color; the expression stays out (`expressionOf`). */
export function describeCharacter(vocab: Vocabulary, traits: Traits, heritage: string): string {
  if (traits.presentation === CREATURE) return describeCreature(vocab, traits);
  const who = traits.age || heritage || traits.presentation
    ? [traits.age, heritage, traits.presentation || "person"].filter(Boolean).join(" ")
    : "";
  const hair = [traits.hair, traits.hair_color].filter(Boolean).join(" ");
  return [
    who && withArticle(who),
    ...vocab.person.map((t) => (t.key === "hair" ? hair && `${hair} hair`
      : ["age", "hair_color", "expression"].includes(t.key) || !traits[t.key] ? "" : worded(t, traits[t.key]))),
  ].filter(Boolean).join(", ");
}

/** The expression in its words ("a smiling expression"), or "". It's how a person looks in one picture, not who they
 * are, so it words the designer's own pictures (the portrait, Try in Create image) and never the description: a
 * mention would fix the face for every shot. */
export function expressionOf(vocab: Vocabulary, traits: Traits): string {
  const t = vocab.person.find((p) => p.key === "expression");
  return t && traits.expression && traits.presentation !== CREATURE ? worded(t, traits.expression) : "";
}

/** The presentation whose traits are a creature's own (body shape, covering, features...) instead of a person's. */
export const CREATURE = "non-human creature";

/** A trait with several picks keeps them in one string ("horns, wings"), so traits stay plain strings; no option holds ", ". */
export const picks = (value?: string) => (value ? value.split(", ") : []);

export const togglePick = (value: string | undefined, option: string) =>
  (picks(value).includes(option) ? picks(value).filter((p) => p !== option) : [...picks(value), option]).join(", ");

const andList = (xs: string[]) => (xs.length > 1 ? `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}` : xs[0]);

/** A creature's traits that make its noun: "a majestic horse-sized four-legged creature". */
const NOUN = ["temperament", "stage", "size", "body"];

/** A creature's picks: "a majestic horse-sized four-legged creature, resembling an owl, covered in feathers, ...", its
 * features and eyes together ("with horns and glowing eyes"). */
export function describeCreature(vocab: Vocabulary, t: Traits): string {
  const has = [...picks(t.features), t.eyes].filter(Boolean).map(withArticle);
  return [
    withArticle([...NOUN.map((k) => t[k]), "creature"].filter(Boolean).join(" ")),
    ...vocab.creature.map((c) => (c.key === "features" ? has.length > 0 && `with ${andList(has)}`
      : NOUN.includes(c.key) || c.key === "eyes" || !t[c.key] ? "" : worded(c, t[c.key]))),
  ].filter(Boolean).join(", ");
}

/** The traits under the presentation: a creature's own, else a person's. */
export const traitsFor = (vocab: Vocabulary, presentation?: string) =>
  (presentation === CREATURE ? vocab.creature : vocab.person);

/** A trait's options for the presentation: the shared ones, then its own (with none picked, the man's). */
export const optionsFor = (t: Trait, presentation?: string) =>
  [...t.options, ...(t.by_presentation?.[presentation || "man"] ?? [])];

const offers = (t: Trait, presentation: string | undefined, value: string) =>
  picks(value).every((p) => optionsFor(t, presentation).includes(p));

/**
 * Sets one trait ("" = Any); a new presentation clears a pick it doesn't offer (another presentation's outfit). A
 * person and a creature have different traits, so changing between them keeps only the style.
 */
export function pickTrait(vocab: Vocabulary, traits: Traits, key: string, value: string): Traits {
  if (key === "presentation" && (value === CREATURE) !== (traits.presentation === CREATURE)) {
    return { presentation: value, style: traits.style ?? "" };
  }
  const next = { ...traits, [key]: value };
  if (key === "presentation") {
    for (const t of traitsFor(vocab, value)) if (next[t.key] && !offers(t, value, next[t.key])) next[t.key] = "";
  }
  return next;
}

/** The locks that still hold after new picks: a change between a person and a creature takes the traits of the kind it
 * leaves (see `pickTrait`), so only the presentation's and the style's stay. A lock left on a trait it took would hold
 * that trait at Any, and the presentation with it, once Randomize came back to that kind. */
export function keptLocks(before: Traits, after: Traits, locked: readonly string[]): string[] {
  return (before.presentation === CREATURE) === (after.presentation === CREATURE)
    ? [...locked] : locked.filter((k) => k === "presentation" || k === "style");
}

/** The presentation stays while it's locked or any trait under it is (a person's hair, a creature's wings), so a
 * locked pick always keeps fitting. */
const holdsPresentation = (vocab: Vocabulary, traits: Traits, locked: readonly string[]) =>
  locked.includes("presentation") || traitsFor(vocab, traits.presentation).some((t) => locked.includes(t.key));

/**
 * Picks from a draft, a starter or Randomize, through `pickTrait`, over every trait not locked: the presentation
 * (unless it stays, see above), then each trait under it, Any when the picks leave it out or the presentation doesn't
 * offer them. The style changes only to another one, so a draft that names none leaves yours.
 */
export function applyPicks(vocab: Vocabulary, traits: Traits, locked: readonly string[], incoming: Traits): Traits {
  let next = holdsPresentation(vocab, traits, locked) ? traits
    : pickTrait(vocab, traits, "presentation", incoming.presentation ?? "");
  for (const t of traitsFor(vocab, next.presentation)) {
    const value = incoming[t.key] ?? "";
    if (!locked.includes(t.key)) next = pickTrait(vocab, next, t.key, offers(t, next.presentation, value) ? value : "");
  }
  return incoming.style && !locked.includes("style") ? { ...next, style: incoming.style } : next;
}

/**
 * Randomize: a random option for every trait not locked (two for a trait with several picks, or one if they match),
 * a random presentation unless it stays (see `holdsPresentation`), and a random catalog style unless the style is
 * locked. `rand` is like Math.random.
 */
export function randomizeTraits(vocab: Vocabulary, traits: Traits, locked: readonly string[],
                                rand: () => number = Math.random): Traits {
  const any = (xs: string[]) => xs[Math.floor(rand() * xs.length)] ?? "";
  const presentation = holdsPresentation(vocab, traits, locked) ? traits.presentation ?? "" : any(vocab.presentations);
  const picked: Traits = { presentation, style: any(vocab.styles ?? []) };
  for (const t of traitsFor(vocab, presentation)) {
    const options = optionsFor(t, presentation);
    picked[t.key] = t.multi ? [...new Set([any(options), any(options)])].join(", ") : any(options);
  }
  return applyPicks(vocab, traits, locked, picked);
}

/** An option's file name: the option as a slug. */
export const traitFile = (option: string) => option.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

/**
 * A trait option's example picture in `public/designer/<presentation>/` (`full/` at 768 px): the picked presentation's
 * set, the man's with none picked. The Presentation row itself always shows the man's set's four pictures.
 */
export function traitPicture(key: string, option: string, presentation?: string, full = false): string {
  const set = key === "presentation" || !presentation ? "man" : traitFile(presentation);
  return `/designer/${set}/${full ? "full/" : ""}${key}-${traitFile(option)}.webp`;
}
