import assert from "node:assert/strict";
import { test } from "node:test";
import { describeCharacter, outfitsFor, pickTrait, togglePick, traitPicture, withArticle } from "./designer.ts";

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
  assert.equal(describeCharacter({ age: "young", presentation: "man", outfit: "work overalls", archetype: "everyday person" }, ""),
               "a young man, wearing work overalls, the look of an everyday person");
});

test("a new presentation clears an outfit it doesn't offer", () => {
  const woman = { presentation: "woman", outfit: "evening dress" };
  assert.equal(pickTrait(woman, "presentation", "man").outfit, "");
  assert.equal(pickTrait(woman, "presentation", "").outfit, "");
  assert.equal(pickTrait({ ...woman, outfit: "spacesuit" }, "presentation", "androgynous person").outfit, "spacesuit");
  assert.ok(!outfitsFor("man").includes("evening dress"));
});

test("changing between a person and a creature keeps only the style", () => {
  const person = { presentation: "woman", style: "anime", hair: "braided", outfit: "spacesuit" };
  assert.deepEqual(pickTrait(person, "presentation", "non-human creature"), { presentation: "non-human creature", style: "anime" });
  const creature = { presentation: "non-human creature", body: "winged", features: "horns" };
  assert.deepEqual(pickTrait(creature, "presentation", ""), { presentation: "", style: "" });
});

test("a creature reads as one description, with several features", () => {
  const features = togglePick(togglePick(togglePick(undefined, "horns"), "beak"), "clubbed tail");
  assert.equal(features, "horns, beak, clubbed tail");
  assert.equal(togglePick(features, "beak"), "horns, clubbed tail");
  assert.equal(describeCharacter({ presentation: "non-human creature", temperament: "majestic", size: "horse-sized", body: "four-legged",
                                   likeness: "owl", covering: "feathers", coloring: "iridescent", features, eyes: "glowing eyes",
                                   habitat: "arctic ice", gear: "rider's saddle" }, "Korean"),
               "a majestic horse-sized four-legged creature, resembling an owl, covered in feathers, iridescent coloring, "
               + "with horns, a beak, a clubbed tail and glowing eyes, from arctic ice, wearing a rider's saddle");
  assert.equal(describeCharacter({ presentation: "non-human creature" }, ""), "a creature");
});

test("pictures follow the presentation; none picked shows the man's set", () => {
  assert.equal(traitPicture("hair", "short curly", "woman"), "/designer/woman/hair-short-curly.webp");
  assert.equal(traitPicture("outfit", "pilot's flight suit", "woman", true), "/designer/woman/full/outfit-pilot-s-flight-suit.webp");
  assert.equal(traitPicture("age", "elderly", ""), "/designer/man/age-elderly.webp");
  assert.equal(traitPicture("presentation", "woman", "non-human creature"), "/designer/man/presentation-woman.webp");
});
