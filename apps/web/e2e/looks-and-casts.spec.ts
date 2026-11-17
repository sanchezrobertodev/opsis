import path from "node:path";
import { expect, type Page, test } from "@playwright/test";

// Every call is answered here, so this runs without the API (like character-designer.spec.ts).
const PUBLIC = path.resolve(__dirname, "../public");
const REFS = { role: "image_references", kinds: ["image"], label: "Reference images", max: 8, min: 1, required: true, auto: false };
const MODEL = {
  id: "nano-banana-pro", name: "Nano Banana Pro", kind: "model", family: "gemini", vendor: "test", description: "Stills.",
  output_type: "image", status: "active", license_tier: "commercial", tags: [],
  prompt: { max_chars: 2000, dialect: "gemini", supports_negative: false, dialogue: false },
  aspect_ratios: [], duration: null, parameters: [], rules: [], routes: [], virtual: null,
  elements: { strategy: "refs_native", ref_role: "image_references" },
  modes: [{ id: "t2i", label: "Text to image", ops: ["t2i"], prompt: "required", medias: [] },
          { id: "edit", label: "Reference images", ops: ["multi_ref"], prompt: "required", medias: [REFS] }],
};
const SUMMARY = { ...MODEL, modes: MODEL.modes.map(({ id, label, ops }) => ({ id, label, ops })), runnable: true };
const MOCK = { route_id: "nano-banana-pro@mock", provider: "mock", provider_kind: "mock", eligible: true, reason: null,
  estimate_usd: 0, pricing_basis: null, is_mock: true };
const ESTIMATE = { model_id: "nano-banana-pro", mode: "edit", chosen_route_id: MOCK.route_id, estimate_usd: 0,
  live_reference: null, candidates: [MOCK], warnings: [], params: {} };
const style = (id: string, label: string) =>
  ({ id, label, category: "painting", summary: `${label}.`, prompt: `${label.toLowerCase()} picture`, dialects: {}, swatch: [], photographic: false });
const LOOKS = ["moves", "shot_sizes", "angles", "lenses", "focal_lengths", "apertures", "camera_bodies", "eras", "lighting",
  "light_sources", "light_positions", "light_colors", "palettes", "genres", "pacing", "emotions"];
const LIBRARY = { ...Object.fromEntries(LOOKS.map((s) => [s, []])), styles: [style("watercolor", "Watercolor")] };
const asset = (id: string, title: string) => ({
  id, kind: "image", status: "ready", source: "upload", mime: "image/webp", url: `/media/${id}.webp`, thumb_url: null,
  poster_url: null, filmstrip_url: null, filmstrip_frames: null, proxy_url: null, width: 256, height: 256, duration_ms: null,
  fps: null, has_audio: null, size_bytes: 1000, title, tags: [], favorite: false, generation_id: null,
  output_index: null, metadata: {}, created_at: "2026-11-17T10:00:00Z",
});
const element = (id: string, kind: string, name: string, more: Record<string, unknown> = {}) => ({
  id, kind, name, slug: id, handle: `@${id}`, description: "", attributes: {}, voice: null, lora: null, cover: null,
  images: [], looks: {}, members: [], profile: { personality: "", backstory: "", catchphrases: [], speech: "" },
  created_at: "2026-11-17T10:00:00Z", ...more,
});
const MAYA_FRONT = asset("maya-front", "Maya");
const KAI_FRONT = asset("kai-front", "Kai");
const MAYA = element("maya", "character", "Maya", {
  description: "a tall woman with short silver hair", cover: MAYA_FRONT,
  images: [{ asset: MAYA_FRONT, view: "front", is_primary: true, look: null },
           { asset: asset("maya-coat", "Maya in her coat"), view: "full_body", is_primary: false, look: "winter-coat" }],
  looks: { "winter-coat": { name: "Winter coat", description: "wearing a long red wool coat" } },
});
const KAI = element("kai", "character", "Kai", { cover: KAI_FRONT,
  images: [{ asset: KAI_FRONT, view: "front", is_primary: true, look: null }] });
const HARBOUR = element("harbour", "location", "Harbour");

