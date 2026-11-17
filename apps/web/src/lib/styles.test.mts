import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import { test } from "node:test";
import { designerStyle, filterStyles, SAMPLE_SUBJECTS, STYLE_CATEGORIES, stylePicture } from "./styles.ts";

const STYLES = [
  { id: "photoreal", label: "Photoreal", category: "photo", summary: "True-to-life photograph.", prompt: "Photorealistic photograph, natural light" },
  { id: "watercolor", label: "Watercolor", category: "painting", summary: "Soft washes on paper.", prompt: "Watercolor painting, cold-press paper grain" },
  { id: "pixel-art", label: "Pixel art", category: "retro", summary: "Chunky game sprites.", prompt: "16-bit pixel art, limited palette" },
];

test("sample pictures: 256 px beside the 768 px ones in full/", () => {
  assert.deepEqual([...SAMPLE_SUBJECTS], ["person", "place", "object"]);
  assert.equal(stylePicture("pixel-art", "place"), "/styles/pixel-art-place.webp");
  assert.equal(stylePicture("pixel-art", "object", true), "/styles/full/pixel-art-object.webp");
});

test("styles filter by category and by words in the label, summary or prompt", () => {
  assert.deepEqual(filterStyles(STYLES, "all", "").map((s) => s.id), ["photoreal", "watercolor", "pixel-art"]);
  assert.deepEqual(filterStyles(STYLES, "painting", "").map((s) => s.id), ["watercolor"]);
  assert.deepEqual(filterStyles(STYLES, "all", " PIXEL ").map((s) => s.id), ["pixel-art"]);
  assert.deepEqual(filterStyles(STYLES, "all", "washes").map((s) => s.id), ["watercolor"]);
  assert.deepEqual(filterStyles(STYLES, "all", "paper grain").map((s) => s.id), ["watercolor"]);
  assert.deepEqual(filterStyles(STYLES, "photo", "pixel"), []);
});

test("the designer's old style phrases become catalog ids; an id passes through", () => {
  assert.equal(designerStyle("photoreal"), "photoreal");
  assert.equal(designerStyle("cinematic film still"), "cinematic-still");
  assert.equal(designerStyle("anime"), "anime");
  assert.equal(designerStyle("stylized 3D animation"), "animated-3d");
  assert.equal(designerStyle("graphic novel"), "comic-book");
  assert.equal(designerStyle("watercolor-chronicle"), "watercolor-chronicle");
  assert.equal(designerStyle("oil painting style"), "");
  assert.equal(designerStyle("constructor"), "constructor"); // id-shaped, not an inherited key
  assert.equal(designerStyle(""), "");
  assert.equal(designerStyle(undefined), "");
});

test("the categories are the registry's folders and the old phrases map to styles it has", () => {
  const root = new URL("../../../../registry/styles/", import.meta.url);
  const folders = readdirSync(root).sort();
  assert.deepEqual(folders, STYLE_CATEGORIES.map((c) => c.id).sort());
  const ids = new Set(folders.flatMap((c) => readdirSync(new URL(`${c}/`, root)).map((f) => f.replace(/\.yaml$/, ""))));
  for (const phrase of ["photoreal", "cinematic film still", "anime", "stylized 3D animation", "graphic novel"]) {
    assert.ok(ids.has(designerStyle(phrase)), phrase);
  }
});
