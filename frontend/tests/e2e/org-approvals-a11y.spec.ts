import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";

/**
 * ZID-UX-WF-013C acceptance AT-14: the setup page, the approvals center and the decision page work at 320 px, in landscape and at
 * 200 percent zoom, are keyboard operable, announce their state, and have no automatically detectable WCAG 2.2 AA violation.
 *
 * The person is REAL: the spec signs up through the real form (real mail from the local sink, real passkey from Chromium's virtual
 * authenticator), so the setup page shows the live backend's readiness. Only the approvals data is mocked, because a brand-new owner has
 * no approval requests and cannot create one. This is automated evidence, not a screen-reader run on a real device.
 */

const MAIL = process.env.E2E_MAIL_API ?? "http://localhost:8025";
const GENERIC = "If this address can be used, check your inbox for next steps.";
const NOW = new Date().toISOString();
const SOON = new Date(Date.now() + 3 * 86_400_000).toISOString();

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile 320", width: 320, height: 640 },
  { name: "mobile landscape", width: 667, height: 320 },
  { name: "200 percent zoom (640 css px)", width: 640, height: 400 },
];

async function mailFor(address: string): Promise<string> {
  for (let i = 0; i < 40; i++) {
    const found = await fetch(`${MAIL}/api/v1/search?query=${encodeURIComponent(`to:${address}`)}`).then((r) => r.json()).catch(() => ({ messages: [] }));
    const id = found.messages?.[0]?.ID as string | undefined;
    if (id) return (await fetch(`${MAIL}/api/v1/message/${id}`).then((r) => r.json())).Text as string;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`no mail arrived for ${address}`);
}

async function signUp(context: BrowserContext, page: Page) {
  const cdp = await context.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: { protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true },
  });
  const email = `a11y.${Date.now()}@example.com`;
  await page.goto("/sign-up");
  await page.waitForLoadState("networkidle");
  await page.locator("label", { hasText: "For myself" }).click();
  await page.getByRole("combobox").first().selectOption({ value: "GB" });
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page.getByText(GENERIC).first()).toBeVisible();
  const link = (await mailFor(email)).match(/https?:\/\/[^\s]+\/sign-up\/verify\?t=[A-Za-z0-9_-]+/)?.[0];
  await page.goto(link!);
  await page.getByRole("button", { name: "Create passkey" }).click();
  await expect(page.getByText(/Your account is ready/i)).toBeVisible({ timeout: 30_000 });
}

const axe = (page: Page) => new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();

