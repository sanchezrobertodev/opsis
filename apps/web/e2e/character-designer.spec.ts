import path from "node:path";
import { expect, type Page, test } from "@playwright/test";

// Every call is answered here, so this runs without the API (like my-styles.spec.ts).
const PUBLIC = path.resolve(__dirname, "../public");
const SUMMARY = {
  id: "nano-banana-2", name: "Nano Banana 2", kind: "model", family: "gemini", vendor: "test", description: "Stills.",
  output_type: "image", status: "active", license_tier: "commercial", tags: [], runnable: true,
  modes: [{ id: "t2i", label: "Text to image", ops: ["t2i"] }],
};
const style = (id: string, label: string) =>
  ({ id, label, category: "photo", summary: `${label}.`, prompt: `${label.toLowerCase()} picture`, dialects: {}, swatch: [], photographic: true });
const LOOKS = ["moves", "shot_sizes", "angles", "lenses", "focal_lengths", "apertures", "camera_bodies", "eras", "lighting",
  "light_sources", "light_positions", "light_colors", "palettes", "genres", "pacing", "emotions"];
const LIBRARY = { ...Object.fromEntries(LOOKS.map((s) => [s, []])), styles: [style("photoreal", "Photoreal"), style("anime", "Anime")] };
const trait = (key: string, label: string, options: string[], more: Record<string, unknown> = {}) =>
  ({ key, label, options, multi: false, by_presentation: {}, phrase: "", overrides: {}, ...more });
const CATALOG = {
  presentations: ["woman", "man", "androgynous person", "non-human creature"],
  person: [
    trait("age", "Age", ["young", "elderly"]),
    trait("hair", "Hair", ["braided", "short curly", "shaved"]),
    trait("hair_color", "Hair color", ["black", "silver"]),
    trait("expression", "Expression", ["smiling", "smirking"], { phrase: "a {o} expression" }),
    trait("outfit", "Outfit", ["spacesuit", "work overalls"], { phrase: "wearing {a}",
      by_presentation: { man: ["naval uniform"], woman: ["evening dress"], "androgynous person": ["lab coat"] } }),
    trait("accessory", "Accessory", ["headphones", "wool scarf"],
          { phrase: "with {a}", overrides: { headphones: "with headphones around the neck" } }),
  ],
  creature: [trait("body", "Body shape", ["golem-like", "four-legged"]),
             trait("features", "Features (pick any number)", ["antlers", "wings"], { multi: true })],
  voice_model: "eleven-v4",
  starters: [
    { id: "iris-vale", name: "Iris Vale", summary: "A botanist.", style: "photoreal", voice_id: "pFZP5JQG7iQjIQuC4Bku",
      traits: { presentation: "woman", age: "elderly", hair: "braided", hair_color: "silver", expression: "smiling",
                outfit: "work overalls", accessory: "wool scarf" } },
    { id: "kai-mendes", name: "Kai Mendes", summary: "A skater.", style: "anime", voice_id: "IKne3meq5aSn9XLyUdCD",
      traits: { presentation: "man", age: "young", hair: "short curly", hair_color: "black", expression: "smirking",
                outfit: "naval uniform", accessory: "headphones" } },
  ],
};
const asset = (id: string, title: string) => ({
  id, kind: "image", status: "ready", source: "upload", mime: "image/webp", url: `/media/${id}.webp`, thumb_url: null,
  poster_url: null, filmstrip_url: null, filmstrip_frames: null, proxy_url: null, width: 256, height: 256, duration_ms: null,
  fps: null, has_audio: null, size_bytes: 1000, title, tags: [], favorite: false, generation_id: null,
  output_index: null, metadata: {}, created_at: "2026-11-17T10:00:00Z",
});
const STREET = asset("street", "Rainy street");
const PORTRAIT = asset("portrait", "Uploaded portrait");

