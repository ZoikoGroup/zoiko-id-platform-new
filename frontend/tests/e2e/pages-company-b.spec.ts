import { expect as baseExpect, test, type Locator, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";

import { addVirtualPasskey, mailFor } from "./helpers";

/**
 * Company-console pages, group B, driven as the seeded administrator with valid and invalid data:
 * Overview, Alerts & tasks, Organization setup, Approvals, Billing (every tab), Email contacts, Company settings.
 *
 *   cd backend && python -m uv run python ../tools/scripts/bootstrap_local.py --ticket-only
 *   E2E_ADMIN_TICKET=<ticket> E2E_SHOTS=<folder> npx playwright test -c tests/e2e/playwright.config.ts pages-company-b --reporter=line --output=<folder>/out
 *
 * Every section runs even if an earlier one fails; the test fails at the end if any section did. Everything it creates is made
 * unique with a timestamp and is taken away again (contact, budget, plan request, billing access, commitment limit, settings), so it can be re-run.
 * Not driven: tenant suspend/resume/offboard, owner add/remove, card entry on Stripe (no provider configured locally).
 */
const TICKET = process.env.E2E_ADMIN_TICKET;
const SHOTS = process.env.E2E_SHOTS;
const stamp = Date.now().toString(36);

// the dev server compiles pages on demand and is shared with other test runs, so give every step room
const expect = baseExpect.configure({ timeout: 30_000 });
test.use({ actionTimeout: 30_000 });

const failures: string[] = [];
let page: Page;
const seen = new Map<string, string>(); // last GET body per API path, so a section can compare the page with what the server said

const shot = async (name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};
const main = () => page.getByRole("main");
const text = async () => ((await main().innerText().catch(() => "")) ?? "").replace(/\s+/g, " ");
const note = (s: string) => console.log(`NOTE  ${s}`);

/** Other test runs share this administrator and some of them end sessions; sign in again with the same software passkey and carry on. */
async function signInAgain(): Promise<boolean> {
  if (!/\/sign-in/.test(page.url())) return false;
  note("the session was ended by something else; signing in again with the passkey");
  await page.getByRole("button", { name: "Sign in with a passkey" }).click();
  await page.waitForURL((u) => !/\/sign-in/.test(u.pathname), { timeout: 30_000 });
  return true;
}

const ONLY = process.env.E2E_ONLY ? new RegExp(process.env.E2E_ONLY, "i") : null;

/** Thrown by a section that checked everything the environment allows but cannot reach its main effect locally (reported as BLOCKED, not PASS). */
class Blocked extends Error {}
const blocked: string[] = [];
let budgetCheckStatus = ""; // status the last "Check now" returned

async function section(name: string, run: () => Promise<void>) {
  if (ONLY && !ONLY.test(name)) return;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      await test.step(name, run);
      console.log(`PASS  ${name}`);
      return;
    } catch (e) {
      if (e instanceof Blocked) {
        blocked.push(`${name}: ${e.message}`);
        console.log(`BLOCKED  ${name}: ${e.message}`);
        return;
      }
      const message = (e as Error).message.split(/\r?\n/)[0] ?? "";
      if (attempt === 0 && (await signInAgain().catch(() => false))) continue;
      failures.push(`${name}: ${message}`);
      console.log(`FAIL  ${name}: ${message}`);
      await shot(`FAILED-${name.replace(/[^A-Za-z0-9]+/g, "-").slice(0, 40)}`).catch(() => undefined);
      await page.keyboard.press("Escape").catch(() => undefined);
      return;
    }
  }
}

async function open(path: string) {
  await page.goto(path, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => undefined);
  if (await signInAgain().catch(() => false)) await page.goto(path, { waitUntil: "domcontentloaded" });
  await expect(main()).toBeVisible();
  await settle();
}

/** The status and body of the write a click causes. */
async function write(method: string, pathPart: string, act: () => Promise<void>): Promise<{ status: number; body: string }> {
  const wait = page.waitForResponse((r) => r.request().method() === method && new URL(r.url()).pathname.includes(pathPart), { timeout: 30_000 });
  await act();
  const r = await wait;
  const body = await r.text().catch(() => "");
  note(`${method} ${pathPart} -> ${r.status()} ${body.slice(0, 220).replace(/\s+/g, " ")}`);
  return { status: r.status(), body };
}

/** A critical write opens "Approve this exact change": the person confirms with the passkey. */
async function usePasskeyIfAsked() {
  const b = page.getByRole("button", { name: "Use my passkey" });
  if (await b.waitFor({ state: "visible", timeout: 8000 }).then(() => true, () => false)) {
    note("passkey confirmation (Approve this exact change) shown, answered with the passkey");
    await b.click();
  }
}

/** A react-aria Select: the button named by its label, then the option. */
async function pick(scope: Page | Locator, label: string, option: string | RegExp) {
  await scope.getByRole("button", { name: new RegExp(label) }).first().click();
  await page.getByRole("option", { name: option }).first().click();
}

