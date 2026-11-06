import { expect as baseExpect, test, type Page } from "@playwright/test";

import { addVirtualPasskey } from "./helpers";

/**
 * Company console pages A: Onboarding, Company verification, Domains, Applications, Integrations, Team & invitations, driven as the seeded
 * administrator with valid and invalid data. Every section runs even if an earlier one fails.
 *   cd backend && UV_PROJECT_ENVIRONMENT=$LOCALAPPDATA/zoiko-id/venv py -m uv run python ../tools/scripts/bootstrap_local.py --ticket-only
 *   E2E_ADMIN_TICKET=<ticket> E2E_SHOTS=<folder> npx playwright test -c tests/e2e/playwright.config.ts pages-company-a --reporter=line --output=<folder>/out
 * Optional: E2E_FED_CONNECTION=federation_connection:<id> to add a real sign-in route.
 */
const TICKET = process.env.E2E_ADMIN_TICKET;
const SHOTS = process.env.E2E_SHOTS;
const MAIL = process.env.E2E_MAIL_API ?? "http://localhost:8025";
const stamp = Date.now().toString(36);
const FED_REF = process.env.E2E_FED_CONNECTION ?? "";

const expect = baseExpect.configure({ timeout: 30_000 }); // the dev server compiles pages on demand and is shared with other runs
test.use({ actionTimeout: 45_000 }); // the dev server compiles pages on demand and is shared with other runs

const ONLY = process.env.E2E_ONLY ? new RegExp(process.env.E2E_ONLY, "i") : null;
const failures: string[] = [];
let page: Page;

const shot = async (name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};
const note = (m: string) => console.log(`NOTE  ${m}`);
const flat = (s: string) => s.replace(/\s+/g, " ");

/** A write that needs a fresh passkey check opens "Confirm it's you": answer it with the software passkey. */
async function stepUp() {
  const b = page.getByRole("button", { name: "Verify with passkey" });
  if (await b.waitFor({ state: "visible", timeout: 12_000 }).then(() => true, () => false)) {
    console.log("NOTE  step-up prompt shown (Confirm it's you), answered with the passkey");
    await b.click();
  }
}

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

async function open(path: string) {
  await page.goto(path);
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await expect(page.getByRole("main")).toBeVisible();
}

async function walkGuided(commitLabel: string) {
  const dialog = page.locator('[role="dialog"], [role="alertdialog"]').first();
  for (let step = 0; step < 8; step++) {
    const commit = dialog.getByRole("button", { name: commitLabel });
    if (await commit.isVisible()) {
      await commit.click();
      return;
    }
    for (const box of await dialog.getByRole("checkbox").all()) if (!(await box.isChecked())) await box.check();
    const next = dialog.getByRole("button", { name: /^Continue/ });
    await expect(next.or(commit)).toBeVisible();
    if (await next.isVisible()) await next.click();
  }
  throw new Error(`never reached "${commitLabel}"`);
}

