import { expect, test } from "@playwright/test";

const HEALTH = {
  status: "ok", database: true, provider_mode: "mock", registry_hash: "x", registry_errors: null, ffmpeg: true,
  media_root: "data/media", llm_mode: "mock", llm_key_set: false,
  local_engine: { configured: true, online: true, url: "http://127.0.0.1:8188", gpu: "cuda:0 NVIDIA GeForce RTX 5070 : cudaMallocAsync" },
};
const MODELS = [
  { id: "wan-2.2-5b", name: "Wan 2.2 5B (local)", ready: true, reason: null },
  { id: "infinitetalk", name: "InfiniteTalk", ready: false, reason: "ComfyUI is missing 5 model files" },
  { id: "ltx-2.5", name: "LTX-2.5", ready: false, reason: "needs a GPU with 24 GB of memory; ComfyUI's has 12 GB" },
];

test.beforeEach(async ({ page }) => {
  // never reaches the API, so nothing is installed or downloaded
  await page.route("http://127.0.0.1:8400/**", (route) => route.fulfill({ status: 503, json: { detail: "off in this test" } }));
  await page.route("**/health", (route) => route.fulfill({ json: HEALTH }));
  await page.route("**/local-engine/models", (route) => route.fulfill({ json: MODELS }));
});

test("Local models: set one up after seeing what it downloads, and follow it", async ({ page }) => {
  let job: Record<string, unknown> | null = null;
  let asked: unknown = null;
  await page.route("**/local-engine/setup", async (route) => {
    if (route.request().method() === "POST") {
      asked = route.request().postDataJSON();
      job = { state: "running", model_ids: ["infinitetalk"], step: "Downloading 5 model files", done_bytes: 8.4e9,
              total_bytes: 23e9, failed: [], restart: false };
      return route.fulfill({ json: job });
    }
    await route.fulfill({ json: { available: true, reason: null, python_problem: null, job, models: [
      { id: "wan-2.2-5b", state: "ready" },
      { id: "infinitetalk", state: "needs", needs: "5 model files, 23.0 GB", packs: [], files: 5, bytes: 23e9,
        free_bytes: 412e9, room: true },
      { id: "ltx-2.5", state: "too_big", needs: "needs a GPU with 24 GB" },
    ] } });
  });
  await page.route("**/local-engine/setup/job", (route) => route.fulfill({ json: job }));

  await page.goto("/settings/local");
  await expect(page.locator("p", { hasText: "Need a bigger GPU:" })).toContainText("LTX-2.5 (24 GB)");
  await expect(page.getByRole("list", { name: "Ready" })).toContainText("Wan 2.2 5B (local)");
  const pending = page.getByRole("list", { name: "Not set up yet" });
  await pending.getByRole("button", { name: "Set up" }).click();
  await expect(pending).toContainText("Download 5 model files, 23.0 GB (412.0 GB free)");
  expect(asked).toBeNull(); // nothing starts before it's confirmed
  await pending.getByRole("button", { name: "Set up" }).click();
  await expect.poll(() => asked).toEqual({ model_ids: ["infinitetalk"] });
  await expect(page.getByRole("progressbar", { name: "Downloaded" })).toHaveAttribute("aria-valuenow", "37");
  await expect(page.getByText("8.4 GB of 23.0 GB")).toBeVisible();

  job = { ...job!, state: "done", step: null, done_bytes: 23e9 };
  await expect(page.getByText("ComfyUI sees the new files within a minute.")).toBeVisible();
});

test("Local models: on another computer, the command to run there", async ({ page }) => {
  await page.route("**/local-engine/setup", (route) => route.fulfill({ json: {
    available: false, reason: "ComfyUI runs on another computer", python_problem: null, models: [], job: null,
  } }));
  await page.route("**/local-engine/setup/job", (route) => route.fulfill({ json: null }));
  await page.goto("/settings/local");
  await expect(page.getByText("ComfyUI runs on another computer, so set them up there")).toBeVisible();
  await expect(page.getByText("uv run python scripts/setup_local.py")).toBeVisible();
  await expect(page.getByRole("button", { name: "Set up" })).toHaveCount(0);
});
