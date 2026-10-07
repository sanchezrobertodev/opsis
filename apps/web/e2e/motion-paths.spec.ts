import { expect, test } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8400";

test.beforeAll(async ({ request }) => {
  const health = await (await request.get(`${API}/health`)).json();
  expect(health.provider_mode, "e2e only runs against the mock provider").toBe("mock");
});

test("Wan-Move: draw a path and a pin on the start frame, then generate", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/create/video");
  await page.getByRole("button", { name: /Choose a model|Veo|Kling|Seedance|Wan/ }).first().click();
  await page.getByRole("dialog", { name: "Choose a model" }).getByRole("button", { name: /^Wan-Move/ }).click();

  const frameSlot = page.getByRole("group", { name: "Start frame" });
  if ((await frameSlot.getByRole("button", { name: /^Remove / }).count()) === 0) {
    await frameSlot.getByRole("button", { name: "From library" }).click();
    const dialog = page.getByRole("dialog", { name: "Choose from your library" });
    await dialog.getByRole("listitem").first().getByRole("button").click();
    await expect(dialog).toBeHidden();
  }
  if (await page.getByRole("button", { name: "Clear all" }).isVisible()) {
    await page.getByRole("button", { name: "Clear all" }).click();
  }
  await expect(page.getByText("Draw at least one motion path on the start frame.")).toBeVisible();

  const canvas = page.getByRole("img", { name: "Motion paths" });
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.7);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.3, { steps: 12 });
  await page.mouse.up();
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.15);

  await expect(page.getByRole("button", { name: "Remove path, moves up and right" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Remove pin" })).toBeVisible();
  await expect(page.getByText("Draw at least one motion path on the start frame.")).toBeHidden();

  await page.getByPlaceholder(/Describe the shot/).fill(`e2e: the sailboat glides right ${Date.now()}`);
  await page.waitForLoadState("networkidle");
  const before = await page.getByRole("article").count();
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  await expect(page.getByRole("article")).toHaveCount(before + 1);
  await expect(page.getByRole("article").first()).toHaveAttribute("aria-label", /Wan-Move, succeeded/, { timeout: 60_000 });
});