/** The API this page talks to, answered in the browser; `sent` keeps what was posted, by path. */
async function studio(page: Page) {
  const sent: Record<string, Record<string, unknown>[]> = {};
  const keep = (key: string, body: Record<string, unknown>) => (sent[key] ??= []).push(body);
  const saved: Record<string, unknown>[] = [MAYA, KAI, HARBOUR];
  await page.route("http://127.0.0.1:8400/**", (route) => route.fulfill({ status: 503, json: { detail: "off in this test" } }));
  await page.route("**/events*", (route) => route.abort());
  await page.route("**/registry", (route) => route.fulfill({ json: { hash: "h", models: [SUMMARY], provider_mode: "mock", warnings: [] } }));
  await page.route("**/registry/models/nano-banana-pro", (route) => route.fulfill({ json: MODEL }));
  await page.route("**/registry/estimate", (route) => {
    keep("estimate", route.request().postDataJSON());
    return route.fulfill({ json: ESTIMATE });
  });
  await page.route("**/director/library", (route) => route.fulfill({ json: LIBRARY }));
  await page.route("**/generations?*", (route) => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.route("**/pipelines/estimate", (route) => route.fulfill({ json: {
    estimate_usd: 0, live_estimate_usd: 0.15, generations: 1, claude_usd: 0, claude_live_usd: 0 } }));
  await page.route("**/pipelines", (route) => {
    keep("pipelines", route.request().postDataJSON());
    return route.fulfill({ status: 201, json: { id: "run-1", status: "running" } });
  });
  await page.route("**/media/*", (route) => route.fulfill({ path: path.join(PUBLIC, "styles/watercolor-person.webp") }));
  await page.route(/\/elements(\?.*)?$/, (route) => {
    if (route.request().method() !== "POST") {
      const kind = new URL(route.request().url()).searchParams.get("kind");
      return route.fulfill({ json: saved.filter((e) => !kind || e.kind === kind) });
    }
    const body = route.request().postDataJSON();
    keep("elements", body);
    const members = (body.members as string[]).map((id) => {
      const m = saved.find((e) => e.id === id)!;
      return { id, name: m.name, handle: m.handle, cover: m.cover };
    });
    const e = element("the-crew", body.kind, body.name, { members });
    saved.push(e);
    return route.fulfill({ status: 201, json: e });
  });
  // a look renamed or deleted, or one of its pictures taken off: kept by method and path, answered with Maya as she was
  await page.route(/\/elements\/maya\/(looks|images)\/[^/]+$/, (route) => {
    const request = route.request();
    keep(`${request.method()} ${new URL(request.url()).pathname}`, request.postDataJSON() ?? {});
    return route.fulfill({ json: MAYA });
  });
  return sent;
}

test("a cast of picked characters: made from the selection, edited, and drafted as a group shot", async ({ page }) => {
  const sent = await studio(page);
  await page.goto("/characters");
  await page.getByRole("button", { name: "Select" }).click();
  await page.getByRole("checkbox", { name: "Kai, Character" }).click(); // the order picked is the cast's
  await page.getByRole("checkbox", { name: "Maya, Character" }).click();
  await expect(page.getByRole("button", { name: "Harbour, Location" })).toBeDisabled(); // a cast holds characters
  await page.getByRole("toolbar", { name: "Selection" }).getByRole("button", { name: "Make a cast" }).click();
  const naming = page.getByRole("dialog", { name: "Make a cast" });
  await naming.getByLabel("Name").fill("The crew");
  await naming.getByRole("button", { name: "Make the cast" }).click();
  await expect.poll(() => sent.elements?.length).toBe(1);
  expect(sent.elements[0]).toEqual({ kind: "cast", name: "The crew", description: "", attributes: {}, images: [],
                                     members: ["kai", "maya"] });
  await expect(page.getByRole("toolbar", { name: "Selection" })).toHaveCount(0);
  // not lost with the selection bar's button that opened the dialog
  await expect(page.getByRole("button", { name: "Select", exact: true })).toBeFocused();

  const card = page.getByRole("button", { name: "The crew, Cast" });
  await expect(card).toContainText("@the-crew, cast, 2 characters");
  await card.click();
  const editor = page.getByRole("dialog", { name: "The crew" });
  await expect(editor.getByText("Cast (a saved group of characters)")).toBeVisible();
  await expect(editor.getByRole("group", { name: "Reference images" })).toHaveCount(0); // its pictures are its characters'
  const save = editor.getByRole("button", { name: "Save changes" });
  const adding = editor.getByRole("group", { name: "Add a character" });
  await expect(adding).toHaveCount(0); // both are in it already
  await editor.getByRole("button", { name: "Take Kai out of the cast" }).click();
  await expect(save).toBeDisabled(); // one character is no cast
  await adding.getByRole("button", { name: "Kai" }).click();
  await expect(save).toBeEnabled();
  // the group shot mentions the cast as saved: Kai now comes last, so it waits for the save
  const groupShot = editor.getByRole("button", { name: "Group shot" });
  await expect(groupShot).toBeDisabled();
  await expect(groupShot).toHaveAttribute("title", "Save your changes first");
  await editor.getByRole("button", { name: "Take Maya out of the cast" }).click();
  await adding.getByRole("button", { name: "Maya" }).click(); // as saved again

  await groupShot.click();
  await expect(page).toHaveURL(/\/create\/image$/);
  await expect(page.getByLabel("Prompt", { exact: true }))
    .toHaveValue("Group shot of @the-crew, standing side by side and facing the camera.");
  // the mention's pictures fill the reference slot, and the estimate keeps the mention so they're priced
  await expect(page.getByRole("button", { name: "Generate", exact: true })).toBeEnabled();
  await expect(page.getByText("Reference images is required")).toHaveCount(0);
  await expect.poll(() => sent.estimate?.at(-1)?.prompt).toBe("@the-crew");
  expect(sent.estimate.at(-1)).toMatchObject({ model_id: "nano-banana-pro", mode: "edit", media: [] });
});

test("looks: listed with their handle, added from an outfit or a style, and suggested after the handle's colon", async ({ page }) => {
  const sent = await studio(page);
  await page.goto("/characters");
  await page.getByRole("button", { name: "Maya, Character" }).click();
  let editor = page.getByRole("dialog", { name: "Maya" });
  const looks = editor.getByRole("region", { name: "Looks" });
  await expect(looks).toContainText("Winter coat");
  await expect(looks).toContainText("@maya:winter-coat, 1 picture");
  // the coat's picture is the look's, not one of her own: it's listed with the look, and taken off there
  await expect(editor.getByRole("group", { name: "Reference images" }).getByRole("button", { name: /^Remove/ })).toHaveCount(1);
  await looks.getByRole("button", { name: "Remove Winter coat picture 1" }).click();
  await expect.poll(() => sent["DELETE /elements/maya/images/maya-coat"]?.length).toBe(1);
  // renamed in place: the name field takes the focus, and gives it back to Rename when it closes
  await looks.getByRole("button", { name: "Rename the Winter coat look" }).click();
  await expect(looks.getByLabel("Name of the look")).toBeFocused();
  await looks.getByLabel("Name of the look").fill("Snow coat");
  await looks.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => sent["PUT /elements/maya/looks/winter-coat"])
    .toEqual([{ name: "Snow coat", description: "wearing a long red wool coat" }]);
  await expect(looks.getByRole("button", { name: "Rename the Winter coat look" })).toBeFocused();
  await looks.getByRole("button", { name: "Delete the Winter coat look" }).click();
  await expect.poll(() => sent["DELETE /elements/maya/looks/winter-coat"]?.length).toBe(1);
  await expect(looks.getByRole("button", { name: "Add a look" })).toHaveAttribute("aria-expanded", "false");

  await looks.getByRole("button", { name: "Add a look" }).click();
  await expect(looks.getByRole("button", { name: "Add a look" })).toHaveAttribute("aria-expanded", "true");
  const outfit = editor.getByRole("region", { name: "Add a look" });
  await outfit.getByLabel("Name").fill("冬");
  await expect(outfit.getByText("Put a letter from a to z or a digit in the name")).toBeVisible(); // no handle in it
  await outfit.getByLabel("Name").fill("Rain gear");
  await expect(outfit.getByText("Put a letter from a to z or a digit in the name")).toHaveCount(0);
  await outfit.getByLabel("What they wear").fill("a yellow raincoat and rubber boots");
  await expect(outfit.getByText("@maya:rain-gear")).toBeVisible();
  await outfit.getByRole("button", { name: /^Draw the look/ }).click();
  await expect.poll(() => sent.pipelines?.length).toBe(1);
  expect(sent.pipelines[0]).toEqual({ recipe: "character_look", confirm_cost_usd: 0, input: {
    look: "rain-gear", name: "Rain gear", outfit: "a yellow raincoat and rubber boots", character: "maya",
    model_id: "nano-banana-pro", count: 1 } });
  await expect(page).toHaveURL(/\/runs\/run-1$/);

  await page.goto("/characters");
  await page.getByRole("button", { name: "Maya, Character" }).click();
  editor = page.getByRole("dialog", { name: "Maya" });
  await editor.getByRole("region", { name: "Looks" }).getByRole("button", { name: "Another style" }).click();
  const styled = editor.getByRole("region", { name: "Another style" });
  await styled.getByRole("button", { name: "Style: No style" }).click();
  await page.getByRole("dialog", { name: "Picture style" }).getByRole("button", { name: /^Watercolor/ }).click();
  await expect(styled.getByLabel("How prompts say it")).toHaveValue("drawn in watercolor style");
  await expect(styled.getByText("@maya:watercolor")).toBeVisible();
  await styled.getByRole("button", { name: /^Draw the look/ }).click();
  await expect.poll(() => sent.pipelines?.length).toBe(2);
  expect(sent.pipelines[1]).toEqual({ recipe: "character_look", confirm_cost_usd: 0, input: {
    look: "watercolor", name: "Watercolor", description: "drawn in watercolor style", style: "watercolor",
    character: "maya", model_id: "nano-banana-pro", count: 1 } });

  await page.goto("/create/image");
  const prompt = page.getByLabel("Prompt", { exact: true });
  await prompt.fill("Portrait of ");
  await prompt.pressSequentially("@maya:w");
  const suggested = page.getByRole("listbox", { name: "Looks" });
  await expect(suggested.getByRole("option")).toHaveText([/Maya, Winter coat\s*@maya:winter-coat, look/]);
  await prompt.press("Enter");
  await expect(prompt).toHaveValue("Portrait of @maya:winter-coat ");
});
