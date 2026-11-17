import path from "node:path";
import { expect, type Page, test } from "@playwright/test";

// Every call is answered here, so this runs without the API (like looks-and-casts.spec.ts).
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
const summary = (id: string, name: string, output_type: string, modes: { id: string; label: string; ops: string[] }[]) => ({
  id, name, kind: "model", family: "test", vendor: "test", description: "", output_type, status: "active",
  license_tier: "commercial", tags: [], runnable: true, modes,
});
const MODELS = [
  { ...MODEL, modes: MODEL.modes.map(({ id, label, ops }) => ({ id, label, ops })), runnable: true },
  summary("tripo-h3.1", "Tripo H3.1", "model3d", [{ id: "image_to_3d", label: "Picture to 3D", ops: ["to_3d"] }]),
  summary("kling-avatar-2", "Kling Avatar 2", "video", [{ id: "avatar", label: "Talking portrait", ops: ["lipsync", "avatar"] }]),
];
const MOCK = { route_id: "nano-banana-pro@mock", provider: "mock", provider_kind: "mock", eligible: true, reason: null,
  estimate_usd: 0, pricing_basis: null, is_mock: true };
// mock mode: a take is free here, and quoted at what the live route would charge
const ESTIMATE = { model_id: "nano-banana-pro", mode: "edit", chosen_route_id: MOCK.route_id, estimate_usd: 0,
  live_reference: { route_id: "nano-banana-pro@fal", estimate_usd: 0.15 }, candidates: [MOCK], warnings: [], params: {} };
const LOOKS = ["moves", "shot_sizes", "angles", "lenses", "focal_lengths", "apertures", "camera_bodies", "eras", "lighting",
  "light_sources", "light_positions", "light_colors", "palettes", "genres", "pacing", "emotions", "styles"];
const LIBRARY = Object.fromEntries(LOOKS.map((s) => [s, []]));
const CALLS = { mode: "mock", verified: "2026-11-01", calls: {
  "character.profile": { model: "claude-sonnet-5-5", name: "Claude Sonnet 5.5", usd: 0.02 },
  "character.check": { model: "claude-sonnet-5-5", name: "Claude Sonnet 5.5", usd: 0.03 } } };
const asset = (id: string, title: string) => ({
  id, kind: "image", status: "ready", source: "upload", mime: "image/webp", url: `/media/${id}.webp`, thumb_url: null,
  poster_url: null, filmstrip_url: null, filmstrip_frames: null, proxy_url: null, width: 256, height: 256, duration_ms: null,
  fps: null, has_audio: null, size_bytes: 1000, title, tags: [], favorite: false, generation_id: null,
  output_index: null, metadata: {}, created_at: "2026-11-17T10:00:00Z",
});
const EMPTY = { personality: "", backstory: "", catchphrases: [] as string[], speech: "" };
const element = (id: string, kind: string, name: string, more: Record<string, unknown> = {}) => ({
  id, kind, name, slug: id, handle: `@${id}`, description: "", attributes: {}, voice: null, lora: null, cover: null,
  images: [], looks: {}, members: [], profile: EMPTY, created_at: "2026-11-17T10:00:00Z", ...more,
});
const MAYA_FRONT = asset("maya-front", "Maya");
// what she has saved: a personality and a catchphrase, nothing else
const SAVED = { ...EMPTY, personality: "Warm and loyal.", catchphrases: ["Leave it to me."] };
const MAYA = element("maya", "character", "Maya", { description: "a tall woman with short silver hair", cover: MAYA_FRONT,
  images: [{ asset: MAYA_FRONT, view: "front", is_primary: true, look: null }], profile: SAVED });
const HARBOUR = element("harbour", "location", "Harbour", { cover: asset("harbour-wide", "Harbour") });
const DRAFT = { personality: "Drafted temper.", backstory: "Grew up by the sea.", catchphrases: ["A drafted line."],
                speech: "Quick and bright." };