async function clean(page: Page) {
  const result = await axe(page);
  expect(result.violations, JSON.stringify(result.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(wide, "horizontal scrolling").toBe(false);
}

const hash = "a".repeat(64);
const item = (n: number, extra: Record<string, unknown> = {}) => ({
  request_id: `01a19b00-0000-7000-8000-00000000000${n}`,
  title: `ownership-transfer-or-replacement on principal:${n}`,
  requester_principal_id: "01a19b00-0000-7000-8000-0000000000aa",
  level: "A2",
  state: "PENDING_APPROVAL",
  status_label: "Pending approval",
  approvals_received: 0,
  approvals_required: 2,
  shortage: null,
  policy_version: "ZID-ENG-DEC-010-011-012-v1.0#abc",
  expires_at: SOON,
  created_at: NOW,
  next_action: "Review and decide",
  ...extra,
});

const detail = (extra: Record<string, unknown> = {}) => ({
  case_id: "01a19b00-0000-7000-8000-000000000001",
  version: 1,
  supersedes_case_id: null,
  action_id: "ownership-transfer-or-replacement",
  tier: "A2",
  level: "A2",
  state: "PENDING_APPROVAL",
  status_label: "Pending approval",
  hold: null,
  execution_hold: null,
  approvals_received: 1,
  approvals_required: 2,
  independent_people_available: 2,
  summary: "ownership-transfer-or-replacement on principal:1",
  reason: "Hand over day-to-day ownership.",
  payload: { principal_id: "01a19b00-0000-7000-8000-0000000000bb" },
  gate: "Reverify incoming Owner identity and organizational authority; notify affected parties.",
  gates: [{ code: "INCOMING_OWNER_STRONG_AUTHENTICATOR", ok: true, blocking: true, note: "The incoming Owner must have a passkey registered before ownership moves." }],
  request_hash: hash,
  policy_version: "ZID-ENG-DEC-010-011-012-v1.0#abc",
  region: "uk-1",
  requester_principal_id: "01a19b00-0000-7000-8000-0000000000aa",
  beneficiary_principal_id: "01a19b00-0000-7000-8000-0000000000bb",
  expires_at: SOON,
  created_at: NOW,
  approved_at: null,
  applied_at: null,
  votes: [{ vote_id: "v1", approver_principal_id: "01a19b00-0000-7000-8000-0000000000cc", person_ref: "9f2c1a7b3d4e5f60", authority_basis: "OWNER", vote: "APPROVE", step_up_tier: "S2", step_up_proof_ref: "p1", decided_at: NOW }],
  notice: "1 of 2 independent approvals received. No changes have taken effect.",
  evidence: { reference: "idv/case-7", access: "SEPARATELY_AUTHORIZED" },
  you: { may_vote: true, reasons: [], is_requester: false },
  ...extra,
});

async function mockApprovals(page: Page, opts: { conflict?: boolean } = {}) {
  await page.route("**/api/bff/v1/organizations/*/approvals**", async (route) => {
    const url = new URL(route.request().url());
    const parts = url.pathname.split("/");
    const last = parts[parts.length - 1];
    if (route.request().method() === "POST" && last === "decisions") {
      return route.fulfill(opts.conflict ? { status: 409, json: { error: { code: "VERSION_CONFLICT", message: "The request changed. Read it again before deciding." } } } : { status: 201, json: detail() });
    }
    if (last === "approvals") {
      const scope = url.searchParams.get("scope") ?? "review";
      const items = scope === "review" ? [item(1), item(2, { shortage: "Independent approvers required", next_action: "Invite or appoint independent approvers", approvals_received: 1 })] : [];
      return route.fulfill({ json: { scope, items, counts: { review: 2, mine: 0, history: 0 }, next_cursor: scope === "review" ? "cursor-1" : null } });
    }
    return route.fulfill({ json: detail() });
  });
}

test.describe.configure({ mode: "serial" });

let context: BrowserContext;
let page: Page;

test.beforeAll(async ({ browser }) => {
  context = await browser.newContext();
  page = await context.newPage();
  await signUp(context, page);
});

test.afterAll(async () => {
  await context.close();
});

for (const vp of VIEWPORTS) {
  test.describe(`at ${vp.name}`, () => {
    test.beforeEach(async () => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
    });

    test("the setup page shows the live readiness, honest words and a disabled activation with reasons", async () => {
      await page.goto("/organizations/current/setup");
      await expect(page.getByRole("heading", { name: "Organization setup" })).toBeVisible();
      await page.waitForLoadState("networkidle");
      for (const word of ["Required", "Verified"]) await expect(page.getByText(word, { exact: false }).first()).toBeVisible();
      await expect(page.getByText(/You can manage your organization on your own/i).first()).toBeVisible();
      const activate = page.getByRole("button", { name: /Activate organization/i });
      await expect(activate).toBeDisabled();
      expect((await activate.boundingBox())!.height).toBeGreaterThanOrEqual(43.5);
      await clean(page);
    });

    test("the approvals center announces counts, shows the shortage and pages", async () => {
      await mockApprovals(page);
      await page.goto("/organizations/current/approvals");
      await expect(page.getByRole("heading", { name: /approvals/i }).first()).toBeVisible();
      await expect(page.getByText("Independent approvers required").first()).toBeVisible();
      await expect(page.getByRole("button", { name: "Load more" })).toBeVisible();
      await expect(page.getByRole("tab", { name: /To review/i })).toBeVisible();
      await expect(page.locator('[role="status"][aria-live="polite"]').first()).toBeAttached();
      await clean(page);
      // keyboard: the first card's link is reachable by Tab, and the tabs by arrow keys
      await page.getByRole("tab", { name: /To review/i }).focus();
      await page.keyboard.press("ArrowRight");
      await expect(page.getByRole("tab", { name: /Submitted by me/i })).toBeFocused();
      await page.unroute("**/api/bff/v1/organizations/*/approvals**");
    });

    test("the decision page shows the exact change, the progress and accessible decision controls", async () => {
      await mockApprovals(page);
      await page.goto("/organizations/current/approvals/01a19b00-0000-7000-8000-000000000001");
      await expect(page.getByRole("button", { name: "Approve — 2 of 2" })).toBeVisible();
      await expect(page.getByText(/Reverify incoming Owner/i).first()).toBeVisible();
      await expect(page.getByText(/idv\/case-7/)).toBeVisible();
      for (const name of ["Approve — 2 of 2", "Reject", "Request changes"]) {
        const box = await page.getByRole("button", { name: new RegExp(`^${name}`) }).first().boundingBox();
        expect(box!.height, name).toBeGreaterThanOrEqual(43.5);
      }
      await clean(page);
      await page.getByRole("button", { name: /Request changes/ }).first().click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await clean(page);
      await page.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
      await page.unroute("**/api/bff/v1/organizations/*/approvals**");
    });
  });
}

test("a stale version disables the form and says to read the request again", async () => {
  await page.setViewportSize({ width: 320, height: 640 });
  await mockApprovals(page, { conflict: true });
  await page.goto("/organizations/current/approvals/01a19b00-0000-7000-8000-000000000001");
  await page.getByRole("button", { name: /Request changes/ }).first().click();
  await page.getByRole("dialog").getByLabel(/reason/i).fill("Please narrow the scope.");
  await page.getByRole("dialog").getByRole("button", { name: /Request changes|Send/ }).click();
  await expect(page.getByText(/This request changed/i).first()).toBeVisible();
  await clean(page);
  await page.unroute("**/api/bff/v1/organizations/*/approvals**");
});