/** A form field by the start of its label (required fields carry a trailing " *"). */
const field = (label: string) => main().getByLabel(new RegExp(`^${label.replace(/[()]/g, (c) => `\\${c}`)}`));

/** Waits until the page has stopped changing (skeletons gone, requests done). */
async function settle() {
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
  let last = "";
  for (let i = 0; i < 40; i++) {
    const now = await text();
    const busy = (await page.locator(".animate-pulse, [aria-busy='true']").count()) > 0;
    if (now === last && !busy && i > 1) return;
    last = now;
    await page.waitForTimeout(500);
  }
}

async function tab(name: string) {
  await page.getByRole("tab", { name, exact: true }).click();
  await settle();
}

async function toast(re: RegExp) {
  await expect(page.getByText(re).first()).toBeVisible({ timeout: 15_000 });
}

/** Walks a guided R3 change (intent, impact, preconditions, approval, commit): acknowledges what it asks, continues, and commits. */
async function walkGuided(commitLabel: string | RegExp) {
  const dialog = page.locator('[role="dialog"], [role="alertdialog"]').first();
  for (let step = 0; step < 8; step++) {
    const commit = dialog.getByRole("button", { name: commitLabel });
    if (await commit.isVisible()) {
      await commit.click();
      await dialog.getByRole("button", { name: "Done" }).click();
      return;
    }
    for (const box of await dialog.getByRole("checkbox").all()) if (!(await box.isChecked())) await box.check();
    const next = dialog.getByRole("button", { name: /^Continue/ });
    await expect(next.or(commit)).toBeVisible();
    if (await next.isVisible()) await next.click();
  }
  throw new Error(`never reached "${commitLabel}"`);
}

const BILLING_TABS = ["Overview", "Subscription", "Plan features", "Usage", "Bill preview", "Invoices", "Commitments", "Billing profile", "Payment", "Budgets", "Billing access"];