/** The API this page talks to, answered in the browser; `sent` keeps what was sent, by method and path. */
async function studio(page: Page) {
  const sent: Record<string, Record<string, unknown>[]> = {};
  const keep = (key: string, body: Record<string, unknown>) => (sent[key] ??= []).push(body);
  await page.route("http://127.0.0.1:8400/**", (route) => route.fulfill({ status: 503, json: { detail: "off in this test" } }));
  await page.route("**/events*", (route) => route.abort());
  await page.route("**/registry", (route) => route.fulfill({ json: { hash: "h", models: MODELS, provider_mode: "mock", warnings: [] } }));
  await page.route("**/registry/models/nano-banana-pro", (route) => route.fulfill({ json: MODEL }));
  await page.route("**/registry/estimate", (route) => {
    keep("estimate", route.request().postDataJSON());
    return route.fulfill({ json: ESTIMATE });
  });
  await page.route("**/director/library", (route) => route.fulfill({ json: LIBRARY }));
  await page.route("**/llm/calls", (route) => route.fulfill({ json: CALLS }));
  await page.route("**/generations?*", (route) => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.route("**/pipelines/estimate", (route) => route.fulfill({ json: {
    estimate_usd: 0, live_estimate_usd: 0.4, generations: 2, claude_usd: 0, claude_live_usd: 0 } }));
  await page.route("**/pipelines", (route) => {
    keep("pipelines", route.request().postDataJSON());
    return route.fulfill({ status: 201, json: { id: "run-1", status: "running" } });
  });
  await page.route("**/media/*", (route) => route.fulfill({ path: path.join(PUBLIC, "styles/watercolor-person.webp") }));
  await page.route(/\/elements(\?.*)?$/, (route) => {
    const kind = new URL(route.request().url()).searchParams.get("kind");
    return route.fulfill({ json: [MAYA, HARBOUR].filter((e) => !kind || e.kind === kind) });
  });
  await page.route(/\/elements\/maya(\/profile\/draft|\/check)?$/, (route) => {
    const request = route.request();
    const where = new URL(request.url()).pathname;
    keep(`${request.method()} ${where}`, request.postDataJSON() ?? {});
    if (where.endsWith("/draft")) return route.fulfill({ json: { profile: DRAFT, model: "claude-sonnet-5-5", mock: true, cost_usd: 0 } });
    if (where.endsWith("/check")) return route.fulfill({ json: { verdict: "different", summary: "This doesn't look like Maya.",
      differences: [{ aspect: "hair", detail: "long dark hair instead of short silver hair" }],
      model: "claude-sonnet-5-5", mock: true, cost_usd: 0 } });
    return route.fulfill({ json: MAYA });
  });
  return sent;
}

test("a profile: Claude's draft fills only what she hasn't written, and saving sends it", async ({ page }) => {
  const sent = await studio(page);
  await page.goto("/characters");
  await page.getByRole("button", { name: "Maya, Character" }).click();
  const editor = page.getByRole("dialog", { name: "Maya" });
  const profile = editor.getByRole("region", { name: "Profile" });
  await expect(profile.getByLabel("Personality")).toHaveValue("Warm and loyal.");
  await expect(profile.getByRole("textbox", { name: "Catchphrase 1" })).toHaveValue("Leave it to me.");
  const make = editor.getByRole("region", { name: "Make" }).getByRole("button", { name: "Say hello" });
  await expect(make).toBeEnabled();

  await profile.getByRole("button", { name: /^Draft with Claude/ }).click();
  await expect(profile.getByLabel("Backstory")).toHaveValue("Grew up by the sea.");
  await expect(profile.getByLabel("How they talk")).toHaveValue("Quick and bright.");
  await expect(profile.getByLabel("Personality")).toHaveValue("Warm and loyal."); // saved: hers
  await expect(profile.getByRole("textbox", { name: "Catchphrase 1" })).toHaveValue("Leave it to me.");
  await expect(profile.getByRole("textbox", { name: "Catchphrase 2" })).toHaveCount(0);
  expect(sent["POST /elements/maya/profile/draft"]).toHaveLength(1);
  // a draft isn't kept until it's saved, so nothing that leaves the editor can start meanwhile
  await expect(make).toBeDisabled();
  await expect(make).toHaveAttribute("title", "Save your changes first");

  await editor.getByRole("button", { name: "Save changes" }).click();
  await expect.poll(() => sent["PATCH /elements/maya"]?.length).toBe(1);
  expect(sent["PATCH /elements/maya"][0].profile).toEqual({ ...SAVED, backstory: "Grew up by the sea.", speech: "Quick and bright." });
});

test("make: placed in a location, built in 3D and saying hello, each priced, and none while edits are unsaved", async ({ page }) => {
  const sent = await studio(page);
  await page.goto("/characters");
  await page.getByRole("button", { name: "Maya, Character" }).click();
  let editor = page.getByRole("dialog", { name: "Maya" });
  let make = editor.getByRole("region", { name: "Make" });

  // an edit waits for a save before anything reads the saved character: the quick actions and the older panels alike
  await editor.getByLabel("Description").fill("a tall woman with long silver hair");
  await expect(make.getByRole("button", { name: "3D figure" })).toBeDisabled();
  await editor.getByRole("button", { name: "Make a reference sheet" }).click();
  const sheet = editor.getByRole("button", { name: /^Make the sheet/ });
  await expect(sheet).toBeDisabled();
  await expect(sheet).toHaveAttribute("title", "Save your changes first");
  await editor.getByLabel("Description").fill("a tall woman with short silver hair"); // as saved
  await expect(sheet).toBeEnabled();
  await editor.getByRole("button", { name: "Make a reference sheet" }).click();

  await make.getByRole("button", { name: "3D figure" }).click();
  await expect(make.getByRole("button", { name: "3D figure" })).toHaveAttribute("aria-expanded", "true");
  const figure = editor.getByRole("region", { name: "3D figure" });
  await expect(figure.getByLabel("Rig it to move")).toBeChecked(); // a person, not a creature
  await expect(figure.getByLabel("Build it from their full-body picture")).toHaveCount(0); // she has none
  await figure.getByRole("button", { name: /^Make the figure/ }).click();
  await expect.poll(() => sent.pipelines?.length).toBe(1);
  expect(sent.pipelines[0]).toEqual({ recipe: "character_figure", confirm_cost_usd: 0,
                                      input: { character: "maya", model_id: "tripo-h3.1", rig: true } });
  await expect(page).toHaveURL(/\/runs\/run-1$/);

  await page.goto("/characters");
  await page.getByRole("button", { name: "Maya, Character" }).click();
  editor = page.getByRole("dialog", { name: "Maya" });
  make = editor.getByRole("region", { name: "Make" });
  await make.getByRole("button", { name: "Say hello" }).click();
  const hello = editor.getByRole("region", { name: "Say hello" });
  await expect(hello.getByLabel("What they say")).toHaveValue("Leave it to me."); // her first catchphrase
  await expect(hello).toContainText("in the default voice"); // she has no saved voice
  await hello.getByRole("button", { name: /^Make the clip/ }).click();
  await expect.poll(() => sent.pipelines?.length).toBe(2);
  expect(sent.pipelines[1]).toEqual({ recipe: "talking_shot", confirm_cost_usd: 0, input: {
    character: "maya", script: "Leave it to me.", models: [{ model_id: "kling-avatar-2" }] } });

  await page.goto("/characters");
  await page.getByRole("button", { name: "Maya, Character" }).click();
  editor = page.getByRole("dialog", { name: "Maya" });
  await editor.getByRole("region", { name: "Make" }).getByRole("button", { name: "Place in a location" }).click();
  const place = editor.getByRole("region", { name: "Place in a location" });
  await expect(place.getByRole("button", { name: "Open in Create image" })).toBeDisabled();
  await place.getByRole("button", { name: "Harbour" }).click();
  const where = "@maya at @harbour, same face and outfit, in the place's light.";
  await expect.poll(() => sent.estimate?.at(-1)?.prompt).toBe(where); // priced with the pictures both mentions bring
  await expect(place).toContainText("1 take, about $0.15");
  await place.getByRole("button", { name: "Open in Create image" }).click();
  await expect(page).toHaveURL(/\/create\/image$/);
  await expect(page.getByLabel("Prompt", { exact: true })).toHaveValue(where);
});

test("a take's likeness: checked against the character it mentioned, and the answer opens to what differs", async ({ page }) => {
  const sent = await studio(page);
  const picture = { ...asset("take-1", "Maya on the pier"), generation_id: "t1", output_index: 0 };
  const take = { id: "t1", model_id: "nano-banana-pro", output_type: "image", mode: "edit", status: "succeeded",
                 assets: [picture], routing: {}, created_at: "2026-11-17T10:00:00Z",
                 request: { prompt: "@maya on the pier", params: {}, media: [],
                            mentions: [{ id: "maya", handle: "@maya", kind: "character", look: null },
                                       { id: "harbour", handle: "@harbour", kind: "location", look: null }] } };
  await page.route("**/generations?*", (route) => route.fulfill({ json: { items: [take], next_cursor: null } }));

  await page.goto("/create/image");
  const check = page.getByRole("button", { name: /^Check likeness: Maya/ });
  await expect(check).toContainText("$0.03");
  await expect(page.getByRole("button", { name: /^Check likeness: Harbour/ })).toHaveCount(0); // a place isn't checked
  await check.click();
  await expect.poll(() => sent["POST /elements/maya/check"]).toEqual([{ asset_id: "take-1" }]);
  const badge = page.getByText("Maya: looks different");
  await badge.click();
  await expect(page.getByText("This doesn't look like Maya.")).toBeVisible();
  await expect(page.getByText("long dark hair instead of short silver hair")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Check Maya again/ })).toBeVisible();
});
