import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  applyPicks,
  describeCharacter,
  expressionOf,
  keptLocks,
  optionsFor,
  pickTrait,
  randomizeTraits,
  togglePick,
  traitPicture,
  type Trait,
  traitsFor,
  type Vocabulary,
  withArticle,
} from "./designer.ts";

// registry/characters/traits.yaml as GET /characters/catalog serves it
const trait = (key: string, phrase: string, options: string[], more: Partial<Trait> = {}): Trait =>
  ({ key, label: key, options, phrase, ...more });
const VOCAB: Vocabulary = {
  presentations: ["woman", "man", "androgynous person", "non-human creature"],
  person: [
    trait("age", "", ["young", "teenage", "twenty-something", "thirty-something", "middle-aged", "elderly"]),
    trait("build", "{o} build", ["slight", "athletic", "average", "broad", "heavyset", "very tall"]),
    trait("hair", "", ["short curly", "long straight", "shaved", "braided", "messy shoulder-length", "slicked back", "bald"]),
    trait("hair_color", "", ["black", "dark brown", "auburn", "blonde", "silver", "dyed blue", "bright red"]),
    trait("eyes", "{o} eyes", ["brown", "hazel", "green", "blue", "grey", "amber"]),
    trait("expression", "a {o} expression", ["smiling", "serious", "smirking", "surprised", "tired", "determined"]),
    trait("outfit", "wearing {a}", ["worn leather jacket", "tailored suit", "streetwear hoodie", "flowing robe", "spacesuit", "work overalls"], {
      by_presentation: {
        man: ["plate armour", "cowboy duster", "naval uniform"],
        woman: ["evening dress", "pilot's flight suit", "fencing whites"],
        "androgynous person": ["long trench coat", "lab coat", "desert nomad wraps"],
      },
    }),
    trait("accessory", "with {a}", ["wide-brimmed hat", "knit beanie", "headphones", "wool scarf", "pendant necklace", "bandana"],
          { overrides: { headphones: "with headphones around the neck" } }),
    trait("palette", "dressed in {o}", ["earth tones", "all black", "soft pastels", "bold primaries", "crisp white", "jewel tones"]),
    trait("detail", "{o}", ["scar across one eyebrow", "freckles", "round glasses", "facial tattoo", "eye patch", "silver earrings", "missing tooth"]),
    trait("archetype", "the look of {a}", ["hero", "rebel", "mentor", "trickster", "villain", "everyday person"]),
  ],
  creature: [
    trait("body", "", ["two-legged", "four-legged", "six-legged", "winged", "snake-like", "finned", "tentacled", "blob-like", "plant-like", "golem-like"]),
    trait("likeness", "resembling {a}", ["wolf", "fox", "big cat", "bear", "owl", "lizard", "frog", "octopus", "moth", "beetle", "deer", "shark"]),
    trait("size", "", ["pocket-sized", "cat-sized", "human-sized", "horse-sized", "towering"]),
    trait("stage", "", ["baby", "young", "adult", "ancient"]),
    trait("covering", "covered in {o}", ["fur", "scales", "feathers", "chitin", "smooth skin", "bark and moss", "crystal", "armour plates", "translucent jelly"]),
    trait("coloring", "{o} coloring", ["camouflage", "striped", "spotted", "iridescent", "bioluminescent", "pale", "two-tone", "vivid"]),
    trait("features", "", ["horns", "antlers", "neck frill", "tusks", "beak", "mandibles", "wings", "clubbed tail", "extra arms"], { multi: true }),
    trait("eyes", "", ["two eyes", "single eye", "many eyes", "eyestalks", "no visible eyes", "glowing eyes"]),
    trait("temperament", "", ["adorable", "majestic", "eerie", "fearsome", "comical", "wise"]),
    trait("habitat", "from {o}", ["forests", "deserts", "the deep sea", "arctic ice", "volcanic lands", "caves", "high skies", "swamps"]),
    trait("gear", "wearing {a}", ["pack harness", "rider's saddle", "battle armour", "jewelled collar", "robes", "tech implants"]),
  ],
  styles: ["photoreal", "anime", "comic-book"],
};
const OUTFIT = VOCAB.person.find((t) => t.key === "outfit")!;