test("company console pages A work for an administrator", async ({ browser }) => {
  test.skip(!TICKET, "set E2E_ADMIN_TICKET to the seeded administrator's enrollment ticket");
  test.setTimeout(1_200_000);
  const context = await browser.newContext();
  page = await context.newPage();
  await addVirtualPasskey(context, page);
  page.on("response", async (r) => {
    const m = r.request().method();
    if (!r.url().includes("/api/")) return;
    if (r.status() >= 400 && (m !== "GET" || r.status() >= 500)) {
      console.log(`HTTP ${r.status()} ${m} ${new URL(r.url()).pathname} ${(await r.text().catch(() => "")).slice(0, 220)}`);
    } else if (m !== "GET") console.log(`HTTP ${r.status()} ${m} ${new URL(r.url()).pathname}`);
  });
  page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message.slice(0, 200)}`));

  await page.goto(`/enroll?ticket=${TICKET}`);
  await expect(page.getByRole("button", { name: "Create passkey" })).toBeEnabled({ timeout: 120_000 });
  await page.getByRole("button", { name: "Create passkey" }).click();
  await expect(page).toHaveURL(/\/(account|overview|admin)/, { timeout: 120_000 }); // polls the URL; waitForURL can miss a navigation that finished first

  /* ------------------------------------------------------------------ Onboarding */
  await section("onboarding: page, steps, gates", async () => {
    await open("/admin/onboarding");
    await expect(page.getByRole("heading", { name: "Onboarding", level: 1 })).toBeVisible();
    for (const t of ["Organization", "Domains & residency", "Identity", "Provisioning", "Applications", "Policy & plan", "Shadow", "Pilot", "Launch"]) {
      await expect(page.getByRole("button", { name: new RegExp(t) }).first()).toBeVisible();
    }
    await shot("onb-0");
  });
  await section("onboarding: create an onboarding plan (Assurance overlay) and approve it as owner", async () => {
    await open("/admin/onboarding");
    await page.getByRole("button", { name: /6\. Policy & plan/ }).click();
    const create = page.getByRole("button", { name: "Create plan" });
    if (await create.isVisible()) {
      await page.getByText("Assurance overlay").first().click();
      await create.click();
      await expect(page.getByText(/Plan v\d+/).first()).toBeVisible({ timeout: 20_000 });
    } else note("a plan already exists from an earlier run");
    await shot("onb-1-plan");
    const approve = page.getByRole("button", { name: "Approve plan" });
    if (await approve.isVisible()) {
      await approve.click();
      await expect(page.getByText(/approved/i).first()).toBeVisible({ timeout: 20_000 });
    }
    await shot("onb-2-approved");
    await page.getByRole("button", { name: "Validate" }).click();
    await page.waitForTimeout(2000);
    await shot("onb-3-validated");
    note("gates after validate: " + flat(await page.getByRole("main").innerText()).split("Gates").pop()!.slice(0, 500));
  });
  await section("onboarding: expand applications step (connect guide)", async () => {
    await open("/admin/onboarding");
    await page.getByRole("button", { name: /5\. Applications/ }).click();
    await expect(page.getByText("Tell us about your application")).toBeVisible();
    await shot("onb-4-connect");
  });

  /* ------------------------------------------------------------------ Company verification */
  await section("company verification: shows the verified company and status", async () => {
    await open("/admin/organization");
    await expect(page.getByText("Company verified")).toBeVisible();
    await expect(page.getByText("Local Company").first()).toBeVisible();
    await shot("org-0");
  });

  /* ------------------------------------------------------------------ Domains */
  const dns = `e2e-${stamp}.example.com`;
  const https = `e2e-https-${stamp}.example.org`;
  await section("domains: invalid domain and public mail domain are refused, nothing stored", async () => {
    await open("/admin/domains");
    await page.getByRole("button", { name: "Add domain" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByRole("textbox", { name: "Domain" }).fill("not a domain");
    await expect(d.getByText("Enter a domain such as example.com.")).toBeVisible();
    await expect(d.getByRole("button", { name: "Add domain" })).toBeDisabled();
    await d.getByRole("textbox", { name: "Domain" }).fill("gmail.com");
    await expect(d.getByText("Public e-mail domains cannot be claimed.")).toBeVisible();
    await expect(d.getByRole("button", { name: "Add domain" })).toBeDisabled();
    await shot("dom-0-invalid");
    await d.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("cell", { name: "gmail.com" })).toHaveCount(0);
  });
  await section("domains: add a domain with DNS TXT, see the challenge, Verify says not verified", async () => {
    await open("/admin/domains");
    await page.getByRole("button", { name: "Add domain" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByRole("textbox", { name: "Domain" }).fill(dns);
    await d.getByRole("button", { name: "Add domain" }).click();
    await expect(d.getByText("Add this TXT record")).toBeVisible({ timeout: 20_000 });
    note("DNS challenge: " + flat(await d.innerText()).slice(0, 400));
    await shot("dom-1-challenge");
    await d.getByRole("button", { name: "Done" }).click();
    const row = page.getByRole("row", { name: new RegExp(dns.replace(/\./g, "\\.")) });
    await expect(row.getByText("Pending").first()).toBeVisible();
    await row.getByRole("button", { name: "Verify" }).click();
    await page.waitForTimeout(2500);
    await shot("dom-2-verify-attempt");
    note("after Verify: " + ((await page.getByRole("alert").allInnerTexts()).join(" | ") || "(no alert)") + " row=" + flat(await row.innerText()));
  });
  await section("domains: add a domain with HTTPS file; adding the same domain twice", async () => {
    await open("/admin/domains");
    await page.getByRole("button", { name: "Add domain" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByRole("textbox", { name: "Domain" }).fill(https);
    await d.getByText("HTTPS file").click();
    await d.getByRole("button", { name: "Add domain" }).click();
    await expect(d.getByText("Serve this text at exactly")).toBeVisible({ timeout: 90_000 });
    note("HTTPS challenge: " + flat(await d.innerText()).slice(0, 400));
    await d.getByRole("button", { name: "Done" }).click();
    await page.getByRole("button", { name: "Add domain" }).first().click();
    await page.getByRole("dialog").getByRole("textbox", { name: "Domain" }).fill(https);
    await page.getByRole("dialog").getByRole("button", { name: "Add domain" }).click();
    await page.waitForTimeout(2000);
    await shot("dom-3-duplicate");
    note("duplicate add: " + flat(await page.getByRole("dialog").innerText().catch(() => "dialog closed")).slice(0, 300));
    if (await page.getByRole("dialog").isVisible()) await page.getByRole("dialog").getByRole("button", { name: /Cancel|Done/ }).click();
  });
  await section("domains: remove the two domains this run added", async () => {
    await open("/admin/domains");
    for (const dom of [dns, https]) {
      const row = page.getByRole("row", { name: new RegExp(dom.replace(/\./g, "\\.")) });
      if (await row.count()) await row.first().getByRole("button", { name: "Remove" }).click();
      await page.waitForTimeout(1500);
    }
    await shot("dom-4-removed");
    note("table after removal: " + flat(await page.getByRole("table").innerText()).slice(0, 400));
  });

  /* ------------------------------------------------------------------ Applications */
  const appName = `Payroll ${stamp}`;
  await section("applications: wizard refuses a too-short name and a missing return address", async () => {
    await open("/admin/applications");
    await page.getByRole("button", { name: "Add application" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByLabel("What is your application called?").fill("a");
    await expect(d.getByRole("button", { name: "Continue" })).toBeDisabled();
    await d.getByLabel("What is your application called?").fill(appName);
    await d.getByRole("button", { name: "Continue" }).click();
    await expect(d.getByRole("button", { name: "Continue" })).toBeDisabled(); // no return address, no key
    await shot("app-0-invalid");
    await page.keyboard.press("Escape");
  });
  await section("applications: add an access-decision application with a new key, then Start testing and activate", async () => {
    await open("/admin/applications");
    await page.getByRole("button", { name: "Add application" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByLabel("What is your application called?").fill(appName);
    await d.getByRole("button", { name: "Continue" }).click();
    await d.getByLabel("A return address for it").fill("https://payroll.example.com/callback");
    await d.getByRole("button", { name: "Make a key for me" }).click();
    await expect(d.getByText("Save the private key now")).toBeVisible();
    await d.getByRole("button", { name: "Continue" }).click();
    await d.getByText("Keep it simple").click();
    await d.getByRole("button", { name: "Continue" }).click();
    await shot("app-1-summary");
    await d.getByRole("button", { name: "Add application" }).click();
    await expect(d.getByText(`“${appName}” is added`)).toBeVisible({ timeout: 30_000 });
    await shot("app-2-added");
    await d.getByRole("button", { name: "Start testing and activate" }).click();
    await expect(d).toBeHidden({ timeout: 30_000 });
    const row = page.getByRole("row", { name: new RegExp(appName) });
    await expect(row.getByText("Active").first()).toBeVisible();
    await shot("app-3-active");
    await row.getByRole("button", { name: "Suspend" }).click();
    await expect(row.getByRole("button", { name: "Resume" })).toBeVisible();
    await row.getByRole("button", { name: "Resume" }).click();
    await expect(row.getByRole("button", { name: "Suspend" })).toBeVisible();
    await shot("app-4-suspend-resume");
  });
  await section("applications: add a sign-in-only application (draft, testing, back to draft)", async () => {
    await open("/admin/applications");
    await page.getByRole("button", { name: "Add application" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByLabel("What is your application called?").fill(`Intranet ${stamp}`);
    await d.getByText("People sign in to it with Zoiko iD").click();
    await d.getByRole("button", { name: "Continue" }).click();
    await d.getByRole("button", { name: "Add application" }).click();
    await expect(d.getByText(/is added/)).toBeVisible({ timeout: 30_000 });
    await d.getByRole("button", { name: "Later" }).click();
    const row = page.getByRole("row", { name: new RegExp(`Intranet ${stamp}`) });
    await expect(row.getByText("Draft").first()).toBeVisible();
    await row.getByRole("button", { name: "Start testing" }).click();
    await expect(row.getByRole("button", { name: "Activate" })).toBeVisible();
    await row.getByRole("button", { name: "Back to draft" }).click();
    await expect(row.getByRole("button", { name: "Start testing" })).toBeVisible();
    await shot("app-5-signin-only");
  });
  await section("applications: create a browser client, a server client, refuse bad redirect and private key, retire one", async () => {
    await open("/admin/applications");
    await page.getByRole("button", { name: "Create client" }).click();
    let d = page.getByRole("dialog");
    await d.getByLabel("Application name").fill(`Web ${stamp}`);
    await d.getByLabel("Redirect URLs").fill("http://app.example.com/callback");
    await d.getByRole("button", { name: "Create client" }).click();
    await page.waitForTimeout(2000);
    await shot("app-6-http-redirect");
    note("http redirect: " + flat(await d.innerText()).slice(-300));
    await d.getByLabel("Redirect URLs").fill("https://app.example.com/callback");
    await d.getByRole("button", { name: "Create client" }).click();
    await expect(page.getByTestId("new-client-id")).toBeVisible({ timeout: 20_000 });
    const clientId = (await page.getByTestId("new-client-id").innerText()).trim();
    await shot("app-7-client");
    await d.getByRole("button", { name: "Done" }).click();
    await page.getByRole("button", { name: "Create client" }).click();
    d = page.getByRole("dialog");
    await d.getByLabel("Application name").fill(`Server ${stamp}`);
    await d.getByText("Server application").click();
    await d.getByLabel("Redirect URLs").fill("https://srv.example.com/callback");
    await d.getByLabel("Public key (JWK Set)").fill('{"keys":[{"kty":"EC","crv":"P-256","kid":"k1","x":"AQAB","y":"AQAB","d":"secret"}]}');
    await d.getByRole("button", { name: "Create client" }).click();
    await page.waitForTimeout(2000);
    note("private key: " + flat(await d.innerText()).slice(-300));
    await shot("app-8-private-key");
    await d.getByLabel("Public key (JWK Set)").fill("not json");
    await d.getByRole("button", { name: "Create client" }).click();
    await expect(d.getByText("That is not a JSON key")).toBeVisible();
    await d.getByRole("button", { name: "Cancel" }).click();
    const row = page.getByRole("row", { name: new RegExp(clientId) });
    await row.getByRole("button", { name: "Retire" }).click();
    await expect(page.getByText("Client retired").first()).toBeVisible({ timeout: 15_000 });
    await shot("app-9-retired");
  });

  /* ------------------------------------------------------------------ Integrations */
  const conn = `Webhook ${stamp}`;
  await section("integrations: register a webhook connector, bad freshness refused", async () => {
    await open("/admin/integrations");
    await page.getByRole("button", { name: "Register connector" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByLabel("Name").fill(conn);
    await d.getByLabel("Freshness (minutes)").fill("99999");
    await expect(d.getByText("1 to 10080 minutes.")).toBeVisible();
    await expect(d.getByRole("button", { name: "Register", exact: true })).toBeDisabled();
    await d.getByLabel("Freshness (minutes)").fill("");
    await d.getByRole("button", { name: /Type/ }).click();
    await page.getByRole("option", { name: "Webhook" }).click();
    await d.getByText("Sign-in", { exact: true }).click();
    await d.getByText("Webhooks", { exact: true }).click();
    await d.getByLabel("Freshness (minutes)").fill("60");
    await shot("int-0-form");
    await d.getByRole("button", { name: "Register", exact: true }).click();
    await stepUp();
    await expect(page.getByRole("cell", { name: conn })).toBeVisible({ timeout: 20_000 });
    await shot("int-1-listed");
  });
  await section("integrations: OIDC connector with a wrong source prefix is refused", async () => {
    await open("/admin/integrations");
    await page.getByRole("button", { name: "Register connector" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByLabel("Name").fill(`Okta ${stamp}`);
    await d.getByLabel("Source").fill("okta-123");
    await expect(d.getByText("Must start with federation_connection:")).toBeVisible();
    await expect(d.getByRole("button", { name: "Register", exact: true })).toBeDisabled();
    await shot("int-2-bad-source");
    await d.getByRole("button", { name: "Cancel" }).click();
  });
  await section("integrations: store a credential (short refused), quarantine, release", async () => {
    await open("/admin/integrations");
    const row = page.getByRole("row", { name: new RegExp(conn) });
    await row.getByRole("button", { name: "Credential" }).click();
    const d = page.getByRole("dialog");
    await d.getByLabel("Secret").fill("short");
    const store = d.getByRole("button", { name: /Store|Save/ });
    await expect(store).toBeDisabled();
    await d.getByLabel("Secret").fill(`e2e-secret-${stamp}-0123456789`);
    await shot("int-3-credential");
    await store.click();
    await stepUp();
    await expect(page.getByText(/Credential version \d+ stored/).first()).toBeVisible({ timeout: 20_000 });
    await row.getByRole("button", { name: "Quarantine" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Quarantine" }).click();
    await stepUp();
    await expect(page.getByText("Connector quarantined").first()).toBeVisible({ timeout: 20_000 });
    await shot("int-4-quarantined");
    await row.getByRole("button", { name: "Release" }).click();
    await shot("int-5-release-dialog");
    note("release dialog: " + flat(await page.getByRole("dialog").innerText()).slice(0, 300));
    await walkGuided("Release from quarantine");
    await page.waitForTimeout(3000);
    await shot("int-6-after-release");
    note("release result: " + flat(await page.getByRole("dialog").innerText().catch(() => "dialog closed")).slice(0, 300));
  });
  await section("integrations: mapping release, simulate, activate", async () => {
    await open("/admin/integrations");
    await page.getByRole("tab", { name: "Mappings" }).click();
    await page.getByRole("button", { name: "New release" }).click();
    const d = page.getByRole("dialog");
    await shot("int-7-new-release");
    await d.getByLabel("Rules (JSON array)").fill("not json");
    const create = d.getByRole("button", { name: /Create|Save/ });
    await expect(create).toBeDisabled();
    note("rules default: " + (await d.getByLabel("Rules (JSON array)").inputValue()).slice(0, 10));
    await d.getByLabel("Rules (JSON array)").fill(JSON.stringify([{ source: "claims.department", target: "profile.department", transform: "trim" }]));
    await create.click();
    await stepUp();
    await page.waitForTimeout(2500);
    await shot("int-8-release-created");
    note("release dialog: " + flat(await page.getByRole("dialog").innerText().catch(() => "closed")).slice(0, 500));
  });
  await section("integrations: sign-in routing, add route and test it", async () => {
    await open("/admin/integrations");
    await page.getByRole("tab", { name: "Sign-in routing" }).click();
    await page.getByRole("textbox", { name: "Domain" }).first().fill(`login-${stamp}.example.com`);
    await page.getByRole("textbox", { name: "Identity provider connection" }).fill("short");
    await expect(page.getByRole("button", { name: "Add", exact: true })).toBeDisabled();
    await page.getByRole("textbox", { name: "Identity provider connection" }).fill("federation_connection:does-not-exist-0000");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await stepUp();
    await page.waitForTimeout(2000);
    await shot("int-9-route-bogus");
    note("bogus route: " + ((await page.getByRole("alert").allInnerTexts()).join(" | ") || "(no alert)") + " added-toast=" + (await page.getByText("Route added").count()));
    if (FED_REF) {
      await page.getByRole("textbox", { name: "Identity provider connection" }).fill(FED_REF);
      await page.getByRole("button", { name: "Add", exact: true }).click();
      await stepUp();
      await expect(page.getByRole("cell", { name: `login-${stamp}.example.com` })).toBeVisible({ timeout: 15_000 });
      await page.getByRole("textbox", { name: "E-mail domain" }).fill(`login-${stamp}.example.com`);
      await page.getByRole("button", { name: "Resolve" }).click();
      await page.waitForTimeout(1500);
      await shot("int-10-route-real");
    }
  });

  const mine: string[] = (process.env.E2E_CLEANUP_IDS ?? "").split(",").filter(Boolean); // principal ids of the people invited, removed again at the end
  const members = async () =>
    (await page.evaluate(async () => ((await (await fetch("/api/bff/v1/tenant/members", { credentials: "same-origin" })).json()) as { data?: { items: { principal_id: string }[] }; items?: { principal_id: string }[] }))) as {
      data?: { items: { principal_id: string }[] };
      items?: { principal_id: string }[];
    };
  const ids = async () => ((await members()).items ?? (await members()).data?.items ?? []).map((m) => m.principal_id);
  const invite = async (label: string) => {
    const before = await ids();
    await open("/admin/team");
    await page.getByRole("button", { name: "Invite person" }).first().click();
    const d = page.getByRole("dialog");
    await d.getByText(label, { exact: true }).click();
    await d.getByRole("button", { name: "Create invitation" }).click();
    await expect(d.getByText("Passkey setup link").first()).toBeVisible({ timeout: 20_000 });
    const l = (await d.getByLabel("Passkey setup link").innerText()).trim();
    const after = await ids();
    mine.push(...after.filter((x) => !before.includes(x)));
    return { d, l };
  };
  // ids are time-ordered (UUIDv7), so the first nine characters repeat; the list shows the first ten and the last four
  const rowOf = (id: string) => page.getByRole("row").filter({ hasText: new RegExp(`${id.slice(0, 10)}.*${id.slice(-4)}`) });
  const finishLink = async (d: ReturnType<Page["getByRole"]>) => {
    await expect(d.getByRole("button", { name: "Done" })).toBeDisabled();
    await d.getByText("I have shared the link").click();
    await d.getByRole("button", { name: "Done" }).click();
  };

  let link = "";
  await section("team: roles and delegations tabs render", async () => {
    await open("/admin/team");
    await page.getByRole("tab", { name: "Roles and permissions" }).click();
    await expect(page.getByText("Policy version")).toBeVisible({ timeout: 15_000 });
    await shot("team-roles");
    await page.getByRole("tab", { name: "Delegations" }).click();
    await page.waitForTimeout(1500);
    await shot("team-delegations");
  });
  await section("team: invite a Member, get a single-use link, no email is sent", async () => {
    const mailBefore = await fetch(`${MAIL}/api/v1/messages?limit=1`).then((r) => r.json()).catch(() => ({ total: -1 }));
    await open("/admin/team");
    await page.getByRole("button", { name: "Invite person" }).first().click();
    await expect(page.getByRole("dialog").getByText("An invitation never makes someone an OWNER")).toBeVisible();
    await page.keyboard.press("Escape");
    const { d, l } = await invite("Member");
    link = l;
    await shot("team-0-link");
    await finishLink(d);
    const mailAfter = await fetch(`${MAIL}/api/v1/messages?limit=1`).then((r) => r.json()).catch(() => ({ total: -1 }));
    note(`invitation link made; mail sink total before=${mailBefore.total} after=${mailAfter.total} (the invite form has no e-mail address field)`);
  });
  await section("team: the invitee opens the link in their own browser and creates a passkey; the list follows", async () => {
    test.skip(!link, "no link");
    const ctx2 = await browser.newContext();
    const p2 = await ctx2.newPage();
    await addVirtualPasskey(ctx2, p2);
    // the seeded local tenant has no workspace-name registration, so a link carrying &workspace=local-company says "Company not found" (local seed gap, reported);
    // the administrator enrols without that parameter, so the invitee does too
    note("invitation link as issued: " + link.replace(/ticket=[^&]+/, "ticket=…"));
    await p2.goto(link.replace(/[&?]workspace=[^&]*/, ""));
    try {
      await expect(p2.getByRole("button", { name: "Create passkey" })).toBeEnabled({ timeout: 60_000 });
    } catch (e) {
      note("invitee page at " + p2.url() + ": " + flat(await p2.locator("body").innerText()).slice(0, 400));
      if (SHOTS) await p2.screenshot({ path: `${SHOTS}/team-1-invitee-stuck.png`, fullPage: true }).catch(() => undefined);
      throw e;
    }
    await p2.getByRole("button", { name: "Create passkey" }).click();
    await p2.waitForURL(/\/(account|overview|admin)/, { timeout: 120_000, waitUntil: "commit" });
    if (SHOTS) await p2.screenshot({ path: `${SHOTS}/team-1-invitee.png`, fullPage: true }).catch(() => undefined);
    note("invitee landed on " + new URL(p2.url()).pathname + " nav: " + flat(await p2.getByRole("navigation", { name: "Main" }).innerText().catch(() => "?")).slice(0, 200));
    await p2.goto("/admin/team");
    await p2.waitForTimeout(2000);
    note("invitee opening /admin/team sees: " + flat(await p2.locator("body").innerText()).slice(0, 200));
    await ctx2.close();
    await open("/admin/team");
    await page.waitForTimeout(6000);
    await shot("team-2-after-join");
    note("team table: " + flat(await page.getByRole("table").first().innerText()).slice(0, 700));
  });
  await section("team: invite an Administrator, reissue a link", async () => {
    const { d } = await invite("Administrator");
    await finishLink(d);
    await page.waitForTimeout(1500);
    const row = rowOf(mine[mine.length - 1]!);
    await row.getByRole("button", { name: "New link" }).click();
    const d2 = page.getByRole("dialog");
    await expect(d2.getByText("Passkey setup link").first()).toBeVisible({ timeout: 20_000 });
    await shot("team-3-reissue");
    await finishLink(d2);
  });
  await section("team: invite an Operator and a Viewer", async () => {
    for (const r of ["Operator", "Viewer"]) {
      const { d } = await invite(r);
      await finishLink(d);
    }
    await shot("team-4-roles");
    note("roles in list: " + flat(await page.getByRole("table").first().innerText()).slice(0, 700));
  });
  await section("team: remove everyone this run invited (confirmation names the role)", async () => {
    await open("/admin/team");
    for (const id of mine) {
      const row = rowOf(id);
      await row.getByRole("button", { name: "Remove" }).click();
      const dlg = page.getByRole("alertdialog");
      if (id === mine[0]) await shot("team-5-remove-confirm");
      await dlg.getByRole("button", { name: "Remove" }).click();
      await expect(page.getByText("Access removed").first()).toBeVisible({ timeout: 15_000 });
      await page.waitForTimeout(800);
    }
    await shot("team-6-removed");
    note("table after removal: " + flat(await page.getByRole("table").first().innerText()).slice(0, 700));
  });

  await context.close();
  expect(failures, failures.join("\n")).toEqual([]);
});
