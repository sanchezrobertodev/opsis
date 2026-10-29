import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  // These UI checks never contact providers or change the machine's real model folders.
  await page.route("http://127.0.0.1:8400/**", (route) =>
    route.fulfill({ status: 503, json: { detail: "Unrelated API disabled for storage UI test" } }));
});

test("Model storage: save, restart notice, persistence and validation", async ({ page }, testInfo) => {
  let directory = "D:\\Models\\ComfyUI";
  let restart = false;
  await page.route("**/local-engine/model-storage", async (route) => {
    if (route.request().method() === "PATCH") {
      const requested = route.request().postDataJSON().directory;
      if (requested === "relative") {
        await route.fulfill({ status: 422, json: { detail: "Choose an absolute folder path." } });
        return;
      }
      directory = requested;
      restart = true;
    }
    await route.fulfill({ json: {
      available: true, directory, active_directory: "D:\\Models\\ComfyUI", restart_required: restart,
      libraries: ["D:\\Models\\Old"], free_bytes: 816.7 * 1024 ** 3,
    } });
  });
  await page.goto("/settings");
  const section = page.getByRole("region", { name: "Local model storage" });
  const input = section.getByLabel("Model download folder");
  await expect(input).toHaveValue(directory);
  await expect(section.getByText("ComfyUI is using this folder.")).toBeVisible();
  await expect(section.getByRole("button", { name: "Save model folder" })).toBeDisabled();
  await input.fill("relative");
  await section.getByRole("button", { name: "Save model folder" }).click();
  await expect(section.getByRole("alert")).toHaveText("Choose an absolute folder path.");
  await input.fill("J:\\AI\\Models");
  await section.getByRole("button", { name: "Save model folder" }).click();
  await expect(section.getByText(/Saved\. Restart ComfyUI/)).toBeVisible();
  await page.reload();
  await expect(input).toHaveValue("J:\\AI\\Models");
  await expect(section.getByText("D:\\Models\\Old", { exact: true })).toBeVisible();
  await section.screenshot({ path: testInfo.outputPath("model-storage.png") });
});

test("Model storage explains when no local installation is connected", async ({ page }) => {
  await page.route("**/local-engine/model-storage", (route) => route.fulfill({ json: {
    available: false, message: "Model storage is not connected to this studio.", libraries: [],
  } }));
  await page.goto("/settings");
  const section = page.getByRole("region", { name: "Local model storage" });
  await expect(section.getByText("Model storage is not connected to this studio.")).toBeVisible();
  await expect(section.getByRole("button", { name: "Save model folder" })).toHaveCount(0);
});