/** The API this page talks to, answered in the browser; `sent` keeps what was posted, by path. */
async function studio(page: Page, draft: Record<string, unknown>) {
  const sent: Record<string, Record<string, unknown>[]> = {};
  const keep = (key: string, body: Record<string, unknown>) => (sent[key] ??= []).push(body);
  const saved: Record<string, unknown>[] = [];
  await page.route("http://127.0.0.1:8400/**", (route) => route.fulfill({ status: 503, json: { detail: "off in this test" } }));
  await page.route("**/events*", (route) => route.abort());
  await page.route("**/registry", (route) => route.fulfill({ json: { hash: "h", models: [SUMMARY], provider_mode: "mock", warnings: [] } }));
  await page.route("**/director/library", (route) => route.fulfill({ json: LIBRARY }));
  await page.route("**/characters/catalog", (route) => route.fulfill({ json: CATALOG }));
  await page.route("**/llm/calls", (route) => route.fulfill({ json: { mode: "mock", verified: "2026-11-01", calls: {
    "character.from_photo": { model: "claude-haiku-5-5", name: "Claude Haiku 5.5", usd: 0.006 },
    "character.from_text": { model: "claude-haiku-5-5", name: "Claude Haiku 5.5", usd: 0.004 } } } }));
  await page.route("**/characters/draft", (route) => {
    keep("draft", route.request().postDataJSON());
    return route.fulfill({ json: { ...draft, model: "claude-haiku-5-5", mock: true, cost_usd: 0 } });
  });
  await page.route("**/assets?*", (route) => route.fulfill({ json: { items: [STREET], next_cursor: null } }));
  await page.route("**/media/*", (route) => route.fulfill({ path: path.join(PUBLIC, "styles/watercolor-person.webp") }));
  await page.route("**/uploads", (route) => route.fulfill({ json: PORTRAIT }));
  await page.route("**/pipelines/estimate", (route) => route.fulfill({ json: {
    estimate_usd: 0, live_estimate_usd: 0.04, generations: 1, claude_usd: 0, claude_live_usd: 0 } }));
  await page.route("**/pipelines", (route) => {
    keep("pipelines", route.request().postDataJSON());
    return route.fulfill({ status: 201, json: { id: "run-1", status: "running" } });
  });
  await page.route(/\/elements(\?.*)?$/, (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: saved });
    const body = route.request().postDataJSON();
    keep("elements", body);
    const slug = body.name.toLowerCase().replace(/\s+/g, "-");
    const e = { id: `e${saved.length + 1}`, kind: body.kind, name: body.name, slug, handle: `@${slug}`, description: body.description,
                attributes: body.attributes, voice: body.voice ?? null, lora: null, cover: null, images: [], looks: {}, members: [],
                created_at: "2026-11-17T10:00:00Z" };
    saved.unshift(e);
    return route.fulfill({ status: 201, json: e });
  });
  return sent;
}

const tile = (dialog: ReturnType<Page["getByRole"]>, option: string) => dialog.getByRole("button", { name: option, exact: true });

test("locked traits stay through Randomize and a draft from a sentence, and saving draws the portrait", async ({ page }) => {
  const sent = await studio(page, { name: "Tomas Wren", style: "anime", traits: {
    presentation: "man", age: "elderly", hair: "shaved", hair_color: "", expression: "smiling", outfit: "naval uniform",
    accessory: "headphones" } });
  await page.goto("/characters");
  await page.getByRole("button", { name: "Design a character" }).click();
  const designer = page.getByRole("dialog", { name: "Design a character" });

  await tile(designer, "woman").click();
  await tile(designer, "braided").click();
  await designer.getByRole("button", { name: "Lock hair", exact: true }).click();
  await expect(designer.getByRole("button", { name: "Lock hair", exact: true })).toHaveAttribute("aria-pressed", "true");
  await designer.getByRole("button", { name: "Randomize" }).click();
  await expect(tile(designer, "braided")).toHaveAttribute("aria-pressed", "true");
  await expect(tile(designer, "woman")).toHaveAttribute("aria-pressed", "true"); // a locked trait keeps the presentation

  await designer.getByRole("textbox", { name: "Or a sentence" }).fill("an elderly man with headphones");
  await designer.getByRole("button", { name: "Fill the traits · free now, about $0.004 when live" }).click();
  // the man's own outfit isn't hers, and her locked hair stays; the expression is for the portrait, not the description
  await expect(designer.getByText("an elderly woman, braided hair, with headphones around the neck")).toBeVisible();
  await expect(designer.getByText("a smiling expression in its pictures")).toBeVisible();
  await expect(designer.getByLabel("Name")).toHaveValue("Tomas Wren");
  expect(sent.draft).toEqual([{ text: "an elderly man with headphones" }]);

  await designer.getByRole("button", { name: "Create character" }).click();
  await expect.poll(() => sent.pipelines?.length).toBe(1);
  expect(sent.elements).toEqual([{
    kind: "character", name: "Tomas Wren", description: "an elderly woman, braided hair, with headphones around the neck",
    images: [], attributes: { traits: { presentation: "woman", age: "elderly", hair: "braided", hair_color: "",
                                        expression: "smiling", outfit: "", accessory: "headphones", style: "anime" } },
  }]);
  expect(sent.pipelines[0]).toEqual({ recipe: "cinema_cast", confirm_cost_usd: null, input: {
    characters: [{ element_id: "e1", slug: "tomas-wren", style: "anime", expression: "smiling" }],
    image_model_id: "nano-banana-2" } });
});

