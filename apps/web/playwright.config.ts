import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against the mock provider only (PROVIDER_MODE=mock), so they cost nothing.
 * `pnpm e2e` reuses a running `pnpm dev`, or starts API + worker + web itself. To check another studio in mock mode
 * (the home server before it gets its keys), set E2E_BASE_URL to its web app and NEXT_PUBLIC_API_URL to its API.
 */
const remote = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: remote ?? "http://localhost:3000",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: remote
    ? undefined
    : { command: "pnpm --dir ../.. dev", url: "http://localhost:3000", reuseExistingServer: true, timeout: 180_000 },
});
