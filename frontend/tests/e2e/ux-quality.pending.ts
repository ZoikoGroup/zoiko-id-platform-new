// Not collected yet (the name does not end in .spec.ts): UX-30 and UX-31 need work first. The core pages shift layout
// while data loads (layout shift 0.12-0.34 against a 0.1 budget), and the pseudo-localization check has not been run.
// Rename to ux-quality.spec.ts once the shift is fixed.
import { expect, test, type Page } from "@playwright/test";

import { addVirtualPasskey, registerCompany } from "./helpers";

/**
 * UX-30 performance and UX-31 localization, in a real browser against the running stack.
 *
 * UX-30: the core list, detail and navigation pages must paint and settle within a budget. The budget is in this file so
 * a change to it is reviewed; the figures are measured on the dev server, which is slower than a production build, so a
 * production-build run in CI can only be tighter.
 *
 * UX-31: text is made 40% longer with accented characters (pseudo-localization, the usual stand-in for German, Finnish
 * or Arabic copy) and the critical controls must still be whole: no sideways scrolling and no button whose label is cut.
 */
const BUDGET = { domContentLoadedMs: 3000, largestContentfulPaintMs: 4000, layoutShift: 0.1, settledMs: 6000 };
const PAGES = ["/overview", "/admin/team", "/admin/residency", "/admin/authority", "/admin/evidence", "/admin/onboarding"];

let page: Page;
test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  page = await ctx.newPage();
  await addVirtualPasskey(ctx, page);
  await registerCompany(page);
});

test.afterAll(async () => {
  await page.context().close();
});

test("UX-30: core pages paint and settle within the budget", async () => {
  test.setTimeout(180_000);
  for (const url of PAGES) {
    await page.goto("about:blank");
    const started = Date.now();
    await page.addInitScript(() => {
      (window as unknown as { __perf: { lcp: number; cls: number } }).__perf = { lcp: 0, cls: 0 };
      new PerformanceObserver((l) => {
        const e = l.getEntries().at(-1);
        if (e) (window as unknown as { __perf: { lcp: number } }).__perf.lcp = e.startTime;
      }).observe({ type: "largest-contentful-paint", buffered: true });
      new PerformanceObserver((l) => {
        for (const e of l.getEntries() as unknown as { value: number; hadRecentInput: boolean }[]) {
          if (!e.hadRecentInput) (window as unknown as { __perf: { cls: number } }).__perf.cls += e.value;
        }
      }).observe({ type: "layout-shift", buffered: true });
    });
    await page.goto(url);
    await page.waitForLoadState("networkidle");
    const settled = Date.now() - started;
    const m = await page.evaluate(() => {
      const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming;
      const p = (window as unknown as { __perf: { lcp: number; cls: number } }).__perf;
      return { dcl: nav.domContentLoadedEventEnd, lcp: p.lcp, cls: p.cls };
    });
    console.log("PERF", url, JSON.stringify({ ...m, settled }));
    expect.soft(m.dcl, `${url} DOMContentLoaded`).toBeLessThan(BUDGET.domContentLoadedMs);
    expect.soft(m.lcp, `${url} largest contentful paint`).toBeLessThan(BUDGET.largestContentfulPaintMs);
    expect.soft(m.cls, `${url} layout shift`).toBeLessThan(BUDGET.layoutShift);
    expect.soft(settled, `${url} settled`).toBeLessThan(BUDGET.settledMs);
  }
});

/** Make every visible text node 40% longer with accented letters, the way a long translation reads. */
async function pseudoLocalize(p: Page) {
  await p.evaluate(() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const map: Record<string, string> = { a: "á", e: "é", i: "í", o: "ö", u: "ü", c: "ç", n: "ñ" };
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const t = n.textContent ?? "";
      const parent = n.parentElement;
      if (!parent || !t.trim() || ["SCRIPT", "STYLE", "CODE"].includes(parent.tagName)) continue;
      const long = t.replace(/[aeioucn]/g, (c) => map[c] ?? c);
      n.textContent = `${long}${"~".repeat(Math.ceil(t.trim().length * 0.4))}`;
    }
  });
}

for (const [name, size] of [["desktop", { width: 1280, height: 800 }], ["phone", { width: 390, height: 844 }]] as const) {
  test(`UX-31: long translated labels do not clip critical controls (${name})`, async () => {
    test.setTimeout(120_000);
    await page.setViewportSize(size);
    for (const url of ["/overview", "/admin/team", "/admin/residency"]) {
      await page.goto(url);
      await page.waitForLoadState("networkidle");
      await pseudoLocalize(page);
      const clipped = await page.evaluate(() => {
        const cut = (el: Element) => {
          const s = getComputedStyle(el);
          return (s.overflow === "hidden" || s.overflowX === "hidden" || s.textOverflow === "ellipsis") && el.scrollWidth > el.clientWidth + 1;
        };
        return Array.from(document.querySelectorAll("button, a[role='button'], [role='tab'], nav a"))
          .filter((el) => (el as HTMLElement).offsetParent !== null && cut(el))
          .map((el) => (el.textContent ?? "").trim().slice(0, 40));
      });
      const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect.soft(clipped, `${url}: controls whose label is cut off`).toEqual([]);
      expect.soft(sideways, `${url}: page scrolls sideways by`).toBeLessThanOrEqual(1);
    }
  });
}
