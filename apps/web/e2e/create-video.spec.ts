import { expect, type Page, test } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8400";

test.beforeAll(async ({ request }) => {
  const health = await request.get(`${API}/health`);
  expect(health.ok(), "API must be running").toBeTruthy();
  expect((await health.json()).provider_mode, "e2e only runs against the mock provider").toBe("mock");
});

async function generate(page: Page, prompt: string, options: { duration?: string } = {}) {
  await page.goto("/create/video");
  await page.getByRole("button", { name: /Choose a model|Veo|Kling|Seedance/ }).first().waitFor();
  await page.getByPlaceholder(/Describe the shot/).fill(prompt);
  if (options.duration) {
    await page.getByRole("radiogroup", { name: "Duration (s)" }).getByRole("radio", { name: options.duration }).click();
  }
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  // the new take is the first card in the sheet
  return page.getByRole("article").filter({ hasText: prompt }).first();
}

test("a mock text-to-video take renders as a slate, then plays, then appears in the library", async ({ page }) => {
  const prompt = `e2e: harbor at dawn, gulls overhead ${Date.now()}`;
  const take = await generate(page, prompt, { duration: "4" });

  // slate while it renders
  await expect(take.getByText(/provider's queue|Rendering|Waiting|Sending|Saving/)).toBeVisible();

  // becomes a playable video with the requested length
  const video = take.locator("video");
  await expect(video).toBeVisible();
  await expect(take.getByText(/4\.0s, free/)).toBeVisible();

  await page.goto("/library");
  await expect(page.getByRole("button", { name: new RegExp(prompt.slice(0, 30)) }).first()).toBeVisible();
});

test("a moderation failure explains itself instead of failing silently", async ({ page }) => {
  const prompt = `e2e: blocked [[mock:fail=moderation]] ${Date.now()}`;
  const take = await generate(page, prompt);
  await expect(take.getByText("Blocked by the provider's content rules")).toBeVisible();
});

test("a running take can be canceled", async ({ page }) => {
  const prompt = `e2e: long take [[mock:slow]] ${Date.now()}`;
  const take = await generate(page, prompt);
  await take.getByRole("button", { name: "Cancel this take" }).click();
  await expect(take.getByText("Canceled")).toBeVisible();
});

test("roadmap studios explain what they will do", async ({ page }) => {
  await page.goto("/soon/faceless");
  await expect(page.getByRole("heading", { level: 1, name: "Faceless studio" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Arrives in phase 4" })).toBeVisible();
  await expect(page.getByText(/narrated video/)).toBeVisible();
});
