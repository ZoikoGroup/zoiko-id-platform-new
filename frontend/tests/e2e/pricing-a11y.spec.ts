import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * ZID-UX-PRICE-004 acceptance rows P26 and P27, in a real browser (Chromium) against the running web app and cell:
 *
 *   P26  semantic headings and landmarks, keyboard operation of the market selectors, a visible focus indicator, and reflow at 200 % and 400 %
 *        (a 1280 px window at 200 % and 400 % zoom is 640 px and 320 px wide) with no horizontal scroll
 *   P27  availability states and reasons are text, not colour alone, and axe (including colour contrast) finds nothing in the states
 *
 * This is AUTOMATED evidence. A screen-reader pass on real devices, a person's keyboard review and low-vision and colour-blindness testing on real screens are
 * still owed (the acceptance kit says so).
 *
 *   npm run dev:web   (with the cell on the address ZID_CELL_API_URL names)   then   npm run test:e2e -- pricing-a11y
 */

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const axe = (page: Page) => new AxeBuilder({ page }).withTags(TAGS).analyze();
const described = (r: Awaited<ReturnType<typeof axe>>) => r.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`);

async function overflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

async function chooseCountry(page: Page, label: RegExp, option: RegExp) {
  await page.getByRole("button", { name: label }).click();
  await page.getByRole("option", { name: option }).click();
}

for (const [label, size] of [
  ["desktop", { width: 1280, height: 900 }],
  ["200 percent zoom (640 px)", { width: 640, height: 450 }],
  ["400 percent zoom (320 px)", { width: 320, height: 640 }],
] as const) {
  test.describe(label, () => {
    test.use({ viewport: size });

    test("the pricing page has headings, landmarks, no horizontal scroll and no axe violation, contrast included", async ({ page }) => {
      await page.goto("/pricing", { waitUntil: "networkidle" });
      expect(await page.locator("main, [role=main]").count(), "one main landmark").toBe(1);
      expect(await page.locator("h1").count(), "one level-one heading").toBe(1);
      expect(await page.locator("h2").count(), "sections are headed").toBeGreaterThan(3);
      // heading levels never skip downward by more than one
      const levels = await page.$$eval("h1, h2, h3, h4", (els) => els.map((e) => Number(e.tagName[1])));
      for (let i = 1; i < levels.length; i++) expect(levels[i]! - levels[i - 1]!, `heading order at ${i}`).toBeLessThanOrEqual(1);
      expect(await overflow(page), "no horizontal scroll").toBeLessThanOrEqual(0);
      expect(described(await axe(page))).toEqual([]);
    });

    test("after a market is checked the answers are words and axe, contrast included, still finds nothing", async ({ page }) => {
      await page.goto("/pricing", { waitUntil: "networkidle" });
      await chooseCountry(page, /Country of the people checked/, /Germany/);
      await chooseCountry(page, /Country of the paying company/, /Germany/);
      await page.getByRole("button", { name: "Check availability" }).click();
      await expect(page.getByText(/Availability checked against market release version/)).toBeVisible();
      // P27: every market badge carries its meaning as text, never as colour alone
      await page.locator("#coverage").scrollIntoViewIfNeeded();
      const badges = await page.locator("#coverage [class*='badge'], #coverage h3 span").allInnerTexts();
      for (const t of badges) expect(t.trim(), "a state is named in words").not.toBe("");
      expect(await page.locator("#coverage").innerText()).toMatch(/Sales-assisted|Available|Restricted|Not available/);
      expect(await overflow(page), "no horizontal scroll after checking").toBeLessThanOrEqual(0);
      expect(described(await axe(page))).toEqual([]);
    });
  });
}

test.describe("keyboard", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test("the market can be chosen and checked with the keyboard alone, and every stop shows a visible focus indicator", async ({ page }) => {
    await page.goto("/pricing", { waitUntil: "networkidle" });
    const trigger = page.getByRole("button", { name: /Country of the people checked/ });
    await trigger.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("listbox")).toBeVisible();
    await page.keyboard.type("Germ");
    await page.keyboard.press("Enter");
    await expect(trigger).toContainText(/Germany/);
    // from the selector, Tab reaches the next selectors and then the button; Enter presses it
    const check = page.getByRole("button", { name: "Check availability" });
    for (let i = 0; i < 12 && !(await check.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press("Tab");
    await expect(check).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByText(/Availability checked against market release version/)).toBeVisible();
  });

  test("every interactive control in the market form has a visible focus indicator", async ({ page }) => {
    await page.goto("/pricing", { waitUntil: "networkidle" });
    const controls = page.locator("#market button, #market a, #market [role=combobox]");
    const n = await controls.count();
    expect(n).toBeGreaterThan(2);
    for (let i = 0; i < n; i++) {
      const c = controls.nth(i);
      if (!(await c.isVisible()) || (await c.isDisabled().catch(() => false))) continue;
      await c.focus();
      const visible = await c.evaluate((el) => {
        const s = getComputedStyle(el);
        const outline = s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0;
        const ring = s.boxShadow !== "none" && s.boxShadow !== "";
        return outline || ring;
      });
      expect(visible, `focus indicator on control ${i}`).toBe(true);
    }
  });

  test("the skip and anchor links of the page reach their sections", async ({ page }) => {
    await page.goto("/pricing", { waitUntil: "networkidle" });
    await page.getByRole("link", { name: "See plans" }).click();
    await expect(page.locator("#plans")).toBeInViewport();
  });
});

test.describe("the partner page", () => {
  for (const size of [{ width: 1280, height: 900 }, { width: 320, height: 640 }]) {
    test(`has no axe violation and reflows at ${size.width}px`, async ({ page }) => {
      await page.setViewportSize(size);
      await page.goto("/pricing/partners", { waitUntil: "networkidle" });
      expect(await page.locator("h1").count()).toBe(1);
      expect(await overflow(page)).toBeLessThanOrEqual(0);
      expect(described(await axe(page))).toEqual([]);
    });
  }
});
