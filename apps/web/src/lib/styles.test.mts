import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import { test } from "node:test";
import { claudeSummary, designerStyle, filterStyles, SAMPLE_SUBJECTS, STYLE_CATEGORIES, stylePicture, stylesRun } from "./styles.ts";

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

test("a saved style's @handle passes through the designer too", () => {
  assert.equal(designerStyle("@ink-and-wash"), "@ink-and-wash");
  assert.equal(designerStyle("@noir-2"), "@noir-2");
  assert.equal(designerStyle("@"), "");
  assert.equal(designerStyle("@@noir"), "");
  assert.equal(designerStyle("@Noir wash"), "");
});

test("several styles: one compare entry per style on the same model and settings, sharing the rest", () => {
  const take = {
    model_id: "nano-banana-2", mode: "t2i", prompt: "A lighthouse at dusk", params: { aspect_ratio: "1:1" },
    media: [{ role: "image_references", asset_id: "a1" }],
    direction: { style: "anime", lighting: "golden-hour" },
  };
  const run = stylesRun(take, [{ id: "watercolor", label: "Watercolor" }, { id: "@my-noir", label: "My noir" }], ["t2i"]);
  assert.deepEqual(run, {
    recipe: "compare",
    title: "Styles: A lighthouse at dusk",
    input: {
      prompt: "A lighthouse at dusk",
      entries: [
        { model_id: "nano-banana-2", mode: "t2i", params: { aspect_ratio: "1:1" }, direction: { style: "watercolor" }, label: "Watercolor" },
        { model_id: "nano-banana-2", mode: "t2i", params: { aspect_ratio: "1:1" }, direction: { style: "@my-noir" }, label: "My noir" },
      ],
      media: [{ role: "image_references", asset_id: "a1" }],
      direction: { lighting: "golden-hour" }, // the current style stays out: each take has its own
      ops: ["t2i"],
      pick: true,
    },
  });
  // a direction that was only the style leaves nothing to share
  assert.equal(stylesRun({ ...take, direction: { style: "anime" } }, [], []).input.direction, null);
  assert.equal(stylesRun({ ...take, direction: null }, [], []).input.direction, null);
});

test("a saved style named like another picked style is labelled with its handle too", () => {
  const take = { model_id: "nano-banana-2", mode: "t2i", prompt: "A heron", params: {}, media: [] };
  const labels = (styles: { id: string; label: string }[]) => stylesRun(take, styles, []).input.entries.map((e) => e.label);
  assert.deepEqual(labels([{ id: "watercolor", label: "Watercolor" }, { id: "@watercolor", label: "Watercolor" }]),
                   ["Watercolor", "Watercolor (@watercolor)"]);
  assert.deepEqual(labels([{ id: "@noir", label: "Noir" }, { id: "@noir-2", label: "Noir" }, { id: "anime", label: "Anime" }]),
                   ["Noir (@noir)", "Noir (@noir-2)", "Anime"]);
  assert.deepEqual(labels([{ id: "@noir", label: "Noir" }, { id: "anime", label: "Anime" }]), ["Noir", "Anime"]);
  assert.equal(labels([{ id: "@a", label: "N".repeat(80) }, { id: "@b", label: "N".repeat(80) }])[0].length, 80); // the run's limit
  assert.equal(stylesRun({ ...take, prompt: "x".repeat(300) }, [], []).title.length, "Styles: ".length + 80);
});

test("a saved style keeps Claude's gallery line only while its look is the one Claude described", () => {
  const described = { look: "soft washes, cold-press paper", summary: "Soft washes on paper." };
  assert.equal(claudeSummary("soft washes, cold-press paper ", described), "Soft washes on paper.");
  assert.equal(claudeSummary("charcoal, heavy cross-hatching", described), ""); // rewritten: the look shows itself
  assert.equal(claudeSummary("soft washes, cold-press paper", undefined), ""); // never described
  assert.equal(claudeSummary("soft washes, cold-press paper", null), "");
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