/** A repeatable Math.random. */
const seeded = (seed: number) => () => ((seed = (seed * 1664525 + 1013904223) % 2 ** 32) / 2 ** 32);

/** Whether every pick is one its presentation offers. */
const fits = (traits: Record<string, string>) => traitsFor(VOCAB, traits.presentation).every((t) =>
  !traits[t.key] || traits[t.key].split(", ").every((p) => optionsFor(t, traits.presentation).includes(p)));

test("the traits and their wording here are the registry's", () => {
  const yaml = readFileSync(new URL("../../../../registry/characters/traits.yaml", import.meta.url), "utf8");
  const [, person, creature] = yaml.split(/^(?:person|creature):$/m);
  for (const [kind, text] of [["person", person], ["creature", creature]] as const) {
    const said = text.split(/^ {2}- key: /m).slice(1).map((block) => [
      block.split(/\r?\n/)[0].trim(),
      /^\s+phrase: "(.*)"$/m.exec(block)?.[1] ?? "",
      /^\s+overrides: \{(.*)\}$/m.exec(block)?.[1] ?? "",
    ]);
    assert.deepEqual(said, VOCAB[kind].map((t) => [t.key, t.phrase,
      Object.entries(t.overrides ?? {}).map(([o, words]) => `${o}: ${words}`).join(", ")]));
  }
});

test("plural and mass nouns take no article", () => {
  assert.equal(withArticle("work overalls"), "work overalls");
  assert.equal(withArticle("round glasses"), "round glasses");
  assert.equal(withArticle("worn armor"), "worn armor");
});

test("singular nouns take a or an by their sound", () => {
  assert.equal(withArticle("evening dress"), "an evening dress");
  assert.equal(withArticle("tailored suit"), "a tailored suit");
  assert.equal(withArticle("elderly man"), "an elderly man");
  assert.equal(withArticle("European woman"), "a European woman");
  assert.equal(withArticle("hero"), "a hero");
});

test("the designer's description reads as English", () => {
  assert.equal(describeCharacter(VOCAB, { age: "young", presentation: "man", outfit: "work overalls", archetype: "everyday person" }, ""),
               "a young man, wearing work overalls, the look of an everyday person");
  assert.equal(describeCharacter(VOCAB, { presentation: "woman", build: "athletic", hair: "braided", hair_color: "auburn", eyes: "grey",
                                          outfit: "pilot's flight suit", detail: "freckles" }, "Irish"),
               "an Irish woman, athletic build, braided auburn hair, grey eyes, wearing a pilot's flight suit, freckles");
});

test("the new traits read in their own words, in the vocabulary's order", () => {
  assert.equal(describeCharacter(VOCAB, { presentation: "man", eyes: "brown", expression: "smirking", outfit: "streetwear hoodie",
                                          accessory: "headphones", palette: "bold primaries", detail: "silver earrings" }, ""),
               "a man, brown eyes, wearing a streetwear hoodie, with headphones around the neck, dressed in bold primaries, "
               + "silver earrings");
  assert.equal(describeCharacter(VOCAB, { accessory: "wide-brimmed hat", palette: "earth tones" }, ""),
               "with a wide-brimmed hat, dressed in earth tones");
});

test("the expression words the designer's pictures, never the description every mention carries", () => {
  const kai = { presentation: "man", age: "twenty-something", expression: "smirking" };
  assert.equal(describeCharacter(VOCAB, kai, ""), "a twenty-something man");
  assert.equal(expressionOf(VOCAB, kai), "a smirking expression");
  assert.equal(expressionOf(VOCAB, { presentation: "woman" }), "");
  assert.equal(expressionOf(VOCAB, { presentation: "non-human creature", expression: "smirking" }), "");
});

