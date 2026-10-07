import { expect, test } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8400";

test.beforeAll(async ({ request }) => {
  const health = await (await request.get(`${API}/health`)).json();
  expect(health.provider_mode, "e2e only runs against the mock provider").toBe("mock");
});

test("Worlds: a draft world is made, explored, saved as a location and filmed in 3D blocking", async ({ page }) => {
  test.setTimeout(150_000);
  const place = `e2e harbour ${Date.now()}`;
  await page.goto("/worlds");
  await page.getByLabel("Describe it").fill(`${place}, fishing boats at the quay, a lighthouse`);
  await page.getByRole("radio", { name: "Draft" }).click();
  await expect(page.getByText(/^Free mock run; live it would cost about \$0\.184/)).toBeVisible();
  await page.getByRole("button", { name: "Make the world" }).click();

  // when it's made it opens; its 360 panorama comes with it
  await expect(page.getByRole("heading", { level: 1, name: new RegExp(place) })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("region", { name: "Panoramas" }).getByRole("button", { name: new RegExp(place) }))
    .toBeVisible();

  await page.getByRole("button", { name: "Grab this view" }).click();
  await expect(page.getByRole("list", { name: "Grabbed views" }).getByRole("img")).toHaveCount(1, { timeout: 15_000 });

  await page.getByRole("button", { name: "Save as a location" }).click();
  const dialog = page.getByRole("dialog", { name: "Save as a location" });
  await expect(dialog.getByLabel("Name")).toHaveValue(place);
  await dialog.getByRole("button", { name: "Save location" }).click();
  await expect(page.getByText(/Mention it as @/)).toBeVisible();

  await page.getByRole("button", { name: "Film it in 3D blocking" }).click();
  await expect(page).toHaveURL(/\/jutsu3d\/[0-9a-f-]{36}$/, { timeout: 15_000 });
  await expect(page.getByRole("region", { name: "Outliner" }).getByRole("button", { name: new RegExp(`^${place}`) }))
    .toBeVisible();
});
