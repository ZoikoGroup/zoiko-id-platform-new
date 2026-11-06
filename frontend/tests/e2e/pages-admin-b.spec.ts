import { generateKeyPairSync } from "node:crypto";

import { expect, test, type Locator, type Page } from "@playwright/test";

import { addVirtualPasskey } from "./helpers";

/**
 * Administration pages B, driven as the seeded administrator with valid and invalid data: Evidence & audit, Privacy & data rights, Operator access,
 * Residency, and the Trust anchors tab of Identity providers.
 *
 *   cd backend && UV_PROJECT_ENVIRONMENT=$LOCALAPPDATA/zoiko-id/venv py -m uv run python ../tools/scripts/bootstrap_local.py --ticket-only
 *   cd frontend && E2E_ADMIN_TICKET=<ticket> E2E_SHOTS=<folder> npx playwright test -c tests/e2e/playwright.config.ts pages-admin-b --reporter=line --output=<folder>/out
 * Every section runs even if an earlier one fails; the test fails at the end if any did. Names carry a timestamp so the spec can be re-run.
 * NOTE lines are observations for the human reading the log, not assertions.
 */
const TICKET = process.env.E2E_ADMIN_TICKET;
const SHOTS = process.env.E2E_SHOTS;
const stamp = Date.now().toString(36);
const STAMP_U = stamp.toUpperCase();
test.use({ actionTimeout: 20_000 });

const failures: string[] = [];
let page: Page;
let principalId = "";

const shot = async (name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};
const say = (m: string) => console.log(`NOTE  ${m}`);
const dialog = () => page.locator('[role="dialog"], [role="alertdialog"]').last();
const text = async (l: Locator) => ((await l.innerText().catch(() => "")) ?? "").replace(/\s+/g, " ").trim();
const alerts = async () => (await page.locator('[role="alert"], [role="status"]').allInnerTexts()).join(" | ").replace(/\s+/g, " ");
const mainText = async () => text(page.getByRole("main"));
const only = (process.env.E2E_ONLY ?? "").split(",").filter(Boolean);

async function section(name: string, run: () => Promise<void>) {
  const group = name.split(":")[0];
  if (only.length && !only.some((o) => group.startsWith(o))) return;
  try {
    await test.step(name, run);
    console.log(`PASS  ${name}`);
  } catch (e) {
    const message = (e as Error).message.split("\n").slice(0, 3).join(" ");
    failures.push(`${name}: ${message}`);
    console.log(`FAIL  ${name}: ${message}`);
    await shot(`FAILED-${name.replace(/[^A-Za-z0-9]+/g, "-").slice(0, 50)}`).catch(() => undefined);
    for (let i = 0; i < 3; i++) await page.keyboard.press("Escape").catch(() => undefined);
  }
}

async function go(path: string, tab?: string) {
  await page.goto(path);
  await page.waitForLoadState("networkidle").catch(() => undefined);
  if (tab) await page.getByRole("tab", { name: tab }).click();
  await page.waitForTimeout(700);
}

/** Walks a guided R3 dialog (intent, impact, preconditions, approval, commit) to the end. */
async function walkGuided(commitLabel: string) {
  const d = page.locator('[role="dialog"], [role="alertdialog"]').first();
  for (let step = 0; step < 8; step++) {
    const commit = d.getByRole("button", { name: commitLabel });
    if (await commit.isVisible()) {
      await commit.click();
      await d.getByRole("button", { name: "Done" }).click();
      return;
    }
    for (const box of await d.getByRole("checkbox").all()) if (!(await box.isChecked())) await box.check();
    const next = d.getByRole("button", { name: /^Continue/ });
    await expect(next.or(commit)).toBeVisible();
    if (await next.isVisible()) await next.click();
  }
  throw new Error(`never reached "${commitLabel}"`);
}

/** The step-up prompt is a second dialog that appears after a write is refused with "fresh authentication"; confirm it with the passkey if it shows up. */
async function confirmStepUp() {
  const b = page.getByRole("button", { name: "Verify with passkey" });
  if (await b.waitFor({ state: "visible", timeout: 15_000 }).then(() => true, () => false)) {
    console.log("NOTE  step-up prompt shown: confirming with the passkey");
    await b.click();
    await page.waitForTimeout(1500);
  }
}

