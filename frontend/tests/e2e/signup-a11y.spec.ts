import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * ZID-UX-WF-013A AC-10: the sign-up form works at 320 px, in landscape and at 200 percent zoom, with keyboard and
 * announced errors, and has no automatically detectable WCAG 2.2 AA violation in any of its states.
 *
 * It needs only the web app (npm run dev:web): the relay is answered by the test, so no cell API or database is involved.
 * This is automated evidence, not a screen-reader run on a real device; that stays with QA.
 */

const GENERIC = "If this address can be used, check your inbox for next steps.";
const CAPS = {
  notice_version: "2026-10-22",
  locale: "und",
  nonce: "1.abc",
  journeys: ["PERSONAL", "ORGANIZATION"],
  identity_providers: [],
  authenticators: { primary: "PASSKEY", fallback: "GOVERNED_RECOVERY" },
  country: null,
};

const country = (code: string) => {
  const states: Record<string, object> = {
    GB: { account: "AVAILABLE", routing: { state: "HERE", url: null }, organization_claims: "AVAILABLE", message: null },
    DE: { account: "AVAILABLE_IN_ANOTHER_REGION", routing: { state: "OTHER_CELL", url: null }, organization_claims: "NOT_AVAILABLE_YET", message: null },
  };
  const found = states[code] ?? {
    account: "NOT_AVAILABLE_YET",
    routing: { state: "UNAVAILABLE", url: null },
    organization_claims: "NOT_AVAILABLE_YET",
    message: "Zoiko iD accounts are not available for this country yet. Nothing has been stored.",
  };
  return { code, ...found };
};

async function mock(page: Page, status: object | null = null) {
  await page.route("**/api/registration/capabilities*", async (route) => {
    const code = new URL(route.request().url()).searchParams.get("country");
    await route.fulfill({ json: { ...CAPS, country: code ? country(code.toUpperCase()) : null } });
  });
  await page.route("**/api/registration/intents", (route) =>
    route.fulfill({ status: 202, json: { request_id: "R".repeat(24), status: "VERIFICATION_REQUESTED", message: GENERIC } }),
  );
  if (status) await page.route("**/api/registration/status", (route) => route.fulfill({ json: status }));
}

const axe = (page: Page) => new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();

const noOverflow = async (page: Page) => {
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(wide, "horizontal scrolling").toBe(false);
};

const viewports = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "tablet", width: 800, height: 1000 },
  { name: "mobile 320", width: 320, height: 640 },
  { name: "mobile landscape", width: 667, height: 320 },
  { name: "200 percent zoom at 1280 (640 css px)", width: 640, height: 400 },
];

