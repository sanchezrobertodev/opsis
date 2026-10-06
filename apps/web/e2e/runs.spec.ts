import { expect, test } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8400";

test.beforeAll(async ({ request }) => {
  const health = await (await request.get(`${API}/health`)).json();
  expect(health.provider_mode, "e2e only runs against the mock provider").toBe("mock");
});

test("a two-shot sequence continues from the last frame and joins the cut", async ({ page }) => {
  test.setTimeout(120_000);
  const tag = Date.now();
  await page.goto("/sequence");
  await page.getByLabel("Shot 1 prompt").fill(`e2e ${tag}: a lantern lit in a dark barn`);
  await page.getByLabel("Shot 2 prompt").fill("an owl lands on the beam above it");
  await expect(page.getByText(/2 takes, free/)).toBeVisible();
  await page.getByRole("button", { name: "Shoot the sequence" }).click();

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/);
  await expect(page.getByText(`Sequence: e2e ${tag}`)).toBeVisible();
  await expect(page.getByText(/^Done/)).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("Join the shots")).toBeVisible();
  await expect(page.locator("main video")).toBeVisible();
});

test("comparing models waits for a pick, then keeps it", async ({ page }) => {
  test.setTimeout(120_000);
  const prompt = `e2e compare ${Date.now()}: a paper crane unfolds`;
  await page.goto("/create/video");
  await page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: "Text to video" }).click();
  await page.getByPlaceholder(/Describe the shot/).fill(prompt);
  await page.getByRole("button", { name: "Compare with other models" }).click();
  await page.getByRole("button", { name: /^Seedance 2\.5/ }).click();
  await page.getByRole("button", { name: "Compare 2 models" }).click();

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/);
  await expect(page.getByText("Which one do you want to keep?")).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: /^Keep / }).first().click();
  await expect(page.getByText(/^Done/)).toBeVisible({ timeout: 30_000 });
});
