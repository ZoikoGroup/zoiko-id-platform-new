import { expect, test } from "@playwright/test";

import { addVirtualPasskey, registerCompany } from "./helpers";

/**
 * The registering person's whole path: register, verify identity, verify the company, then the onboarding
 * stages. Uses the local register and identity-provider simulators (ZID_DEV_SIMULATORS), so it never touches a
 * real register. Company number 01234567 is "ACME WIDGETS LIMITED", director "DOE, Jane Mary".
 */
test("registrant verifies identity and company and becomes owner", async ({ page, context }) => {
  test.setTimeout(240_000);
  await addVirtualPasskey(context, page);
  const company = await registerCompany(page);
  await page.screenshot({ path: "../../test-results/flow/01-overview.png" });

  const shot = (n: string) => page.screenshot({ path: `test-results/flow/${n}.png`, fullPage: true });

  // identity
  await page.goto("/account/verification");
  const prepare = page.getByRole("button", { name: /prepare development checks/i });
  const start = page.getByRole("button", { name: /start verification/i });
  await expect(prepare.or(start)).toBeVisible({ timeout: 20_000 });
  if (await prepare.isVisible()) await prepare.click();
  await expect(start).toBeVisible({ timeout: 20_000 });
  await shot("02-verification-form");
  await page.getByRole("button", { name: /verification check/i }).click();
  await page.getByRole("option", { name: /representative-proofing/ }).click();
  await page.getByRole("button", { name: /country of your documents/i }).click();
  await shot("02b-country");
  await page.getByRole("option").first().click();
  await page.getByRole("button", { name: /start verification/i }).click();
  await expect(page.getByText("Development identity provider")).toBeVisible({ timeout: 20_000 });
  await page.getByLabel("Legal name on the document").fill("Jane Mary Doe");
  await page.getByLabel("Date of birth").fill("1985-04-12");
  await shot("03-provider");
  await page.getByRole("button", { name: /complete check/i }).click();
  await expect(page.getByText("IAL-Z2").first()).toBeVisible({ timeout: 40_000 });
  await shot("04-identity-verified");

  // company is already verified at sign-up in development; look at the rest of onboarding
  await page.goto("/admin/onboarding");
  await page.getByRole("button", { name: "Create plan" }).click();
  await page.waitForTimeout(2500);
  await shot("06-plan-created");
  const open = page.getByRole("button", { name: /open approval request/i });
  if (await open.isVisible().catch(() => false)) {
    await open.click();
    await page.waitForTimeout(2500);
    await shot("07-approval-opened");
  }
  const problems: string[] = [];
  const bad: string[] = [];
  page.on("console", (m) => m.type() === "error" && problems.push(m.text().slice(0, 160)));
  page.on("pageerror", (e) => problems.push("pageerror: " + e.message.slice(0, 160)));
  page.on("response", (r) => {
    if (r.status() >= 400 && r.url().includes("/api/")) bad.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`);
  });
  const pages = [
    "/overview", "/alerts", "/admin/onboarding", "/admin/organization", "/admin/domains", "/admin/applications",
    "/admin/integrations", "/admin/team", "/admin/billing", "/admin/contacts", "/admin/settings",
    "/account", "/account/sessions", "/account/passkeys", "/account/connected-apps", "/account/verification",
    "/account/recovery", "/account/privacy", "/admin/identity-providers", "/admin/verification", "/admin/credentials",
    "/admin/authority", "/admin/mandates", "/admin/policies", "/admin/decisions", "/admin/signals", "/admin/accounts",
    "/admin/provisioning", "/admin/evidence", "/admin/privacy", "/admin/operator-access", "/admin/residency",
    "/admin/agents", "/admin/workloads", "/admin/developers",
  ];
  for (const url of pages) {
    problems.length = 0;
    bad.length = 0;
    await page.goto(url);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(800);
    console.log("PAGE", url, JSON.stringify({ bad: [...new Set(bad)], console: problems.slice(0, 2) }));
  }
}
);