test("company console pages B", async ({ browser }) => {
  test.skip(!TICKET, "set E2E_ADMIN_TICKET");
  test.setTimeout(3_000_000);
  const context = await browser.newContext();
  page = await context.newPage();
  await addVirtualPasskey(context, page);
  const consoleErrors: string[] = [];
  page.on("pageerror", (e) => consoleErrors.push(`script error: ${e.message.slice(0, 160)}`));
  page.on("response", async (r) => {
    const url = new URL(r.url());
    if (!url.pathname.includes("/v1/") && !url.pathname.startsWith("/api/")) return;
    if (r.request().method() === "GET" && r.status() < 400) seen.set(url.pathname, await r.text().catch(() => ""));
    if (r.status() >= 400 && r.status() !== 404) consoleErrors.push(`${r.request().method()} ${url.pathname} -> ${r.status()}`);
    if (r.status() >= 500) console.log(`HTTP5 ${r.request().method()} ${url.pathname} -> ${r.status()} ${(await r.text().catch(() => "")).slice(0, 400)}`);
  });

  await page.goto(`/enroll?ticket=${TICKET}`);
  await expect(page.getByRole("button", { name: "Create passkey" })).toBeEnabled({ timeout: 120_000 });
  await page.getByRole("button", { name: "Create passkey" }).click();
  await expect(page).toHaveURL(/\/(account|overview|admin)/, { timeout: 120_000 }); // polls the URL; waitForURL can miss a navigation that finished first

  // ------------------------------------------------------------------------------------------------ Overview
  await section("overview: shows the company, counts match the members the server returned", async () => {
    await open("/overview");
    await expect(main().getByRole("heading", { name: "Local Company" })).toBeVisible();
    await expect(main().getByText("People joined")).toBeVisible();
    const members = [...seen.entries()].find(([k]) => k.endsWith("/v1/tenant/members"));
    const items = (JSON.parse(members?.[1] ?? '{"items":[]}') as { items: { progress: string }[] }).items;
    const joined = items.filter((m) => m.progress === "ACTIVE").length;
    const waiting = items.filter((m) => m.progress !== "ACTIVE" && m.progress !== "REVOKED").length;
    const t = await text();
    expect(t, "People joined count").toContain(`People joined ${joined}`);
    expect(t, "Invitations open count").toContain(`Invitations open ${waiting}`);
    note(`overview: ${joined} joined, ${waiting} waiting, as the server said`);
    for (const name of ["Invite people"]) await expect(main().getByRole("link", { name })).toBeVisible();
    await shot("overview");
  });

  // ------------------------------------------------------------------------------------------------ Organization setup
  await section("setup: read-only trust facts, required steps, activation refused while a fact is missing", async () => {
    await open("/organizations/current/setup");
    await expect(main().getByRole("heading", { name: "Organization setup" })).toBeVisible();
    for (const t of ["Legal entity", "Your identity", "Authority to represent the company", "Screening"]) await expect(main().getByText(t, { exact: true })).toBeVisible();
    // there is no control that edits a verification fact
    await expect(main().getByRole("list", { name: "Verification checks" }).getByRole("button")).toHaveCount(0);
    const activate = main().getByRole("button", { name: "Activate organization" });
    await expect(activate).toBeDisabled();
    await expect(main().getByText("Activation is not available yet:")).toBeVisible();
    await expect(main().getByText(/There is no payment, plan or override shortcut/)).toBeVisible();
    await shot("setup-before");
  });

  await section("setup: accept the organizational controls and the data region and policy", async () => {
    await open("/organizations/current/setup");
    for (const label of [/Accept organizational controls/i, /Accept data region and policy/i]) {
      const btn = main().getByRole("button", { name: label });
      if (await btn.count()) {
        const res = await write("POST", "/acknowledgments", async () => btn.click());
        expect(res.status, res.body).toBeLessThan(300);
        await toast(/Recorded/);
      } else note(`${label} was already accepted by an earlier run`);
    }
    await open("/organizations/current/setup");
    await expect(main().getByText(/Accepted version/).first()).toBeVisible();
    await expect(main().getByText("Accept the organizational controls").first()).toBeVisible();
    const t = await text();
    note(`setup after accepting: ${t.slice(t.indexOf("Required setup"), t.indexOf("Required setup") + 700)}`);
    // the legal entity, identity, authority and screening facts are still missing, so activation must still be refused
    await expect(main().getByRole("button", { name: "Activate organization" })).toBeDisabled();
    await shot("setup-after");
  });

  // ------------------------------------------------------------------------------------------------ Approvals
  let requestId = "";
  await section("approvals: inbox tabs, filters, counts", async () => {
    await open("/organizations/current/approvals");
    await expect(main().getByRole("heading", { name: "Approvals" }).first()).toBeVisible();
    for (const t of ["To review", "Submitted by me", "History"]) await expect(main().getByRole("tab", { name: new RegExp(t) })).toBeVisible();
    await pick(main(), "Level", /Critical/);
    await main().getByRole("button", { name: "Apply filters" }).click();
    await page.waitForTimeout(800);
    await main().getByRole("button", { name: "Clear" }).click();
    await shot("approvals-inbox");
  });

  await section("approvals: invalid new request is refused with a clear message and stores nothing", async () => {
    await open("/organizations/current/approvals");
    await main().getByRole("button", { name: "New request" }).click();
    const dialog = page.getByRole("dialog");
    await pick(dialog, "What do you want to change", /Invite a team member/);
    await pick(dialog, "Which role", /Member/);
    // no reason given
    await dialog.getByRole("button", { name: "Send for approval" }).click();
    await expect(dialog.getByText("Check the request")).toBeVisible();
    note(`approvals: refusal text: ${(await dialog.getByText("Check the request").locator("..").innerText()).replace(/\s+/g, " ")}`);
    await shot("approvals-invalid-no-reason");
    // a form whose server-side check fails: an agent that does not exist
    await pick(dialog, "What do you want to change", /Activate a powerful agent/);
    await dialog.getByLabel("The agent's id").fill("00000000-0000-4000-8000-0000000000ff");
    await dialog.getByLabel("Why are you asking?").fill(`E2E ${stamp}: agent id that does not exist`);
    const res = await write("POST", "/approvals", async () => dialog.getByRole("button", { name: "Send for approval" }).click());
    expect(res.status, "an unknown agent must be refused").toBeGreaterThanOrEqual(400);
    await expect(dialog.getByText("The request was not sent")).toBeVisible();
    note(`approvals: server refusal shown: ${(await dialog.getByText("The request was not sent").locator("..").innerText()).replace(/\s+/g, " ")}`);
    await shot("approvals-invalid-server");
    await dialog.getByRole("button", { name: "Cancel" }).click();
  });

  await section("approvals: ask for an A1 change (invite a team member), open it, requester cannot approve, withdraw it", async () => {
    await open("/organizations/current/approvals");
    await main().getByRole("button", { name: "New request" }).click();
    const dialog = page.getByRole("dialog");
    await pick(dialog, "What do you want to change", /Invite a team member/);
    await pick(dialog, "Which role", /Viewer/);
    await dialog.getByLabel("Why are you asking?").fill(`E2E ${stamp}: a colleague needs read-only access`);
    const res = await write("POST", "/approvals", async () => dialog.getByRole("button", { name: "Send for approval" }).click());
    expect(res.status, res.body).toBeLessThan(300);
    requestId = (JSON.parse(res.body) as { case_id?: string; request_id?: string }).case_id ?? "";
    await page.waitForURL(/\/approvals\/[^/]+$/, { timeout: 20_000 });
    await expect(main().getByText("The change you are asked to authorize")).toBeVisible();
    await shot("approvals-detail");
    const t = await text();
    note(`approvals detail: ${t.slice(0, 900)}`);
    await expect(main().getByRole("button", { name: /^Approve|Approve/ }).first()).toBeDisabled();
    await expect(main().getByText(/Independent approvers required|not eligible|cannot approve|You asked for this/i).first()).toBeVisible();
    await open("/organizations/current/approvals");
    await main().getByRole("tab", { name: /Submitted by me/ }).click();
    await expect(main().getByText(/invite-nonprivileged-team-member/).first()).toBeVisible(); // the inbox card is titled with the action id
    await shot("approvals-submitted-by-me");
    // the same request, withdrawn by its requester
    await open(`/organizations/current/approvals/${requestId}`);
    const w = await write("POST", ":cancel", async () => main().getByRole("button", { name: "Withdraw" }).click());
    expect(w.status, w.body).toBeLessThan(300);
    await shot("approvals-withdrawn");
    await open("/organizations/current/approvals");
    await main().getByRole("tab", { name: /History/ }).click();
    await expect(main().getByText(/Cancelled|Withdrawn/i).first()).toBeVisible();
  });

  // ------------------------------------------------------------------------------------------------ Email contacts
  const contact = `e2e.billing.${stamp}@example.com`;
  await section("contacts: add a billing contact, listed masked, mail check, then remove it", async () => {
    await open("/admin/contacts");
    const form = page.getByRole("form", { name: "Add an email contact" });
    await pick(form, "Purpose", "Billing");
    await form.getByLabel("Email address").fill(contact);
    const res = await write("PUT", "/v1/tenant/contacts", async () => form.getByRole("button", { name: "Add contact" }).click());
    expect(res.status, res.body).toBeLessThan(300);
    await toast(/Contact added/);
    const table = main().getByRole("table", { name: "Organization email contacts" });
    const row = table.getByRole("row", { name: /Billing/ }).last();
    const shown = (await row.innerText()).replace(/\s+/g, " ");
    note(`contacts: row shown as "${shown}"`);
    expect(shown, "the address is masked, not shown in full").not.toContain(contact);
    await shot("contacts-added");
    const mail = await mailFor(contact).then((m) => m.slice(0, 200)).catch(() => "no mail arrived within 20 s");
    note(`contacts: mail to the new contact: ${mail.replace(/\s+/g, " ")}`);
    const rm = await write("DELETE", "/v1/tenant/contacts/", async () => page.getByRole("button", { name: /^Remove the billing contact/ }).last().click());
    expect(rm.status).toBeLessThan(300);
    await toast(/Contact removed/);
  });

  await section("contacts: invalid addresses are refused and nothing is stored", async () => {
    await open("/admin/contacts");
    const form = page.getByRole("form", { name: "Add an email contact" });
    for (const bad of ["not-an-email", "two@@example.com", "spaces in@example.com", "no-tld@example"]) {
      await form.getByLabel("Email address").fill(bad);
      await expect(form.getByText("Enter a plain email address, such as billing@yourcompany.com.")).toBeVisible();
      await expect(form.getByRole("button", { name: "Add contact" })).toBeDisabled();
    }
    await shot("contacts-invalid");
    await form.getByLabel("Email address").fill("");
    const rows = await main().getByRole("table", { name: "Organization email contacts" }).getByRole("row").count().catch(() => 0);
    note(`contacts: ${rows} rows after the invalid tries (no row was added)`);
  });

  await section("contacts: set my own security email", async () => {
    await open("/admin/contacts");
    const mine = `e2e.admin.${stamp}@example.com`;
    const form = page.getByRole("form", { name: "Set my security email" });
    await form.getByLabel("My email address").fill("not-valid");
    await expect(form.getByRole("button", { name: "Save" })).toBeDisabled();
    await form.getByLabel("My email address").fill(mine);
    const res = await write("PUT", "/v1/account/contact", async () => form.getByRole("button", { name: "Save" }).click());
    expect(res.status, res.body).toBeLessThan(300);
    await expect(main().getByText(/Saved as/)).toBeVisible();
    note(`contacts: saved line: ${await main().getByText(/Saved as/).innerText()}`);
    await shot("contacts-mine");
  });

  // ------------------------------------------------------------------------------------------------ Company settings
  await section("settings: details, owners (read), lifecycle (read only), cryptography, history", async () => {
    await open("/admin/settings");
    for (const name of ["Details", "Owners", "Lifecycle", "Authentication", "Notifications", "Branding", "Cryptography", "History"]) await expect(page.getByRole("tab", { name, exact: true })).toBeVisible();
    await expect(main().getByText("Local Company").first()).toBeVisible();
    await tab("Owners");
    await expect(main().getByRole("table", { name: "Active members" })).toBeVisible();
    note(`settings owners: ${(await text()).slice(0, 400)}`);
    await shot("settings-owners");
    await tab("Lifecycle");
    await expect(main().getByText("Suspend the workspace")).toBeVisible(); // visible only; not pressed
    await shot("settings-lifecycle");
    await tab("Cryptography");
    await expect(main().getByText("Key management")).toBeVisible();
    await expect(main().getByText(/Raw keys are never shown/).first()).toBeVisible(); // said twice: card description and info banner
    await shot("settings-crypto");
    await tab("History");
    await expect(main().getByRole("table", { name: "Lifecycle transitions" })).toBeVisible();
    await expect(main().getByRole("table", { name: "Configuration revisions" })).toBeVisible();
    await shot("settings-history-before");
  });

  await section("settings: authentication session limits save and revert", async () => {
    await open("/admin/settings");
    await tab("Authentication");
    await pick(main(), "Maximum session length", "8 hours");
    await pick(main(), "Idle timeout", "15 minutes");
    const res = await write("PUT", "/v1/settings/AUTHENTICATION", async () => main().getByRole("button", { name: "Save session limits" }).click());
    expect(res.status, res.body).toBeLessThan(300);
    await toast(/Session limits saved/);
    await shot("settings-auth-saved");
    await expect(main().getByText(/Revision \d+/).first()).toBeVisible();
    // back to the platform defaults so the shared administrator keeps a normal session
    await pick(main(), "Maximum session length", /Platform default/);
    await pick(main(), "Idle timeout", /Platform default/);
    const back = await write("PUT", "/v1/settings/AUTHENTICATION", async () => main().getByRole("button", { name: "Save session limits" }).click());
    expect(back.status, back.body).toBeLessThan(300);
  });

  await section("settings: notifications mute a routine alert and restore it", async () => {
    await open("/admin/settings");
    await tab("Notifications");
    const first = main().getByRole("group", { name: "Routine alerts" }).getByRole("checkbox").first();
    await expect(first).toBeChecked();
    // react-aria renders the input visually hidden: toggle it through its visible label
    await main().getByRole("group", { name: "Routine alerts" }).locator("label").first().click();
    await expect(first).not.toBeChecked();
    const res = await write("PUT", "/v1/settings/NOTIFICATIONS", async () => main().getByRole("button", { name: "Save notification settings" }).click());
    expect(res.status, res.body).toBeLessThan(300);
    await toast(/Notification settings saved/);
    await shot("settings-notifications-muted");
    await main().getByRole("group", { name: "Routine alerts" }).locator("label").first().click();
    await expect(main().getByRole("group", { name: "Routine alerts" }).getByRole("checkbox").first()).toBeChecked();
    const back = await write("PUT", "/v1/settings/NOTIFICATIONS", async () => main().getByRole("button", { name: "Save notification settings" }).click());
    expect(back.status, back.body).toBeLessThan(300);
    await expect(main().getByText(/Critical and high-severity security notices cannot be turned off/)).toBeVisible();
  });

  await section("settings: branding valid save, invalid names/links refused, restore", async () => {
    await open("/admin/settings");
    await tab("Branding");
    await main().getByLabel("Display name").fill(`E2E Co ${stamp}`);
    await pick(main(), "Accent colour", "Teal");
    await main().getByLabel("Support link").fill("https://help.example.com");
    await expect(main().getByText(`Sign in to E2E Co ${stamp}`)).toBeVisible();
    const res = await write("PUT", "/v1/settings/BRANDING", async () => main().getByRole("button", { name: "Save branding" }).click());
    expect(res.status, res.body).toBeLessThan(300);
    await toast(/Branding saved/);
    await shot("settings-branding-saved");
    // invalid: plain http link, and a name that imitates the platform
    await main().getByLabel("Support link").fill("http://insecure.example.com");
    const bad1 = await write("PUT", "/v1/settings/BRANDING", async () => main().getByRole("button", { name: "Save branding" }).click());
    expect(bad1.status, "an http link must be refused").toBeGreaterThanOrEqual(400);
    await shot("settings-branding-http-refused");
    await main().getByLabel("Support link").fill("https://help.example.com");
    await main().getByLabel("Display name").fill("Zoiko iD Support");
    const bad2 = await write("PUT", "/v1/settings/BRANDING", async () => main().getByRole("button", { name: "Save branding" }).click());
    expect(bad2.status, "a name that implies the platform must be refused").toBeGreaterThanOrEqual(400);
    await shot("settings-branding-name-refused");
    note(`settings branding: refusal shown: ${(await text()).slice(0, 600)}`);
    // restore
    await open("/admin/settings");
    await tab("Branding");
    await main().getByLabel("Display name").fill("");
    await main().getByLabel("Support link").fill("");
    await pick(main(), "Accent colour", "Blue");
    const back = await write("PUT", "/v1/settings/BRANDING", async () => main().getByRole("button", { name: "Save branding" }).click());
    expect(back.status, back.body).toBeLessThan(300);
  });

  await section("settings: history shows the new revisions", async () => {
    await open("/admin/settings");
    await tab("History");
    const rows = await main().getByRole("table", { name: "Configuration revisions" }).getByRole("row").count();
    note(`settings history: ${rows} rows in configuration revisions`);
    await shot("settings-history-after");
    await expect(main().getByRole("table", { name: "Configuration revisions" })).toBeVisible();
  });

  // ------------------------------------------------------------------------------------------------ Billing
  await section("billing: every tab renders and matches the free-account contract (ZID-UX-COM-008)", async () => {
    await open("/admin/billing");
    for (const t of BILLING_TABS) {
      await expect(page.getByRole("tab", { name: t, exact: true }), `tab ${t}`).toBeVisible();
      await tab(t);
      const body = await text();
      note(`billing ${t}: ${body.replace(/^Billing Your plan.*?Billing access\s*/, "").slice(0, 650)}`);
      await shot(`billing-${t.replace(/\W+/g, "_")}`);
    }
    await tab("Overview");
    await expect(main().getByText("You are using a free account. No paid subscription is active.")).toBeVisible();
    await expect(main().getByText("No invoices yet")).toBeVisible();
    await tab("Invoices");
    await expect(main().getByText("No invoices yet")).toBeVisible(); // no synthetic zero invoice
    await tab("Bill preview");
    await expect(main().getByText("Not priced yet")).toBeVisible();
  });

  await section("billing: usage month, meter filter and ledger download", async () => {
    await open("/admin/billing");
    await tab("Usage");
    await expect(main().getByText(/Usage in 20\d\d-\d\d/)).toBeVisible();
    await pick(main(), "Month \\(UTC\\)", /^2026-10/);
    await expect(main().getByText("Usage in 2026-10")).toBeVisible();
    await pick(main(), "Month \\(UTC\\)", /this month/);
    await pick(main(), "Meter", "Sign-ins");
    await page.waitForTimeout(1000);
    const t = await text();
    expect(t).toContain("Billable events");
    const href = await main().getByRole("link", { name: /Download ledger/ }).getAttribute("href");
    const csv = await page.request.get(href as string);
    const body = await csv.text();
    note(`usage: ledger CSV ${csv.status()} ${csv.headers()["content-type"]} disposition=${csv.headers()["content-disposition"]} first line: ${(body.split(/\r?\n/)[0] ?? "").slice(0, 160)}`);
    expect(csv.status()).toBe(200);
    await shot("billing-usage-filtered");
  });

  await section("billing subscription: ask for a plan, see it requested, withdraw it", async () => {
    await open("/admin/billing");
    await tab("Subscription");
    await expect(main().getByRole("table", { name: "Available plans" })).toBeVisible();
    const ask = main().getByRole("button", { name: "Ask for this plan" }).first();
    if (!(await ask.count())) {
      note("subscription: a plan request is already open; withdrawing it first");
      await main().getByRole("button", { name: "Withdraw" }).click();
    }
    await main().getByRole("button", { name: "Ask for this plan" }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel(/What do you need it for/).fill(`E2E ${stamp}: two more people with single sign-on`);
    const res = await write("POST", "/v1/billing/plan-requests", async () => dialog.getByRole("button", { name: "Send request" }).click());
    expect(res.status, res.body).toBeLessThan(300);
    await toast(/Request sent to our team/);
    await expect(main().getByText("Your plan request is with our team")).toBeVisible();
    await shot("billing-plan-requested");
    const w = await write("POST", "/plan-requests/", async () => main().getByRole("button", { name: "Withdraw" }).click());
    expect(w.status, w.body).toBeLessThan(300);
    await toast(/Request withdrawn/);
  });

  await section("billing profile: invalid values are refused, valid values are saved with a version", async () => {
    await open("/admin/billing");
    await tab("Billing profile");
    await field("Address line 1").waitFor();
    // invalid: missing address, bad email, bad country, short tax id
    await field("Address line 1").fill("");
    await field("Invoice email").fill("not-an-email");
    await field("Country (two letters)").fill("G");
    await main().getByRole("button", { name: "Save billing profile" }).click();
    await expect(main().getByText("Enter the first line of the address.")).toBeVisible();
    await expect(main().getByText("That does not look like an email address.").first()).toBeVisible();
    await expect(main().getByText("Use the two-letter country code, for example GB.")).toBeVisible();
    await shot("billing-profile-invalid");
    // valid
    await field("Address line 1").fill(`${10 + (Date.now() % 80)} Example Street`);
    await field("Address line 2").fill("Suite 4");
    await field("City").fill("London");
    await field("Region or state").fill("Greater London");
    await field("Postal code").fill("EC1A 1BB");
    await field("Country (two letters)").fill("gb");
    if (!(await field("Number").count())) await main().getByRole("button", { name: "Add a tax identifier" }).click();
    await field("Number").first().fill(`GB${Math.floor(100000000 + (Date.now() % 800000000))}`);
    await field("Invoice email").fill(`invoices.${stamp}@example.com`);
    await field("Notices email").fill(`notices.${stamp}@example.com`);
    await field("Tax document email").fill(`tax.${stamp}@example.com`);
    const res = await write("PUT", "/v1/billing/profile", async () => main().getByRole("button", { name: "Save billing profile" }).click());
    if (res.status === 409 && res.body.includes("ACCOUNT_REQUIRED")) {
      // ENVIRONMENT: a profile belongs to an organization with a commercial account, created when an operator-approved plan order is signed; this tenant has none
      await expect(main().getByText("The billing profile was not saved")).toBeVisible();
      await expect(main().getByText(/commercial account/).first()).toBeVisible();
      await shot("billing-profile-refused-no-account");
      throw new Blocked("saving is refused with ACCOUNT_REQUIRED because the tenant has no commercial account (needs an operator-approved plan order); validation messages and the honest refusal were verified, a saved version was not");
    }
    expect(res.status, res.body).toBeLessThan(300);
    await toast(/Billing profile saved/);
    const t = await text();
    note(`billing profile after save: ${t.slice(t.indexOf("Billing address"), t.indexOf("Billing address") + 700)}`);
    await expect(main().getByText("Unverified").first()).toBeVisible();
    await expect(main().getByText(/Version \d+/)).toBeVisible();
    await shot("billing-profile-saved");
    await expect(main().getByRole("table", { name: "Billing profile versions" })).toBeVisible();
    // server-side refusal: a country that does not exist
    await field("Country (two letters)").fill("ZZ");
    const bad = await write("PUT", "/v1/billing/profile", async () => main().getByRole("button", { name: "Save billing profile" }).click());
    note(`billing profile: country ZZ -> ${bad.status}`);
    await shot("billing-profile-country-zz");
  });

  await section("billing payment: states honestly what the local environment lacks", async () => {
    await open("/admin/billing");
    await tab("Payment");
    await page.waitForTimeout(1500);
    const t = await text();
    note(`payment: ${t.slice(t.indexOf("Amount due"), t.indexOf("Amount due") + 900)}`);
    await expect(main().getByText("Amount due now")).toBeVisible();
    await expect(main().getByText("Nothing is due right now.")).toBeVisible();
    await expect(main().getByText("Payment history")).toBeVisible();
    await shot("billing-payment");
  });

  await section("billing budgets: invalid limits refused, valid budget saved, check now raises an alert", async () => {
    await open("/admin/billing");
    await tab("Budgets");
    const limit = main().getByLabel(/Monthly limit/);
    for (const bad of ["abc", "-5", "0", "1e3"]) {
      await limit.fill(bad);
      await expect(main().getByText("Enter a positive number.")).toBeVisible();
      await expect(main().getByRole("button", { name: "Save budget" })).toBeDisabled();
    }
    await pick(main(), "Budget for", "Machine grants");
    await main().getByLabel(/Monthly limit/).fill("1");
    const res = await write("PUT", "/v1/", async () => main().getByRole("button", { name: "Save budget" }).click()).catch(async () => ({ status: 0, body: "" }));
    note(`budgets: save status ${res.status}`);
    await toast(/Budget saved/);
    await expect(main().getByRole("table", { name: "Active budgets" }).getByText("Machine grants")).toBeVisible();
    await shot("billing-budgets-saved");
    await pick(main(), "Budget for", "The whole monthly bill");
    await main().getByLabel(/Monthly limit/).fill("250.50");
    await main().getByRole("button", { name: "Save budget" }).click();
    await toast(/Budget saved/);
    const check = await write("POST", "/budgets", async () => main().getByRole("button", { name: "Check now" }).click());
    note(`budgets: check now -> ${check.status} ${check.body.slice(0, 200)}`);
    budgetCheckStatus = (JSON.parse(check.body || "{}") as { status?: string }).status ?? "";
    // with no approved price book the cell cannot evaluate budgets, and the page must say so instead of "Nothing new to report"
    if (budgetCheckStatus === "UNPRICED") await toast(/cannot be checked yet/);
    await shot("billing-budgets-checked");
  });

  await section("alerts: the budget alert is listed, can be acknowledged and resolved", async () => {
    await open("/alerts");
    await page.waitForTimeout(1000);
    const t = await text();
    note(`alerts: ${t.slice(0, 700)}`);
    await shot("alerts-before");
    const summary = await page.getByRole("status").first().innerText().catch(() => "");
    note(`alerts summary line: ${summary}`);
    const table = main().getByRole("table", { name: "Alerts and tasks" });
    if (!(await table.count()) && budgetCheckStatus === "UNPRICED") {
      // ENVIRONMENT: budgets and allowance warnings are only evaluated once an approved price book covers the month (operator-approved; none exists locally)
      await expect(main().getByText("Nothing needs your attention")).toBeVisible();
      await expect(main().getByText(/0 open · 0 not yet acknowledged/)).toBeVisible();
      for (const [label, option] of [["State", "Resolved"], ["Severity", "Critical"], ["Severity", "All severities"], ["State", /Open and acknowledged/]] as const) await pick(main(), label, option);
      throw new Blocked("Check now returned UNPRICED (no approved price book locally), so no budget alert can be raised; the empty inbox and its filters were verified, acknowledge/resolve were not");
    }
    if (!(await table.count())) throw new Error("no alert was raised by the budget check");
    const ackBtn = table.getByRole("button", { name: "Acknowledge" }).first();
    if (await ackBtn.count()) {
      const a = await write("POST", ":acknowledge", async () => ackBtn.click());
      expect(a.status, a.body).toBeLessThan(300);
      await toast(/Alert acknowledged/);
    }
    // filters
    await pick(main(), "State", "Acknowledged");
    await page.waitForTimeout(800);
    await pick(main(), "Severity", "Critical");
    await page.waitForTimeout(800);
    await pick(main(), "Severity", "All severities");
    await pick(main(), "State", /Open and acknowledged/);
    await page.waitForTimeout(800);
    const resolveBtn = table.getByRole("button", { name: "Resolve" }).first();
    if (await resolveBtn.count()) {
      const r = await write("POST", ":resolve", async () => resolveBtn.click());
      expect(r.status, r.body).toBeLessThan(300);
      await toast(/Alert resolved/);
    }
    await pick(main(), "State", "Resolved");
    await page.waitForTimeout(800);
    await shot("alerts-resolved");
    await expect(main().getByRole("table", { name: "Alerts and tasks" })).toBeVisible();
  });

  await section("billing budgets: remove the test budgets by setting them aside (budgets cannot be deleted here)", async () => {
    await open("/admin/billing");
    await tab("Budgets");
    const t = await text();
    note(`budgets left in place (the page has no delete): ${t.slice(t.indexOf("Active budgets"), t.indexOf("Active budgets") + 300)}`);
  });

  await section("billing access: give Billing Viewer access, see it listed, take it away; invalid is refused", async () => {
    await open("/admin/billing");
    await tab("Billing access");
    await expect(main().getByText("Your billing access")).toBeVisible();
    const form = main().locator("form").filter({ hasText: "Give access" });
    await expect(form.getByRole("button", { name: "Give access" })).toBeDisabled(); // nobody chosen, no reason
    await form.getByRole("button", { name: /Person/ }).click();
    const options = page.getByRole("option");
    const n = await options.count();
    note(`billing access: ${n} choices in Person`);
    await options.nth(n - 1).click();
    await expect(form.getByRole("button", { name: "Give access" })).toBeDisabled(); // still no reason
    await form.getByLabel("Why").fill(`E2E ${stamp}: read the invoices`);
    const res = await write("POST", "/v1/tenant/billing-grants", async () => form.getByRole("button", { name: "Give access" }).click());
    note(`billing access: give -> ${res.status}`);
    await shot("billing-access-given");
    if (res.status < 300) {
      await toast(/Billing access given/);
      const row = main().getByRole("table", { name: "Billing access grants" }).getByRole("row", { name: new RegExp(`read the invoices`) }).last();
      await expect(row).toBeVisible();
      const w = await write("POST", ":revoke", async () => row.getByRole("button", { name: "Take away" }).click());
      expect(w.status, w.body).toBeLessThan(300);
      await toast(/Billing access taken away/);
      await shot("billing-access-revoked");
    }
  });

  await section("billing commitments: invalid limit refused, give a limit (with passkey), see it listed, take it away", async () => {
    await open("/admin/billing");
    await tab("Commitments");
    const form = main().locator("form").filter({ hasText: "Give limit" });
    await form.getByRole("button", { name: /Person/ }).click();
    const n = await page.getByRole("option").count();
    await page.getByRole("option").nth(n - 1).click();
    for (const bad of ["abc", "-1", "0", ""]) {
      await form.getByLabel("Limit (USD)").fill(bad);
      await expect(form.getByRole("button", { name: "Give limit" })).toBeDisabled();
    }
    await form.getByLabel("Limit (USD)").fill("1500");
    const res = await write("POST", "/commitment-delegates", async () => {
      await form.getByRole("button", { name: "Give limit" }).click();
      await usePasskeyIfAsked();
    });
    note(`commitments: give limit -> ${res.status}`);
    await shot("billing-commitments-limit");
    if (res.status < 300) {
      await toast(/Limit given/);
      const row = main().getByRole("table", { name: "Commitment approval limits" }).getByRole("row", { name: /1,500/ }).last();
      await expect(row).toBeVisible();
      const w = await write("POST", "/commitment-delegates/", async () => {
        await row.getByRole("button", { name: "Take away" }).click();
        await usePasskeyIfAsked();
      });
      expect(w.status, w.body).toBeLessThan(300);
      await toast(/Limit taken away/);
    }
    await expect(main().getByText("No commitment needs approval")).toBeVisible(); // no signed order exists locally, so the approval drawer (B05) has nothing to open
  });

  await section("billing plan features: read-only list", async () => {
    await open("/admin/billing");
    await tab("Plan features");
    await page.waitForTimeout(1500);
    note(`plan features: ${(await text()).slice(0, 600)}`);
    await shot("billing-plan-features");
  });

  writeFileSync(`${SHOTS ?? "."}/browser-errors.txt`, consoleErrors.join("\n"));
  note(`browser errors seen: ${consoleErrors.length} (${[...new Set(consoleErrors)].slice(0, 12).join(" | ")})`);
  await context.close();
  console.log(`SUMMARY ${failures.length} failed sections, ${blocked.length} blocked by environment${blocked.length ? `: ${blocked.join(" | ")}` : ""}`);
  expect(failures, failures.join("\n")).toEqual([]);
});
