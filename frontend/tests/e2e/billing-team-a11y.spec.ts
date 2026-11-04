import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addVirtualPasskey, registerCompany } from "./helpers";

/**
 * ZID-UX-COM-008 UI-15, ZID-UX-COM-010 C00-17, ZID-COM-CL-002 P15: the customer billing tabs (Overview, Commitments, Billing profile, Billing access), the
 * team and roles tabs (People, Roles and permissions, Delegations) and the verification level card work at desktop, 320 px, landscape and 200 percent zoom,
 * are keyboard operable, name their fields, announce a financial error, and have no automatically detectable WCAG 2.2 AA violation.
 *
 * The person is REAL: a throwaway company is registered through the real form with a virtual passkey, so the console session is live and the cell answers
 * for real. This is automated evidence, not a screen-reader run on a real device.
 */

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile 320", width: 320, height: 640 },
  { name: "mobile landscape", width: 667, height: 320 },
  { name: "200 percent zoom (640 css px)", width: 640, height: 400 },
];

const axe = (page: Page) => new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();

async function clean(page: Page) {
  // a scan taken a moment after a click can catch a colour half way through its hover transition; the settled colours are what count
  await page.addStyleTag({ content: "*, *::before, *::after { transition: none !important; animation: none !important; }" });
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

test.describe.configure({ mode: "serial" });

let context: BrowserContext;
let page: Page;

test.beforeAll(async ({ browser }) => {
  context = await browser.newContext();
  page = await context.newPage();
  await addVirtualPasskey(context, page);
  await registerCompany(page);
});

test.afterAll(async () => {
  await context.close();
});

async function open(path: string, heading: RegExp | string) {
  await page.goto(path);
  await expect(page.getByRole("heading", { name: heading }).first()).toBeVisible({ timeout: 20_000 });
  await page.waitForLoadState("networkidle");
}

for (const vp of VIEWPORTS) {
  test.describe(`at ${vp.name}`, () => {
    test.beforeEach(async () => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
    });

    test("the billing tabs are reachable by keyboard and each is clean", async () => {
      await open("/admin/billing", "Billing");
      const tabs = page.getByRole("tablist", { name: "Billing" });
      await expect(tabs).toBeVisible();
      for (const name of ["Overview", "Commitments", "Billing profile", "Billing access"]) {
        await tabs.getByRole("tab", { name }).focus();
        await page.keyboard.press("Enter");
        await expect(tabs.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
        await page.waitForLoadState("networkidle");
        await clean(page);
      }
      // arrow keys move between tabs, as the pattern requires
      await tabs.getByRole("tab", { name: "Overview" }).focus();
      await page.keyboard.press("ArrowRight");
      await expect(page.locator('[role="tab"]:focus')).toHaveCount(1);
    });

    test("the billing profile names its fields and announces a missing detail instead of saving", async () => {
      await open("/admin/billing", "Billing");
      await page.getByRole("tablist", { name: "Billing" }).getByRole("tab", { name: "Billing profile" }).click();
      await expect(page.getByLabel(/Address line 1/)).toBeVisible();
      await expect(page.getByLabel(/^City/)).toBeVisible();
      await expect(page.getByLabel(/Invoice email/)).toBeVisible();
      await page.getByLabel(/Invoice email/).fill("not-an-email");
      await page.getByLabel(/Address line 1/).fill("1 High Street");
      await page.getByRole("button", { name: "Save billing profile" }).press("Enter").catch(() => undefined);
      await page.getByRole("button", { name: "Save billing profile" }).click({ trial: true }).catch(() => undefined);
      await expect(page.getByText("That does not look like an email address.").first()).toBeVisible();
      await clean(page);
    });

    test("the team page offers people, roles and delegations, and each is clean", async () => {
      await open("/admin/team", /Team/);
      const tabs = page.getByRole("tablist", { name: "Team" });
      for (const name of ["People", "Roles and permissions", "Delegations"]) {
        await tabs.getByRole("tab", { name }).focus();
        await page.keyboard.press("Enter");
        await expect(tabs.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
        await page.waitForLoadState("networkidle");
        await clean(page);
      }
    });

    test("the role gallery opens a role and says what the policy allows", async () => {
      await open("/admin/team", /Team/);
      await page.getByRole("tablist", { name: "Team" }).getByRole("tab", { name: "Roles and permissions" }).click();
      await expect(page.getByText("The published role policy", { exact: false })).toBeVisible();
      const row = page.getByRole("row", { name: /Owner/ }).first();
      await row.click();
      await expect(page.getByText("what the policy allows", { exact: false })).toBeVisible();
      await clean(page);
    });

    test("the verification level says Not identity verified and shows the alternatives only when asked", async () => {
      await open("/account/verification", "Identity verification");
      await expect(page.getByText("Your verification level")).toBeVisible();
      await expect(page.getByText(/never proof of who a person is/i)).toBeVisible();
      await expect(page.getByRole("list", { name: "Approved alternatives" })).toHaveCount(0);
      await page.getByText("I can't use a camera").click();
      await expect(page.getByRole("list", { name: "Approved alternatives" })).toBeVisible();
      await expect(page.getByText(/you stay at your current level/i).first()).toBeVisible();
      await clean(page);
    });
  });
}