for (const vp of viewports) {
  test.describe(`sign-up at ${vp.name}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    test("shows the required controls, reflows without scrolling and has no axe violation", async ({ page }) => {
      await mock(page);
      await page.goto("/sign-up");
      await expect(page.getByRole("heading", { name: "Create your Zoiko iD." })).toBeVisible();
      await expect(page.getByText("Your secure starting point for digital trust.")).toBeVisible();
      const radios = page.getByRole("radio");
      await expect(radios).toHaveCount(2);
      await expect(page.getByLabel("For myself")).not.toBeChecked();
      await expect(page.getByLabel("For an organization")).not.toBeChecked();
      await expect(page.getByLabel("Email address")).toHaveAttribute("autocomplete", "email");
      await expect(page.getByRole("button", { name: "Continue securely" })).toBeVisible();
      await expect(page.getByRole("link", { name: /sign in/i }).first()).toBeVisible();
      await noOverflow(page);
      const result = await axe(page);
      expect(result.violations, JSON.stringify(result.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
    });

    test("every control is at least 44 px and reachable by keyboard in a logical order", async ({ page }) => {
      await mock(page);
      await page.goto("/sign-up");
      await page.waitForLoadState("networkidle"); // measure the hydrated page, not the server HTML
      await expect(page.getByLabel("Email address")).toBeVisible();
      // a radio's hit area is its label row; the others are measured directly
      const rows = [page.locator("label", { hasText: "For myself" }), page.locator("label", { hasText: "For an organization" }), page.getByLabel("Email address"), page.getByRole("button", { name: "Continue securely" })];
      for (const target of rows) {
        const box = await target.first().boundingBox();
        expect(box, "control is on screen").not.toBeNull();
        expect(box!.height).toBeGreaterThanOrEqual(43.5);
      }
      const order: string[] = [];
      for (let i = 0; i < 14; i++) {
        await page.keyboard.press("Tab");
        order.push(
          await page.evaluate(() => {
            const el = document.activeElement as HTMLElement | null;
            return el ? `${el.tagName}:${el.getAttribute("type") ?? ""}:${(el.getAttribute("aria-label") ?? el.textContent ?? "").trim().slice(0, 24)}` : "";
          }),
        );
      }
      const idx = (needle: string) => order.findIndex((o) => o.includes(needle));
      expect(idx("INPUT:radio")).toBeGreaterThanOrEqual(0);
      expect(idx("INPUT:email")).toBeGreaterThan(idx("INPUT:radio"));
      expect(idx("Continue securely")).toBeGreaterThan(idx("INPUT:email"));
    });
  });
}

test.describe("states", () => {
  test.use({ viewport: { width: 320, height: 640 } });

  test("an invalid submission announces an error summary, keeps the values and passes axe", async ({ page }) => {
    await mock(page);
    await page.goto("/sign-up");
    await page.getByLabel("Email address").fill("not-an-email");
    await page.getByRole("button", { name: "Continue securely" }).click();
    const alert = page.locator('[role="alert"][tabindex="-1"]');
    await expect(alert).toBeVisible();
    await expect(alert).toBeFocused();
    await expect(page.getByLabel("Email address")).toHaveValue("not-an-email");
    const result = await axe(page);
    expect(result.violations).toEqual([]);
  });

  test("an unavailable country is stated honestly and cannot be submitted", async ({ page }) => {
    await mock(page);
    await page.goto("/sign-up");
    await page.locator("label", { hasText: "For myself" }).click();
    await page.getByLabel("Email address").fill("person@example.com");
    const picker = page.getByLabel(/country or region/i).first();
    await picker.selectOption({ value: "BR" }).catch(async () => {
      await page.getByRole("combobox").first().selectOption({ value: "BR" });
    });
    await expect(page.getByText(/not available for this country yet/i)).toBeVisible();
    await expect(page.getByRole("button", { name: "Continue securely" })).toBeDisabled();
    expect((await axe(page)).violations).toEqual([]);
  });

  test("a valid submission shows only the generic message and no account signal", async ({ page }) => {
    await mock(page);
    await page.goto("/sign-up");
    await page.locator("label", { hasText: "For myself" }).click();
    await page.getByLabel("Email address").fill("person@example.com");
    await page.getByRole("combobox").first().selectOption({ value: "GB" });
    await page.getByRole("button", { name: "Continue securely" }).click();
    await expect(page.getByText(GENERIC)).toBeVisible();
    expect((await axe(page)).violations).toEqual([]);
    await noOverflow(page);
  });

  test("basic registration asks for no biometric, phone, address proof or card", async ({ page }) => {
    await mock(page);
    await page.goto("/sign-up");
    const text = (await page.locator("main, body").first().innerText()).toLowerCase();
    for (const word of ["selfie", "passport", "biometric", "phone", "card number", "proof of address"]) expect(text).not.toContain(word);
    await expect(page.locator('input[type="tel"], input[type="file"], input[autocomplete^="cc-"]')).toHaveCount(0);
  });
});

test.describe("status views", () => {
  test.use({ viewport: { width: 320, height: 640 } });

  for (const [name, status] of [
    ["held", { request_id: "R".repeat(24), state: "ON_HOLD", next_step: "CONTACT_SUPPORT", account: "NONE", identity: "NOT_VERIFIED", organization: "NONE", message: "We couldn't complete registration securely. Contact support or try again later.", expires_at: "2099-01-01T00:00:00Z" }],
    ["expired", { request_id: "R".repeat(24), state: "EXPIRED", next_step: "REQUEST_NEW_LINK", account: "NONE", identity: "NOT_VERIFIED", organization: "NONE", message: "This link is no longer valid. Request a new link.", expires_at: "2099-01-01T00:00:00Z" }],
    ["active, enrolled", { request_id: "R".repeat(24), state: "COMPLETED", next_step: "PERSONAL_WORKSPACE", account: "ACTIVE", identity: "NOT_VERIFIED", organization: "NONE", message: null, expires_at: "2099-01-01T00:00:00Z" }],
  ] as const) {
    test(`${name} has no axe violation, no overflow and the exact copy`, async ({ page }) => {
      await mock(page, status);
      await page.goto("/sign-up/status");
      await page.waitForLoadState("networkidle");
      if (status.message) await expect(page.getByText(status.message)).toBeVisible();
      if (status.account === "ACTIVE") {
        await expect(page.getByText(/account active/i)).toBeVisible();
        await expect(page.getByText(/identity not verified/i)).toBeVisible();
      }
      await noOverflow(page);
      const result = await axe(page);
      expect(result.violations, JSON.stringify(result.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
    });
  }
});
