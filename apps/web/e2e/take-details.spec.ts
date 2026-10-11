import { expect, test } from "@playwright/test";

test("take chips use the declared registry family, including unrelated ID prefixes", async ({ page }) => {
  const models = [
    { id: "infinitetalk", name: "InfiniteTalk", family: "wan" },
    { id: "wan-2.2", name: "Wan 2.2", family: "wan" },
  ].map((model) => ({ ...model, output_type: "video", modes: [], tags: [] }));
  const items = [...models, { id: "removed-model", name: "removed-model" }].map((model, i) => ({
    id: `family-take-${i}`,
    model_id: model.id,
    output_type: "video",
    mode: "t2v",
    status: "canceled",
    request: { prompt: "A cinematic shot of the coast", params: {}, media: [] },
    assets: [],
    routing: {},
    created_at: "2026-10-11T12:00:00Z",
  }));
  await page.route("**/registry", (route) => route.fulfill({ json: { models, provider_mode: "mock" } }));
  await page.route("**/generations?*", (route) => route.fulfill({ json: { items, total: items.length } }));
  await page.route("**/budget", (route) => route.fulfill({ json: { today_usd: 0 } }));
  await page.route("**/events*", (route) => route.abort());

  await page.goto("/create/video");
  const cards = page.getByRole("article");
  await expect(cards).toHaveCount(3);
  await expect(cards.nth(0).getByText("InfiniteTalk", { exact: true })).toBeVisible();
  await expect(cards.nth(1).getByText("Wan 2.2", { exact: true })).toBeVisible();
  const chipColors = await cards.locator("dl > div:nth-child(2)").evaluateAll((chips) =>
    chips.map((chip) => {
      const style = getComputedStyle(chip);
      return [style.color, style.backgroundColor, style.borderColor];
    }),
  );
  expect(chipColors[0]).toEqual(chipColors[1]);
  expect(chipColors[2]).not.toEqual(chipColors[0]);
});
