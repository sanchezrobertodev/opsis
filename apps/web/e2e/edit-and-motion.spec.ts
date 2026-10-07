import { expect, type Page, test } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8400";

test.beforeAll(async ({ request }) => {
  const health = await (await request.get(`${API}/health`)).json();
  expect(health.provider_mode, "e2e only runs against the mock provider").toBe("mock");
  // these flows pick clips from the library, so make sure there is one
  const videos = await (await request.get(`${API}/assets?kind=video&limit=1`)).json();
  expect(videos.items.length, "run the create-video e2e first so the library has a clip").toBeGreaterThan(0);
});

async function pickFromLibrary(page: Page, slotIndex: number) {
  await page.getByRole("button", { name: "From library" }).nth(slotIndex).click();
  const dialog = page.getByRole("dialog", { name: "Choose from your library" });
  await dialog.getByRole("listitem").first().getByRole("button").click();
  await expect(dialog).toBeHidden();
}

test("Edit video reverses a clip on this computer, free", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/edit");
  await page.getByRole("radiogroup", { name: "Edit" }).getByRole("radio", { name: "Reverse" }).click();
  await expect(page.getByRole("button", { name: /Video tools \(this computer\)/ })).toBeVisible();
  const clip = page.getByRole("button", { name: /^Remove / });
  if ((await clip.count()) === 0) await pickFromLibrary(page, 0);
  await expect(page.getByText(/Free on this computer/)).toBeVisible();
  await page.waitForLoadState("networkidle");
  const before = await page.getByRole("article").count();
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  await expect(page.getByRole("article")).toHaveCount(before + 1);
  await expect(page.getByRole("article").first()).toHaveAttribute("aria-label", /Video tools.*succeeded/, { timeout: 60_000 });
});

test("Motion transfer copies a clip's movement onto a character and keeps the clip for reuse", async ({ page, request }) => {
  test.setTimeout(90_000);
  await page.goto("/motion-transfer");
  await expect(page.getByRole("group", { name: "Movement to copy" })).toBeVisible();
  for (const label of ["Character", "Movement to copy"]) {
    const slot = page.getByRole("group", { name: label });
    if ((await slot.getByRole("button", { name: /^Remove / }).count()) === 0) {
      await slot.getByRole("button", { name: "From library" }).click();
      const dialog = page.getByRole("dialog", { name: "Choose from your library" });
      await dialog.getByRole("listitem").first().getByRole("button").click();
      await expect(dialog).toBeHidden();
    }
  }
  await page.waitForLoadState("networkidle");
  const before = await page.getByRole("article").count();
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  await expect(page.getByRole("article")).toHaveCount(before + 1);
  await expect(page.getByRole("article").first()).toHaveAttribute("aria-label", /Take \d+, .*Motion Control.*succeeded/, { timeout: 60_000 });

  const tagged = await (await request.get(`${API}/assets?tag=driving&kind=video`)).json();
  expect(tagged.items.length).toBeGreaterThan(0);
});
