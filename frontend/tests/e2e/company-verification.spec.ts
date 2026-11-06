import { expect, test, type Page } from "@playwright/test";

import { addVirtualPasskey, registerCompany } from "./helpers";

/**
 * Company verification (WF-13B), step by step, as a newly signed-up person on the Organization journey: search the register, choose the
 * company, state your authority and the owners, submit, send for review, and read each check. Uses the local register simulator
 * (ZID_DEV_SIMULATORS): every 99xxxxxx number is a fresh synthetic company whose director is "DOE, Jane Mary".
 *
 *   E2E_SHOTS=<folder> npm run test:e2e -- company-verification
 */
const SHOTS = process.env.E2E_SHOTS;
const number = `99${Math.floor(100000 + Math.random() * 899999)}`;
let page: Page;

test.use({ actionTimeout: 20_000 });

const shot = async (name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};
const text = async (selector = "main") => ((await page.locator(selector).first().innerText().catch(() => "")) ?? "").replace(/\s+/g, " ").trim();

test("a new organization is verified step by step", async ({ browser }) => {
  test.setTimeout(300_000);
  const context = await browser.newContext();
  page = await context.newPage();
  await addVirtualPasskey(context, page);
  page.on("response", async (r) => {
    const u = new URL(r.url());
    if ((u.pathname.startsWith("/api/") || u.pathname.startsWith("/authn/")) && r.status() >= 400) console.log(`        HTTP ${r.status()} ${r.request().method()} ${u.pathname} ${(await r.text().catch(() => "")).slice(0, 200)}`);
  });
  page.on("console", (m) => { if (m.type() === "error") console.log(`        browser console: ${m.text().slice(0, 200)}`); });

  console.log("STEP 1  sign up for an organization (GB) and create a passkey");
  const account = await registerCompany(page, "For an organization");
  console.log(`        signed up as ${account.name}`);
  await shot("01-signed-up");

  console.log("STEP 2  open Company verification");
  await page.goto("/admin/organization");
  await expect(page.getByRole("heading", { name: "Company verification" }).first()).toBeVisible();
  console.log(`        page: ${(await text()).slice(0, 260)}`);
  await shot("02-company-verification");

  console.log(`STEP 3  search the register for ${number} (United Kingdom)`);
  await page.goto("/admin/organization/claim");
  await page.getByLabel("Registration number or legal name").fill(number);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByRole("list", { name: "Matching companies" })).toBeVisible({ timeout: 30_000 });
  console.log(`        found: ${(await text('[aria-label="Matching companies"]')).slice(0, 200)}`);
  await shot("03-search-results");

  console.log("STEP 4  choose the company and open the claim");
  await page.getByRole("button", { name: "Select" }).first().click();
  await page.getByRole("button", { name: "Continue with the selected company" }).click();
  await page.waitForURL(/\/claim\/[^/]+/, { timeout: 30_000 });
  console.log(`        claim page: ${page.url()}`);
  await shot("04-claim-opened");
  await page.waitForTimeout(2500);
  console.log(`        status after the company is found: ${(await text()).slice(0, 520)}`);
  await shot("04b-status-company-found");

  console.log("STEP 5  your authority (director of the company) and the owners");
  await page.goto(page.url().replace(/\/status.*$/, "/authority"));
  await expect(page.getByRole("heading", { name: "Your authority and the company's owners" })).toBeVisible();
  await page.getByLabel("Your full legal name").fill("Jane Mary Doe");
  const relationship = await text();
  console.log(`        relationship shown: ${relationship.match(/I am a director or officer[^.]*/)?.[0] ?? "(see screenshot)"}`);
  const names = page.getByLabel(/^Full legal name/);
  await names.nth(0).fill("Jane Mary Doe");
  await page.getByLabel("Birth year (optional)").nth(0).fill("1985");
  await page.getByLabel("Share of the company, %").nth(0).fill("60");
  await page.getByLabel("Share of the votes, %").nth(0).fill("60");
  await page.getByRole("button", { name: "Add another owner or controller" }).click();
  await names.nth(1).fill("Alex Smith");
  await page.getByLabel("Birth year (optional)").nth(1).fill("1980");
  await page.getByLabel("Share of the company, %").nth(1).fill("40");
  await page.getByLabel("Share of the votes, %").nth(1).fill("40");
  await shot("05-authority-and-owners");

  console.log("STEP 5b confirm where the organization's data lives (the residency baseline, needed before owners can be screened)");
  const claimUrl = page.url();
  await page.goto("/admin/residency");
  await page.waitForLoadState("networkidle");
  console.log(`        residency page: ${(await text()).slice(0, 360)}`);
  await shot("05b-residency-before");
  if (await page.getByText("No residency profile assigned").isVisible().catch(() => false)) {
    await page.getByRole("button", { name: "Change profile" }).click();
    const dlg = page.getByRole("dialog");
    await dlg.getByLabel(/^Legal mapping reference/).fill(`LM-${Date.now().toString(36)}`);
    console.log(`        residency dialog: ${(await text('[role="dialog"]')).slice(0, 500)}`);
    await shot("05c-residency-dialog");
    await dlg.getByRole("button", { name: "Confirm residency" }).click();
    const verify = page.getByRole("button", { name: "Verify with passkey" });
    if (await verify.waitFor({ state: "visible", timeout: 15_000 }).then(() => true, () => false)) {
      console.log("        passkey check asked: confirming");
      await verify.click();
    }
    await page.waitForTimeout(4000);
    console.log(`        residency after: ${(await text()).slice(0, 420)}`);
    await shot("05d-residency-after");
  }
  await page.goto(claimUrl);
  await page.waitForLoadState("networkidle");
  // the form is empty again after leaving it: fill it a second time
  await page.getByLabel("Your full legal name").fill("Jane Mary Doe");
  await page.getByLabel(/^Full legal name/).nth(0).fill("Jane Mary Doe");
  await page.getByLabel("Birth year (optional)").nth(0).fill("1985");
  await page.getByLabel("Share of the company, %").nth(0).fill("60");
  await page.getByLabel("Share of the votes, %").nth(0).fill("60");
  await page.getByRole("button", { name: "Add another owner or controller" }).click();
  await page.getByLabel(/^Full legal name/).nth(1).fill("Alex Smith");
  await page.getByLabel("Birth year (optional)").nth(1).fill("1980");
  await page.getByLabel("Share of the company, %").nth(1).fill("40");
  await page.getByLabel("Share of the votes, %").nth(1).fill("40");

  console.log("STEP 6  submit for checking");
  await page.getByRole("button", { name: "Submit for checking" }).click();
  await page.waitForTimeout(4000);
  console.log(`        after submit: ${page.url()}`);
  console.log(`        page: ${(await text()).slice(0, 700)}`);
  await shot("06-after-submit");

  console.log("STEP 7  verification status: the four checks");
  if (!/status/.test(page.url())) await page.goto(page.url().replace(/\/?$/, "") + "/status").catch(() => undefined);
  await page.waitForTimeout(2500);
  const checks = await page.getByRole("list", { name: "Verification checks" }).innerText().catch(() => "(not shown)");
  console.log(`        outcome: ${(await text()).slice(0, 400)}`);
  console.log(`        checks: ${checks.replace(/\s+/g, " ").slice(0, 600)}`);
  console.log(`        state: ${await page.getByTestId("claim-state").innerText().catch(() => "(none)")}`);
  await shot("07-status");

  const send = page.getByRole("button", { name: "Send for review" });
  if (await send.isVisible().catch(() => false)) {
    console.log("STEP 8  send for review");
    await send.click();
    await page.waitForTimeout(3500);
    console.log(`        state: ${await page.getByTestId("claim-state").innerText().catch(() => "(none)")}`);
    console.log(`        outcome: ${(await text()).slice(0, 500)}`);
    await shot("08-sent-for-review");
  } else {
    console.log("STEP 8  no 'Send for review' button on this page (see 07-status)");
  }
  await context.close();
});