test("the longest description fits what a mention carries (DESCRIPTION_LIMIT, director/elements.py)", () => {
  const py = readFileSync(new URL("../../../server/src/studio/director/elements.py", import.meta.url), "utf8");
  const limit = Number(/^DESCRIPTION_LIMIT = (\d+)$/m.exec(py)?.[1]);
  for (const presentation of VOCAB.presentations) {
    const longest: Record<string, string> = { presentation }; // each trait's longest pick (every feature)
    for (const t of traitsFor(VOCAB, presentation)) {
      const options = optionsFor(t, presentation);
      longest[t.key] = t.multi ? options.join(", ") : options.reduce((a, b) =>
        (describeCharacter(VOCAB, { ...longest, [t.key]: b }, "").length
          > describeCharacter(VOCAB, { ...longest, [t.key]: a }, "").length ? b : a));
    }
    const words = describeCharacter(VOCAB, longest, "");
    assert.ok(words.length <= limit, `${words.length} > ${limit}: ${words}`);
  }
});

test("changing between a person and a creature lets go of the other kind's locks", () => {
  const creature = { presentation: "non-human creature", features: "horns" };
  const woman = pickTrait(VOCAB, creature, "presentation", "woman");
  assert.deepEqual(keptLocks(creature, woman, ["features", "eyes", "style", "presentation"]), ["style", "presentation"]);
  assert.deepEqual(keptLocks(woman, { ...woman, presentation: "man" }, ["eyes"]), ["eyes"]); // a person still
});

test("a new presentation clears an outfit it doesn't offer", () => {
  const woman = { presentation: "woman", outfit: "evening dress" };
  assert.equal(pickTrait(VOCAB, woman, "presentation", "man").outfit, "");
  assert.equal(pickTrait(VOCAB, woman, "presentation", "").outfit, "");
  assert.equal(pickTrait(VOCAB, { ...woman, outfit: "spacesuit" }, "presentation", "androgynous person").outfit, "spacesuit");
  assert.ok(!optionsFor(OUTFIT, "man").includes("evening dress"));
  assert.deepEqual(optionsFor(OUTFIT, ""), optionsFor(OUTFIT, "man")); // with none picked, the man's
});

test("changing between a person and a creature keeps only the style (a catalog id)", () => {
  const person = { presentation: "woman", style: "comic-book", hair: "braided", outfit: "spacesuit" };
  assert.deepEqual(pickTrait(VOCAB, person, "presentation", "non-human creature"), { presentation: "non-human creature", style: "comic-book" });
  const creature = { presentation: "non-human creature", body: "winged", features: "horns" };
  assert.deepEqual(pickTrait(VOCAB, creature, "presentation", ""), { presentation: "", style: "" });
});

test("a creature reads as one description, with several features", () => {
  const features = togglePick(togglePick(togglePick(undefined, "horns"), "beak"), "clubbed tail");
  assert.equal(features, "horns, beak, clubbed tail");
  assert.equal(togglePick(features, "beak"), "horns, clubbed tail");
  assert.equal(describeCharacter(VOCAB, { presentation: "non-human creature", temperament: "majestic", size: "horse-sized", body: "four-legged",
                                          likeness: "owl", covering: "feathers", coloring: "iridescent", features, eyes: "glowing eyes",
                                          habitat: "arctic ice", gear: "rider's saddle" }, "Korean"),
               "a majestic horse-sized four-legged creature, resembling an owl, covered in feathers, iridescent coloring, "
               + "with horns, a beak, a clubbed tail and glowing eyes, from arctic ice, wearing a rider's saddle");
  assert.equal(describeCharacter(VOCAB, { presentation: "non-human creature" }, ""), "a creature");
});

test("pictures follow the presentation; none picked shows the man's set", () => {
  assert.equal(traitPicture("hair", "short curly", "woman"), "/designer/woman/hair-short-curly.webp");
  assert.equal(traitPicture("outfit", "pilot's flight suit", "woman", true), "/designer/woman/full/outfit-pilot-s-flight-suit.webp");
  assert.equal(traitPicture("age", "elderly", ""), "/designer/man/age-elderly.webp");
  assert.equal(traitPicture("presentation", "woman", "non-human creature"), "/designer/man/presentation-woman.webp");
  assert.equal(traitPicture("expression", "smirking", "androgynous person"), "/designer/androgynous-person/expression-smirking.webp");
});

