import { expect, test } from "@playwright/test";

test("Settings: each provider's key and balance on one row, and where the money went", async ({ page }) => {
  // the balances come from the providers themselves; answer for them here so the test never leaves the machine
  const now = new Date().toISOString();
  await page.route("**/keys", (route) => route.fulfill({ json: [
    { setting: "fal_key", label: "fal.ai", link: "https://fal.ai/dashboard/keys", what: "Most hosted models.",
      source: "env", last4: "a1b2", unreadable: false },
    { setting: "runway_api_key", label: "Runway", link: "https://dev.runwayml.com/", what: "Runway's video models.",
      source: "app", last4: "c3d4", unreadable: false },
    { setting: "anthropic_api_key", label: "Anthropic (Claude)", link: "https://platform.claude.com/",
      what: "Scripts.", source: "env", last4: "e5f6", unreadable: false },
    { setting: "pika_api_key", label: "Pika", link: "https://dev.pika.art/", what: "Seedance on Pika.",
      source: null, last4: null, unreadable: false },
  ] }));
  await page.route("**/budget/balances**", (route) => route.fulfill({ json: [
    { provider: "fal", label: "fal.ai", status: "ok", link: "https://fal.ai/dashboard/billing", setting: "fal_key",
      amount: 7.73, unit: "usd", usd: 7.73, detail: null, checked_at: now },
    { provider: "runway", label: "Runway", status: "ok", link: "https://dev.runwayml.com/", setting: "runway_api_key",
      amount: 862, unit: "credits", usd: 8.62, detail: null, checked_at: now },
    { provider: "anthropic", label: "Anthropic (Claude)", status: "no_api", link: "https://platform.claude.com/",
      setting: "anthropic_api_key", amount: null, unit: null, usd: null,
      detail: "no balance API; see the billing page", checked_at: now },
  ] }));
  await page.goto("/settings/providers");
  const connected = page.getByRole("list", { name: "Connected providers" });
  await expect(connected.getByRole("listitem").filter({ hasText: "fal.ai" }).getByText("$7.73")).toBeVisible();
  const runway = connected.getByRole("listitem").filter({ hasText: "Runway" });
  await expect(runway.getByText("862 credits ($8.62)")).toBeVisible();
  await expect(runway.getByText("Key saved here ending c3d4")).toBeVisible();
  await expect(connected.getByRole("listitem").filter({ hasText: "Anthropic" }).getByRole("link", { name: "Billing" }))
    .toBeVisible();
  await expect(page.getByRole("list", { name: "Providers without a key" }).getByText("Pika", { exact: true })).toBeVisible();

  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Spending" }).click();
  await expect(page).toHaveURL(/\/settings\/spending$/);
  const spend = page.getByRole("region", { name: "Where it went" });
  await expect(spend.getByRole("radio", { name: "This month" })).toHaveAttribute("aria-checked", "true");
  await spend.getByRole("radio", { name: "All time" }).click();
  // either the tables or the empty note, depending on what this database has been charged
  await expect(spend.getByText(/By feature|Nothing billed yet/).first()).toBeVisible();
});

test("Settings: a long charge label doesn't widen the page", async ({ page }) => {
  // under lg the page is one column; a long, truncated label once stretched it, and every section, past the window
  await page.route("**/budget/history**", (route) => route.fulfill({ json: { by_provider: [], entries: [
    { id: "long", created_at: new Date().toISOString(), amount_usd: 0.5, entry_type: "charge", provider: "fal",
      route_id: "mock", estimated: false, kind: "generation", label: "A huge body gliding past ".repeat(20) },
  ] } }));
  for (const width of [960, 1023, 1024, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/settings/spending");
    const heading = page.getByRole("heading", { name: "Spend history" });
    await expect(page.getByText("A huge body gliding past").first()).toBeVisible();
    const overflow = await heading.evaluate((h) => {
      const scroller = h.closest(".overflow-y-auto")!;
      return scroller.scrollWidth - scroller.clientWidth;
    });
    expect(overflow, `at ${width} px`).toBe(0);
  }
});
