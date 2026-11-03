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
