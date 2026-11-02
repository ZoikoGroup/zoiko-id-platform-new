import { expect, test, type Page } from "@playwright/test";

/**
 * ZID-UX-WF-013A end to end on the real stack, with nothing mocked: the form, the backend, the verification mail (read from a local
 * mail sink, Mailpit on :8025), the single-use link, and a real WebAuthn ceremony using Chromium's virtual authenticator.
 *
 * Needs: the web container (:3000), the backend with a registration tenant and email routed to the sink, and Mailpit. See the Docker override
 * used in the L-11 notes. Set E2E_MAIL_API to point somewhere else.
 */

const MAIL = process.env.E2E_MAIL_API ?? "http://localhost:8025";
const GENERIC = "If this address can be used, check your inbox for next steps.";

async function mailFor(address: string): Promise<{ text: string; subject: string; to: string }> {
  for (let i = 0; i < 40; i++) {
    const found = await fetch(`${MAIL}/api/v1/search?query=${encodeURIComponent(`to:${address}`)}`).then((r) => r.json()).catch(() => ({ messages: [] }));
    const id = found.messages?.[0]?.ID as string | undefined;
    if (id) {
      const msg = await fetch(`${MAIL}/api/v1/message/${id}`).then((r) => r.json());
      return { text: msg.Text as string, subject: msg.Subject as string, to: msg.To?.[0]?.Address as string };
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`no mail arrived for ${address}`);
}

async function withPasskeys(page: Page) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: { protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true },
  });
  return cdp;
}

async function fillForm(page: Page, email: string, journey: "For myself" | "For an organization") {
  await page.goto("/sign-up");
  await page.waitForLoadState("networkidle");
  await page.locator("label", { hasText: journey }).click();
  await page.getByRole("combobox").first().selectOption({ value: "GB" });
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page.getByText(GENERIC).first()).toBeVisible();
}

test("a person signs up end to end: form, real mail, single-use link, real passkey, active but not identity verified", async ({ page }) => {
  const email = `live.${Date.now()}@example.com`;
  await fillForm(page, email, "For myself");

  const mail = await mailFor(email);
  expect(mail.subject).toBe("Verify your email for Zoiko iD");
  expect(mail.to).toBe(email);
  const link = mail.text.match(/https?:\/\/[^\s]+\/sign-up\/verify\?t=[A-Za-z0-9_-]+/)?.[0];
  expect(link, "the mail carries the single-use link").toBeTruthy();
  expect(mail.text).not.toMatch(/selfie|passport|phone number|card number/i);

  await withPasskeys(page);
  await page.goto(link!);
  await expect(page.getByRole("heading", { name: "Email confirmed" })).toBeVisible();
  await expect(page.getByText(/account active/i)).toBeVisible();
  await expect(page.getByText(/identity not verified/i)).toBeVisible();
  await expect(page.getByText(/j\*\*\*@example\.com|l\*\*\*@example\.com/i)).toBeVisible(); // the masked address only
  await expect(page.getByText(/never accept email or SMS codes/i)).toBeVisible();

  await page.getByRole("button", { name: "Create passkey" }).click();
  await expect(page.getByText(/Your account is ready/i)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/identity not verified/i)).toBeVisible();

  // the link is single use: opening it again says only that it is no longer valid
  const second = await page.context().newPage();
  await second.goto(link!);
  await expect(second.getByRole("heading", { name: "This link can't be used" })).toBeVisible();
  await expect(second.getByText("This link is no longer valid. Request a new link.")).toBeVisible();
  await second.close();
});

test("the organization journey ends at a provisional claim route, not a verified company", async ({ page }) => {
  const email = `live.org.${Date.now()}@example.com`;
  await fillForm(page, email, "For an organization");
  const link = (await mailFor(email)).text.match(/https?:\/\/[^\s]+\/sign-up\/verify\?t=[A-Za-z0-9_-]+/)?.[0];
  await withPasskeys(page);
  await page.goto(link!);
  await page.getByRole("button", { name: "Create passkey" }).click();
  await expect(page.getByText(/Your account is ready/i)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/identity not verified/i)).toBeVisible();
  await expect(page.getByText(/verified company|organization verified/i)).toHaveCount(0);
  await expect(page.locator('a[href="/admin/organization/claim"]').first()).toBeVisible(); // the 13B claim route, nothing verified yet
});

test("a known address and a new one look the same to the person typing", async ({ page, browser }) => {
  const email = `live.same.${Date.now()}@example.com`;
  await fillForm(page, email, "For myself"); // a brand-new address
  const link = (await mailFor(email)).text.match(/https?:\/\/[^\s]+\/sign-up\/verify\?t=[A-Za-z0-9_-]+/)?.[0];
  await withPasskeys(page);
  await page.goto(link!);
  await page.getByRole("button", { name: "Create passkey" }).click();
  await expect(page.getByText(/Your account is ready/i)).toBeVisible({ timeout: 30_000 });

  const again = await (await browser.newContext()).newPage();
  await fillForm(again, email, "For myself"); // the same address, now registered: same screen, same words, no second mail
  await again.waitForTimeout(1500);
  const all = await fetch(`${MAIL}/api/v1/search?query=${encodeURIComponent(`to:${email}`)}`).then((r) => r.json());
  expect(all.messages.length).toBe(1);
});
