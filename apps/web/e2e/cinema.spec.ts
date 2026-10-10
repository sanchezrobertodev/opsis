import { expect, test } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8400";

test.beforeAll(async ({ request }) => {
  const health = await (await request.get(`${API}/health`)).json();
  expect(health.provider_mode, "e2e only runs against the mock provider").toBe("mock");
});

test("Cinema: script, Claude's shot plan, film every shot, pick takes, export the cut", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/cinema");
  await page.getByRole("button", { name: "New film" }).click();
  await page.getByLabel("Title").fill(`e2e film ${Date.now()}`);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page).toHaveURL(/\/cinema\/[0-9a-f-]+$/);

  await page.getByRole("radio", { name: "Script" }).click();
  await page.getByLabel("Script").fill("A kite lifts off the beach. It climbs into the wind.");
  await page.getByRole("button", { name: "Plan shots with Claude" }).click();
  await page.getByRole("button", { name: "Use this plan" }).click();

  await expect(page.getByText("Saved, version 2")).toBeVisible();
  await page.getByRole("button", { name: /^Film 2 unfilmed shots/ }).click();

  for (const action of ["A kite lifts off the beach.", "It climbs into the wind."]) {
    await page.getByRole("button", { name: new RegExp(action.replace(".", "\\.")) }).click();
    await page.getByRole("button", { name: "Use this take" }).first().click({ timeout: 90_000 });
    await expect(page.getByRole("button", { name: "In the cut" })).toBeVisible();
  }

  await expect(page.getByRole("button", { name: /^Film 0 unfilmed shots/ })).toBeVisible();  // both picked
  await page.getByRole("radio", { name: "Cut" }).first().click();
  // fade into the second shot, open on a title card, then export: the film is as long as the preview
  await page.getByLabel("Timeline").getByRole("button", { name: /^02 / }).click();
  await page.getByRole("region", { name: "Selected item" }).getByRole("radio", { name: "Fade" }).click();
  await page.getByRole("button", { name: "Title card" }).click();
  await page.getByLabel("Title", { exact: true }).fill("The Kite");
  await expect(page.getByLabel("Timeline").getByRole("button", { name: /The Kite/ })).toBeVisible();
  const length = (await page.getByLabel("Film length").textContent())!;
  await page.getByRole("button", { name: "Export the film" }).click();
  await expect(page.getByText(`Exported, ${length}.`)).toBeVisible({ timeout: 60_000 });
});

test("Cinema: light and feeling on a shot, a scene in one take, then continue a take", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/cinema");
  await page.getByRole("button", { name: "New film" }).click();
  await page.getByLabel("Title").fill(`e2e one take ${Date.now()}`);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page).toHaveURL(/\/cinema\/[0-9a-f-]+$/);

  await page.getByRole("radio", { name: "Script" }).click();
  await page.getByLabel("Script").fill("A kite lifts off the beach. It climbs into the wind.");
  await page.getByRole("button", { name: "Plan shots with Claude" }).click();
  await page.getByRole("button", { name: "Use this plan" }).click();
  await expect(page.getByText("Saved, version 2")).toBeVisible();

  // the director's lighting console and emotion wheel on the first shot
  await page.getByRole("button", { name: /A kite lifts off the beach\./ }).click();
  await page.getByRole("button", { name: "Lighting", exact: true }).click();
  await page.getByRole("button", { name: "Light from left" }).click();
  await expect(page.getByRole("tab", { name: "Key light" })).toBeVisible();
  await page.getByRole("button", { name: "Performance", exact: true }).click();
  await page.getByRole("radio", { name: "Joy", exact: true }).click();
  await expect(page.getByText("the subject feels joy")).toBeVisible();
  await page.getByRole("button", { name: "Add a line" }).click();
  await page.getByLabel("Line 1", { exact: true }).fill("Up it goes!");
  // the plan put its character in the cast, so a new line starts as theirs; the narrator is one pick away
  await expect(page.getByLabel("Who says line 1")).toHaveValue(/^@lead(-\d+)?$/);
  await page.getByLabel("Who says line 1").selectOption("Narrator");
  await expect(page.getByLabel("Who says line 1")).toHaveValue("Narrator");

  // both shots in one multi-shot call, cut back into a take per shot
  await page.getByRole("button", { name: /^Film as one take/ }).click();
  await expect(page.getByText(/cut from a scene take/)).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Use this take" }).first().click();
  await expect(page.getByRole("button", { name: "In the cut" })).toBeVisible();

  await page.getByLabel("What happens next").fill("It loops once and dives toward the sand");
  await page.getByRole("button", { name: "Continue the take" }).click();
  await expect(page.getByText(/continued from take 1/)).toBeVisible({ timeout: 90_000 });
});

