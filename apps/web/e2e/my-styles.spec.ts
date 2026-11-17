import path from "node:path";
import { expect, type Page, test } from "@playwright/test";

// Every call is answered here, so this runs without the API (like style-gallery.spec.ts).
const PUBLIC = path.resolve(__dirname, "../public");
const SLOT = { role: "image_references", kinds: ["image"], label: "References", required: false, min: 0, max: 2, auto: false };
const MODE = { id: "i2i", label: "Edit a picture", ops: ["i2i"], prompt: "required" };
const MODEL = {
  id: "still-maker", name: "Still Maker", kind: "model", family: "flux", vendor: "test", description: "Stills.",
  output_type: "image", status: "active", license_tier: "open_permissive", tags: [],
  prompt: { max_chars: 2000, dialect: "flux", supports_negative: false, dialogue: false },
  aspect_ratios: [], duration: null, parameters: [], rules: [], elements: null, routes: [], virtual: null,
  modes: [{ ...MODE, medias: [SLOT] }],
};
const SUMMARY = { ...MODEL, modes: [{ id: MODE.id, label: MODE.label, ops: MODE.ops }], runnable: true };
const style = (id: string, label: string, category: string, summary: string) =>
  ({ id, label, category, summary, prompt: `${label.toLowerCase()} picture`, dialects: {}, swatch: [], photographic: false });
const LOOKS = ["moves", "shot_sizes", "angles", "lenses", "focal_lengths", "apertures", "camera_bodies", "eras", "lighting",
  "light_sources", "light_positions", "light_colors", "palettes", "genres", "pacing", "emotions"];
const LIBRARY = {
  ...Object.fromEntries(LOOKS.map((s) => [s, []])),
  styles: [style("neon-noir", "Neon noir", "photo", "Rain and signs."), style("watercolor", "Watercolor", "painting", "Soft washes.")],
};
const MOCK = { route_id: "still-maker@mock", provider: "mock", provider_kind: "mock", eligible: true, reason: null,
  estimate_usd: 0, pricing_basis: null, is_mock: true };
const ESTIMATE = { model_id: "still-maker", mode: "i2i", chosen_route_id: MOCK.route_id, estimate_usd: 0,
  live_reference: null, candidates: [MOCK], warnings: [], params: {} };
const asset = (id: string, title: string, width: number, height: number) => ({
  id, kind: "image", status: "ready", source: "upload", mime: "image/webp", url: `/media/${id}.webp`, thumb_url: null,
  poster_url: null, filmstrip_url: null, filmstrip_frames: null, proxy_url: null, width, height, duration_ms: null,
  fps: null, has_audio: null, size_bytes: 1000, title, tags: [], favorite: false, generation_id: null,
  output_index: null, metadata: {}, created_at: "2026-11-17T10:00:00Z",
});
const STREET = asset("street", "Rainy street", 256, 256);
const PASTED = asset("pasted", "Pasted picture", 256, 256);
const LOOK = "1970s colour film photograph, warm faded tones, soft grain, gentle halation, lifted blacks";

/** The API this page talks to, answered in the browser; `sent` keeps what was posted, by path. */
async function studio(page: Page) {
  const sent: Record<string, Record<string, unknown>[]> = {};
  const keep = (path: string, body: Record<string, unknown>) => (sent[path] ??= []).push(body);
  const saved: Record<string, unknown>[] = [];
  await page.route("http://127.0.0.1:8400/**", (route) => route.fulfill({ status: 503, json: { detail: "off in this test" } }));
  await page.route("**/events*", (route) => route.abort());
  await page.route("**/registry", (route) => route.fulfill({ json: { hash: "h", models: [SUMMARY], provider_mode: "mock", warnings: [] } }));
  await page.route("**/registry/models/still-maker", (route) => route.fulfill({ json: MODEL }));
  await page.route("**/registry/estimate", (route) => route.fulfill({ json: ESTIMATE }));
  await page.route("**/director/library", (route) => route.fulfill({ json: LIBRARY }));
  await page.route("**/llm/calls", (route) => route.fulfill({ json: { mode: "mock", verified: "2026-11-01",
    calls: { "style.describe": { model: "claude-sonnet-5-5", name: "Claude Sonnet 5.5", usd: 0.012 } } } }));
  await page.route("**/assets?*", (route) => route.fulfill({ json: { items: [STREET], next_cursor: null } }));
  // a wide picture (640 x 360) for "wide", else a square one
  await page.route("**/media/*", (route) => route.fulfill({ path: path.join(PUBLIC, route.request().url().includes("wide")
    ? "faceless/colorful-3d.webp" : "styles/watercolor-person.webp") }));
  await page.route("**/uploads", (route) => route.fulfill({ json: PASTED }));
  await page.route("**/generations?*", (route) => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.route("**/generations", (route) => {
    keep("generations", route.request().postDataJSON());
    return route.fulfill({ status: 422, json: { errors: ["not in this test"] } });
  });
  await page.route("**/llm/describe-style", (route) => {
    keep("describe", route.request().postDataJSON());
    return route.fulfill({ json: { name: "Faded film", category: "retro", summary: "Warm, grainy colour like an old print.",
                                   look: LOOK, model: "claude-sonnet-5-5", mock: true, cost_usd: 0 } });
  });
  await page.route(/\/elements(\?.*)?$/, (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: saved });
    const body = route.request().postDataJSON();
    keep("elements", body);
    const e = { id: "e1", kind: "style", name: body.name, slug: "faded-film", handle: "@faded-film",
                description: body.description, attributes: body.attributes, voice: null, lora: null, cover: STREET,
                images: [{ asset: STREET, view: "style", is_primary: true }], created_at: "2026-11-17T10:00:00Z" };
    saved.unshift(e);
    return route.fulfill({ status: 201, json: e });
  });
  return sent;
}

