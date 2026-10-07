import { expect, test } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8400";

test.beforeAll(async ({ request }) => {
  const health = await (await request.get(`${API}/health`)).json();
  expect(health.provider_mode, "e2e only runs against the mock provider").toBe("mock");
});

test("Faceless: suggested topic, edited script, approval, finished captioned episode", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/faceless");

  await page.getByRole("button", { name: "Suggest" }).click();
  await page.getByRole("button", { name: /^The keeper of the last lighthouse/ }).click();
  await expect(page.getByLabel("Topic")).toHaveValue("The keeper of the last lighthouse");

  await page.getByRole("radio", { name: "Poster Vector" }).click();
  await page.getByRole("switch", { name: "Thumbnail" }).click();
  await page.getByRole("switch", { name: "Music" }).click();
  await expect(page.getByText(/free on mock routes/)).toBeVisible();
  await page.getByRole("button", { name: "Make the episode" }).click();

  await expect(page).toHaveURL(/\/runs\//);
  const script = page.getByLabel("Script");
  await expect(script).toBeVisible();
  await script.fill("The keeper climbed the stairs. She lit the lamp one last time. Then the ships came home.");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("button", { name: "Save changes" })).toBeHidden();
  await page.getByRole("button", { name: "Use this script" }).click();

  await expect(page.getByText(/^Done, /)).toBeVisible({ timeout: 150_000 });
  await expect(page.getByText(/^\d+ blocks/)).toBeVisible();
  await expect(page.locator("main video").first()).toBeVisible();
  await expect(page.getByText("Burn in captions")).toBeVisible();
});
