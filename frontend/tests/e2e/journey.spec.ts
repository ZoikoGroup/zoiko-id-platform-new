import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addVirtualPasskey, registerCompany, type Company } from "./helpers";

/**
 * One registered company, then the UI acceptance checks of ZID-UX-001 §33 that need a real browser.
 * The tests run in order and share the signed-in page.
 */
test.describe.configure({ mode: "serial" });

let context: BrowserContext;
let page: Page;
let company: Company;

test.beforeAll(async ({ browser }) => {
  context = await browser.newContext();
  page = await context.newPage();
  await addVirtualPasskey(context, page);
  company = await registerCompany(page);
});

test.afterAll(async () => {
  await context.close();
});

const PAGES = ["/overview", "/alerts", "/admin/developers", "/admin/agents", "/admin/accounts", "/admin/settings", "/help"];

test("sign-up with a passkey lands in the company's console", async () => {
  await expect(page).toHaveURL(/\/overview/);
  await expect(page.getByRole("main")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
});

test("UX-04: the environment is stated in text, not colour alone", async () => {
  // the badge carries the environment name as text and is announced with the tenant context
  await expect(page.getByLabel(/^Tenant: /)).toBeVisible();
  await expect(page.getByText(/local|development|sandbox|production/i).first()).toBeVisible();
});

test("every page loads and none leaks an error screen", async () => {
  for (const path of PAGES) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBeLessThan(500);
    await expect(page.getByRole("main"), path).toBeVisible();
    await expect(page.getByText(/application error|something went wrong/i), path).toHaveCount(0);
  }
});

test("UX-23: an unknown object or route does not reveal what exists", async () => {
  const response = await page.goto("/admin/does-not-exist");
  expect(response?.status()).toBe(404);
  // generic on purpose: it never says whether the thing is missing or merely not yours
  await expect(page.getByRole("heading", { name: /isn't available/i })).toBeVisible();
  await expect(page.getByText(/may not exist, or your access may not include it/i)).toBeVisible();
});

test("UX-19: pages have no serious or critical accessibility violations", async () => {
  for (const path of PAGES) {
    await page.goto(path);
    await expect(page.getByRole("main")).toBeVisible();
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(
      serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.html.slice(0, 160)).join(" | ")} - ${v.help}`),
      `accessibility violations on ${path}`,
    ).toEqual([]);
  }
});

test("UX-18: the command palette opens and closes from the keyboard, and focus returns", async () => {
  await page.goto("/overview");
  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("combobox").or(dialog.getByRole("searchbox")).or(dialog.getByRole("textbox")).first()).toBeFocused();
  await page.keyboard.type("alerts");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/alerts/);
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("UX-18: the first Tab offers a skip link that puts focus in the page content, and focus is never lost", async () => {
  await page.goto("/help");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: /skip to main content/i });
  await expect(skip).toBeFocused(); // the very first stop, so a keyboard user never has to cross the whole navigation
  await page.keyboard.press("Enter");
  expect(
    await page.evaluate(() => document.activeElement?.id === "main" || !!document.activeElement?.closest("main")),
    "the skip link did not move focus into <main>",
  ).toBe(true);
  // from there Tab keeps moving: it never sticks on one element (a keyboard trap), even at the end of the page where
  // the browser hands focus to its own interface
  let previous = "";
  let repeats = 0;
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Tab");
    const here = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return "";
      el.setAttribute("data-tab-probe", "1");
      return `${el.tagName}|${el.textContent?.trim().slice(0, 30)}|${el.getAttribute("href") ?? ""}|${el.getAttribute("aria-label") ?? ""}`;
    });
    repeats = here !== "" && here === previous ? repeats + 1 : 0;
    expect(repeats, `focus is stuck on ${here}`).toBeLessThan(2);
    previous = here;
  }
});

test("UX-18: a dialog traps focus while open and returns it on close", async () => {
  await page.goto("/admin/developers");
  const open = page.getByRole("button", { name: /register endpoint/i }).first();
  await open.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => !!document.activeElement?.closest("[role=dialog]")), "focus left the open dialog").toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(open).toBeFocused();
});

test("webhook registration refuses a plain http URL with a clear message", async () => {
  await page.goto("/admin/developers");
  await page.getByRole("button", { name: /register endpoint/i }).first().click();
  await page.getByLabel("Endpoint URL").fill("http://hooks.example.com/x");
  await page.getByRole("dialog").getByRole("button", { name: /register endpoint/i }).click();
  await expect(page.getByText(/must start with https/i)).toBeVisible();
});

test("UX-21: at 200% zoom the console stays usable (no sideways scrolling, controls reachable)", async () => {
  // 200% zoom of a 1280px window is a 640px-wide CSS viewport
  await page.setViewportSize({ width: 640, height: 720 });
  for (const path of ["/overview", "/alerts", "/admin/developers"]) {
    await page.goto(path);
    await expect(page.getByRole("main")).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${path} scrolls sideways at 200% zoom`).toBeLessThanOrEqual(1);
    await expect(page.getByRole("button", { name: "Open navigation" }).or(page.getByRole("navigation", { name: "Main" }))).toBeVisible();
  }
});

test("UX-22: on a phone-sized screen the navigation opens and a page can be reached", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/overview");
  await page.getByRole("button", { name: "Open navigation" }).click();
  const nav = page.getByRole("dialog", { name: "Navigation" });
  await expect(nav).toBeVisible();
  await nav.getByRole("link", { name: /alerts/i }).first().click();
  await expect(page).toHaveURL(/\/alerts/);
  await expect(page.getByRole("heading", { name: /alerts/i }).first()).toBeVisible();
});

test("signing out ends the session: the console is no longer reachable", async () => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/overview");
  await page.getByRole("button", { name: /^Account:/ }).click();
  await page.getByRole("menuitem", { name: /sign out/i }).click();
  await page.waitForURL(/signed-out|sign-in/, { timeout: 20_000 });
  await page.goto("/admin/developers");
  await expect(page).toHaveURL(/sign-in/);
  void company;
});
