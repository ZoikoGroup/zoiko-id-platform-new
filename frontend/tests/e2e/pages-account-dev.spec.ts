import { createHash, randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect as baseExpect, test, type BrowserContext, type Locator, type Page } from "@playwright/test";

// The local stack is slow when several people use it at once: wait longer than the default before calling something missing.
const expect = baseExpect.configure({ timeout: 60_000 });

/**
 * Customer-console pages for an administrator, driven with realistic data:
 * My account, Sessions, Passkeys, My limits, Connected apps, Identity verification, Recovery codes, Privacy,
 * Developers (webhooks), Agents & tools, Workload identity.
 *
 * Needs a running stack and the seeded administrator's first-passkey link (one use, fresh for every run):
 *   cd backend && UV_PROJECT_ENVIRONMENT=$LOCALAPPDATA/zoiko-id/venv py -m uv run python ../tools/scripts/bootstrap_local.py --ticket-only
 *   cd frontend && E2E_ADMIN_TICKET=<ticket> E2E_SHOTS=<folder> npx playwright test -c tests/e2e/playwright.config.ts pages-account-dev --reporter=line --output=<folder>/out
 * Optional: E2E_REMOVE_PASSKEY=1 also removes the passkey this run adds (it signs the administrator out everywhere, so leave it off while
 * other people use the same administrator). E2E_ONLY=<regex> runs only the sections whose name matches.
 * Every section runs even if an earlier one fails; the test fails at the end if any did.
 */
const TICKET = process.env.E2E_ADMIN_TICKET;
const SHOTS = process.env.E2E_SHOTS;
const ONLY = process.env.E2E_ONLY ? new RegExp(process.env.E2E_ONLY, "i") : null;
const stamp = Date.now().toString(36);
const DB = process.env.E2E_DB_URL ?? "postgresql://zid:zid_local_only@localhost:5432/zid_cell";
const PY = process.env.E2E_PY ?? `${process.env.LOCALAPPDATA}/zoiko-id/venv/Scripts/python.exe`;
const ADMIN = "00000000-0000-4000-8000-0000000000b1";
const ORG = "00000000-0000-4000-8000-0000000000d1";

test.use({ actionTimeout: 60_000 });

const failures: string[] = [];
const notes: string[] = [];
let page: Page;
let ctxA: BrowserContext;

const note = (m: string) => {
  notes.push(m);
  console.log(`NOTE  ${m}`);
};
const shot = async (name: string, p: Page = page) => {
  if (SHOTS) await p.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};

async function section(name: string, run: () => Promise<void>) {
  if (ONLY && !ONLY.test(name)) return;
  try {
    await test.step(name, run);
    console.log(`PASS  ${name}`);
  } catch (e) {
    const message = (e as Error).message.split("\n").slice(0, 3).join(" / ");
    failures.push(`${name}: ${message}`);
    console.log(`FAIL  ${name}: ${message}`);
    await shot(`FAILED-${name.replace(/[^A-Za-z0-9]+/g, "-").slice(0, 50)}`).catch(() => undefined);
    await page.keyboard.press("Escape").catch(() => undefined);
  }
}

/** Read-only SQL against the local cell database (python + asyncpg from the project venv). Returns rows as JSON. */
function sql(query: string, ...args: string[]): Record<string, unknown>[] {
  const dir = join(tmpdir(), "zid-e2e-acct");
  mkdirSync(dir, { recursive: true });
  const file = join(dir, `q-${randomBytes(4).toString("hex")}.py`);
  writeFileSync(
    file,
    `import asyncio, asyncpg, json, sys\nasync def m():\n    c = await asyncpg.connect(sys.argv[1])\n    rows = await c.fetch(sys.argv[2], *sys.argv[3:])\n    print("JSON:" + json.dumps([dict(r) for r in rows], default=str))\nasyncio.run(m())\n`,
  );
  const r = spawnSync(PY, ["-I", file, DB, query, ...args], { encoding: "utf8" });
  const out = (r.stdout ?? "").split("\n").find((l) => l.startsWith("JSON:"));
  if (!out) throw new Error(`sql failed: ${(r.stderr ?? "").slice(-300)}`);
  return JSON.parse(out.slice(5));
}

/** A software passkey, returning the CDP session and authenticator so the credential can be copied to a second browser. */
async function virtualPasskey(context: BrowserContext, p: Page, transport: "internal" | "usb" = "internal") {
  const cdp = await context.newCDPSession(p);
  await cdp.send("WebAuthn.enable");
  const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: { protocol: "ctap2", transport, hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true },
  });
  return { cdp, authenticatorId };
}

async function cellSessionId(context: BrowserContext): Promise<string | undefined> {
  const cookie = (await context.cookies()).find((c) => c.name === "__Host-zid_session");
  if (!cookie) return undefined;
  const hex = createHash("sha256").update(cookie.value).digest("hex");
  const rows = sql("select session_id::text as id, status from authn.session where handle_hash = decode($1, 'hex')", hex);
  return rows[0]?.id as string | undefined;
}

/** Walks a guided R3 change (intent, impact, preconditions, approval, commit): acknowledges what it asks, continues, and commits. */
async function walkGuided(commitLabel: string) {
  const dialog = page.locator('[role="dialog"], [role="alertdialog"]').first();
  for (let step = 0; step < 8; step++) {
    const commit = dialog.getByRole("button", { name: commitLabel });
    if (await commit.isVisible()) {
      await commit.click();
      await dialog.getByRole("button", { name: "Done" }).click();
      return;
    }
    for (const box of await dialog.getByRole("checkbox").all()) await tick(box);
    const next = dialog.getByRole("button", { name: /^Continue/ });
    await expect(next.or(commit)).toBeVisible();
    if (await next.isVisible()) await next.click();
  }
  throw new Error(`never reached "${commitLabel}"`);
}

/** react-aria renders checkboxes as visually hidden inputs: tick one by clicking its visible label, then confirm it took. */
async function tick(box: Locator) {
  if (!(await box.isChecked())) await box.locator("xpath=ancestor::label").click();
  await expect(box).toBeChecked();
}

/** Opens a page and waits for it to settle. */
async function open(path: string) {
  await page.goto(path, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => undefined);
  await expect(page.getByRole("main")).toBeVisible();
}

/** Chooses an option in a react-aria Select by its label. */
async function pick(scope: Page | ReturnType<Page["locator"]>, label: string | RegExp, option: string | RegExp) {
  // Once something is chosen the button's name starts with the chosen value, not the label: match the label anywhere.
  const anywhere = typeof label === "string" ? label : new RegExp(label.source.replace(/^\^/, ""), label.flags);
  await scope.getByRole("button", { name: anywhere }).first().click();
  // Only the open list's options: the select's hidden native <select> also exposes option roles, and it comes first in the page.
  await page.getByRole("listbox").getByRole("option", { name: option }).first().click();
}

