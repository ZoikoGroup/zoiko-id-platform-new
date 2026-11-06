import { generateKeyPairSync } from "node:crypto";

import { expect as baseExpect, test, type Locator, type Page } from "@playwright/test";

import { addVirtualPasskey } from "./helpers";

/**
 * Customer console, Administration group, part A: Verification, Credentials & trust, Authority & approvals, Mandates, Policies,
 * Access decisions, Security signals, People & principals. Driven as the seeded administrator with valid data, then a few invalid inputs.
 *
 *   cd backend && python -m uv run python ../tools/scripts/bootstrap_local.py --ticket-only      (a fresh one-use ticket per run)
 *   E2E_ADMIN_TICKET=<ticket> E2E_SHOTS=<folder> E2E_ONLY=<optional regex on section names> npx playwright test -c tests/e2e/playwright.config.ts pages-admin-a
 * Every section runs even if an earlier one fails; the test fails at the end if any did.
 */
const TICKET = process.env.E2E_ADMIN_TICKET;
const SHOTS = process.env.E2E_SHOTS;
const ONLY = process.env.E2E_ONLY ? new RegExp(process.env.E2E_ONLY, "i") : null;
const stamp = Date.now().toString(36);
const ME = "00000000-0000-4000-8000-0000000000b1"; // the seeded administrator's principal

// The shared dev server compiles pages on first visit and may be busy with other people, so waits are generous.
const expect = baseExpect.configure({ timeout: 45_000 });
test.use({ actionTimeout: 45_000 });

const failures: string[] = [];
let page: Page;

/** A field label as the page names it, which carries a trailing "*" when required. */
const L = (s: string) => new RegExp("^" + s.replace(/[().]/g, (c) => "\\" + c) + "(?:\\s|$)");
const shot = async (name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};
const say = (m: string) => console.log(`NOTE  ${m}`);

async function section(name: string, run: () => Promise<void>) {
  if (ONLY && !ONLY.test(name)) return;
  try {
    await test.step(name, run);
    console.log(`PASS  ${name}`);
  } catch (e) {
    const message = (e as Error).message.split("\n")[0];
    failures.push(`${name}: ${message}`);
    console.log(`FAIL  ${name}: ${message}`);
    await shot(`FAILED-${name.replace(/[^A-Za-z0-9]+/g, "-").slice(0, 50)}`).catch(() => undefined);
    await page.keyboard.press("Escape").catch(() => undefined);
  }
}

async function visit(path: string) {
  await page.goto(path, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => undefined);
  await expect(page.getByRole("main")).toBeVisible();
}

/** Ticks a react-aria checkbox, whose input is visually hidden: click the label it sits in, as a person would. */
async function tick(box: Locator) {
  await box.locator("xpath=ancestor::label[1]").click();
  await expect(box).toBeChecked();
}

/** Walks a guided change (intent, impact, preconditions, approval, commit): acknowledges what it asks, continues, and commits. */
async function walkGuided(commitLabel: string) {
  const dialog = page.locator('[role="dialog"], [role="alertdialog"]').first();
  for (let step = 0; step < 8; step++) {
    const commit = dialog.getByRole("button", { name: commitLabel });
    const next = dialog.getByRole("button", { name: /^Continue/ });
    await expect(next.or(commit)).toBeVisible();
    // Acknowledge everything asked first: the commit button is shown (disabled) before the acknowledgement is ticked.
    for (const box of await dialog.getByRole("checkbox").all()) if (!(await box.isChecked())) await tick(box);
    if (await commit.isVisible()) {
      await commit.click();
      await dialog.getByRole("button", { name: "Done" }).click();
      return;
    }
    await next.click();
  }
  throw new Error(`never reached "${commitLabel}"`);
}

/** Chooses an option in a react-aria Select by its label. */
async function pick(scope: Page | Locator, label: string | RegExp, option: string | RegExp) {
  await scope.getByRole("button", { name: label }).first().click();
  await page.getByRole("option", { name: option }).first().click();
}

