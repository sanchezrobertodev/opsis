import path from "node:path";
import { expect, test } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8400";
const FIXTURES = path.resolve(__dirname, "../../../fixtures");

test.beforeAll(async ({ request }) => {
  const health = await (await request.get(`${API}/health`)).json();
  expect(health.provider_mode, "e2e only runs against the mock provider").toBe("mock");
});

test("Lipsync: a picture speaks a script, compared on two models, then side by side with the original", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/lipsync");

  const who = page.getByRole("group", { name: "Who speaks: a picture or a clip" });
  await who.locator('input[type="file"]').setInputFiles(path.join(FIXTURES, "images", "keyframe.png"));
  await expect(who.getByRole("img")).toBeVisible();

  await page.getByLabel("What they say").fill("The lamp has burned every night for forty years. Tonight it goes dark.");
  await expect(page.getByRole("combobox", { name: "Voice" })).toBeVisible();
  // the first talking-portrait model is picked; add a second to compare
  await expect(page.getByRole("button", { name: /^InfiniteTalk/ })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: /^Kling Avatar 2.0/ }).click();
  await expect(page.getByText(/^Free mock run/)).toBeVisible();
  await page.getByRole("button", { name: "Make it speak" }).click();

  await expect(page.getByRole("heading", { name: "Pick the take you like" })).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Keep InfiniteTalk" }).click();
  await expect(page.getByLabel("The talking take")).toBeVisible({ timeout: 30_000 });

  await page.getByRole("radio", { name: "Side by side" }).click();
  await expect(page.getByAltText("The original picture")).toBeVisible();
  await expect(page.getByLabel("The talking take")).toBeVisible();
});

test("Lipsync: a clip re-synced to a recording offers only lipsync models", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/lipsync");
  await page.getByRole("group", { name: "Who speaks: a picture or a clip" }).locator('input[type="file"]')
    .setInputFiles(path.join(FIXTURES, "videos", "clip_3s.mp4"));
  await expect(page.getByRole("button", { name: /^Sync lipsync-3/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^VEED Fabric/ })).toHaveCount(0);

  await page.getByRole("radio", { name: "Use a recording" }).click();
  await page.getByRole("group", { name: "Recorded speech" }).locator('input[type="file"]')
    .setInputFiles(path.join(FIXTURES, "audio", "line.mp3"));
  await page.getByRole("button", { name: "Make it speak" }).click();
  await expect(page.getByLabel("The talking take")).toBeVisible({ timeout: 90_000 });
});