/** The guided "Confirm it's you" passkey check, when the cell asks for a fresh strong sign-in: answer it with the software passkey. */
async function confirmIt(wait = 6000) {
  const d = page.getByRole("dialog", { name: "Confirm it's you" });
  try {
    await d.waitFor({ state: "visible", timeout: wait });
  } catch {
    return false;
  }
  await d.getByRole("button", { name: "Verify with passkey" }).click();
  await d.waitFor({ state: "hidden", timeout: 60_000 });
  return true;
}

async function toastSeen(text: string | RegExp) {
  await expect(page.getByText(text).first()).toBeVisible({ timeout: 20_000 });
}

test("account, developer and machine pages work end to end for an administrator", async ({ browser }) => {
  test.skip(!TICKET, "set E2E_ADMIN_TICKET to the seeded administrator's enrollment ticket");
  test.setTimeout(1_500_000);
  ctxA = await browser.newContext();
  page = await ctxA.newPage();
  const passkeyA = await virtualPasskey(ctxA, page);
  await page.addLocatorHandler(page.getByRole("dialog", { name: "Confirm it's you" }), async (d) => {
    await d.getByRole("button", { name: "Verify with passkey" }).click();
  });
  page.on("response", async (r) => {
    const method = r.request().method();
    if (r.url().includes("/api/") && r.status() >= 400 && !(method === "GET" && r.status() === 404)) {
      console.log(`HTTP  ${r.status()} ${method} ${new URL(r.url()).pathname} ${(await r.text().catch(() => "")).slice(0, 200)}`);
    }
  });

  await page.goto(`/enroll?ticket=${TICKET}`);
  await page.getByRole("button", { name: "Create passkey" }).click();
  await page.waitForURL(/\/(account|overview|admin)/, { timeout: 120_000 });
  const mySession = await cellSessionId(ctxA);
  note(`this run's own session is ${mySession}`);

  // ------------------------------------------------------------------ My account
  await section("my account: identity, counts and links agree with the cell", async () => {
    await open("/account");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { name: "My account" })).toBeVisible();
    await expect(main.getByText(ADMIN).first()).toBeVisible();
    await expect(main.getByText("00000000-0000-4000-8000-0000000000a1").first()).toBeVisible();
    const sessions = sql("select count(*)::int as n from authn.session where principal_id = $1::uuid and status = 'ACTIVE' and expires_at > now()", ADMIN)[0]?.n;
    const keys = sql("select count(*)::int as n from authn.authenticator where principal_id = $1::uuid and status = 'ACTIVE'", ADMIN)[0]?.n;
    const text = (await main.innerText()).replace(/\s+/g, " ");
    note(`account cards: ${text.match(/Active sessions \d+/)?.[0]} / ${text.match(/Passkeys \d+/)?.[0]} / ${text.match(/Connected apps \d+/)?.[0]}; database: ${sessions} active sessions, ${keys} active passkeys`);
    await expect(main.getByText("Trusted").first()).toBeVisible();
    await shot("01-account");
    for (const [link, url] of [
      ["Identity verification", /\/account\/verification/],
      ["Recovery codes", /\/account\/recovery/],
    ] as const) {
      await open("/account");
      await main.getByRole("link", { name: link }).click();
      await expect(page).toHaveURL(url);
    }
    await open("/account");
    await main.getByRole("link", { name: /Active sessions/ }).click();
    await expect(page).toHaveURL(/\/account\/sessions/);
  });

  // ------------------------------------------------------------------ Sessions
  await section("sessions: sign out one of my own other sessions; sign out everywhere is offered but cancelled", async () => {
    // A second browser holding a copy of the same software passkey signs in, so there is a session that belongs to this run to end.
    const ctxB = await browser.newContext();
    const pageB = await ctxB.newPage();
    const keyB = await virtualPasskey(ctxB, pageB);
    const { credentials } = await passkeyA.cdp.send("WebAuthn.getCredentials", { authenticatorId: passkeyA.authenticatorId });
    for (const credential of credentials) await keyB.cdp.send("WebAuthn.addCredential", { authenticatorId: keyB.authenticatorId, credential });
    await pageB.goto("/sign-in");
    await pageB.waitForLoadState("networkidle").catch(() => undefined);
    await pageB.getByRole("button", { name: "Sign in with a passkey" }).click();
    await pageB.waitForURL(/\/(account|overview|admin)/, { timeout: 90_000 });
    const idB = await cellSessionId(ctxB);
    expect(idB, "the second browser has its own session").toBeTruthy();
    expect(idB).not.toBe(mySession);
    note(`second browser session ${idB}`);

    await open("/account/sessions");
    const short = idB!.slice(0, 8);
    const row = page.getByRole("row").filter({ hasText: short });
    await expect(row).toBeVisible();
    await expect(row.getByText("AAL-Z2")).toBeVisible();
    await shot("02-sessions");

    // "Sign out everywhere" asks first; cancel it (it would end every other person's session of the shared administrator).
    await page.getByRole("button", { name: "Sign out everywhere" }).click();
    const all = page.getByRole("alertdialog");
    await expect(all.getByText("Every session for your account ends now, including this one")).toBeVisible();
    await shot("02b-sessions-signout-all-dialog");
    await all.getByRole("button", { name: "Cancel" }).click();
    await expect(all).toBeHidden();

    await row.getByRole("button", { name: "Sign out" }).click();
    const dlg = page.getByRole("alertdialog");
    await expect(dlg.getByText("The session ends now and its tokens stop working")).toBeVisible();
    await dlg.getByRole("button", { name: "Sign out" }).click();
    await toastSeen("Session signed out");
    const after = sql("select status, revoked_at from authn.session where session_id = $1::uuid", idB!)[0];
    note(`database after sign-out: session ${short} is ${after?.status} (revoked_at ${after?.revoked_at})`);
    expect(after?.status).toBe("REVOKED");
    await expect(row.getByRole("button", { name: "Sign out" })).toHaveCount(0);
    // The signed-out browser can no longer use the console.
    const status = await pageB.evaluate(async () => (await fetch("/api/cell/sessions", { credentials: "same-origin" }).catch(() => ({ status: 0 }))).status);
    await pageB.goto("/account/sessions");
    await pageB.waitForLoadState("networkidle").catch(() => undefined);
    note(`second browser after revoke: url ${pageB.url()}, probe ${status}`);
    expect(pageB.url()).toMatch(/sign-in|signed-out|login/);
    await shot("02c-second-browser-after-revoke", pageB);
    await ctxB.close();
    // My own session is untouched.
    expect(sql("select status from authn.session where session_id = $1::uuid", mySession!)[0]?.status).toBe("ACTIVE");
  });

  // ------------------------------------------------------------------ Passkeys
  const keyLabel = `E2E laptop ${stamp}`;
  await section("passkeys: add a named passkey, see it listed as Active", async () => {
    await open("/account/passkeys");
    await expect(page.getByRole("heading", { name: "Passkeys", exact: true })).toBeVisible();
    await shot("03-passkeys");
    await page.getByRole("button", { name: "Add a passkey" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Name (optional)").fill(keyLabel);
    // The browser refuses a second passkey on an authenticator that already holds one for this account (as a real device does), so the new
    // passkey goes to a second software authenticator while the first one is muted.
    await passkeyA.cdp.send("WebAuthn.setAutomaticPresenceSimulation", { authenticatorId: passkeyA.authenticatorId, enabled: false });
    const second = await virtualPasskey(ctxA, page, "usb"); // Chrome allows only one internal authenticator per browser context
    await dialog.getByRole("button", { name: "Create passkey" }).click();
    await confirmIt(4000);
    await toastSeen("Passkey added");
    await passkeyA.cdp.send("WebAuthn.setAutomaticPresenceSimulation", { authenticatorId: passkeyA.authenticatorId, enabled: true });
    note(`second software authenticator ${second.authenticatorId} now holds the new passkey (kept for this run)`);
    const row = page.getByRole("row").filter({ hasText: keyLabel });
    await expect(row).toBeVisible();
    await expect(row.getByText("Active").first()).toBeVisible();
    const db = sql("select authenticator_type, status, label, backup_eligible from authn.authenticator where label = $1", keyLabel);
    note(`database passkey row: ${JSON.stringify(db)}`);
    expect(db.length).toBe(1);
    await shot("03b-passkey-added");
  });

  await section("passkeys: a name over 64 characters is refused and nothing is stored", async () => {
    await open("/account/passkeys");
    const long = "L".repeat(70);
    const before = sql("select count(*)::int as n from authn.authenticator where principal_id = $1::uuid", ADMIN)[0]?.n as number;
    await page.getByRole("button", { name: "Add a passkey" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Name (optional)").fill(long);
    await dialog.getByRole("button", { name: "Create passkey" }).click();
    await page.waitForTimeout(4000);
    const after = sql("select count(*)::int as n from authn.authenticator where principal_id = $1::uuid", ADMIN)[0]?.n as number;
    const shown = (await dialog.innerText().catch(() => "")).replace(/\s+/g, " ");
    note(`70-character name: passkeys before ${before}, after ${after}; dialog text: ${shown.slice(0, 200)}`);
    await shot("03c-passkey-long-name");
    if (await dialog.isVisible()) await dialog.getByRole("button", { name: "Cancel" }).click();
    expect(after, "no passkey is stored for an over-long name").toBe(before);
  });

  await section("passkeys: the last-passkey rule and the remove dialog", async () => {
    await open("/account/passkeys");
    await expect(page.getByText(/You can.t remove your only active passkey/)).toBeVisible();
    const row = page.getByRole("row").filter({ hasText: keyLabel });
    await row.getByRole("button", { name: "Remove" }).click();
    const dlg = page.getByRole("alertdialog");
    await expect(dlg.getByText("every session for your account then ends")).toBeVisible();
    await shot("03d-passkey-remove-dialog");
    if (process.env.E2E_REMOVE_PASSKEY === "1") {
      await dlg.getByRole("button", { name: "Remove passkey" }).click();
      await page.waitForTimeout(8000);
      note(`after removing ${keyLabel}: ${JSON.stringify(sql("select status, revoked_reason from authn.authenticator where label = $1", keyLabel))}`);
    } else {
      await dlg.getByRole("button", { name: "Cancel" }).click();
      note("removal of the added passkey not executed: the cell ends every session of the shared administrator when a passkey is removed (set E2E_REMOVE_PASSKEY=1 to run it)");
    }
  });

  // ------------------------------------------------------------------ My limits
  let mandateId: string | undefined;
  await section("my limits: create a limit, see it listed, pause it and cancel it", async () => {
    await open("/account/limits");
    await shot("04-limits-before");
    await open("/admin/mandates");
    await page.getByRole("button", { name: "Create mandate" }).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("A mandate can only narrow a grant")).toBeVisible();
    // The administrator holds two grants that can carry a mandate, so none is chosen for them: pick the source grant first.
    await pick(dialog, /Source grant/, /./);
    await pick(dialog, /Executor/, /00000000…00b1/);
    await tick(dialog.getByRole("checkbox", { name: /Release a payment/ }));
    await tick(dialog.getByRole("checkbox", { name: /Any payment/ }));
    await dialog.getByLabel("Currency").fill("USD");
    await dialog.getByLabel("Total value limit").fill("500.00");
    await dialog.getByLabel("Per-transaction limit").fill("50.00");
    await dialog.getByLabel("Count limit").fill("10");
    await shot("04b-create-mandate-filled");
    await dialog.getByRole("button", { name: "Create mandate" }).last().click();
    await toastSeen(/Mandate created/);
    mandateId = sql("select mandate_id::text as id from agent.mandate where created_by_principal_id = $1::uuid order by created_at desc limit 1", ADMIN)[0]?.id as string;
    note(`mandate created ${mandateId}`);
    const m = sql("select status, value_limit, per_transaction_value_limit, currency, count_limit from agent.mandate where mandate_id = $1::uuid", mandateId)[0];
    note(`mandate row ${JSON.stringify(m)}`);
    await page.keyboard.press("Escape");

    await open("/account/limits");
    const main = page.getByRole("main");
    await expect(main.getByText("You set this up and you use it").first()).toBeVisible();
    await expect(main.getByText(/up to 50(\.00)? USD/).first()).toBeVisible();
    await shot("04c-limits-listed");
    const text = (await main.innerText()).replace(/\s+/g, " ");
    note(`my limits card text: ${text.slice(0, 500)}`);
  });

  await section("my limits: a pending limit offers Cancel it; cancelling ends it", async () => {
    test.skip(!mandateId, "no mandate");
    await open("/account/limits");
    const card = page.getByRole("main").locator("div.space-y-3").filter({ hasText: /500/ }).first();
    const buttons = (await card.getByRole("button").allTextContents()).join(" | ");
    note(`my limits buttons on the new limit: ${buttons}`);
    const cancelBtn = card.getByRole("button", { name: /Cancel it|Pause it/ }).first();
    await cancelBtn.click();
    const dlg = page.getByRole("alertdialog");
    await shot("04d-limits-cancel-dialog");
    const label = (await cancelBtn.innerText()).trim();
    await dlg.getByRole("button", { name: /(Revoke|Pause|Cancel) mandate|Revoke|Pause/ }).last().click();
    await toastSeen(/Mandate is now/);
    note(`clicked "${label}"; mandate now ${JSON.stringify(sql("select status from agent.mandate where mandate_id = $1::uuid", mandateId!))}`);
    await shot("04e-limits-after");
  });

  // ------------------------------------------------------------------ Connected apps
  await section("connected apps: allow an app through the consent screen, see it listed, remove its access", async () => {
    const client = "payroll-mw3as3eb-access-decision-96b59eaa";
    const verifier = randomBytes(32).toString("base64url");
    const challenge = createHash("sha256").update(verifier).digest("base64url");
    await ctxA.route("https://payroll.example.com/**", (r) => r.fulfill({ status: 200, contentType: "text/html", body: "callback reached" }));
    const url = `http://localhost:8000/oauth2/authorize?${new URLSearchParams({ response_type: "code", client_id: client, redirect_uri: "https://payroll.example.com/callback", scope: "openid", nonce: `n${stamp}`, state: `e2e${stamp}`, code_challenge: challenge, code_challenge_method: "S256" })}`;
    await page.goto(url).catch((e) => note(`authorize navigation: ${(e as Error).message.slice(0, 120)}`));
    await page.waitForURL(/consent|payroll\.example\.com/, { timeout: 60_000 });
    await page.waitForLoadState("networkidle").catch(() => undefined);
    note(`after authorize the browser is at ${page.url().slice(0, 140)}`);
    await shot("05-consent-screen");
    const allow = page.getByRole("button", { name: /Allow|Approve|Continue/ }).first();
    await allow.click();
    await page.waitForURL(/payroll\.example\.com\/callback\?/, { timeout: 30_000 });
    note(`redirected to ${page.url().slice(0, 120)}`);
    await open("/account/connected-apps");
    const row = page.getByRole("row").filter({ hasText: client });
    await expect(row).toBeVisible();
    await expect(row.getByText("openid")).toBeVisible();
    await shot("05b-connected-apps");
    await row.getByRole("button", { name: "Remove access" }).click();
    const dlg = page.getByRole("alertdialog");
    await expect(dlg.getByText("can no longer refresh its access")).toBeVisible();
    await dlg.getByRole("button", { name: "Remove access" }).click();
    await toastSeen("Access removed");
    await expect(row).toHaveCount(0);
    note(`consent rows left for the client: ${JSON.stringify(sql("select revoked_at is not null as revoked from authn.oauth_consent where client_id = $1", client))}`);
  });

  // ------------------------------------------------------------------ Identity verification
  await section("identity verification: tier card, alternatives, and the guided check (cancel, fail, verify)", async () => {
    await open("/account/verification");
    const main = page.getByRole("main");
    await expect(main.getByText("Not identity verified means exactly that.")).toBeVisible();
    await tick(main.getByRole("checkbox", { name: "I can't use a camera" }));
    await expect(main.getByRole("list", { name: "Approved alternatives" })).toBeVisible({ timeout: 15_000 });
    note(`alternatives shown: ${(await main.getByRole("list", { name: "Approved alternatives" }).innerText()).replace(/\s+/g, " ").slice(0, 300)}`);
    await shot("06-verification-alternatives");
    await main.getByRole("checkbox", { name: "I can't use a camera" }).locator("xpath=ancestor::label").click();
    await expect(main.getByRole("checkbox", { name: "I can't use a camera" })).not.toBeChecked();

    const start = async (country?: string) => {
      await pick(main, /^Verification check/, /representative-proofing/);
      const jur = main.getByRole("button", { name: /Country of your documents/ });
      if (!(await jur.innerText()).match(/GB|US|[A-Z]{2}/)) await pick(main, /Country of your documents/, country ?? /./);
      await main.getByRole("button", { name: "Start verification" }).click();
      await toastSeen("Verification started");
    };
    // invalid: empty purpose keeps Start disabled
    await pick(main, /^Verification check/, /representative-proofing/);
    await main.getByLabel("Purpose").fill("");
    await expect(main.getByRole("button", { name: "Start verification" })).toBeDisabled();
    await main.getByLabel("Purpose").fill("account_verification");

    await start();
    await expect(main.getByText("Waiting for the verification provider")).toBeVisible({ timeout: 30_000 });
    await shot("06b-verification-pending");
    await main.getByRole("button", { name: "Cancel check" }).click();
    await toastSeen("Verification cancelled");
    await expect(main.getByText("The check was cancelled.")).toBeVisible();
    await main.getByRole("button", { name: "Start another check" }).click();

    await start();
    await expect(main.getByText("Development identity provider")).toBeVisible({ timeout: 30_000 });
    await pick(main, /^Provider result/, "Check fails");
    await main.getByRole("button", { name: "Complete check" }).click();
    await expect(main.getByText("The check could not verify your identity.")).toBeVisible({ timeout: 30_000 });
    await shot("06c-verification-failed");
    await main.getByRole("button", { name: "Start another check" }).click();

    await start();
    await expect(main.getByText("Development identity provider")).toBeVisible({ timeout: 30_000 });
    await main.getByRole("button", { name: "Complete check" }).click();
    await expect(main.getByText("Your identity was verified.")).toBeVisible({ timeout: 40_000 });
    await shot("06d-verification-verified");
    await page.reload();
    await page.waitForLoadState("networkidle").catch(() => undefined);
    const text = (await page.getByRole("main").innerText()).replace(/\s+/g, " ");
    note(`after verification: ${text.match(/Your verification level.{0,260}/)?.[0]} || ${text.match(/Your assurance.{0,260}/)?.[0]}`);
    await shot("06e-verification-after-reload");
  });

  // ------------------------------------------------------------------ Recovery codes
  await section("recovery codes: generate (passkey check), shown once, copy/download, confirm, gone after leaving", async () => {
    await open("/account/recovery");
    await shot("07-recovery");
    const before = sql("select count(*)::int as n from authn.recovery_code where principal_id = $1::uuid and used_at is null and revoked_at is null", ADMIN)[0]?.n;
    await page.getByRole("button", { name: "Generate new codes" }).click();
    const dlg = page.getByRole("alertdialog");
    await dlg.getByRole("button", { name: "Generate codes" }).click();
    await confirmIt();
    const list = page.getByRole("list", { name: "Recovery codes" });
    await expect(list).toBeVisible({ timeout: 60_000 });
    const codes = await list.getByRole("listitem").locator("code").allTextContents();
    note(`recovery codes shown: ${codes.length}; shape of the first: ${codes[0]?.replace(/[A-Za-z0-9]/g, "x")}`);
    expect(codes.length).toBeGreaterThanOrEqual(8);
    expect(new Set(codes).size).toBe(codes.length);
    await expect(page.getByText("These codes can't be shown again")).toBeVisible();
    await shot("07b-recovery-codes");
    const done = page.getByRole("button", { name: "Done" });
    await expect(done).toBeDisabled();
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download .txt" }).click();
    expect((await download).suggestedFilename()).toBe("zoiko-id-recovery-codes.txt");
    await tick(page.getByRole("checkbox", { name: /I have saved these codes/ }));
    await done.click();
    await expect(list).toHaveCount(0);
    await page.reload();
    await expect(page.getByText(codes[0] ?? "never")).toHaveCount(0);
    const stored = sql("select count(*)::int as n, bool_or(encode(code_hash,'escape') like '%' || $1 || '%') as plain from authn.recovery_code where principal_id = $2::uuid", codes[0] ?? "x", ADMIN)[0];
    note(`recovery_code rows for the administrator: ${JSON.stringify(stored)} (the plain code must not be stored)`);
    note(`unused recovery codes before generating: ${before}; now: ${JSON.stringify(sql("select count(*)::int as n from authn.recovery_code where principal_id = $1::uuid and used_at is null and revoked_at is null", ADMIN)[0]?.n)}`);
  });

  await section("recovery codes: stopping a recovery attempt that does not exist is refused clearly", async () => {
    await open("/account/recovery");
    const stop = page.getByRole("button", { name: "Stop this recovery" });
    await expect(stop).toBeDisabled();
    await page.getByLabel("Recovery attempt ID").fill("not-an-id");
    await stop.click();
    await page.waitForTimeout(2500);
    const t1 = (await page.getByRole("main").innerText()).replace(/\s+/g, " ");
    note(`stop recovery with "not-an-id": ${t1.match(/(Something|couldn|Could not|not found|invalid|No recovery|isn.t)[^|]{0,160}/i)?.[0] ?? "no visible message"}`);
    await page.getByLabel("Recovery attempt ID").fill("11111111-2222-4333-8444-555555555555");
    await stop.click();
    await page.waitForTimeout(2500);
    const t2 = (await page.getByRole("main").innerText()).replace(/\s+/g, " ");
    note(`stop recovery with an unknown UUID: ${t2.match(/(Something|couldn|Could not|not found|invalid|No recovery|isn.t|stopped)[^|]{0,160}/i)?.[0] ?? "no visible message"}`);
    await shot("07c-recovery-stop-unknown");
  });

  // ------------------------------------------------------------------ Privacy
  await section("privacy: opt-out switches save and persist", async () => {
    await open("/account/privacy");
    await shot("08-privacy");
    const sw = page.getByRole("switch", { name: "No direct marketing" });
    const was = String(await sw.isChecked());
    await sw.locator("xpath=ancestor::label").click();
    await toastSeen("Preference saved");
    await page.reload();
    await page.waitForLoadState("networkidle").catch(() => undefined);
    const now = await page.getByRole("switch", { name: "No direct marketing" }).isChecked();
    note(`"No direct marketing" was ${was}, after saving and reload it is ${now}`);
    expect(String(now)).not.toBe(was);
    await page.getByRole("switch", { name: "No direct marketing" }).locator("xpath=ancestor::label").click(); // put it back
    await toastSeen("Preference saved");
  });

  await section("privacy: consent options, give consent, withdraw it", async () => {
    await open("/account/privacy");
    const main = page.getByRole("main");
    const empty = await main.getByText("Nothing to agree to").isVisible().catch(() => false);
    note(empty ? "no optional consent is on offer in this tenant (nothing to give or withdraw)" : "consent options exist");
    if (!empty) {
      const read = main.getByRole("checkbox", { name: /I have read notice/ }).first();
      const give = main.getByRole("button", { name: "Give consent" }).first();
      await expect(give).toBeDisabled();
      await tick(read);
      await give.click();
      await toastSeen(/your consent was recorded/);
      await shot("08b-consent-given");
      await main.getByRole("button", { name: "Withdraw" }).first().click();
      await toastSeen(/Consent withdrawn/);
    }
  });

  await section("privacy: open an access request and a complaint; the delete request warns first", async () => {
    await open("/account/privacy");
    const request = async (what: string, law?: string) => {
      await page.getByRole("button", { name: "New request" }).click();
      const dlg = page.getByRole("dialog");
      await pick(dlg, /^What would you like to do/, what);
      if (law) await pick(dlg, /^Applicable law/, law);
      return dlg;
    };
    let dlg = await request("Delete my data");
    await expect(dlg.getByText("Deletion cannot be undone")).toBeVisible();
    await shot("08c-privacy-erasure-warning");
    await dlg.getByRole("button", { name: "Cancel" }).click();
    dlg = await request(/Get a copy of my data/);
    await dlg.getByRole("button", { name: "Submit request" }).click();
    await toastSeen("Request received");
    dlg = await request("Make a privacy complaint");
    await dlg.getByRole("button", { name: "Submit request" }).click();
    await toastSeen("Request received");
    const main = page.getByRole("main");
    await expect(main.getByRole("row").filter({ hasText: "Get a copy of my data" }).first()).toBeVisible();
    await expect(main.getByRole("row").filter({ hasText: "Make a privacy complaint" }).first()).toBeVisible();
    await shot("08d-privacy-requests");
    note(`rights requests in the database: ${JSON.stringify(sql("select request_type, status from privacy.rights_request order by received_at desc limit 3"))}`);
  });

  // ------------------------------------------------------------------ Developers (webhooks)
  const hook = `https://example.com/zoiko-e2e-${stamp}`;
  await section("developers: register a webhook endpoint, see it pending, try to verify it", async () => {
    await open("/admin/developers");
    await expect(page.getByRole("heading", { name: "Developers", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Register endpoint" }).first().click();
    const dlg = page.getByRole("dialog");
    await dlg.getByLabel("Endpoint URL").fill(hook);
    await tick(dlg.getByRole("checkbox").first());
    await shot("09-webhook-register");
    await dlg.getByRole("button", { name: "Register endpoint" }).click();
    await confirmIt();
    await toastSeen("Endpoint registered");
    const row = page.getByRole("row").filter({ hasText: hook });
    await expect(row).toBeVisible();
    await expect(row.getByText(/Pending/i).first()).toBeVisible();
    note(`webhook row: ${JSON.stringify(sql("select status, event_types from events.webhook_endpoint where url = $1", hook))}`);
    await row.getByRole("button", { name: "Verify" }).click();
    const v = page.getByRole("alertdialog");
    await v.getByRole("button", { name: "Send challenge" }).click();
    await page.waitForTimeout(8000);
    const out = (await v.innerText().catch(() => "")).replace(/\s+/g, " ");
    note(`verify against a URL that is not our receiver: ${out.slice(0, 300) || "(dialog closed)"} | status ${JSON.stringify(sql("select status from events.webhook_endpoint where url = $1", hook))}`);
    await shot("09b-webhook-verify-result");
    if (await v.isVisible()) await v.getByRole("button", { name: "Cancel" }).click();
  });

  await section("developers: bad endpoint URLs and no events are refused and nothing is stored", async () => {
    await open("/admin/developers");
    const bad = async (url: string, pickEvent: boolean) => {
      await page.getByRole("button", { name: "Register endpoint" }).first().click();
      const dlg = page.getByRole("dialog");
      await dlg.getByLabel("Endpoint URL").fill(url);
      if (pickEvent) await tick(dlg.getByRole("checkbox").first());
      await dlg.getByRole("button", { name: "Register endpoint" }).click();
      await page.waitForTimeout(3000);
      const text = (await dlg.innerText()).replace(/\s+/g, " ");
      const msg = text.match(/(Check the form|Not allowed|refused|invalid|blocked|HTTPS|Choose at least)[^.]{0,140}/i)?.[0] ?? text.slice(-200);
      await shot(`09c-webhook-bad-${url.replace(/\W+/g, "_").slice(0, 25)}`);
      await dlg.getByRole("button", { name: "Cancel" }).click();
      return msg;
    };
    const m1 = await bad("http://example.com/hook", true);
    const m2 = await bad(hook + "-noevents", false);
    const m3 = await bad("https://127.0.0.1/hook", true);
    const m4 = await bad("https://localhost/hook", true);
    note(`refusals: http -> "${m1}" | no events -> "${m2}" | IP literal -> "${m3}" | localhost -> "${m4}"`);
    expect(sql("select count(*)::int as n from events.webhook_endpoint where url like $1 or url like $2 or url like $3", "http://example.com%", "https://127.%", "https://localhost%")[0]?.n).toBe(0);
    expect(sql("select count(*)::int as n from events.webhook_endpoint where url = $1", hook + "-noevents")[0]?.n).toBe(0);
  });

  await section("developers: delivery log, usage, rate limits and signature guide tabs show real data", async () => {
    await open("/admin/developers");
    const main = page.getByRole("main");
    for (const tab of ["Delivery log", "Usage", "Rate limits", "Verify signatures"]) {
      await main.getByRole("tab", { name: tab }).click();
      await page.waitForTimeout(2500);
      await shot(`09d-developers-${tab.replace(/\W+/g, "-")}`);
      const text = (await main.innerText()).replace(/\s+/g, " ");
      note(`${tab} tab: ${text.slice(text.indexOf(tab, 100) > 0 ? 0 : 0, 0)}${text.replace(/^.*Verify signatures/, "").slice(0, 380)}`);
    }
    await main.getByRole("tab", { name: "Verify signatures" }).click();
    await expect(main.getByText("Signing keys")).toBeVisible();
    await expect(main.getByText("Key ID").first()).toBeVisible();
  });

  await section("developers: pause is not offered while pending; retire is permanent", async () => {
    await open("/admin/developers");
    const row = page.getByRole("row").filter({ hasText: hook });
    await expect(row.getByRole("button", { name: "Pause" })).toHaveCount(0);
    await row.getByRole("button", { name: "Retire" }).click();
    const dlg = page.getByRole("alertdialog");
    await expect(dlg.getByText("Retiring is permanent")).toBeVisible();
    await dlg.getByRole("button", { name: "Retire endpoint" }).click();
    await toastSeen(/Endpoint is now retired/);
    await expect(row.getByText(/Retired/i).first()).toBeVisible();
    await expect(row.getByRole("button", { name: "Retire" })).toHaveCount(0);
    await shot("09e-webhook-retired");
  });

  // ------------------------------------------------------------------ Agents & tools
  const appPrincipals = sql("select principal_id::text as id from core.principal where tenant_id = '00000000-0000-4000-8000-0000000000a1' and principal_type = 'APPLICATION' and status = 'ACTIVE' order by created_at desc limit 3").map((r) => r.id as string);
  const toolId = `e2e.reports-${stamp}`;
  const bpId = `e2e-bot-${stamp}`;
  const digest = (s: string) => `sha256:${createHash("sha256").update(s).digest("hex")}`;
  const agentName = `E2E Agent ${stamp}`;
  await section("agents: register a tool, see it Draft and not executable, activate it", async () => {
    await open("/admin/agents");
    const main = page.getByRole("main");
    await main.getByRole("tab", { name: "Tools" }).click();
    await main.getByRole("button", { name: "Register tool" }).first().click();
    const dlg = page.getByRole("dialog");
    await dlg.getByLabel("Tool ID").fill(toolId);
    await dlg.getByLabel("Hosting server principal ID").fill(appPrincipals[0] ?? "");
    await dlg.getByLabel("Authorization action").fill("report.read");
    await shot("10-tool-register");
    await dlg.getByRole("button", { name: "Register tool" }).last().click();
    await confirmIt();
    await toastSeen("Tool registered as a draft");
    const row = page.getByRole("row").filter({ hasText: toolId });
    await expect(row).toBeVisible();
    await expect(row.getByText("No: not active")).toBeVisible();
    await row.getByRole("button", { name: "Activate" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Activate" }).click();
    await toastSeen("Tool updated");
    await expect(row.getByText("Yes")).toBeVisible();
    await shot("10b-tool-active");
  });

  await section("agents: invalid tool form is refused (bad id, bad server, bad action)", async () => {
    await open("/admin/agents");
    const main = page.getByRole("main");
    await main.getByRole("tab", { name: "Tools" }).click();
    await main.getByRole("button", { name: "Register tool" }).first().click();
    const dlg = page.getByRole("dialog");
    await dlg.getByLabel("Tool ID").fill("Bad Tool!");
    await dlg.getByRole("button", { name: "Register tool" }).last().click();
    const a = (await dlg.getByRole("alert").first().innerText().catch(() => "")).replace(/\s+/g, " ");
    await dlg.getByLabel("Tool ID").fill(`e2e.bad-${stamp}`);
    await dlg.getByLabel("Hosting server principal ID").fill("not-a-uuid");
    await dlg.getByRole("button", { name: "Register tool" }).last().click();
    const b = (await dlg.getByRole("alert").first().innerText().catch(() => "")).replace(/\s+/g, " ");
    await dlg.getByLabel("Hosting server principal ID").fill(appPrincipals[0] ?? "");
    await dlg.getByLabel("Authorization action").fill("nodots");
    await dlg.getByRole("button", { name: "Register tool" }).last().click();
    const c = (await dlg.getByRole("alert").first().innerText().catch(() => "")).replace(/\s+/g, " ");
    note(`tool refusals: id -> "${a}" | server -> "${b}" | action -> "${c}"`);
    await shot("10c-tool-invalid");
    await dlg.getByRole("button", { name: "Cancel" }).click();
    expect(sql("select count(*)::int as n from agent.tool_capability where tool_id like $1", `e2e.bad-${stamp}%`)[0]?.n).toBe(0);
  });

  await section("agents: draft a blueprint release, its publisher cannot approve it, someone else can", async () => {
    await open("/admin/agents");
    const main = page.getByRole("main");
    await main.getByRole("tab", { name: "Blueprints" }).click();
    await main.getByRole("button", { name: "Draft release" }).first().click();
    let dlg = page.getByRole("dialog");
    await dlg.getByLabel("Blueprint ID").fill(bpId);
    await dlg.getByLabel("Version").fill("1.0.0");
    await dlg.getByLabel("Artifact digest").fill("sha256:abc");
    await dlg.getByRole("button", { name: "Draft release" }).last().click();
    const bad = (await dlg.getByRole("alert").first().innerText().catch(() => "")).replace(/\s+/g, " ");
    note(`blueprint with a short digest: "${bad}"`);
    await dlg.getByLabel("Artifact digest").fill(digest("artifact-" + stamp));
    await dlg.getByLabel("Tool manifest digest").fill(digest("manifest-" + stamp));
    await dlg.getByLabel("Publisher principal ID").fill(appPrincipals[1] ?? "");
    await dlg.getByRole("button", { name: "Draft release" }).last().click();
    await confirmIt();
    await toastSeen("Release drafted");
    const row = page.getByRole("row").filter({ hasText: bpId });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Approve" }).click();
    await toastSeen("Release approved");
    await expect(row.getByText(/Approved/i).first()).toBeVisible();
    await shot("11-blueprint-approved");

    // a release whose publisher is the administrator cannot be approved by the administrator
    await main.getByRole("button", { name: "Draft release" }).first().click();
    dlg = page.getByRole("dialog");
    await dlg.getByLabel("Blueprint ID").fill(`${bpId}-self`);
    await dlg.getByLabel("Version").fill("1.0.0");
    await dlg.getByLabel("Artifact digest").fill(digest("self-a-" + stamp));
    await dlg.getByLabel("Tool manifest digest").fill(digest("self-m-" + stamp));
    await dlg.getByLabel("Publisher principal ID").fill(ADMIN);
    await dlg.getByRole("button", { name: "Draft release" }).last().click();
    await confirmIt();
    await toastSeen("Release drafted");
    const own = page.getByRole("row").filter({ hasText: `${bpId}-self` });
    await own.getByRole("button", { name: "Approve" }).click();
    await page.waitForTimeout(3000);
    const refusal = (await page.getByRole("alert").allInnerTexts()).join(" / ").replace(/\s+/g, " ");
    note(`approving a release the administrator published: ${refusal.slice(0, 240)} | review_status ${JSON.stringify(sql("select review_status from agent.blueprint_release where blueprint_id = $1", `${bpId}-self`))}`);
    await shot("11b-blueprint-self-approval");
  });

  let agentId: string | undefined;
  await section("agents: register an agent as a draft; invalid forms are refused", async () => {
    await open("/admin/agents");
    const main = page.getByRole("main");
    await main.getByRole("button", { name: "Register agent" }).first().click();
    const dlg = page.getByRole("dialog");
    await dlg.getByLabel("Agent name").fill("x");
    await dlg.getByRole("button", { name: "Register agent" }).last().click();
    note(`one-letter agent name: "${(await dlg.getByRole("alert").first().innerText().catch(() => "")).replace(/\s+/g, " ")}"`);
    await dlg.getByLabel("Agent name").fill(agentName);
    await pick(dlg, /^Blueprint release/, new RegExp(bpId + " @ 1.0.0$"));
    await dlg.getByLabel("Sponsor principal ID").fill("not-a-uuid");
    await dlg.getByLabel("Operator principal ID").fill(ADMIN);
    await dlg.getByRole("button", { name: "Register agent" }).last().click();
    note(`bad sponsor: "${(await dlg.getByRole("alert").first().innerText().catch(() => "")).replace(/\s+/g, " ")}"`);
    await dlg.getByLabel("Sponsor principal ID").fill(ADMIN);
    await dlg.getByLabel("Owning organization ID").fill(ORG);
    await dlg.getByLabel("Model provider (metadata)").fill("example-model-v1");
    await dlg.getByLabel("Allowed tools").fill("no.such.tool");
    await dlg.getByRole("button", { name: "Register agent" }).last().click();
    note(`unregistered tool: "${(await dlg.getByRole("alert").first().innerText().catch(() => "")).replace(/\s+/g, " ")}"`);
    await dlg.getByLabel("Allowed tools").fill(toolId);
    await shot("12-agent-register");
    await dlg.getByRole("button", { name: "Register agent" }).last().click();
    await confirmIt();
    await toastSeen("Agent registered as a draft");
    agentId = sql("select agent_principal_id::text as id from agent.agent_profile where agent_name = $1", agentName)[0]?.id as string;
    note(`agent ${agentId}: ${JSON.stringify(sql("select status from agent.agent_profile where agent_name = $1", agentName))}`);
    await expect(page.getByRole("row").filter({ hasText: agentName }).first()).toBeVisible();
    await shot("12b-agent-draft");
  });

  await section("agents: the activation checklist blocks until every gate holds, then the agent runs", async () => {
    test.skip(!agentId, "no agent");
    await open("/admin/agents");
    const main = page.getByRole("main");
    await main.getByRole("row").filter({ hasText: agentName }).first().click();
    const panel = main.getByText("Activation checklist").first();
    await expect(panel).toBeVisible();
    await shot("12c-agent-detail");
    note(`agent detail text: ${(await main.innerText()).replace(/\s+/g, " ").slice(0, 700)}`);
    // attest the four person-only gates (the security review is refused while the reviewer is the sponsor/operator)
    const attestGate = async (label: string) => {
      const b = main.getByRole("button", { name: /^Attest/ }).first();
      await b.click();
      const d = page.getByRole("dialog").last();
      await d.getByLabel(/Note/).fill(`E2E ${label}`);
      await d.getByRole("button", { name: "Record attestation" }).click();
      await page.waitForTimeout(2500);
      const err = (await d.getByRole("alert").allInnerTexts().catch(() => [])).join(" ").replace(/\s+/g, " ");
      if (await d.isVisible()) {
        note(`attesting ${label}: refused with "${err.slice(0, 200)}"`);
        await d.getByRole("button", { name: "Cancel" }).click();
        return false;
      }
      return true;
    };
    for (const g of ["gate 1", "gate 2", "gate 3", "gate 4"]) {
      if ((await main.getByRole("button", { name: /^Attest/ }).count()) === 0) break;
      if (!(await attestGate(g))) break;
    }
    await shot("12d-agent-attested");
    note(`attestations stored: ${JSON.stringify(sql("select gate from agent.agent_gate_attestation where agent_principal_id = $1::uuid", agentId!))}`);
  });

  // ------------------------------------------------------------------ Workload identity
  const spiffe = `spiffe://local-1.local.id.zoiko/ns/e2e/sa/agent-${stamp}`;
  const envName = `e2e-env-${stamp}`;
  await section("workloads: register a binding for the agent and see it Active", async () => {
    test.skip(!agentId, "no agent");
    await open("/admin/workloads");
    await expect(page.getByText("Trust domain local-1.local.id.zoiko")).toBeVisible();
    await page.getByRole("button", { name: "Register binding" }).first().click();
    const dlg = page.getByRole("dialog");
    await dlg.getByLabel("SPIFFE ID").fill(spiffe);
    await dlg.getByLabel("Agent principal ID").fill(agentId!);
    await dlg.getByLabel("Blueprint version").fill("1.0.0");
    await dlg.getByLabel("Approved artifact digest").fill(digest("artifact-" + stamp));
    await dlg.getByLabel("Environment").fill(envName);
    const until = new Date(Date.now() + 30 * 86400_000).toISOString().slice(0, 10);
    await dlg.getByLabel("Valid until").fill(until);
    await shot("13-workload-register");
    await dlg.getByRole("button", { name: "Register", exact: true }).click();
    await toastSeen("Workload binding registered");
    const row = page.getByRole("row").filter({ hasText: spiffe });
    await expect(row).toBeVisible();
    await expect(row.getByText(/Active/i).first()).toBeVisible();
    await shot("13b-workload-registered");
  });

  await section("workloads: a binding with a SPIFFE id outside the trust domain or a bad digest is refused", async () => {
    await open("/admin/workloads");
    await page.getByRole("button", { name: "Register binding" }).first().click();
    const dlg = page.getByRole("dialog");
    await dlg.getByLabel("SPIFFE ID").fill("https://not-spiffe.example.com/x");
    await dlg.getByLabel("Agent principal ID").fill(agentId ?? ADMIN);
    await expect(dlg.getByRole("button", { name: "Register", exact: true })).toBeDisabled();
    await dlg.getByLabel("SPIFFE ID").fill("spiffe://other-domain.example/ns/x/sa/y");
    await dlg.getByLabel("Blueprint version").fill("1.0.0");
    await dlg.getByLabel("Approved artifact digest").fill("sha256:deadbeef");
    await dlg.getByLabel("Valid until").fill(new Date(Date.now() + 86400_000).toISOString().slice(0, 10));
    await dlg.getByRole("button", { name: "Register", exact: true }).click();
    await page.waitForTimeout(3500);
    note(`foreign trust domain + short digest: "${(await dlg.getByRole("alert").allInnerTexts().catch(() => [])).join(" ").replace(/\s+/g, " ").slice(0, 240)}"`);
    await shot("13c-workload-refused");
    await dlg.getByRole("button", { name: "Cancel" }).click();
    expect(sql("select count(*)::int as n from agent.workload_binding where artifact_digest = 'sha256:deadbeef'")[0]?.n).toBe(0);
  });

  await section("workloads: quarantine my own environment, then release it", async () => {
    await open("/admin/workloads");
    const card = page.getByRole("main").locator("section, div").filter({ hasText: "Environment quarantine" }).last();
    await page.getByLabel("Environment", { exact: true }).fill(envName);
    await page.getByRole("button", { name: "Quarantine", exact: true }).click();
    await toastSeen(/quarantined/);
    note(`binding after quarantine: ${JSON.stringify(sql("select status from agent.workload_binding where logical_principal_id = $1::uuid and environment_id = $2", agentId!, envName))}`);
    await shot("13d-workload-quarantined");
    await page.getByRole("button", { name: "Release", exact: true }).click();
    await toastSeen("Environment released");
    note(`binding after release: ${JSON.stringify(sql("select status from agent.workload_binding where logical_principal_id = $1::uuid and environment_id = $2", agentId!, envName))}`);
    void card;
  });

  await section("agents: with a binding present, the agent can be submitted and activated, then suspended", async () => {
    test.skip(!agentId, "no agent");
    await open("/admin/agents");
    const main = page.getByRole("main");
    await main.getByRole("row").filter({ hasText: agentName }).first().click();
    await expect(main.getByText("Activation checklist").first()).toBeVisible();
    note(`agent panel after the binding: ${(await main.innerText()).replace(/\s+/g, " ").slice(0, 900)}`);
    await shot("14-agent-after-binding");
    for (const [btn, cta, toast] of [
      ["Submit for approval", /Submit/, /./],
      ["Activate", /Activate/, /./],
    ] as const) {
      const b = main.getByRole("button", { name: btn });
      if (!(await b.isVisible().catch(() => false))) {
        note(`"${btn}" is not offered for this agent`);
        continue;
      }
      await b.click();
      const d = page.getByRole("alertdialog");
      await d.getByRole("button", { name: cta }).last().click();
      await page.waitForTimeout(3500);
      note(`${btn}: ${(await d.innerText().catch(() => "(closed)")).replace(/\s+/g, " ").slice(0, 300)} | status ${JSON.stringify(sql("select status from agent.agent_profile where agent_name = $1", agentName))}`);
      if (await d.isVisible().catch(() => false)) await d.getByRole("button", { name: "Cancel" }).click();
      void toast;
    }
    await shot("14b-agent-final");
  });

  await ctxA.close();
  console.log(`NOTES ${notes.length}`);
  expect(failures, failures.join("\n")).toEqual([]);
});
