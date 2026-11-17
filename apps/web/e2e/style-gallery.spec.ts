import { expect, test } from "@playwright/test";

// Every call is answered here, so this runs without the API.
const MODEL = {
  id: "still-maker", name: "Still Maker", kind: "model", family: "flux", vendor: "test", description: "Stills.",
  output_type: "image", status: "active", license_tier: "open_permissive", tags: [],
  prompt: { max_chars: 2000, dialect: "flux", supports_negative: false, dialogue: false },
  aspect_ratios: [], duration: null, parameters: [], rules: [], elements: null, routes: [], virtual: null,
  modes: [{ id: "t2i", label: "Text to image", ops: ["t2i"], prompt: "required", medias: [] }],
};
const SUMMARY = { ...MODEL, modes: [{ id: "t2i", label: "Text to image", ops: ["t2i"] }], runnable: true };
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
const ESTIMATE = { model_id: "still-maker", mode: "t2i", chosen_route_id: MOCK.route_id, estimate_usd: 0,
  live_reference: null, candidates: [MOCK], warnings: [], params: {} };

test("Enter in the style search doesn't start a take, and the picked style goes with the next one", async ({ page }) => {
  await page.route("http://127.0.0.1:8400/**", (route) => route.fulfill({ status: 503, json: { detail: "off in this test" } }));
  await page.route("**/events*", (route) => route.abort());
  await page.route("**/registry", (route) => route.fulfill({ json: { hash: "h", models: [SUMMARY], provider_mode: "mock", warnings: [] } }));
  await page.route("**/registry/models/still-maker", (route) => route.fulfill({ json: MODEL }));
  await page.route("**/registry/estimate", (route) => route.fulfill({ json: ESTIMATE }));
  await page.route("**/director/library", (route) => route.fulfill({ json: LIBRARY }));
  await page.route("**/generations?*", (route) => route.fulfill({ json: { items: [], next_cursor: null } }));
  const sent: Record<string, unknown>[] = [];
  await page.route("**/generations", (route) => {
    sent.push(route.request().postDataJSON());
    return route.fulfill({ status: 422, json: { errors: ["not in this test"] } });
  });

  await page.goto("/create/image");
  await page.getByPlaceholder(/Describe the shot/).fill("A lighthouse at dusk");
  const generate = page.getByRole("button", { name: "Generate", exact: true });
  await expect(generate).toBeEnabled(); // so Enter would submit the form, were it allowed to

  await page.getByRole("button", { name: "Style: No style" }).click();
  const search = page.getByRole("textbox", { name: "Search styles" });
  await search.fill("neon");
  await search.press("Enter");
  await expect(page.getByRole("dialog", { name: "Picture style" })).toBeVisible(); // still choosing
  await expect(page.getByRole("button", { name: /^Watercolor/ })).toHaveCount(0); // the search filtered
  await page.waitForTimeout(500);
  expect(sent).toEqual([]); // and nothing started

  await page.getByRole("button", { name: /^Neon noir/ }).click();
  await expect(page.getByRole("button", { name: "Style: Neon noir" })).toBeVisible();
  await generate.click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0].direction).toEqual({ style: "neon-noir" });
});
