import { expect, test } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8400";

test.beforeAll(async ({ request }) => {
  const health = await (await request.get(`${API}/health`)).json();
  expect(health.provider_mode, "e2e only runs against the mock provider").toBe("mock");
});

test("3D blocking: a new scene is edited, saved, rendered and made real from its angles", async ({ page }) => {
  test.setTimeout(150_000);
  await page.goto("/jutsu3d");
  await page.getByRole("button", { name: "New scene" }).click();
  await page.getByRole("dialog").getByRole("textbox").fill(`e2e rooftop ${Date.now()}`);
  await page.getByRole("dialog").getByRole("button", { name: /Create|Start/ }).click();
  await expect(page).toHaveURL(/\/jutsu3d\/[0-9a-f-]{36}$/);

  // the starter scene: a camera, a figure and the sun
  const outliner = page.getByRole("region", { name: "Outliner" });
  await expect(outliner.getByRole("button", { name: /^Camera/ })).toBeVisible();
  await expect(outliner.getByRole("button", { name: /^Figure/ })).toBeVisible();
  await page.getByRole("button", { name: "+ Box" }).click();
  await expect(outliner.getByRole("button", { name: /^Box/ })).toBeVisible();
  await expect(page.getByText(/Saved, version 2/)).toBeVisible({ timeout: 10_000 });

  // Make it real: the angle at the camera's key is rendered, redrawn and animated (all on the mock)
  await page.keyboard.press("Escape"); // nothing selected: the scene's own panels show
  const real = page.getByRole("region", { name: "Make it real" });
  await real.getByRole("button", { name: "Render the angles" }).click();
  await expect(real.getByRole("list", { name: "Inputs" }).getByRole("img")).toHaveCount(1, { timeout: 30_000 });
  await real.getByLabel("What it looks like").fill("A rain-soaked rooftop at night, a courier in a yellow jacket");
  await expect(real.getByText(/^Free mock run/)).toBeVisible();
  await real.getByRole("button", { name: "Make it real" }).click();
  const made = real.getByRole("list", { name: "Made from this scene" });
  await expect(made.locator("video")).toHaveCount(1, { timeout: 90_000 });
});
