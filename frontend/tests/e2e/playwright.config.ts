import { defineConfig, devices } from "@playwright/test";

/**
 * Browser acceptance tests for ZID-UX-001 §33 (UX-04, 18, 19, 20, 21, 22, 23, 24).
 *
 * They drive a RUNNING stack: the cell API (docker compose up) and the web app (npm run dev:web) at
 * http://localhost:3000. The origin must be exactly `localhost` (passkeys bind to it). Each run registers
 * its own throwaway company, so nothing needs seeding and nothing is shared between runs.
 *
 *   npm run test:e2e                       # headless
 *   npm run test:e2e -- --headed           # watch it
 *   E2E_BASE_URL=http://localhost:3000 npm run test:e2e
 */
export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  workers: 1, // the journey tests share one registered company
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { outputFolder: "../../test-results/e2e-report", open: "never" }]],
  outputDir: "../../test-results/e2e",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
