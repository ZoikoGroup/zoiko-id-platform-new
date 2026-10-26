import { expect, type BrowserContext, type Page } from "@playwright/test";

/** A software passkey, so the WebAuthn ceremonies run for real without a physical authenticator. */
export async function addVirtualPasskey(context: BrowserContext, page: Page): Promise<void> {
  const cdp = await context.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2",
      transport: "internal",
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
}

export type Company = { workspace: string; name: string };

/**
 * Registers a throwaway workspace, creates its first passkey and lands signed in on the overview.
 * Numbers starting 99 are answered by the local register simulator as a fresh synthetic company
 * (ZID_DEV_SIMULATORS, local/test only), so runs never collide.
 */
export async function registerCompany(page: Page): Promise<Company> {
  const suffix = Math.random().toString(36).slice(2, 8);
  const company: Company = { workspace: `e2e-${suffix}`, name: `E2E Company ${suffix}` };
  const number = `99${String(Math.floor(Math.random() * 1_000_000)).padStart(6, "0")}`;

  await page.goto("/register");
  await page.getByLabel("Company name").fill(company.name);
  await page.getByLabel("Workspace name").fill(company.workspace);
  await page.getByLabel("Company number").fill(number);
  await page.getByLabel("Your name").fill("E2E Admin");
  // The first request after a cold start can arrive before the page hydrates: retry until the box really is checked.
  const consent = page.getByRole("checkbox");
  await expect(async () => {
    if (!(await consent.isChecked())) await consent.click({ force: true });
    await expect(consent).toBeChecked({ timeout: 1_000 });
  }).toPass({ timeout: 15_000 });
  const submit = page.getByRole("button", { name: "Create workspace" });
  await expect(submit).toBeEnabled({ timeout: 15_000 });
  await submit.click();

  await page.waitForURL(/\/enroll\?/, { timeout: 20_000 });
  await page.getByRole("button", { name: /create passkey/i }).click();
  await page.waitForURL(/\/overview/, { timeout: 30_000 });
  return company;
}
