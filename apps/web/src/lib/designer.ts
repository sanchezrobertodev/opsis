/** "a"/"an" and the phrase, or the phrase alone when it ends in a plural or mass noun ("work overalls", "armor"). */
export function withArticle(phrase: string): string {
  const last = phrase.split(/\s+/).pop() ?? "";
  if (/[^siu]s$/i.test(last) || /^(armou?r|attire|clothing|gear|denim|chainmail)$/i.test(last)) return phrase;
  return `${/^(?!eu|uni)[aeiou]/i.test(phrase) ? "an" : "a"} ${phrase}`;
}

/** Design a character's picks as one description: "a young woman, athletic build, ..., wearing a tailored suit". */
export function describeCharacter(traits: Record<string, string>, heritage: string): string {
  const who = traits.age || heritage || traits.presentation
    ? [traits.age, heritage, traits.presentation || "person"].filter(Boolean).join(" ")
    : "";
  const parts = [
    who && withArticle(who),
    traits.build && `${traits.build} build`,
    (traits.hair || traits.hair_color) && `${[traits.hair, traits.hair_color].filter(Boolean).join(" ")} hair`,
    traits.eyes && `${traits.eyes} eyes`,
    traits.outfit && `wearing ${withArticle(traits.outfit)}`,
    traits.detail,
    traits.archetype && `the look of ${withArticle(traits.archetype)}`,
  ].filter(Boolean);
  return parts.join(", ");
}

const BASE_OUTFITS = ["worn leather jacket", "tailored suit", "streetwear hoodie", "flowing robe", "spacesuit", "work overalls"];

/** Outfits by presentation; with none picked the man's list (and pictures) show. */
export const OUTFITS: Record<string, string[]> = {
  man: [...BASE_OUTFITS, "plate armour", "cowboy duster", "naval uniform"],
  woman: [...BASE_OUTFITS, "evening dress", "pilot's flight suit", "fencing whites"],
  "androgynous person": [...BASE_OUTFITS, "long trench coat", "lab coat", "desert nomad wraps"],
  "non-human creature": ["flowing robe", "spacesuit", "worn leather jacket", "moss-covered cloak", "bone armour",
                         "jewelled harness", "tattered pirate coat"],
};

export const outfitsFor = (presentation?: string) => OUTFITS[presentation || "man"] ?? OUTFITS.man;

/** Sets one trait ("" = Any); a new presentation clears an outfit it doesn't offer. */
export function pickTrait(traits: Record<string, string>, key: string, value: string): Record<string, string> {
  const next = { ...traits, [key]: value };
  if (key === "presentation" && next.outfit && !outfitsFor(value).includes(next.outfit)) next.outfit = "";
  return next;
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