test("Cinema storyboard: duplicate, drag to reorder, split, and what each shot costs", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/cinema");
  await page.getByRole("button", { name: "New film" }).click();
  await page.getByLabel("Title").fill(`e2e storyboard ${Date.now()}`);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page).toHaveURL(/\/cinema\/[0-9a-f-]+$/);
  await page.getByRole("radio", { name: "Script" }).click();
  await page.getByLabel("Script").fill("A kite lifts off the beach. It climbs into the wind.");
  await page.getByRole("button", { name: "Plan shots with Claude" }).click();
  await page.getByRole("button", { name: "Use this plan" }).click();
  await expect(page.getByText("Saved, version 2")).toBeVisible();

  // each card says how long the shot is and about what filming it costs on the paid routes
  const cards = page.locator("section ol > li");
  await expect(page.getByText(/^about \$\d/).first()).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: /It climbs into the wind\./ }).click();
  await page.getByRole("button", { name: "Duplicate shot" }).click();
  await expect(page.getByRole("heading", { name: "Shot 03" })).toBeVisible();
  await page.getByLabel("What happens").fill("The string snaps");
  await expect(page.getByRole("button", { name: /^Film 3 unfilmed shots/ })).toBeVisible();

  // the copy moves to the front with the keyboard, by its handle
  await page.getByRole("button", { name: "Move shot 03" }).focus();
  await page.keyboard.press("Space");
  for (const key of ["ArrowLeft", "ArrowLeft"]) {
    await page.waitForTimeout(250);
    await page.keyboard.press(key);
  }
  await page.waitForTimeout(250);
  await page.keyboard.press("Space");
  await expect(cards.first()).toContainText("The string snaps");

  // splitting a shot makes a second part that continues the first
  await page.getByRole("button", { name: /The string snaps/ }).click();
  await page.getByRole("button", { name: "Split shot" }).click();
  await page.getByRole("region", { name: "Split the shot" }).getByRole("button", { name: "Split" }).click();
  await expect(page.getByRole("heading", { name: "Shot 02" })).toBeVisible();
  await expect(page.getByRole("radio", { name: "From last" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByRole("button", { name: /^Film 4 unfilmed shots/ })).toBeVisible();
});

test("Cinema: pick an opening and a closing frame, then film from one to the other", async ({ page }) => {
  test.setTimeout(150_000);
  await page.goto("/cinema");
  await page.getByRole("button", { name: "New film" }).click();
  await page.getByLabel("Title").fill(`e2e frames ${Date.now()}`);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page).toHaveURL(/\/cinema\/[0-9a-f-]+$/);
  await page.getByRole("radio", { name: "Script" }).click();
  await page.getByLabel("Script").fill("A kite lifts off the beach.");
  await page.getByRole("button", { name: "Plan shots with Claude" }).click();
  await page.getByRole("button", { name: "Use this plan" }).click();
  await expect(page.getByText("Saved, version 2")).toBeVisible();

  await page.getByRole("button", { name: /A kite lifts off the beach\./ }).click();
  await page.getByRole("button", { name: /^Make 4$/ }).first().click();
  await page.getByRole("button", { name: "Opening frame option" }).first().click({ timeout: 60_000 });
  const closing = page.getByRole("region", { name: "Closing frame" });
  await closing.getByRole("button", { name: /^Make 4$/ }).click();
  await closing.getByRole("button", { name: "Closing frame option" }).first().click({ timeout: 60_000 });
  await expect(closing.getByRole("button", { name: "Closing frame option" })).toHaveCount(4);
  await expect(page.getByText("Ends on the picked closing frame.")).toBeVisible();

  await page.getByRole("button", { name: /^Film \d$/ }).click();
  await expect(page.getByRole("button", { name: "Use this take" }).first()).toBeVisible({ timeout: 90_000 });
});

test("Cinema: the AI Director proposes changes as a diff, applied only when asked", async ({ page }) => {
  await page.goto("/cinema");
  await page.getByRole("button", { name: "New film" }).click();
  await page.getByLabel("Title").fill(`e2e director ${Date.now()}`);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page).toHaveURL(/\/cinema\/[0-9a-f-]+$/);
  await page.getByRole("radio", { name: "Script" }).click();
  await page.getByLabel("Script").fill("A kite lifts off the beach. It climbs into the wind. The string snaps.");
  await page.getByRole("button", { name: "Plan shots with Claude" }).click();
  await page.getByRole("button", { name: "Use this plan" }).click();
  await expect(page.getByText("Saved, version 2")).toBeVisible();

  await page.getByRole("button", { name: "Director" }).click();
  const director = page.getByRole("complementary", { name: "AI Director" });
  await director.getByLabel("Message the director").fill("Remove shot 2");
  await director.getByLabel("Message the director").press("Enter");
  const changes = director.getByRole("list", { name: "Proposed changes" });
  await expect(changes).toContainText("Removed");
  await expect(changes).toContainText("Shot 2");
  await expect(page.getByText("It climbs into the wind.")).toBeVisible();  // not yet

  await director.getByRole("button", { name: "Apply changes" }).click();
  await expect(director.getByText("Applied")).toBeVisible();
  await expect(page.getByText("Saved, version 3")).toBeVisible();
  await expect(page.getByText("It climbs into the wind.")).toHaveCount(0);

  // the conversation carries on, and a discarded answer changes nothing
  await director.getByLabel("Message the director").fill("Make shot 1 tighter");
  await director.getByRole("button", { name: "Send" }).click();
  await expect(director.getByText(/Shot 1 is now a close-up/)).toBeVisible();
  await director.getByRole("button", { name: "Discard" }).click();
  await expect(director.getByText("Discarded")).toBeVisible();
  await expect(page.getByText("Saved, version 3")).toBeVisible();
});
