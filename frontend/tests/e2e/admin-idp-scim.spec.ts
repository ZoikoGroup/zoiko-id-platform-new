import { generateKeyPairSync, sign, type KeyObject } from "node:crypto";

import { expect, test, type Page } from "@playwright/test";

import { addVirtualPasskey } from "./helpers";

/**
 * The identity-provider and provisioning screens, driven as an administrator with real data.
 *
 * Needs a running stack and the seeded administrator's first-passkey link (one use):
 *   cd backend && python -m uv run python ../tools/scripts/bootstrap_local.py --ticket-only
 *   E2E_ADMIN_TICKET=<ticket from the link> npm run test:e2e -- admin-idp-scim
 * Set E2E_SHOTS to a folder to keep a screenshot of every step. Every section runs even if an earlier one fails, so one run shows
 * everything that is wrong; the test fails at the end if any section did.
 */
const TICKET = process.env.E2E_ADMIN_TICKET;
const SHOTS = process.env.E2E_SHOTS;
const stamp = Date.now().toString(36);

test.use({ actionTimeout: 20_000 }); // a step that cannot find its target fails in seconds, not minutes

const failures: string[] = [];
let page: Page;
const API = process.env.E2E_CELL_API_URL ?? "http://localhost:8000";
/** The connector registered in the provisioning section: its client id and the private key that matches the public keys pasted into the page. */
let connector: { clientId: string; privateKey: KeyObject; kid: string } | undefined;

const b64 = (v: Buffer | string) => Buffer.from(v).toString("base64url");
/** A private_key_jwt client assertion (ES256) for the cell's token endpoint. */
function clientAssertion(c: NonNullable<typeof connector>): string {
  const now = Math.floor(Date.now() / 1000);
  const head = b64(JSON.stringify({ alg: "ES256", typ: "JWT", kid: c.kid }));
  const body = b64(JSON.stringify({ iss: c.clientId, sub: c.clientId, aud: API, jti: crypto.randomUUID(), iat: now, exp: now + 60 }));
  const sig = sign("sha256", Buffer.from(`${head}.${body}`), { key: c.privateKey, dsaEncoding: "ieee-p1363" });
  return `${head}.${body}.${b64(sig)}`;
}

const shot = async (name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};

async function section(name: string, run: () => Promise<void>) {
  try {
    await test.step(name, run);
    console.log(`PASS  ${name}`);
  } catch (e) {
    const message = (e as Error).message.split("\n")[0];
    failures.push(`${name}: ${message}`);
    console.log(`FAIL  ${name}: ${message}`);
    await shot(`FAILED-${name.replace(/[^A-Za-z0-9]+/g, "-").slice(0, 40)}`).catch(() => undefined);
    await page.keyboard.press("Escape").catch(() => undefined);
  }
}

/** Walks a guided R3 change (intent, impact, preconditions, approval, commit): acknowledges what it asks, continues, and commits. */
async function walkGuided(commitLabel: string) {
  const dialog = page.locator('[role="dialog"], [role="alertdialog"]').first();
  for (let step = 0; step < 8; step++) {
    const commit = dialog.getByRole("button", { name: commitLabel });
    if (await commit.isVisible()) {
      await commit.click();
      await dialog.getByRole("button", { name: "Done" }).click(); // the result step closes with Done
      return;
    }
    for (const box of await dialog.getByRole("checkbox").all()) if (!(await box.isChecked())) await box.check();
    const next = dialog.getByRole("button", { name: /^Continue/ });
    await expect(next.or(commit)).toBeVisible();
    if (await next.isVisible()) await next.click();
  }
  throw new Error(`never reached "${commitLabel}"`);
}

