import assert from "node:assert/strict";
import { test } from "node:test";
import { describeCharacter, withArticle } from "./designer.ts";

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
