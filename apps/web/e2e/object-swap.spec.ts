import { expect, test } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8400";

test.beforeAll(async ({ request }) => {
  const health = await (await request.get(`${API}/health`)).json();
  expect(health.provider_mode, "e2e only runs against the mock provider").toBe("mock");
});

test("Object swap: click the object on a frame, find it, swap it, keep the rest as filmed", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/swap");

  const clip = page.getByRole("group", { name: "Clip" });
  await clip.getByRole("button", { name: "From library" }).click();
  const dialog = page.getByRole("dialog", { name: "Choose from your library" });
  await dialog.getByRole("listitem").first().getByRole("button").click();
  await expect(dialog).toBeHidden();

  await page.getByRole("button", { name: "Select on this frame" }).click();
  const frame = page.getByRole("img", { name: "Clicks on the frame" });
  await expect(page.getByAltText("The frame to select on")).toHaveJSProperty("complete", true);
  await frame.scrollIntoViewIfNeeded();
  const box = (await frame.boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await expect(page.getByText(/^Click more of the object/)).toBeVisible();
  await page.keyboard.down("Alt");  // Alt-click marks what isn't the object
  await page.mouse.click(box.x + box.width * 0.9, box.y + box.height * 0.1);
  await page.keyboard.up("Alt");
  await page.getByRole("button", { name: "Find the object" }).click();
  await expect(page.getByText(/^Highlighted is what will change/)).toBeVisible({ timeout: 60_000 });

  await page.getByLabel("Replace it with").fill("Replace it with a small potted cactus");
  await expect(page.getByText(/free on mock routes/)).toBeVisible();
  await page.getByRole("button", { name: "Swap it" }).click();
  await expect(page).toHaveURL(/\/runs\//);
  await expect(page.getByText(/^Done, /)).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(/keep the rest as filmed/)).toBeVisible();
  await expect(page.locator("main video").first()).toBeVisible();
});
