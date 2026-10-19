import { expect, test } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8400";

test.beforeAll(async ({ request }) => {
  const health = await (await request.get(`${API}/health`)).json();
  expect(health.provider_mode, "e2e only runs against the mock provider").toBe("mock");
  expect(health.llm_mode, "e2e never calls Claude").toBe("mock");
});

test("@mentions autocomplete saved characters and the preview describes them", async ({ page, request }) => {
  const name = `E2E Wren ${Date.now()}`;
  const created = await request.post(`${API}/elements`, {
    data: { kind: "character", name, description: "a girl in a green cape", images: [] },
  });
  expect(created.ok()).toBeTruthy();
  const element = await created.json();
  try {
    await page.goto("/create/video");
    const prompt = page.getByPlaceholder(/Describe the shot/);
    await prompt.fill("");
    await prompt.pressSequentially(`Wide shot, @${element.slug.slice(0, 8)}`);
    const option = page.getByRole("option", { name: new RegExp(name) });
    await expect(option).toBeVisible();
    await prompt.press("Enter");
    await expect(prompt).toHaveValue(`Wide shot, @${element.slug} `);

    await page.getByRole("button", { name: "See the prompt the model receives" }).click();
    await expect(page.getByText(`${name}, a girl in a green cape`).first()).toBeVisible();
  } finally {
    await request.delete(`${API}/elements/${element.id}`);
  }
});

test("a preset fills in a blank and starts a normal take", async ({ page }) => {
  const place = `a misty fjord ${Date.now()}`;
  await page.goto("/templates");
  await page.getByRole("button", { name: /FPV dive/ }).click();
  await page.getByPlaceholder(/canyon river/).fill(place);
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  await expect(page).toHaveURL(/\/create\/video$/);
  await expect(page.getByRole("article").filter({ hasText: place }).first()).toBeVisible();
});

test("camera moves picked in Motion control land in Create video", async ({ page }) => {
  await page.goto("/motion");
  await page.getByRole("button", { name: /^Bullet time/ }).click();
  await page.getByRole("button", { name: /^Whip pan/ }).click();
  await page.getByRole("button", { name: "Shoot this in Create video" }).click();
  await expect(page).toHaveURL(/\/create\/video$/);
  await expect(page.getByText("Bullet time").first()).toBeVisible();
  await expect(page.getByText("Whip pan").first()).toBeVisible();
});

test("Enhance rewrites the prompt and Undo puts it back", async ({ page }) => {
  await page.goto("/create/video");
  const prompt = page.getByPlaceholder(/Describe the shot/);
  await prompt.fill("a lighthouse keeper climbs the stairs");
  await page.getByRole("button", { name: "Enhance" }).click();
  await expect(prompt).toHaveValue(/climbs the stairs, natural motion/);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(prompt).toHaveValue("a lighthouse keeper climbs the stairs");
});
