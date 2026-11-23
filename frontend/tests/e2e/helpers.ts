import { expect, type BrowserContext, type Page } from "@playwright/test";

/** A software passkey, so the WebAuthn ceremonies run for real without a physical authenticator. */
export async function addVirtualPasskey(context: BrowserContext, page: Page): Promise<void> {
  const cdp = await context.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2",
      transport: "internal",
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
}

export type Company = { workspace: string; name: string };

/**
 * A signed-in console session for a spec, by whichever way the stack allows.
 *
 * With E2E_ADMIN_TICKET set (the one-use enrollment ticket of an existing person, for example from `bootstrap_local.py --ticket-only`, or an
 * enrollment link a company administrator issued), the person enrols a passkey and is signed in. No email is involved, so it works on a stack
 * that sends mail through a real provider and has no mail catcher. Without it, a throwaway person signs up and the verification mail is read
 * from the local mail sink (E2E_MAIL_API), as before. The ticket is single-use: a second run needs a new one.
 */
export async function startSession(page: Page): Promise<void> {
  const ticket = process.env.E2E_ADMIN_TICKET;
  if (!ticket) {
    await registerCompany(page);
    return;
  }
  await page.goto(`/enroll?ticket=${encodeURIComponent(ticket)}`);
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "Create passkey" }).click();
  await page.waitForURL(/\/(account|overview|admin)/, { timeout: 30_000 });
}

const MAIL = process.env.E2E_MAIL_API ?? "http://localhost:8025";

/** The verification mail for an address, read from the local mail sink. */
export async function mailFor(address: string): Promise<string> {
  for (let i = 0; i < 40; i++) {
    const found = await fetch(`${MAIL}/api/v1/search?query=${encodeURIComponent(`to:${address}`)}`).then((r) => r.json()).catch(() => ({ messages: [] }));
    const id = found.messages?.[0]?.ID as string | undefined;
    if (id) return (await fetch(`${MAIL}/api/v1/message/${id}`).then((r) => r.json())).Text as string;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`no mail arrived for ${address}`);
}

/**
 * Signs up a throwaway person the way a real one does (ZID-UX-WF-013A): country, journey and email on /sign-up, the single-use link from the mail, a first passkey, then
 * the overview. The person is signed in with a personal workspace and a pending owner role; no company exists until a claim is verified (13B), so a spec that needs an
 * administrator's pages must verify a claim first. The old company-first /register page no longer exists.
 */
export async function registerCompany(page: Page, journey: "For myself" | "For an organization" = "For an organization"): Promise<Company> {
  const email = `e2e.${Date.now()}.${Math.random().toString(36).slice(2, 6)}@example.com`;
  await page.goto("/sign-up");
  await page.waitForLoadState("networkidle");
  await page.locator("label", { hasText: journey }).click();
  await page.getByRole("combobox").first().selectOption({ value: "GB" });
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page.getByText("If this address can be used, check your inbox for next steps.").first()).toBeVisible();
  const link = (await mailFor(email)).match(/https?:\/\/[^\s]+\/sign-up\/verify\?t=[A-Za-z0-9_-]+/)?.[0];
  if (!link) throw new Error("the verification mail carried no link");
  await page.goto(link);
  await page.waitForLoadState("networkidle"); // a click before the page has hydrated does nothing, and the passkey is never created
  await page.getByRole("button", { name: "Create passkey" }).click();
  await expect(page.getByText(/Your account is ready/i)).toBeVisible({ timeout: 30_000 });
  await page.goto("/overview");
  await page.waitForURL(/\/overview/, { timeout: 30_000 });
  return { workspace: "Personal workspace", name: email };
}