test("a new style from a picture: Claude describes it, it's saved and picked, and the next take names it", async ({ page }) => {
  const sent = await studio(page);
  await page.goto("/create/image");
  await page.getByPlaceholder(/Describe the shot/).fill("A lighthouse at dusk");

  await page.getByRole("button", { name: "Style: No style" }).click();
  const gallery = page.getByRole("dialog", { name: "Picture style" });
  await gallery.getByRole("button", { name: "My styles" }).click();
  await gallery.getByRole("button", { name: /^New style/ }).click();
  const form = page.getByRole("dialog", { name: "New style" });

  // pasted while typing in the dialog: it goes to the dialog's pictures, not the form behind it
  await form.getByLabel("Name").click();
  await page.evaluate(() => {
    const files = new DataTransfer();
    files.items.add(new File([new Uint8Array([137, 80, 78, 71])], "look.png", { type: "image/png" }));
    document.activeElement!.dispatchEvent(new ClipboardEvent("paste", { clipboardData: files, bubbles: true }));
  });
  await expect(form.getByText("1 of 4")).toBeVisible();
  await expect(page.getByRole("group", { name: "References" }).getByText("0 of 2")).toBeVisible();

  await form.getByRole("button", { name: "From library" }).click();
  const library = page.getByRole("dialog", { name: "Choose from your library" });
  await library.getByRole("button", { name: "Rainy street" }).click();
  await library.getByRole("button", { name: /^Done/ }).click();
  await expect(form.getByText("2 of 4")).toBeVisible();

  await form.getByLabel("Name").fill("Old print"); // yours: Claude's answer leaves it
  await form.getByRole("button", { name: /^Describe with Claude · free now/ }).click();
  await expect(form.getByLabel(/^Look/)).toHaveValue(LOOK);
  await expect(form.getByLabel("Name")).toHaveValue("Old print");
  await expect(form.getByRole("radio", { name: "Print & retro" })).toHaveAttribute("aria-checked", "true");
  expect(sent.describe).toEqual([{ asset_ids: ["pasted", "street"] }]);

  // closed by mistake (Escape, a stray click on the backdrop): it opens again as it was
  await form.getByLabel(/^Look/).press("Escape");
  await expect(form).toHaveCount(0);
  await gallery.getByRole("button", { name: /^New style/ }).click();
  await expect(form.getByLabel(/^Look/)).toHaveValue(LOOK);
  await expect(form.getByText("2 of 4")).toBeVisible();

  await form.getByRole("button", { name: "Save style" }).click();
  await expect(page.getByRole("button", { name: "Style: Old print" })).toBeFocused(); // both dialogs closed, in order
  expect(sent.elements).toEqual([{
    kind: "style", name: "Old print", description: LOOK,
    images: [{ asset_id: "pasted", view: "style" }, { asset_id: "street", view: "style" }],
    attributes: { style: { category: "retro", summary: "Warm, grainy colour like an old print." } },
  }]);

  await page.getByRole("button", { name: "Generate", exact: true }).click();
  await expect.poll(() => sent.generations?.length).toBe(1);
  expect(sent.generations[0].direction).toEqual({ style: "@faded-film" });
});

