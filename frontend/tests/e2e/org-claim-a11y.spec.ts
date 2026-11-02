import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";

/**
 * ZID-UX-WF-013B acceptance AT-13: the company search, the authority form and the status page with its supporting documents work at 320 px, in landscape and at
 * 200 percent zoom, are keyboard operable, announce their state, and have no automatically detectable WCAG 2.2 AA violation.
 *
 * The person is REAL: the spec signs up through the real form (real mail from the local sink, real passkey from Chromium's virtual
 * authenticator), so the console session is live. The company data is mocked, because the real registers are outside this stack and a brand-new owner has
 * no claim. This is automated evidence, not a screen-reader run on a real device.
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
  expect(result.violations, JSON.stringify(result.violations.map((v) => [v.id, v.nodes.map((n) => [n.target, n.any.map((a) => a.message)])]))).toEqual([]);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  const offenders = wide
    ? await page.evaluate(() =>
        [...document.querySelectorAll("body *")]
          .filter((e) => e.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
          .slice(0, 5)
          .map((e) => `${e.tagName}.${String(e.className).slice(0, 60)}`),
      )
    : [];
  expect(wide, `horizontal scrolling: ${JSON.stringify(offenders)}`).toBe(false);
}


const CLAIM_ID = "01a19b00-0000-7000-8000-0000000000c1";
const PDF = Buffer.from("%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n");

const status = () => ({
  claim_id: CLAIM_ID,
  jurisdiction: "GB",
  registration_number: "00123456",
  outcome: "PROVISIONAL",
  checks: { company: "COMPLETED", person_assurance: "PENDING", authority: "PENDING", risk: "PENDING" },
  company: { status: "VERIFIED", review_reason: null, reasons: [], expires_at: SOON },
  screening: null,
});

type Proof = { proof_id: string; kind: string; media_type: string; size_bytes: number; label: string; scan_status: string; content_hash: string; uploaded_at: string };

/** The company service, as the browser sees it. `refuse` makes the next upload fail the way the server does for an unsafe file. */
async function mockClaims(page: Page, opts: { refuse?: boolean } = {}) {
  const proofs: Proof[] = [];
  await page.route("**/api/bff/v1/org-claims**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/bff", "");
    const method = route.request().method();
    if (path === "/v1/org-claims/search") {
      return route.fulfill({
        json: {
          manual_review_available: true,
          candidates: [
            { jurisdiction: "GB", registration_number: "00123456", legal_name: "Acme Widgets Limited", status: "active", confidence: 0.97, register: "companies-house" },
            { jurisdiction: "GB", registration_number: "00123457", legal_name: "Acme Widgets Holdings Limited", status: "active", confidence: 0.9, register: "companies-house" },
          ],
        },
      });
    }
    if (path === "/v1/org-claims" && method === "POST") return route.fulfill({ status: 201, json: status() });
    if (path.endsWith("/proofs") && method === "POST") {
      if (opts.refuse) {
        return route.fulfill({ status: 422, json: { error: { code: "PROOF_ACTIVE_CONTENT", message: "That PDF contains scripts or embedded content and cannot be accepted." } } });
      }
      const headers = route.request().headers();
      const p: Proof = {
        proof_id: `p${proofs.length + 1}`,
        kind: headers["x-proof-kind"] ?? "OTHER",
        media_type: "application/pdf",
        size_bytes: PDF.length,
        label: headers["x-proof-name"] ?? "document",
        scan_status: "CLEAN",
        content_hash: "sha-256:" + "a".repeat(64),
        uploaded_at: NOW,
      };
      proofs.push(p);
      return route.fulfill({ status: 201, json: p });
    }
    if (path.endsWith("/proofs")) return route.fulfill({ json: { claim_id: CLAIM_ID, proofs, limit: 20 } });
    return route.fulfill({ json: status() });
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
      await page.unrouteAll({ behavior: "ignoreErrors" });
    });

    test("the search lists matches without choosing one and opens only a provisional claim", async () => {
      await mockClaims(page);
      await page.goto("/admin/organization/claim");
      await expect(page.getByRole("heading", { name: "Find your organization" })).toBeVisible();
      await page.waitForLoadState("networkidle");
      await page.getByLabel("Registration number or legal name").fill("Acme Widgets");
      await page.locator("form").getByRole("button", { name: "Search" }).click();
      await expect(page.getByRole("list", { name: "Matching companies" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Continue with the selected company" })).toBeDisabled();
      await expect(page.getByText(/not a verified organization/i)).toBeVisible();
      await clean(page);
      await page.getByRole("button", { name: "Select", exact: true }).first().click();
      await expect(page.getByRole("button", { name: "Continue with the selected company" })).toBeEnabled();
      await clean(page);
    });

    test("the authority form names its fields, announces a missing detail and stays operable by keyboard", async () => {
      await mockClaims(page);
      await page.goto(`/admin/organization/claim/${CLAIM_ID}/authority`);
      await expect(page.getByRole("heading", { name: /authority and the company's owners/i })).toBeVisible();
      await page.waitForLoadState("networkidle");
      await expect(page.getByRole("button", { name: "Submit for checking" })).toBeDisabled();
      await page.getByLabel("Your full legal name").fill("Alice Smith");
      await page.getByLabel(/^Full legal name/).fill("Eric Owner");
      await expect(page.getByRole("alert").first()).toBeVisible();
      await clean(page);
      await page.getByLabel("Share of the company, %").fill("60");
      await expect(page.getByRole("button", { name: "Submit for checking" })).toBeEnabled();
      await page.getByLabel("Share of the company, %").focus();
      await page.keyboard.press("Tab");
      await expect(page.getByLabel("Share of the votes, %")).toBeFocused();
      await clean(page);
    });

    test("the status page adds a supporting document, announces it, and explains a refusal", async () => {
      await mockClaims(page);
      await page.goto(`/admin/organization/claim/${CLAIM_ID}/status`);
      await expect(page.getByRole("heading", { name: "Verification status" })).toBeVisible();
      await page.waitForLoadState("networkidle");
      await expect(page.getByRole("list", { name: "Verification checks" })).toBeVisible();
      await expect(page.getByText("No documents added yet.")).toBeVisible();
      await expect(page.getByRole("button", { name: "Add document" })).toBeDisabled();
      await clean(page);

      await page.getByLabel("File", { exact: true }).setInputFiles({ name: "certificate of incorporation.pdf", mimeType: "application/pdf", buffer: PDF });
      await page.getByRole("button", { name: "Add document" }).click();
      const list = page.getByRole("list", { name: "Documents on this claim" });
      await expect(list).toBeVisible();
      await expect(list.getByText("certificate of incorporation.pdf")).toBeVisible();
      await expect(list.getByText("Scanned")).toBeVisible();
      expect((await page.getByRole("button", { name: "Add document" }).boundingBox())!.height).toBeGreaterThanOrEqual(43.5);
      await clean(page);

      // a file the browser can already see is wrong is explained in words, in a live region, and nothing is sent
      let sent = false;
      await page.route("**/api/bff/v1/org-claims/*/proofs", async (route) => {
        if (route.request().method() === "POST") sent = true;
        await route.fallback();
      });
      await page.getByLabel("File", { exact: true }).setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
      await page.getByRole("button", { name: "Add document" }).click();
      await expect(page.getByText("Upload a PDF, PNG or JPEG file.").first()).toBeVisible();
      await expect(page.locator('[role="status"][aria-live="polite"]').first()).toContainText("not added");
      expect(sent).toBe(false);
      await clean(page);
    });

    test("an unsafe file the server refuses is explained and not listed", async () => {
      await mockClaims(page, { refuse: true });
      await page.goto(`/admin/organization/claim/${CLAIM_ID}/status`);
      await page.waitForLoadState("networkidle");
      await page.getByLabel("File", { exact: true }).setInputFiles({ name: "active.pdf", mimeType: "application/pdf", buffer: PDF });
      await page.getByRole("button", { name: "Add document" }).click();
      await expect(page.getByText(/scripts or embedded content/i).first()).toBeVisible();
      await expect(page.getByText("No documents added yet.")).toBeVisible();
      await clean(page);
    });
  });
}
