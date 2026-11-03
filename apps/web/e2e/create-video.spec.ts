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
  await expect(take.getByText("4.0s", { exact: true })).toBeVisible();
  await expect(take.getByText("free", { exact: true })).toBeVisible();

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

test("a pinned provider reaches the estimate and says why it can't run in mock mode; Auto runs on the mock", async ({ page }) => {
  // a pin kept from Mixed or Live, as the form remembers it
  await page.addInitScript(() => {
    const d = { prompt: "e2e: a fox in the snow", negativePrompt: "", params: {}, media: {}, takes: 1, direction: {} };
    localStorage.setItem("studio-drafts", JSON.stringify({ version: 3, state: {
      video: { ...d, modelId: "seedance-2.5", routes: { "seedance-2.5": "seedance-2.5@fal" } }, image: d, edit: d, motion: d,
    } }));
  });
  const pinnedEstimate = page.waitForRequest((r) => r.url().endsWith("/registry/estimate")
    && r.postDataJSON()?.route_policy === "pinned:seedance-2.5@fal");
  await page.goto("/create/video");
  await pinnedEstimate;

  const provider = page.getByLabel("Provider", { exact: true });
  await expect(provider).toHaveValue("seedance-2.5@fal");
  await expect(provider.locator("option", { hasText: "fal.ai" })).toBeDisabled();
  await expect(provider.locator("option", { hasText: "fal.ai" })).toContainText("PROVIDER_MODE=mock");
  await expect(page.getByText("fal.ai can't run this", { exact: false })).toBeVisible();
  const generate = page.getByRole("button", { name: "Generate", exact: true });
  await expect(generate).toBeDisabled();

  await provider.selectOption("");
  await expect(page.getByText(/Free mock run/)).toBeVisible();
  const created = page.waitForRequest((r) => r.url().endsWith("/generations") && r.method() === "POST");
  await generate.click();
  expect((await created).postDataJSON().route_policy).toBe("cheapest_healthy");
});

test("every studio has shipped, so none is shown as coming soon", async ({ page }) => {
  const soon = await page.goto("/soon/worlds");
  expect(soon?.status()).toBe(404);
  await page.goto("/create/video");
  await expect(page.getByRole("link", { name: "Worlds" })).toHaveAttribute("href", "/worlds");
});