test("a take saved as a style: Claude describes its picture in place of the prompt", async ({ page }) => {
  const sent = await studio(page);
  const take = { id: "t1", model_id: "still-maker", output_type: "image", mode: "i2i", status: "succeeded",
                 assets: [STREET], routing: {}, created_at: "2026-11-17T10:00:00Z",
                 request: { prompt: "A lighthouse at dusk", params: {}, media: [] } };
  await page.route("**/generations?*", (route) => route.fulfill({ json: { items: [take], next_cursor: null } }));

  await page.goto("/create/image");
  await page.getByRole("button", { name: "Save to Characters" }).click();
  const dialog = page.getByRole("dialog", { name: "Save to Characters" });
  await expect(dialog.getByLabel("Description")).toHaveValue(""); // a take's prompt names more than the subject
  await dialog.getByRole("radio", { name: "Style" }).click();
  await expect(dialog.getByLabel("Look")).toHaveValue(""); // the prompt's lighthouse isn't a look
  await expect(dialog.getByLabel("This image shows")).toHaveCount(0); // a style's picture shows its look
  await dialog.getByRole("button", { name: /^Describe with Claude/ }).click();
  await expect(dialog.getByLabel("Look")).toHaveValue(LOOK);
  await expect(dialog.getByLabel("Name")).toHaveValue("Faded film");
  expect(sent.describe).toEqual([{ asset_ids: ["street"] }]);

  await dialog.getByRole("button", { name: "Save style" }).click();
  await expect.poll(() => sent.elements?.length).toBe(1);
  expect(sent.elements[0]).toEqual({
    kind: "style", name: "Faded film", description: LOOK, images: [{ asset_id: "street", view: "style" }],
    attributes: { style: { category: "retro", summary: "Warm, grainy colour like an old print." } },
  });
});

test("several styles: picked in the gallery, priced, started as one comparison with a take per style", async ({ page }) => {
  const sent = await studio(page);
  const take = (n: number, label: string, a: ReturnType<typeof asset>) => ({
    key: `take[${n}:still-maker]`, kind: "generate", label, status: "succeeded", depends_on: [], error: null, output: {},
    generation_id: `g${n}`, held: false, assets: [a], started_at: null, finished_at: null });
  const SQUARE = asset("square", "Watercolor take", 256, 256);
  const WIDE = asset("wide", "Neon noir take", 640, 360);
  const RUN = {
    id: "run-1", recipe: "compare", recipe_name: "Compare models", title: "Styles: A lighthouse at dusk",
    status: "waiting", error: null, estimate_usd: 0, cap_usd: 5, created_at: "2026-11-17T10:00:00Z", finished_at: null,
    output_asset: null, steps_done: 2, steps_total: 3, spent_usd: 0, input: {}, output: {},
    steps: [take(0, "Neon noir", WIDE), take(1, "Watercolor", SQUARE), {
      key: "pick", kind: "checkpoint", label: "Pick the best take", status: "waiting", depends_on: [], error: null,
      output: {}, generation_id: null, held: false, assets: [], started_at: null, finished_at: null,
      options: [{ value: "wide", label: "Neon noir", asset: WIDE }, { value: "square", label: "Watercolor", asset: SQUARE }] }],
  };
  await page.route("**/pipelines/estimate", (route) => route.fulfill({ json: {
    estimate_usd: 0, live_estimate_usd: 0.08, generations: 2, claude_usd: 0, claude_live_usd: 0 } }));
  await page.route("**/pipelines", (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: [] });
    sent.pipelines = [route.request().postDataJSON()];
    return route.fulfill({ status: 201, json: RUN });
  });
  await page.route("**/pipelines/run-1", (route) => route.fulfill({ json: RUN }));

  await page.goto("/create/image");
  await page.getByPlaceholder(/Describe the shot/).fill("A lighthouse at dusk");
  await page.getByRole("button", { name: "Style: No style" }).click();
  await page.getByRole("button", { name: /^Neon noir/ }).click();

  await page.getByRole("button", { name: "Try in several styles" }).click();
  const dialog = page.getByRole("dialog", { name: "Try in several styles" });
  await expect(dialog.getByRole("button", { name: /^Neon noir/ })).toHaveAttribute("aria-pressed", "true"); // the current one
  await expect(dialog.getByRole("button", { name: "Pick at least 2 styles" })).toBeDisabled();
  await dialog.getByRole("button", { name: /^Watercolor/ }).click();
  await expect(dialog.getByText("2 takes, about $0.08 when live")).toBeVisible();
  await dialog.getByRole("button", { name: "Try 2 styles · $0.08" }).click();

  await expect(page).toHaveURL(/\/runs\/run-1$/);
  const entry = (s: string, label: string) => ({ model_id: "still-maker", mode: "i2i", params: {}, direction: { style: s }, label });
  expect(sent.pipelines).toEqual([{
    recipe: "compare",
    title: "Styles: A lighthouse at dusk",
    input: { prompt: "A lighthouse at dusk", entries: [entry("neon-noir", "Neon noir"), entry("watercolor", "Watercolor")],
             media: [], direction: null, ops: ["i2i"], pick: true },
    confirm_cost_usd: 0,
  }]);

  // each take at its own shape, named for its style (the pick's pictures come after the steps' thumbnails)
  await expect(page.getByRole("button", { name: "Keep Watercolor" })).toBeVisible();
  const shape = (title: string) => page.getByRole("button", { name: `See ${title} full size` }).last().locator("img")
    .evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth ? img.width / img.height : 0);
  await expect.poll(() => shape("Watercolor take")).toBeCloseTo(1, 1);
  await expect.poll(() => shape("Neon noir take")).toBeCloseTo(640 / 360, 1);
});