test("a photo fills the traits and is kept as the first picture instead of a drawn portrait", async ({ page }) => {
  const sent = await studio(page, { name: "Kai Mendes", style: "anime", traits: CATALOG.starters[1].traits });
  await page.goto("/characters");
  await page.getByRole("button", { name: "Design a character" }).click();
  const designer = page.getByRole("dialog", { name: "Design a character" });
  await designer.getByRole("group", { name: "A photo" }).getByRole("button", { name: "From library" }).click();
  await page.getByRole("dialog", { name: "Choose from your library" }).getByRole("button", { name: "Rainy street" }).click();
  await expect(designer.getByRole("textbox", { name: "Or a sentence" })).toBeDisabled();

  await designer.getByRole("button", { name: "Fill the traits · free now, about $0.006 when live" }).click();
  await expect(tile(designer, "man")).toHaveAttribute("aria-pressed", "true");
  await expect(designer.getByRole("checkbox", { name: "Use this photo as the first picture" })).toBeChecked();
  expect(sent.draft).toEqual([{ asset_id: "street" }]);

  await designer.getByRole("button", { name: "Create character" }).click();
  await expect.poll(() => sent.elements?.length).toBe(1);
  expect(sent.elements[0].images).toEqual([{ asset_id: "street", view: "front" }]);
  await expect(page.getByText("Created @kai-mendes with your photo")).toBeVisible();
  expect(sent.pipelines).toBeUndefined();
});

test("starters: one added with its portrait, voice and picks, another opened in the designer", async ({ page }) => {
  const sent = await studio(page, {});
  await page.goto("/characters");
  await page.getByRole("button", { name: "Add starters" }).click();
  const starters = page.getByRole("dialog", { name: "Starters" });
  await expect(starters.getByRole("button", { name: "Add locations" })).toBeVisible();

  await starters.getByRole("button", { name: "Add Iris Vale" }).click();
  await expect(starters.getByRole("button", { name: "Iris Vale is added" })).toBeDisabled();
  expect(sent.elements).toEqual([{
    kind: "character", name: "Iris Vale",
    description: "an elderly woman, braided silver hair, wearing work overalls, with a wool scarf",
    attributes: { traits: { ...CATALOG.starters[0].traits, style: "photoreal" } },
    images: [{ asset_id: "portrait", view: "front" }],
    voice: { model_id: "eleven-v4", params: { voice_id: "pFZP5JQG7iQjIQuC4Bku" } },
  }]);

  await starters.getByRole("button", { name: "Open in designer: Kai Mendes" }).click();
  const designer = page.getByRole("dialog", { name: "Design a character" });
  await expect(designer.getByLabel("Name")).toHaveValue("Kai Mendes");
  await expect(designer.getByText("a young man, short curly black hair, wearing a naval uniform, "
                                  + "with headphones around the neck")).toBeVisible();
  await expect(designer.getByRole("button", { name: "Style: Anime" })).toBeVisible();
  await expect(designer.getByRole("group", { name: "Hair: short curly", exact: true })).toBeVisible(); // not its lock's name

  // another starter brings its own name: Kai's came with his picks, it isn't one you wrote
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Add starters" }).click();
  await starters.getByRole("button", { name: "Open in designer: Iris Vale" }).click();
  await expect(designer.getByLabel("Name")).toHaveValue("Iris Vale");
  await expect(tile(designer, "silver")).toHaveAttribute("aria-pressed", "true");
});