test("Randomize re-rolls every trait from its options, the style from the catalog", () => {
  const rand = seeded(7);
  for (let i = 0; i < 200; i++) {
    const next = randomizeTraits(VOCAB, {}, [], rand);
    assert.ok(VOCAB.presentations.includes(next.presentation));
    assert.ok(VOCAB.styles!.includes(next.style));
    assert.ok(fits(next), JSON.stringify(next));
    assert.ok(traitsFor(VOCAB, next.presentation).every((t) => next[t.key]), "every trait gets a pick");
  }
  assert.deepEqual(randomizeTraits(VOCAB, {}, [], seeded(3)), randomizeTraits(VOCAB, {}, [], seeded(3)));
  assert.equal(randomizeTraits({ ...VOCAB, styles: [] }, { style: "anime" }, [], seeded(3)).style, "anime"); // no catalog yet
});

test("locked traits survive Randomize, and a locked one keeps the presentation", () => {
  const rand = seeded(11);
  const woman = { presentation: "woman", outfit: "evening dress", hair: "braided", style: "@faded-film" };
  for (let i = 0; i < 100; i++) {
    const next = randomizeTraits(VOCAB, woman, ["outfit", "style"], rand);
    assert.equal(next.presentation, "woman"); // so the locked outfit stays one she offers
    assert.equal(next.outfit, "evening dress");
    assert.equal(next.style, "@faded-film"); // a locked saved style too
    assert.ok(fits(next));
  }
  const creature = { presentation: "non-human creature", features: "horns, wings" };
  assert.equal(randomizeTraits(VOCAB, creature, ["features"], rand).features, "horns, wings");
  assert.equal(randomizeTraits(VOCAB, creature, ["features"], rand).presentation, "non-human creature");
  assert.equal(randomizeTraits(VOCAB, { presentation: "man", age: "" }, ["age"], rand).age, ""); // locked as Any
  assert.equal(randomizeTraits(VOCAB, { presentation: "" }, ["presentation"], rand).presentation, "");
  // a lock on a trait the presentation doesn't show (left from a person) holds nothing
  const seen = new Set(Array.from({ length: 60 }, () => randomizeTraits(VOCAB, creature, ["hair"], rand).presentation));
  assert.ok(seen.size > 1);
});

test("a draft or a starter fills every trait not locked, and only with options its presentation offers", () => {
  const mine = { presentation: "woman", hair: "braided", outfit: "spacesuit", eyes: "green", style: "anime" };
  const kai = { presentation: "man", hair: "short curly", eyes: "brown", outfit: "streetwear hoodie", style: "photoreal" };
  assert.deepEqual(applyPicks(VOCAB, mine, [], kai), { ...Object.fromEntries(VOCAB.person.map((t) => [t.key, ""])), ...kai });
  const kept = applyPicks(VOCAB, mine, ["hair"], { ...kai, outfit: "naval uniform" });
  assert.equal(kept.presentation, "woman"); // her locked hair keeps her
  assert.equal(kept.hair, "braided");
  assert.equal(kept.eyes, "brown");
  assert.equal(kept.outfit, ""); // a man's own outfit isn't hers
  assert.equal(kept.style, "photoreal");
  assert.equal(applyPicks(VOCAB, mine, ["style"], kai).style, "anime");
  assert.equal(applyPicks(VOCAB, mine, [], { ...kai, style: "" }).style, "anime"); // a draft naming no style leaves yours
  const bramble = applyPicks(VOCAB, mine, [], { presentation: "non-human creature", body: "golem-like", features: "antlers, wings" });
  assert.equal(bramble.hair, undefined); // a person's traits go with the person
  assert.equal(bramble.features, "antlers, wings");
});
