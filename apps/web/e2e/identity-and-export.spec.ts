import { readFile } from "node:fs/promises";
import path from "node:path";
import { expect, type Page, test } from "@playwright/test";

// Every call is answered here, so this runs without the API (like looks-and-casts.spec.ts).
const PUBLIC = path.resolve(__dirname, "../public");
const summary = (id: string, name: string, output_type: string, ops: string[], lora_base: string) => ({
  id, name, kind: "model", family: "test", vendor: "test", description: "", output_type, status: "active",
  license_tier: "commercial", tags: [], runnable: true, modes: [{ id: "m", label: "M", ops }], lora_base });
const MODELS = [
  summary("wan-2.2-lora-trainer", "Wan 2.2 character LoRA trainer", "lora", ["lora_train"], "wan-2.2-a14b"),
  summary("flux-3-klein-lora-trainer", "FLUX 3 Klein character LoRA trainer", "lora", ["lora_train"], "flux-3-klein"),
  summary("wan-2.2-image", "Wan 2.2 stills", "image", ["t2i"], "wan-2.2-a14b"),
  summary("wan-2.2-lora", "Wan 2.2 with characters", "video", ["t2v"], "wan-2.2-a14b"),
  summary("flux-3-klein", "FLUX 3 Klein stills", "image", ["t2i"], "flux-3-klein"),
];
const asset = (id: string, title: string) => ({
  id, kind: "image", status: "ready", source: "upload", mime: "image/webp", url: `/media/${id}.webp`, thumb_url: null,
  poster_url: null, filmstrip_url: null, filmstrip_frames: null, proxy_url: null, width: 256, height: 256, duration_ms: null,
  fps: null, has_audio: null, size_bytes: 1000, title, tags: [], favorite: false, generation_id: null,
  output_index: null, metadata: {}, created_at: "2026-11-17T10:00:00Z",
});
const PHOTOS = Array.from({ length: 10 }, (_, n) => asset(`maya-${n + 1}`, `Maya ${n + 1}`));
const identity = (base: string, trigger: string, steps: number) =>
  ({ base, trigger, steps, low: `${base}-low`, high: null, run_id: "run-0", trained_at: "2026-11-10T10:00:00Z" });
const maya = (id: string, slug: string) => ({
  id, kind: "character", name: "Maya", slug, handle: `@${slug}`, description: "a tall woman with short silver hair",
  attributes: {}, voice: null, cover: PHOTOS[0], looks: {}, members: [], created_at: "2026-11-17T10:00:00Z",
  lora: { "wan-2.2-a14b": identity("wan-2.2-a14b", "mayazqwan", 1000), "flux-3-klein": identity("flux-3-klein", "mayazqflux", 600) },
  images: PHOTOS.map((a, n) => ({ asset: a, view: "front", is_primary: n === 0, look: null })),
  profile: { personality: "", backstory: "", catchphrases: [], speech: "" },
});
const ZIP = Buffer.from("PK\u0005\u0006 a character zip, as far as this test cares");

/** The API this page talks to, answered in the browser; `sent` keeps what was sent, by path. */
async function studio(page: Page) {
  const sent: Record<string, Record<string, unknown>[]> = {};
  const keep = (key: string, body: Record<string, unknown>) => (sent[key] ??= []).push(body);
  const saved: ReturnType<typeof maya>[] = [maya("maya", "maya")];
  await page.route("http://127.0.0.1:8400/**", (route) => route.fulfill({ status: 503, json: { detail: "off in this test" } }));
  await page.route("**/events*", (route) => route.abort());
  await page.route("**/registry", (route) => route.fulfill({ json: { hash: "h", models: MODELS, provider_mode: "mock", warnings: [] } }));
  await page.route("**/media/*", (route) => route.fulfill({ path: path.join(PUBLIC, "styles/watercolor-person.webp") }));
  await page.route("**/pipelines/estimate", (route) => {
    keep("estimate", route.request().postDataJSON());
    return route.fulfill({ json: { estimate_usd: 0, live_estimate_usd: 4.3, generations: 2, claude_usd: 0, claude_live_usd: 0 } });
  });
  await page.route("**/pipelines", (route) => {
    keep("pipelines", route.request().postDataJSON());
    return route.fulfill({ status: 201, json: { id: "run-1", status: "running" } });
  });
  await page.route(/\/elements(\?.*)?$/, (route) => route.fulfill({ json: saved }));
  await page.route(/\/elements\/maya$/, (route) => {
    const body = route.request().postDataJSON();
    keep("patch", body);
    delete (saved[0].lora as Record<string, unknown>)[body.clear_lora];
    return route.fulfill({ json: saved[0] });
  });
  await page.route("**/elements/maya/export", (route) => route.fulfill({ contentType: "application/zip", body: ZIP }));
  await page.route("**/elements/import", (route) => {
    const request = route.request();
    keep("import", { type: request.headers()["content-type"], body: request.postDataBuffer()?.toString("latin1") });
    saved.push(maya("maya-2", "maya-2"));
    return route.fulfill({ status: 201, json: saved.at(-1) });
  });
  return sent;
}

