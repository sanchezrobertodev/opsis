import { expect, test } from "@playwright/test";

test("Settings: provider balances and where the money went", async ({ page }) => {
  // the balances come from the providers themselves; answer for them here so the test never leaves the machine
  await page.route("**/budget/balances**", (route) => route.fulfill({ json: [
    { provider: "fal", label: "fal.ai", status: "ok", link: "https://fal.ai/dashboard/billing", amount: 7.73,
      unit: "usd", usd: 7.73, detail: null, checked_at: new Date().toISOString() },
    { provider: "runway", label: "Runway", status: "ok", link: "https://dev.runwayml.com/", amount: 862,
      unit: "credits", usd: 8.62, detail: null, checked_at: new Date().toISOString() },
    { provider: "anthropic", label: "Anthropic (Claude)", status: "no_api", link: "https://platform.claude.com/",
      amount: null, unit: null, usd: null, detail: "no balance API; see the billing page",
      checked_at: new Date().toISOString() },
  ] }));
  await page.goto("/settings");
  const balances = page.getByRole("region", { name: "Provider balances" });
  await expect(balances.getByText("$7.73")).toBeVisible();
  await expect(balances.getByText("862 credits ($8.62)")).toBeVisible();
  await expect(balances.getByText("no balance API; see the billing page")).toBeVisible();

  const spend = page.getByRole("region", { name: "Where it went" });
  await expect(spend.getByRole("radio", { name: "This month" })).toHaveAttribute("aria-checked", "true");
  await spend.getByRole("radio", { name: "All time" }).click();
  // either the tables or the empty note, depending on what this database has been charged
  await expect(spend.getByText(/By feature|Nothing billed yet/).first()).toBeVisible();
});
