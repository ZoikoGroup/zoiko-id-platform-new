import { expect, test } from "@playwright/test";

import { addVirtualPasskey } from "./helpers";

/**
 * Opens every page of the customer console as the seeded administrator and reports, per page, whether it loaded cleanly: the page
 * rendered, no error or "Not permitted" banner, no failed API call and no console error.
 *
 * Needs a running stack and the administrator's first-passkey link (one use):
 *   cd backend && python -m uv run python ../tools/scripts/bootstrap_local.py --ticket-only
 *   E2E_ADMIN_TICKET=<ticket from the link> npm run test:e2e -- all-pages
 * Set E2E_SHOTS to a folder to keep a screenshot of each page. A page with a problem is listed, not fatal, so one run shows them all;
 * the test fails at the end if any page had one.
 */
const TICKET = process.env.E2E_ADMIN_TICKET;
const SHOTS = process.env.E2E_SHOTS;

const PAGES: [string, string][] = [
  ["Overview", "/overview"],
  ["Alerts & tasks", "/alerts"],
  ["Onboarding", "/admin/onboarding"],
  ["Company verification", "/admin/organization"],
  ["Domains", "/admin/domains"],
  ["Applications", "/admin/applications"],
  ["Integrations", "/admin/integrations"],
  ["Team & invitations", "/admin/team"],
  ["Organization setup", "/organizations/current/setup"],
  ["Approvals", "/organizations/current/approvals"],
  ["Billing", "/admin/billing"],
  ["Email contacts", "/admin/contacts"],
  ["Company settings", "/admin/settings"],
  ["My account", "/account"],
  ["Sessions", "/account/sessions"],
  ["Passkeys", "/account/passkeys"],
  ["My limits and permissions", "/account/limits"],
  ["Connected apps", "/account/connected-apps"],
  ["Identity verification", "/account/verification"],
  ["Recovery codes", "/account/recovery"],
  ["Privacy (my account)", "/account/privacy"],
  ["Identity providers", "/admin/identity-providers"],
  ["Verification", "/admin/verification"],
  ["Credentials & trust", "/admin/credentials"],
  ["Authority & approvals", "/admin/authority"],
  ["Mandates", "/admin/mandates"],
  ["Policies", "/admin/policies"],
  ["Access decisions", "/admin/decisions"],
  ["Security signals", "/admin/signals"],
  ["People & principals", "/admin/accounts"],
  ["Provisioning", "/admin/provisioning"],
  ["Evidence & audit", "/admin/evidence"],
  ["Privacy & data rights", "/admin/privacy"],
  ["Operator access", "/admin/operator-access"],
  ["Residency", "/admin/residency"],
  ["Agents & tools", "/admin/agents"],
  ["Workload identity", "/admin/workloads"],
  ["Developers", "/admin/developers"],
];

const ERROR_TEXT = /Not permitted|Something went wrong|This page isn.t available|couldn.t load|Application error|Unhandled Runtime Error|could not be verified/i;

test.use({ actionTimeout: 15_000 });

test("every page of the console loads cleanly for an administrator", async ({ browser }) => {
  test.skip(!TICKET, "set E2E_ADMIN_TICKET to the seeded administrator's enrollment ticket");
  test.setTimeout(900_000);
  const context = await browser.newContext();
  const page = await context.newPage();
  await addVirtualPasskey(context, page);

  let issues: string[] = [];
  page.on("response", (r) => {
    const url = new URL(r.url());
    if (url.pathname.startsWith("/api/") && r.status() >= 400) issues.push(`${r.request().method()} ${url.pathname} -> ${r.status()}`);
  });
  page.on("pageerror", (e) => issues.push(`script error: ${e.message.slice(0, 120)}`));

  await page.goto(`/enroll?ticket=${TICKET}`);
  await page.getByRole("button", { name: "Create passkey" }).click();
  await page.waitForURL(/\/(account|overview|admin)/, { timeout: 30_000 });

  const problems: string[] = [];
  for (const [label, path] of PAGES) {
    issues = [];
    let note = "";
    try {
      await page.goto(path, { waitUntil: "domcontentloaded", timeout: 60_000 });
      await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => undefined);
      await page.waitForTimeout(1200);
      const main = page.getByRole("main");
      await expect(main).toBeVisible({ timeout: 10_000 });
      const heading = ((await main.getByRole("heading").first().textContent().catch(() => "")) ?? "").trim();
      const alerts = (await page.getByRole("alert").allTextContents()).map((t) => t.replace(/\s+/g, " ").trim()).filter((t) => ERROR_TEXT.test(t));
      const body = (await main.innerText()).replace(/\s+/g, " ");
      const banner = alerts[0] ?? (ERROR_TEXT.test(body) ? (body.match(ERROR_TEXT)?.[0] ?? "") : "");
      if (banner) issues.push(`message on page: "${banner.slice(0, 140)}"`);
      if (!heading) issues.push("no heading rendered");
      note = heading;
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/${path.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_|_$/g, "") || "root"}.png`, fullPage: true });
    } catch (e) {
      issues.push(`did not load: ${(e as Error).message.split("\n")[0].slice(0, 140)}`);
    }
    const bad = [...new Set(issues)];
    console.log(bad.length ? `ISSUE ${label} (${path}): ${bad.join(" | ")}` : `OK    ${label} (${path}) "${note}"`);
    if (bad.length) problems.push(`${label}: ${bad.join(" | ")}`);
  }

  await context.close();
  console.log(`SUMMARY ${PAGES.length - problems.length} of ${PAGES.length} pages loaded cleanly`);
  expect(problems, problems.join("\n")).toEqual([]);
});