test("trained identities: one per base, named by the models that load it, forgotten one at a time", async ({ page }) => {
  const sent = await studio(page);
  await page.goto("/characters");
  await page.getByRole("button", { name: "Maya, Character" }).click();
  const panel = page.getByRole("dialog", { name: "Maya" }).getByRole("region", { name: "Trained identity" });
  await expect(panel).toContainText("with 1,000 steps. Write @maya on Wan 2.2 stills or Wan 2.2 with characters and this "
    + "face comes along; the trigger word mayazqwan goes into the prompt for you.");
  await expect(panel).toContainText("with 600 steps. Write @maya on FLUX 3 Klein stills and this face comes along; the "
    + "trigger word mayazqflux");

  await panel.getByRole("button", { name: "Forget the identity for FLUX 3 Klein stills" }).click();
  await expect.poll(() => sent.patch).toEqual([{ clear_voice: false, clear_lora: "flux-3-klein" }]);
  await expect(page.getByText("Forgot Maya's identity for FLUX 3 Klein stills")).toBeVisible();
  await expect(panel.getByRole("button", { name: /^Forget/ })).toHaveCount(1); // Wan 2.2's stays
  // focus isn't lost with the button that had it: Forget's goes to Train, Train's into the form, Cancel's back
  const again = panel.getByRole("button", { name: "Train an identity" });
  await expect(again).toBeFocused();
  await again.click();
  await expect(panel.getByLabel("Trainer")).toBeFocused();
  await panel.getByRole("button", { name: "Cancel" }).click();
  await expect(again).toBeFocused();

  await again.click();
  await expect(panel.getByLabel("Trainer")).toHaveValue("wan-2.2-lora-trainer"); // the recipe's own default
  await expect(panel).toContainText("Afterwards, Wan 2.2 stills or Wan 2.2 with characters keeps it wherever a prompt "
    + "mentions @maya, replacing the one trained before.");
  await panel.getByLabel("Trainer").selectOption("flux-3-klein-lora-trainer");
  await expect(panel).toContainText("Afterwards, FLUX 3 Klein stills keeps it wherever a prompt mentions @maya.");
  await expect.poll(() => sent.estimate?.at(-1)?.input).toMatchObject({ trainer_model_id: "flux-3-klein-lora-trainer" });
  const train = panel.getByRole("button", { name: /^Train identity/ });
  await expect(train).toHaveText("Train identity · $4.30");
  await train.click();
  await expect.poll(() => sent.pipelines?.length).toBe(1);
  expect(sent.pipelines[0]).toEqual({ recipe: "lora_train", confirm_cost_usd: 0, input: {
    element_id: "maya", slug: "maya", photos: PHOTOS.map((p) => p.id), steps: 1000,
    trainer_model_id: "flux-3-klein-lora-trainer" } });
  await expect(page).toHaveURL(/\/runs\/run-1$/);
});

test("a character exports as a zip and comes back from one as a new character", async ({ page }) => {
  const sent = await studio(page);
  await page.goto("/characters");
  await page.getByRole("button", { name: "Maya, Character" }).click();
  const editor = page.getByRole("dialog", { name: "Maya" });
  const download = page.waitForEvent("download");
  await editor.getByRole("button", { name: "Export" }).click();
  expect((await download).suggestedFilename()).toBe("maya.zip");
  expect(await readFile(await (await download).path())).toEqual(ZIP);
  await page.keyboard.press("Escape");
  await expect(editor).toHaveCount(0);

  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await (await chooser).setFiles({ name: "maya.zip", mimeType: "application/zip", buffer: ZIP });
  await expect.poll(() => sent.import?.length).toBe(1);
  expect(sent.import[0].type).toMatch(/^multipart\/form-data; boundary=/);
  expect(sent.import[0].body).toContain('name="file"; filename="maya.zip"');
  await expect(page.getByText("Imported @maya-2")).toBeVisible();
  // it opens, a new character with a handle of its own
  await expect(page.getByRole("dialog", { name: "Maya" })).toContainText("Write @maya-2 in a prompt to use it.");
  await expect(page.getByRole("button", { name: "Maya, Character" })).toHaveCount(2);
});
