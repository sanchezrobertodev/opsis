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

test("another take keeps the take's direction; reusing its prompt keeps what the form can show", async ({ page }) => {
  const models = [
    { id: "kling-x", name: "Kling X", family: "kling", output_type: "video", modes: [], tags: [] },
    { id: "still-x", name: "Still X", family: "flux", output_type: "image", modes: [], tags: [] },
  ];
  const lines = [{ who: "@nova", text: "Over here!" }];
  const take = (id: string, output_type: string, request: Record<string, unknown>) => ({
    id, model_id: request.model_id, output_type, mode: request.mode, status: "canceled", assets: [], routing: {},
    created_at: "2026-11-17T12:00:00Z", request: { params: {}, media: [], ...request },
  });
  const items = [
    take("styled", "video", { model_id: "kling-x", mode: "t2v", prompt: "A chase", compiled_prompt: "A chase, anime",
                              direction: { style: "anime", genre: "thriller", dialogue: lines } }),
    // a multi-shot take: its mode takes no prompt, and its shots were stored compiled with the direction
    take("shots", "video", { model_id: "kling-x", mode: "multi_shot", prompt: "", compiled_prompt: "",
                             params: { shots: [{ prompt: "A chase, thriller", duration: 3 }] }, direction: { genre: "thriller" } }),
    take("still", "image", { model_id: "still-x", mode: "t2i", prompt: "A portrait", compiled_prompt: "A portrait. Anime.",
                             direction: { style: "anime", dialogue: lines } }),
  ];
  await page.route("**/registry", (route) => route.fulfill({ json: { models, provider_mode: "mock" } }));
  await page.route("**/generations?*", (route) => route.fulfill({ json: { items, total: items.length } }));
  await page.route("**/budget", (route) => route.fulfill({ json: { today_usd: 0 } }));
  await page.route("**/events*", (route) => route.abort());
  const sent: Record<string, unknown>[] = [];
  await page.route("**/generations", (route) => {
    sent.push(route.request().postDataJSON());
    return route.fulfill({ status: 422, json: { errors: ["not in this test"] } });
  });

  await page.goto("/create/video");
  const cards = page.getByRole("article");
  await expect(cards).toHaveCount(3);
  for (const i of [0, 1]) await cards.nth(i).getByRole("button", { name: "Generate another take with the same settings" }).click();
  await expect.poll(() => sent.length).toBe(2);
  expect(sent[0].direction).toEqual({ style: "anime", genre: "thriller", dialogue: lines }); // the same settings
  expect(sent[1].direction).toBeNull(); // not compiled into its shots a second time

  await cards.nth(0).getByRole("button", { name: "Reuse this prompt" }).click();
  await cards.nth(2).getByRole("button", { name: "Reuse this prompt" }).click();
  const drafts = await page.evaluate(() => JSON.parse(localStorage.getItem("studio-drafts") ?? "{}").state);
  expect(drafts.video.direction).toEqual({ genre: "thriller" }); // Create video shows no style or lines to clear
  expect(drafts.image.direction).toEqual({ style: "anime" }); // Create image has the style picker
});