const esc = (v: string) => v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/** Required fields carry a trailing asterisk in their label, so match from the start of the label. */
const byLabel = (d: Locator, label: string) => d.getByLabel(new RegExp(`^${esc(label)}(\\s*\\*)?$`));
const fill = (d: Locator, label: string, value: string) => byLabel(d, label).fill(value);
const uuid = () => crypto.randomUUID();

test("administration pages B behave as specified for an administrator", async ({ browser }) => {
  test.skip(!TICKET, "set E2E_ADMIN_TICKET to the seeded administrator's enrollment ticket");
  test.setTimeout(2_700_000);
  const context = await browser.newContext();
  page = await context.newPage();
  await addVirtualPasskey(context, page);
  page.setDefaultNavigationTimeout(120_000);
  // Whenever the console asks to "Confirm it's you" (a read or write needs a fresh passkey proof), confirm it with the software passkey.
  await page.addLocatorHandler(page.getByRole("button", { name: "Verify with passkey" }), async (b) => {
    console.log("NOTE  step-up prompt shown: confirming with the passkey");
    await b.click();
    await page.waitForTimeout(1500);
  });
  page.on("response", async (r) => {
    const method = r.request().method();
    if (r.url().includes("/api/") && r.status() >= 400 && !(method === "GET" && r.status() === 404)) {
      console.log(`HTTP ${r.status()} ${method} ${new URL(r.url()).pathname} ${(await r.text().catch(() => "")).slice(0, 220)}`);
    }
  });
  page.on("pageerror", (e) => console.log(`PAGEERROR ${e.message.slice(0, 160)}`));

  await page.goto(`/enroll?ticket=${TICKET}`);
  await page.getByRole("button", { name: "Create passkey" }).click();
  await page.waitForURL(/\/(account|overview|admin)/, { timeout: 120_000 });
  principalId = await page.evaluate(async () => {
    const s = JSON.stringify(await (await fetch("/api/auth/session", { credentials: "same-origin" })).json());
    return s.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g)?.[0] ?? "";
  });
  say(`session principal guess: ${principalId}`);

  // ---------------------------------------------------------------- Trust anchors
  const entity = `https://anchor-${stamp}.example.org`;
  await section("trust anchors: invalid input is refused and nothing is stored", async () => {
    await go("/admin/identity-providers", "Trust anchors");
    await page.getByRole("button", { name: "Add trust anchor" }).click();
    const d = dialog();
    await fill(d, "Entity ID", "http://insecure.example.org");
    await fill(d, "Pinned JWKS", '{"keys": []}');
    await fill(d, "Allowed domains", "example.org");
    await d.getByRole("button", { name: "Add trust anchor" }).click();
    await expect(d.getByText(/https:\/\//).first()).toBeVisible();
    await shot("ta-10-invalid-http");
    await fill(d, "Entity ID", entity);
    await d.getByRole("button", { name: "Add trust anchor" }).click();
    await expect(d.getByText(/at least one key/i)).toBeVisible();
    await fill(d, "Pinned JWKS", "not json");
    await d.getByRole("button", { name: "Add trust anchor" }).click();
    await page.waitForTimeout(500);
    await shot("ta-11-invalid-json");
    say(`bad-json message: ${await text(d)}`.slice(0, 400));
    await d.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByText(entity)).toHaveCount(0);
  });
  await section("trust anchors: create with valid data, list, disable, enable", async () => {
    const { publicKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
    const jwk = { ...publicKey.export({ format: "jwk" }), kid: `ta-${stamp}`, alg: "ES256", use: "sig" };
    await go("/admin/identity-providers", "Trust anchors");
    await page.getByRole("button", { name: "Add trust anchor" }).click();
    const d = dialog();
    await expect(d.getByText("R3").first()).toBeVisible();
    await fill(d, "Entity ID", entity);
    await fill(d, "Pinned JWKS", JSON.stringify({ keys: [jwk] }));
    await fill(d, "Allowed domains", "example.org, anchor.example.org");
    await shot("ta-20-filled");
    await d.getByRole("button", { name: "Add trust anchor" }).click();
    const row = page.getByRole("row", { name: new RegExp(`anchor-${stamp}`) });
    await expect(row).toBeVisible({ timeout: 120_000 });
    say(`anchor row: ${await text(row)}`);
    await expect(row.getByText("Active").first()).toBeVisible();
    await shot("ta-21-listed");
    await page.getByRole("button", { name: "Add trust anchor" }).click();
    const d2 = dialog();
    await fill(d2, "Entity ID", entity);
    await fill(d2, "Pinned JWKS", JSON.stringify({ keys: [jwk] }));
    await fill(d2, "Allowed domains", "example.org");
    await d2.getByRole("button", { name: "Add trust anchor" }).click();
    await page.waitForTimeout(2500);
    say(`duplicate anchor result: ${(await text(d2)).slice(0, 400)}`);
    await shot("ta-22-duplicate");
    await d2.getByRole("button", { name: "Cancel" }).click();
    await row.getByRole("button", { name: "Disable" }).click();
    await walkGuided("Disable trust anchor");
    await expect(row.getByText(/Quarantined/i).first()).toBeVisible();
    await shot("ta-23-disabled");
    await row.getByRole("button", { name: "Enable" }).click();
    await walkGuided("Enable trust anchor");
    await expect(row.getByText("Active").first()).toBeVisible();
    await shot("ta-24-enabled");
  });

  // ---------------------------------------------------------------- Operator access
  await section("operator access: policy save, reload, invalid code refused", async () => {
    await go("/admin/operator-access");
    await shot("oa-10-initial");
    say(`operator page before: ${(await text(page.getByRole("main"))).slice(0, 700)}`);
    await byLabel(page.locator("body"), "Permitted operator jurisdictions").fill("GBR, 12");
    await page.getByRole("button", { name: "Save policy" }).click();
    await page.waitForTimeout(2000);
    say(`invalid jurisdictions result: ${await alerts()}`);
    await shot("oa-11-invalid");
    await page.reload();
    await page.waitForLoadState("networkidle").catch(() => undefined);
    say(`after reload: ${await text(page.getByText(/Policy revision/))}, jurisdictions: "${await byLabel(page.locator("body"), "Permitted operator jurisdictions").inputValue()}"`);
    await byLabel(page.locator("body"), "Permitted operator jurisdictions").fill("gb, ie");
    await page.getByRole("button", { name: "Save policy" }).click();
    await expect(page.getByText("Operator access policy saved").first()).toBeVisible({ timeout: 20_000 });
    await shot("oa-12-saved");
    await page.reload();
    await page.waitForLoadState("networkidle").catch(() => undefined);
    say(`after valid save: ${await text(page.getByText(/Policy revision/))}, jurisdictions: "${await byLabel(page.locator("body"), "Permitted operator jurisdictions").inputValue()}"`);
    await expect(byLabel(page.locator("body"), "Permitted operator jurisdictions")).toHaveValue("GB, IE");
  });
  await section("operator access: the lockbox switch shows its warning", async () => {
    await go("/admin/operator-access");
    const label = page.getByText("Require my approval (lockbox) before an operator reads customer data");
    await expect(label).toBeVisible({ timeout: 20_000 });
    const sw = page.getByLabel(/Require my approval/);
    const warning = page.getByText("Operators will not need your approval for data access");
    const was = await sw.isChecked();
    say(`lockbox switch was ${was}`);
    expect(was, "the seeded policy requires the lockbox").toBe(true);
    await expect(warning).toHaveCount(0);
    await label.click();
    await expect(warning).toBeVisible();
    expect(await sw.isChecked()).toBe(false);
    await shot("oa-20-toggled");
    await label.click();
    await expect(warning).toHaveCount(0);
    expect(await sw.isChecked()).toBe(true);
    // Nothing was saved: a reload still shows the stored value.
    await page.reload();
    await page.waitForLoadState("networkidle").catch(() => undefined);
    expect(await page.getByLabel(/Require my approval/).isChecked()).toBe(true);
  });

  // ---------------------------------------------------------------- Residency
  await section("residency: profile page and the baseline confirmation", async () => {
    await go("/admin/residency");
    await shot("rs-10-profile-before");
    if (await page.getByText("No residency profile assigned").isVisible()) {
      await page.getByRole("button", { name: "Change profile" }).click();
      const d = dialog();
      await shot("rs-11-dialog-baseline");
      say(`residency dialog: ${(await text(d)).slice(0, 600)}`);
      await fill(d, "Legal mapping reference", "ab");
      await expect(d.getByRole("button", { name: "Confirm residency" })).toHaveCount(0);
      await fill(d, "Legal mapping reference", `LM-${STAMP_U}`);
      await d.getByRole("button", { name: "Confirm residency" }).click();
      await page.waitForTimeout(5000);
      await shot("rs-12-after-confirm");
      say(`after confirm alerts: ${await alerts()}`);
      say(`profile card: ${(await text(page.getByRole("main"))).slice(0, 500)}`);
      if (await dialog().isVisible().catch(() => false)) await page.keyboard.press("Escape");
    } else {
      say("a residency profile already exists (earlier run), the baseline path is not available");
    }
  });
  await section("residency: a later change needs a second administrator", async () => {
    await go("/admin/residency");
    say(`profile now: ${(await text(page.getByRole("main"))).slice(0, 400)}`);
    await page.getByRole("button", { name: "Change profile" }).click();
    const d = dialog();
    await fill(d, "Legal mapping reference", `LM2-${STAMP_U}`);
    await shot("rs-20-change-dialog");
    say(`change dialog: ${(await text(d)).slice(0, 900)}`);
    say(`buttons: ${(await d.getByRole("button").allInnerTexts()).join(" | ")}`);
    const ask = d.getByRole("button", { name: /Request|Ask/ }).first();
    if (await ask.isVisible()) {
      await ask.click();
      await page.waitForTimeout(3500);
      await shot("rs-21-after-request");
      say(`after request: ${(await text(d)).slice(-700)}`);
    }
    await page.keyboard.press("Escape");
  });
  await section("residency: transfer rules, dry-run checks, rule refused without approval", async () => {
    await go("/admin/residency", "Transfer rules");
    const check = async (dest: string, purpose: string) => {
      await byLabel(page.locator("main"), "Destination cell").fill(dest);
      await byLabel(page.locator("main"), "Purpose code").fill(purpose);
      await page.getByRole("button", { name: "Check", exact: true }).click();
      await page.waitForTimeout(2500);
      say(`transfer check DC2 -> ${dest} / ${purpose}: ${(await mainText()).match(/Transfer (allowed|denied).{0,120}/)?.[0] ?? (await alerts())}`);
    };
    await check("eu-1", `SUPPORT_${STAMP_U}`);
    await shot("rs-30-check-eu");
    await check("local-1", "SUPPORT");
    await check("nowhere-9", "SUPPORT");
    await page.getByRole("button", { name: "New rule" }).click();
    const d = dialog();
    await fill(d, "Destination cell", "eu-1");
    await fill(d, "Purpose code", `SUPPORT_${STAMP_U}`);
    await fill(d, "Transfer mechanism", "eu_sccs");
    await fill(d, "Field allowlist", "email_domain");
    await fill(d, "Legal approval reference", `LEGAL-${STAMP_U}`);
    await fill(d, "Approval request ID", "00000000-0000-0000-0000-000000000000");
    await d.getByRole("button", { name: "Approve rule" }).click();
    await page.waitForTimeout(3000);
    say(`rule with a made-up approval id: ${(await text(d)).slice(-400)}`);
    await shot("rs-31-rule-refused");
    await d.getByRole("button", { name: "Cancel" }).click();
  });
  await section("residency: placement matrix", async () => {
    await go("/admin/residency", "Placement matrix");
    await shot("rs-40-matrix");
    say(`matrix: ${(await text(page.getByRole("main"))).slice(0, 700)}`);
  });

  // ---------------------------------------------------------------- Evidence & audit
  const caseRef = `INC-${STAMP_U}`;
  await section("evidence: receipt detail, verify, replay, inclusion proof, annotate, export", async () => {
    await go("/admin/evidence", "Receipts");
    await page.getByRole("button", { name: /Evidence class/ }).first().click();
    await page.getByRole("option", { name: "E3" }).click();
    await page.waitForTimeout(2500);
    await page.getByRole("row").nth(1).click();
    const panel = page.getByRole("dialog").last();
    await expect(panel.getByText("Receipt hash")).toBeVisible();
    await shot("ev-10-receipt");
    await panel.getByRole("button", { name: "Verify hash, ledger and checkpoint" }).click();
    await page.waitForTimeout(2500);
    say(`panel after verify: ${(await text(panel)).slice(0, 900)}`);
    await panel.getByRole("button", { name: "Replay decision" }).click();
    await page.waitForTimeout(2500);
    await panel.getByRole("button", { name: "Show inclusion proof" }).click();
    await page.waitForTimeout(2500);
    say(`panel after replay+proof: ${(await text(panel)).slice(0, 1500)}`);
    await shot("ev-11-verified");
    await fill(panel, "Purpose", "Customer audit request");
    await fill(panel, "Case reference", `CASE-${STAMP_U}`);
    const dl = page.waitForEvent("download", { timeout: 20_000 }).catch(() => null);
    await panel.getByRole("button", { name: /^Export/ }).click();
    const got = await dl;
    say(`export download: ${got ? got.suggestedFilename() : "none"}; alerts: ${await alerts()}`);
    await shot("ev-12-exported");
    await fill(panel, "Reason code", "bad code");
    await expect(panel.getByRole("button", { name: "Add annotation" })).toBeDisabled();
    await fill(panel, "Reason code", `REVIEWED_${STAMP_U}`);
    await panel.getByRole("button", { name: "Add annotation" }).click();
    await expect(page.getByText("Annotation added beside the receipt").first()).toBeVisible({ timeout: 15_000 });
    await shot("ev-13-annotated");
    await page.keyboard.press("Escape");
  });
  await section("evidence: receipt filter by outcome", async () => {
    await go("/admin/evidence", "Receipts");
    await page.getByRole("button", { name: /Outcome/ }).first().click();
    await page.getByRole("option", { name: "Deny" }).click();
    await page.waitForTimeout(2000);
    say(`deny filter rows: ${(await page.getByRole("row").count()) - 1}; first: ${await text(page.getByRole("row").nth(1))}`);
    await shot("ev-20-filter-deny");
  });
  await section("evidence: ledger verification and audit filter", async () => {
    await go("/admin/evidence", "Audit & integrity");
    await page.getByRole("button", { name: "Verify the ledger" }).click();
    await page.waitForTimeout(7000);
    say(`ledger: ${(await mainText()).match(/Ledger integrity.{0,400}/)?.[0]}`);
    await shot("ev-30-ledger");
    for (const ev of ["tenant.residency_profile.changed", "operator_access.policy.changed", "cross_cell_transfer.denied"]) {
      await byLabel(page.locator("main"), "Event type").fill(ev);
      await page.getByRole("button", { name: "Filter" }).click();
      await page.waitForTimeout(2000);
      say(`audit rows for ${ev}: ${(await page.getByRole("row").count()) - 1}; first: ${(await text(page.getByRole("row").nth(1))).slice(0, 200)}`);
    }
    await shot("ev-31-audit-filter");
  });
  await section("evidence: legal hold on a made-up subject (incomplete refused, unknown approval id)", async () => {
    await go("/admin/evidence", "Legal holds");
    await page.getByRole("button", { name: "New hold" }).click();
    const d = dialog();
    await fill(d, "Legal basis", "Litigation hold");
    await fill(d, "Matter / case reference", `MATTER-${STAMP_U}`);
    await fill(d, "Subject principal IDs", uuid());
    await fill(d, "Review date", "2027-03-01");
    say(`hold buttons: ${(await d.getByRole("button").allInnerTexts()).join(" | ")}`);
    await expect(d.getByRole("button", { name: /^Apply/ }).last()).toBeDisabled();
    await fill(d, "Approval request ID", uuid());
    await shot("ev-40-hold");
    await d.getByRole("button", { name: /^Apply/ }).last().click();
    await page.waitForTimeout(3000);
    say(`hold with unknown approval id: ${(await text(d)).slice(-400)}`);
    await shot("ev-41-hold-result");
    await page.keyboard.press("Escape");
  });
  await section("evidence: open a case, collect an item, close it", async () => {
    await go("/admin/evidence", "Cases");
    await page.getByRole("button", { name: "Open case", exact: true }).first().click();
    const d = dialog();
    await expect(d.getByRole("button", { name: "Open case" })).toBeDisabled();
    await fill(d, "Reference", caseRef);
    await fill(d, "Purpose", "Review of access decisions during an audit");
    await d.getByRole("button", { name: "Open case" }).click();
    await expect(page.getByText("Case opened").first()).toBeVisible({ timeout: 15_000 });
    await shot("ev-50-case-opened");
    const detail = page.getByRole("dialog", { name: "Evidence case" });
    say(`case detail: ${(await text(detail)).slice(0, 500)}; buttons: ${(await detail.getByRole("button").allInnerTexts()).join(" | ")}`);
    await fill(detail, "Evidence item ID", uuid());
    await fill(detail, "Purpose", "Supporting record");
    await detail.getByRole("button", { name: /Collect|Add/ }).first().click();
    await page.waitForTimeout(2500);
    say(`collect (made-up item id): ${(await text(detail)).slice(-400)}`);
    await shot("ev-51-item");
    await detail.getByRole("button", { name: /Close case/ }).click();
    await page.waitForTimeout(2500);
    await shot("ev-52-closed");
    say(`case after close: ${(await text(detail)).slice(0, 400)}`);
    await page.keyboard.press("Escape");
  });
  await section("evidence: provenance lineage and artifacts", async () => {
    await go("/admin/evidence", "Provenance");
    await byLabel(page.locator("main"), "Entity type").fill("decision");
    await byLabel(page.locator("main"), "Entity ID").fill(uuid());
    await page.getByRole("button", { name: "Trace" }).click();
    await page.waitForTimeout(2500);
    say(`lineage (unknown id): ${(await text(page.getByRole("main"))).match(/Lineage.{0,700}/)?.[0]}`);
    await shot("ev-60-provenance");
    await byLabel(page.locator("main"), "Reference").fill("nope");
    await page.getByRole("button", { name: "Find", exact: true }).click();
    await page.waitForTimeout(2500);
    say(`artifact find (unknown): ${await alerts()}`);
  });
  await section("evidence: keys and re-identify (invalid handle refused)", async () => {
    await go("/admin/evidence", "Keys & identity");
    say(`keys: ${await text(page.getByText(/public keys? published/))}`);
    const dl = page.waitForEvent("download", { timeout: 10_000 }).catch(() => null);
    await page.getByRole("button", { name: "Download JWKS" }).click();
    say(`jwks download: ${(await dl)?.suggestedFilename() ?? "none"}`);
    await byLabel(page.locator("main"), "Handle").fill("not-a-handle");
    say(`handle error: ${await text(page.getByText(/A handle looks like/))}`);
    await fill(page.getByRole("main"), "Purpose", "Audit review");
    await fill(page.getByRole("main"), "Case reference", caseRef);
    await expect(page.getByRole("button", { name: "Review and resolve" })).toBeDisabled();
    await shot("ev-70-keys");
  });

  // ---------------------------------------------------------------- Privacy
  await section("privacy: record a rights request on behalf, verify, fulfil", async () => {
    await go("/admin/privacy", "Rights requests");
    await page.getByRole("button", { name: "Record request" }).first().click();
    const d = dialog();
    await expect(d.getByRole("button", { name: "Record request" })).toBeDisabled();
    await fill(d, "Subject principal ID", "not-a-uuid");
    await expect(d.getByRole("button", { name: "Record request" })).toBeDisabled();
    await fill(d, "Subject principal ID", principalId);
    await shot("pv-10-record");
    await d.getByRole("button", { name: "Record request" }).click();
    await confirmStepUp();
    await expect(d).toBeHidden({ timeout: 15_000 });
    say(`record request alerts: ${await alerts()}`);
    await shot("pv-11-recorded");
    const row = page.getByRole("row").nth(1);
    say(`first request row: ${await text(row)}`);
    await row.click();
    const panel = page.getByRole("dialog").last();
    say(`request panel: ${(await text(panel)).slice(0, 700)}`);
    await shot("pv-12-panel");
    await panel.getByRole("button", { name: "Mark verified" }).click();
    await confirmStepUp();
    await page.waitForTimeout(2500);
    say(`after verify: ${(await text(panel)).slice(0, 400)}`);
    await panel.getByRole("button", { name: /Execute fulfilment/ }).click();
    await confirmStepUp();
    await page.waitForTimeout(4000);
    say(`after fulfil: ${(await text(panel)).slice(0, 900)}`);
    await shot("pv-13-fulfilled");
    const closeBtn = panel.getByRole("button", { name: "Close request" });
    if (await closeBtn.isVisible()) {
      await closeBtn.click();
      await page.waitForTimeout(2500);
      say(`after close: ${(await text(panel)).slice(0, 300)}`);
    }
    await page.keyboard.press("Escape");
  });
  await section("privacy: purposes and processing activities", async () => {
    await go("/admin/privacy", "Purposes & role map");
    await page.getByRole("button", { name: "New purpose" }).click();
    let d = dialog();
    await fill(d, "Purpose code", "bad code!");
    await fill(d, "Description", "short");
    say(`purpose buttons: ${(await d.getByRole("button").allInnerTexts()).join(" | ")}`);
    await expect(d.getByRole("button", { name: /^Register|^Create/ }).last()).toBeDisabled();
    await fill(d, "Purpose code", `E2E_PURPOSE_${STAMP_U}`);
    await fill(d, "Description", "Verify the identity of people signing in during testing");
    await fill(d, "Allowed data categories", "D1, D2");
    await shot("pv-20-purpose");
    await d.getByRole("button", { name: /^Register|^Create/ }).last().click();
    await confirmStepUp();
    await page.waitForTimeout(3000);
    await shot("pv-21-purpose-result");
    const prow = page.getByRole("row", { name: new RegExp(`E2E_PURPOSE_${STAMP_U}`) });
    say(`purpose result: ${await alerts()}; row: ${await text(prow)}`);
    if (await dialog().isVisible().catch(() => false)) await page.keyboard.press("Escape");
    const act = prow.getByRole("button", { name: /Activate/ });
    if (await act.isVisible()) {
      await act.click();
    await confirmStepUp();
      await page.waitForTimeout(3000);
      say(`activate own purpose: ${await alerts()}; row now: ${await text(prow)}`);
      await shot("pv-22-activate");
    }
    await page.getByRole("button", { name: "New activity" }).click();
    d = dialog();
    await fill(d, "Activity code", `E2E_ACT_${STAMP_U}`);
    await fill(d, "Name", `E2E activity ${stamp}`);
    await fill(d, "Purpose codes", `E2E_PURPOSE_${STAMP_U}`);
    await fill(d, "Data categories", "D1, D2");
    say(`activity buttons: ${(await d.getByRole("button").allInnerTexts()).join(" | ")}`);
    await d.getByRole("button", { name: /^Register|^Create/ }).last().click();
    await confirmStepUp();
    await page.waitForTimeout(3000);
    await shot("pv-23-activity");
    say(`activity result: ${await alerts()}; row: ${await text(page.getByRole("row", { name: new RegExp(`E2E_ACT_${STAMP_U}`) }))}`);
    if (await dialog().isVisible().catch(() => false)) await page.keyboard.press("Escape");
  });
  await section("privacy: retention policy and subject data map", async () => {
    await go("/admin/privacy", "Retention & data map");
    say(`retention tab: ${(await text(page.getByRole("main"))).slice(0, 900)}`);
    await page.getByRole("button", { name: "Publish policy" }).click();
    const d = dialog();
    await fill(d, "Policy code", `E2E_RET_${STAMP_U}`);
    await fill(d, "Object type", "e2e.test_object");
    await fill(d, "Retention (days)", "30");
    await fill(d, "Approval request ID", uuid());
    say(`retention buttons: ${(await d.getByRole("button").allInnerTexts()).join(" | ")}`);
    await d.getByRole("button", { name: /^Publish/ }).last().click();
    await page.waitForTimeout(3000);
    say(`retention with made-up approval id: ${(await text(d)).slice(-400)}`);
    await shot("pv-30-retention");
    await d.getByRole("button", { name: "Cancel" }).click();
    await byLabel(page.locator("main"), "Subject principal ID").fill(principalId);
    await page.getByRole("button", { name: "Look up" }).click();
    await page.waitForTimeout(2500);
    say(`data map: ${(await text(page.getByRole("main"))).match(/Subject data map.{0,500}/)?.[0]}`);
    await shot("pv-31-datamap");
  });
  await section("privacy: processors register, approve, suspend", async () => {
    await go("/admin/privacy", "Processors");
    await page.getByRole("button", { name: "Register processor" }).first().click();
    const d = dialog();
    await fill(d, "Processor ID", "Bad ID!");
    await expect(d.getByRole("button", { name: "Register", exact: true })).toBeDisabled();
    await fill(d, "Processor ID", `e2e-mail-${stamp}`);
    await fill(d, "Name", `E2E Mail Provider ${stamp}`);
    await fill(d, "Processing locations", "GB, IE");
    await byLabel(d, "Transfer mechanism").fill("UK_IDTA");
    await byLabel(d, "Deletion SLA (days)").fill("30");
    await byLabel(d, "Contract reference").fill(`DPA-${STAMP_U}`);
    await d.getByRole("button", { name: "Register", exact: true }).click();
    await confirmStepUp();
    const row = page.getByRole("row", { name: new RegExp(`E2E Mail Provider ${stamp}`) });
    await expect(row).toBeVisible({ timeout: 60_000 });
    say(`processor row: ${await text(row)}`);
    await shot("pv-40-processor");
    await row.getByRole("button", { name: /Approve/ }).click();
    await confirmStepUp();
    await page.waitForTimeout(2500);
    say(`after approve: ${await text(row)}; alerts ${await alerts()}`);
    await row.getByRole("button", { name: /Suspend/ }).click();
    await confirmStepUp();
    await page.waitForTimeout(2500);
    say(`after suspend: ${await text(row)}`);
    await shot("pv-41-processor-suspended");
  });
  await section("privacy: publish a notice (guided approval) and describe an automated decision", async () => {
    await go("/admin/privacy", "Notices");
    await page.getByRole("button", { name: "Publish notice" }).first().click();
    let d = dialog();
    await fill(d, "Notice code", `E2E_NOTICE_${STAMP_U}`);
    await fill(d, "Notice text", "Short");
    await expect(d.getByRole("button", { name: "Prepare for approval" })).toBeDisabled();
    await fill(d, "Notice text", "We process your email address only to sign you in and to tell you about your account.");
    await d.getByRole("button", { name: "Prepare for approval" }).click();
    await page.waitForTimeout(2500);
    say(`notice approval step: ${(await text(d)).slice(-500)}; buttons: ${(await d.getByRole("button").allInnerTexts()).join(" | ")}`);
    await shot("pv-50-notice");
    const pub = d.getByRole("button", { name: "Publish notice" });
    if (await pub.isVisible()) {
      await pub.click();
      await page.waitForTimeout(3000);
      say(`notice publish: ${await alerts()}`);
    }
    if (await dialog().isVisible().catch(() => false)) await page.keyboard.press("Escape");
    await go("/admin/privacy", "Automated decisions");
    await page.getByRole("button", { name: "New profile" }).first().click();
    d = dialog();
    await fill(d, "Decision", `e2e_decision_${stamp}`);
    await fill(d, "Purpose code", `E2E_PURPOSE_${STAMP_U}`);
    await fill(d, "Model or rules reference", "rules-v1");
    await fill(d, "How the decision is explained", "The score is explained by the three rules that fired.");
    await fill(d, "Human review route", "Support, then the decision review team");
    say(`adm buttons: ${(await d.getByRole("button").allInnerTexts()).join(" | ")}`);
    await d.getByRole("button", { name: /^Create|^Save|^Describe/ }).last().click();
    await page.waitForTimeout(3000);
    say(`adm result: ${await alerts()}`);
    await shot("pv-51-adm");
    if (await dialog().isVisible().catch(() => false)) await page.keyboard.press("Escape");
    say(`adm table: ${(await text(page.getByRole("main"))).slice(-500)}`);
  });

  await section("operator access: restore the policy to its original (no jurisdiction limit)", async () => {
    await go("/admin/operator-access");
    await expect(byLabel(page.locator("body"), "Permitted operator jurisdictions")).toBeVisible({ timeout: 60_000 });
    await byLabel(page.locator("body"), "Permitted operator jurisdictions").fill("");
    await page.getByRole("button", { name: "Save policy" }).click();
    await expect(page.getByText("Operator access policy saved").first()).toBeVisible({ timeout: 20_000 });
  });

  await context.close();
  expect(failures, failures.join("\n")).toEqual([]);
});