/** What the customer's HR system does: sign in with its private key, then create, find, deactivate and delete a user through SCIM. */
async function provisionAsConnector() {
  expect(connector?.clientId, "the page showed the connector's client id").toBeTruthy();
  const tokenRes = await fetch(`${API}/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "client_credentials",
      scope: "scim:provision",
      client_id: connector!.clientId,
      client_assertion_type: "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
      client_assertion: clientAssertion(connector!),
    }),
  });
  const tokenBody = await tokenRes.text();
  expect(tokenRes.status, `token endpoint: ${tokenBody.slice(0, 200)}`).toBe(200);
  const auth = { authorization: `Bearer ${(JSON.parse(tokenBody) as { access_token: string }).access_token}`, "content-type": "application/scim+json" };
  const userName = `e2e.user.${stamp}@example.com`;
  const created = await fetch(`${API}/scim/v2/Users`, {
    method: "POST",
    headers: auth,
    body: JSON.stringify({
      schemas: ["urn:ietf:params:scim:schemas:core:2.0:User"],
      userName,
      externalId: `emp-${stamp}`,
      displayName: "E2E Test User",
      name: { givenName: "E2E", familyName: "User" },
      emails: [{ value: userName, type: "work", primary: true }],
      title: "Engineer",
      active: true,
    }),
  });
  expect(created.status, await created.clone().text()).toBe(201);
  const user = (await created.json()) as { id: string; userName: string; title?: string };
  expect(user.userName).toBe(userName);
  expect(user.title).toBe("Engineer");
  const found = (await (await fetch(`${API}/scim/v2/Users?filter=${encodeURIComponent(`userName eq "${userName}"`)}`, { headers: auth })).json()) as { totalResults: number };
  expect(found.totalResults).toBe(1);
  const patched = await fetch(`${API}/scim/v2/Users/${user.id}`, {
    method: "PATCH",
    headers: auth,
    body: JSON.stringify({ schemas: ["urn:ietf:params:scim:api:messages:2.0:PatchOp"], Operations: [{ op: "replace", path: "active", value: false }] }),
  });
  expect(patched.status, await patched.clone().text()).toBe(200);
  expect(((await patched.json()) as { active: boolean }).active).toBe(false);
  expect((await fetch(`${API}/scim/v2/Users/${user.id}`, { method: "DELETE", headers: auth })).status).toBe(204);
  console.log(`NOTE  the connector signed in with its key, then created, found, deactivated and deleted ${userName} through SCIM`);
}

test("identity providers and provisioning work end to end for an administrator", async ({ browser }) => {
  test.skip(!TICKET, "set E2E_ADMIN_TICKET to the seeded administrator's enrollment ticket");
  test.setTimeout(420_000);
  const context = await browser.newContext();
  page = await context.newPage();
  await addVirtualPasskey(context, page);
  page.on("response", async (r) => {
    const method = r.request().method();
    if (method !== "GET" && r.url().includes("/api/") && r.status() >= 400) {
      const sent = ((await r.request().allHeaders())["x-zid-csrf"] ?? "NONE").slice(0, 8);
      const now = await page.evaluate(async () => ((await (await fetch("/api/auth/session", { credentials: "same-origin" })).json()) as { csrf?: string }).csrf?.slice(0, 8)).catch(() => "?");
      console.log(`HTTP ${r.status()} ${method} ${new URL(r.url()).pathname} sent-csrf=${sent} session-csrf-now=${now} ${(await r.text().catch(() => "")).slice(0, 160)}`);
    }
  });

  await page.goto(`/enroll?ticket=${TICKET}`);
  await page.getByRole("button", { name: "Create passkey" }).click();
  await page.waitForURL(/\/(account|overview|admin)/, { timeout: 30_000 });

  await section("sign in as the administrator and see the administration menu", async () => {
    await page.goto("/overview");
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Identity providers" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Provisioning" })).toBeVisible();
    await shot("00-overview");
  });

  await section("identity providers: connect a SAML provider (Mock SAML) and see it listed", async () => {
    const name = `Mock SAML ${stamp}`;
    await page.goto("/admin/identity-providers");
    await page.getByRole("button", { name: "Add provider" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByText("A SAML 2.0 provider").click();
    await dialog.getByLabel("Name people will see").fill(name);
    await dialog.getByLabel("The provider's metadata address").fill("https://mocksaml.com/api/saml/metadata");
    await dialog.getByRole("button", { name: "Find the rest for me" }).click();
    await expect(dialog.getByText("Found it")).toBeVisible({ timeout: 30_000 });
    await shot("10-saml-found");
    await dialog.getByRole("button", { name: "Connect provider" }).click();
    const connected = page.getByRole("dialog", { name: "Provider connected" });
    const duplicate = dialog.getByText(/already exists/i);
    await expect(connected.or(duplicate)).toBeVisible({ timeout: 30_000 });
    if (await duplicate.isVisible()) {
      console.log("NOTE  Mock SAML was connected by an earlier run: the backend refuses the same issuer twice, as it should");
      await shot("11-saml-duplicate-refused");
      await dialog.getByRole("button", { name: "Cancel" }).click();
      return;
    }
    await expect(connected.getByText(/\/federation\/saml\/acs/).first()).toBeVisible();
    await shot("11-saml-connected");
    await connected.getByRole("button", { name: "Done" }).click();
    await expect(page.getByRole("cell", { name }).first()).toBeVisible();
    await shot("12-saml-listed");
  });

  await section("identity providers: connect Google Workspace (OIDC) and see it listed", async () => {
    await page.goto("/admin/identity-providers");
    await page.getByRole("button", { name: "Add provider" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByText("Google Workspace").first().click();
    await dialog.getByLabel("Name people will see").fill(`Google ${stamp}`);
    await dialog.getByLabel("Client ID").fill(`${Date.now()}-e2e.apps.googleusercontent.com`);
    await dialog.getByRole("button", { name: "Find the rest for me" }).click();
    await expect(dialog.getByText("Found it")).toBeVisible({ timeout: 30_000 });
    await dialog.locator("summary", { hasText: "Advanced settings" }).click();
    await dialog.getByLabel("Name of the stored secret").fill("google-workspace");
    await shot("20-google-found");
    await dialog.getByRole("button", { name: "Connect provider" }).click();
    const listed = page.getByRole("cell", { name: `Google ${stamp}` }).first();
    const duplicate = dialog.getByText(/already exists/i);
    await expect(listed.or(duplicate)).toBeVisible({ timeout: 30_000 });
    if (await duplicate.isVisible()) {
      console.log("NOTE  Google Workspace was connected by an earlier run: the backend refuses the same issuer twice, as it should");
      await shot("21-google-duplicate-refused");
      await dialog.getByRole("button", { name: "Cancel" }).click();
      return;
    }
    await shot("21-google-listed");
  });

  await section("identity providers: a private key in the keys box is refused", async () => {
    await page.goto("/admin/identity-providers");
    await page.getByRole("button", { name: "Add provider" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByText("Another OpenID Connect provider").click();
    await dialog.getByLabel("Name people will see").fill(`Bad ${stamp}`);
    await dialog.getByLabel("The provider's address (issuer)").fill("https://login.example.com");
    await dialog.getByLabel("Client ID").fill("client-1");
    await dialog.locator("summary", { hasText: "Details" }).click();
    await dialog.getByLabel("Sign-in address (authorization endpoint)").fill("https://login.example.com/authorize");
    await dialog.getByLabel("Token address (token endpoint)").fill("https://login.example.com/token");
    await dialog
      .getByLabel("Keys (the provider's public signing keys)")
      .fill(JSON.stringify({ kty: "EC", crv: "P-256", x: "AQAB", y: "AQAB", d: "secret" }));
    await dialog.getByRole("button", { name: "Connect provider" }).click();
    await expect(dialog.getByText("This contains a private key")).toBeVisible();
    await shot("30-private-key-refused");
    await dialog.getByRole("button", { name: "Cancel" }).click();
  });

  await section("identity providers: edit, disable and enable the connected SAML provider", async () => {
    await page.goto("/admin/identity-providers");
    const row = page.getByRole("row", { name: /saml\.example\.com\/entityid/ }).first();
    await expect(row).toBeVisible();
    const menu = async (item: string) => {
      await row.getByRole("button", { name: /^Actions for / }).click();
      await page.getByRole("menuitem", { name: item }).click();
    };
    if (await row.getByText("Quarantined").first().isVisible()) {
      await menu("Enable"); // an earlier run left it disabled
      await walkGuided("Enable integration");
      await expect(row.getByText("Active").first()).toBeVisible();
    }
    const renamed = `Mock SAML edited ${stamp}`;
    await menu("Edit settings");
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Name people will see").fill(renamed);
    await dialog.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("cell", { name: renamed }).first()).toBeVisible();
    await shot("50-idp-edited");
    await menu("Disable");
    await walkGuided("Disable integration");
    await expect(row.getByText(/Quarantined|Disabled/i).first()).toBeVisible();
    await shot("51-idp-disabled");
    await menu("Enable");
    await walkGuided("Enable integration");
    await expect(row.getByText("Active").first()).toBeVisible();
    await shot("52-idp-enabled");
  });

  await section("provisioning: register a SCIM source from public keys, suspend it, resume it", async () => {
    const name = `Workday ${stamp}`;
    const { publicKey, privateKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
    const jwk = { ...publicKey.export({ format: "jwk" }), kid: `e2e-${stamp}`, alg: "ES256", use: "sig" };
    await page.goto("/admin/provisioning");
    await page.getByRole("button", { name: "Register source" }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Display name").fill(name);
    await dialog.getByLabel("Public keys (JWKS)").fill(JSON.stringify({ keys: [jwk] }));
    await dialog.getByRole("button", { name: "Register source" }).click();
    await expect(dialog.getByText(`${name} is active`)).toBeVisible({ timeout: 30_000 });
    await shot("40-scim-registered");
    const copyClient = await dialog.getByRole("button", { name: /^Copy client ID / }).first().getAttribute("aria-label");
    connector = { clientId: (copyClient ?? "").replace(/^Copy client ID /, ""), privateKey, kid: jwk.kid };
    await dialog.getByRole("button", { name: "Done" }).click();
    const row = page.getByRole("row", { name: new RegExp(name) });
    await expect(row).toBeVisible();
    await provisionAsConnector();
    await row.getByRole("button", { name: "Suspend" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Suspend source" }).click();
    await expect(row.getByRole("button", { name: "Resume" })).toBeVisible({ timeout: 30_000 });
    await shot("41-scim-suspended");
    await row.getByRole("button", { name: "Resume" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Resume source" }).click();
    await expect(row.getByRole("button", { name: "Suspend" })).toBeVisible({ timeout: 30_000 });
    await shot("42-scim-resumed");
  });

  await context.close();
  expect(failures, failures.join("\n")).toEqual([]);
});