function publicJwks(kid: string) {
  const { publicKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
  return JSON.stringify({ keys: [{ ...publicKey.export({ format: "jwk" }), kid, alg: "ES256", use: "sig" }] });
}

test("part A admin pages work end to end for an administrator", async ({ browser }) => {
  test.skip(!TICKET, "set E2E_ADMIN_TICKET to the seeded administrator's enrollment ticket");
  test.setTimeout(2_700_000);
  const context = await browser.newContext();
  page = await context.newPage();
  await addVirtualPasskey(context, page);
  page.on("response", async (r) => {
    const method = r.request().method();
    if (r.url().includes("/api/") && r.status() >= 400) {
      console.log(`HTTP  ${r.status()} ${method} ${new URL(r.url()).pathname} ${(await r.text().catch(() => "")).slice(0, 220)}`);
    }
  });
  // Whenever the console asks to "Confirm it's you" (a critical write needs a fresh passkey proof), confirm it with the software passkey.
  await page.addLocatorHandler(page.getByRole("button", { name: "Verify with passkey" }), async (b) => {
    console.log("NOTE  step-up prompt shown: confirming with the passkey");
    await b.click();
    await page.waitForTimeout(1500);
  });
  page.on("pageerror", (e) => console.log(`PAGEERR ${e.message.slice(0, 160)}`));

  await page.goto(`/enroll?ticket=${TICKET}`);
  await page.getByRole("button", { name: "Create passkey" }).click();
  await page.waitForURL(/\/(account|overview|admin)/, { timeout: 120_000, waitUntil: "domcontentloaded" });

  // ---------------------------------------------------------------- Verification
  const profile = `e2e-profile-${stamp}`;
  await section("verification: create a profile, see it as a draft, activate it", async () => {
    await visit("/admin/verification");
    await page.getByRole("button", { name: "New profile" }).click();
    const d = page.getByRole("dialog");
    await d.getByLabel(L("Name")).fill(profile);
    await d.getByLabel(L("Jurisdictions")).fill("GB");
    await shot("v10-profile-form");
    await d.getByRole("button", { name: "Create draft" }).click();
    await expect(page.getByText(`Profile “${profile}” v1 created as a draft`).first()).toBeVisible();
    const row = page.getByRole("row", { name: new RegExp(profile) });
    await expect(row.getByText("Draft").first()).toBeVisible();
    await shot("v11-profile-draft");
    await row.getByRole("button", { name: "Activate" }).click();
    await walkGuided("Activate profile");
    await expect(row.getByText("Active").first()).toBeVisible();
    await expect(row.getByRole("button", { name: "Activate" })).toHaveCount(0);
    await shot("v12-profile-active");
  });
  await section("verification: invalid profile inputs are refused and nothing is stored", async () => {
    await visit("/admin/verification");
    await page.getByRole("button", { name: "New profile" }).click();
    const d = page.getByRole("dialog");
    await d.getByLabel(L("Name")).fill(`e2e-bad-${stamp}`);
    await d.getByLabel(L("Jurisdictions")).fill("United Kingdom");
    await d.getByRole("button", { name: "Create draft" }).click();
    await expect(d.getByText(/not a two-letter country code/)).toBeVisible();
    await d.getByLabel(L("Jurisdictions")).fill("");
    await d.getByRole("button", { name: "Create draft" }).click();
    await expect(d.getByText("Add at least one jurisdiction.")).toBeVisible();
    await d.getByLabel(L("Jurisdictions")).fill("GB");
    await d.getByLabel(L("Freshness (days)")).fill("0");
    await d.getByRole("button", { name: "Create draft" }).click();
    await expect(d.getByText(/whole number of days/)).toBeVisible();
    await shot("v20-profile-invalid");
    await d.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("row", { name: new RegExp(`e2e-bad-${stamp}`) })).toHaveCount(0);
  });
  const adapter = `e2e-idv-${stamp}`;
  await section("verification: register an adapter, then quarantine it", async () => {
    await visit("/admin/verification");
    await page.getByRole("tab", { name: "Adapters" }).click();
    await page.getByRole("button", { name: "Register adapter" }).click();
    const d = page.getByRole("dialog");
    await d.getByLabel(L("Adapter ID")).fill(adapter);
    await d.getByLabel(L("Provider")).fill("Acme IDV (e2e)");
    await d.getByLabel(L("Equivalence group")).fill(`grp-${stamp}`);
    await d.getByLabel(L("Submit endpoint")).fill("https://api.example.com/verifications");
    await d.getByLabel(L("Jurisdictions")).fill("GB");
    await d.getByLabel(L("Callback JWKS")).fill(publicJwks(`k-${stamp}`));
    await d.getByRole("button", { name: "Register adapter" }).click();
    await expect(page.getByRole("dialog", { name: "Adapter registered" })).toBeVisible();
    await shot("v30-adapter-registered");
    await page.getByRole("button", { name: "Done" }).click();
    const row = page.getByRole("row", { name: new RegExp(adapter) });
    await expect(row).toBeVisible();
    await shot("v31-adapter-listed");
    await row.getByRole("button", { name: "Quarantine" }).click();
    await walkGuided("Quarantine adapter");
    await expect(row.getByText(/Quarantined/i).first()).toBeVisible();
    await shot("v32-adapter-quarantined");
  });
  await section("verification: invalid adapter inputs are refused", async () => {
    await visit("/admin/verification");
    await page.getByRole("tab", { name: "Adapters" }).click();
    await page.getByRole("button", { name: "Register adapter" }).click();
    const d = page.getByRole("dialog");
    await d.getByLabel(L("Adapter ID")).fill("Bad ID!");
    await d.getByLabel(L("Provider")).fill("X");
    await d.getByLabel(L("Equivalence group")).fill("g");
    await d.getByLabel(L("Submit endpoint")).fill("http://insecure.example.com");
    await d.getByLabel(L("Jurisdictions")).fill("GB");
    await d.getByLabel(L("Callback JWKS")).fill(publicJwks("k"));
    await d.getByRole("button", { name: "Register adapter" }).click();
    await expect(d.getByText(/Adapter ID: 2/)).toBeVisible();
    await d.getByLabel(L("Adapter ID")).fill(`e2e-bad-${stamp}`);
    await d.getByRole("button", { name: "Register adapter" }).click();
    await expect(d.getByText(/must be an https/)).toBeVisible();
    await d.getByLabel(L("Submit endpoint")).fill("https://api.example.com/v");
    await d.getByLabel(L("Callback JWKS")).fill("not json");
    await d.getByRole("button", { name: "Register adapter" }).click();
    await shot("v40-adapter-invalid");
    await expect(d.getByRole("alert").first()).toBeVisible();
    await d.getByRole("button", { name: "Cancel" }).click();
  });
  await section("verification: review decision (bad id, unknown case) and reliance decision", async () => {
    await visit("/admin/verification");
    await page.getByRole("tab", { name: "Review & reliance" }).click();
    await page.getByLabel(L("Review case ID")).fill("not-a-uuid");
    await page.getByLabel(L("Reason codes")).fill("DOCUMENT_MATCHES");
    await page.getByRole("button", { name: "Record decision" }).click();
    await shot("v50-review-invalid");
    await expect(page.getByRole("alert").first()).toBeVisible();
    await page.getByLabel(L("Review case ID")).fill("0190a000-0000-7000-8000-000000000999");
    await page.getByRole("button", { name: "Record decision" }).click();
    await page.waitForTimeout(1500);
    await shot("v51-review-unknown-case");
    await page.getByLabel(L("Subject principal ID")).fill(ME);
    await page.getByLabel(L("Purpose")).fill("account_opening");
    await page.getByLabel(L("Claims")).fill("given_name, birthdate");
    await page.getByLabel(L("Jurisdiction (optional)")).fill("GB");
    await page.getByRole("button", { name: "Decide reliance" }).click();
    await page.waitForTimeout(2000);
    await shot("v52-reliance");
    say("RELIANCE-RESULT " + (await page.getByRole("status").allInnerTexts()).join(" | ").replace(/\s+/g, " ").slice(0, 300));
  });
  await section("verification: principal lookup", async () => {
    await visit("/admin/verification");
    await page.getByRole("tab", { name: "Principal lookup" }).click();
    await page.getByLabel(L("Principal ID")).fill("abc");
    await page.keyboard.press("Enter");
    await expect(page.getByText("Principal ID must be a UUID.")).toBeVisible();
    await page.getByLabel(L("Principal ID")).fill(ME);
    await page.keyboard.press("Enter");
    await expect(page.getByText("Current assurance")).toBeVisible();
    await page.waitForTimeout(1500);
    await shot("v60-principal-assurance");
  });

  // ---------------------------------------------------------------- Credentials & trust
  const credType = `https://credentials.example.com/employee-${stamp}`;
  const credRe = new RegExp(credType.replace(/[./]/g, "\\$&"));
  await section("credentials: create a configuration draft, activate it, refuse an empty one", async () => {
    await visit("/admin/credentials");
    await page.getByRole("tab", { name: "Configurations" }).click();
    await page.getByRole("button", { name: "New configuration" }).click();
    const d = page.getByRole("dialog");
    await d.getByLabel(L("Credential type")).fill(credType);
    await d.getByRole("button", { name: "Create draft" }).click();
    await expect(d.getByText("Add at least one required claim.")).toBeVisible();
    await shot("c10-config-invalid");
    await d.getByLabel(L("Required claims")).fill("given_name, family_name");
    await d.getByLabel(L("Optional claims")).fill("department");
    await d.getByRole("button", { name: "Create draft" }).click();
    await expect(page.getByText("Credential configuration created as a draft").first()).toBeVisible();
    const row = page.getByRole("row", { name: credRe });
    await expect(row.getByText("Draft").first()).toBeVisible();
    await shot("c11-config-draft");
    await row.getByRole("button", { name: "Activate" }).click();
    await walkGuided(/Activate/);
    await expect(row.getByText("Active").first()).toBeVisible();
    await shot("c12-config-active");
  });
  await section("credentials: trust an issuer with pinned keys, rotate keys, suspend, reactivate", async () => {
    await visit("/admin/credentials");
    await page.getByRole("tab", { name: "Issuer trust" }).click();
    await page.getByRole("button", { name: "Add issuer" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByLabel(L("Issuer identifier")).fill(`https://issuer-${stamp}.example.com`);
    await d.getByRole("button", { name: "Add issuer" }).click();
    await expect(d.getByText("Add at least one credential type.")).toBeVisible();
    await d.getByLabel(L("Credential types")).fill(credType);
    await d.getByLabel(L("Jurisdictions (optional)")).fill("GB");
    await d.getByRole("button", { name: "Add issuer" }).click();
    await expect(d.getByText(/Pin a JWKS or at least one IACA/)).toBeVisible();
    await d.getByLabel(L("JWKS")).fill(publicJwks(`ik-${stamp}`));
    await shot("c20-trust-form");
    await d.getByRole("button", { name: "Add issuer" }).click();
    const row = page.getByRole("row", { name: new RegExp(`issuer-${stamp}`) });
    await expect(row).toBeVisible();
    await shot("c21-trust-listed");
    await row.getByRole("button", { name: "Rotate keys" }).click();
    await page.getByRole("dialog").getByLabel(L("New JWKS")).fill(publicJwks(`ik2-${stamp}`));
    await page.getByRole("button", { name: "Review change" }).click();
    await walkGuided(/Replace keys/);
    await shot("c22-trust-rotated");
    await row.getByRole("button", { name: "Change status" }).click();
    await page.getByRole("dialog").getByText("Suspended", { exact: true }).click();
    await page.getByRole("button", { name: "Review change" }).click();
    await walkGuided("Change status");
    await expect(row.getByText(/Suspended/i).first()).toBeVisible();
    await shot("c23-trust-suspended");
    await row.getByRole("button", { name: "Change status" }).click();
    await page.getByRole("dialog").getByText("Active", { exact: true }).click();
    await page.getByRole("button", { name: "Review change" }).click();
    await walkGuided("Change status");
    await expect(row.getByText(/Active/i).first()).toBeVisible();
  });
  const presPurpose = `e2e_age_${stamp}`;
  const CONSOLE_CLIENT = process.env.E2E_CONSOLE_CLIENT_ID ?? "zoiko-web"; // ZID_OAUTH_CLIENT_ID in apps/web/.env.local
  await section("credentials: register a relying party, refuse bad retention", async () => {
    await visit("/admin/credentials");
    await page.getByRole("tab", { name: "Relying parties" }).click();
    await page.getByRole("button", { name: "Register relying party" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByLabel(L("Client ID")).fill(`e2e-rp-${stamp}`);
    await d.getByLabel(L("Purpose")).fill("age_verification");
    await d.getByLabel(L("Allowed attributes")).fill("age_over_18, given_name");
    await d.getByLabel(L("Retention (days)")).fill("99999");
    await d.getByRole("button", { name: "Register", exact: true }).click();
    await expect(d.getByText("Retention must be 0–3650 days.")).toBeVisible();
    await d.getByLabel(L("Retention (days)")).fill("30");
    await d.getByRole("button", { name: "Register", exact: true }).click();
    await expect(page.getByRole("row", { name: new RegExp(`e2e-rp-${stamp}`) })).toBeVisible();
    await shot("c30-rp-listed");
    // Presentation requests are made by the console's own OAuth client, so it must be registered for the purpose the next section uses.
    await page.getByRole("button", { name: "Register relying party" }).first().click();
    const d2 = page.getByRole("dialog");
    await d2.getByLabel(L("Client ID")).fill(CONSOLE_CLIENT);
    await d2.getByLabel(L("Purpose")).fill(presPurpose);
    await d2.getByLabel(L("Allowed attributes")).fill("given_name, family_name");
    await d2.getByLabel(L("Retention (days)")).fill("30");
    await d2.getByRole("button", { name: "Register", exact: true }).click();
    await expect(page.getByRole("row", { name: new RegExp(presPurpose) })).toBeVisible();
  });
  await section("credentials: presentation request, issued list filter", async () => {
    await visit("/admin/credentials");
    await page.getByRole("tab", { name: "Presentations" }).click();
    await page.getByRole("button", { name: "Create request" }).click();
    await expect(page.getByText("Choose an active credential configuration.")).toBeVisible();
    await pick(page, /Credential configuration/, credRe);
    await page.getByLabel(L("Purpose")).fill(presPurpose);
    await page.getByLabel(L("Jurisdiction")).fill("GB");
    await page.getByLabel(L("Claims")).fill("given_name");
    await page.getByRole("button", { name: "Create request" }).click();
    await expect(page.getByText(/Request ready|Something went wrong|not completed/).first()).toBeVisible();
    await shot("c40-presentation");
    say("PRESENTATION " + (await page.getByRole("main").innerText()).replace(/\s+/g, " ").match(/(Request ready|Something went wrong|not completed)[^.]{0,200}/i)?.[0]);
    await expect(page.getByText("Request ready")).toBeVisible();
    await visit("/admin/credentials");
    await pick(page, /statuses/, "Valid");
    await page.waitForTimeout(800);
    await shot("c41-issued-filter");
  });

  // ---------------------------------------------------------------- Policies
  const pol = `e2e-pol-${stamp}`;
  await section("policies: create a draft, validate, simulate, try to review and publish", async () => {
    await visit("/admin/policies");
    await page.getByRole("button", { name: "New release" }).click();
    let d = page.getByRole("dialog");
    await d.getByLabel(L("Policy name")).fill("Bad Name");
    await d.getByRole("button", { name: "Save draft" }).click();
    await page.waitForTimeout(800);
    say("POLICY bad-name feedback: " + ((await d.getByRole("alert").allInnerTexts()).join("|") || "NONE shown"));
    await shot("p10-bad-name");
    await d.getByLabel(L("Policy name")).fill(pol);
    await d.getByRole("button", { name: "Save draft" }).click();
    await expect(page.getByText("Draft created").first()).toBeVisible();
    d = page.getByRole("dialog", { name: "Policy release" });
    await expect(d.getByText("Draft", { exact: true }).first()).toBeVisible();
    await shot("p11-draft");
    await d.getByRole("button", { name: "Validate" }).click();
    await expect(d.getByText(/All checks passed|Blocking checks failed/)).toBeVisible();
    await shot("p12-validated");
    say("POLICY validate: " + (await d.getByText(/All checks passed|Blocking checks failed/).first().innerText()));
    await d.getByRole("button", { name: "Simulate" }).click();
    await expect(d.getByText("This release")).toBeVisible();
    await shot("p13-simulated");
    if (await d.getByRole("button", { name: "Approve as reviewer" }).isVisible()) {
      await d.getByRole("button", { name: "Approve as reviewer" }).click();
      // Four-eyes rule (ENVIRONMENT LIMIT for going further): the author cannot review their own release and a second administrator does not exist locally.
      await expect(d.getByText("the author cannot review their own release")).toBeVisible();
      say("POLICY review by author: " + ((await d.getByRole("alert").allInnerTexts()).join("|").replace(/\s+/g, " ") || "no alert"));
      await d.getByRole("button", { name: "Publish" }).click();
      await expect(d.getByText("independent reviewer(s) missing")).toBeVisible();
      say("POLICY publish without reviewer: " + ((await d.getByRole("alert").allInnerTexts()).join("|").replace(/\s+/g, " ") || "no alert"));
      await shot("p14-review-publish");
    } else say("POLICY: release did not reach REVIEW after validate");
    await d.getByRole("button", { name: "Close" }).last().click();
    await expect(page.getByRole("row", { name: new RegExp(pol) })).toBeVisible();
  });
  await section("policies: invalid case JSON and invalid cedar are refused", async () => {
    await visit("/admin/policies");
    await page.getByRole("button", { name: "New release" }).click();
    const d = page.getByRole("dialog");
    await d.getByLabel(L("Policy name")).fill(`e2e-bad-${stamp}`);
    await d.getByLabel(L("Test cases (JSON)")).fill("[ not json");
    await expect(d.getByRole("button", { name: "Save draft" })).toBeDisabled();
    await shot("p20-bad-json");
    await d.getByLabel(L("Test cases (JSON)")).fill("[]");
    await expect(d.getByText("Give at least one allow and one deny case.")).toBeVisible();
    await d.getByRole("button", { name: "Cancel" }).click();
    await page.getByRole("button", { name: "New release" }).click();
    const d2 = page.getByRole("dialog");
    await d2.getByLabel(L("Policy name")).fill(`e2e-badcedar-${stamp}`);
    await d2.getByLabel(L("Cedar policies")).fill("permit (principal, action, resource) when {");
    await d2.getByRole("button", { name: "Save draft" }).click();
    await page.waitForTimeout(2000);
    await shot("p21-bad-cedar");
    say("POLICY bad cedar: " + ((await page.getByRole("alert").allInnerTexts()).join("|").replace(/\s+/g, " ").slice(0, 300) || "no alert; saved as draft?"));
    await page.keyboard.press("Escape");
  });

  // ---------------------------------------------------------------- Access decisions
  await section("decisions: evaluate a payment as myself (valid), then bad inputs", async () => {
    await visit("/admin/decisions");
    await page.getByRole("button", { name: "Evaluate" }).click();
    // Required fields use the browser's own validation, so nothing is sent and no evaluation is recorded.
    await expect(page.getByText("No evaluations yet")).toBeVisible();
    await expect(page.getByText("No decision yet")).toBeVisible();
    await pick(page, /What do they want to do/, "Release a payment");
    await page.getByLabel(L("Applies to: name or number")).fill(`inv-${stamp}`);
    await page.getByLabel(L("Amount")).fill("300.00");
    await shot("d10-form");
    await page.getByRole("button", { name: "Evaluate" }).click();
    await expect(page.getByText(/Outcome:/).first()).toBeVisible();
    await shot("d11-result");
    say("DECISION payment: " + (await page.getByRole("main").innerText()).replace(/\s+/g, " ").match(/Decision.{0,500}/)?.[0]);
    await expect(page.getByRole("list", { name: "Recent evaluations" })).toBeVisible();
    await page.getByLabel(L("Amount")).fill("abc");
    await page.getByRole("button", { name: "Evaluate" }).click();
    await expect(page.getByText("The amount must be a number such as 300.00.")).toBeVisible();
    await shot("d12-bad-amount");
  });
  await section("decisions: a read-sensitive-data evaluation and the recent list", async () => {
    await visit("/admin/decisions");
    await pick(page, /What do they want to do/, "Read sensitive data");
    await page.getByLabel(L("Applies to: name or number")).fill(`rec-${stamp}`);
    await page.getByRole("button", { name: "Evaluate" }).click();
    await expect(page.getByText(/Outcome:/).first()).toBeVisible();
    await shot("d20-sensitive");
    say("DECISION read-sensitive: " + (await page.getByRole("main").innerText()).replace(/\s+/g, " ").match(/Decision.{0,500}/)?.[0]);
    await expect(page.getByRole("list", { name: "Recent evaluations" })).toBeVisible();
  });

  // ---------------------------------------------------------------- Signals
  const fakeSubject = crypto.randomUUID();
  await section("signals: publish a canonical signal for a made-up principal and read its state", async () => {
    await visit("/admin/signals");
    await page.getByRole("tab", { name: "Publish signal" }).click();
    await page.getByLabel(L("Subject ID")).fill("not-a-uuid");
    await page.getByRole("button", { name: /Publish/ }).last().click();
    await page.waitForTimeout(600);
    await shot("s10-bad-subject");
    await page.getByLabel(L("Subject ID")).fill(fakeSubject);
    await page.getByRole("button", { name: /Publish/ }).last().click();
    await page.waitForTimeout(2500);
    await shot("s11-published");
    say("SIGNAL publish: " + (await page.getByRole("main").innerText()).replace(/\s+/g, " ").match(/Result.{0,300}/)?.[0]);
    await page.getByRole("tab", { name: "Security state" }).click();
    await page.getByLabel(L("Subject ID")).fill(fakeSubject);
    await page.getByRole("button", { name: /Look up|Search|Find/ }).first().click();
    await page.waitForTimeout(2000);
    await shot("s12-state");
    say("SIGNAL state: " + (await page.getByRole("main").innerText()).replace(/\s+/g, " ").match(/Security state.{0,300}/)?.[0]);
  });
  await section("signals: create a receiver stream, see the one-time credential", async () => {
    await visit("/admin/signals");
    await page.getByRole("button", { name: "Create stream" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByLabel(L("Audience")).fill(`https://receiver-${stamp}.example.com`);
    await d.getByRole("button", { name: "Create stream" }).click();
    await expect(d.getByText("A receiver stream pins the transmitter's issuer.")).toBeVisible();
    await shot("s20-invalid");
    await d.getByLabel(L("Transmitter issuer")).fill(`https://idp-${stamp}.example.com`);
    await d.getByLabel(L("Transmitter JWKS")).fill(publicJwks(`sk-${stamp}`));
    await d.getByRole("button", { name: "Create stream" }).click();
    // The passkey confirmation (handled above) may be shown first; the one-time push credential follows.
    const done = page.getByRole("dialog", { name: "Stream push credential" });
    await expect(done).toBeVisible({ timeout: 150_000 }); // the first call after a passkey confirmation can wait for the dev server to compile
    await shot("s21-created");
    await done.getByRole("button", { name: "Reveal" }).click();
    await expect(done.getByLabel("Push authorization", { exact: true })).toHaveText(/^Bearer \S{20,}/);
    say("SIGNAL stream credential dialog: " + (await done.innerText()).replace(/\s+/g, " ").slice(0, 200));
    await tick(done.getByRole("checkbox"));
    await done.getByRole("button", { name: "Done" }).click();
    await expect(done).toHaveCount(0);
    await expect(page.getByText(`https://receiver-${stamp}.example.com`).first()).toBeVisible();
  });

  // ---------------------------------------------------------------- Authority & approvals
  await section("authority: invalid grant form is refused", async () => {
    await visit("/admin/authority");
    await page.getByRole("button", { name: "Issue grant" }).click();
    const d = page.getByRole("dialog");
    await d.getByRole("button", { name: "Issue grant" }).click();
    await expect(d.getByText("Choose the grantee principal.")).toBeVisible();
    await shot("a10-invalid");
    await d.getByRole("button", { name: "Cancel" }).click();
  });
  await section("authority: issue a narrow grant to myself", async () => {
    await visit("/admin/authority");
    await page.getByRole("button", { name: "Issue grant" }).click();
    const d = page.getByRole("dialog");
    await pick(d, /Grantee/, /Human · 00000000…00b1/);
    await d.getByLabel(L("Value limit")).fill("250.00");
    await pick(d, /Currency/, "GBP");
    await tick(d.getByRole("checkbox", { name: /Create a purchase/ }));
    await tick(d.getByRole("checkbox", { name: /Any purchase/ }));
    // "Any purchase" is purchase:*, a wide scope: the form asks for it to be reviewed before it can be issued.
    await expect(d.getByText("purchase:* covers every resource", { exact: false }).or(d.getByText(/covers every resource of that type/))).toBeVisible();
    await shot("a20-form");
    await d.getByRole("button", { name: "Issue grant" }).click();
    await expect(d.getByText(/This grant is broad/)).toBeVisible();
    await tick(d.getByRole("checkbox", { name: /I have reviewed this scope/ }));
    await d.getByRole("button", { name: "Issue grant" }).click();
    await expect(page.getByText("Grant issued").first()).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await shot("a21-issued");
  });
  await section("authority: approvals tab, new request form", async () => {
    await visit("/admin/authority");
    await page.getByRole("tab", { name: "Approvals" }).click();
    await page.getByRole("button", { name: "New request" }).click();
    const d = page.getByRole("dialog");
    await shot("a30-approval-form");
    say("APPROVAL form: " + (await d.innerText()).replace(/\s+/g, " ").slice(0, 400));
    await d.getByRole("button", { name: "Cancel" }).click();
  });

  // ---------------------------------------------------------------- Mandates
  await section("mandates: invalid form refused", async () => {
    await visit("/admin/mandates");
    await page.getByRole("button", { name: "Create mandate" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByRole("button", { name: "Create mandate" }).click();
    await page.waitForTimeout(500);
    await shot("m10-invalid");
    say("MANDATE empty form: " + ((await d.getByRole("alert").allInnerTexts()).join("|").replace(/\s+/g, " ") || "none"));
    await d.getByRole("button", { name: "Cancel" }).click();
  });

  // ---------------------------------------------------------------- People & principals
  await section("accounts: list, filter by type and status, open a principal", async () => {
    await visit("/admin/accounts");
    await shot("u10-list");
    await page.getByRole("combobox", { name: "Type" }).selectOption({ label: "Type: Human" });
    await page.waitForTimeout(800);
    await page.getByRole("combobox", { name: "Status" }).selectOption({ label: "Status: Active" });
    await page.waitForTimeout(800);
    await shot("u11-filtered");
    await expect(page.getByRole("row").filter({ hasText: "Suspended" })).toHaveCount(0);
    await expect(page.getByRole("row").filter({ hasText: "Application" })).toHaveCount(0);
    await page.getByRole("row").filter({ hasText: "0000000000b1" }).first().click();
    await expect(page.getByText("Human principal")).toBeVisible();
    await expect(page.getByText("Revoke all sessions")).toBeVisible();
    await shot("u12-detail");
    say("ACCOUNTS detail: " + (await page.getByRole("main").innerText()).replace(/\s+/g, " ").slice(0, 500));
  });


  await context.close();
  console.log(`SUMMARY ${failures.length} failed sections`);
  expect(failures, failures.join("\n")).toEqual([]);
});
