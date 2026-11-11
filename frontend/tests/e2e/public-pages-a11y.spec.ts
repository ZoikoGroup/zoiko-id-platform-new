import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * ZID-GAP-001 GID-062: every page that needs no sign-in is checked in a real browser (Chromium), at desktop width and at 320 px, for
 * automatically detectable WCAG 2.2 AA violations (axe, including colour contrast), reflow without a horizontal scroll, a page title,
 * one main landmark and one level-one heading.
 *
 * It needs only the web app (npm run dev:web). The pages read some state from the cell; where the cell is down they still render.
 * This is AUTOMATED evidence. It does not replace a screen-reader run on real devices, a manual keyboard review or user testing, which a
 * person must do; axe finds only the kind of failure a program can see.
 */

const PAGES = [
  "/sign-in",
  "/sign-up",
  "/recover",
  "/signed-out",
  "/legal/privacy",
  "/legal/terms",
  "/pricing",
  "/verify/cred_00000000-0000-0000-0000-000000000000",
];

const axe = (page: Page) =>
  new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();

for (const [label, size] of [
  ["desktop", { width: 1280, height: 900 }],
  ["320px", { width: 320, height: 640 }],
] as const) {
  test.describe(label, () => {
    test.use({ viewport: size });

    for (const path of PAGES) {
      test(`${path} has no detectable WCAG 2.2 AA violation and reflows`, async ({ page }) => {
        const resp = await page.goto(path, { waitUntil: "networkidle" });
        expect(resp?.status(), `${path} answered`).toBeLessThan(500);
        expect(await page.title(), "a page title").not.toBe("");
        expect(await page.locator("main, [role=main]").count(), "one main landmark").toBe(1);
        expect(await page.locator("h1").count(), "one level-one heading").toBe(1);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow, "no horizontal scroll").toBeLessThanOrEqual(0);
        const result = await axe(page);
        expect(
          result.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`),
        ).toEqual([]);
      });
    }
  });
}
